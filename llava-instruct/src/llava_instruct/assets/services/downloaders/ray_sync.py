"""Ray-based sync driver: one Ray task per remote file.

The driver (``store.sync_source``) resolves the file list, then submits one
``_sync_file_task`` per file and consumes results with a sliding window so at
most ``workers`` tasks are in flight (streaming progress + pause between
files, as before).

Fault tolerance:
  - worker crash (OOM/segfault/…) → Ray re-runs the task (``max_retries``);
    download is idempotent and the persist dedup (BEGIN IMMEDIATE) prevents
    double registration
  - application errors (network/parse/persist) are caught inside the task and
    reported in the ``FileOutcome``, counted by the driver as failures

State is shared through the SQLite database only (WAL mode, per-process
connections): workers open their own ``Database`` with ``mark_stale=False``
(only the driver may mark stale runs) and rebuild the storage backend from a
serializable ``BackendConfig`` (boto3 clients are not picklable).
"""

from __future__ import annotations

import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import ray

from ...meta.db import Database
from ...meta.models import Source
from ...storage import LocalStorageBackend, S3StorageBackend, StorageBackend
from . import processors  # noqa: F401  (registers built-in processors)
from .base import RemoteRef
from .download import DownloadStage
from .persist import PersistStage
from .process import get_processor


@dataclass
class BackendConfig:
    """Serializable storage backend description; workers rebuild the backend.

    ``from_backend`` captures the constructor arguments of the live backend so
    Ray tasks (separate processes) can reconstruct it without pickling the
    (non-serializable) boto3 client.
    """

    kind: str = "s3"
    root: str = ""
    endpoint: str = ""
    access_key: str = ""
    secret_key: str = ""
    bucket: str = "llava-assets"
    region: str = "us-east-1"

    @classmethod
    def from_backend(cls, backend: StorageBackend) -> BackendConfig:
        if isinstance(backend, LocalStorageBackend):
            return cls(kind="local", root=str(backend.root))
        if isinstance(backend, S3StorageBackend):
            return cls(
                kind="s3",
                endpoint=backend.endpoint_url or "",
                access_key=backend.access_key,
                secret_key=backend.secret_key,
                bucket=backend.bucket,
                region=backend.region,
            )
        raise ValueError(f"unsupported backend for ray sync: {type(backend).__name__}")

    def build(self) -> StorageBackend:
        if self.kind == "local":
            return LocalStorageBackend(Path(self.root))
        if self.kind == "s3":
            return S3StorageBackend(
                self.endpoint or None,
                self.access_key,
                self.secret_key,
                self.bucket,
                self.region,
            )
        raise ValueError(f"unsupported backend kind: {self.kind}")


@dataclass
class SyncConfig:
    """Everything a per-file task needs; must stay cloudpickle-serializable.

    ``hub`` is a test hook: an injectable object with ``list_repo_files`` /
    ``hf_hub_download`` (the real one is created lazily inside
    ``DownloadStage``). Do not put live backend/DB objects here.
    """

    db_path: str
    backend: BackendConfig
    tmp_dir: str
    source: Source
    run_id: str
    workers: int = 2
    attempts: int = 3
    hub: object | None = None


@dataclass
class FileOutcome:
    """Result of one per-file task, aggregated by the driver."""

    remote: str = ""
    new: int = 0
    skipped: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)


def _wait_until_resumed(db: Database, run_id: str) -> None:
    """Park while the run is paused (file granularity, same semantics as the
    thread-based pipeline): an in-flight download finishes, but nothing new is
    persisted until the run is resumed."""
    while True:
        run = db.get_sync_run(run_id)
        if run is None or run["status"] != "paused":
            return
        time.sleep(0.5)


