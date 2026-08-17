"""Observability tests: sync run lifecycle, progress events, unified logging.

Sync runs execute on Ray tasks (separate processes), so fake hubs are
injected via ``hub=`` (picklable instances from ``fakehub``), never by
monkeypatching. Blocking gates are file-based: a worker polls until the gate
file appears.
"""

import io
import logging
import threading
import time
from pathlib import Path

import pytest
import ray
from fakehub import CrashingHub, FailingHub, FakeHub
from fastapi.testclient import TestClient
from PIL import Image

from asset_management.assets.api import AssetStore, open_store
from asset_management.assets.routes import create_app
from asset_management.assets.services.downloaders.download import _progress_tqdm_class
from asset_management.assets.storage import LocalStorageBackend
from asset_management.log import get_logger, setup_logging


def _images(root: Path, n: int = 3):
    root.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        Image.new("RGB", (10, 10), "red").save(root / f"photo_{i}.png")
    return root


def _add_hf_source(store, repo_id="org/ds", **params):
    return store.add_source("hf", "huggingface", params={"repo_id": repo_id, **params})


def _wait_sync_finished(client, run_id, timeout=15.0):
    """Wait until the run's terminal 'done' event is visible — i.e. the
    background thread finished its final DB writes — so the test can close
    the store without racing a live sqlite connection."""
    last = 0
    deadline = time.time() + timeout
    while time.time() < deadline:
        events = client.get(f"/api/sync/{run_id}/events", params={"after": last}).json()
        if events and events[-1]["stage"] == "done":
            return
        time.sleep(0.05)
    raise AssertionError(f"sync run {run_id} did not finish in time")


# ----------------------------------------------------------- run lifecycle
def test_sync_run_lifecycle_and_stages(tmp_path, ray_runtime):
    with open_store(data_dir=tmp_path / "data") as store:
        source = _add_hf_source(store)
        report = store.sync_source(source.id, hub=FakeHub())
        assert report.new == 3

        runs = store.list_sync_runs()
        assert len(runs) == 1
        run = runs[0]
        assert run["status"] == "done"
        assert run["progress"] == 100.0
        assert run["total_files"] == 3
        assert run["done_files"] == 3

        events = store.get_sync_events(run["id"])
        stages = {ev["stage"] for ev in events}
        assert {"resolve", "download", "process", "persist", "done"} <= stages
        messages = " | ".join(ev["message"] for ev in events)
        assert "暂存区" in messages
        assert "持久化" in messages


def test_sync_events_incremental(tmp_path, ray_runtime):
    with open_store(data_dir=tmp_path / "data") as store:
        source = _add_hf_source(store)
        store.sync_source(source.id, hub=FakeHub())
        run = store.list_sync_runs()[0]
        all_events = store.get_sync_events(run["id"])
        tail = store.get_sync_events(run["id"], after_id=all_events[0]["id"])
        assert all(ev["id"] > all_events[0]["id"] for ev in tail)
        assert len(tail) == len(all_events) - 1


def test_sync_run_failed(tmp_path, ray_runtime):
    with open_store(data_dir=tmp_path / "data") as store:
        source = _add_hf_source(store)
        store.sync_source(source.id, hub=FailingHub())
        run = store.list_sync_runs()[0]
        # the run completes (pipeline finished); per-file failures are counted
        assert run["status"] == "done"
        assert run["failed_files"] == 3
        assert run["done_files"] == 0
        events = store.get_sync_events(run["id"])
        assert any(ev["level"] == "error" for ev in events)
        assert any("失败" in ev["message"] for ev in events)


def test_sync_crash_retried_then_reported(tmp_path, ray_runtime):
    """A task that keeps killing its worker is retried by Ray Data; once the
    retries are exhausted the pipeline aborts and the run is marked failed.
    The raw-layer state (attempts) is kept for a later resume."""
    with open_store(data_dir=tmp_path / "data") as store:
        source = _add_hf_source(store)
        with pytest.raises(ray.exceptions.WorkerCrashedError):
            store.sync_source(source.id, hub=CrashingHub())
        run = store.list_sync_runs()[0]
        assert run["status"] == "failed"
        raws = store.list_raw_files(source.id)
        assert raws, "crashed tasks must leave their raw-layer state behind"
        assert any(r["attempts"] >= 2 for r in raws)  # Ray 重试至少发生一次


