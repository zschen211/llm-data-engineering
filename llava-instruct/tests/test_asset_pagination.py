"""Cursor pagination tests: db keyset page, SQL-pushed filters, web API."""

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from llava_instruct.assets.api import AssetStore, open_store
from llava_instruct.assets.meta.db import Database
from llava_instruct.assets.routes import create_app
from llava_instruct.assets.storage import LocalStorageBackend


@pytest.fixture
def db_with_assets(tmp_path):
    db = Database(tmp_path / "assets.db")
    source = db.add_source("s", "huggingface", params={"repo_id": "org/ds"})
    for i in range(60):
        db.add_asset(
            f"ast_{i:04d}",
            source.id,
            f"img_{i:04d}.png",
            "general_image",
            f"k{i}",
            f"sha{i}",
            100,
            10,
            10,
        )
    yield db
    db.close()


# ---------------------------------------------------------------- db keyset
def test_list_assets_page_continuity(db_with_assets):
    seen = set()
    cursor = None
    pages = 0
    while True:
        items, cursor = db_with_assets.list_assets_page(limit=20, cursor=cursor)
        pages += 1
        assert items
        assert all(a.id not in seen for a in items)  # no overlap
        seen |= {a.id for a in items}
        if cursor is None:
            break
        assert pages <= 10
    assert len(seen) == 60
    assert pages == 3


def test_list_assets_page_deterministic_order(db_with_assets):
    items, _cursor = db_with_assets.list_assets_page(limit=20)
    ids = [a.id for a in items]
    assert ids == sorted(
        ids, reverse=True
    )  # created_at DESC, id DESC (same-second tiebreak)


def test_list_assets_page_tag_filter_in_sql(db_with_assets):
    db_with_assets.tag_asset("ast_0000", "chart", group="task")
    items, cursor = db_with_assets.list_assets_page(tags=["task=chart"], limit=10)
    assert [a.id for a in items] == ["ast_0000"]
    assert cursor is None  # single match, no more pages


def test_list_assets_page_keyword_search(db_with_assets):
    items, _ = db_with_assets.list_assets_page(q="img_0005", limit=10)
    assert [a.id for a in items] == ["ast_0005"]
    items, _ = db_with_assets.list_assets_page(q="ast_0007", limit=10)
    assert [a.id for a in items] == ["ast_0007"]


def test_count_assets_with_filters(db_with_assets):
    db_with_assets.tag_asset("ast_0001", "chart", group="task")
    assert db_with_assets.count_assets() == 60
    assert db_with_assets.count_assets(status="ready") == 60
    assert db_with_assets.count_assets(tags=["task=chart"]) == 1
    assert db_with_assets.count_assets(q="img_0002") == 1
    db_with_assets.add_asset(
        "ast_bad",
        db_with_assets.list_sources()[0].id,
        "broken.png",
        "general_image",
        "kb",
        "shab",
        1,
        1,
        1,
        status="failed",
    )
    assert db_with_assets.count_assets(status="failed") == 1


def test_list_assets_unlimited_still_works(db_with_assets):
    assert len(db_with_assets.list_assets()) == 60


# ------------------------------------------------------------------- store
def test_store_list_assets_page(tmp_path):

    with open_store(data_dir=tmp_path / "data") as store:
        source = store.add_source("hf", "huggingface", params={"repo_id": "org/ds"})
        store._db.add_asset(
            "ast_1", source.id, "a.png", "general_image", "k1", "sha1", 1, 1, 1
        )
        store._db.add_asset(
            "ast_2", source.id, "b.png", "general_image", "k2", "sha2", 1, 1, 1
        )

        page = store.list_assets_page(page_size=1)
        assert len(page["items"]) == 1
        assert page["page_size"] == 1
        assert page["next_cursor"]

        page2 = store.list_assets_page(page_size=1, cursor=page["next_cursor"])
        assert len(page2["items"]) == 1
        assert page2["next_cursor"] is None
        assert page["items"][0]["id"] != page2["items"][0]["id"]

        with pytest.raises(ValueError, match="invalid cursor"):
            store.list_assets_page(cursor="not-a-valid-cursor")


# -------------------------------------------------------------------- web
def test_web_assets_cursor_pagination(tmp_path):
    src = tmp_path / "imgs"
    src.mkdir()
    Image.new("RGB", (10, 10), "red").save(src / "photo_a.png")
    Image.new("RGB", (10, 10), "gray").save(src / "doc_page1.png")
    store = AssetStore(
        tmp_path / "assets.db",
        LocalStorageBackend(tmp_path / "blobs"),
        tmp_dir=tmp_path / "tmp",
    )
    store.import_dir(src, source_name="web")
    client = TestClient(create_app(store))
    try:
        page1 = client.get("/api/assets", params={"page_size": 1}).json()
        assert len(page1["items"]) == 1
        assert page1["next_cursor"]

        page2 = client.get(
            "/api/assets", params={"page_size": 1, "cursor": page1["next_cursor"]}
        ).json()
        assert len(page2["items"]) == 1
        assert page2["next_cursor"] is None
        assert page1["items"][0]["id"] != page2["items"][0]["id"]

        assert (
            client.get("/api/assets", params={"cursor": "garbage"}).status_code == 400
        )
        assert client.get("/api/assets", params={"page_size": 0}).status_code == 422

        search = client.get("/api/assets", params={"q": "photo_a"}).json()
        assert [a["name"] for a in search["items"]] == ["photo_a.png"]
    finally:
        store.close()
