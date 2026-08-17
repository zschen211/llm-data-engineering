"""Model registry endpoints."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..api import DataFactory
from .common import guard


class ModelBody(BaseModel):
    name: str = Field(min_length=1)
    backend: str = Field(pattern="^(local|vllm|api)$")
    model_id: str = ""
    weights_dir: str = ""
    base_url: str = ""
    api_key_env: str = ""


def make_router(factory: DataFactory) -> APIRouter:
    router = APIRouter()

    @router.get("/api/models")
    def list_models():
        return factory.list_models()

    @router.post("/api/models", status_code=201)
    def register_model(body: ModelBody):
        return guard(
            factory.register_model,
            body.name,
            body.backend,
            body.model_id,
            body.weights_dir,
            body.base_url,
            body.api_key_env,
        )

    @router.post("/api/models/scan")
    def scan_models():
        return factory.scan_models()

    @router.post("/api/models/{model_id}/check")
    def check_model(model_id: str):
        return guard(factory.check_model, model_id)

    @router.delete("/api/models/{model_id}", status_code=204)
    def remove_model(model_id: str):
        guard(factory.remove_model, model_id)

    return router
