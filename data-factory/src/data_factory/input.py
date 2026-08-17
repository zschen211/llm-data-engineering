"""Dataset input materialization: snapshot / import / derived -> rows.

- ``snapshot``: consumes the asset-management asset layer through its public
  API only (``asset_management.assets.api``); tag filters intersect the
  snapshot's assets. The run pins the snapshot + filters, so later asset
  label drift never changes a run's input.
- ``import``: rows come from a JSONL manifest (local path or an object key
  in the factory's own storage backend).
- ``derived``: rows come from an upstream dataset version
  (``dataset_id@version``, default latest).
"""

from __future__ import annotations

import os
from pathlib import Path

from asset_management.assets.api import open_store

from . import jsonl
from .meta import models as m

INPUT_NODE_ID = "input"


def _snapshot_rows(dataset: m.DatasetDefinition) -> list[dict]:
    # data dir is resolved per call so a later env change (tests) is honored
    # even though asset_management binds its default at import time.
    data_dir = Path(os.environ.get("ASSET_DATA_DIR", "data"))
    with open_store(data_dir=data_dir) as store:
        assets = store.snapshot_assets(dataset.snapshot_id)
        filters = dataset.tag_filters or []
        for filt in filters:
            tag = filt["name"] if "name" in filt else filt.get("group", "")
            if "group" in filt:
                tag = f"{filt['group']}={tag}"
            wanted = {a.id for a in store.list_assets(tags=[tag])}
            assets = [a for a in assets if a.id in wanted]
        return [
            {
                "asset_id": a.id,
                "object_key": a.object_key,
                "name": a.name,
                "asset_type": a.asset_type,
                "sha256": a.sha256,
                "size": a.size,
                "width": a.width,
                "height": a.height,
                "tags": [f"{g}={n}" for g, n in a.tags],
            }
            for a in assets
        ]


def _import_rows(dataset: m.DatasetDefinition, backend) -> list[dict]:
    target = dataset.import_manifest
    if Path(target).is_file():
        return jsonl.read_rows_from_path(Path(target))
    return jsonl.read_rows(backend, target)


def _derived_rows(dataset: m.DatasetDefinition, db, backend) -> list[dict]:
    ref = dataset.derived_from
    if "@" in ref:
        did, ver = ref.split("@", 1)
        version = int(ver)
    else:
        did, version = ref, None
    if version is None:
        versions = db.list_dataset_versions(did)
        if not versions:
            raise ValueError(f"dataset {did} has no published version")
        version = versions[0].version
    dv = db.get_dataset_version(did, version)
    if dv is None:
        raise ValueError(f"dataset {did}@v{version} not found")
    manifest = jsonl.read_manifest(backend, dv.manifest_key)
    files = manifest.get("files") or []
    if not files:
        raise ValueError(f"manifest {dv.manifest_key} has no files")
    return jsonl.read_rows(backend, files[0]["object_key"])


def materialize_input(
    db, backend, dataset: m.DatasetDefinition, version: int
) -> list[dict]:
    """Build the immutable input row list for a run."""
    if dataset.source_type == "snapshot":
        rows = _snapshot_rows(dataset)
    elif dataset.source_type == "import":
        rows = _import_rows(dataset, backend)
    elif dataset.source_type == "derived":
        rows = _derived_rows(dataset, db, backend)
    else:
        raise ValueError(f"unknown source_type: {dataset.source_type}")
    return [
        {
            **row,
            "_source": {
                "dataset_id": dataset.id,
                "version": version,
                "source_type": dataset.source_type,
            },
        }
        for row in rows
    ]
