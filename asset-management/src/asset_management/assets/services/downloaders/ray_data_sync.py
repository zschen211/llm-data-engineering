"""Ray Data sync pipelines: raw-layer upload (Phase A) and asset processing (Phase B).

Phase A (network IO): ``ray.data.from_items`` of pending remotes → per-row
``map`` (HF download → upload to the ``raw/`` layer → ``raw_files``
registration), streamed back through ``iter_rows``. The pipeline is
pull-based, so a parked driver stalls the workers via backpressure — that is
the pause primitive.

Phase B (CPU/storage IO): ``from_items`` of uploaded raw rows → per-row
``flat_map`` (fetch raw → processor → candidate rows) → a ``materialize``
stage boundary (independent stage timing and retry isolation) → per-row
``map`` (persist to ``blobs/`` + asset registration). Candidate rows are
node-agnostic (payload bytes or a raw-layer source key for zero-copy
persist), never worker-local paths.

Ray Data provides sharding, streaming backpressure and crash retries
(``max_retries`` per op); workers share state with the driver only through
SQLite (WAL) and outcome rows. Application errors are caught inside the row
functions and returned in the outcome, so one failing file never fails the
run; only retry-exhausted worker crashes abort the pipeline (the run is
marked failed and a later resume re-submits the unfinished rows).
"""

from __future__ import annotations

import shutil
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

import ray

from ...meta.db import Database
from ...meta.models import Source
from ...storage import (
    LocalStorageBackend,
    S3StorageBackend,
    StorageBackend,
    raw_key_for,
)
from ..cluster import cluster_manager
from ..obs import observability

# side-effect import: registers the built-in processors (register_processor)
from . import processors  # noqa: F401  # pylint: disable=unused-import
from .base import RemoteRef, sha256_of
from .download import DownloadStage
from .persist import PersistStage
from .process import get_processor

RETRIES = 2
MAX_BLOCKS = 512


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
    bucket: str = "asset-assets"
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
        return S3StorageBackend(
            self.endpoint or None,
            self.access_key,
            self.secret_key,
            self.bucket,
            self.region,
        )


