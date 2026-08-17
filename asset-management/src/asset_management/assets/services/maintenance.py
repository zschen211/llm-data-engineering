"""Maintenance service: operational tasks (database backup).

Mixin of ``AssetStore``; expects ``self._db`` (Database) and ``self.data_dir``
(property on AssetStore).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from ...log import get_logger

logger = get_logger("assets.services.maintenance")


class MaintenanceService:
    def backup_db(self, out_path: Path | None = None) -> Path:
        """Backup the metadata database (online, consistent, safe while running).

        Default location: ``<data_dir>/backups/assets_<timestamp>.db``.
        Note: this backs up metadata (sources/assets/versions/tags/snapshots);
        image blobs live in the storage backend and are backed up separately.
        """
        if out_path is None:
            stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
            out_path = self.data_dir / "backups" / f"assets_{stamp}.db"
        self._db.backup_to(out_path)
        logger.info("数据库备份完成: %s (assets=%s)", out_path, self.count_assets())
        return Path(out_path)
