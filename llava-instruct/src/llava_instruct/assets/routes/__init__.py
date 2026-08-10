"""FastAPI app assembly for the asset layer.

One router module per API resource (``routes/<resource>.py``), each exposing
``make_router(store) -> APIRouter``. ``create_app`` mounts them all; webui
index, system info and backup live in ``routes/info.py``.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from ...log import persist_uvicorn_logs, setup_logging
from ..api import AssetStore, open_store
from .assets import make_router as assets_router
from .downloads import make_router as downloads_router
from .info import make_router as info_router
from .snapshots import make_router as snapshots_router
from .sources import make_router as sources_router
from .sync import make_router as sync_router


@asynccontextmanager
async def _lifespan(app: FastAPI):
    # Uvicorn reconfigures logging at startup; re-attach its loggers to the
    # persisted file handler from the lifespan, which runs afterwards.
    persist_uvicorn_logs()
    yield


def create_app(store: AssetStore) -> FastAPI:
    setup_logging()
    app = FastAPI(
        title="llava-instruct asset manager", version="0.1.0", lifespan=_lifespan
    )
    for router in (
        info_router(store),
        sources_router(store),
        sync_router(store),
        assets_router(store),
        downloads_router(store),
        snapshots_router(store),
    ):
        app.include_router(router)
    return app


def default_app(data_dir=None) -> FastAPI:
    """Build an app wired to the default store (env-configured backend)."""
    return create_app(open_store(data_dir=data_dir))
