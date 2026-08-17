"""Shared contracts and helpers for the download -> process -> persist pipeline.

Pipeline flow (per source):
  DownloadStage  : resolve remote files, fetch them (retry + parallel workers)
  Processor      : transform a downloaded file into asset candidates
                   ("file" = identity; "parquet" = decode rows into images)
  PersistStage   : hand candidates to the storage backend + metadata index
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image


@dataclass
class RemoteRef:
    """A file to fetch from a data source (currently a HuggingFace repo)."""

    id: str
    name: str
    path_in_repo: str
    meta: dict = field(default_factory=dict)


@dataclass
class Candidate:
    """One asset produced by a Processor, ready for the persist stage."""

    name: str
    path: str
    sha256: str
    size: int
    ext: str
    asset_type: str = "general_image"
    width: int | None = None
    height: int | None = None
    meta: dict = field(default_factory=dict)


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def image_size(path: Path) -> tuple[int, int] | None:
    """Return (width, height) for image files; None when not decodable."""
    try:
        with Image.open(path) as img:
            return img.width, img.height
    except Exception:
        return None


def ext_of(name: str) -> str:
    suffix = Path(name).suffix.lower()
    return suffix or ".bin"
