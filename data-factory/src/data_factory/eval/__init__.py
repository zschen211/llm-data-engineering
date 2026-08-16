"""Eval subsystem: model registry, clients, scorers, runner, reports."""

from __future__ import annotations

from .models import ModelClient, build_client, build_client_for_model
from .registry import ModelRegistryService
from .runner import EvalRunner
from .scorers import score
from .service import EvalService

__all__ = [
    "EvalRunner",
    "EvalService",
    "ModelClient",
    "ModelRegistryService",
    "build_client",
    "build_client_for_model",
    "score",
]
