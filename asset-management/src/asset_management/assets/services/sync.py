"""Sync service: the raw -> asset two-phase state machine.

Mixin of ``AssetStore``; expects ``self._db`` (Database), ``self.backend``
(StorageBackend), ``self.tmp_dir`` (Path) and ``self._hub_hook`` (test hook),
all owned by ``AssetStore.__init__``.

Covers the remote HF sync (``sync_source``: Phase A raw upload + Phase B
asset processing, both Ray Data pipelines), the process-only rerun
(``reprocess_source``, zero network) and the local import (``import_dir``,
direct persist); they return a ``SyncReport``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path

from ...log import get_logger
from ..classify import IMAGE_SUFFIXES, classify_image
from ..meta.models import Source
from .downloaders.base import Candidate, RemoteRef, image_size, sha256_of
from .downloaders.download import DownloadStage
from .downloaders.persist import PersistStage
from .downloaders.ray_data_sync import (
    BackendConfig,
    PhaseOutcome,
    SyncConfig,
    run_process_persist,
    run_raw_upload,
)
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

        The interrupted run keeps its per-file task table (sync_tasks) and
        the persistent raw-layer state (raw_files), so a resume continues at
        file granularity: raw files already uploaded skip the download phase
        and files already persisted are skipped entirely.
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
        """Run the full two-phase pipeline for one source.

        Phase A (``download_raw``): a Ray Data pipeline downloads every
        pending repo file into the ``raw/`` layer (HF cache + upload +
        ``raw_files`` registration). Phase B (``process`` + ``persist``): a
        second Ray Data pipeline loads the uploaded raw files, runs the
        processor chosen by ``params.process`` ("file" | "parquet") and
        persists the resulting assets into the content-addressed ``blobs/``
        layer. Both pipelines shard, stream (backpressure = pause) and
        retry worker crashes through Ray Data; per-file/per-asset
        application errors are caught in the row functions and counted in
        the report, so a failing file never fails the run.

        Crash recovery: each file's state lives in ``sync_tasks`` (per-run)
        and ``raw_files`` (persistent). A run left 'running'/'paused' by a
        crash becomes 'interrupted' on the next ``Database`` open; a resume
        reuses the run and skips files whose raw layer object is already
        uploaded or whose assets are already persisted. The per-source HF
        cache dir (stable across runs) avoids re-downloading fetched files.

        ``hub`` injects a fake huggingface_hub for tests; the real client is
        huggingface_hub, installed with the project.
        """
        return self._run_pipeline(source_id, run_id, hub, download=True)

    def reprocess_source(
        self, source_id: str, run_id: str | None = None, hub=None
    ) -> SyncReport:
        """Phase B only: re-run the processor over already-uploaded raw files.

        Zero network — the raw layer must already be populated (run
        ``sync_source`` first). Useful after changing ``params.process`` or
        processor parameters; per-asset sha256 dedup keeps it idempotent.
        """
        return self._run_pipeline(source_id, run_id, hub, download=False)

    def _run_pipeline(
        self, source_id: str, run_id: str | None, hub, download: bool
    ) -> SyncReport:
        source = self._validate_source(source_id)
        if run_id is None:
            run_id = self.start_sync(source_id)
        hub = hub or self._hub_hook
        report = SyncReport(source_id=source.id, source_kind=source.kind)
        started = time.perf_counter()
        observability.event(
            "sync_run_started",
            run_id=run_id,
            source_id=source.id,
            kind=source.kind,
            download=download,
        )

        def event(stage: str, message: str, level: str = "info") -> None:
            self._db.append_sync_event(run_id, stage, "", level, message)
            self._db.update_sync_run(run_id, current_stage=stage, current_file="")
            logger.info("run=%s stage=%s %s", run_id, stage, message)

        try:
            self._resume_if_interrupted(run_id)
            stage = DownloadStage.from_source(source, hub=hub)
            remotes = self._resolve_phase(
                source, run_id, report, stage, event, download
            )
            if remotes is None:
                return report
            cfg = self._sync_config(source, run_id, hub)
            paused = partial(self._run_paused, run_id)

            def on_item(_row: dict) -> None:
                self._apply_progress(run_id, report.resolved)

            if download:
                phase_b_rows = self._download_phase(
                    source, run_id, cfg, remotes, report, event, paused, on_item
                )
            else:
                event("process", "跳过下载：直接使用已入库的 raw 层文件")
                phase_b_rows = self._reprocess_rows(source.id, remotes)
            self._process_phase(
                source, run_id, cfg, phase_b_rows, report, event, paused, on_item
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

    def _resolve_phase(
        self,
        source: Source,
        run_id: str,
        report: SyncReport,
        stage: DownloadStage,
        event,
        download: bool,
    ) -> list[RemoteRef] | None:
        """Resolve the repo file list, plan tasks and record the resolve
        stage; None when there is nothing to do (run already finished)."""
        started = time.perf_counter()
        if not download:
            event("resolve", "重新解析 raw 层（跳过下载阶段）…")
        else:
            event("resolve", "解析文件清单…")
        remotes = stage.resolve()
        report.resolved = len(remotes)
        self._db.update_sync_run(run_id, total_files=len(remotes))
        if not remotes:
            self._db.update_sync_run(run_id, status="done", progress=100.0)
            event("done", "没有需要下载的文件")
            self._record_stage(
                run_id,
                "resolve",
                PhaseOutcome(
                    items=0, duration_s=round(time.perf_counter() - started, 3)
                ),
            )
            return None
        self._plan_sync(run_id, remotes, event)
        self._record_stage(
            run_id,
            "resolve",
            PhaseOutcome(
                items=len(remotes),
                done=len(remotes),
                duration_s=round(time.perf_counter() - started, 3),
            ),
        )
        return remotes

    def _download_phase(
        self,
        source: Source,
        run_id: str,
        cfg: SyncConfig,
        remotes: list[RemoteRef],
        report: SyncReport,
        event,
        paused,
        on_item,
    ) -> list[dict]:
        """Phase A: upload pending remotes into the raw layer; returns the
        Phase B rows (recomputed after the uploads: every uploaded raw file
        whose assets are not persisted yet)."""
        commit = DownloadStage.from_source(source, hub=cfg.hub).commit_hash()
        phase_a_rows = self._plan_phase_a(source.id, run_id, remotes, commit)
        if phase_a_rows:
            event("download", f"开始下载 {len(phase_a_rows)} 个文件 → raw 层")
        else:
            event("download", "raw 层已就绪，无需下载")
        outcome = run_raw_upload(cfg, phase_a_rows, paused=paused, on_item=on_item)
        self._record_stage(run_id, "download_raw", outcome)
        report.failed += outcome.failed
        report.errors.extend(outcome.errors)
        event(
            "download",
            f"原始层入库完成：成功 {outcome.done}，失败 {outcome.failed}",
            level="error" if outcome.failed else "info",
        )
        return self._processable_rows(source.id, run_id, remotes)

    def _process_phase(
        self,
        source: Source,
        run_id: str,
        cfg: SyncConfig,
        phase_b_rows: list[dict],
        report: SyncReport,
        event,
        paused,
        on_item,
    ) -> None:
        """Phase B: process uploaded raw files into assets via Ray Data."""
        if not phase_b_rows:
            event("process", "没有待处理的 raw 文件")
            self._record_stage(run_id, "process", PhaseOutcome())
            self._record_stage(run_id, "persist", PhaseOutcome())
            return
        event("process", f"开始处理 {len(phase_b_rows)} 个 raw 文件")
        process_outcome, persist_outcome = run_process_persist(
            cfg, phase_b_rows, paused=paused, on_item=on_item
        )
        self._record_stage(run_id, "process", process_outcome)
        self._record_stage(run_id, "persist", persist_outcome)
        report.new = persist_outcome.done
        report.skipped_existing = persist_outcome.skipped
        report.failed += persist_outcome.failed
        report.errors.extend(persist_outcome.errors)
        event(
            "persist",
            f"资产层完成：新增 {persist_outcome.done}，"
            f"跳过 {persist_outcome.skipped}，失败 {persist_outcome.failed}",
        )

    def _plan_phase_a(
        self, source_id: str, run_id: str, remotes: list[RemoteRef], commit: str
    ) -> list[dict]:
        """Phase A rows: files whose raw-layer object is not uploaded yet
        (files already persisted or uploaded are skipped)."""
        tasks = {t["remote_id"]: t for t in self._db.get_sync_tasks(run_id)}
        rows = []
        for remote in remotes:
            task = tasks.get(remote.id)
            if task is not None and task["status"] in ("persisted", "skipped"):
                continue
            raw = self._db.get_raw_file(source_id, remote.path_in_repo)
            if raw is None or raw["status"] != "uploaded":
                rows.append(self._download_row(remote, commit))
        return rows

    def _processable_rows(
        self, source_id: str, run_id: str, remotes: list[RemoteRef]
    ) -> list[dict]:
        """Phase B rows: uploaded raw files whose assets are not persisted
        yet (per-run task status)."""
        tasks = {t["remote_id"]: t for t in self._db.get_sync_tasks(run_id)}
        rows = []
        for remote in remotes:
            task = tasks.get(remote.id)
            if task is not None and task["status"] in ("persisted", "skipped"):
                continue
            raw = self._db.get_raw_file(source_id, remote.path_in_repo)
            if raw is not None and raw["status"] == "uploaded":
                rows.append(self._raw_row(remote, raw))
        return rows

    def _reprocess_rows(self, source_id: str, remotes: list[RemoteRef]) -> list[dict]:
        """Phase B rows for a reprocess: every uploaded raw file, regardless
        of the per-run task status (assets are deduped by sha256)."""
        rows = []
        for remote in remotes:
            raw = self._db.get_raw_file(source_id, remote.path_in_repo)
            if raw is not None and raw["status"] == "uploaded":
                rows.append(self._raw_row(remote, raw))
        return rows

    @staticmethod
    def _download_row(remote: RemoteRef, commit: str) -> dict:
        return {
            "remote_id": remote.id,
            "name": remote.name,
            "path_in_repo": remote.path_in_repo,
            "meta": remote.meta,
            "commit_hash": commit,
        }

    @staticmethod
    def _raw_row(remote: RemoteRef, raw: dict) -> dict:
        return {
            "remote_id": remote.id,
            "name": remote.name,
            "path_in_repo": remote.path_in_repo,
            "meta": remote.meta,
            "object_key": raw["object_key"],
            "sha256": raw["sha256"],
            "size": raw["size"],
        }

    def _record_stage(self, run_id: str, stage: str, outcome: PhaseOutcome) -> None:
        """Durable per-run stage record (sync_stages) + Prometheus metrics."""
        self._db.upsert_sync_stage(
            run_id,
            stage,
            duration_s=outcome.duration_s,
            item_count=outcome.items,
            failed_count=outcome.failed,
            retry_app=outcome.retry_app,
            retry_ray=outcome.retry_ray,
        )
        observability.stage_finished(
            run_id,
            stage,
            outcome.duration_s,
            item_count=outcome.items,
            failed_count=outcome.failed,
            retry_app=outcome.retry_app,
            retry_ray=outcome.retry_ray,
        )

    def _resume_if_interrupted(self, run_id: str) -> None:
        """Turn an interrupted run back into 'running' at the start of sync."""
        run = self._db.get_sync_run(run_id)
        if run is None:
            raise ValueError(f"unknown sync run: {run_id}")
        if run["status"] == "interrupted":
            self._db.update_sync_run(run_id, status="running")

    def _plan_sync(self, run_id: str, remotes: list[RemoteRef], event) -> None:
        """Register/reconcile the per-file task rows of the run."""
        total = len(remotes)
        inserted = self._db.create_sync_tasks(run_id, remotes)
        if inserted:
            event("resolve", f"解析完成：{total} 个文件，登记 {inserted} 个新任务")
        else:
            event("resolve", f"解析完成：{total} 个文件，续传 {total} 个已有任务")
        self._db.reconcile_sync_tasks(run_id, {r.id for r in remotes})

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

    def _apply_progress(self, run_id: str, total: int) -> None:
        counts = self._db.count_sync_tasks_by_status(run_id)
        done_files = counts.get("persisted", 0) + counts.get("skipped", 0)
        weighted = done_files + 0.5 * counts.get("downloaded", 0)
        self._db.update_sync_run(
            run_id,
            done_files=done_files,
            failed_files=counts.get("failed", 0),
            current_stage="",
            current_file="",
            progress=round(weighted / total * 100, 1) if total else 0.0,
        )

    def _finish_sync(self, run_id: str, report: SyncReport, event) -> None:
        tasks = self._db.get_sync_tasks(run_id)
        done_files = sum(1 for t in tasks if t["status"] in ("persisted", "skipped"))
        failed_files = sum(1 for t in tasks if t["status"] == "failed")
        report.failed = failed_files
        report.errors.extend(
            f"{t['name']}: {t['error']}"
            for t in tasks
            if t["status"] == "failed"
            and t.get("error")
            and t.get("process_attempts", 0)
        )
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

    def get_sync_stages(self, run_id: str) -> list[dict]:
        return self._db.get_sync_stages(run_id)

    def list_sync_runs(self, limit: int = 20) -> list[dict]:
        return self._db.list_sync_runs(limit=limit)

    def list_raw_files(self, source_id: str) -> list[dict]:
        return self._db.list_raw_files(source_id)

    def pause_sync(self, run_id: str) -> dict:
        """Pause a running sync run.

        The driver parks between outcome rows (pull-based backpressure
        stalls the whole pipeline); in-flight items complete, progress/state
        are kept so ``resume_sync`` can continue.
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
        """Resume a paused sync run; the driver keeps pulling rows again."""
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
