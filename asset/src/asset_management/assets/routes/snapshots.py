"""Snapshots resource: create and list dataset-version snapshots."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from ..api import AssetStore


class SnapshotIn(BaseModel):
    name: str = ""


def make_router(store: AssetStore) -> APIRouter:
    router = APIRouter()

    @router.post("/api/snapshots", status_code=201)
    def create_snapshot(body: SnapshotIn):
        return store.create_snapshot(name=body.name)

    @router.get("/api/snapshots")
    def list_snapshots():
        return store.list_snapshots()

    return router
