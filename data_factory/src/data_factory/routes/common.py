"""Shared route helpers: facade-error mapping and request metrics.

Metrics follow the infra contract (``asset_`` prefix); the registry and its
metric objects are module-level singletons so repeated app creation (tests)
never re-registers duplicates. ``reset_metrics`` exists for tests.
"""

from __future__ import annotations

import time

from fastapi import HTTPException
from fastapi.responses import Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Histogram,
    generate_latest,
)
from starlette.middleware.base import BaseHTTPMiddleware

_registry = CollectorRegistry()
_requests = Counter(
    "asset_http_requests_total",
    "HTTP requests handled by the data-factory web app",
    ["method", "route", "status"],
    registry=_registry,
)
_duration = Histogram(
    "asset_http_request_duration_seconds",
    "HTTP request handling latency",
    ["method", "route"],
    registry=_registry,
)


def guard(fn, *args, **kwargs):
    """Call a DataFactory facade method, mapping exceptions to HTTP codes.

    ``ValueError`` → 400 (bad input / unknown id), ``RuntimeError`` → 409
    (conflict: already running / model not ready), anything else bubbles up.
    """
    try:
        return fn(*args, **kwargs)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def metrics_response() -> Response:
    """Prometheus text exposition of the module registry (served at /metrics)."""
    return Response(content=generate_latest(_registry), media_type=CONTENT_TYPE_LATEST)


def reset_metrics() -> None:
    """Test support: rebuild the registry to drop accumulated samples."""
    global _registry, _requests, _duration
    _registry = CollectorRegistry()
    _requests = Counter(
        "asset_http_requests_total",
        "HTTP requests handled by the data-factory web app",
        ["method", "route", "status"],
        registry=_registry,
    )
    _duration = Histogram(
        "asset_http_request_duration_seconds",
        "HTTP request handling latency",
        ["method", "route"],
        registry=_registry,
    )


class MetricsMiddleware(BaseHTTPMiddleware):
    """Starlette middleware recording request count and latency."""

    async def dispatch(self, request, call_next):
        start = time.perf_counter()
        response = await call_next(request)
        route = getattr(request.scope.get("route"), "path", None) or request.url.path
        _requests.labels(
            method=request.method,
            route=route,
            status=str(response.status_code),
        ).inc()
        _duration.labels(method=request.method, route=route).observe(
            time.perf_counter() - start
        )
        return response
