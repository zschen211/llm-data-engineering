"""AssetStore: the unified facade of the asset layer.

This is the single programmatic entry point for other modules — CLI, Web UI
and any downstream data-processing module should only ever talk to
``AssetStore`` (or the ``open_store`` factory) and never to Database or
StorageBackend internals directly.

Typical usage from another module::

    from llava_instruct.assets.api import open_store

    with open_store(data_dir="data") as store:      # env-configured backend
        report = store.import_dir(Path("./images"))
        assets = store.list_assets(tags=["task=chart"])
"""
from __future__ import annotations

import base64
import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from ..log import get_logger
from ..schema import write_jsonl
from . import downloaders as _downloaders  # noqa: F401  (registers processors)
from .classify import IMAGE_SUFFIXES, classify_image
from .db import Database, new_id, utcnow
from .downloaders.base import Candidate, image_size, sha256_of
from .downloaders.download import DownloadStage
from .downloaders.persist import PersistStage
from .downloaders.ray_sync import BackendConfig, SyncConfig, run_ray_sync
from .models import Asset, Source
from .storage import LocalStorageBackend, S3StorageBackend, StorageBackend

logger = get_logger("assets.store")

DEFAULT_DATA_DIR = Path(os.environ.get("LLAVA_DATA_DIR", "data"))


def _encode_cursor(cursor: tuple[str, str]) -> str:
    return base64.urlsafe_b64encode(f"{cursor[0]}|{cursor[1]}".encode("utf-8")).decode("ascii")


def _decode_cursor(cursor: str) -> tuple[str, str]:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
        created_at, sep, asset_id = raw.partition("|")
        if not sep or not created_at or not asset_id:
            raise ValueError
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError(f"invalid cursor: {cursor!r}") from exc
    return created_at, asset_id


def open_store(data_dir: Path | None = None, backend: StorageBackend | None = None) -> "AssetStore":
    """Build an AssetStore from configuration (env or explicit backend).

    Backend resolution: an explicit ``backend`` wins; otherwise
    ``RUSTFS_ENDPOINT`` (+ access/secret/bucket) selects the RustFS/S3 backend,
    and the local content-addressed directory is the fallback.
    """
    data_dir = Path(data_dir or DEFAULT_DATA_DIR)
    if backend is None:
        endpoint = os.environ.get("RUSTFS_ENDPOINT")
        if endpoint:
            if not (os.environ.get("RUSTFS_ACCESS_KEY") and os.environ.get("RUSTFS_SECRET_KEY")):
                raise ValueError(
                    "RUSTFS_ENDPOINT is set but RUSTFS_ACCESS_KEY / RUSTFS_SECRET_KEY are missing"
                )
            backend = S3StorageBackend(
                endpoint,
                os.environ["RUSTFS_ACCESS_KEY"],
                os.environ["RUSTFS_SECRET_KEY"],
                os.environ.get("RUSTFS_BUCKET", "llava-assets"),
            )
        else:
            backend = LocalStorageBackend(data_dir / "blobs")
    return AssetStore(data_dir / "assets.db", backend, tmp_dir=data_dir / "tmp")


@dataclass
class SyncReport:
    source_id: str
    source_kind: str = ""
    resolved: int = 0
    new: int = 0
    skipped_existing: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)


