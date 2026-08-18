"""SQLite metadata store for the asset layer.

Schema is written in a PostgreSQL-compatible style (TEXT PKs, explicit
timestamps) so it can be migrated to a shared server later.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Self

from .models import Asset, AssetVersion, Snapshot, Source, Tag
from .sync_state import SyncStateMixin, utcnow

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  kind TEXT NOT NULL,
  url TEXT DEFAULT '',
  license TEXT DEFAULT '',
  description TEXT DEFAULT '',
  params TEXT DEFAULT '{}',
  enabled INTEGER DEFAULT 1,
  created_at TEXT DEFAULT '',
  updated_at TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS assets (
  id TEXT PRIMARY KEY,
  source_id TEXT REFERENCES sources(id),
  name TEXT NOT NULL,
  asset_type TEXT DEFAULT '',
  object_key TEXT DEFAULT '',
  sha256 TEXT UNIQUE,
  size INTEGER DEFAULT 0,
  width INTEGER,
  height INTEGER,
  status TEXT DEFAULT 'pending',
  current_version INTEGER DEFAULT 1,
  meta TEXT DEFAULT '{}',
  created_at TEXT DEFAULT '',
  updated_at TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_assets_source ON assets(source_id);
CREATE INDEX IF NOT EXISTS idx_assets_status ON assets(status);
CREATE INDEX IF NOT EXISTS idx_assets_created ON assets(created_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS asset_versions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  asset_id TEXT REFERENCES assets(id),
  version INTEGER,
  sha256 TEXT DEFAULT '',
  object_key TEXT DEFAULT '',
  change_note TEXT DEFAULT '',
  created_at TEXT DEFAULT '',
  UNIQUE (asset_id, version)
);

CREATE TABLE IF NOT EXISTS tags (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  tag_group TEXT DEFAULT 'default'
);

CREATE TABLE IF NOT EXISTS asset_tags (
  asset_id TEXT REFERENCES assets(id),
  tag_id TEXT REFERENCES tags(id),
  PRIMARY KEY (asset_id, tag_id)
);

CREATE TABLE IF NOT EXISTS downloads (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  asset_id TEXT,
  downloader TEXT DEFAULT '',
  status TEXT DEFAULT 'running',
  error TEXT DEFAULT '',
  attempts INTEGER DEFAULT 0,
  started_at TEXT DEFAULT '',
  finished_at TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS snapshots (
  id TEXT PRIMARY KEY,
  manifest_sha1 TEXT DEFAULT '',
  asset_count INTEGER DEFAULT 0,
  created_at TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS snapshot_assets (
  snapshot_id TEXT REFERENCES snapshots(id),
  asset_id TEXT REFERENCES assets(id),
  asset_version INTEGER,
  PRIMARY KEY (snapshot_id, asset_id)
);

CREATE TABLE IF NOT EXISTS sync_runs (
  id TEXT PRIMARY KEY,
  source_id TEXT DEFAULT '',
  status TEXT DEFAULT 'running',      -- running / paused / done / failed
  total_files INTEGER DEFAULT 0,
  done_files INTEGER DEFAULT 0,
  failed_files INTEGER DEFAULT 0,
  current_stage TEXT DEFAULT '',      -- resolve / download / process / persist
  current_file TEXT DEFAULT '',
  progress REAL DEFAULT 0.0,
  error TEXT DEFAULT '',
  created_at TEXT DEFAULT '',
  updated_at TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS sync_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT DEFAULT '',
  ts TEXT DEFAULT '',
  stage TEXT DEFAULT '',
  remote TEXT DEFAULT '',
  level TEXT DEFAULT 'info',
  message TEXT DEFAULT '',
  fraction REAL
);
CREATE INDEX IF NOT EXISTS idx_sync_events_run ON sync_events(run_id, id);

CREATE TABLE IF NOT EXISTS sync_tasks (
  id TEXT PRIMARY KEY,
  run_id TEXT DEFAULT '',
  remote_id TEXT DEFAULT '',         -- stable across runs (sha1 of repo path)
  name TEXT DEFAULT '',
  path_in_repo TEXT DEFAULT '',
  status TEXT DEFAULT 'pending',     -- pending / downloading / downloaded / persisted / skipped / failed
  bytes_downloaded INTEGER DEFAULT 0,
  total_bytes INTEGER DEFAULT 0,
  fraction REAL,
  attempts INTEGER DEFAULT 0,        -- download task starts (Phase A)
  process_attempts INTEGER DEFAULT 0, -- process task starts (Phase B)
  error TEXT DEFAULT '',
  created_at TEXT DEFAULT '',
  updated_at TEXT DEFAULT '',
  UNIQUE (run_id, remote_id)
);
CREATE INDEX IF NOT EXISTS idx_sync_tasks_run ON sync_tasks(run_id);

CREATE TABLE IF NOT EXISTS raw_files (
  source_id TEXT NOT NULL,
  path_in_repo TEXT NOT NULL,
  object_key TEXT NOT NULL,           -- raw/<source_id>/<path_in_repo>
  sha256 TEXT DEFAULT '',
  size INTEGER DEFAULT 0,
  status TEXT DEFAULT 'pending',      -- pending / uploaded / failed
  commit_hash TEXT DEFAULT '',
  attempts INTEGER DEFAULT 0,
  error TEXT DEFAULT '',
  created_at TEXT DEFAULT '',
  updated_at TEXT DEFAULT '',
  PRIMARY KEY (source_id, path_in_repo)
);
CREATE INDEX IF NOT EXISTS idx_raw_files_source ON raw_files(source_id, status);

CREATE TABLE IF NOT EXISTS sync_stages (
  run_id TEXT NOT NULL,
  stage TEXT NOT NULL,                -- resolve / download_raw / process / persist
  started_at TEXT DEFAULT '',
  finished_at TEXT DEFAULT '',
  duration_s REAL DEFAULT 0.0,
  item_count INTEGER DEFAULT 0,
  failed_count INTEGER DEFAULT 0,
  retry_app INTEGER DEFAULT 0,
  retry_ray INTEGER DEFAULT 0,
  PRIMARY KEY (run_id, stage)
);
"""


