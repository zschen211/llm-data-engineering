"""App-level endpoints: webui index, system info, database backup."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from ...log import get_logger
from ..api import AssetStore

logger = get_logger("assets.web")

WEBUI_PATH = Path(__file__).resolve().parent.parent / "static" / "webui.html"
try:
    WEBUI_HTML = WEBUI_PATH.read_text(encoding="utf-8")
except OSError:  # pragma: no cover
    WEBUI_HTML = "<!doctype html><html><body><h1>asset manager</h1></body></html>"


def make_router(store: AssetStore) -> APIRouter:
    router = APIRouter()

    @router.get("/", response_class=HTMLResponse)
    def index():
        return WEBUI_HTML

    @router.get("/api/info")
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

    @router.post("/api/backup", status_code=201)
    def backup_db():
        """Create a consistent metadata-db backup (online backup API)."""
        path = store.backup_db()
        return {"path": str(path), "assets": store.count_assets()}

    return router
