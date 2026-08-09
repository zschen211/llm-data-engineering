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
    assert "资产管理" in response.text


def test_sources_api(client):
    response = client.get("/api/sources")
    assert response.status_code == 200
    assert response.json()[0]["kind"] == "local"

    response = client.post("/api/sources", json={"name": "http-src", "kind": "http", "url": "https://x"})
    assert response.status_code == 201
    source_id = response.json()["id"]
    assert client.put(f"/api/sources/{source_id}", json={"name": "http-src2", "kind": "http"}).status_code == 200
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


def test_sync_trigger_and_preview(client):
    source = client.get("/api/sources").json()[0]
    report = client.post(f"/api/sources/{source['id']}/sync").json()
    assert report["skipped_existing"] == 2

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
