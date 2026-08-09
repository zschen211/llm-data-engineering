"""SQLite metadata store for the asset layer.

Schema is written in a PostgreSQL-compatible style (TEXT PKs, explicit
timestamps) so it can be migrated to a shared server later.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .models import Asset, AssetVersion, Snapshot, Source, Tag

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
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:12]}"


def new_snapshot_id(manifest_sha1: str, assets: list[Asset]) -> str:
    return f"snap_{manifest_sha1[:10]}"


def _snapshot_hash(assets: list[Asset]) -> str:
    payload = "\n".join(sorted(f"{a.id}:{a.current_version}" for a in assets))
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


class Database:
    """Thin sqlite3 wrapper: schema init + typed CRUD for the asset layer."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ------------------------------------------------------------- sources
    def add_source(self, name: str, kind: str, url: str = "", license: str = "",
                   description: str = "", params: dict | None = None,
                   enabled: bool = True) -> Source:
        source = Source(
            id=new_id("src_"), name=name, kind=kind, url=url,
            license=license, description=description,
            params=params or {}, enabled=enabled,
            created_at=utcnow(), updated_at=utcnow(),
        )
        self._conn.execute(
            "INSERT INTO sources (id, name, kind, url, license, description, params, enabled, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (source.id, source.name, source.kind, source.url, source.license,
             source.description, json.dumps(source.params, ensure_ascii=False),
             int(source.enabled), source.created_at, source.updated_at),
        )
        self._conn.commit()
        return source

    def get_source(self, source_id: str) -> Source | None:
        row = self._conn.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
        return self._source_from_row(row) if row else None

    def get_source_by_name(self, name: str) -> Source | None:
        row = self._conn.execute("SELECT * FROM sources WHERE name = ?", (name,)).fetchone()
        return self._source_from_row(row) if row else None

    def list_sources(self) -> list[Source]:
        rows = self._conn.execute("SELECT * FROM sources ORDER BY created_at").fetchall()
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
        self._conn.execute(f"UPDATE sources SET {', '.join(sets)} WHERE id = ?", values)
        self._conn.commit()
        return self.get_source(source_id)

    def delete_source(self, source_id: str) -> None:
        self._conn.execute("DELETE FROM sources WHERE id = ?", (source_id,))
        self._conn.commit()

    @staticmethod
    def _source_from_row(row: sqlite3.Row) -> Source:
        return Source(
            id=row["id"], name=row["name"], kind=row["kind"], url=row["url"],
            license=row["license"], description=row["description"],
            params=json.loads(row["params"] or "{}"), enabled=bool(row["enabled"]),
            created_at=row["created_at"], updated_at=row["updated_at"],
        )

    # -------------------------------------------------------------- assets
    def add_asset(self, asset_id: str, source_id: str, name: str, asset_type: str,
                  object_key: str, sha256: str, size: int, width: int | None,
                  height: int | None, status: str = "ready", meta: dict | None = None) -> Asset:
        now = utcnow()
        self._conn.execute(
            "INSERT INTO assets (id, source_id, name, asset_type, object_key, sha256, size, width, height, status, current_version, meta, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)",
            (asset_id, source_id, name, asset_type, object_key, sha256, size,
             width, height, status, json.dumps(meta or {}, ensure_ascii=False), now, now),
        )
        self._conn.execute(
            "INSERT INTO asset_versions (asset_id, version, sha256, object_key, created_at) VALUES (?, 1, ?, ?, ?)",
            (asset_id, sha256, object_key, now),
        )
        self._conn.commit()
        return self.get_asset(asset_id)

    def get_asset(self, asset_id: str) -> Asset | None:
        row = self._conn.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
        return self._asset_from_row(row) if row else None

    def get_asset_by_sha256(self, sha256: str) -> Asset | None:
        row = self._conn.execute("SELECT * FROM assets WHERE sha256 = ?", (sha256,)).fetchone()
        return self._asset_from_row(row) if row else None

    def list_assets(self, asset_type: str | None = None, status: str | None = None,
                    source_id: str | None = None, tags: list[str] | None = None,
                    limit: int = 1000) -> list[Asset]:
        clauses = []
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
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._conn.execute(
            f"SELECT * FROM assets {where} ORDER BY created_at DESC LIMIT ?", values + [limit]
        ).fetchall()
        assets = [self._asset_from_row(r) for r in rows]
        if tags:
            assets = [a for a in assets if self._matches_tags(a.id, tags)]
        for asset in assets:
            asset.tags = self.asset_tags(asset.id)
        return assets

    def _matches_tags(self, asset_id: str, tags: list[str]) -> bool:
        for spec in tags:
            group, _, name = spec.partition("=")
            row = self._conn.execute(
                "SELECT 1 FROM asset_tags at JOIN tags t ON t.id = at.tag_id "
                "WHERE at.asset_id = ? AND t.name = ? AND t.tag_group = ?",
                (asset_id, name, group),
            ).fetchone()
            if row is None:
                return False
        return True

    def count_assets(self, source_id: str | None = None) -> int:
        if source_id:
            return self._conn.execute("SELECT COUNT(*) FROM assets WHERE source_id = ?", (source_id,)).fetchone()[0]
        return self._conn.execute("SELECT COUNT(*) FROM assets").fetchone()[0]

    def delete_asset(self, asset_id: str) -> None:
        for table in ("asset_versions", "asset_tags", "snapshot_assets", "downloads"):
            self._conn.execute(f"DELETE FROM {table} WHERE asset_id = ?", (asset_id,))
        self._conn.execute("DELETE FROM assets WHERE id = ?", (asset_id,))
        self._conn.commit()

    def bump_version(self, asset_id: str, sha256: str, object_key: str, change_note: str) -> None:
        version = self._conn.execute(
            "SELECT COALESCE(MAX(version), 0) + 1 FROM asset_versions WHERE asset_id = ?", (asset_id,)
        ).fetchone()[0]
        self._conn.execute(
            "INSERT INTO asset_versions (asset_id, version, sha256, object_key, change_note, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (asset_id, version, sha256, object_key, change_note, utcnow()),
        )
        self._conn.execute(
            "UPDATE assets SET sha256 = ?, object_key = ?, current_version = ?, updated_at = ? WHERE id = ?",
            (sha256, object_key, version, utcnow(), asset_id),
        )
        self._conn.commit()

    def version_history(self, asset_id: str) -> list[AssetVersion]:
        rows = self._conn.execute(
            "SELECT * FROM asset_versions WHERE asset_id = ? ORDER BY version DESC", (asset_id,)
        ).fetchall()
        return [
            AssetVersion(asset_id=r["asset_id"], version=r["version"], sha256=r["sha256"],
                         object_key=r["object_key"], change_note=r["change_note"],
                         created_at=r["created_at"])
            for r in rows
        ]

    def rollback(self, asset_id: str, version: int) -> Asset | None:
        row = self._conn.execute(
            "SELECT * FROM asset_versions WHERE asset_id = ? AND version = ?", (asset_id, version)
        ).fetchone()
        if row is None:
            return None
        self._conn.execute(
            "UPDATE assets SET sha256 = ?, object_key = ?, current_version = ?, updated_at = ? WHERE id = ?",
            (row["sha256"], row["object_key"], version, utcnow(), asset_id),
        )
        self._conn.commit()
        return self.get_asset(asset_id)

    @staticmethod
    def _asset_from_row(row: sqlite3.Row) -> Asset:
        return Asset(
            id=row["id"], source_id=row["source_id"], name=row["name"],
            asset_type=row["asset_type"], object_key=row["object_key"],
            sha256=row["sha256"], size=row["size"], width=row["width"],
            height=row["height"], status=row["status"],
            current_version=row["current_version"],
            meta=json.loads(row["meta"] or "{}"),
            created_at=row["created_at"], updated_at=row["updated_at"],
        )

    # ---------------------------------------------------------------- tags
    def get_or_create_tag(self, name: str, group: str = "default") -> Tag:
        row = self._conn.execute(
            "SELECT * FROM tags WHERE name = ?", (name,)
        ).fetchone()
        if row:
            tag = Tag(id=row["id"], name=row["name"], group=row["tag_group"])
            if tag.group != group:
                self._conn.execute("UPDATE tags SET tag_group = ? WHERE id = ?", (group, tag.id))
                self._conn.commit()
            return tag
        tag = Tag(id=new_id("tag_"), name=name, group=group)
        self._conn.execute(
            "INSERT INTO tags (id, name, tag_group) VALUES (?, ?, ?)",
            (tag.id, tag.name, tag.group),
        )
        self._conn.commit()
        return tag

    def tag_asset(self, asset_id: str, name: str, group: str = "default") -> None:
        tag = self.get_or_create_tag(name, group)
        self._conn.execute(
            "INSERT OR IGNORE INTO asset_tags (asset_id, tag_id) VALUES (?, ?)",
            (asset_id, tag.id),
        )
        self._conn.commit()

    def untag_asset(self, asset_id: str, name: str) -> None:
        row = self._conn.execute("SELECT id FROM tags WHERE name = ?", (name,)).fetchone()
        if row:
            self._conn.execute("DELETE FROM asset_tags WHERE asset_id = ? AND tag_id = ?", (asset_id, row["id"]))
            self._conn.commit()

    def list_tags(self, group: str | None = None) -> list[Tag]:
        if group:
            rows = self._conn.execute("SELECT * FROM tags WHERE tag_group = ? ORDER BY tag_group, name", (group,)).fetchall()
        else:
            rows = self._conn.execute("SELECT * FROM tags ORDER BY tag_group, name").fetchall()
        return [Tag(id=r["id"], name=r["name"], group=r["tag_group"]) for r in rows]

    def asset_tags(self, asset_id: str) -> list[tuple[str, str]]:
        rows = self._conn.execute(
            "SELECT t.tag_group, t.name FROM asset_tags at JOIN tags t ON t.id = at.tag_id "
            "WHERE at.asset_id = ? ORDER BY t.tag_group, t.name", (asset_id,)
        ).fetchall()
        return [(r["tag_group"], r["name"]) for r in rows]

    # ----------------------------------------------------------- downloads
    def record_download(self, asset_id: str, downloader: str, status: str, error: str = "") -> int:
        now = utcnow()
        latest = self._conn.execute(
            "SELECT * FROM downloads WHERE asset_id = ? ORDER BY id DESC LIMIT 1", (asset_id,)
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
        self._conn.commit()
        return download_id

    def list_downloads(self, limit: int = 100) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM downloads ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    # ----------------------------------------------------------- snapshots
    def create_snapshot(self, assets: list[Asset], name: str = "") -> Snapshot:
        manifest_sha1 = _snapshot_hash(assets)
        snapshot_id = name or new_snapshot_id(manifest_sha1, assets)
        snapshot = Snapshot(id=snapshot_id, manifest_sha1=manifest_sha1,
                            asset_count=len(assets), created_at=utcnow())
        self._conn.execute(
            "INSERT OR REPLACE INTO snapshots (id, manifest_sha1, asset_count, created_at) VALUES (?, ?, ?, ?)",
            (snapshot.id, snapshot.manifest_sha1, snapshot.asset_count, snapshot.created_at),
        )
        for asset in assets:
            self._conn.execute(
                "INSERT OR REPLACE INTO snapshot_assets (snapshot_id, asset_id, asset_version) VALUES (?, ?, ?)",
                (snapshot.id, asset.id, asset.current_version),
            )
        self._conn.commit()
        return snapshot

    def list_snapshots(self) -> list[Snapshot]:
        rows = self._conn.execute("SELECT * FROM snapshots ORDER BY created_at DESC").fetchall()
        return [Snapshot(id=r["id"], manifest_sha1=r["manifest_sha1"],
                         asset_count=r["asset_count"], created_at=r["created_at"]) for r in rows]

    def get_snapshot(self, snapshot_id: str) -> Snapshot | None:
        row = self._conn.execute("SELECT * FROM snapshots WHERE id = ?", (snapshot_id,)).fetchone()
        if row is None:
            return None
        return Snapshot(id=row["id"], manifest_sha1=row["manifest_sha1"],
                        asset_count=row["asset_count"], created_at=row["created_at"])

    def snapshot_assets(self, snapshot_id: str) -> list[Asset]:
        rows = self._conn.execute(
            "SELECT a.* FROM snapshot_assets sa JOIN assets a ON a.id = sa.asset_id "
            "WHERE sa.snapshot_id = ?", (snapshot_id,)
        ).fetchall()
        return [self._asset_from_row(r) for r in rows]
