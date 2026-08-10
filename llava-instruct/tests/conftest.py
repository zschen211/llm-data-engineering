"""Shared test fixtures for the asset layer."""

import os
from pathlib import Path

import pytest
import ray

TESTS_DIR = Path(__file__).parent


@pytest.fixture(scope="session")
def ray_runtime():
    """Session-scoped local Ray cluster for sync tests.

    Ray tasks run in separate processes, so sync tests must not rely on
    monkeypatching; fake hubs are injected via ``hub=`` (cloudpickle ships the
    class definition to the workers, which need the tests dir on PYTHONPATH).
    The working dir is excluded from the runtime-env package (code is
    pip-installed).
    """
    cpus = min(4, max(1, os.cpu_count() or 2))
    ray.init(
        num_cpus=cpus,
        ignore_reinit_error=True,
        log_to_driver=False,
        runtime_env={
            "excludes": ["**"],
            "env_vars": {"PYTHONPATH": str(TESTS_DIR.resolve())},
        },
    )
    yield ray
    ray.shutdown()
