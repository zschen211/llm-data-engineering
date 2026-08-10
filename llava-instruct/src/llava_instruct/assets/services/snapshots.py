"""Snapshot service: freeze a set of ready assets under a version id.

Mixin of ``AssetStore``; expects ``self._db`` (Database).
"""

from __future__ import annotations

from dataclasses import asdict

from ..meta.models import Asset


class SnapshotsService:
    def create_snapshot(self, name: str = "") -> dict:
        assets = self._db.list_assets(status="ready")
        snapshot = self._db.create_snapshot(assets, name=name)
        return asdict(snapshot)

    def list_snapshots(self) -> list[dict]:
        return [asdict(s) for s in self._db.list_snapshots()]

    def snapshot_assets(self, snapshot_id: str) -> list[Asset]:
        return self._db.snapshot_assets(snapshot_id)
