"""Single owner of the process-wide Ray cluster: lifecycle and status.

Every ``ray.init``/``ray.shutdown`` call goes through the module-level
``cluster_manager`` singleton, so the cluster is started exactly once per
process and reused by every sync (zero per-run init overhead).

Ownership semantics: ``ensure_started`` records whether the manager started
the cluster itself. A cluster initialized by someone else (e.g. the pytest
``ray_runtime`` fixture) is reused read-only and never shut down by
``stop``.

Configuration (read once at construction):
  - ``LLAVA_RAY_NUM_CPUS``: CPU resources for the local cluster
    (default: all cores);
  - ``LLAVA_RAY_ADDRESS``: attach to an existing Ray cluster instead of
    starting a local one (the web app then manages nothing, only monitors).
"""

from __future__ import annotations

import os
from threading import Lock

import ray
from ray.util.state import list_actors, list_nodes, list_tasks

NUM_CPUS_ENV = "LLAVA_RAY_NUM_CPUS"
ADDRESS_ENV = "LLAVA_RAY_ADDRESS"


class ClusterManager:
    """Idempotent, thread-safe wrapper around the Ray cluster lifecycle."""

    def __init__(self, num_cpus: int | None = None, address: str | None = None):
        self._lock = Lock()
        self._owned = False
        self._num_cpus = num_cpus if num_cpus is not None else self._default_num_cpus()
        self._address = (
            address if address is not None else os.environ.get(ADDRESS_ENV, "")
        )
        self._dashboard_url = ""
        self._gcs_address = ""

    @staticmethod
    def _default_num_cpus() -> int:
        override = os.environ.get(NUM_CPUS_ENV)
        if override:
            return max(1, int(override))
        return max(1, os.cpu_count() or 2)

    def ensure_started(self) -> None:
        """Start the cluster if it is not up yet; cheap no-op otherwise."""
        with self._lock:
            if ray.is_initialized():
                return
            self._owned = True
            context = ray.init(
                num_cpus=self._num_cpus,
                address=self._address or None,
                ignore_reinit_error=True,
                runtime_env={"excludes": ["**"]},
            )
            self._dashboard_url = context.dashboard_url or ""
            self._gcs_address = context.address_info.get("gcs_address", "") or ""

    def stop(self) -> None:
        """Shut down the cluster only if this manager started it."""
        with self._lock:
            if self._owned and ray.is_initialized():
                ray.shutdown()
            self._owned = False

    def status(self) -> dict:
        """Current cluster state, safe to call at any time (even while down)."""
        with self._lock:
            base = {
                "initialized": False,
                "address": self._gcs_address,
                "dashboard_url": self._dashboard_url,
                "total_cpus": 0,
                "available_cpus": 0,
                "alive_nodes": 0,
                "running_tasks": 0,
                "alive_actors": 0,
            }
            if not ray.is_initialized():
                return base
            base["initialized"] = True
            base["total_cpus"] = ray.cluster_resources().get("CPU", 0)
            base["available_cpus"] = ray.available_resources().get("CPU", 0)
            try:
                nodes = list_nodes(limit=100)
                base["alive_nodes"] = sum(1 for node in nodes if node.state == "ALIVE")
                base["running_tasks"] = len(
                    list_tasks(filters=[("state", "=", "RUNNING")], limit=1000)
                )
                base["alive_actors"] = len(
                    list_actors(filters=[("state", "=", "ALIVE")], limit=1000)
                )
            except Exception:
                # State API needs the dashboard; fall back to the legacy
                # node table so the status stays useful without it.
                base["alive_nodes"] = sum(1 for node in ray.nodes() if node["Alive"])
            return base


cluster_manager = ClusterManager()
