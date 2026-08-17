"""Storage backend selection + pluggable backends.

Env contract (mirrors the asset layer): ``DFAC_STORAGE_BACKEND`` in
``auto|local|s3`` selects the backend; ``auto`` (default) uses RustFS when
``RUSTFS_ENDPOINT`` is configured and falls back to local with a loud warning.
"""

from __future__ import annotations

import os
from pathlib import Path

from ..log import get_logger
from .base import StorageBackend
from .local import LocalStorageBackend
from .s3 import S3StorageBackend

__all__ = [
    "LocalStorageBackend",
    "S3StorageBackend",
    "StorageBackend",
    "resolve_backend",
]

logger = get_logger("storage")


def resolve_backend(
    data_dir: Path, backend: StorageBackend | None = None
) -> StorageBackend:
    """Resolve the storage backend from the environment (or explicit arg).

    ``DFAC_STORAGE_BACKEND``: ``s3``/``rustfs`` (requires endpoint +
    credentials; missing config raises instead of silently falling back),
    ``local`` (forces the local directory), ``auto`` (default:
    ``RUSTFS_ENDPOINT`` → S3/RustFS, else local with a loud warning so a
    misconfigured deployment never silently stores artifacts on disk).
    """
    if backend is not None:
        return backend
    switch = os.environ.get("DFAC_STORAGE_BACKEND", "auto").lower()
    endpoint = os.environ.get("RUSTFS_ENDPOINT")
    if switch not in ("auto", "local", "s3", "rustfs"):
        raise ValueError(f"unknown DFAC_STORAGE_BACKEND {switch!r} (auto|local|s3)")
    if switch in ("s3", "rustfs") and not endpoint:
        raise ValueError(f"DFAC_STORAGE_BACKEND={switch} requires RUSTFS_ENDPOINT")
    use_s3 = (bool(endpoint) and switch != "local") or switch in ("s3", "rustfs")
    if not use_s3:
        logger.warning(
            "no RUSTFS_ENDPOINT configured — falling back to LOCAL storage "
            "backend at %s; set DFAC_STORAGE_BACKEND=s3 to require S3",
            data_dir / "artifacts",
        )
        return LocalStorageBackend(data_dir / "artifacts")
    if not (
        os.environ.get("RUSTFS_ACCESS_KEY") and os.environ.get("RUSTFS_SECRET_KEY")
    ):
        raise ValueError(
            "RUSTFS_ENDPOINT is set but RUSTFS_ACCESS_KEY / RUSTFS_SECRET_KEY are missing"
        )
    logger.info("storage backend: s3 (%s)", endpoint)
    return S3StorageBackend(
        endpoint,
        os.environ["RUSTFS_ACCESS_KEY"],
        os.environ["RUSTFS_SECRET_KEY"],
        os.environ.get("RUSTFS_BUCKET", "dfac-datasets"),
    )
