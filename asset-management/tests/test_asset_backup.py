"""Backup tests: online backup API produces a consistent, restorable snapshot."""

from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from asset_management.assets.api import AssetStore, open_store
from asset_management.assets.meta.db import Database
from asset_management.assets.routes import create_app
from asset_management.assets.storage import LocalStorageBackend


def test_db_backup_to_restores_data(tmp_path):
    db = Database(tmp_path / "assets.db")
    source = db.add_source("s", "huggingface", params={"repo_id": "org/ds"})
    db.add_asset("ast_1", source.id, "a.png", "general_image", "k1", "sha1", 1, 1, 1)
    db.add_asset("ast_2", source.id, "b.png", "general_image", "k2", "sha2", 1, 1, 1)
    db.tag_asset("ast_1", "chart", group="task")

    backup = db.backup_to(tmp_path / "backups" / "b1.db")
    assert backup.exists()

    restored = Database(backup)
    assert restored.count_assets() == 2
    assert restored.asset_tags("ast_1") == [("task", "chart")]
    assert restored.get_source_by_name("s") is not None
    restored.close()
    db.close()


def test_store_backup_default_path_and_restore(tmp_path):
    src = tmp_path / "imgs"
    src.mkdir()
    for i in range(3):
        Image.new("RGB", (10, 10), (i * 80, 40, 200)).save(src / f"p{i}.png")
    with open_store(data_dir=tmp_path / "data") as store:
        store.import_dir(src, source_name="imp")
        store.create_snapshot(name="v1")
        path = store.backup_db()
        assert path.parent == tmp_path / "data" / "backups"
        assert path.name.startswith("assets_") and path.suffix == ".db"
        assert path.exists()

    # the backup is an independent, consistent copy
    with open_store(data_dir=tmp_path / "data") as store:
        assert store.count_assets() == 3
        assert len(store.list_snapshots()) == 1
    backup_path = tmp_path / "data" / "backups"
    backup = max(backup_path.glob("*.db"))
    db = Database(backup)
    assert db.count_assets() == 3
    assert len(db.list_snapshots()) == 1
    db.close()


def test_web_backup_endpoint(tmp_path):
    src = tmp_path / "imgs"
    src.mkdir()
    Image.new("RGB", (10, 10), "red").save(src / "a.png")
    store = AssetStore(
        tmp_path / "assets.db",
        LocalStorageBackend(tmp_path / "blobs"),
        tmp_dir=tmp_path / "tmp",
    )
    store.import_dir(src, source_name="web")
    client = TestClient(create_app(store))
    try:
        response = client.post("/api/backup")
        assert response.status_code == 201
        body = response.json()
        assert body["assets"] == 1
        assert Path(body["path"]).exists()
    finally:
        store.close()
