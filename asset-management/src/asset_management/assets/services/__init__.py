"""Business services for the asset layer, split from the old store.py by domain.

Each module defines one service class; ``AssetStore`` (assets/api.py) is the
composition facade mixing them all in. Services share state through the
composed object: ``self._db`` (metadata store) and ``self.backend`` (blob
storage) are owned and initialized by ``AssetStore.__init__``.

``services/downloaders/`` is the download pipeline (second-class citizen of
this package); its import registers the built-in processors.
"""

from . import downloaders as _downloaders  # noqa: F401  (registers processors)
from .assets import AssetsService
from .maintenance import MaintenanceService
from .materialize import MaterializeService
from .snapshots import SnapshotsService
from .sources import SourcesService
from .sync import SyncReport, SyncService
from .tags import TagsService
from .versions import VersionsService

__all__ = [
    "AssetsService",
    "MaintenanceService",
    "MaterializeService",
    "SnapshotsService",
    "SourcesService",
    "SyncReport",
    "SyncService",
    "TagsService",
    "VersionsService",
]