@dataclass
class SyncConfig:
    """Everything the row functions need; must stay cloudpickle-serializable.

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
    cache_dir: str = ""


@dataclass
class PhaseOutcome:
    """Aggregated stats of one pipeline phase, built by the driver."""

    items: int = 0
    done: int = 0
    skipped: int = 0
    failed: int = 0
    retry_app: int = 0
    retry_ray: int = 0
    duration_s: float = 0.0
    errors: list[str] = field(default_factory=list)


def _num_blocks(rows: int) -> int:
    """One block per file (fine pause/retry granularity), capped for big repos."""
    return max(1, min(rows, MAX_BLOCKS))


def _park(paused: Callable[[], bool] | None) -> None:
    while paused is not None and paused():
        time.sleep(0.5)


def _iter_rows(ds, paused: Callable[[], bool] | None) -> Iterator[dict]:
    """Stream rows with a park between rows: stopping to pull stalls the
    whole pipeline through backpressure (pause semantics)."""
    for row in ds.iter_rows():
        yield row
        _park(paused)


def _remote_of(row: dict) -> RemoteRef:
    return RemoteRef(
        id=row["remote_id"],
        name=row["name"],
        path_in_repo=row["path_in_repo"],
        meta=row.get("meta") or {},
    )


def _record_event(
    cfg: SyncConfig,
    db: Database,
    row: dict,
    stage: str,
    message: str,
    level: str = "info",
    fraction: float | None = None,
    n: int | None = None,
    total: int | None = None,
    remote: str = "",
) -> None:
    remote = remote or row["name"]
    db.append_sync_event(cfg.run_id, stage, remote, level, message, fraction=fraction)
    db.update_sync_run(cfg.run_id, current_stage=stage, current_file=remote)
    if stage == "download" and n is not None:
        db.update_sync_task(
            cfg.run_id,
            row["remote_id"],
            bytes_downloaded=n,
            total_bytes=total or 0,
            fraction=fraction,
        )


# ------------------------------------------------------------------ Phase A


def _download_row(cfg: SyncConfig, row: dict) -> dict:
    """Phase A map fn: download one remote and upload it to the raw layer."""
    db = Database(cfg.db_path, mark_stale=False)
    try:
        return _download_one(cfg, db, row)
    finally:
        db.close()


def _download_one(cfg: SyncConfig, db: Database, row: dict) -> dict:
    started = time.perf_counter()
    outcome = {
        "remote_id": row["remote_id"],
        "name": row["name"],
        "path_in_repo": row["path_in_repo"],
        "status": "failed",
        "size": 0,
        "sha256": "",
        "retry_app": 0,
        "retry_ray": 0,
        "duration_s": 0.0,
        "error": "",
    }
    raw = db.get_raw_file(cfg.source.id, row["path_in_repo"])
    attempts_before = raw["attempts"] if raw else 0
    if raw is None:
        db.upsert_raw_file(
            cfg.source.id,
            row["path_in_repo"],
            raw_key_for(cfg.source.id, row["path_in_repo"]),
        )
    db.update_raw_file(
        cfg.source.id, row["path_in_repo"], attempts=attempts_before + 1, error=""
    )
    task_row = db.get_sync_task(cfg.run_id, row["remote_id"])
    db.update_sync_task(
        cfg.run_id,
        row["remote_id"],
        status="downloading",
        attempts=(task_row["attempts"] if task_row else 0) + 1,
        bytes_downloaded=0,
        fraction=0.0,
        error="",
    )
    outcome["retry_ray"] = attempts_before

    backend = cfg.backend.build()
    stage = DownloadStage.from_source(cfg.source, hub=cfg.hub)
    remote = _remote_of(row)
    work_dir = Path(cfg.tmp_dir) / f"raw_{row['remote_id']}"
    work_dir.mkdir(parents=True, exist_ok=True)
    try:
        _record_event(
            cfg, db, row, "download", f"开始下载 → 暂存区 {work_dir / remote.name}"
        )
        local = stage.download(
            remote,
            work_dir / remote.name,
            on_event=lambda **kw: _record_event(cfg, db, row, **kw),
            cache_dir=Path(cfg.cache_dir) if cfg.cache_dir else None,
        )
        size = local.stat().st_size
        sha256 = sha256_of(local)
        backend.put_object(raw_key_for(cfg.source.id, row["path_in_repo"]), local)
        db.update_raw_file(
            cfg.source.id,
            row["path_in_repo"],
            sha256=sha256,
            size=size,
            status="uploaded",
            commit_hash=row.get("commit_hash", ""),
        )
        db.update_sync_task(
            cfg.run_id, row["remote_id"], status="downloaded", fraction=1.0
        )
        _record_event(cfg, db, row, "download", f"下载完成并上传 raw 层（{size} 字节）")
        outcome.update(status="done", size=size, sha256=sha256)
    except Exception as exc:
        outcome["error"] = str(exc)
        db.update_raw_file(
            cfg.source.id, row["path_in_repo"], status="failed", error=str(exc)
        )
        db.record_download(row["remote_id"], cfg.source.kind, "failed", str(exc))
        db.update_sync_task(
            cfg.run_id, row["remote_id"], status="failed", error=str(exc)
        )
        _record_event(cfg, db, row, "download", f"处理失败: {exc}", level="error")
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
    outcome["duration_s"] = round(time.perf_counter() - started, 3)
    outcome["retry_app"] = max(0, getattr(stage, "attempts_used", 1) - 1)
    return outcome


def run_raw_upload(
    cfg: SyncConfig,
    rows: list[dict],
    paused: Callable[[], bool] | None = None,
    on_item: Callable[[dict], None] | None = None,
) -> PhaseOutcome:
    """Phase A: download remotes into the raw layer via a Ray Data pipeline."""
    cluster_manager.ensure_started()
    outcome = PhaseOutcome()
    if not rows:
        return outcome
    started = time.perf_counter()
    ds = ray.data.from_items(rows, override_num_blocks=_num_blocks(len(rows)))
    ds = ds.map(
        lambda row: _download_row(cfg, row),
        compute=ray.data.TaskPoolStrategy(size=max(1, cfg.workers)),
        max_retries=RETRIES,
    )
    for row in _iter_rows(ds, paused):
        outcome.items += 1
        outcome.retry_app += row["retry_app"]
        outcome.retry_ray += row["retry_ray"]
        if row["status"] == "done":
            outcome.done += 1
        else:
            outcome.failed += 1
            outcome.errors.append(f"{row['name']}: {row['error']}")
        observability.item_finished(
            cfg.run_id, "download_raw", row["status"], row["duration_s"]
        )
        if on_item is not None:
            on_item(row)
    outcome.duration_s = round(time.perf_counter() - started, 3)
    return outcome


# ------------------------------------------------------------------ Phase B


def _process_row(cfg: SyncConfig, row: dict) -> Iterator[dict]:
    """Phase B flat_map fn: fetch one raw object, run the processor, yield
    node-agnostic candidate rows."""
    db = Database(cfg.db_path, mark_stale=False)
    try:
        yield from _process_one(cfg, db, row)
    finally:
        db.close()


def _process_one(cfg: SyncConfig, db: Database, row: dict) -> Iterator[dict]:
    started = time.perf_counter()
    task_row = db.get_sync_task(cfg.run_id, row["remote_id"])
    retry_before = task_row["process_attempts"] if task_row else 0
    db.update_sync_task(
        cfg.run_id, row["remote_id"], process_attempts=retry_before + 1, error=""
    )
    backend = cfg.backend.build()
    processor = get_processor(
        cfg.source.params.get("process", "file"), cfg.source.params
    )
    remote = _remote_of(row)
    work_dir = Path(cfg.tmp_dir) / f"proc_{row['remote_id']}"
    work_dir.mkdir(parents=True, exist_ok=True)
    try:
        _record_event(cfg, db, row, "process", f"开始解析 → {work_dir}")
        local = work_dir / remote.name
        backend.get_file(row["object_key"], local)
        candidates = processor.process(remote, local, work_dir)
        _record_event(cfg, db, row, "process", f"解析完成：{len(candidates)} 个候选")
        duration = round(time.perf_counter() - started, 3)
        total = len(candidates)
        for index, candidate in enumerate(candidates):
            yield _candidate_row(
                cfg, row, candidate, index, total, duration, retry_before
            )
    except Exception as exc:
        db.record_download(row["remote_id"], cfg.source.kind, "failed", str(exc))
        db.update_sync_task(
            cfg.run_id, row["remote_id"], status="failed", error=str(exc)
        )
        _record_event(cfg, db, row, "process", f"解析失败: {exc}", level="error")
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def _candidate_row(
    cfg: SyncConfig,
    row: dict,
    candidate,
    index: int,
    total: int,
    process_duration: float,
    retry_ray: int,
) -> dict:
    """Convert a processor Candidate (worker-local file) into a node-agnostic
    row: identity candidates keep the raw-layer key (zero-copy persist),
    everything else ships its bytes through the object store."""
    identity = candidate.sha256 == row["sha256"]
    return {
        "remote_id": row["remote_id"],
        "file_name": row["name"],
        "name": candidate.name,
        "sha256": candidate.sha256,
        "size": candidate.size,
        "ext": candidate.ext,
        "asset_type": candidate.asset_type,
        "width": candidate.width,
        "height": candidate.height,
        "meta": candidate.meta,
        "path_in_repo": row["path_in_repo"],
        "raw_sha256": row["sha256"],
        "source_key": row["object_key"] if identity else None,
        "payload": None if identity else Path(candidate.path).read_bytes(),
        "file_index": index,
        "file_total": total,
        "process_duration_s": process_duration,
        "retry_ray": retry_ray,
    }


def _persist_row(cfg: SyncConfig, row: dict) -> dict:
    """Phase B map fn: persist one candidate row and register its asset."""
    db = Database(cfg.db_path, mark_stale=False)
    try:
        return _persist_one(cfg, db, row)
    finally:
        db.close()


def _persist_one(cfg: SyncConfig, db: Database, row: dict) -> dict:
    started = time.perf_counter()
    outcome = {
        "remote_id": row["remote_id"],
        "file_name": row["file_name"],
        "name": row["name"],
        "status": "failed",
        "duration_s": 0.0,
        "error": "",
        "file_last": row["file_index"] == row["file_total"] - 1,
        "process_duration_s": row["process_duration_s"],
        "retry_ray": row["retry_ray"],
    }
    try:
        persister = PersistStage(cfg.backend.build(), db)
        result = persister.persist_one_row(cfg.source, row)
        outcome["status"] = result
    except Exception as exc:
        outcome["error"] = str(exc)
        db.update_sync_task(
            cfg.run_id, row["remote_id"], status="failed", error=str(exc)
        )
        _record_event(cfg, db, row, "persist", f"持久化失败: {exc}", level="error")
    if outcome["file_last"]:
        _record_event(cfg, db, row, "persist", f"持久化完成：{row['file_name']}")
    outcome["duration_s"] = round(time.perf_counter() - started, 3)
    return outcome


def run_process_persist(
    cfg: SyncConfig,
    rows: list[dict],
    paused: Callable[[], bool] | None = None,
    on_item: Callable[[dict], None] | None = None,
) -> tuple[PhaseOutcome, PhaseOutcome]:
    """Phase B: process uploaded raw files and persist the resulting assets.

    The materialize boundary between the flat_map (process) and the map
    (persist) splits the two stages: independent stage timing, retry
    isolation and a bounded object-store buffer (spills to disk when the
    candidate set is large).
    """
    cluster_manager.ensure_started()
    process = PhaseOutcome()
    persist = PhaseOutcome()
    if not rows:
        return process, persist
    started = time.perf_counter()
    ds = ray.data.from_items(rows, override_num_blocks=_num_blocks(len(rows)))
    ds = ds.flat_map(
        lambda row: _process_row(cfg, row),
        compute=ray.data.TaskPoolStrategy(size=max(1, cfg.workers)),
        max_retries=RETRIES,
    )
    ds = ds.materialize()
    process.duration_s = round(time.perf_counter() - started, 3)

    ds = ds.map(
        lambda row: _persist_row(cfg, row),
        compute=ray.data.TaskPoolStrategy(size=max(1, cfg.workers)),
        max_retries=RETRIES,
    )
    db = Database(cfg.db_path, mark_stale=False)
    process_seen: set[str] = set()
    file_failed: dict[str, bool] = {}
    try:
        for row in _iter_rows(ds, paused):
            remote_id = row["remote_id"]
            if remote_id not in process_seen:
                process_seen.add(remote_id)
                process.items += 1
                process.done += 1
                process.retry_ray += row["retry_ray"]
                observability.item_finished(
                    cfg.run_id, "process", "done", row["process_duration_s"]
                )
            persist.items += 1
            if row["status"] == "new":
                persist.done += 1
            elif row["status"] == "skipped":
                persist.skipped += 1
            else:
                persist.failed += 1
                persist.errors.append(f"{row['name']}: {row['error']}")
                file_failed[remote_id] = True
            observability.item_finished(
                cfg.run_id, "persist", row["status"], row["duration_s"]
            )
            if row["file_last"]:
                db.update_sync_task(
                    cfg.run_id,
                    remote_id,
                    status="failed" if file_failed.get(remote_id) else "persisted",
                    fraction=1.0,
                )
            if on_item is not None:
                on_item(row)
        db.mark_processed_files_persisted(cfg.run_id)
    finally:
        db.close()
    persist.duration_s = round(time.perf_counter() - started - process.duration_s, 3)
    return process, persist
