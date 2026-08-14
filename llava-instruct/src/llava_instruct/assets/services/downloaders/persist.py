"""Persist stage: hand candidates to the storage backend + metadata index.

Dedup by sha256 (content-addressed keys), version-1 registration and download
records. This is the only stage that touches the storage layer.

Two entry shapes are supported: the local ``Candidate`` (download ->
process -> persist pipeline and local imports) and the node-agnostic
candidate row of the Ray Data pipeline (``persist_one_row``: payload bytes
or a raw-layer source key for a zero-copy server-side copy).

The dedup check-then-insert runs inside a ``BEGIN IMMEDIATE`` transaction, so
concurrent writers (Ray workers / web threads / processes) serialize on the
write lock instead of double-registering the same content.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from ...meta.db import Database, new_id
from ...meta.models import Source
from ...storage import StorageBackend, object_key_for
from .base import Candidate


class PersistStage:
    def __init__(self, backend: StorageBackend, db: Database):
        self._backend = backend
        self._db = db

    def persist_one(self, source: Source, candidate: Candidate) -> str:
        """Register one candidate; returns "new" or "skipped" (dedup).

        The read-dedup-insert sequence is atomic via ``db.transaction()``
        (BEGIN IMMEDIATE): concurrent workers block on the write lock, so the
        same sha256 is only ever registered once.
        """
        with self._db.transaction():
            key = self._backend.put_file(
                Path(candidate.path), candidate.sha256, candidate.ext
            )
            return self._register(
                source,
                candidate.sha256,
                candidate.name,
                candidate.ext,
                candidate.size,
                candidate.width,
                candidate.height,
                candidate.asset_type,
                {"downloader": source.kind, "remote": candidate.meta},
                key,
            )

    def persist_one_row(self, source: Source, row: dict) -> str:
        """Register one node-agnostic candidate row; returns "new"/"skipped".

        ``row`` carries either ``payload`` bytes (uploaded to the blob layer)
        or ``source_key`` (a raw-layer object copied server-side into the
        blob key — identity processors never re-upload bytes).
        """
        key = object_key_for(row["sha256"], row["ext"])
        with self._db.transaction():
            if row["payload"] is not None:
                self._put_payload(row)
            elif row["source_key"]:
                self._backend.copy_object(row["source_key"], key)
            else:
                raise ValueError(
                    f"candidate row {row['name']!r} has neither payload nor source_key"
                )
            meta = {
                "downloader": source.kind,
                "remote": row.get("meta") or {},
                "raw": {
                    "path_in_repo": row.get("path_in_repo", ""),
                    "sha256": row.get("raw_sha256", ""),
                },
            }
            return self._register(
                source,
                row["sha256"],
                row["name"],
                row["ext"],
                row["size"],
                row.get("width"),
                row.get("height"),
                row.get("asset_type"),
                meta,
                key,
            )

    def _put_payload(self, row: dict) -> None:
        """Write the row payload to a temp file and upload it to the blob key."""
        with tempfile.NamedTemporaryFile(suffix=row["ext"]) as tmp:
            tmp.write(row["payload"])
            tmp.flush()
            self._backend.put_file(Path(tmp.name), row["sha256"], row["ext"])

    def _register(
        self,
        source: Source,
        sha256: str,
        name: str,
        ext: str,
        size: int,
        width: int | None,
        height: int | None,
        asset_type: str,
        meta: dict,
        key: str,
    ) -> str:
        existing = self._db.get_asset_by_sha256(sha256)
        if existing is not None:
            self._db.record_download(existing.id, source.kind, "done")
            return "skipped"
        asset_id = new_id("ast_")
        self._db.add_asset(
            asset_id=asset_id,
            source_id=source.id,
            name=name,
            asset_type=asset_type,
            object_key=key,
            sha256=sha256,
            size=size,
            width=width,
            height=height,
            status="ready",
            meta=meta,
        )
        self._db.record_download(asset_id, source.kind, "done")
        return "new"

    def persist(
        self, source: Source, candidates: list[Candidate], on_progress=None
    ) -> tuple[int, int, list[str]]:
        """Persist a batch; returns (new, skipped, errors).

        ``on_progress(fraction)`` is invoked periodically with the share of
        candidates already persisted (0.0-1.0, throttled).
        """
        new = 0
        skipped = 0
        errors: list[str] = []
        total = len(candidates)
        throttle = max(1, total // 100)
        for index, candidate in enumerate(candidates):
            try:
                outcome = self.persist_one(source, candidate)
                if outcome == "new":
                    new += 1
                else:
                    skipped += 1
            except Exception as exc:
                errors.append(f"{candidate.name}: {exc}")
            if on_progress and (index + 1) % throttle == 0:
                on_progress((index + 1) / total)
        return new, skipped, errors
