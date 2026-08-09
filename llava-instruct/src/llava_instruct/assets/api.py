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

Everything underneath (Database, StorageBackend, downloaders) is internal;
only the public methods of ``AssetStore`` and the ``open_store`` factory are
stable across versions.
"""
from .models import Asset, Source  # noqa: F401
from .store import AssetStore, SyncReport, open_store  # noqa: F401

__all__ = [
    "AssetStore",
    "SyncReport",
    "open_store",
    "Asset",
    "Source",
]
