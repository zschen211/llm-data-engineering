"""AssetStore: the unified facade of the asset layer.

Orchestrates DataSource -> Downloader -> StorageLocation -> metadata index,
and exposes versioning, tagging, snapshots and pool export.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import downloaders as _downloaders  # noqa: F401  (populate registry)
from .db import Database, new_id, utcnow
from .models import Asset, Source
from .registry import get_downloader
from .storage import StorageBackend


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
    def __init__(self, db_path: Path, backend: StorageBackend, tmp_dir: Path | None = None):
        self.db = Database(db_path)
        self.backend = backend
        self.tmp_dir = Path(tmp_dir or Path(db_path).parent / "tmp")
        self.tmp_dir.mkdir(parents=True, exist_ok=True)

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> "AssetStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ------------------------------------------------------------- sources
    def add_source(self, name: str, kind: str, url: str = "", license: str = "",
                   description: str = "", params: dict | None = None) -> Source:
        return self.db.add_source(name, kind, url=url, license=license,
                                  description=description, params=params)

    def list_sources(self) -> list[Source]:
        return self.db.list_sources()

    def get_source(self, source_id: str) -> Source | None:
        return self.db.get_source(source_id)

    def update_source(self, source_id: str, **fields) -> Source | None:
        return self.db.update_source(source_id, **fields)

    def delete_source(self, source_id: str) -> None:
        for asset in self.db.list_assets(source_id=source_id):
            self.db.delete_asset(asset.id)
        self.db.delete_source(source_id)

    # --------------------------------------------------------------- sync
    def sync_source(self, source_id: str) -> SyncReport:
        source = self.db.get_source(source_id)
        if source is None:
            raise ValueError(f"unknown source: {source_id}")
        if not source.enabled:
            raise ValueError(f"source {source_id} is disabled")
        downloader = get_downloader(source.kind)
        remotes = downloader.resolve(source)
        report = SyncReport(source_id=source.id, source_kind=source.kind,
                            resolved=len(remotes))
        for remote in remotes:
            target = self.tmp_dir / f"{remote.id}.part"
            try:
                result = downloader.download(remote, target)
                key = self.backend.put_file(target, result.sha256, result.ext)
                existing = self.db.get_asset_by_sha256(result.sha256)
                if existing is not None:
                    report.skipped_existing += 1
                    self.db.record_download(existing.id, source.kind, "done")
                    continue
                asset_id = new_id("ast_")
                self.db.add_asset(
                    asset_id=asset_id, source_id=source.id, name=remote.name,
                    asset_type=result.meta.get("asset_type", ""),
                    object_key=key, sha256=result.sha256, size=result.size,
                    width=result.width, height=result.height, status="ready",
                    meta={"downloader": source.kind, "remote": remote.meta},
                )
                self.db.record_download(asset_id, source.kind, "done")
                report.new += 1
            except Exception as exc:
                report.failed += 1
                report.errors.append(f"{remote.name}: {exc}")
                self.db.record_download(remote.id, source.kind, "failed", str(exc))
            finally:
                if target.exists():
                    target.unlink()
        return report

    def import_dir(self, path: Path, labels: dict[str, str] | None = None,
                   source_name: str | None = None) -> SyncReport:
        """Import a local image directory; idempotent per source name."""
        path = Path(path)
        if not path.is_dir():
            raise ValueError(f"not a directory: {path}")
        name = source_name or f"local:{path.resolve()}"
        source = self.db.get_source_by_name(name)
        if source is None:
            source = self.db.add_source(name=name, kind="local", url=str(path),
                                        params={"labels": labels or {}})
        else:
            self.db.update_source(source.id, url=str(path), params={"labels": labels or {}})
        return self.sync_source(source.id)

    # --------------------------------------------------------------- assets
    def list_assets(self, asset_type: str | None = None, status: str | None = None,
                    source_id: str | None = None, tags: list[str] | None = None) -> list[Asset]:
        return self.db.list_assets(asset_type=asset_type, status=status,
                                   source_id=source_id, tags=tags)

    def get_asset(self, asset_id: str) -> Asset | None:
        asset = self.db.get_asset(asset_id)
        if asset is not None:
            asset.tags = self.db.asset_tags(asset_id)
        return asset

    def delete_asset(self, asset_id: str) -> None:
        self.db.delete_asset(asset_id)

    # ---------------------------------------------------------------- tags
    def tag_asset(self, asset_id: str, name: str, group: str = "default") -> None:
        if self.db.get_asset(asset_id) is None:
            raise ValueError(f"unknown asset: {asset_id}")
        self.db.tag_asset(asset_id, name, group)

    def untag_asset(self, asset_id: str, name: str) -> None:
        self.db.untag_asset(asset_id, name)

    def list_tags(self, group: str | None = None) -> list[dict]:
        return [asdict(tag) for tag in self.db.list_tags(group)]

    # ------------------------------------------------------------- versions
    def version_history(self, asset_id: str) -> list[dict]:
        return [asdict(v) for v in self.db.version_history(asset_id)]

    def rollback(self, asset_id: str, version: int) -> Asset | None:
        asset = self.db.rollback(asset_id, version)
        if asset is not None:
            asset.tags = self.db.asset_tags(asset_id)
        return asset

    # ------------------------------------------------------------ snapshots
    def create_snapshot(self, name: str = "") -> dict:
        assets = self.db.list_assets(status="ready")
        snapshot = self.db.create_snapshot(assets, name=name)
        return asdict(snapshot)

    def list_snapshots(self) -> list[dict]:
        return [asdict(s) for s in self.db.list_snapshots()]

    def snapshot_assets(self, snapshot_id: str) -> list[Asset]:
        return self.db.snapshot_assets(snapshot_id)

    # ------------------------------------------------------------- materialize
    def materialize(self, out_dir: Path, tags: list[str] | None = None,
                    source_id: str | None = None) -> list[dict]:
        """Download selected assets to a local directory (for the downstream
        generate/qa/render pipeline) and return asset records with local paths."""
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        assets = self.db.list_assets(status="ready", tags=tags, source_id=source_id)
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
                    "tags": [f"{g}={n}" for g, n in self.db.asset_tags(asset.id)],
                }
            )
        return records

    def export_pool(self, out_path: Path, out_dir: Path | None = None,
                    tags: list[str] | None = None, source_id: str | None = None) -> list[dict]:
        """Materialize + write the pool manifest consumed by ``generate``."""
        from ..schema import write_jsonl

        materialized = out_dir or out_path.parent / "pool"
        records = self.materialize(materialized, tags=tags, source_id=source_id)
        write_jsonl(out_path, records)
        return records
