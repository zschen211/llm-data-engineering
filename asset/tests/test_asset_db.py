import sqlite3

import pytest

from asset_management.assets.meta.db import Database


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "assets.db")
    yield database
    database.close()


def test_source_crud(db):
    source = db.add_source(
        "coco", "http", url="https://cocodataset.org", params={"urls": []}
    )
    assert source.kind == "http"
    assert db.get_source(source.id).name == "coco"
    assert db.get_source_by_name("coco").id == source.id
    assert len(db.list_sources()) == 1
    db.update_source(source.id, description="updated")
    assert db.get_source(source.id).description == "updated"
    db.delete_source(source.id)
    assert db.get_source(source.id) is None


def test_source_name_unique(db):
    db.add_source("a", "local")
    with pytest.raises(sqlite3.IntegrityError):
        db.add_source("a", "local")


def test_asset_crud_and_versioning(db):
    source = db.add_source("s", "local")
    asset = db.add_asset(
        "ast_1",
        source.id,
        "photo.png",
        "general_image",
        "blobs/ab/abc.png",
        "abc123",
        100,
        120,
        80,
    )
    assert asset.current_version == 1
    assert db.get_asset_by_sha256("abc123").id == "ast_1"
    assert len(db.version_history("ast_1")) == 1

    db.bump_version("ast_1", "def456", "blobs/de/def456.png", "replaced")
    updated = db.get_asset("ast_1")
    assert updated.current_version == 2
    assert updated.sha256 == "def456"
    assert len(db.version_history("ast_1")) == 2

    rolled = db.rollback("ast_1", 1)
    assert rolled.sha256 == "abc123"
    assert rolled.current_version == 1


def test_asset_sha256_unique(db):
    source = db.add_source("s", "local")
    db.add_asset("ast_1", source.id, "a.png", "general_image", "k1", "same", 1, 1, 1)
    with pytest.raises(sqlite3.IntegrityError):
        db.add_asset(
            "ast_2", source.id, "b.png", "general_image", "k2", "same", 1, 1, 1
        )


def test_tags(db):
    source = db.add_source("s", "local")
    db.add_asset("ast_1", source.id, "a.png", "general_image", "k", "sha", 1, 1, 1)
    db.tag_asset("ast_1", "chart", group="task")
    db.tag_asset("ast_1", "high", group="quality")
    tags = db.asset_tags("ast_1")
    assert ("task", "chart") in tags
    assert ("quality", "high") in tags
    assert len(db.list_tags(group="task")) == 1
    db.untag_asset("ast_1", "chart")
    assert ("task", "chart") not in db.asset_tags("ast_1")
    db.tag_asset("ast_1", "chart", group="task")  # idempotent
    assert len(db.asset_tags("ast_1")) == 2


def test_list_assets_with_tag_filter(db):
    source = db.add_source("s", "local")
    db.add_asset("ast_1", source.id, "a.png", "general_image", "k1", "sha1", 1, 1, 1)
    db.add_asset("ast_2", source.id, "b.png", "chart_image", "k2", "sha2", 1, 1, 1)
    db.tag_asset("ast_2", "chart", group="task")
    assert len(db.list_assets(tags=["task=chart"])) == 1
    assert len(db.list_assets(asset_type="general_image")) == 1
    assert len(db.list_assets(status="ready")) == 2


def test_downloads(db):
    db.record_download("ast_1", "http", "failed", "boom")
    db.record_download("ast_1", "http", "failed", "boom again")
    rows = db.list_downloads()
    assert len(rows) == 1
    assert rows[0]["attempts"] == 2
    assert rows[0]["status"] == "failed"


def test_snapshots(db):
    source = db.add_source("s", "local")
    db.add_asset("ast_1", source.id, "a.png", "general_image", "k1", "sha1", 1, 1, 1)
    db.add_asset("ast_2", source.id, "b.png", "general_image", "k2", "sha2", 1, 1, 1)
    assets = db.list_assets(status="ready")
    snapshot = db.create_snapshot(assets, name="v1")
    assert snapshot.asset_count == 2
    assert snapshot.manifest_sha1
    assert db.get_snapshot("v1") is not None
    assert len(db.snapshot_assets("v1")) == 2
    assert len(db.list_snapshots()) == 1


def test_sync_events_with_fraction(db):
    run_id = "run_1"
    db.append_sync_event(run_id, "download", "a.png", fraction=0.42)
    events = db.get_sync_events(run_id)
    assert events[0]["fraction"] == 0.42
    assert (
        db.append_sync_event(run_id, "download", "b.png", fraction=None)
        > events[0]["id"]
    )


def test_sync_events_fraction_column_migrated(tmp_path):
    """A pre-fraction database (old schema) gets the column added on open."""
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE sync_events (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          run_id TEXT DEFAULT '',
          ts TEXT DEFAULT '',
          stage TEXT DEFAULT '',
          remote TEXT DEFAULT '',
          level TEXT DEFAULT 'info',
          message TEXT DEFAULT ''
        );
        """
    )
    conn.close()
    database = Database(path)
    try:
        database.append_sync_event("run_1", "download", "a.png", fraction=0.5)
        events = database.get_sync_events("run_1")
        assert events[0]["fraction"] == 0.5
    finally:
        database.close()
