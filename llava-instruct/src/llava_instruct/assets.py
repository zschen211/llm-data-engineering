"""Asset pool layer: turn raw images into a balanced, trackable asset manifest.

Three asset classes (general / document / chart) are kept balanced so task
distribution can be controlled downstream (P03 section 6).
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from .schema import ASSET_TYPES, write_jsonl

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


def classify_image(path: Path, labels: dict[str, str] | None = None) -> str:
    """Classify an image into an asset type.

    Uses an explicit per-file label map when given, otherwise a filename
    heuristic (document_/chart_ prefixes), defaulting to general_image.
    """
    if labels and path.name in labels:
        if labels[path.name] in ASSET_TYPES:
            return labels[path.name]
    stem = path.stem.lower()
    if stem.startswith("doc") or stem.startswith("page"):
        return "document_image"
    if stem.startswith("chart") or stem.startswith("fig"):
        return "chart_image"
    return "general_image"


def _asset_id(path: Path) -> str:
    digest = hashlib.sha1(str(path).encode("utf-8")).hexdigest()[:12]
    return f"asset_{digest}"


def scan_image_dir(src_dir: Path, labels: dict[str, str] | None = None) -> list[dict]:
    """Scan a directory and return one asset record per image."""
    records = []
    for path in sorted(src_dir.iterdir()):
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES:
            records.append(
                {
                    "id": _asset_id(path),
                    "path": str(path),
                    "name": path.name,
                    "asset_type": classify_image(path, labels),
                    "labels": labels.get(path.name, {}) if labels else {},
                }
            )
    return records


def balance_assets(records: list[dict], per_type: int = 29) -> list[dict]:
    """Balance assets by type, keeping at most ``per_type`` per class."""
    selected: list[dict] = []
    counts: dict[str, int] = {}
    for record in records:
        asset_type = record["asset_type"]
        if counts.get(asset_type, 0) >= per_type:
            continue
        selected.append(record)
        counts[asset_type] = counts.get(asset_type, 0) + 1
    return selected


def build_asset_pool(src_dir: Path, out_path: Path, labels: dict[str, str] | None = None,
                     per_type: int = 29) -> list[dict]:
    """Scan, balance and persist the asset pool manifest."""
    records = scan_image_dir(src_dir, labels)
    balanced = balance_assets(records, per_type=per_type)
    write_jsonl(out_path, balanced)
    return balanced


def load_asset_pool(path: Path) -> list[dict]:
    from .schema import read_jsonl

    return read_jsonl(path)
