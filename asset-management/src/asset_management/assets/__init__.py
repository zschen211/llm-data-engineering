"""Asset layer package: unified asset management (sources, download, storage,
versioning, tags, snapshots) backed by SQLite metadata + pluggable blob storage.

Public entry point is ``api.py``; the ``classify`` helpers are re-exported
here for the CLI's pool commands.
"""

from .classify import IMAGE_SUFFIXES, balance_assets, classify_image  # noqa: F401

__version__ = "0.1.0"
