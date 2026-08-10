"""Downloads resource: the download-history ledger of assets."""

from __future__ import annotations

from fastapi import APIRouter

from ..api import AssetStore


def make_router(store: AssetStore) -> APIRouter:
    router = APIRouter()

    @router.get("/api/downloads")
    def list_downloads(limit: int = 20):
        return store.list_downloads(limit=limit)

    return router