def _sync_file_task(cfg: SyncConfig, remote: RemoteRef) -> FileOutcome:
    """Full download -> process -> persist chain for ONE file (one process).

    Runs in a Ray worker: opens its own DB connection (mark_stale=False),
    rebuilds the backend, and writes progress events straight into
    sync_events/sync_runs. All exceptions are converted into the outcome so a
    failing file never fails the driver.
    """
    db = Database(cfg.db_path, mark_stale=False)
    try:
        backend = cfg.backend.build()
        stage = DownloadStage.from_source(cfg.source, hub=cfg.hub)
        processor = get_processor(
            cfg.source.params.get("process", "file"), cfg.source.params
        )
        persister = PersistStage(backend, db)
        work_dir = Path(cfg.tmp_dir) / remote.id
        work_dir.mkdir(parents=True, exist_ok=True)
        outcome = FileOutcome(remote=remote.name)

        def event(
            stage: str,
            remote: str = "",
            message: str = "",
            level: str = "info",
            fraction: float | None = None,
        ) -> None:
            db.append_sync_event(
                cfg.run_id, stage, remote, level, message, fraction=fraction
            )
            db.update_sync_run(cfg.run_id, current_stage=stage, current_file=remote)

        try:
            _wait_until_resumed(db, cfg.run_id)
            event(
                "download", remote.name, f"开始下载 → 暂存区 {work_dir / remote.name}"
            )
            local = stage.download(remote, work_dir / remote.name, on_event=event)
            event("download", remote.name, f"下载完成（{local.stat().st_size} 字节）")
            _wait_until_resumed(db, cfg.run_id)
            event("process", remote.name, f"开始解析 → {work_dir}")
            candidates = processor.process(remote, local, work_dir)
            event("process", remote.name, f"解析完成：{len(candidates)} 个候选")
            event("persist", remote.name, f"持久化 {len(candidates)} 个候选到 backend")
            new, skipped, errors_in_persist = persister.persist(cfg.source, candidates)
            event(
                "persist",
                remote.name,
                f"持久化完成：新增 {new}，跳过 {skipped}"
                + (f"，失败 {len(errors_in_persist)}" if errors_in_persist else ""),
            )
            outcome.new = new
            outcome.skipped = skipped
            outcome.failed = len(errors_in_persist)
            outcome.errors = errors_in_persist
        except Exception as exc:
            outcome.failed += 1
            outcome.errors.append(f"{remote.name}: {exc}")
            db.record_download(remote.id, cfg.source.kind, "failed", str(exc))
            event("download", remote.name, f"处理失败: {exc}", level="error")
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)
        return outcome
    finally:
        db.close()


def run_ray_sync(
    cfg: SyncConfig, remotes: list[RemoteRef], paused: Callable[[], bool] | None = None
) -> list[FileOutcome]:
    """Submit one task per remote and stream results back in completion order.

    A sliding window keeps at most ``cfg.workers`` tasks in flight; when
    ``paused()`` returns True the driver parks between completions and stops
    submitting new tasks (in-flight tasks finish their chain, matching the
    file-granularity pause semantics). Outcomes are returned in the order the
    tasks completed, not the input order.
    """
    if not ray.is_initialized():
        # Exclude the cwd from the runtime-env package: the package is pip-
        # installed in the same venv, so workers don't need the source tree
        # (and it may be huge / contain other projects' virtualenvs).
        ray.init(
            num_cpus=max(1, cfg.workers),
            ignore_reinit_error=True,
            runtime_env={"excludes": ["**"]},
        )

    def park_if_paused() -> None:
        while paused is not None and paused():
            time.sleep(0.5)

    task = ray.remote(_sync_file_task).options(max_retries=2)
    pending: dict[object, RemoteRef] = {}
    outcomes: list[FileOutcome] = []
    iterator = iter(remotes)
    for _ in range(min(max(1, cfg.workers), len(remotes))):
        remote = next(iterator)
        pending[task.remote(cfg, remote)] = remote

    while pending:
        park_if_paused()
        ready, not_ready = ray.wait(list(pending), num_returns=1, timeout=30)
        if not ready:
            pending = {ref: pending[ref] for ref in not_ready}
            continue
        for ref in ready:
            remote = pending.pop(ref)
            try:
                outcome = ray.get(ref)
            except Exception as exc:
                outcome = FileOutcome(
                    remote=remote.name, failed=1, errors=[f"{remote.name}: {exc}"]
                )
            outcomes.append(outcome)
            next_remote = next(iterator, None)
            if next_remote is not None:
                pending[task.remote(cfg, next_remote)] = next_remote
    return outcomes