def test_start_sync_rejects_running_run(tmp_path):
    with open_store(data_dir=tmp_path / "data") as store:
        source = _add_hf_source(store)
        store.start_sync(source.id)
        with pytest.raises(ValueError, match="already syncing"):
            store.start_sync(source.id)


def test_start_sync_rejects_paused_run(tmp_path):
    with open_store(data_dir=tmp_path / "data") as store:
        source = _add_hf_source(store)
        run_id = store.start_sync(source.id)
        store.pause_sync(run_id)
        with pytest.raises(ValueError, match="already syncing"):
            store.start_sync(source.id)
        assert store.get_running_run(source.id)["id"] == run_id  # paused 仍占用入口


def test_pause_resume_invalid_transitions(tmp_path):
    with open_store(data_dir=tmp_path / "data") as store:
        source = _add_hf_source(store)
        run_id = store.start_sync(source.id)
        with pytest.raises(ValueError, match="not paused"):
            store.resume_sync(run_id)
        store.pause_sync(run_id)
        with pytest.raises(ValueError, match="not running"):
            store.pause_sync(run_id)
        store.resume_sync(run_id)
        with pytest.raises(ValueError, match="not paused"):
            store.resume_sync(run_id)
        with pytest.raises(ValueError, match="unknown sync run"):
            store.pause_sync("nope")


def test_sync_pause_halts_between_files_and_resume_continues(tmp_path, ray_runtime):
    """Pause parks the driver between outcome rows (pull-based backpressure):
    raw uploads in flight finish, but Phase B never starts while paused.
    Resume picks the run up and finishes it."""
    gate = tmp_path / "gate"
    hub = FakeHub(gate_path=str(gate), gated_suffix="c.png")
    with open_store(data_dir=tmp_path / "data") as store:
        source = _add_hf_source(store, workers=3)
        thread = threading.Thread(
            target=store.sync_source,
            args=(source.id,),
            kwargs={"hub": hub},
            daemon=True,
        )
        thread.start()
        try:
            for _ in range(400):  # 等 a/b 两个文件上传 raw 层（c 被 gate 阻塞）
                if len(store.list_raw_files(source.id)) >= 2:
                    break
                time.sleep(0.05)
            run = store.list_sync_runs()[0]
            store.pause_sync(run["id"])
            assert store.get_sync_run(run["id"])["status"] == "paused"

            gate.write_text("")  # c.png 完成下载上传，但 driver 已停驻
            time.sleep(0.8)
            assert store.count_assets() == 0  # Phase B 在暂停期间不会启动

            store.resume_sync(run["id"])
            thread.join(timeout=60)
            assert store.count_assets() == 3
            run = store.get_sync_run(run["id"])
            assert run["status"] == "done"
            assert run["done_files"] == 3
            assert any(
                ev["stage"] == "control" for ev in store.get_sync_events(run["id"])
            )
        finally:
            gate.write_text("")
            thread.join(timeout=60)


def test_stale_paused_runs_marked_interrupted_on_reopen(tmp_path):
    with open_store(data_dir=tmp_path / "data") as store:
        source = _add_hf_source(store)
        run_id = store.start_sync(source.id)
        store.pause_sync(run_id)
    with open_store(data_dir=tmp_path / "data") as store:
        run = store.list_sync_runs()[0]
        assert run["status"] == "interrupted"
        assert "restart" in run["error"]
        assert store.get_interrupted_run(source.id)["id"] == run_id


def test_stale_runs_marked_interrupted_on_reopen(tmp_path):
    with open_store(data_dir=tmp_path / "data") as store:
        source = _add_hf_source(store)
        store.start_sync(source.id)  # left "running"
    with open_store(data_dir=tmp_path / "data") as store:
        run = store.list_sync_runs()[0]
        assert run["status"] == "interrupted"
        assert "restart" in run["error"]


def test_run_history_persisted(tmp_path, ray_runtime):
    with open_store(data_dir=tmp_path / "data") as store:
        source = _add_hf_source(store)
        store.sync_source(source.id, hub=FakeHub())
        store.sync_source(source.id, hub=FakeHub())
        assert len(store.list_sync_runs(limit=10)) == 2


