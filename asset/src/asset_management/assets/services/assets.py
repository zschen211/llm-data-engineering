"""Asset-query service: listing, pagination, detail, downloads ledger.

Mixin of ``AssetStore``; expects ``self._db`` (Database). Cursor tokens are
opaque base64url strings over "(created_at|id)".
"""

from __future__ import annotations

import base64
from dataclasses import asdict

from ..meta.models import Asset


def _encode_cursor(cursor: tuple[str, str]) -> str:
    return base64.urlsafe_b64encode(f"{cursor[0]}|{cursor[1]}".encode()).decode("ascii")


def _decode_cursor(cursor: str) -> tuple[str, str]:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
        created_at, sep, asset_id = raw.partition("|")
        if not sep or not created_at or not asset_id:
            raise ValueError
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError(f"invalid cursor: {cursor!r}") from exc
    return created_at, asset_id


class AssetsService:
    def list_assets(
        self,
        asset_type: str | None = None,
        status: str | None = None,
        source_id: str | None = None,
        tags: list[str] | None = None,
        q: str | None = None,
    ) -> list[Asset]:
        return self._db.list_assets(
            asset_type=asset_type, status=status, source_id=source_id, tags=tags, q=q
        )

    def list_assets_page(
        self,
        asset_type: str | None = None,
        status: str | None = None,
        source_id: str | None = None,
        tags: list[str] | None = None,
        q: str | None = None,
        cursor: str | None = None,
        page_size: int = 50,
    ) -> dict:
        """Cursor-paginated assets; returns {"items", "next_cursor", "page_size"}.

        ``cursor`` is an opaque base64url token of the previous page's last
        item ("created_at|id"); None starts at the first page.
        """
        parsed: tuple[str, str] | None = _decode_cursor(cursor) if cursor else None
        items, next_cursor = self._db.list_assets_page(
            asset_type=asset_type,
            status=status,
            source_id=source_id,
            tags=tags,
            q=q,
            cursor=parsed,
            limit=page_size,
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

    def count_assets(
        self,
        asset_type: str | None = None,
        status: str | None = None,
        source_id: str | None = None,
        tags: list[str] | None = None,
        q: str | None = None,
    ) -> int:
        return self._db.count_assets(
            asset_type=asset_type, status=status, source_id=source_id, tags=tags, q=q
        )

    def asset_tags(self, asset_id: str) -> list[tuple[str, str]]:
        return self._db.asset_tags(asset_id)

    def list_downloads(self, limit: int = 100) -> list[dict]:
        return self._db.list_downloads(limit=limit)
