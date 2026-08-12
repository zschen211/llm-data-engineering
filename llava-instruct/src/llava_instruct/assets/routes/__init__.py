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
from ..services.cluster import cluster_manager
from ..services.obs import MetricsMiddleware, observability
from .assets import make_router as assets_router
from .cluster import make_router as cluster_router
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
    # Own the Ray cluster for the app's lifetime: started once here, reused
    # by every sync (run_ray_sync only ensures it is up). If the cluster was
    # already initialized by someone else, it is reused and not shut down.
    cluster_manager.ensure_started()
    observability.start()
    status = cluster_manager.status()
    if status["initialized"]:
        observability.event(
            "ray_cluster_started",
            dashboard_url=status["dashboard_url"],
            gcs_address=status["address"],
            logs_dir=status["logs_dir"],
            metrics_port=status["metrics_port"],
            total_cpus=status["total_cpus"],
        )
    try:
        yield
    finally:
        observability.event("ray_cluster_stopped")
        observability.stop()
        cluster_manager.stop()


def create_app(store: AssetStore) -> FastAPI:
    setup_logging()
    app = FastAPI(
        title="llava-instruct asset manager", version="0.1.0", lifespan=_lifespan
    )
    app.add_middleware(MetricsMiddleware)
    app.mount("/metrics", observability.metrics_app())
    for router in (
        info_router(store),
        sources_router(store),
        sync_router(store),
        assets_router(store),
        downloads_router(store),
        snapshots_router(store),
        cluster_router(store),
    ):
        app.include_router(router)
    return app


def default_app(data_dir=None) -> FastAPI:
    """Build an app wired to the default store (env-configured backend)."""
    return create_app(open_store(data_dir=data_dir))
