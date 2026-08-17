"""Ray cluster endpoints: live state of the process-wide cluster."""

from __future__ import annotations

from fastapi import APIRouter

from ..api import AssetStore
from ..services.cluster import cluster_manager


def make_router(store: AssetStore) -> APIRouter:
    router = APIRouter()

    @router.get("/api/cluster/status")
    def cluster_status():
        """Live Ray cluster state; drives the console strip indicator."""
        return cluster_manager.status()

    return router
