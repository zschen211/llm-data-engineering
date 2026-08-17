"""FastAPI app assembly for data-factory.

One router module per API resource (``routes/<resource>.py``), each exposing
``make_router(factory) -> APIRouter``; ``create_app`` mounts them all under
``/api/*`` (contract: infra/docs/contract.md). ``default_app`` wires a
factory from the environment so uvicorn can boot it directly::

    scripts/serve.sh                  # or:
    uv run uvicorn data_factory.routes:default_app --factory --port 8001
"""

from __future__ import annotations

from fastapi import FastAPI

from ..api import DataFactory, open_factory
from ..log import setup_logging
from . import (
    capabilities,
    datasets,
    eval_runs,
    eval_sets,
    info,
    lineage,
    models,
    reports,
    runs,
    stages,
    strategies,
    workflows,
)
from .common import MetricsMiddleware, metrics_response


def create_app(factory: DataFactory) -> FastAPI:
    setup_logging()
    app = FastAPI(title="data-factory", version="0.1.0")
    app.add_middleware(MetricsMiddleware)
    app.add_api_route("/metrics", metrics_response, methods=["GET"])
    for router in (
        info.make_router(factory),
        capabilities.make_router(factory),
        strategies.make_router(factory),
        datasets.make_router(factory),
        workflows.make_router(factory),
        runs.make_router(factory),
        stages.make_router(factory),
        models.make_router(factory),
        eval_sets.make_router(factory),
        eval_runs.make_router(factory),
        reports.make_router(factory),
        lineage.make_router(factory),
    ):
        app.include_router(router)
    return app


def default_app(data_dir=None) -> FastAPI:
    """Build an app wired to a factory with the env-configured backend."""
    return create_app(open_factory(data_dir=data_dir))