def new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:12]}"


def new_snapshot_id(manifest_sha1: str, assets: list[Asset]) -> str:
    return f"snap_{manifest_sha1[:10]}"


def _snapshot_hash(assets: list[Asset]) -> str:
    payload = "\n".join(sorted(f"{a.id}:{a.current_version}" for a in assets))
    return hashlib.sha1(payload.encode("utf-8"), usedforsecurity=False).hexdigest()


class Database(SyncStateMixin):
    """Thin sqlite3 wrapper: schema init + typed CRUD for the asset layer."""

    def __init__(self, path: Path, mark_stale: bool = True):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._thread_local = threading.local()
        self._connections: list[sqlite3.Connection] = []
        self._connections_lock = threading.Lock()
        self._executescript(SCHEMA)
        self._migrate()
        if mark_stale:
            self._mark_stale_runs_interrupted()

    def _new_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.isolation_level = (
            None  # autocommit; transactions are explicit (persist dedup)
        )
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        with self._connections_lock:
            self._connections.append(conn)
        return conn

    @property
    def _conn(self) -> sqlite3.Connection:
        """Per-thread connection (WAL mode): concurrent workers each get their
        own connection instead of sharing one across threads (which is not
        safe at the C level and can crash sqlite3)."""
        conn = getattr(self._thread_local, "conn", None)
        if conn is None:
            conn = self._new_connection()
            self._thread_local.conn = conn
        return conn

    def _executescript(self, script: str) -> None:
        self._conn.executescript(script)

    def _migrate(self) -> None:
        """Add columns introduced after the initial schema release.

        ``CREATE TABLE IF NOT EXISTS`` never alters existing tables, so new
        columns are added via ALTER here. Idempotent per column; safe to run
        from multiple processes (Ray workers) concurrently.
        """
        cols = {row[1] for row in self._conn.execute("PRAGMA table_info(sync_events)")}
        if "fraction" not in cols:
            try:
                self._conn.execute("ALTER TABLE sync_events ADD COLUMN fraction REAL")
            except sqlite3.OperationalError:
                cols = {
                    row[1]
                    for row in self._conn.execute("PRAGMA table_info(sync_events)")
                }
                if "fraction" not in cols:
                    raise
        task_cols = {
            row[1] for row in self._conn.execute("PRAGMA table_info(sync_tasks)")
        }
        if "process_attempts" not in task_cols:
            self._conn.execute(
                "ALTER TABLE sync_tasks ADD COLUMN process_attempts INTEGER DEFAULT 0"
            )

    @contextmanager
    def transaction(self):
        """Serialized write transaction (BEGIN IMMEDIATE .. COMMIT).

        Connections run in autocommit, so multi-statement writes must be
        grouped explicitly. BEGIN IMMEDIATE also serializes writers across
        processes (Ray workers / web threads), which is the dedup primitive
        of the persist stage.
        """
        conn = self._conn
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")

    def close(self) -> None:
        with self._connections_lock:
            conns, self._connections = self._connections, []
        for conn in conns:
            conn.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ------------------------------------------------------------- sources
    def add_source(
        self,
        name: str,
        kind: str,
        url: str = "",
        license: str = "",
        description: str = "",
        params: dict | None = None,
        enabled: bool = True,
    ) -> Source:
        source = Source(
            id=new_id("src_"),
            name=name,
            kind=kind,
            url=url,
            license=license,
            description=description,
            params=params or {},
            enabled=enabled,
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        self._conn.execute(
            "INSERT INTO sources (id, name, kind, url, license, description, params, enabled, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                source.id,
                source.name,
                source.kind,
                source.url,
                source.license,
                source.description,
                json.dumps(source.params, ensure_ascii=False),
                int(source.enabled),
                source.created_at,
                source.updated_at,
            ),
        )
        return source

    def get_source(self, source_id: str) -> Source | None:
        row = self._conn.execute(
            "SELECT * FROM sources WHERE id = ?", (source_id,)
        ).fetchone()
        return self._source_from_row(row) if row else None

    def get_source_by_name(self, name: str) -> Source | None:
        row = self._conn.execute(
            "SELECT * FROM sources WHERE name = ?", (name,)
        ).fetchone()
        return self._source_from_row(row) if row else None

    def list_sources(self) -> list[Source]:
        rows = self._conn.execute(
            "SELECT * FROM sources ORDER BY created_at"
        ).fetchall()
        return [self._source_from_row(r) for r in rows]

    def update_source(self, source_id: str, **fields) -> Source | None:
        allowed = {"name", "kind", "url", "license", "description", "params", "enabled"}
        sets = []
        values = []
        for key, value in fields.items():
            if key not in allowed:
                raise ValueError(f"unknown source field: {key}")
            if key == "params":
                value = json.dumps(value, ensure_ascii=False)
            elif key == "enabled":
                value = int(value)
            sets.append(f"{key} = ?")
            values.append(value)
        if not sets:
            return self.get_source(source_id)
        sets.append("updated_at = ?")
        values.append(utcnow())
        values.append(source_id)
        # keys allowlisted above; values parameterized
        self._conn.execute(f"UPDATE sources SET {', '.join(sets)} WHERE id = ?", values)  # nosec B608
        return self.get_source(source_id)

    def delete_source(self, source_id: str) -> None:
        self._conn.execute("DELETE FROM sources WHERE id = ?", (source_id,))

    @staticmethod
    def _source_from_row(row: sqlite3.Row) -> Source:
        return Source(
            id=row["id"],
            name=row["name"],
            kind=row["kind"],
            url=row["url"],
            license=row["license"],
            description=row["description"],
            params=json.loads(row["params"] or "{}"),
            enabled=bool(row["enabled"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    # -------------------------------------------------------------- assets
    def add_asset(
        self,
        asset_id: str,
        source_id: str,
        name: str,
        asset_type: str,
        object_key: str,
        sha256: str,
        size: int,
        width: int | None,
        height: int | None,
        status: str = "ready",
        meta: dict | None = None,
    ) -> Asset:
        now = utcnow()
        self._conn.execute(
            "INSERT INTO assets (id, source_id, name, asset_type, object_key, sha256, size, width, height, status, current_version, meta, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)",
            (
                asset_id,
                source_id,
                name,
                asset_type,
                object_key,
                sha256,
                size,
                width,
                height,
                status,
                json.dumps(meta or {}, ensure_ascii=False),
                now,
                now,
            ),
        )
        self._conn.execute(
            "INSERT INTO asset_versions (asset_id, version, sha256, object_key, created_at) VALUES (?, 1, ?, ?, ?)",
            (asset_id, sha256, object_key, now),
        )
        return self.get_asset(asset_id)

    def get_asset(self, asset_id: str) -> Asset | None:
        row = self._conn.execute(
            "SELECT * FROM assets WHERE id = ?", (asset_id,)
        ).fetchone()
        return self._asset_from_row(row) if row else None

    def get_asset_by_sha256(self, sha256: str) -> Asset | None:
        row = self._conn.execute(
            "SELECT * FROM assets WHERE sha256 = ?", (sha256,)
        ).fetchone()
        return self._asset_from_row(row) if row else None

    def list_assets(
        self,
        asset_type: str | None = None,
        status: str | None = None,
        source_id: str | None = None,
        tags: list[str] | None = None,
        q: str | None = None,
    ) -> list[Asset]:
        """All matching assets (no limit); used by materialize/snapshots.

        Tag matching and keyword search are pushed down into SQL so results
        stay correct regardless of size.
        """
        clauses, values = self._asset_filters(asset_type, status, source_id, tags, q)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._conn.execute(
            # clauses from _asset_filters are code constants + placeholders
            f"SELECT * FROM assets {where} ORDER BY created_at DESC, id DESC",  # nosec B608
            values,
        ).fetchall()
        return self._assets_with_tags(rows)

    def list_assets_page(
        self,
        asset_type: str | None = None,
        status: str | None = None,
        source_id: str | None = None,
        tags: list[str] | None = None,
        q: str | None = None,
        cursor: tuple[str, str] | None = None,
        limit: int = 50,
    ) -> tuple[list[Asset], tuple[str, str] | None]:
        """Keyset (cursor) pagination over assets ordered by (created_at, id) DESC.

        ``cursor`` is the (created_at, id) of the last row of the previous page.
        Returns (items, next_cursor); next_cursor is None when there is no more.
        """
        clauses, values = self._asset_filters(asset_type, status, source_id, tags, q)
        if cursor is not None:
            clauses.append("(created_at, id) < (?, ?)")
            values += [cursor[0], cursor[1]]
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._conn.execute(
            # clauses from _asset_filters are code constants + placeholders
            f"SELECT * FROM assets {where} ORDER BY created_at DESC, id DESC LIMIT ?",  # nosec B608
            values + [limit + 1],
        ).fetchall()
        has_more = len(rows) > limit
        items = self._assets_with_tags(rows[:limit])
        next_cursor = None
        if has_more and items:
            last = items[-1]
            next_cursor = (last.created_at, last.id)
        return items, next_cursor

    def count_assets(
        self,
        asset_type: str | None = None,
        status: str | None = None,
        source_id: str | None = None,
        tags: list[str] | None = None,
        q: str | None = None,
    ) -> int:
        clauses, values = self._asset_filters(asset_type, status, source_id, tags, q)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return self._conn.execute(
            # clauses from _asset_filters are code constants + placeholders
            f"SELECT COUNT(*) FROM assets {where}",  # nosec B608
            values,
        ).fetchone()[0]

    def dataset_stats(self) -> dict[str, dict]:
        """Per-source dataset aggregation for the management console.

        Each asset dataset is the set of assets synced from one source.
        Returns a dict keyed by source id with status counts, ready bytes,
        distinct tags and the latest sync run of that source.
        """
        stats: dict[str, dict] = {}
        for row in self._conn.execute(
            "SELECT source_id, status, COUNT(*) AS n FROM assets GROUP BY source_id, status"
        ):
            bucket = stats.setdefault(row["source_id"], {})
            counts = bucket.setdefault("status_counts", {})
            counts[row["status"]] = row["n"]
        for row in self._conn.execute(
            "SELECT source_id, SUM(size) AS total FROM assets "
            "WHERE status = 'ready' GROUP BY source_id"
        ):
            stats.setdefault(row["source_id"], {})["ready_bytes"] = row["total"] or 0
        for row in self._conn.execute(
            "SELECT DISTINCT a.source_id, t.tag_group, t.name FROM asset_tags at "
            "JOIN tags t ON t.id = at.tag_id JOIN assets a ON a.id = at.asset_id "
            "ORDER BY t.tag_group, t.name"
        ):
            bucket = stats.setdefault(row["source_id"], {})
            bucket.setdefault("tags", []).append(f"{row['tag_group']}={row['name']}")
        for row in self._conn.execute(
            "SELECT id, source_id, status, current_stage, progress, "
            "created_at, updated_at FROM sync_runs "
            "ORDER BY created_at DESC, id DESC"
        ):
            bucket = stats.setdefault(row["source_id"], {})
            if "latest_run" not in bucket:
                bucket["latest_run"] = dict(row)
        return stats

    @staticmethod
    def _asset_filters(
        asset_type: str | None,
        status: str | None,
        source_id: str | None,
        tags: list[str] | None,
        q: str | None,
    ) -> tuple[list[str], list]:
        """Build WHERE clauses; tag specs are "group=name" and match via JOIN."""
        clauses: list[str] = []
        values: list = []
        if asset_type:
            clauses.append("asset_type = ?")
            values.append(asset_type)
        if status:
            clauses.append("status = ?")
            values.append(status)
        if source_id:
            clauses.append("source_id = ?")
            values.append(source_id)
        for spec in tags or []:
            group, _, name = spec.partition("=")
            clauses.append(
                "id IN (SELECT at.asset_id FROM asset_tags at JOIN tags t ON t.id = at.tag_id "
                "WHERE t.tag_group = ? AND t.name = ?)"
            )
            values += [group, name]
        if q:
            pattern = (
                "%"
                + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                + "%"
            )
            clauses.append("(name LIKE ? ESCAPE '\\' OR id LIKE ? ESCAPE '\\')")
            values += [pattern, pattern]
        return clauses, values

    def _assets_with_tags(self, rows) -> list[Asset]:
        assets = [self._asset_from_row(r) for r in rows]
        for asset in assets:
            asset.tags = self.asset_tags(asset.id)
        return assets

    def delete_asset(self, asset_id: str) -> None:
        with self.transaction():
            for table in (
                "asset_versions",
                "asset_tags",
                "snapshot_assets",
                "downloads",
            ):
                self._conn.execute(
                    # table names are literal constants in this loop
                    f"DELETE FROM {table} WHERE asset_id = ?",  # nosec B608
                    (asset_id,),
                )
            self._conn.execute("DELETE FROM assets WHERE id = ?", (asset_id,))

    def bump_version(
        self, asset_id: str, sha256: str, object_key: str, change_note: str
    ) -> None:
        with self.transaction():
            version = self._conn.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 FROM asset_versions WHERE asset_id = ?",
                (asset_id,),
            ).fetchone()[0]
            self._conn.execute(
                "INSERT INTO asset_versions (asset_id, version, sha256, object_key, change_note, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (asset_id, version, sha256, object_key, change_note, utcnow()),
            )
            self._conn.execute(
                "UPDATE assets SET sha256 = ?, object_key = ?, current_version = ?, updated_at = ? WHERE id = ?",
                (sha256, object_key, version, utcnow(), asset_id),
            )

    def version_history(self, asset_id: str) -> list[AssetVersion]:
        rows = self._conn.execute(
            "SELECT * FROM asset_versions WHERE asset_id = ? ORDER BY version DESC",
            (asset_id,),
        ).fetchall()
        return [
            AssetVersion(
                asset_id=r["asset_id"],
                version=r["version"],
                sha256=r["sha256"],
                object_key=r["object_key"],
                change_note=r["change_note"],
                created_at=r["created_at"],
            )
            for r in rows
        ]

    def rollback(self, asset_id: str, version: int) -> Asset | None:
        row = self._conn.execute(
            "SELECT * FROM asset_versions WHERE asset_id = ? AND version = ?",
            (asset_id, version),
        ).fetchone()
        if row is None:
            return None
        self._conn.execute(
            "UPDATE assets SET sha256 = ?, object_key = ?, current_version = ?, updated_at = ? WHERE id = ?",
            (row["sha256"], row["object_key"], version, utcnow(), asset_id),
        )
        return self.get_asset(asset_id)

    @staticmethod
    def _asset_from_row(row: sqlite3.Row) -> Asset:
        return Asset(
            id=row["id"],
            source_id=row["source_id"],
            name=row["name"],
            asset_type=row["asset_type"],
            object_key=row["object_key"],
            sha256=row["sha256"],
            size=row["size"],
            width=row["width"],
            height=row["height"],
            status=row["status"],
            current_version=row["current_version"],
            meta=json.loads(row["meta"] or "{}"),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    # ---------------------------------------------------------------- tags
    def get_or_create_tag(self, name: str, group: str = "default") -> Tag:
        row = self._conn.execute(
            "SELECT * FROM tags WHERE name = ?", (name,)
        ).fetchone()
        if row:
            tag = Tag(id=row["id"], name=row["name"], group=row["tag_group"])
            if tag.group != group:
                self._conn.execute(
                    "UPDATE tags SET tag_group = ? WHERE id = ?", (group, tag.id)
                )
            return tag
        tag = Tag(id=new_id("tag_"), name=name, group=group)
        self._conn.execute(
            "INSERT INTO tags (id, name, tag_group) VALUES (?, ?, ?)",
            (tag.id, tag.name, tag.group),
        )
        return tag

    def tag_asset(self, asset_id: str, name: str, group: str = "default") -> None:
        tag = self.get_or_create_tag(name, group)
        self._conn.execute(
            "INSERT OR IGNORE INTO asset_tags (asset_id, tag_id) VALUES (?, ?)",
            (asset_id, tag.id),
        )

    def untag_asset(self, asset_id: str, name: str) -> None:
        row = self._conn.execute(
            "SELECT id FROM tags WHERE name = ?", (name,)
        ).fetchone()
        if row:
            self._conn.execute(
                "DELETE FROM asset_tags WHERE asset_id = ? AND tag_id = ?",
                (asset_id, row["id"]),
            )

    def list_tags(self, group: str | None = None) -> list[Tag]:
        if group:
            rows = self._conn.execute(
                "SELECT * FROM tags WHERE tag_group = ? ORDER BY tag_group, name",
                (group,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM tags ORDER BY tag_group, name"
            ).fetchall()
        return [Tag(id=r["id"], name=r["name"], group=r["tag_group"]) for r in rows]

    def asset_tags(self, asset_id: str) -> list[tuple[str, str]]:
        rows = self._conn.execute(
            "SELECT t.tag_group, t.name FROM asset_tags at JOIN tags t ON t.id = at.tag_id "
            "WHERE at.asset_id = ? ORDER BY t.tag_group, t.name",
            (asset_id,),
        ).fetchall()
        return [(r["tag_group"], r["name"]) for r in rows]

    # ----------------------------------------------------------- downloads
    def record_download(
        self, asset_id: str, downloader: str, status: str, error: str = ""
    ) -> int:
        now = utcnow()
        latest = self._conn.execute(
            "SELECT * FROM downloads WHERE asset_id = ? ORDER BY id DESC LIMIT 1",
            (asset_id,),
        ).fetchone()
        if latest and (latest["status"] == "running" or latest["status"] == status):
            attempts = latest["attempts"] + 1
            finished = now if status != "running" else ""
            self._conn.execute(
                "UPDATE downloads SET downloader = ?, status = ?, error = ?, attempts = ?, finished_at = ? WHERE id = ?",
                (downloader, status, error, attempts, finished, latest["id"]),
            )
            download_id = latest["id"]
        else:
            cur = self._conn.execute(
                "INSERT INTO downloads (asset_id, downloader, status, error, attempts, started_at) VALUES (?, ?, ?, ?, 1, ?)",
                (asset_id, downloader, status, error, now),
            )
            download_id = cur.lastrowid
        return download_id

    def list_downloads(self, limit: int = 100) -> list[dict]:
        """Download ledger rows joined with the asset for the console
        (asset_name / sha256 / size); deleted assets keep their record
        (LEFT JOIN) with NULL asset columns."""
        rows = self._conn.execute(
            "SELECT d.id, d.asset_id, a.name AS asset_name, a.sha256,"
            " a.size AS bytes_downloaded, d.downloader, d.status, d.error,"
            " d.attempts, d.started_at, d.finished_at"
            " FROM downloads d LEFT JOIN assets a ON a.id = d.asset_id"
            " ORDER BY d.id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ----------------------------------------------------------- snapshots
    def create_snapshot(self, assets: list[Asset], name: str = "") -> Snapshot:
        manifest_sha1 = _snapshot_hash(assets)
        snapshot_id = name or new_snapshot_id(manifest_sha1, assets)
        snapshot = Snapshot(
            id=snapshot_id,
            manifest_sha1=manifest_sha1,
            asset_count=len(assets),
            created_at=utcnow(),
        )
        with self.transaction():
            self._conn.execute(
                "INSERT OR REPLACE INTO snapshots (id, manifest_sha1, asset_count, created_at) VALUES (?, ?, ?, ?)",
                (
                    snapshot.id,
                    snapshot.manifest_sha1,
                    snapshot.asset_count,
                    snapshot.created_at,
                ),
            )
            for asset in assets:
                self._conn.execute(
                    "INSERT OR REPLACE INTO snapshot_assets (snapshot_id, asset_id, asset_version) VALUES (?, ?, ?)",
                    (snapshot.id, asset.id, asset.current_version),
                )
        return snapshot

    def list_snapshots(self) -> list[Snapshot]:
        rows = self._conn.execute(
            "SELECT * FROM snapshots ORDER BY created_at DESC"
        ).fetchall()
        return [
            Snapshot(
                id=r["id"],
                manifest_sha1=r["manifest_sha1"],
                asset_count=r["asset_count"],
                created_at=r["created_at"],
            )
            for r in rows
        ]

    def get_snapshot(self, snapshot_id: str) -> Snapshot | None:
        row = self._conn.execute(
            "SELECT * FROM snapshots WHERE id = ?", (snapshot_id,)
        ).fetchone()
        if row is None:
            return None
        return Snapshot(
            id=row["id"],
            manifest_sha1=row["manifest_sha1"],
            asset_count=row["asset_count"],
            created_at=row["created_at"],
        )

    def snapshot_assets(self, snapshot_id: str) -> list[Asset]:
        rows = self._conn.execute(
            "SELECT a.* FROM snapshot_assets sa JOIN assets a ON a.id = sa.asset_id "
            "WHERE sa.snapshot_id = ?",
            (snapshot_id,),
        ).fetchall()
        return [self._asset_from_row(r) for r in rows]

    # ------------------------------------------------------------ sync runs
    RUN_FIELDS = (
        "status",
        "total_files",
        "done_files",
        "failed_files",
        "current_stage",
        "current_file",
        "progress",
        "error",
    )

    def create_sync_run(self, source_id: str) -> str:
        run_id = new_id("run_")
        now = utcnow()
        self._conn.execute(
            "INSERT INTO sync_runs (id, source_id, status, created_at, updated_at) "
            "VALUES (?, ?, 'running', ?, ?)",
            (run_id, source_id, now, now),
        )
        return run_id

    def update_sync_run(self, run_id: str, **fields) -> None:
        sets = []
        values: list = []
        for key, value in fields.items():
            if key not in self.RUN_FIELDS:
                raise ValueError(f"unknown sync_run field: {key}")
            sets.append(f"{key} = ?")
            values.append(value)
        if not sets:
            return
        sets.append("updated_at = ?")
        values.append(utcnow())
        values.append(run_id)
        self._conn.execute(
            # keys allowlisted above; values parameterized
            f"UPDATE sync_runs SET {', '.join(sets)} WHERE id = ?",  # nosec B608
            values,
        )

    def get_sync_run(self, run_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM sync_runs WHERE id = ?", (run_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_running_run(self, source_id: str) -> dict | None:
        """The most recent still-active sync run of a source (running or
        paused; a paused run keeps its entry point visible until resumed)."""
        row = self._conn.execute(
            "SELECT * FROM sync_runs WHERE source_id = ? AND status IN ('running', 'paused') "
            "ORDER BY created_at DESC LIMIT 1",
            (source_id,),
        ).fetchone()
        return dict(row) if row else None

    def get_interrupted_run(self, source_id: str) -> dict | None:
        """The most recent interrupted (crash-stale) run of a source, if any.

        An interrupted run keeps its per-file task table, so resuming it
        continues exactly where the crash happened (file granularity)."""
        row = self._conn.execute(
            "SELECT * FROM sync_runs WHERE source_id = ? AND status = 'interrupted' "
            "ORDER BY created_at DESC LIMIT 1",
            (source_id,),
        ).fetchone()
        return dict(row) if row else None

    def list_sync_runs(self, limit: int = 20) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM sync_runs ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def append_sync_event(
        self,
        run_id: str,
        stage: str,
        remote: str,
        level: str = "info",
        message: str = "",
        fraction: float | None = None,
    ) -> int:
        """Persist one sync event; ``fraction`` is a 0.0-1.0 per-file progress
        share (e.g. download byte progress reported by tqdm callbacks)."""
        cur = self._conn.execute(
            "INSERT INTO sync_events (run_id, ts, stage, remote, level, message, fraction) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (run_id, utcnow(), stage, remote, level, message, fraction),
        )
        return cur.lastrowid

    def get_sync_events(
        self, run_id: str, after_id: int = 0, limit: int = 200
    ) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM sync_events WHERE run_id = ? AND id > ? ORDER BY id LIMIT ?",
            (run_id, after_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------ sync tasks
    TASK_FIELDS = (
        "status",
        "bytes_downloaded",
        "total_bytes",
        "fraction",
        "attempts",
        "process_attempts",
        "error",
    )

    def create_sync_tasks(self, run_id: str, remotes) -> int:
        """Insert one pending task per remote (INSERT OR IGNORE on
        (run_id, remote_id), so resuming a run never duplicates tasks).
        Returns the number of rows actually inserted."""
        now = utcnow()
        inserted = 0
        for remote in remotes:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO sync_tasks "
                "(id, run_id, remote_id, name, path_in_repo, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)",
                (
                    new_id("tsk_"),
                    run_id,
                    remote.id,
                    remote.name,
                    remote.path_in_repo,
                    now,
                    now,
                ),
            )
            inserted += cur.rowcount
        return inserted

    def reconcile_sync_tasks(self, run_id: str, remote_ids: set[str]) -> int:
        """Fail tasks whose file disappeared from the repo while the run was
        interrupted (resume against a changed file list)."""
        if remote_ids:
            placeholders = ",".join("?" * len(remote_ids))
            sql = (
                "UPDATE sync_tasks SET status = 'failed', error = 'file removed from repo', "
                "updated_at = ? WHERE run_id = ? AND status IN ('pending', 'downloading') "
                # placeholders are generated from the set size, values parameterized
                f"AND remote_id NOT IN ({placeholders})"  # nosec B608
            )
            params: tuple = (utcnow(), run_id, *sorted(remote_ids))
        else:
            sql = (
                "UPDATE sync_tasks SET status = 'failed', error = 'file removed from repo', "
                "updated_at = ? WHERE run_id = ? AND status IN ('pending', 'downloading')"
            )
            params = (utcnow(), run_id)
        cur = self._conn.execute(sql, params)
        return cur.rowcount

    def get_sync_tasks(self, run_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM sync_tasks WHERE run_id = ? ORDER BY path_in_repo, id",
            (run_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_sync_tasks_page(
        self, run_id: str, offset: int = 0, limit: int = 20
    ) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM sync_tasks WHERE run_id = ? "
            "ORDER BY path_in_repo, id LIMIT ? OFFSET ?",
            (run_id, limit, offset),
        ).fetchall()
        return [dict(r) for r in rows]

    def count_sync_tasks(self, run_id: str) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) FROM sync_tasks WHERE run_id = ?", (run_id,)
        ).fetchone()
        return row[0]

    def get_sync_task(self, run_id: str, remote_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM sync_tasks WHERE run_id = ? AND remote_id = ?",
            (run_id, remote_id),
        ).fetchone()
        return dict(row) if row else None

    def update_sync_task(self, run_id: str, remote_id: str, **fields) -> None:
        sets = []
        values: list = []
        for key, value in fields.items():
            if key not in self.TASK_FIELDS:
                raise ValueError(f"unknown sync_task field: {key}")
            sets.append(f"{key} = ?")
            values.append(value)
        if not sets:
            return
        sets.append("updated_at = ?")
        values.append(utcnow())
        values.append(run_id)
        values.append(remote_id)
        self._conn.execute(
            # keys allowlisted above; values parameterized
            f"UPDATE sync_tasks SET {', '.join(sets)} "  # nosec B608
            "WHERE run_id = ? AND remote_id = ?",
            values,
        )

    def _mark_stale_runs_interrupted(self) -> None:
        """Runs left 'running'/'paused' by a previous process become
        'interrupted' on open (no worker thread survives a restart to resume
        them). Their per-file tasks are reset to 'pending' so a later resume
        resubmits only the unfinished files; the recorded bytes/fraction are
        kept so the UI can show the last-known progress before retrying."""
        self._conn.execute(
            "UPDATE sync_runs SET status = 'interrupted', "
            "error = error || ' | interrupted by restart', updated_at = ? "
            "WHERE status IN ('running', 'paused')",
            (utcnow(),),
        )
        self._conn.execute(
            "UPDATE sync_tasks SET status = 'pending', updated_at = ? "
            "WHERE status = 'downloading'",
            (utcnow(),),
        )

    def backup_to(self, path: Path) -> Path:
        """Create a consistent backup of the database via the online backup API.

        Safe to run while the store is being written (reads + writes in
        flight); the result is an independent, consistent snapshot.
        """
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        target = sqlite3.connect(str(path))
        try:
            self._conn.backup(target)
        finally:
            target.close()
        return path