class AssetStore:
    def __init__(self, db_path: Path, backend: StorageBackend, tmp_dir: Path | None = None,
                 hub=None):
        self._db = Database(db_path)
        self.backend = backend
        self.tmp_dir = Path(tmp_dir or Path(db_path).parent / "tmp")
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        self._hub_hook = hub  # test injection; sync_source falls back to it

    def close(self) -> None:
        self._db.close()

    @property
    def backend_name(self) -> str:
        return getattr(self.backend, "name", type(self.backend).__name__)

    @property
    def db_path(self) -> Path:
        return self._db.path

    @property
    def data_dir(self) -> Path:
        return self._db.path.parent

    def __enter__(self) -> "AssetStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ------------------------------------------------------------- sources
    def add_source(self, name: str, kind: str, url: str = "", license: str = "",
                   description: str = "", params: dict | None = None) -> Source:
        return self._db.add_source(name, kind, url=url, license=license,
                                  description=description, params=params)

    def list_sources(self) -> list[Source]:
        return self._db.list_sources()

    def get_source(self, source_id: str) -> Source | None:
        return self._db.get_source(source_id)

    def get_source_by_name(self, name: str) -> Source | None:
        return self._db.get_source_by_name(name)

    def update_source(self, source_id: str, **fields) -> Source | None:
        return self._db.update_source(source_id, **fields)

    def delete_source(self, source_id: str) -> None:
        for asset in self._db.list_assets(source_id=source_id):
            self._db.delete_asset(asset.id)
        self._db.delete_source(source_id)

    # --------------------------------------------------------------- sync
    def start_sync(self, source_id: str) -> str:
        """Validate a source and open a persistent sync run; returns run_id.

        Raises ValueError when the source is unknown/disabled/not huggingface
        or already has an active sync run (running or paused).
        """
        source = self._db.get_source(source_id)
        if source is None:
            raise ValueError(f"unknown source: {source_id}")
        if not source.enabled:
            raise ValueError(f"source {source_id} is disabled")
        if source.kind != "huggingface":
            raise ValueError(f"only the 'huggingface' source kind is supported, got {source.kind!r}")
        active = [
            r for r in self._db.list_sync_runs(limit=100)
            if r["source_id"] == source_id and r["status"] in ("running", "paused")
        ]
        if active:
            raise ValueError(f"source {source_id} is already syncing (run={active[0]['id']})")
        return self._db.create_sync_run(source_id)

    def sync_source(self, source_id: str, run_id: str | None = None,
                    hub=None) -> SyncReport:
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

        ``hub`` injects a fake huggingface_hub for tests; the real client is
        huggingface_hub, installed with the project.
        """
        source = self._db.get_source(source_id)
        if source is None:
            raise ValueError(f"unknown source: {source_id}")
        if not source.enabled:
            raise ValueError(f"source {source_id} is disabled")
        if source.kind != "huggingface":
            raise ValueError(f"only the 'huggingface' source kind is supported, got {source.kind!r}")
        if run_id is None:
            run_id = self.start_sync(source_id)

        if hub is None:
            hub = self._hub_hook
        stage = DownloadStage.from_source(source, hub=hub)
        report = SyncReport(source_id=source.id, source_kind=source.kind)
        total_remotes = 0
        processed_remotes = 0

        def event(stage: str, message: str, level: str = "info") -> None:
            self._db.append_sync_event(run_id, stage, "", level, message)
            self._db.update_sync_run(run_id, current_stage=stage, current_file="")
            logger.info("run=%s stage=%s %s", run_id, stage, message)

        try:
            logger.info("run=%s 同步开始 source=%s", run_id, source_id)
            event("resolve", "解析文件清单…")
            remotes = stage.resolve()
            total_remotes = len(remotes)
            report.resolved = total_remotes
            self._db.update_sync_run(run_id, total_files=total_remotes)
            event("resolve", f"解析完成：{total_remotes} 个文件")
            if not remotes:
                self._db.update_sync_run(run_id, status="done", progress=100.0)
                event("done", "没有需要下载的文件")
                return report

            cfg = SyncConfig(
                db_path=str(self._db.path),
                backend=BackendConfig.from_backend(self.backend),
                tmp_dir=str(self.tmp_dir),
                source=source,
                run_id=run_id,
                workers=max(1, int(source.params.get("workers", 2))),
                attempts=int(source.params.get("attempts", 3)),
                hub=hub,
            )
            for outcome in run_ray_sync(cfg, remotes,
                                        paused=lambda: self._run_paused(run_id)):
                report.new += outcome.new
                report.skipped_existing += outcome.skipped
                for error in outcome.errors:
                    report.failed += 1
                    report.errors.append(f"{outcome.remote}: {error}")
                processed_remotes += 1
                self._db.update_sync_run(
                    run_id,
                    done_files=report.new + report.skipped_existing,
                    failed_files=report.failed,
                    current_stage="", current_file="",
                    progress=round(processed_remotes / total_remotes * 100, 1),
                )

            self._db.update_sync_run(run_id, status="done", progress=100.0)
            event("done", f"同步完成：新增 {report.new}，跳过 {report.skipped_existing}，失败 {report.failed}")
            logger.info("run=%s 同步完成 new=%s skipped=%s failed=%s", run_id,
                        report.new, report.skipped_existing, report.failed)
        except Exception as exc:
            self._db.update_sync_run(run_id, status="failed", error=str(exc))
            self._db.append_sync_event(run_id, "error", "", "error", f"同步失败: {exc}")
            logger.error("run=%s 同步失败: %s", run_id, exc)
            raise
        return report

    def get_sync_run(self, run_id: str) -> dict | None:
        return self._db.get_sync_run(run_id)

    def get_running_run(self, source_id: str) -> dict | None:
        return self._db.get_running_run(source_id)

    def get_sync_events(self, run_id: str, after_id: int = 0, limit: int = 200) -> list[dict]:
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
            raise ValueError(f"sync run {run_id} is not running (status={run['status']})")
        self._db.update_sync_run(run_id, status="paused")
        self._db.append_sync_event(run_id, "control", "", "info", "同步已暂停，可随时继续")
        logger.info("run=%s 同步已暂停", run_id)
        return self._db.get_sync_run(run_id)

    def resume_sync(self, run_id: str) -> dict:
        """Resume a paused sync run; tasks pick up their loops again."""
        run = self._db.get_sync_run(run_id)
        if run is None:
            raise ValueError(f"unknown sync run: {run_id}")
        if run["status"] != "paused":
            raise ValueError(f"sync run {run_id} is not paused (status={run['status']})")
        self._db.update_sync_run(run_id, status="running")
        self._db.append_sync_event(run_id, "control", "", "info", "同步已继续")
        logger.info("run=%s 同步已继续", run_id)
        return self._db.get_sync_run(run_id)

    def _run_paused(self, run_id: str) -> bool:
        run = self._db.get_sync_run(run_id)
        return run is not None and run["status"] == "paused"

    def backup_db(self, out_path: Path | None = None) -> Path:
        """Backup the metadata database (online, consistent, safe while running).

        Default location: ``<data_dir>/backups/assets_<timestamp>.db``.
        Note: this backs up metadata (sources/assets/versions/tags/snapshots);
        image blobs live in the storage backend and are backed up separately.
        """
        if out_path is None:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            out_path = self.data_dir / "backups" / f"assets_{stamp}.db"
        self._db.backup_to(out_path)
        logger.info("数据库备份完成: %s (assets=%s)", out_path, self.count_assets())
        return Path(out_path)

    def import_dir(self, path: Path, labels: dict[str, str] | None = None,
                   source_name: str | None = None) -> SyncReport:
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
            source = self._db.add_source(name=name, kind="local", url=str(path),
                                         params={"labels": labels or {}})
        else:
            self._db.update_source(source.id, url=str(path), params={"labels": labels or {}})

        run_id = self._db.create_sync_run(source.id)
        logger.info("run=%s 本地导入开始 source=%s dir=%s", run_id, source.id, path)
        self._db.append_sync_event(run_id, "scan", "", "info", f"扫描目录: {path}")
        persister = PersistStage(self.backend, self._db)
        report = SyncReport(source_id=source.id, source_kind="local")
        for file_path in sorted(path.iterdir()):
            if not (file_path.is_file() and file_path.suffix.lower() in IMAGE_SUFFIXES):
                continue
            report.resolved += 1
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
        self._db.update_sync_run(run_id, status="done", progress=100.0,
                                 total_files=report.resolved,
                                 done_files=report.resolved,
                                 failed_files=report.failed)
        self._db.append_sync_event(run_id, "done", "", "info",
                                   f"导入完成：新增 {report.new}，跳过 {report.skipped_existing}，失败 {report.failed}")
        logger.info("run=%s 导入完成 new=%s skipped=%s failed=%s", run_id,
                    report.new, report.skipped_existing, report.failed)
        return report

    # --------------------------------------------------------------- assets
    def list_assets(self, asset_type: str | None = None, status: str | None = None,
                    source_id: str | None = None, tags: list[str] | None = None,
                    q: str | None = None) -> list[Asset]:
        return self._db.list_assets(asset_type=asset_type, status=status,
                                    source_id=source_id, tags=tags, q=q)

    def list_assets_page(self, asset_type: str | None = None, status: str | None = None,
                         source_id: str | None = None, tags: list[str] | None = None,
                         q: str | None = None, cursor: str | None = None,
                         page_size: int = 50) -> dict:
        """Cursor-paginated assets; returns {"items", "next_cursor", "page_size"}.

        ``cursor`` is an opaque base64url token of the previous page's last
        item ("created_at|id"); None starts at the first page.
        """
        parsed: tuple[str, str] | None = _decode_cursor(cursor) if cursor else None
        items, next_cursor = self._db.list_assets_page(
            asset_type=asset_type, status=status, source_id=source_id,
            tags=tags, q=q, cursor=parsed, limit=page_size,
        )
        return {
            "items": [{**asdict(a), "tags": a.tags} for a in items],
            "next_cursor": _encode_cursor(next_cursor) if next_cursor else None,
            "page_size": page_size,
        }

    def get_asset(self, asset_id: str) -> Asset | None:
        asset = self._db.get_asset(asset_id)
        if asset is not None:
            asset.tags = self._db.asset_tags(asset_id)
        return asset

    def delete_asset(self, asset_id: str) -> None:
        self._db.delete_asset(asset_id)

    def count_assets(self, asset_type: str | None = None, status: str | None = None,
                     source_id: str | None = None, tags: list[str] | None = None,
                     q: str | None = None) -> int:
        return self._db.count_assets(asset_type=asset_type, status=status,
                                     source_id=source_id, tags=tags, q=q)

    def asset_tags(self, asset_id: str) -> list[tuple[str, str]]:
        return self._db.asset_tags(asset_id)

    def list_downloads(self, limit: int = 100) -> list[dict]:
        return self._db.list_downloads(limit=limit)

    # ---------------------------------------------------------------- tags
    def tag_asset(self, asset_id: str, name: str, group: str = "default") -> None:
        if self._db.get_asset(asset_id) is None:
            raise ValueError(f"unknown asset: {asset_id}")
        self._db.tag_asset(asset_id, name, group)

    def untag_asset(self, asset_id: str, name: str) -> None:
        self._db.untag_asset(asset_id, name)

    def list_tags(self, group: str | None = None) -> list[dict]:
        return [asdict(tag) for tag in self._db.list_tags(group)]

    # ------------------------------------------------------------- versions
    def bump_version(self, asset_id: str, sha256: str, object_key: str,
                     change_note: str) -> None:
        self._db.bump_version(asset_id, sha256, object_key, change_note)

    def version_history(self, asset_id: str) -> list[dict]:
        return [asdict(v) for v in self._db.version_history(asset_id)]

    def rollback(self, asset_id: str, version: int) -> Asset | None:
        asset = self._db.rollback(asset_id, version)
        if asset is not None:
            asset.tags = self._db.asset_tags(asset_id)
        return asset

    # ------------------------------------------------------------ snapshots
    def create_snapshot(self, name: str = "") -> dict:
        assets = self._db.list_assets(status="ready")
        snapshot = self._db.create_snapshot(assets, name=name)
        return asdict(snapshot)

    def list_snapshots(self) -> list[dict]:
        return [asdict(s) for s in self._db.list_snapshots()]

    def snapshot_assets(self, snapshot_id: str) -> list[Asset]:
        return self._db.snapshot_assets(snapshot_id)

    # ------------------------------------------------------------- materialize
    def materialize(self, out_dir: Path, tags: list[str] | None = None,
                    source_id: str | None = None) -> list[dict]:
        """Download selected assets to a local directory (for the downstream
        generate/qa/render pipeline) and return asset records with local paths."""
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        assets = self._db.list_assets(status="ready", tags=tags, source_id=source_id)
        records = []
        for asset in assets:
            local = out_dir / asset.name
            self.backend.get_file(asset.object_key, local)
            records.append(
                {
                    "id": asset.id,
                    "path": str(local),
                    "name": asset.name,
                    "asset_type": asset.asset_type,
                    "labels": (asset.meta or {}).get("remote", {}).get("labels", {}),
                    "sha256": asset.sha256,
                    "size": asset.size,
                    "width": asset.width,
                    "height": asset.height,
                    "source_id": asset.source_id,
                    "tags": [f"{g}={n}" for g, n in self._db.asset_tags(asset.id)],
                }
            )
        return records

    def export_pool(self, out_path: Path, out_dir: Path | None = None,
                    tags: list[str] | None = None, source_id: str | None = None) -> list[dict]:
        """Materialize + write the pool manifest consumed by ``generate``."""
        materialized = out_dir or out_path.parent / "pool"
        records = self.materialize(materialized, tags=tags, source_id=source_id)
        write_jsonl(out_path, records)
        return records
