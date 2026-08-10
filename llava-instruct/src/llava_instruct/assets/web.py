"""FastAPI management UI for the asset layer.

Endpoints: sources CRUD, asset listing/filtering, tagging, snapshots, image
preview, async sync runs (202 + polling, pause/resume control) and sync
history.
"""
from __future__ import annotations

import mimetypes
import threading
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field

from ..log import get_logger
from .api import open_store
from .store import AssetStore

logger = get_logger("assets.web")

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
        return {
            "backend": store.backend_name,
            "bucket": getattr(store.backend, "bucket", None),
            "data_dir": str(store.data_dir),
            "db_path": str(store.db_path),
            "source_count": len(store.list_sources()),
            "asset_count": store.count_assets(),
            "ready_count": store.count_assets(status="ready"),
            "failed_count": store.count_assets(status="failed"),
            "snapshot_count": len(store.list_snapshots()),
        }

    # ------------------------------------------------------------- sources
    @app.get("/api/sources")
    def list_sources():
        """Sources with their currently-running sync run id (None when idle)."""
        return [
            {**asdict(s), "running_run_id": (store.get_running_run(s.id) or {}).get("id")}
            for s in store.list_sources()
        ]

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

    @app.post("/api/sources/{source_id}/sync", status_code=202)
    def sync_source(source_id: str):
        """Start a sync run in the background; poll /api/sync/{run_id}."""
        try:
            run_id = store.start_sync(source_id)
        except ValueError as exc:
            if "already syncing" in str(exc):
                raise HTTPException(409, str(exc))
            raise HTTPException(400, str(exc))

        def _run():
            try:
                store.sync_source(source_id, run_id=run_id)
            except Exception as exc:
                logger.error("background sync run=%s failed: %s", run_id, exc)

        threading.Thread(target=_run, daemon=True).start()
        return {"run_id": run_id}

    # ---------------------------------------------------------------- sync
    @app.get("/api/sync/runs")
    def list_sync_runs(limit: int = Query(default=20, le=200)):
        return store.list_sync_runs(limit=limit)

    @app.get("/api/sync/{run_id}")
    def get_sync_run(run_id: str):
        run = store.get_sync_run(run_id)
        if run is None:
            raise HTTPException(404, "sync run not found")
        return run

    @app.post("/api/sync/{run_id}/pause")
    def pause_sync(run_id: str):
        try:
            return store.pause_sync(run_id)
        except ValueError as exc:
            raise HTTPException(404 if store.get_sync_run(run_id) is None else 400, str(exc))

    @app.post("/api/sync/{run_id}/resume")
    def resume_sync(run_id: str):
        try:
            return store.resume_sync(run_id)
        except ValueError as exc:
            raise HTTPException(404 if store.get_sync_run(run_id) is None else 400, str(exc))

    @app.get("/api/sync/{run_id}/events")
    def get_sync_events(run_id: str, after: int = 0, limit: int = Query(default=200, le=1000)):
        return store.get_sync_events(run_id, after_id=after, limit=limit)

    @app.post("/api/backup", status_code=201)
    def backup_db():
        """Create a consistent metadata-db backup (online backup API)."""
        path = store.backup_db()
        return {"path": str(path), "assets": store.count_assets()}

    # -------------------------------------------------------------- assets
    @app.get("/api/assets")
    def list_assets(type: str | None = None, status: str | None = None,
                    source: str | None = None, tag: str | None = None,
                    q: str | None = None, cursor: str | None = None,
                    page_size: int = Query(default=50, ge=1, le=200)):
        """Cursor-paginated assets: {"items", "next_cursor", "page_size"}.

        Use the returned next_cursor as the cursor param for the next page;
        filters and search are evaluated server-side (SQL).
        """
        try:
            return store.list_assets_page(
                asset_type=type, status=status, source_id=source,
                tags=[tag] if tag else None, q=q, cursor=cursor, page_size=page_size,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc))

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
    return create_app(open_store(data_dir=data_dir))
