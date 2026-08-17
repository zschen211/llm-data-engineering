"""Eval-set endpoints: import items from the request body (JSONL rows)."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..api import DataFactory
from ..meta.db import new_id
from .common import guard


class EvalItemBody(BaseModel):
    question: str | dict = Field(min_length=1)
    expected: str = ""
    category: str = ""
    rubric: dict | None = None


class EvalSetBody(BaseModel):
    name: str = Field(min_length=1)
    capability_domain_id: str = ""
    rubric: dict | None = None
    items: list[EvalItemBody] = []


def make_router(factory: DataFactory) -> APIRouter:
    router = APIRouter()

    def _import_eval_set(body: EvalSetBody):
        path = Path(factory.tmp_dir) / f"import_{new_id('es_')}.jsonl"
        with open(path, "w", encoding="utf-8") as fh:
            fh.writelines(
                json.dumps(item.model_dump(), ensure_ascii=False) + "\n"
                for item in body.items
            )
        try:
            return factory.import_eval_set(
                body.name, path, body.capability_domain_id, rubric=body.rubric
            )
        finally:
            path.unlink(missing_ok=True)

    @router.get("/api/eval-sets")
    def list_eval_sets():
        return factory.list_eval_sets()

    @router.get("/api/eval-sets/{eval_set_id}")
    def show_eval_set(eval_set_id: str):
        return guard(factory.show_eval_set, eval_set_id)

    @router.post("/api/eval-sets", status_code=201)
    def create_eval_set(body: EvalSetBody):
        return guard(_import_eval_set, body)

    return router
