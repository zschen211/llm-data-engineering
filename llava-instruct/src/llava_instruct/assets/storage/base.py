"""StorageBackend contract: the pluggable blob-storage abstraction.

Object keys are content-addressed: ``blobs/<sha256[:2]>/<sha256><ext>`` so the
same content is stored exactly once and the key is bound to the content.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

KEY_PREFIX = "blobs"


def object_key_for(sha256: str, ext: str) -> str:
    return f"{KEY_PREFIX}/{sha256[:2]}/{sha256}{ext}"


class StorageBackend(ABC):
    """Pluggable blob storage. Implementations: local disk, S3/RustFS."""

    @abstractmethod
    def put_file(self, local_path: Path, sha256: str, ext: str) -> str:
        """Store a file; return its object key. No-op when the key already exists."""

    @abstractmethod
    def get_file(self, object_key: str, target: Path) -> Path:
        """Fetch an object to a local path."""

    @abstractmethod
    def exists(self, object_key: str) -> bool: ...

    @abstractmethod
    def open_stream(self, object_key: str):
        """Return a binary file-like object for streaming reads (preview)."""
