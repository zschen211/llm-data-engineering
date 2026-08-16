"""Stage contract + static registry.

Row contract: every pipeline row is a JSON-serializable dict; images are
referenced by ``asset_id``/``object_key`` and never copied into rows.

QC convention: QC stages append ``row["_qc"] = {"ok": bool, ...}`` instead of
dropping rows, so a judge's verdict stays traceable downstream; the ``filter``
stage and the ``publish`` sink decide when to drop.

Failure isolation: simple stages implement ``row_fn(row)``; the default
``transform`` wraps it so a raising row becomes
``{"__error__": {...}, "row": {...}}`` instead of killing the batch (the
executor counts and the publish sink archives such rows). Stateful stages
override ``transform`` (e.g. dedup via a ``map_batches`` handler with
``concurrency=1``); sink stages implement ``write``.

The registry is static (repo convention: no dynamic imports). The ``stages``
table mirrors ``BUILTIN_STAGES`` for documentation; dispatch always uses
``REGISTRY``.
"""

from __future__ import annotations

from typing import ClassVar

import ray

from ...meta.models import StageType


class Stage:
    """A workflow stage: one Ray Data transform (or a sink write).

    Implementations are stateless wrt the dataset; state that must cross
    rows (e.g. dedup) lives in a ``map_batches`` handler with
    ``concurrency=1``.
    """

    name: str = ""
    kind: str = "transform"
    description: str = ""
    config_schema: ClassVar[dict] = {}

    def __init__(self, config: dict | None = None):
        self.config = dict(config or {})

    def row_fn(self, row: dict) -> dict:
        """Per-row transform (simple map stages); error-isolated by default."""
        raise NotImplementedError(f"stage {self.name} needs row_fn or transform")

    def transform(self, rows: ray.data.Dataset) -> ray.data.Dataset:
        """Transform a Ray dataset. Default: map ``row_fn`` with per-row
        error isolation (one bad row never fails the stage)."""
        fn = self.row_fn

        def _safe(row: dict) -> dict:
            try:
                return fn(row)
            except Exception as exc:
                return row_error(exc, row)

        return rows.map(_safe)


class SinkStage(Stage):
    """A sink stage: persists rows, creates version + lineage, returns stats."""

    kind = "sink"

    def write(self, rows: list[dict], ctx: StageContext) -> dict:
        raise NotImplementedError(f"stage {self.name} is not a sink stage")


class StageContext:
    """Executor-provided context for sink stages (DB/storage/run identity)."""

    def __init__(self, db, backend, tmp_dir, run_id: str, node_id: str):
        self.db = db
        self.backend = backend
        self.tmp_dir = tmp_dir
        self.run_id = run_id
        self.node_id = node_id


REGISTRY: dict[str, type[Stage]] = {}


def register(stage_cls: type[Stage]) -> type[Stage]:
    REGISTRY[stage_cls.name] = stage_cls
    return stage_cls


def build_stage(stage_name: str, config: dict | None = None) -> Stage:
    """Instantiate a registered stage (registry key = stable stage name)."""
    try:
        stage_cls = REGISTRY[stage_name]
    except KeyError as exc:
        raise KeyError(f"unknown stage: {stage_name}") from exc
    return stage_cls(config)


def stage_type_for(stage_cls: type[Stage]) -> StageType:
    return StageType(
        name=stage_cls.name,
        module=f"{stage_cls.__module__}.{stage_cls.__name__}",
        kind=stage_cls.kind,
        description=stage_cls.description,
        config_schema=stage_cls.config_schema,
    )


def row_error(exc: BaseException, row: dict) -> dict:
    """Wrap a row whose transform raised: the row survives as metadata."""
    return {
        "__error__": {"type": type(exc).__name__, "message": str(exc)},
        "row": row,
    }


def has_error(row: dict) -> bool:
    return "__error__" in row


def qc_mark(row: dict, ok: bool, **checks) -> dict:
    """Attach a QC verdict to a row (append to existing checks)."""
    prev = row.get("_qc", {"ok": True, "checks": {}})
    prev["checks"].update(checks)
    prev["ok"] = prev["ok"] and ok
    row["_qc"] = prev
    return row
