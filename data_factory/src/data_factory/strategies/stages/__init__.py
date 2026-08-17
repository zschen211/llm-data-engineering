"""Built-in stage registry.

Importing this package registers every built-in stage into ``REGISTRY``
(static, per repo convention); ``BUILTIN_STAGES`` mirrors the same list as
metadata rows for the ``stages`` table.
"""

from __future__ import annotations

from . import llm as _llm  # noqa: F401  (registers qc_llm)
from . import rule as _rule  # noqa: F401  (registers rule QC stages)
from . import sink as _sink  # noqa: F401  (registers publish)
from .base import (
    REGISTRY,
    SinkStage,
    Stage,
    StageContext,
    build_stage,
    stage_type_for,
)

BUILTIN_STAGES = [stage_type_for(cls) for cls in REGISTRY.values()]

__all__ = [
    "BUILTIN_STAGES",
    "REGISTRY",
    "SinkStage",
    "Stage",
    "StageContext",
    "build_stage",
    "stage_type_for",
]
