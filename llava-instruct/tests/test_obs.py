"""Observability tests: /metrics endpoint, middleware, Ray gauges, events."""

import json
import os

import pytest
import ray
from fastapi.testclient import TestClient
from PIL import Image

from llava_instruct.assets.api import AssetStore
from llava_instruct.assets.routes import create_app
from llava_instruct.assets.services.cluster import ClusterManager
from llava_instruct.assets.services.obs import Observability, observability
from llava_instruct.assets.storage import LocalStorageBackend


@pytest.fixture(autouse=True)
def _reset_observability(tmp_path):
    observability.reset(log_dir=tmp_path / "obs", interval=0.5)
    yield
    observability.reset(log_dir=None)


def _make_store(tmp_path) -> AssetStore:
    backend = LocalStorageBackend(tmp_path / "blobs")
    return AssetStore(tmp_path / "assets.db", backend, tmp_dir=tmp_path / "tmp")


def _metric_lines(text: str, name: str) -> list[str]:
    return [line for line in text.splitlines() if line.startswith(name)]


def _metrics_text() -> str:
    client = TestClient(observability.metrics_app())
    return client.get("/metrics").text


def test_metrics_endpoint_serves_prometheus_text():
    text = _metrics_text()
    # Gauges are exposed from creation (0 until sampled); counters and
    # histograms only appear once they carry samples, so they are asserted
    # in the tests that exercise them.
    for name in (
        "llava_process_cpu_percent",
        "llava_process_rss_bytes",
        "llava_process_threads",
        "llava_ray_total_cpus",
        "llava_ray_alive_nodes",
        "llava_ray_metrics_up",
        "llava_ray_session_logs_bytes",
    ):
        assert _metric_lines(text, name), f"missing metric {name}"


def test_http_middleware_records_requests(tmp_path):
    store = _make_store(tmp_path)
    with TestClient(create_app(store)) as client:
        assert client.get("/api/info").status_code == 200
        assert client.get("/api/info").status_code == 200
        text = _metrics_text()
    lines = _metric_lines(
        text, 'llava_http_requests_total{method="GET",route="/api/info",status="200"}'
    )
    assert len(lines) == 1
    assert float(lines[0].split()[-1]) == 2.0
    store.close()


def test_ray_gauges_reflect_cluster(ray_runtime, tmp_path):
    store = _make_store(tmp_path)
    with TestClient(create_app(store)) as client:
        observability._sample()
        text = client.get("/metrics").text
    expected = ray.cluster_resources().get("CPU", 0)
    lines = _metric_lines(text, "llava_ray_total_cpus ")
    assert lines and float(lines[0].split()[-1]) == expected
    assert any(
        float(l.split()[-1]) >= 1 for l in _metric_lines(text, "llava_ray_alive_nodes ")
    )
    store.close()


def test_task_counters_and_duration_histogram():
    observability.submit_ray_task()
    observability.submit_ray_task()
    observability.record_ray_task(succeeded=True, duration=0.5)
    observability.record_ray_task(succeeded=False, duration=1.5)
    text = _metrics_text()
    assert (
        float(_metric_lines(text, "llava_ray_tasks_submitted_total ")[0].split()[-1])
        == 2.0
    )
    assert (
        float(_metric_lines(text, "llava_ray_tasks_succeeded_total ")[0].split()[-1])
        == 1.0
    )
    assert (
        float(_metric_lines(text, "llava_ray_tasks_failed_total ")[0].split()[-1])
        == 1.0
    )
    assert "llava_ray_task_duration_seconds_count 2.0" in text


def test_event_sink_writes_jsonl(tmp_path):
    observability.start()
    observability.event("sync_run_finished", run_id="r1", failed=1, duration_s=2.5)
    observability.stop()
    path = tmp_path / "obs" / "events.jsonl"
    assert path.exists()
    record = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert record["event"] == "sync_run_finished"
    assert record["project"] == "llava-instruct"
    assert record["fields"] == {"run_id": "r1", "failed": 1, "duration_s": 2.5}


def test_import_dir_emits_sync_events(tmp_path):
    src = tmp_path / "imgs"
    src.mkdir()
    Image.new("RGB", (12, 8), "red").save(src / "a.png")
    Image.new("RGB", (12, 8), "gray").save(src / "b.png")
    store = _make_store(tmp_path)
    observability.start()
    store.import_dir(src, source_name="obs-test")
    observability.stop()
    records = [
        json.loads(line)
        for line in (tmp_path / "obs" / "events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    started = [r for r in records if r["event"] == "sync_run_started"]
    finished = [r for r in records if r["event"] == "sync_run_finished"]
    assert len(started) == 1 and started[0]["fields"]["kind"] == "local"
    assert len(finished) == 1
    assert finished[0]["fields"]["resolved"] == 2
    assert finished[0]["fields"]["new"] == 2
    assert finished[0]["fields"]["failed"] == 0
    store.close()


def test_sampler_start_stop_idempotent(tmp_path):
    observability.start()
    observability.start()
    thread = observability._thread
    assert thread is not None and thread.is_alive()
    observability.stop()
    observability.stop()
    assert thread.is_alive() is False


def test_cluster_manager_records_logs_dir_and_env():
    preexisting = ray.is_initialized()
    manager = ClusterManager(num_cpus=2)
    manager.ensure_started()
    try:
        # A foreign (reused) cluster never runs the init-time side effects.
        if not preexisting:
            assert os.environ.get("RAY_LOG_TO_DRIVER") == "0"
            assert manager.status()["logs_dir"] != ""
        assert manager.status()["metrics_port"] >= 1
    finally:
        manager.stop()


def test_metrics_agent_probe_goes_up_with_cluster(ray_runtime):
    """With Ray running, the metrics-agent probe must report 1."""
    observability._sample()
    text = _metrics_text()
    lines = _metric_lines(text, "llava_ray_metrics_up ")
    assert lines and float(lines[0].split()[-1]) == 1.0


def test_env_configuration(monkeypatch, tmp_path):
    monkeypatch.setenv("LLAVA_OBS_INTERVAL", "7")
    monkeypatch.setenv("LLAVA_OBS_DIR", str(tmp_path / "obs-env"))
    obs = Observability()
    assert obs._interval == 7.0
    obs.start()
    assert (tmp_path / "obs-env" / "events.jsonl").parent.is_dir()
    obs.stop()
