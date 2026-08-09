"""Downloader base contract and shared helpers."""
from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

from ..models import Source


@dataclass
class RemoteAsset:
    id: str
    name: str
    url: str = ""
    expected_sha256: str | None = None
    meta: dict = field(default_factory=dict)


@dataclass
class DownloadResult:
    sha256: str
    size: int
    ext: str
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
        from PIL import Image

        with Image.open(path) as img:
            return img.width, img.height
    except Exception:
        return None


def ext_of(name: str) -> str:
    suffix = Path(name).suffix.lower()
    return suffix or ".bin"


class BaseDownloader(ABC):
    """One downloader per data source kind: enumerate resources, then fetch."""

    kind: str = ""

    @abstractmethod
    def resolve(self, source: Source) -> list[RemoteAsset]:
        """Enumerate the resources available on this source."""

    @abstractmethod
    def download(self, remote: RemoteAsset, target: Path) -> DownloadResult:
        """Fetch one remote asset to ``target`` and compute its sha256."""
