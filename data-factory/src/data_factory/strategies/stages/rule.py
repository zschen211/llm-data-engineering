"""Built-in rule QC stages (zero-GPU, core dependencies only)."""

from __future__ import annotations

from typing import ClassVar

import ray

from .base import Stage, qc_mark, register

_DEFAULT_FIELDS = (
    {"name": "question", "type": "string"},
    {"name": "answer", "type": "string"},
    {"name": "image_id", "type": "string"},
)

_FIELD_TYPES = ("string", "number", "list", "dict")


def _check_field(row: dict, spec: dict) -> bool:
    name = spec["name"]
    if spec.get("required", True) and name not in row:
        return False
    if name not in row:
        return True
    value = row[name]
    expected = spec.get("type")
    if expected == "string":
        return isinstance(value, str)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "list":
        return isinstance(value, list)
    if expected == "dict":
        return isinstance(value, dict)
    return True


@register
class SchemaCheckStage(Stage):
    """qc_rule: verify required fields exist and match their declared types.

    Config: ``{"fields": [{"name", "required", "type"}]}``. Without config
    the default schema (question/answer/image_id as strings) is enforced.
    """

    name = "schema_check"
    kind = "qc_rule"
    description = "Check required fields and their types"
    config_schema: ClassVar[dict] = {
        "fields": [{"name": "question", "required": True, "type": "string"}]
    }

    def __init__(self, config: dict | None = None):
        super().__init__(config)
        self.fields = self.config.get("fields") or _DEFAULT_FIELDS
        for spec in self.fields:
            spec.setdefault("required", True)
            if spec.get("type") and spec["type"] not in _FIELD_TYPES:
                raise ValueError(f"unknown field type: {spec['type']}")

    def row_fn(self, row: dict) -> dict:
        results = {}
        ok = True
        for spec in self.fields:
            good = _check_field(row, spec)
            ok = ok and good
            results[spec["name"]] = (
                "ok"
                if good
                else "missing"
                if spec["name"] not in row
                else f"type != {spec.get('type')}"
            )
        return qc_mark(row, ok, schema=results)


@register
class DedupStage(Stage):
    """qc_rule: mark rows whose key fields repeat earlier content as
    duplicates (keep-first). Config: ``{"fields": ["question"]}``.

    Dedup needs global row order, which a stateless per-row map cannot see
    (Ray provides no global index in 2.5+). The stage therefore materializes
    its input once — the executor already materializes between nodes — and
    marks duplicates in order; no stateful actors, deterministic everywhere.
    """

    name = "dedup"
    kind = "qc_rule"
    description = "Mark duplicate rows by key fields (keep first)"
    config_schema: ClassVar[dict] = {"fields": ["question"]}

    def transform(self, rows: ray.data.Dataset) -> ray.data.Dataset:
        fields = self.config.get("fields") or ["question"]

        def _key(row: dict) -> tuple:
            return tuple(str(row.get(f)) for f in fields)

        seen: set = set()
        marked = []
        for row in rows.take_all():
            key = _key(row)
            dup = key in seen
            seen.add(key)
            marked.append(
                qc_mark(
                    row,
                    not dup,
                    dedup={"duplicate": dup, "key": list(key)},
                )
            )
        return ray.data.from_items(marked)


@register
class FieldRangeStage(Stage):
    """qc_rule: check numeric/length ranges per field.

    Config: ``{"fields": {"answer_len": {"min": 1, "max": 200}}}`` — numeric
    fields are checked by value, string fields by length.
    """

    name = "field_range"
    kind = "qc_rule"
    description = "Check numeric value / string length ranges"
    config_schema: ClassVar[dict] = {"fields": {"answer_len": {"min": 1, "max": 200}}}

    def _check(self, row: dict, name: str, limits: dict) -> tuple[bool, str]:
        if name not in row:
            return True, "absent"
        value = row[name]
        lo, hi = limits.get("min"), limits.get("max")
        if isinstance(value, str):
            num = len(value)
            kind = "len"
        else:
            num = value
            kind = "value"
        if lo is not None and num < lo:
            return False, f"{kind} < {lo}"
        if hi is not None and num > hi:
            return False, f"{kind} > {hi}"
        return True, "ok"

    def row_fn(self, row: dict) -> dict:
        limits = self.config.get("fields") or {}
        results = {}
        ok = True
        for name, spec in limits.items():
            good, reason = self._check(row, name, spec)
            results[name] = reason
            ok = ok and good
        return qc_mark(row, ok, field_range=results)


@register
class FilterStage(Stage):
    """transform: drop rows that failed QC (``_qc.ok == false``) or hard
    errors. Config: ``{"qc": true, "errors": true}`` (both default true)."""

    name = "filter"
    kind = "transform"
    description = "Drop rows rejected by QC or per-row errors"
    config_schema: ClassVar[dict] = {"qc": True, "errors": True}

    def transform(self, rows: ray.data.Dataset) -> ray.data.Dataset:
        drop_qc = self.config.get("qc", True)
        drop_errors = self.config.get("errors", True)

        def _keep(row: dict) -> bool:
            from .base import has_error

            bad = (drop_errors and has_error(row)) or (
                drop_qc and not row.get("_qc", {}).get("ok", True)
            )
            return not bad

        return rows.filter(_keep)
