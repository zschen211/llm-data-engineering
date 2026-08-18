"""Asset datasets resource: per-source dataset aggregation for the console.

The path is ``/api/asset-datasets`` (not ``/api/datasets``) so the single-
origin gateway split keeps working: ``/api/datasets`` belongs to
data-factory's run-input datasets. Mirrors the ``factory-info`` convention
of namespacing a shared-ish noun with the owning service.
"""

from __future__ import annotations

from fastapi import APIRouter

from ..api import AssetStore


def make_router(store: AssetStore) -> APIRouter:
    router = APIRouter()

    @router.get("/api/asset-datasets")
    def list_datasets():
        return store.list_datasets()

    return router
