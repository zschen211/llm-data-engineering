from pathlib import Path

import pytest
from PIL import Image

from llava_instruct import qa as qa_mod
from llava_instruct import render
from llava_instruct import split
from llava_instruct.schema import write_jsonl
from llava_instruct.templates import build_conversations


def _sample(sid="s1", answer="A detailed answer.", meta=None):
    return {
        "id": sid, "image": ["a.jpg"], "asset_type": "general_image",
        "task_type": "image_description", "source_id": "asset_1",
        "conversations": build_conversations("What is this?", answer),
        "split": "train", "meta": meta or {},
    }


def test_semantic_check_short_answer():
    ok, errors = qa_mod.semantic_check(_sample(answer="Yes"))
    assert not ok
    assert errors


def test_semantic_check_question_repetition():
    ok, errors = qa_mod.semantic_check(_sample(answer="What is this? And more words here."))
    assert not ok


def test_bbox_check_clamps_out_of_bounds():
    sample = _sample(meta={"bbox": [950, 950, 100, 100]})
    ok, errors = qa_mod.bbox_check(sample, 1000, 1000)
    assert not ok
    assert "clamped" in errors[0]


def test_run_qa_marks_low_quality(tmp_path):
    (tmp_path / "a.jpg").write_bytes(b"x")
    samples = [_sample("s1", answer="Yes"), _sample("s2")]
    report = qa_mod.run_qa(samples, image_root=tmp_path)
    assert report["total"] == 2
    assert report["passed"] == 1
    assert report["low_quality_ids"] == ["s1"]
    assert samples[0]["meta"]["quality"] == "low"


def test_mark_and_export(tmp_path):
    samples = [_sample("s1")]
    report = {"low_quality_ids": ["s1"]}
    qa_mod.mark_and_export(samples, report, tmp_path / "out.jsonl")
    assert samples[0]["meta"]["qa_pass"] is False


def test_render_bboxes(tmp_path):
    img_path = tmp_path / "img.png"
    Image.new("RGB", (200, 200), "white").save(img_path)
    out = render.render_bboxes(img_path, [{"bbox": [10, 20, 50, 60], "label": "cat"}], tmp_path / "out.png")
    assert out.exists()
    assert Image.open(out).size == (200, 200)


def test_split_and_manifest(tmp_path):
    samples = [_sample(str(i)) for i in range(10)]
    result = split.split_samples(samples, seed=0, smoke=2)
    assert result["train"] and result["val"]
    assert len(result["smoke"]) == 2
    split.write_split_files(result, tmp_path)
    manifest = split.build_manifest(result, tmp_path / "manifest.jsonl")
    assert manifest["total"] == 10
    assert set(manifest["by_split"]) == {"train", "val", "smoke"}
    assert manifest["content_sha1"]
    split.write_report(manifest, {"passed": 9, "total": 10, "failed": 1, "errors_by_type": {"x": 1}, "low_quality_ids": ["s1"]}, tmp_path / "report.md")
    assert "QA" in (tmp_path / "report.md").read_text()
