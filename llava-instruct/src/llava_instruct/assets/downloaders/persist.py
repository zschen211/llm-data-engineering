"""Persist stage: hand candidates to the storage backend + metadata index.

Dedup by sha256 (content-addressed keys), version-1 registration and download
records. This is the only stage that touches the storage layer.

The dedup check-then-insert runs inside a ``BEGIN IMMEDIATE`` transaction, so
concurrent writers (Ray workers / web threads / processes) serialize on the
write lock instead of double-registering the same content.
"""
from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path

from ..db import Database, new_id
from ..models import Source
from ..storage import StorageBackend
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
            key = self._backend.put_file(Path(candidate.path), candidate.sha256, candidate.ext)
            existing = self._db.get_asset_by_sha256(candidate.sha256)
            if existing is not None:
                self._db.record_download(existing.id, source.kind, "done")
                return "skipped"
            asset_id = new_id("ast_")
            self._db.add_asset(
                asset_id=asset_id, source_id=source.id, name=candidate.name,
                asset_type=candidate.asset_type, object_key=key,
                sha256=candidate.sha256, size=candidate.size,
                width=candidate.width, height=candidate.height, status="ready",
                meta={"downloader": source.kind, "remote": candidate.meta},
            )
            self._db.record_download(asset_id, source.kind, "done")
            return "new"

    def persist(self, source: Source, candidates: list[Candidate],
                on_progress=None) -> tuple[int, int, list[str]]:
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
