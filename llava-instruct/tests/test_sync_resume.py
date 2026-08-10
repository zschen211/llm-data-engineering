"""File-level crash recovery tests: sync_tasks, interrupted runs, resume.

A crash is simulated by seeding a run with tasks in various states and then
re-opening the database with ``mark_stale=True`` (what happens when the
service is restarted): stale runs become 'interrupted' and in-flight tasks go
back to 'pending'. Resuming must continue exactly the unfinished files.
"""

import time

from fakehub import FakeHub
from fastapi.testclient import TestClient

from llava_instruct.assets.api import AssetStore, open_store
from llava_instruct.assets.routes import create_app
from llava_instruct.assets.services.downloaders.base import RemoteRef
from llava_instruct.assets.services.downloaders.download import DownloadStage
from llava_instruct.assets.storage import LocalStorageBackend


def _add_hf_source(store, repo_id="org/ds", **params):
    return store.add_source("hf", "huggingface", params={"repo_id": repo_id, **params})


def _tasks_by_name(store, run_id):
    return {t["name"]: t for t in store.get_sync_tasks(run_id)}


# ------------------------------------------------------------- task tracking
def test_sync_tasks_track_file_progress(tmp_path, ray_runtime):
    """After a full sync every remote file has a task row with status,
    byte counts and final fraction — the two-level progress backbone."""
    with open_store(data_dir=tmp_path / "data") as store:
        source = _add_hf_source(store)
        store.sync_source(source.id, hub=FakeHub())
        run = store.list_sync_runs()[0]
        tasks = store.get_sync_tasks(run["id"])
        assert [t["name"] for t in tasks] == ["a.png", "b.png", "c.png"]
        assert all(t["status"] == "persisted" for t in tasks)
        assert all(t["fraction"] == 1.0 for t in tasks)
        assert all(t["attempts"] == 1 for t in tasks)
        assert all(t["bytes_downloaded"] == 100 for t in tasks)
        assert all(t["total_bytes"] == 100 for t in tasks)


def test_stable_cache_dir_survives_task_cleanup(tmp_path, ray_runtime):
    """The per-source HF cache (stable across runs, keyed by repo_id) is not
    deleted when a task finishes — only the staging work_dir is cleaned."""
    with open_store(data_dir=tmp_path / "data") as store:
        source = _add_hf_source(store)
        store.sync_source(source.id, hub=FakeHub())
        cache = tmp_path / "data" / "hf_cache" / "org" / "ds"
        assert (cache / "data" / "a.png").is_file()
        assert (cache / "data" / "b.png").is_file()
        assert not list((tmp_path / "data" / "tmp").iterdir())


# ------------------------------------------------------- crash + file resume
def test_crash_then_resume_continues_unfinished_files(tmp_path, ray_runtime):
    """Seeded crash mid-flight: on reopen the run is 'interrupted', the
    in-flight task goes back to 'pending' (keeping its last-known progress),
    and resuming re-submits only the unfinished files — the persisted one is
    never requested again (hub denies it)."""
    store1 = open_store(data_dir=tmp_path / "data")
    source = _add_hf_source(store1)
    run_id = store1.start_sync(source.id)
    remotes = DownloadStage.from_source(source, hub=FakeHub()).resolve()
    assert store1._db.create_sync_tasks(run_id, remotes) == 3
    by_name = _tasks_by_name(store1, run_id)
    store1._db.update_sync_task(
        run_id, by_name["a.png"]["remote_id"], status="persisted", fraction=1.0
    )
    store1._db.update_sync_task(
        run_id,
        by_name["b.png"]["remote_id"],
        status="downloading",
        bytes_downloaded=70,
        total_bytes=100,
        fraction=0.7,
        attempts=1,
    )
    store1.close()  # crash: the process dies with b mid-download

    with open_store(data_dir=tmp_path / "data") as store2:
        run = store2.get_sync_run(run_id)
        assert run["status"] == "interrupted"
        assert "restart" in run["error"]
        tasks = _tasks_by_name(store2, run_id)
        assert tasks["a.png"]["status"] == "persisted"
        assert tasks["b.png"]["status"] == "pending"  # in-flight reset
        assert tasks["b.png"]["bytes_downloaded"] == 70  # last-known kept
        assert tasks["c.png"]["status"] == "pending"

        # a.png must never be requested again → deny it
        hub = FakeHub(deny=["data/a.png"])
        assert store2.resume_source(source.id) == run_id  # reuses the run
        report = store2.sync_source(source.id, run_id=run_id, hub=hub)
        assert report.failed == 0

        run = store2.get_sync_run(run_id)
        assert run["status"] == "done"
        assert run["done_files"] == 3
        assert run["failed_files"] == 0
        assert store2.count_assets() == 2  # only b/c were actually persisted
        tasks = _tasks_by_name(store2, run_id)
        assert tasks["a.png"]["attempts"] == 0  # never re-submitted
        assert tasks["b.png"]["attempts"] == 2  # seeded 1 + resume
        assert tasks["c.png"]["attempts"] == 1


