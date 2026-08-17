"""StorageBackend contract: the pluggable blob-storage abstraction.

Two-layer layout in the bucket:

- ``raw/<source_id>/<path_in_repo>`` — the raw mirror layer (path-addressed,
  one object per downloaded repo file, no dedup)
- ``blobs/<sha256[:2]>/<sha256><ext>`` — the final asset layer
  (content-addressed, so the same content is stored exactly once)

``put_file`` is the content-addressed writer; ``put_object``/``copy_object``
are the raw-layer writers (arbitrary keys, no dedup) and the server-side
raw→blobs copy used by identity processors.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

KEY_PREFIX = "blobs"
RAW_PREFIX = "raw"


def object_key_for(sha256: str, ext: str) -> str:
    return f"{KEY_PREFIX}/{sha256[:2]}/{sha256}{ext}"


def raw_key_for(source_id: str, path_in_repo: str) -> str:
    return f"{RAW_PREFIX}/{source_id}/{path_in_repo}"


class StorageBackend(ABC):
    """Pluggable blob storage. Implementations: local disk, S3/RustFS."""

    @abstractmethod
    def put_file(self, local_path: Path, sha256: str, ext: str) -> str:
        """Store a file; return its object key. No-op when the key already exists."""

    @abstractmethod
    def put_object(self, key: str, local_path: Path) -> str:
        """Store a file at an arbitrary key (raw layer). No-op when existing."""

    @abstractmethod
    def copy_object(self, src_key: str, dst_key: str) -> str:
        """Copy between keys without pulling bytes through the client
        (S3 server-side copy / local file copy). No-op when ``dst_key``
        already exists."""

    @abstractmethod
    def get_file(self, object_key: str, target: Path) -> Path:
        """Fetch an object to a local path."""

    @abstractmethod
    def exists(self, object_key: str) -> bool: ...

    @abstractmethod
    def open_stream(self, object_key: str):
        """Return a binary file-like object for streaming reads (preview)."""
