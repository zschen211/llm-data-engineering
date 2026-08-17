"""App-level endpoint: factory configuration overview."""

from __future__ import annotations

from fastapi import APIRouter

from ..api import DataFactory


def make_router(factory: DataFactory) -> APIRouter:
    router = APIRouter()

    @router.get("/api/factory-info")
    def factory_info():
        return {
            "backend": factory.backend_name,
            "bucket": getattr(factory.backend, "bucket", None),
            "data_dir": str(factory.data_dir),
            "db_path": str(factory.db_path),
            "models_dir": str(factory._models_dir),
            "capability_count": len(factory.list_capability_domains()),
            "strategy_count": len(factory.list_strategies()),
            "dataset_count": len(factory.list_datasets()),
            "workflow_count": len(factory.list_workflows()),
            "run_count": len(factory.list_runs()),
            "model_count": len(factory.list_models()),
            "eval_set_count": len(factory.list_eval_sets()),
            "eval_run_count": len(factory.list_eval_runs()),
            "report_count": len(factory.list_reports()),
        }

    return router
