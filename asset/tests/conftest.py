"""Shared test fixtures for the asset layer."""

import os
from pathlib import Path

import pytest
import ray

TESTS_DIR = Path(__file__).parent

# Tests never attach the shared Ray cluster (infra contract): Ray's
# address=None auto-detects a running cluster via /tmp/ray/ray_current_cluster,
# so scrub RAY_ADDRESS and use address="local" (forces a fresh local cluster).
os.environ.pop("RAY_ADDRESS", None)


@pytest.fixture(scope="session", autouse=True)
def _test_log_dir(tmp_path_factory):
    """Route persisted logs to a session temp dir: tests never write into the
    repo, and every test that initializes logging exercises the file path."""
    os.environ["ASSET_LOG_DIR"] = str(tmp_path_factory.mktemp("logs"))


@pytest.fixture(scope="session")
def ray_runtime():
    """Session-scoped local Ray cluster for sync tests.

    Ray tasks run in separate processes, so sync tests must not rely on
    monkeypatching; fake hubs are injected via ``hub=`` (cloudpickle ships the
    class definition to the workers, which need the tests dir on PYTHONPATH).
    The working dir is excluded from the runtime-env package (code is
    pip-installed). ``_temp_dir`` forces a private cluster even when the
    shared one is running.
    """
    cpus = min(4, max(1, os.cpu_count() or 2))
    ray.init(
        num_cpus=cpus,
        address="local",
        ignore_reinit_error=True,
        log_to_driver=False,
        runtime_env={
            "excludes": ["**"],
            "env_vars": {"PYTHONPATH": str(TESTS_DIR.resolve())},
        },
    )
    yield ray
    ray.shutdown()
