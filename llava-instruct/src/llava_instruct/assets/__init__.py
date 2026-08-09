"""Asset layer package: unified asset management (sources, download, storage,
versioning, tags, snapshots) backed by SQLite metadata + pluggable blob storage.

Legacy helpers (scan/classify/balance) are re-exported from ``classify`` for
backward compatibility with the pre-asset-layer pipeline.
"""
from .classify import (  # noqa: F401
    IMAGE_SUFFIXES,
    balance_assets,
    build_asset_pool,
    classify_image,
    load_asset_pool,
    scan_image_dir,
)

__version__ = "0.1.0"
