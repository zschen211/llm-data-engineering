"""Process stage: transform a downloaded file into asset candidates.

The processor is selected per source via ``params.process``:
  - "file"    (default): the downloaded file IS the asset (identity)
  - "parquet": decode parquet rows into individual image assets

Adding a new data format = implement a Processor and decorate it with
``@register_processor(name)``; nothing else changes.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from ..classify import classify_image
from .base import Candidate, RemoteRef, ext_of, image_size, sha256_of

PROCESSORS: dict[str, type] = {}


def register_processor(name: str):
    def deco(cls):
        PROCESSORS[name] = cls
        return cls

    return deco


def get_processor(name: str, params: dict | None = None) -> "Processor":
    if name not in PROCESSORS:
        raise ValueError(
            f"unknown processor {name!r}; available: {sorted(PROCESSORS)}"
        )
    return PROCESSORS[name](params or {})


class Processor(ABC):
    """Transform one downloaded file into zero or more Candidates.

    The processor owns the downloaded temp file lifecycle: it may delete
    ``local_path`` after extraction (e.g. large parquet files).
    """

    name: str = ""

    def __init__(self, params: dict | None = None):
        self.params = params or {}

    @abstractmethod
    def process(self, remote: RemoteRef, local_path: Path, work_dir: Path) -> list[Candidate]:
        ...


@register_processor("file")
class FileProcessor(Processor):
    """Identity processor: the downloaded file becomes one asset."""

    name = "file"

    def process(self, remote: RemoteRef, local_path: Path, work_dir: Path) -> list[Candidate]:
        width, height = image_size(local_path) or (None, None)
        asset_type = self.params.get("asset_type") or classify_image(Path(remote.name))
        return [
            Candidate(
                name=remote.name,
                path=str(local_path),
                sha256=sha256_of(local_path),
                size=local_path.stat().st_size,
                ext=ext_of(remote.name),
                asset_type=asset_type,
                width=width,
                height=height,
                meta=dict(remote.meta),
            )
        ]
