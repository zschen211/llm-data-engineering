"""Lineage endpoints (one of run/dataset/strategy must be given)."""

from __future__ import annotations

from fastapi import APIRouter

from .. import lineage
from ..api import DataFactory
from .common import guard


def make_router(factory: DataFactory) -> APIRouter:
    router = APIRouter()

    @router.get("/api/lineage")
    def get_lineage(run_id: str = "", dataset_id: str = "", strategy_id: str = ""):
        if run_id:
            return guard(lineage.by_run, factory._db, run_id)
        if dataset_id:
            did, _, ver = dataset_id.partition("@")
            version = int(ver) if ver else None
            return guard(lineage.by_dataset, factory._db, did, version)
        if strategy_id:
            return guard(lineage.by_strategy, factory._db, strategy_id)
        from fastapi import HTTPException

        raise HTTPException(
            status_code=400,
            detail="one of run_id / dataset_id / strategy_id is required",
        )

    return router
