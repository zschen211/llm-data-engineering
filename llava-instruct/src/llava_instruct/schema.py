"""Schema contract for LLaVA-style multimodal instruction samples.

Fields follow the book (P03): id, image, asset_type, task_type, source_id,
bbox, ocr_text, conversations, split, meta.
"""
from __future__ import annotations

import json
from pathlib import Path

ASSET_TYPES = ("general_image", "document_image", "chart_image", "interleaved_pair")
TASK_TYPES = (
    "image_description",
    "counting_vqa",
    "ocr_summary",
    "document_qa",
    "chart_reading",
    "chart_comparison",
    "region_grounding",
    "multi_image_comparison",
)
SPLITS = ("train", "val", "smoke")
REQUIRED_FIELDS = (
    "id",
    "image",
    "asset_type",
    "task_type",
    "source_id",
    "conversations",
    "split",
    "meta",
)


def validate_sample(sample: dict) -> list[str]:
    """Return a list of structural errors; empty list means the sample is valid."""
    errors: list[str] = []
    for field in REQUIRED_FIELDS:
        if field not in sample:
            errors.append(f"missing field: {field}")

    if "asset_type" in sample and sample["asset_type"] not in ASSET_TYPES:
        errors.append(f"unknown asset_type: {sample['asset_type']}")
    if "task_type" in sample and sample["task_type"] not in TASK_TYPES:
        errors.append(f"unknown task_type: {sample['task_type']}")
    if "split" in sample and sample["split"] not in SPLITS:
        errors.append(f"unknown split: {sample['split']}")

    convs = sample.get("conversations")
    if not isinstance(convs, list) or not convs:
        errors.append("conversations must be a non-empty list")
    elif not convs[-1].get("from") == "gpt" or not convs[-1].get("value"):
        errors.append("last conversation turn must be a non-empty gpt answer")

    if "bbox" in sample and sample["bbox"] is not None:
        bbox_err = validate_bbox(sample["bbox"])
        if bbox_err:
            errors.append(f"invalid bbox: {bbox_err}")
    return errors


def validate_bbox(bbox: list[float]) -> str | None:
    """Validate a [x, y, w, h] bbox; return an error string or None."""
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        return "must be [x, y, w, h]"
    x, y, w, h = (float(v) for v in bbox)
    if w <= 0 or h <= 0:
        return "width and height must be positive"
    if x < 0 or y < 0:
        return "x and y must be non-negative"
    return None


def clamp_bbox(bbox: list[float], width: float, height: float) -> list[float]:
    """Clamp a bbox into [0, width] x [0, height] (grounding coordinate alignment)."""
    x, y, w, h = (float(v) for v in bbox)
    x = max(0.0, min(x, width))
    y = max(0.0, min(y, height))
    w = max(0.0, min(w, width - x))
    h = max(0.0, min(h, height - y))
    return [x, y, w, h]


def read_jsonl(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
