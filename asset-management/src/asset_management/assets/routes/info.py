"""App-level endpoints: index pointer, system info, database backup.

The management UI lives in the standalone ``frontend/`` SPA (dev :5173,
production served by the infra nginx gateway); this service only serves a
pointer page at ``/`` so a bare browser hit is not a blank 404.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from ...log import get_logger
from ..api import AssetStore

logger = get_logger("assets.web")

INDEX_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>asset-management API</title>
  <style>
    body { font-family: ui-monospace, monospace; background: #eef1f5;
           color: #17202e; padding: 48px; }
    a { color: #1e56c8; }
  </style>
</head>
<body>
  <h1>asset-management · 管理 API</h1>
  <p>前端控制台是独立的 frontend/ SPA：开发 <a href="http://localhost:5173">http://localhost:5173</a>，
  生产经 infra nginx 网关单源访问。API 见 <code>/api/*</code>（契约：
  infra/docs/contract.md），Prometheus 埋点见 <code>/metrics</code>。</p>
</body>
</html>"""


def make_router(store: AssetStore) -> APIRouter:
    router = APIRouter()

    @router.get("/", response_class=HTMLResponse)
    def index():
        return INDEX_HTML

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
