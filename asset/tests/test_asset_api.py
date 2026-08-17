"""Unified public API contract: other modules access the asset layer only
through asset_management.assets.api (open_store + AssetStore)."""

import pytest
from PIL import Image

from asset_management.assets.api import AssetStore, SyncReport, open_store
from asset_management.assets.storage import LocalStorageBackend


def _images(root):
    root.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (10, 10), "red").save(root / "photo_a.png")
    Image.new("RGB", (10, 10), "gray").save(root / "doc_page1.png")


def test_open_store_local_backend_default(tmp_path, monkeypatch):
    monkeypatch.delenv("RUSTFS_ENDPOINT", raising=False)
    store = open_store(data_dir=tmp_path / "data")
    assert isinstance(store, AssetStore)
    assert (tmp_path / "data" / "assets.db").exists()
    store.close()


def test_open_store_requires_credentials_with_rustfs_env(tmp_path, monkeypatch):
    monkeypatch.setenv("RUSTFS_ENDPOINT", "http://localhost:9000")
    monkeypatch.delenv("RUSTFS_ACCESS_KEY", raising=False)
    monkeypatch.delenv("RUSTFS_SECRET_KEY", raising=False)
    with pytest.raises(ValueError, match="RUSTFS_ACCESS_KEY"):
        open_store(data_dir=tmp_path / "data")


def test_open_store_rustfs_switch_requires_endpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("ASSET_STORAGE_BACKEND", "rustfs")
    monkeypatch.delenv("RUSTFS_ENDPOINT", raising=False)
    with pytest.raises(ValueError, match="RUSTFS_ENDPOINT"):
        open_store(data_dir=tmp_path / "data")


def test_open_store_unknown_switch_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("ASSET_STORAGE_BACKEND", "nope")
    with pytest.raises(ValueError, match="ASSET_STORAGE_BACKEND"):
        open_store(data_dir=tmp_path / "data")


def test_open_store_local_switch_forces_local(tmp_path, monkeypatch):
    """ASSET_STORAGE_BACKEND=local ignores a RUSTFS_ENDPOINT (no silent
    drift to the object store)."""
    monkeypatch.setenv("ASSET_STORAGE_BACKEND", "local")
    monkeypatch.setenv("RUSTFS_ENDPOINT", "http://localhost:9000")
    store = open_store(data_dir=tmp_path / "data")
    assert store.backend_name == "local"
    store.close()


def test_open_store_with_explicit_backend(tmp_path):
    backend = LocalStorageBackend(tmp_path / "blobs")
    store = open_store(data_dir=tmp_path / "data", backend=backend)
    assert store.backend is backend
    store.close()


def test_public_api_flow(tmp_path):
    """The documented usage pattern for downstream modules."""
    src = tmp_path / "imgs"
    _images(src)
    with open_store(data_dir=tmp_path / "data") as store:
        report = store.import_dir(src, source_name="api-test")
        assert isinstance(report, SyncReport)
        assert report.new == 2

        assets = store.list_assets(status="ready")
        assert len(assets) == 2
        doc = next(a for a in assets if a.asset_type == "document_image")
        store.tag_asset(doc.id, "doc", group="task")
        assert [a.id for a in store.list_assets(tags=["task=doc"])] == [doc.id]

        snapshot = store.create_snapshot(name="v1")
        assert snapshot["asset_count"] == 2

        records = store.materialize(tmp_path / "pool")
        assert len(records) == 2
        assert store.count_assets() == 2
