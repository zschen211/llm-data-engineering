"""Data-source service: source CRUD + cascading delete.

Mixin of ``AssetStore``; expects ``self._db`` (Database).
"""

from __future__ import annotations

from ..meta.models import Source


class SourcesService:
    def add_source(
        self,
        name: str,
        kind: str,
        url: str = "",
        license: str = "",
        description: str = "",
        params: dict | None = None,
    ) -> Source:
        return self._db.add_source(
            name, kind, url=url, license=license, description=description, params=params
        )

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
