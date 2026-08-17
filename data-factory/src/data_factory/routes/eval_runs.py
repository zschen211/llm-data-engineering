"""Eval-run endpoints (create/execute/list/show)."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..api import DataFactory
from .common import guard


class EvalRunBody(BaseModel):
    eval_set_id: str = Field(min_length=1)
    model_id: str = Field(min_length=1)


def make_router(factory: DataFactory) -> APIRouter:
    router = APIRouter()

    @router.get("/api/eval-runs")
    def list_eval_runs(eval_set_id: str = ""):
        return factory.list_eval_runs(eval_set_id)

    @router.get("/api/eval-runs/{eval_run_id}")
    def show_eval_run(eval_run_id: str):
        return guard(factory.show_eval_run, eval_run_id)

    @router.post("/api/eval-runs", status_code=201)
    def create_eval_run(body: EvalRunBody):
        return guard(factory.create_eval_run, body.eval_set_id, body.model_id)

    @router.post("/api/eval-runs/{eval_run_id}/run", status_code=202)
    def execute_eval_run(eval_run_id: str, concurrency: int = 4):
        return guard(factory.run_eval, eval_run_id, concurrency)

    return router
