import io
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from fakehub import FailingHub, FakeHub
from PIL import Image

from llava_instruct.assets.api import AssetStore
from llava_instruct.assets.storage import LocalStorageBackend


def _images(root):
    root.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (10, 10), "red").save(root / "photo_red.png")
    Image.new("RGB", (10, 10), "blue").save(root / "photo_blue.png")
    Image.new("RGB", (10, 10), "gray").save(root / "doc_page1.png")
    Image.new("RGB", (10, 10), "white").save(root / "chart_rev.png")
    return root


def make_store(tmp_path):
    backend = LocalStorageBackend(tmp_path / "blobs")
    return AssetStore(tmp_path / "assets.db", backend, tmp_dir=tmp_path / "tmp")


def test_import_dir_end_to_end(tmp_path):
    src = _images(tmp_path / "src")
    store = make_store(tmp_path)
    with store:
        report = store.import_dir(src, source_name="test-local")
        assert report.new == 4
        assert report.failed == 0
        assert len(store.list_assets(status="ready")) == 4

        # idempotent re-sync: dedup by sha256, no new assets
        report2 = store.import_dir(src, source_name="test-local")
        assert report2.new == 0
        assert report2.skipped_existing == 4

        assets = store.list_assets()
        types = {a.asset_type for a in assets}
        assert "document_image" in types
        assert "chart_image" in types
        assert "general_image" in types
        assert all(a.width == 10 for a in assets)
        assert all(a.sha256 for a in assets)
        assert all(a.object_key.startswith("blobs/") for a in assets)


def test_sync_failure_recorded(tmp_path, ray_runtime):
    store = make_store(tmp_path)
    with store:
        store.add_source("hf-bad", "huggingface", params={"repo_id": "org/bad"})
        source = store.list_sources()[0]
        report = store.sync_source(source.id, hub=FailingHub(files=["data/nope.png"]))
        assert report.failed == 1
        assert any("nope.png" in e for e in report.errors)
        rows = store.list_downloads()
        assert any(r["status"] == "failed" for r in rows)


def test_sync_rejects_non_huggingface_kind(tmp_path):
    store = make_store(tmp_path)
    with store:
        store.add_source("legacy", "http", params={"urls": []})
        source = store.list_sources()[0]
        with pytest.raises(ValueError, match="huggingface"):
            store.sync_source(source.id)


def test_tags_snapshot_materialize(tmp_path):
    src = _images(tmp_path / "src")
    store = make_store(tmp_path)
    with store:
        store.import_dir(src, source_name="test-local")
        assets = store.list_assets()
        chart = next(a for a in assets if a.asset_type == "chart_image")
        store.tag_asset(chart.id, "chart", group="task")
        assert len(store.list_assets(tags=["task=chart"])) == 1

        snapshot = store.create_snapshot(name="v1")
        assert snapshot["asset_count"] == 4
        assert len(store.list_snapshots()) == 1

        out_dir = tmp_path / "pool"
        records = store.materialize(out_dir)
        assert len(records) == 4
        assert all(Path(r["path"]).exists() for r in records)
        chart_record = next(r for r in records if r["asset_type"] == "chart_image")
        assert "task=chart" in chart_record["tags"]

        # export_pool writes the jsonl consumed by generate
        pool_path = tmp_path / "assets.jsonl"
        exported = store.export_pool(pool_path, out_dir=out_dir)
        assert len(exported) == 4
        assert pool_path.exists()


def test_version_rollback(tmp_path):
    src = _images(tmp_path / "src")
    store = make_store(tmp_path)
    with store:
        store.import_dir(src, source_name="test-local")
        asset = store.list_assets()[0]
        store.bump_version(asset.id, "0" * 64, "blobs/00/000.png", "manual replace")
        history = store.version_history(asset.id)
        assert len(history) == 2
        rolled = store.rollback(asset.id, 1)
        assert rolled.sha256 == asset.sha256
        assert rolled.current_version == 1


def test_delete_source_cascades(tmp_path):
    src = _images(tmp_path / "src")
    store = make_store(tmp_path)
    with store:
        store.import_dir(src, source_name="test-local")
        source = store.list_sources()[0]
        store.delete_source(source.id)
        assert len(store.list_assets()) == 0
        assert store.count_assets() == 0


def test_sync_parquet_processor_end_to_end(tmp_path, ray_runtime):
    """huggingface source + process=parquet: parquet decoded into image assets."""

    images = []
    for i in range(3):
        buf = io.BytesIO()
        Image.new("RGB", (10 + i, 8 + i), "red").save(buf, "PNG")
        images.append(buf.getvalue())
    parquet = tmp_path / "val.parquet"
    pq.write_table(
        pa.table({"image": pa.array(images, type=pa.binary())}),
        parquet,
    )

    hub = FakeHub(files=["data/val.parquet"], copies={"data/val.parquet": str(parquet)})
    store = make_store(tmp_path)
    with store:
        store.add_source(
            "coco", "huggingface", params={"repo_id": "org/coco", "process": "parquet"}
        )
        source = store.list_sources()[0]
        report = store.sync_source(source.id, hub=hub)
        assert report.new == 3
        assert report.failed == 0

        assets = store.list_assets(status="ready")
        assert len(assets) == 3
        assert all(a.asset_type == "general_image" for a in assets)
        assert all(a.width is not None and a.height is not None for a in assets)

        report2 = store.sync_source(source.id, hub=hub)  # dedup by sha256
        assert report2.new == 0
        assert report2.skipped_existing == 3


def test_materialize_missing_object(tmp_path):
    store = make_store(tmp_path)
    with store:
        src = _images(tmp_path / "src")
        store.import_dir(src, source_name="test-local")
        asset = store.list_assets()[0]
        store.delete_asset(asset.id)
        # deleting metadata does not remove blobs; the pool simply shrinks
        assert len(store.list_assets(status="ready")) == 3
