import pytest
from PIL import Image

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from llava_instruct.assets.store import AssetStore  # noqa: E402
from llava_instruct.assets.storage import LocalStorageBackend  # noqa: E402
from llava_instruct.assets.web import create_app  # noqa: E402


@pytest.fixture
def client(tmp_path):
    src = tmp_path / "imgs"
    src.mkdir()
    Image.new("RGB", (12, 8), "red").save(src / "photo_a.png")
    Image.new("RGB", (12, 8), "gray").save(src / "doc_page1.png")
    backend = LocalStorageBackend(tmp_path / "blobs")
    store = AssetStore(tmp_path / "assets.db", backend, tmp_dir=tmp_path / "tmp")
    store.import_dir(src, source_name="web-test")
    app = create_app(store)
    yield TestClient(app)
    store.close()


def test_index_page(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "数据资产管理" in response.text
    assert "llava-instruct asset manager" in response.text


def test_info_endpoint(client):
    response = client.get("/api/info")
    assert response.status_code == 200
    info = response.json()
    assert info["backend"] == "local"
    assert info["asset_count"] == 2
    assert info["ready_count"] == 2
    assert info["failed_count"] == 0


def test_downloads_endpoint(client):
    response = client.get("/api/downloads?limit=10")
    assert response.status_code == 200
    records = response.json()
    assert len(records) == 2
    assert all(r["status"] == "done" for r in records)


def test_rollback_endpoint(client):
    asset = client.get("/api/assets").json()[0]
    client.post(f"/api/assets/{asset['id']}/rollback", json={"version": 1})
    detail = client.get(f"/api/assets/{asset['id']}").json()
    assert detail["current_version"] == 1


def test_sources_api(client):
    response = client.get("/api/sources")
    assert response.status_code == 200
    assert response.json()[0]["kind"] == "local"

    response = client.post("/api/sources", json={"name": "hf-src", "kind": "huggingface", "url": "https://hf.co", "params": {"repo_id": "org/ds"}})
    assert response.status_code == 201
    source_id = response.json()["id"]
    assert client.put(f"/api/sources/{source_id}", json={"name": "hf-src2", "kind": "huggingface"}).status_code == 200
    assert client.delete(f"/api/sources/{source_id}").status_code == 204


def test_assets_api_filter_and_tag(client):
    response = client.get("/api/assets")
    assert response.status_code == 200
    assets = response.json()
    assert len(assets) == 2

    chart = [a for a in assets if a["asset_type"] == "document_image"][0]
    response = client.post(f"/api/assets/{chart['id']}/tags", json={"name": "doc", "group": "task"})
    assert response.status_code == 201
    filtered = client.get("/api/assets", params={"tag": "task=doc"}).json()
    assert len(filtered) == 1
    assert filtered[0]["id"] == chart["id"]

    assert client.delete(f"/api/assets/{chart['id']}/tags/doc").status_code == 204
    detail = client.get(f"/api/assets/{chart['id']}").json()
    assert "versions" in detail and len(detail["versions"]) == 1


def test_sync_rejects_local_import_source(client):
    """Import sources (kind=local) are store-level only; sync requires huggingface."""
    source = client.get("/api/sources").json()[0]
    response = client.post(f"/api/sources/{source['id']}/sync")
    assert response.status_code == 400


def test_preview(client):
    asset = client.get("/api/assets").json()[0]
    response = client.get(f"/api/assets/{asset['id']}/preview")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/png")


def test_snapshots_api(client):
    response = client.post("/api/snapshots", json={"name": "v1"})
    assert response.status_code == 201
    assert response.json()["asset_count"] == 2
    assert len(client.get("/api/snapshots").json()) == 1


def test_delete_asset(client):
    asset = client.get("/api/assets").json()[0]
    assert client.delete(f"/api/assets/{asset['id']}").status_code == 204
    assert client.get(f"/api/assets/{asset['id']}").status_code == 404
