"""FastAPI management UI for the asset layer (optional ``web`` extra).

Endpoints: sources CRUD, asset listing/filtering, tagging, snapshots, sync
trigger and image preview (streamed from the storage backend).
"""
from __future__ import annotations

import mimetypes
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field

from .store import AssetStore

WEBUI_PATH = Path(__file__).with_name("webui.html")
try:
    WEBUI_HTML = WEBUI_PATH.read_text(encoding="utf-8")
except OSError:  # pragma: no cover
    WEBUI_HTML = "<!doctype html><html><body><h1>asset manager</h1></body></html>"


class SourceIn(BaseModel):
    name: str
    kind: str
    url: str = ""
    license: str = ""
    description: str = ""
    params: dict = Field(default_factory=dict)


class TagIn(BaseModel):
    name: str
    group: str = "default"


class SnapshotIn(BaseModel):
    name: str = ""


class RollbackIn(BaseModel):
    version: int


def create_app(store: AssetStore) -> FastAPI:
    app = FastAPI(title="llava-instruct asset manager", version="0.1.0")

    @app.get("/", response_class=HTMLResponse)
    def index():
        return WEBUI_HTML

    @app.get("/api/info")
    def info():
        assets = store.list_assets()
        return {
            "backend": store.backend_name,
            "bucket": getattr(store.backend, "bucket", None),
            "source_count": len(store.list_sources()),
            "asset_count": len(assets),
            "ready_count": sum(1 for a in assets if a.status == "ready"),
            "failed_count": sum(1 for a in assets if a.status == "failed"),
            "snapshot_count": len(store.list_snapshots()),
        }

    # ------------------------------------------------------------- sources
    @app.get("/api/sources")
    def list_sources():
        return [asdict(s) for s in store.list_sources()]

    @app.post("/api/sources", status_code=201)
    def add_source(body: SourceIn):
        try:
            return asdict(store.add_source(**body.model_dump()))
        except Exception as exc:
            raise HTTPException(400, str(exc))

    @app.put("/api/sources/{source_id}")
    def update_source(source_id: str, body: SourceIn):
        source = store.update_source(source_id, **body.model_dump())
        if source is None:
            raise HTTPException(404, "source not found")
        return asdict(source)

    @app.delete("/api/sources/{source_id}", status_code=204)
    def delete_source(source_id: str):
        store.delete_source(source_id)

    @app.post("/api/sources/{source_id}/sync")
    def sync_source(source_id: str):
        from dataclasses import asdict as _ad

        try:
            return _ad(store.sync_source(source_id))
        except Exception as exc:
            raise HTTPException(400, str(exc))

    # -------------------------------------------------------------- assets
    @app.get("/api/assets")
    def list_assets(type: str | None = None, status: str | None = None,
                    source: str | None = None, tag: str | None = Query(default=None)):
        tags = [tag] if tag else None
        assets = store.list_assets(asset_type=type, status=status,
                                   source_id=source, tags=tags)
        return [
            {**asdict(a), "tags": a.tags}
            for a in assets
        ]

    @app.get("/api/assets/{asset_id}")
    def get_asset(asset_id: str):
        asset = store.get_asset(asset_id)
        if asset is None:
            raise HTTPException(404, "asset not found")
        return {
            **asdict(asset),
            "tags": asset.tags,
            "versions": store.version_history(asset_id),
        }

    @app.delete("/api/assets/{asset_id}", status_code=204)
    def delete_asset(asset_id: str):
        store.delete_asset(asset_id)

    @app.post("/api/assets/{asset_id}/tags", status_code=201)
    def tag_asset(asset_id: str, body: TagIn):
        try:
            store.tag_asset(asset_id, body.name, body.group)
        except ValueError as exc:
            raise HTTPException(404, str(exc))

    @app.delete("/api/assets/{asset_id}/tags/{tag_name}", status_code=204)
    def untag_asset(asset_id: str, tag_name: str):
        store.untag_asset(asset_id, tag_name)

    @app.post("/api/assets/{asset_id}/rollback")
    def rollback(asset_id: str, body: RollbackIn):
        asset = store.rollback(asset_id, body.version)
        if asset is None:
            raise HTTPException(404, "version not found")
        return {**asdict(asset), "tags": asset.tags}

    @app.get("/api/downloads")
    def list_downloads(limit: int = 20):
        return store.list_downloads(limit=limit)

    @app.get("/api/assets/{asset_id}/preview")
    def preview(asset_id: str):
        asset = store.get_asset(asset_id)
        if asset is None or not asset.object_key:
            raise HTTPException(404, "asset not found")
        if not store.backend.exists(asset.object_key):
            raise HTTPException(404, "object missing on backend")
        stream = store.backend.open_stream(asset.object_key)
        media_type = mimetypes.guess_type(asset.name)[0] or "application/octet-stream"
        return StreamingResponse(stream, media_type=media_type)

    # ----------------------------------------------------------- snapshots
    @app.post("/api/snapshots", status_code=201)
    def create_snapshot(body: SnapshotIn):
        return store.create_snapshot(name=body.name)

    @app.get("/api/snapshots")
    def list_snapshots():
        return store.list_snapshots()

    return app


def default_app(data_dir: Path | None = None) -> FastAPI:
    """Build an app wired to the default store (env-configured backend)."""
    from .api import open_store

    return create_app(open_store(data_dir=data_dir))
