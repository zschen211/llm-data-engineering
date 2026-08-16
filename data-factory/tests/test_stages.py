"""Stage behavior tests (unit, via Ray Data datasets)."""

from typing import ClassVar

import pytest
import ray

from data_factory.strategies.stages import build_stage


def _run(stage_name: str, rows: list[dict], config=None) -> list[dict]:
    stage = build_stage(stage_name, config)
    return stage.transform(ray.data.from_items(rows)).take_all()


def test_schema_check_default():
    out = _run(
        "schema_check",
        [
            {"question": "q", "answer": "a", "image_id": "i"},
            {"question": "q", "answer": "a"},
            {"question": 5, "answer": "a", "image_id": "i"},
        ],
    )
    assert out[0]["_qc"]["ok"] is True
    assert out[1]["_qc"]["ok"] is False
    assert out[2]["_qc"]["ok"] is False
    assert out[2]["_qc"]["checks"]["schema"]["question"] == "type != string"


def test_schema_check_custom_fields():
    out = _run(
        "schema_check",
        [{"n": 1}, {"n": "x"}],
        {"fields": [{"name": "n", "type": "number"}]},
    )
    assert out[0]["_qc"]["ok"] is True
    assert out[1]["_qc"]["ok"] is False


def test_schema_check_unknown_type_rejected():
    with pytest.raises(ValueError, match="unknown field type"):
        build_stage("schema_check", {"fields": [{"name": "n", "type": "blob"}]})


def test_dedup_marks_second_occurrence():
    out = _run(
        "dedup",
        [
            {"question": "same"},
            {"question": "same"},
            {"question": "other"},
        ],
    )
    assert out[0]["_qc"]["checks"]["dedup"]["duplicate"] is False
    assert out[1]["_qc"]["checks"]["dedup"]["duplicate"] is True
    assert out[2]["_qc"]["checks"]["dedup"]["duplicate"] is False
    assert out[0]["_qc"]["ok"] is True
    assert out[1]["_qc"]["ok"] is False


def test_field_range_numeric_and_length():
    out = _run(
        "field_range",
        [
            {"n": 5, "s": "hello"},
            {"n": 99, "s": "hi"},
        ],
        {"fields": {"n": {"min": 1, "max": 10}, "s": {"min": 5}}},
    )
    assert out[0]["_qc"]["ok"] is True
    assert out[1]["_qc"]["ok"] is False
    assert out[1]["_qc"]["checks"]["field_range"]["n"] == "value > 10"


def test_filter_drops_rejected():
    rows = [
        {"_qc": {"ok": True}},
        {"_qc": {"ok": False}},
        {"__error__": {"type": "X"}},
    ]
    out = _run("filter", rows)
    assert len(out) == 1


def test_filter_keeps_qc_rejects_when_configured():
    rows = [{"_qc": {"ok": False}}, {"__error__": {"type": "X"}}]
    out = _run("filter", rows, {"errors": False})
    assert len(out) == 1
    assert "__error__" in out[0]
    out = _run("filter", rows, {"qc": False})
    assert len(out) == 1
    assert "_qc" in out[0]


def test_default_transform_isolates_row_errors():
    from data_factory.strategies.stages.base import Stage

    class Boom(Stage):
        name = "boom"
        kind = "transform"
        description = ""
        config_schema: ClassVar[dict] = {}

        def __init__(self, config=None):
            self.config = dict(config or {})

        def row_fn(self, row):
            if row["v"] == 2:
                raise ValueError("boom")
            return row

    stage = Boom()
    out = stage.transform(
        ray.data.from_items([{"v": 1}, {"v": 2}, {"v": 3}])
    ).take_all()
    assert len(out) == 3
    assert "__error__" in out[1]
    assert out[1]["row"]["v"] == 2


def test_publish_is_sink():
    stage = build_stage("publish", {"dataset_id": "ds_1"})
    assert stage.kind == "sink"


def test_qc_llm_requires_judge():
    with pytest.raises(ValueError, match="judge_model_id"):
        build_stage("qc_llm", {})
