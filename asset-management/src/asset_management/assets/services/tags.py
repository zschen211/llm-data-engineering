"""Tag service: attach/detach/query asset tags (grouped).

Mixin of ``AssetStore``; expects ``self._db`` (Database).
"""

from __future__ import annotations

from dataclasses import asdict


class TagsService:
    def tag_asset(self, asset_id: str, name: str, group: str = "default") -> None:
        if self._db.get_asset(asset_id) is None:
            raise ValueError(f"unknown asset: {asset_id}")
        self._db.tag_asset(asset_id, name, group)

    def untag_asset(self, asset_id: str, name: str) -> None:
        self._db.untag_asset(asset_id, name)

    def list_tags(self, group: str | None = None) -> list[dict]:
        return [asdict(tag) for tag in self._db.list_tags(group)]