def test_import_dir_records_run(tmp_path):
    src = _images(tmp_path / "imgs")
    with open_store(data_dir=tmp_path / "data") as store:
        store.import_dir(src, source_name="imp")
        run = store.list_sync_runs()[0]
        assert run["status"] == "done"
        assert run["total_files"] == 3


# ------------------------------------------------------------ byte progress
def test_tqdm_progress_class_throttled():

    events = []
    cls = _progress_tqdm_class(
        lambda **kw: events.append(kw), "a.png", min_interval_pct=25
    )
    bar = cls(total=100)
    bar.update(20)  # 20%  no event
    bar.update(20)  # 40%  event
    bar.update(5)  # 45%  no event (throttle)
    bar.update(35)  # 80%  event
    bar.update(20)  # 100% event (final)
    assert [round(ev["fraction"], 1) for ev in events] == [0.4, 0.8, 1.0]
    assert all(ev["stage"] == "download" and ev["remote"] == "a.png" for ev in events)
    assert all("MB/s" in ev["message"] for ev in events)


def test_tqdm_progress_without_total_still_reports():
    """Downloads without a known total (gzip/chunked, Xet streams) must still
    emit time-throttled byte events instead of going silent."""

    events = []
    cls = _progress_tqdm_class(
        lambda **kw: events.append(kw), "a.png", min_interval_sec=0.0
    )
    bar = cls(total=None)
    bar.update(1000)
    bar.update(2000)
    bar.update(3000)
    assert len(events) == 3
    assert all(ev["fraction"] is None for ev in events)
    assert all("字节" in ev["message"] for ev in events)


def test_download_fraction_survives_ray(tmp_path, ray_runtime):
    """Per-file download fractions reported by the tqdm callback inside a Ray
    worker are persisted to sync_events (fraction column) and readable back."""
    with open_store(data_dir=tmp_path / "data") as store:
        source = _add_hf_source(store)
        store.sync_source(source.id, hub=FakeHub())
        run = store.list_sync_runs()[0]
        fractions = {
            round(ev["fraction"], 1)
            for ev in store.get_sync_events(run["id"])
            if ev["stage"] == "download" and ev["fraction"] is not None
        }
        assert {0.4, 0.8, 1.0} <= fractions


def test_sources_api_reports_running_run(tmp_path, ray_runtime):
    """/api/sources exposes running_run_id while a sync is in flight (for the
    "查看进度" entry point), and None once it finishes."""

    gate = tmp_path / "gate"
    store = AssetStore(
        tmp_path / "assets.db",
        LocalStorageBackend(tmp_path / "blobs"),
        tmp_dir=tmp_path / "tmp",
        hub=FakeHub(gate_path=str(gate)),
    )
    source = store.add_source("hf", "huggingface", params={"repo_id": "org/ds"})
    client = TestClient(create_app(store))
    try:
        assert client.get("/api/sources").json()[0]["running_run_id"] is None

        response = client.post(f"/api/sources/{source.id}/sync")
        assert response.status_code == 202
        run_id = response.json()["run_id"]

        running = client.get("/api/sources").json()[0]
        assert running["running_run_id"] == run_id
        assert store.get_running_run(source.id)["id"] == run_id

        gate.write_text("")
        for _ in range(400):
            run = client.get(f"/api/sync/{run_id}").json()
            if run["status"] != "running":
                break
            time.sleep(0.05)
        assert client.get("/api/sources").json()[0]["running_run_id"] is None
        assert store.get_running_run(source.id) is None
        _wait_sync_finished(client, run_id)
    finally:
        gate.write_text("")
        store.close()


def test_two_phase_pipeline_uploads_raw_before_assets(tmp_path, ray_runtime):
    """Phase A must fully populate the raw layer before Phase B persists any
    asset: no asset exists while a download is still blocked."""
    gate = tmp_path / "gate"
    hub = FakeHub(
        files=["data/a.png", "data/b.png"], gate_path=str(gate), gated_suffix="b.png"
    )
    with open_store(data_dir=tmp_path / "data") as store:
        source = _add_hf_source(store, workers=2)
        thread = threading.Thread(
            target=store.sync_source,
            args=(source.id,),
            kwargs={"hub": hub},
            daemon=True,
        )
        thread.start()
        try:
            for _ in range(400):  # a.png 已上传 raw 层，b.png 仍在下载
                if len(store.list_raw_files(source.id)) >= 1:
                    break
                time.sleep(0.05)
            assert store.count_assets() == 0, "b.png 未下载完，资产层必须为空"

            gate.write_text("")
            thread.join(timeout=60)
            assert store.count_assets() == 2
            assert len(store.list_raw_files(source.id)) == 2
            run = store.list_sync_runs()[0]
            assert run["status"] == "done"
        finally:
            gate.write_text("")
            thread.join(timeout=60)


