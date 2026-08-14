"""Ray Data pipeline tests: two-phase sync, raw layer, reprocess, stages."""

from fakehub import FailingHub, FakeHub
from fastapi.testclient import TestClient

from llava_instruct.assets.api import open_store
from llava_instruct.assets.routes import create_app


def _add_hf_source(store, repo_id="org/ds", **params):
    return store.add_source("hf", "huggingface", params={"repo_id": repo_id, **params})


def test_sync_populates_raw_layer_and_blobs(tmp_path, ray_runtime):
    """A full sync uploads every repo file into the path-addressed raw layer
    first, then persists content-addressed assets referencing their raw row."""
    with open_store(data_dir=tmp_path / "data") as store:
        source = _add_hf_source(store)
        report = store.sync_source(source.id, hub=FakeHub())
        assert report.new == 3
        raws = store.list_raw_files(source.id)
        assert [r["path_in_repo"] for r in raws] == [
            "data/a.png",
            "data/b.png",
            "data/c.png",
        ]
        assert all(r["status"] == "uploaded" for r in raws)
        assert all(r["object_key"].startswith(f"raw/{source.id}/") for r in raws)
        assert all(r["sha256"] and r["size"] > 0 for r in raws)
        assert all(r["attempts"] == 1 for r in raws)
        for raw in raws:
            assert store.backend.exists(raw["object_key"])
        assets = store.list_assets(status="ready")
        assert len(assets) == 3
        for asset in assets:
            assert asset.object_key.startswith("blobs/")
            assert asset.meta["raw"]["path_in_repo"].startswith("data/")
            assert asset.meta["raw"]["sha256"] != ""


def test_sync_stages_recorded(tmp_path, ray_runtime):
    """Every run persists one sync_stages row per stage: wall time, item and
    failure counts, app/Ray retry counters."""
    with open_store(data_dir=tmp_path / "data") as store:
        source = _add_hf_source(store)
        store.sync_source(source.id, hub=FakeHub())
        run = store.list_sync_runs()[0]
        stages = {s["stage"]: s for s in store.get_sync_stages(run["id"])}
        assert {"resolve", "download_raw", "process", "persist"} <= set(stages)
        assert stages["download_raw"]["item_count"] == 3
        assert stages["download_raw"]["failed_count"] == 0
        assert stages["persist"]["item_count"] == 3
        assert all(s["duration_s"] >= 0 for s in stages.values())


def test_reprocess_after_sync_without_network(tmp_path, ray_runtime):
    """reprocess_source runs Phase B only: the hub denies every file, so any
    download attempt would fail — the reprocess must still succeed (dedup)."""
    with open_store(data_dir=tmp_path / "data") as store:
        source = _add_hf_source(store)
        store.sync_source(source.id, hub=FakeHub())
        denied = FakeHub(deny=["data/a.png", "data/b.png", "data/c.png"])
        report = store.reprocess_source(source.id, hub=denied)
        assert report.new == 0
        assert report.skipped_existing == 3
        assert report.failed == 0
        assert store.count_assets() == 3


def test_failed_download_retried_on_next_sync(tmp_path, ray_runtime):
    """A download failure marks the raw row failed; the next sync retries it
    (attempts increment) and finishes the assets."""
    with open_store(data_dir=tmp_path / "data") as store:
        source = _add_hf_source(store)
        report = store.sync_source(source.id, hub=FailingHub())
        assert report.failed == 3
        raws = store.list_raw_files(source.id)
        assert all(r["status"] == "failed" for r in raws)
        assert all(r["attempts"] == 1 for r in raws)  # one task start each
        report2 = store.sync_source(source.id, hub=FakeHub())
        assert report2.failed == 0
        assert report2.new == 3
        assert all(r["status"] == "uploaded" for r in store.list_raw_files(source.id))


def test_raw_and_stages_endpoints(tmp_path, ray_runtime):
    with open_store(data_dir=tmp_path / "data") as store:
        source = _add_hf_source(store)
        store.sync_source(source.id, hub=FakeHub())
        run = store.list_sync_runs()[0]
        client = TestClient(create_app(store))

        data = client.get(f"/api/sources/{source.id}/raw").json()
        assert len(data) == 3
        assert all(r["status"] == "uploaded" for r in data)

        stages = client.get(f"/api/sync/{run['id']}/stages").json()
        assert {s["stage"] for s in stages} == {
            "resolve",
            "download_raw",
            "process",
            "persist",
        }
        assert client.get("/api/sync/nope/stages").status_code == 404
        assert client.get("/api/sources/nope/raw").status_code == 404
