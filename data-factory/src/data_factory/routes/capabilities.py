"""Capability domain endpoints."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..api import DataFactory
from .common import guard


class CapabilityBody(BaseModel):
    name: str = Field(min_length=1)
    description: str = ""
    parent_id: str = ""


def make_router(factory: DataFactory) -> APIRouter:
    router = APIRouter()

    @router.get("/api/capabilities")
    def list_capabilities():
        return factory.list_capability_domains()

    @router.post("/api/capabilities", status_code=201)
    def create_capability(body: CapabilityBody):
        return guard(
            factory.create_capability_domain,
            body.name,
            body.description,
            body.parent_id,
        )

    return router
