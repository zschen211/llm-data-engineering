"""Data strategy endpoints."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..api import DataFactory
from .common import guard


class StrategyBody(BaseModel):
    name: str = Field(min_length=1)
    capability_domain_id: str = Field(min_length=1)
    description: str = ""


def make_router(factory: DataFactory) -> APIRouter:
    router = APIRouter()

    @router.get("/api/strategies")
    def list_strategies():
        return factory.list_strategies()

    @router.post("/api/strategies", status_code=201)
    def create_strategy(body: StrategyBody):
        return guard(
            factory.create_strategy,
            body.name,
            body.capability_domain_id,
            body.description,
        )

    return router
