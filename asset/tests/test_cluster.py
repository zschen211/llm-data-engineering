"""ClusterManager lifecycle/ownership tests and the /api/cluster/status endpoint."""

import ray
from fastapi.testclient import TestClient

from asset_management.assets.api import AssetStore
from asset_management.assets.routes import create_app
from asset_management.assets.services.cluster import ClusterManager
from asset_management.assets.storage import LocalStorageBackend


def _make_store(tmp_path) -> AssetStore:
    backend = LocalStorageBackend(tmp_path / "blobs")
    return AssetStore(tmp_path / "assets.db", backend, tmp_dir=tmp_path / "tmp")


def test_manager_owns_what_it_starts():
    """A manager that initializes the cluster owns it and shuts it down."""
    preexisting = ray.is_initialized()
    manager = ClusterManager(num_cpus=2)
    manager.ensure_started()
    try:
        assert ray.is_initialized()
        status = manager.status()
        assert status["initialized"] is True
        if preexisting:
            assert status["total_cpus"] == ray.cluster_resources().get("CPU", 0)
        else:
            assert status["total_cpus"] == 2
        assert status["alive_nodes"] >= 1
    finally:
        manager.stop()
    if preexisting:
        assert ray.is_initialized()
    else:
        assert not ray.is_initialized()
        assert manager.status()["initialized"] is False


def test_manager_reuses_foreign_cluster(ray_runtime):
    """A cluster started by someone else is reused read-only, never stopped."""
    manager = ClusterManager(num_cpus=2)
    manager.ensure_started()
    manager.stop()
    assert ray.is_initialized()
    status = manager.status()
    assert status["initialized"] is True
    assert status["total_cpus"] >= 1


def test_env_configuration(monkeypatch):
    monkeypatch.setenv("ASSET_RAY_NUM_CPUS", "3")
    monkeypatch.setenv("RAY_ADDRESS", "ray://example:10001")
    manager = ClusterManager()
    assert manager._num_cpus == 3
    assert manager._address == "ray://example:10001"


def test_lifespan_bounds_cluster_lifetime(tmp_path):
    """The web app owns the cluster for its lifespan: started on startup,
    shut down on shutdown (unless someone else already owns it)."""
    preexisting = ray.is_initialized()
    store = _make_store(tmp_path)
    with TestClient(create_app(store)) as client:
        assert ray.is_initialized()
        status = client.get("/api/cluster/status").json()
        assert status["initialized"] is True
        assert status["total_cpus"] >= 1
        assert status["alive_nodes"] >= 1
    if preexisting:
        assert ray.is_initialized()
    else:
        assert not ray.is_initialized()
    store.close()


def test_status_endpoint(ray_runtime, tmp_path):
    store = _make_store(tmp_path)
    with TestClient(create_app(store)) as client:
        status = client.get("/api/cluster/status").json()
        assert status["initialized"] is True
        assert status["total_cpus"] >= 1
        assert status["alive_nodes"] >= 1
        assert isinstance(status["running_tasks"], int)
    store.close()
