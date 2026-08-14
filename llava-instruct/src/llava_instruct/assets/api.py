"""Unified public API of the asset layer.

Other modules (data-processing pipelines, CLI, Web UI, notebooks) should
access the asset layer exclusively through this module::

    from llava_instruct.assets.api import open_store, AssetStore

    with open_store() as store:                  # env-configured (RustFS or local)
        store.import_dir(Path("./images"), labels={"a.png": "chart_image"})
        assets = store.list_assets(tags=["task=chart"], status="ready")
        snapshot = store.create_snapshot(name="v1")
        records = store.materialize(Path("./pool"))

    # explicit backend (e.g. a specific RustFS instance):
    from llava_instruct.assets.storage import S3StorageBackend
    backend = S3StorageBackend("http://localhost:9000", "user", "secret", "my-bucket")
    with open_store(backend=backend) as store:
        ...

Everything underneath (Database, StorageBackend, services, downloaders) is
internal; only the public methods of ``AssetStore`` and the ``open_store``
factory are stable across versions.

``AssetStore`` is a composition facade over the per-domain service classes in
``services/`` (sources / sync / assets / tags / versions / snapshots /
materialize / maintenance); this module wires them together with the shared
state (Database + StorageBackend) and exposes the ``SyncReport`` type.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Self

from ..log import get_logger
from .meta.db import Database
from .meta.models import Asset, Source
from .services import (
    AssetsService,
    MaintenanceService,
    MaterializeService,
    SnapshotsService,
    SourcesService,
    SyncReport,
    SyncService,
    TagsService,
    VersionsService,
)
from .storage import LocalStorageBackend, S3StorageBackend, StorageBackend

__all__ = ["Asset", "AssetStore", "Source", "SyncReport", "open_store"]

logger = get_logger("assets.api")

DEFAULT_DATA_DIR = Path(os.environ.get("LLAVA_DATA_DIR", "data"))


def _resolve_backend(data_dir: Path) -> StorageBackend:
    """Resolve the storage backend from the environment.

    ``LLAVA_STORAGE_BACKEND``: ``rustfs`` (requires endpoint + credentials,
    missing config raises instead of silently falling back), ``local``
    (forces the local content-addressed directory), ``auto`` (default:
    ``RUSTFS_ENDPOINT`` → RustFS/S3, else local with a loud warning so a
    misconfigured deployment never silently stores blobs on disk).
    """
    switch = os.environ.get("LLAVA_STORAGE_BACKEND", "auto").lower()
    endpoint = os.environ.get("RUSTFS_ENDPOINT")
    if switch not in ("auto", "local", "rustfs"):
        raise ValueError(
            f"unknown LLAVA_STORAGE_BACKEND {switch!r} (auto|local|rustfs)"
        )
    if switch == "rustfs" and not endpoint:
        raise ValueError("LLAVA_STORAGE_BACKEND=rustfs requires RUSTFS_ENDPOINT")
    use_rustfs = (bool(endpoint) and switch != "local") or switch == "rustfs"
    if not use_rustfs:
        logger.warning(
            "no RUSTFS_ENDPOINT configured — falling back to LOCAL storage "
            "backend at %s (blobs will NOT go to RustFS); set "
            "LLAVA_STORAGE_BACKEND=rustfs to require RustFS",
            data_dir / "blobs",
        )
        return LocalStorageBackend(data_dir / "blobs")
    if not (
        os.environ.get("RUSTFS_ACCESS_KEY") and os.environ.get("RUSTFS_SECRET_KEY")
    ):
        raise ValueError(
            "RUSTFS_ENDPOINT is set but RUSTFS_ACCESS_KEY / RUSTFS_SECRET_KEY are missing"
        )
    logger.info("storage backend: rustfs (%s)", endpoint)
    return S3StorageBackend(
        endpoint,
        os.environ["RUSTFS_ACCESS_KEY"],
        os.environ["RUSTFS_SECRET_KEY"],
        os.environ.get("RUSTFS_BUCKET", "llava-assets"),
    )


def open_store(
    data_dir: Path | None = None, backend: StorageBackend | None = None
) -> AssetStore:
    """Build an AssetStore from configuration (env or explicit backend).

    Backend resolution: an explicit ``backend`` wins; otherwise the
    ``LLAVA_STORAGE_BACKEND`` switch selects the target (see
    ``_resolve_backend``).
    """
    data_dir = Path(data_dir or DEFAULT_DATA_DIR)
    if backend is None:
        backend = _resolve_backend(data_dir)
    return AssetStore(data_dir / "assets.db", backend, tmp_dir=data_dir / "tmp")


class AssetStore(
    SourcesService,
    SyncService,
    AssetsService,
    TagsService,
    VersionsService,
    SnapshotsService,
    MaterializeService,
    MaintenanceService,
):
    """Composition facade over the per-domain services (see ``services/``).

    Owns the shared state every service operates on: ``self._db`` (metadata
    store), ``self.backend`` (blob storage), the sync temp dir and the test
    hub hook. Lifecycle helpers (close / context manager) live here; all
    business methods come from the mixed-in service classes.
    """

    def __init__(
        self,
        db_path: Path,
        backend: StorageBackend,
        tmp_dir: Path | None = None,
        hub=None,
    ):
        self._db = Database(db_path)
        self.backend = backend
        self.tmp_dir = Path(tmp_dir or Path(db_path).parent / "tmp")
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        self._hub_hook = hub  # test injection; sync_source falls back to it

    def close(self) -> None:
        self._db.close()

    @property
    def backend_name(self) -> str:
        return getattr(self.backend, "name", type(self.backend).__name__)

    @property
    def db_path(self) -> Path:
        return self._db.path

    @property
    def data_dir(self) -> Path:
        return self._db.path.parent

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc) -> None:
        self.close()
