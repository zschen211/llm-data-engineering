"""Dataset definition endpoints."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..api import DataFactory
from .common import guard


class DatasetBody(BaseModel):
    name: str = Field(min_length=1)
    source_type: str = Field(pattern="^(snapshot|import|derived)$")
    snapshot_id: str = ""
    tag_filters: list[dict] | None = None
    import_manifest: str = ""
    derived_from: str = ""


def make_router(factory: DataFactory) -> APIRouter:
    router = APIRouter()

    @router.get("/api/datasets")
    def list_datasets():
        return factory.list_datasets()

    @router.post("/api/datasets", status_code=201)
    def create_dataset(body: DatasetBody):
        return guard(
            factory.create_dataset,
            body.name,
            body.source_type,
            body.snapshot_id,
            body.tag_filters,
            body.import_manifest,
            body.derived_from,
        )

    return router