# ------------------------------------------------------------ unified logging
def test_unified_log_format():

    buf = io.StringIO()
    setup_logging(level=logging.INFO, stream=buf)
    get_logger("test.obs").info("hello %s", "world")
    line = buf.getvalue().strip()
    assert "[asset]" in line
    # format identifies the emitting file:line:function
    assert "test_asset_observability.py" in line
    assert "test_unified_log_format" in line
    assert "hello world" in line


# ------------------------------------------------------------ web async sync
def test_web_async_sync_and_polling(tmp_path, ray_runtime):

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
        run_id = response.json()["run_id"]

        run = None
        for _ in range(400):
            run = client.get(f"/api/sync/{run_id}").json()
            if run["status"] != "running":
                break
            time.sleep(0.05)
        assert run["status"] == "done"
        assert run["progress"] == 100.0

        events = client.get(f"/api/sync/{run_id}/events").json()
        assert {"download", "process", "persist", "done"} <= {
            ev["stage"] for ev in events
        }

        runs = client.get("/api/sync/runs").json()
        assert any(r["id"] == run_id for r in runs)
        assert client.get("/api/sync/nope").status_code == 404
        _wait_sync_finished(client, run_id)
    finally:
        store.close()


def test_web_sync_pause_resume_endpoints(tmp_path, ray_runtime):

    gate = tmp_path / "gate"
    store = AssetStore(
        tmp_path / "assets.db",
        LocalStorageBackend(tmp_path / "blobs"),
        tmp_dir=tmp_path / "tmp",
        hub=FakeHub(gate_path=str(gate)),
    )
    source = store.add_source("hf", "huggingface", params={"repo_id": "org/ds"})
    client = TestClient(create_app(store))
    try:
        run_id = client.post(f"/api/sources/{source.id}/sync").json()["run_id"]

        response = client.post(f"/api/sync/{run_id}/pause")
        assert response.status_code == 200
        assert response.json()["status"] == "paused"
        assert client.post(f"/api/sync/{run_id}/pause").status_code == 400  # 已暂停

        response = client.post(f"/api/sync/{run_id}/resume")
        assert response.status_code == 200
        assert response.json()["status"] == "running"

        assert client.post("/api/sync/nope/pause").status_code == 404

        gate.write_text("")
        for _ in range(400):
            run = client.get(f"/api/sync/{run_id}").json()
            if run["status"] != "running":
                break
            time.sleep(0.05)
        assert run["status"] == "done"
        assert client.post(f"/api/sync/{run_id}/pause").status_code == 400  # 已完成
        _wait_sync_finished(client, run_id)
    finally:
        gate.write_text("")
        store.close()


def test_web_duplicate_sync_returns_409(tmp_path, ray_runtime):

    gate = tmp_path / "gate"
    store = AssetStore(
        tmp_path / "assets.db",
        LocalStorageBackend(tmp_path / "blobs"),
        tmp_dir=tmp_path / "tmp",
        hub=FakeHub(gate_path=str(gate)),
    )
    source = store.add_source("hf", "huggingface", params={"repo_id": "org/ds"})
    client = TestClient(create_app(store))
    try:
        first = client.post(f"/api/sources/{source.id}/sync")
        assert first.status_code == 202
        run_id = first.json()["run_id"]
        assert client.post(f"/api/sources/{source.id}/sync").status_code == 409
        gate.write_text("")  # let the blocked download finish
        for _ in range(400):
            run = client.get(f"/api/sync/{run_id}").json()
            if run["status"] != "running":
                break
            time.sleep(0.05)
        assert run["status"] == "done"
        _wait_sync_finished(client, run_id)
    finally:
        gate.write_text("")
        store.close()
