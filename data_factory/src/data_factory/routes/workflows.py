"""Workflow endpoints (define/validate/list/show)."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..api import DataFactory
from .common import guard


class StageSpec(BaseModel):
    stage: str = Field(min_length=1)
    config: dict | None = None


class WorkflowBody(BaseModel):
    strategy_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    stages: list[StageSpec] = []
    description: str = ""


def make_router(factory: DataFactory) -> APIRouter:
    router = APIRouter()

    @router.get("/api/workflows")
    def list_workflows():
        return factory.list_workflows()

    @router.get("/api/workflows/{workflow_id}")
    def show_workflow(workflow_id: str):
        return guard(factory.show_workflow, workflow_id)

    @router.post("/api/workflows", status_code=201)
    def define_workflow(body: WorkflowBody):
        stages = [(s.stage, s.config) for s in body.stages]
        return guard(
            factory.define_workflow,
            body.strategy_id,
            body.name,
            stages,
            body.description,
        )

    @router.post("/api/workflows/{workflow_id}/validate")
    def validate_workflow(workflow_id: str):
        return {"order": guard(factory.validate_workflow, workflow_id)}

    return router
