"""Version service: asset content versioning + rollback.

Mixin of ``AssetStore``; expects ``self._db`` (Database).
"""

from __future__ import annotations

from dataclasses import asdict

from ..meta.models import Asset


class VersionsService:
    def bump_version(
        self, asset_id: str, sha256: str, object_key: str, change_note: str
    ) -> None:
        self._db.bump_version(asset_id, sha256, object_key, change_note)

    def version_history(self, asset_id: str) -> list[dict]:
        return [asdict(v) for v in self._db.version_history(asset_id)]

    def rollback(self, asset_id: str, version: int) -> Asset | None:
        asset = self._db.rollback(asset_id, version)
        if asset is not None:
            asset.tags = self._db.asset_tags(asset_id)
        return asset
