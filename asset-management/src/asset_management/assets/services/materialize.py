"""Materialize service: pull blobs back to local for the downstream pipeline.

Mixin of ``AssetStore``; expects ``self._db`` (Database) and ``self.backend``
(StorageBackend).
"""

from __future__ import annotations

from pathlib import Path

from ...schema import write_jsonl


class MaterializeService:
    def materialize(
        self, out_dir: Path, tags: list[str] | None = None, source_id: str | None = None
    ) -> list[dict]:
        """Download selected assets to a local directory (for the downstream
        generate/qa/render pipeline) and return asset records with local paths."""
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        assets = self._db.list_assets(status="ready", tags=tags, source_id=source_id)
        records = []
        for asset in assets:
            local = out_dir / asset.name
            self.backend.get_file(asset.object_key, local)
            records.append(
                {
                    "id": asset.id,
                    "path": str(local),
                    "name": asset.name,
                    "asset_type": asset.asset_type,
                    "labels": (asset.meta or {}).get("remote", {}).get("labels", {}),
                    "sha256": asset.sha256,
                    "size": asset.size,
                    "width": asset.width,
                    "height": asset.height,
                    "source_id": asset.source_id,
                    "tags": [f"{g}={n}" for g, n in self._db.asset_tags(asset.id)],
                }
            )
        return records

    def export_pool(
        self,
        out_path: Path,
        out_dir: Path | None = None,
        tags: list[str] | None = None,
        source_id: str | None = None,
    ) -> list[dict]:
        """Materialize + write the pool manifest consumed by ``generate``."""
        materialized = out_dir or out_path.parent / "pool"
        records = self.materialize(materialized, tags=tags, source_id=source_id)
        write_jsonl(out_path, records)
        return records
