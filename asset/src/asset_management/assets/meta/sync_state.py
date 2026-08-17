"""Sync-run state tables beyond the core schema: raw_files + sync_stages.

``Database`` mixes this in so the two new tables of the two-phase sync stay
out of the (already large) core ``db.py`` module. The mixin methods only use
``self._conn`` (owned by ``Database``) and the shared ``utcnow`` helper.
"""

from __future__ import annotations

from datetime import UTC, datetime


def utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class SyncStateMixin:
    """CRUD for the raw layer (``raw_files``) and stage records
    (``sync_stages``), plus the Phase B task helpers."""

    # ------------------------------------------------------------- raw files
    RAW_FILE_FIELDS = (
        "sha256",
        "size",
        "status",
        "commit_hash",
        "attempts",
        "error",
    )

    def upsert_raw_file(
        self,
        source_id: str,
        path_in_repo: str,
        object_key: str,
        sha256: str = "",
        size: int = 0,
        status: str = "pending",
        commit_hash: str = "",
        error: str = "",
    ) -> None:
        """Insert or replace one raw-layer row (content-addressed identity of
        a downloaded repo file; the raw object key is path-addressed)."""
        now = utcnow()
        self._conn.execute(
            "INSERT OR REPLACE INTO raw_files "
            "(source_id, path_in_repo, object_key, sha256, size, status, "
            "commit_hash, error, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                source_id,
                path_in_repo,
                object_key,
                sha256,
                size,
                status,
                commit_hash,
                error,
                now,
                now,
            ),
        )

    def update_raw_file(self, source_id: str, path_in_repo: str, **fields) -> None:
        sets = []
        values: list = []
        for key, value in fields.items():
            if key not in self.RAW_FILE_FIELDS:
                raise ValueError(f"unknown raw_file field: {key}")
            sets.append(f"{key} = ?")
            values.append(value)
        if not sets:
            return
        sets.append("updated_at = ?")
        values.append(utcnow())
        values.append(source_id)
        values.append(path_in_repo)
        self._conn.execute(
            # keys allowlisted above; values parameterized
            f"UPDATE raw_files SET {', '.join(sets)} "  # nosec B608
            "WHERE source_id = ? AND path_in_repo = ?",
            values,
        )

    def get_raw_file(self, source_id: str, path_in_repo: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM raw_files WHERE source_id = ? AND path_in_repo = ?",
            (source_id, path_in_repo),
        ).fetchone()
        return dict(row) if row else None

    def list_raw_files(self, source_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM raw_files WHERE source_id = ? ORDER BY path_in_repo",
            (source_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------ sync stages
    def upsert_sync_stage(
        self,
        run_id: str,
        stage: str,
        started_at: str = "",
        finished_at: str = "",
        duration_s: float = 0.0,
        item_count: int = 0,
        failed_count: int = 0,
        retry_app: int = 0,
        retry_ray: int = 0,
    ) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO sync_stages "
            "(run_id, stage, started_at, finished_at, duration_s, item_count, "
            "failed_count, retry_app, retry_ray) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                stage,
                started_at,
                finished_at,
                duration_s,
                item_count,
                failed_count,
                retry_app,
                retry_ray,
            ),
        )

    def get_sync_stages(self, run_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM sync_stages WHERE run_id = ? ORDER BY finished_at, stage",
            (run_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------- Phase B task helpers
    def count_sync_tasks_by_status(self, run_id: str) -> dict[str, int]:
        rows = self._conn.execute(
            "SELECT status, COUNT(*) FROM sync_tasks WHERE run_id = ? GROUP BY status",
            (run_id,),
        ).fetchall()
        return {row[0]: row[1] for row in rows}

    def mark_processed_files_persisted(self, run_id: str) -> int:
        """Phase B finished: files that were processed (raw uploaded, no
        candidates or fully persisted) but never reached a terminal status
        become persisted — zero-candidate parquet files included."""
        cur = self._conn.execute(
            "UPDATE sync_tasks SET status = 'persisted', fraction = 1.0, "
            "updated_at = ? WHERE run_id = ? AND status = 'downloaded'",
            (utcnow(), run_id),
        )
        return cur.rowcount
