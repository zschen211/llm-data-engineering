"""Workflow run endpoints (create/execute/cancel/list/show)."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..api import DataFactory
from .common import guard


class RunBody(BaseModel):
    workflow_id: str = Field(min_length=1)
    input_dataset_id: str = Field(min_length=1)
    params: dict | None = None


def make_router(factory: DataFactory) -> APIRouter:
    router = APIRouter()

    @router.get("/api/runs")
    def list_runs(workflow_id: str = ""):
        return factory.list_runs(workflow_id)

    @router.get("/api/runs/{run_id}")
    def show_run(run_id: str):
        return guard(factory.show_run, run_id)

    @router.post("/api/runs", status_code=201)
    def create_run(body: RunBody):
        return guard(
            factory.create_run, body.workflow_id, body.input_dataset_id, body.params
        )

    @router.post("/api/runs/{run_id}/run", status_code=202)
    def execute_run(run_id: str):
        return guard(factory.run_workflow, run_id)

    @router.post("/api/runs/{run_id}/cancel")
    def cancel_run(run_id: str):
        return guard(factory.cancel_run, run_id)

    return router
