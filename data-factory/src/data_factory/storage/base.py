"""StorageBackend contract: the pluggable artifact-storage abstraction.

Layout (bucket ``llava-datasets`` by default):

- ``blobs/<sha256[:2]>/<sha256><ext>`` — content-addressed layer (result
  artifacts; same content stored exactly once)
- ``artifacts/<run_id>/<node_id>/<file>`` — run-path-addressed layer
  (intermediate artifacts; overwritable on re-run)
- ``datasets/<dataset_id>/v<N>/manifest.json`` — version manifest, immutable
- ``evals/<eval_set_id>/<report_id>.json|.md`` — eval report archives

``put_file`` is the content-addressed writer; ``put_object`` is the
path-addressed writer (arbitrary keys, no dedup).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

BLOBS_PREFIX = "blobs"


def content_key_for(sha256: str, ext: str) -> str:
    """Content-addressed object key (``blobs/<sha256[:2]>/<sha256><ext>``)."""
    return f"{BLOBS_PREFIX}/{sha256[:2]}/{sha256}{ext}"


def artifact_key_for(run_id: str, node_id: str, filename: str) -> str:
    return f"artifacts/{run_id}/{node_id}/{filename}"


def manifest_key_for(dataset_id: str, version: int) -> str:
    return f"datasets/{dataset_id}/v{version}/manifest.json"


def report_key_for(eval_set_id: str, report_id: str, ext: str) -> str:
    return f"evals/{eval_set_id}/{report_id}.{ext}"


class StorageBackend(ABC):
    """Pluggable artifact storage. Implementations: local disk, S3/RustFS."""

    @abstractmethod
    def put_file(self, local_path: Path, sha256: str, ext: str) -> str:
        """Store a file at its content-addressed key; return the key.
        No-op when the key already exists."""

    @abstractmethod
    def put_object(self, key: str, local_path: Path) -> str:
        """Store a file at an arbitrary key (path-addressed layer).
        No-op when the key already exists."""

    @abstractmethod
    def get_file(self, object_key: str, target: Path) -> Path:
        """Fetch an object to a local path."""

    @abstractmethod
    def exists(self, object_key: str) -> bool: ...

    @abstractmethod
    def open_stream(self, object_key: str):
        """Return a binary file-like object for streaming reads."""
