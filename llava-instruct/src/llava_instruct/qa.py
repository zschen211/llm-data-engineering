"""Quality control: structural, semantic and bbox checks (P03 section 14).

Each check returns (passes: bool, errors: list[str]).
"""
from __future__ import annotations

from pathlib import Path

from .schema import clamp_bbox, read_jsonl, validate_sample

MIN_ANSWER_WORDS = 3


def structure_check(sample: dict, image_root: Path | None = None) -> tuple[bool, list[str]]:
    errors = validate_sample(sample)
    if image_root is not None:
        for img in sample.get("image", []):
            if not (image_root / img).exists():
                errors.append(f"image not found: {img}")
    return (not errors, errors)


def semantic_check(sample: dict) -> tuple[bool, list[str]]:
    """Rule-based semantic checks: answer length, token/answer duplication."""
    errors: list[str] = []
    answer = sample["conversations"][-1]["value"]
    n_words = len(answer.split())
    if n_words < MIN_ANSWER_WORDS:
        errors.append(f"answer too short ({n_words} words < {MIN_ANSWER_WORDS})")
    question = sample["conversations"][0]["value"].replace("<image>", "").strip()
    if question.lower() in answer.lower():
        errors.append("answer repeats the question verbatim")
    return (not errors, errors)


def bbox_check(sample: dict, width: float = 1000.0, height: float = 1000.0) -> tuple[bool, list[str]]:
    bbox = sample.get("meta", {}).get("bbox") or sample.get("bbox")
    if bbox is None:
        return True, []
    errors: list[str] = []
    clamped = clamp_bbox(bbox, width, height)
    if clamped != [float(v) for v in bbox]:
        errors.append(f"bbox out of bounds, clamped {bbox} -> {clamped}")
    if width and height and (clamped[2] > width or clamped[3] > height):
        errors.append("bbox larger than image")
    return (not errors, errors)


def run_qa(
    samples: list[dict],
    image_root: Path | None = None,
    image_size: tuple[float, float] = (1000.0, 1000.0),
) -> dict:
    """Run all checks; return a report and mark failed samples in-place.

    Returns {"total", "passed", "failed", "errors_by_type", "low_quality_ids"}.
    """
    errors_by_type: dict[str, int] = {}
    low_quality: list[str] = []
    passed = 0
    for sample in samples:
        errs: list[str] = []
        ok, e = structure_check(sample, image_root)
        errs += e
        ok2, e2 = semantic_check(sample)
        errs += e2
        ok3, e3 = bbox_check(sample, *image_size)
        errs += e3
        if ok and ok2 and ok3:
            passed += 1
        else:
            low_quality.append(sample["id"])
            for err in errs:
                errors_by_type[err] = errors_by_type.get(err, 0) + 1
            sample.setdefault("meta", {})["quality"] = "low"
    return {
        "total": len(samples),
        "passed": passed,
        "failed": len(samples) - passed,
        "errors_by_type": errors_by_type,
        "low_quality_ids": low_quality,
    }


def mark_and_export(samples: list[dict], report: dict, out_path) -> list[dict]:
    """Export samples with QA results appended to meta."""
    passed_ids = set(report["low_quality_ids"])
    for sample in samples:
        meta = sample.setdefault("meta", {})
        meta["qa_pass"] = sample["id"] not in passed_ids
    from .schema import write_jsonl

    write_jsonl(out_path, samples)
    return samples
