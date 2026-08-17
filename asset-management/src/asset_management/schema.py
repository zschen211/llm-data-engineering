"""Shared constants and JSONL helpers for the asset layer.

``ASSET_TYPES`` is the closed set of asset types used by classification
(``assets/classify.py``); ``write_jsonl`` is the manifest writer used by
``export_pool`` (``assets/services/materialize.py``).
"""

from __future__ import annotations

import json
from pathlib import Path

ASSET_TYPES = ("general_image", "document_image", "chart_image", "interleaved_pair")


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(
            json.dumps(record, ensure_ascii=False) + "\n" for record in records
        )
