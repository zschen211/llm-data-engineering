"""Assets resource: cursor-paginated listing, detail, preview, tags, rollback."""

from __future__ import annotations

import mimetypes
from dataclasses import asdict

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..api import AssetStore


class TagIn(BaseModel):
    name: str
    group: str = "default"


class RollbackIn(BaseModel):
    version: int


def make_router(store: AssetStore) -> APIRouter:
    router = APIRouter()

    @router.get("/api/assets")
    def list_assets(
        type: str | None = None,
        status: str | None = None,
        source: str | None = None,
        tag: str | None = None,
        q: str | None = None,
        cursor: str | None = None,
        page_size: int = Query(default=50, ge=1, le=200),
    ):
        """Cursor-paginated assets: {"items", "next_cursor", "page_size"}.

        Use the returned next_cursor as the cursor param for the next page;
        filters and search are evaluated server-side (SQL).
        """
        try:
            return store.list_assets_page(
                asset_type=type,
                status=status,
                source_id=source,
                tags=[tag] if tag else None,
                q=q,
                cursor=cursor,
                page_size=page_size,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.get("/api/assets/{asset_id}")
    def get_asset(asset_id: str):
        asset = store.get_asset(asset_id)
        if asset is None:
            raise HTTPException(404, "asset not found")
        return {
            **asdict(asset),
            "tags": asset.tags,
            "versions": store.version_history(asset_id),
        }

    @router.delete("/api/assets/{asset_id}", status_code=204)
    def delete_asset(asset_id: str):
        store.delete_asset(asset_id)

    @router.post("/api/assets/{asset_id}/tags", status_code=201)
    def tag_asset(asset_id: str, body: TagIn):
        try:
            store.tag_asset(asset_id, body.name, body.group)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc

    @router.delete("/api/assets/{asset_id}/tags/{tag_name}", status_code=204)
    def untag_asset(asset_id: str, tag_name: str):
        store.untag_asset(asset_id, tag_name)

    @router.post("/api/assets/{asset_id}/rollback")
    def rollback(asset_id: str, body: RollbackIn):
        asset = store.rollback(asset_id, body.version)
        if asset is None:
            raise HTTPException(404, "version not found")
        return {**asdict(asset), "tags": asset.tags}

    @router.get("/api/assets/{asset_id}/preview")
    def preview(asset_id: str):
        asset = store.get_asset(asset_id)
        if asset is None or not asset.object_key:
            raise HTTPException(404, "asset not found")
        if not store.backend.exists(asset.object_key):
            raise HTTPException(404, "object missing on backend")
        stream = store.backend.open_stream(asset.object_key)
        media_type = mimetypes.guess_type(asset.name)[0] or "application/octet-stream"
        return StreamingResponse(stream, media_type=media_type)

    return router
