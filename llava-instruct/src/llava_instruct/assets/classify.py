"""Asset classification: turn images into asset types.

``classify_image`` is the filename-heuristic classifier used by the asset
pipeline (local import + file processor); ``balance_assets`` caps how many
assets of each type survive the CLI's pool export (P03 section 6).
"""

from __future__ import annotations

from pathlib import Path

from ..schema import ASSET_TYPES

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


def classify_image(path: Path, labels: dict[str, str] | None = None) -> str:
    """Classify an image into an asset type.

    Uses an explicit per-file label map when given, otherwise a filename
    heuristic (document_/chart_ prefixes), defaulting to general_image.
    """
    if labels and path.name in labels and labels[path.name] in ASSET_TYPES:
        return labels[path.name]
    stem = path.stem.lower()
    if stem.startswith(("doc", "page")):
        return "document_image"
    if stem.startswith(("chart", "fig")):
        return "chart_image"
    return "general_image"


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
