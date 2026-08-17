"""Stage registry endpoint (single-stage debug stays CLI-only)."""

from __future__ import annotations

from fastapi import APIRouter

from ..api import DataFactory
from ..strategies.stages import BUILTIN_STAGES


def make_router(factory: DataFactory) -> APIRouter:
    router = APIRouter()

    @router.get("/api/stages")
    def list_stages():
        return [
            {
                "name": s.name,
                "kind": s.kind,
                "description": s.description,
                "config_schema": s.config_schema,
            }
            for s in BUILTIN_STAGES
        ]

    return router
