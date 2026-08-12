"""Sync service: the download -> process -> persist state machine.

Mixin of ``AssetStore``; expects ``self._db`` (Database), ``self.backend``
(StorageBackend), ``self.tmp_dir`` (Path) and ``self._hub_hook`` (test hook),
all owned by ``AssetStore.__init__``.

Covers the remote HF sync (``sync_source``, Ray-based) and the local import
(``import_dir``, direct persist); both drive the same pipeline and return a
``SyncReport``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from ...log import get_logger
from ..classify import IMAGE_SUFFIXES, classify_image
from ..meta.models import Source
from .downloaders.base import Candidate, RemoteRef, image_size, sha256_of
from .downloaders.download import DownloadStage
from .downloaders.persist import PersistStage
from .downloaders.ray_sync import BackendConfig, SyncConfig, run_ray_sync
from .obs import observability

logger = get_logger("assets.services.sync")


@dataclass
class SyncReport:
    source_id: str
    source_kind: str = ""
    resolved: int = 0
    new: int = 0
    skipped_existing: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)


class SyncService:
    def start_sync(self, source_id: str) -> str:
        """Validate a source and open a persistent sync run; returns run_id.

        Raises ValueError when the source is unknown/disabled/not huggingface
        or already has an active sync run (running or paused).
        """
        self._validate_source(source_id)
        active = [
            r
            for r in self._db.list_sync_runs(limit=100)
            if r["source_id"] == source_id and r["status"] in ("running", "paused")
        ]
        if active:
            raise ValueError(
                f"source {source_id} is already syncing (run={active[0]['id']})"
            )
        return self._db.create_sync_run(source_id)

    def _validate_source(self, source_id: str) -> Source:
        source = self._db.get_source(source_id)
        if source is None:
            raise ValueError(f"unknown source: {source_id}")
        if not source.enabled:
            raise ValueError(f"source {source_id} is disabled")
        if source.kind != "huggingface":
            raise ValueError(
                f"only the 'huggingface' source kind is supported, got {source.kind!r}"
            )
        return source

    def resume_source(self, source_id: str) -> str:
        """Resume the latest crash-interrupted run of a source, or start a
        fresh run when there is none. Returns the run_id to continue with.

        The interrupted run keeps its per-file task table (sync_tasks), so a
        resume continues at file granularity: files already persisted are
        skipped, unfinished ones are re-submitted (their last-known download
        progress stays visible until the download restarts).
        """
        source = self._validate_source(source_id)
        active = [
            r
            for r in self._db.list_sync_runs(limit=100)
            if r["source_id"] == source_id and r["status"] in ("running", "paused")
        ]
        if active:
            raise ValueError(
                f"source {source_id} is already syncing (run={active[0]['id']})"
            )
        run = self._db.get_interrupted_run(source_id)
        if run is None:
            return self._db.create_sync_run(source_id)
        self._db.update_sync_run(run["id"], status="running")
        self._db.append_sync_event(
            run["id"], "control", "", "info", "同步已从上次中断处继续（文件级续传）"
        )
        logger.info("run=%s 续传开始 source=%s", run["id"], source.id)
        return run["id"]

    def sync_source(
        self, source_id: str, run_id: str | None = None, hub=None
    ) -> SyncReport:
        """Run the download -> process -> persist pipeline for one source.

        Only the ``huggingface`` source kind is supported; the data format
        transformation is chosen by ``params.process`` ("file" | "parquet").

        Execution is **Ray-based**: one per-file task per remote (``workers``
        in-flight tasks via a sliding window), each running the full download
        -> process -> persist chain in its own process. Ray re-runs tasks
        whose worker crashed (``max_retries``); per-file application errors
        are caught in the task and counted in the report. Progress and step
        events are recorded in sync_runs/sync_events and mirrored to the
        unified log (prefix ``run=<id>``).

        Pause control: ``pause_sync``/``resume_sync`` switch the run's status.
        While ``paused``, tasks and the driver park at file boundaries (before
        starting a download and before persisting a downloaded file), so an
        in-flight download may finish but its results are not written until
        the run is resumed.

        Crash recovery: each remote file is tracked in ``sync_tasks`` (status
        + byte progress). A run left 'running'/'paused' by a crash becomes
        'interrupted' on the next ``Database`` open; ``resume_source`` (or a
        direct call with the interrupted run_id) continues it at file
        granularity — persisted files are skipped, unfinished files are
        re-submitted, and the per-source HF cache dir (stable across runs)
        avoids re-downloading files that were fetched but not yet persisted.

        ``hub`` injects a fake huggingface_hub for tests; the real client is
        huggingface_hub, installed with the project.
        """
        source = self._validate_source(source_id)
        if run_id is None:
            run_id = self.start_sync(source_id)

        if hub is None:
            hub = self._hub_hook
        stage = DownloadStage.from_source(source, hub=hub)
        report = SyncReport(source_id=source.id, source_kind=source.kind)
        total_remotes = 0
        started = time.perf_counter()
        observability.event(
            "sync_run_started", run_id=run_id, source_id=source.id, kind=source.kind
        )

        def event(stage: str, message: str, level: str = "info") -> None:
            self._db.append_sync_event(run_id, stage, "", level, message)
            self._db.update_sync_run(run_id, current_stage=stage, current_file="")
            logger.info("run=%s stage=%s %s", run_id, stage, message)

        try:
            self._resume_if_interrupted(run_id)
            logger.info("run=%s 同步开始 source=%s", run_id, source_id)
            event("resolve", "解析文件清单…")
            remotes = stage.resolve()
            total_remotes = len(remotes)
            report.resolved = total_remotes
            self._db.update_sync_run(run_id, total_files=total_remotes)
            if not remotes:
                self._db.update_sync_run(run_id, status="done", progress=100.0)
                event("done", "没有需要下载的文件")
                return report

            to_sync, skipped_files = self._plan_sync(run_id, remotes, event)
            cfg = self._sync_config(source, run_id, hub)
            failed_files = 0
            for processed_remotes, outcome in enumerate(
                run_ray_sync(cfg, to_sync, paused=lambda: self._run_paused(run_id)),
                start=1,
            ):
                failed_files += self._record_outcome(report, outcome)
                self._apply_progress(
                    run_id,
                    processed_remotes,
                    failed_files,
                    skipped_files,
                    total_remotes,
                )
            self._finish_sync(run_id, report, event)
            observability.event(
                "sync_run_finished",
                run_id=run_id,
                source_id=source.id,
                resolved=report.resolved,
                new=report.new,
                skipped=report.skipped_existing,
                failed=report.failed,
                duration_s=round(time.perf_counter() - started, 3),
            )
        except Exception as exc:
            self._db.update_sync_run(run_id, status="failed", error=str(exc))
            self._db.append_sync_event(run_id, "error", "", "error", f"同步失败: {exc}")
            logger.error("run=%s 同步失败: %s", run_id, exc)
            observability.event(
                "sync_run_failed",
                level="error",
                run_id=run_id,
                source_id=source.id,
                error=str(exc),
                duration_s=round(time.perf_counter() - started, 3),
            )
            raise
        return report

    def _resume_if_interrupted(self, run_id: str) -> None:
        """Turn an interrupted run back into 'running' at the start of sync."""
        run = self._db.get_sync_run(run_id)
        if run is None:
            raise ValueError(f"unknown sync run: {run_id}")
        if run["status"] == "interrupted":
            self._db.update_sync_run(run_id, status="running")

    def _plan_sync(
        self, run_id: str, remotes: list[RemoteRef], event
    ) -> tuple[list[RemoteRef], int]:
        """Register/reconcile tasks and return (to_sync, skipped_files)."""
        total = len(remotes)
        inserted = self._db.create_sync_tasks(run_id, remotes)
        if inserted:
            event("resolve", f"解析完成：{total} 个文件，登记 {inserted} 个新任务")
        else:
            event("resolve", f"解析完成：{total} 个文件，续传 {total} 个已有任务")
        self._db.reconcile_sync_tasks(run_id, {r.id for r in remotes})
        tasks = self._db.get_sync_tasks(run_id)
        task_status = {t["remote_id"]: t["status"] for t in tasks}
        to_sync = [
            r
            for r in remotes
            if task_status.get(r.id, "pending") not in ("persisted", "skipped")
        ]
        skipped_files = total - len(to_sync)
        if skipped_files:
            event("resolve", f"{skipped_files} 个文件已持久化，跳过（断点续传）")
        return to_sync, skipped_files

    def _sync_config(self, source: Source, run_id: str, hub) -> SyncConfig:
        return SyncConfig(
            db_path=str(self._db.path),
            backend=BackendConfig.from_backend(self.backend),
            tmp_dir=str(self.tmp_dir),
            source=source,
            run_id=run_id,
            workers=max(1, int(source.params.get("workers", 2))),
            attempts=int(source.params.get("attempts", 3)),
            hub=hub,
            cache_dir=str(
                self.data_dir / "hf_cache" / source.params.get("repo_id", "repo")
            ),
        )

    def _record_outcome(self, report: SyncReport, outcome) -> int:
        """Fold one Ray task outcome into the report; 1 when the task failed."""
        report.new += outcome.new
        report.skipped_existing += outcome.skipped
        for error in outcome.errors:
            report.failed += 1
            report.errors.append(f"{outcome.remote}: {error}")
        return int(outcome.failed)

    def _apply_progress(
        self,
        run_id: str,
        processed_remotes: int,
        failed_files: int,
        skipped_files: int,
        total_remotes: int,
    ) -> None:
        completed = processed_remotes - failed_files + skipped_files
        self._db.update_sync_run(
            run_id,
            done_files=completed,
            failed_files=failed_files,
            current_stage="",
            current_file="",
            progress=round(completed / total_remotes * 100, 1),
        )

    def _finish_sync(self, run_id: str, report: SyncReport, event) -> None:
        tasks = self._db.get_sync_tasks(run_id)
        done_files = sum(1 for t in tasks if t["status"] in ("persisted", "skipped"))
        failed_files = sum(1 for t in tasks if t["status"] == "failed")
        self._db.update_sync_run(
            run_id,
            status="done",
            progress=100.0,
            done_files=done_files,
            failed_files=failed_files,
        )
        event(
            "done",
            f"同步完成：新增 {report.new}，跳过 {report.skipped_existing}，失败 {report.failed}",
        )
        logger.info(
            "run=%s 同步完成 new=%s skipped=%s failed=%s",
            run_id,
            report.new,
            report.skipped_existing,
            report.failed,
        )

    def get_sync_run(self, run_id: str) -> dict | None:
        return self._db.get_sync_run(run_id)

    def get_running_run(self, source_id: str) -> dict | None:
        return self._db.get_running_run(source_id)

    def get_interrupted_run(self, source_id: str) -> dict | None:
        return self._db.get_interrupted_run(source_id)

    def get_sync_tasks(self, run_id: str) -> list[dict]:
        return self._db.get_sync_tasks(run_id)

    def get_sync_tasks_page(
        self, run_id: str, offset: int = 0, limit: int = 20
    ) -> list[dict]:
        return self._db.get_sync_tasks_page(run_id, offset=offset, limit=limit)

    def count_sync_tasks(self, run_id: str) -> int:
        return self._db.count_sync_tasks(run_id)

    def get_sync_events(
        self, run_id: str, after_id: int = 0, limit: int = 200
    ) -> list[dict]:
        return self._db.get_sync_events(run_id, after_id=after_id, limit=limit)

    def list_sync_runs(self, limit: int = 20) -> list[dict]:
        return self._db.list_sync_runs(limit=limit)

    def pause_sync(self, run_id: str) -> dict:
        """Pause a running sync run.

        Tasks and the driver park at file boundaries (at most ``workers``
        in-flight tasks complete); progress/state are kept so ``resume_sync``
        can continue.
        """
        run = self._db.get_sync_run(run_id)
        if run is None:
            raise ValueError(f"unknown sync run: {run_id}")
        if run["status"] != "running":
            raise ValueError(
                f"sync run {run_id} is not running (status={run['status']})"
            )
        self._db.update_sync_run(run_id, status="paused")
        self._db.append_sync_event(
            run_id, "control", "", "info", "同步已暂停，可随时继续"
        )
        logger.info("run=%s 同步已暂停", run_id)
        return self._db.get_sync_run(run_id)

    def resume_sync(self, run_id: str) -> dict:
        """Resume a paused sync run; tasks pick up their loops again."""
        run = self._db.get_sync_run(run_id)
        if run is None:
            raise ValueError(f"unknown sync run: {run_id}")
        if run["status"] != "paused":
            raise ValueError(
                f"sync run {run_id} is not paused (status={run['status']})"
            )
        self._db.update_sync_run(run_id, status="running")
        self._db.append_sync_event(run_id, "control", "", "info", "同步已继续")
        logger.info("run=%s 同步已继续", run_id)
        return self._db.get_sync_run(run_id)

    def _run_paused(self, run_id: str) -> bool:
        run = self._db.get_sync_run(run_id)
        return run is not None and run["status"] == "paused"

    def import_dir(
        self,
        path: Path,
        labels: dict[str, str] | None = None,
        source_name: str | None = None,
    ) -> SyncReport:
        """Import a local image directory; idempotent per source name.

        Local import is a store-level convenience (no network pipeline): files
        are scanned, classified and handed straight to the persist stage.
        """
        path = Path(path)
        if not path.is_dir():
            raise ValueError(f"not a directory: {path}")
        name = source_name or f"local:{path.resolve()}"
        source = self._db.get_source_by_name(name)
        if source is None:
            source = self._db.add_source(
                name=name, kind="local", url=str(path), params={"labels": labels or {}}
            )
        else:
            self._db.update_source(
                source.id, url=str(path), params={"labels": labels or {}}
            )

        run_id = self._db.create_sync_run(source.id)
        started = time.perf_counter()
        observability.event(
            "sync_run_started", run_id=run_id, source_id=source.id, kind="local"
        )
        logger.info("run=%s 本地导入开始 source=%s dir=%s", run_id, source.id, path)
        self._db.append_sync_event(run_id, "scan", "", "info", f"扫描目录: {path}")
        persister = PersistStage(self.backend, self._db)
        report = SyncReport(source_id=source.id, source_kind="local")
        for file_path in sorted(path.iterdir()):
            if not (file_path.is_file() and file_path.suffix.lower() in IMAGE_SUFFIXES):
                continue
            report.resolved += 1
            self._import_one(persister, source, file_path, labels, report)
        self._db.update_sync_run(
            run_id,
            status="done",
            progress=100.0,
            total_files=report.resolved,
            done_files=report.resolved,
            failed_files=report.failed,
        )
        self._db.append_sync_event(
            run_id,
            "done",
            "",
            "info",
            f"导入完成：新增 {report.new}，跳过 {report.skipped_existing}，失败 {report.failed}",
        )
        logger.info(
            "run=%s 导入完成 new=%s skipped=%s failed=%s",
            run_id,
            report.new,
            report.skipped_existing,
            report.failed,
        )
        observability.event(
            "sync_run_finished",
            run_id=run_id,
            source_id=source.id,
            resolved=report.resolved,
            new=report.new,
            skipped=report.skipped_existing,
            failed=report.failed,
            duration_s=round(time.perf_counter() - started, 3),
        )
        return report

    def _import_one(
        self,
        persister: PersistStage,
        source: Source,
        file_path: Path,
        labels: dict[str, str] | None,
        report: SyncReport,
    ) -> None:
        """Persist one local file into the store and tally the outcome."""
        width, height = image_size(file_path) or (None, None)
        candidate = Candidate(
            name=file_path.name,
            path=str(file_path),
            sha256=sha256_of(file_path),
            size=file_path.stat().st_size,
            ext=file_path.suffix.lower() or ".bin",
            asset_type=classify_image(file_path, labels),
            width=width,
            height=height,
            meta={"labels": (labels or {}).get(file_path.name, {})},
        )
        try:
            outcome = persister.persist_one(source, candidate)
            if outcome == "new":
                report.new += 1
            else:
                report.skipped_existing += 1
        except Exception as exc:
            report.failed += 1
            report.errors.append(f"{file_path.name}: {exc}")
            self._db.record_download(file_path.name, source.kind, "failed", str(exc))