def test_resume_skips_already_persisted_files(tmp_path, ray_runtime):
    """A resume where every file was already persisted finishes immediately
    without submitting any task."""
    store1 = open_store(data_dir=tmp_path / "data")
    source = _add_hf_source(store1)
    run_id = store1.start_sync(source.id)
    remotes = DownloadStage.from_source(source, hub=FakeHub()).resolve()
    store1._db.create_sync_tasks(run_id, remotes)
    for t in store1.get_sync_tasks(run_id):
        store1._db.update_sync_task(
            run_id, t["remote_id"], status="persisted", fraction=1.0
        )
    store1.close()

    with open_store(data_dir=tmp_path / "data") as store2:
        assert store2.get_sync_run(run_id)["status"] == "interrupted"
        store2.resume_source(source.id)
        report = store2.sync_source(
            source.id,
            run_id=run_id,
            hub=FakeHub(deny=["data/a.png", "data/b.png", "data/c.png"]),
        )
        assert report.failed == 0
        run = store2.get_sync_run(run_id)
        assert run["status"] == "done"
        assert run["done_files"] == 3


def test_reconcile_fails_tasks_for_removed_files(tmp_path):
    """Tasks whose file disappeared from the repo (resume against a changed
    file list) are marked failed instead of being downloaded."""
    with open_store(data_dir=tmp_path / "data") as store:
        source = _add_hf_source(store)
        run_id = store.start_sync(source.id)
        store._db.create_sync_tasks(
            run_id,
            [RemoteRef(id="hf_old", name="old.png", path_in_repo="data/old.png")],
        )
        assert store._db.reconcile_sync_tasks(run_id, {"hf_new"}) == 1
        task = store.get_sync_tasks(run_id)[0]
        assert task["status"] == "failed"
        assert "removed" in task["error"]


# ------------------------------------------------------------------- web API
def test_web_resume_after_interrupt(tmp_path, ray_runtime):
    """/api/sources exposes resumable_run_id after a crash; POSTing /sync
    resumes the interrupted run (same run_id, resumed=true) and the tasks
    endpoint returns per-file rows."""
    store1 = open_store(data_dir=tmp_path / "data")
    source = _add_hf_source(store1)
    run_id = store1.start_sync(source.id)
    remotes = DownloadStage.from_source(source, hub=FakeHub()).resolve()
    store1._db.create_sync_tasks(run_id, remotes)
    store1.close()

    store2 = AssetStore(
        tmp_path / "data" / "assets.db",
        LocalStorageBackend(tmp_path / "data" / "blobs"),
        tmp_dir=tmp_path / "data" / "tmp",
        hub=FakeHub(),
    )
    client = TestClient(create_app(store2))
    try:
        sources = client.get("/api/sources").json()
        assert sources[0]["running_run_id"] is None
        assert sources[0]["resumable_run_id"] == run_id

        response = client.post(f"/api/sources/{source.id}/sync")
        assert response.status_code == 202
        assert response.json()["resumed"] is True
        assert response.json()["run_id"] == run_id

        for _ in range(400):
            run = client.get(f"/api/sync/{run_id}").json()
            if run["status"] != "running":
                break
            time.sleep(0.05)
        assert run["status"] == "done"

        data = client.get(f"/api/sync/{run_id}/tasks").json()
        assert data["total"] == 3
        assert len(data["items"]) == 3
        assert all(t["status"] == "persisted" for t in data["items"])
        page = client.get(f"/api/sync/{run_id}/tasks?offset=1&limit=2").json()
        assert page["total"] == 3
        assert [t["name"] for t in page["items"]] == ["b.png", "c.png"]
        assert client.get("/api/sync/nope/tasks").status_code == 404
    finally:
        store2.close()


def test_web_sync_starts_fresh_when_no_interrupted_run(tmp_path, ray_runtime):
    store = AssetStore(
        tmp_path / "assets.db",
        LocalStorageBackend(tmp_path / "blobs"),
        tmp_dir=tmp_path / "tmp",
        hub=FakeHub(),
    )
    source = store.add_source("hf", "huggingface", params={"repo_id": "org/ds"})
    client = TestClient(create_app(store))
    try:
        response = client.post(f"/api/sources/{source.id}/sync")
        assert response.status_code == 202
        assert response.json()["resumed"] is False
        run_id = response.json()["run_id"]
        for _ in range(400):
            run = client.get(f"/api/sync/{run_id}").json()
            if run["status"] != "running":
                break
            time.sleep(0.05)
        assert run["status"] == "done"
        assert client.get("/api/sources").json()[0]["resumable_run_id"] is None
    finally:
        store.close()
