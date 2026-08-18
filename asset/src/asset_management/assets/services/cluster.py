"""Single owner of the process-wide Ray cluster: attach and status.

Every ``ray.init``/``ray.shutdown`` call goes through the module-level
``cluster_manager`` singleton, so the cluster is attached exactly once per
process and reused by every sync (zero per-run init overhead).

Ownership semantics: ``ensure_started`` records whether the manager attached
the cluster itself. A cluster initialized by someone else (e.g. the pytest
``ray_runtime`` fixture) is reused read-only and never shut down by ``stop``.

Configuration (read at call time so env changes are honored):
  - ``RAY_ADDRESS``: required infra-contract variable (see
    ``infra/docs/contract.md``); the manager attaches to the standalone Ray
    cluster started by ``infra/scripts/ray-start.sh`` and raises a clear
    error when it is unset (the embedded local fallback was removed — one
    process, one cluster);
  - ``ASSET_RAY_NUM_CPUS``: CPU resources for an explicit ``address="local"``
    cluster (tests only; the app never starts clusters itself);
  - ``ASSET_RAY_METRICS_PORT``: fixed Prometheus metrics port of the shared
    cluster's metrics agent (default 8080, matches ``ray-start.sh``). Ray
    serves its native metrics from this agent, not from the dashboard port,
    so Prometheus can scrape a stable target;
  - ``RAY_ENABLE_UV_RUN_RUNTIME_ENV``: forced to 0 by the package import
    (see ``asset_management/__init__.py``), before ``ray`` is imported, so the
    uv-run hook never injects ``working_dir=<cwd>`` — the dashboard's
    subprocess modules would otherwise repackage the whole project root
    without ``excludes`` and blow up node memory. The ``excludes`` passed
    below only shrink the driver's own package; they are stripped from the
    serialized job config that child drivers (ServeHead etc.) inherit.

Observability: driver/worker logs stay in the Ray session dir (``logs_dir``
in ``status``); ``RAY_LOG_TO_DRIVER`` is forced to 0 so Ray never mixes its
output into uvicorn's stderr. ``status`` also reports the live
``metrics_port``.
"""

from __future__ import annotations

import os
from threading import Lock

import ray
from ray.util.state import list_actors, list_nodes, list_tasks

from ...log import get_logger

logger = get_logger("assets.cluster")

NUM_CPUS_ENV = "ASSET_RAY_NUM_CPUS"
ADDRESS_ENV = "RAY_ADDRESS"  # shared infra contract (see infra/docs/contract.md)
METRICS_PORT_ENV = "ASSET_RAY_METRICS_PORT"
METRICS_PORT_DEFAULT = 8080
# Ray streams worker stdout/stderr to the driver's stderr by default
# (RAY_LOG_TO_DRIVER=1); flip it so all Ray logs stay in the session log
# files and never mix into uvicorn's stderr.
RAY_LOG_TO_DRIVER_ENV = "RAY_LOG_TO_DRIVER"


class ClusterManager:
    """Idempotent, thread-safe wrapper around Ray cluster attachment."""

    def __init__(self, num_cpus: int | None = None, address: str | None = None):
        self._lock = Lock()
        self._owned = False
        self._num_cpus = num_cpus if num_cpus is not None else self._default_num_cpus()
        self._address = address if address is not None else ""
        self._metrics_port = self._env_int(METRICS_PORT_ENV, METRICS_PORT_DEFAULT)
        self._dashboard_url = ""
        self._gcs_address = ""
        self._logs_dir = ""

    @staticmethod
    def _env_int(name: str, default: int) -> int:
        try:
            return int(os.environ[name])
        except (KeyError, ValueError):
            return default

    @staticmethod
    def _default_num_cpus() -> int:
        override = os.environ.get(NUM_CPUS_ENV)
        if override:
            return max(1, int(override))
        return max(1, os.cpu_count() or 2)

    def ensure_started(self) -> None:
        """Attach to the shared cluster if it is not up yet; cheap no-op
        otherwise. Raises ValueError when ``RAY_ADDRESS`` is unset."""
        with self._lock:
            if ray.is_initialized():
                return
            # Resolved per call so a later env change (tests, restart) is
            # honored even though the default binds at construction.
            address = self._address or os.environ.get(ADDRESS_ENV, "")
            if not address:
                raise ValueError(
                    "RAY_ADDRESS is not set — the asset service requires the "
                    "shared Ray cluster (infra/scripts/ray-start.sh + export "
                    "RAY_ADDRESS); the embedded local fallback was removed"
                )
            self._owned = True
            os.environ.setdefault(RAY_LOG_TO_DRIVER_ENV, "0")
            local = address == "local"
            context = ray.init(
                address=address,
                ignore_reinit_error=True,
                runtime_env={"excludes": ["**"]},
                # Pinned metrics port only makes sense for the app's attach
                # path (it matches the shared cluster's agent); an explicit
                # test cluster ("local") picks a free port instead.
                **({"_metrics_export_port": self._metrics_port} if not local else {}),
                **({"num_cpus": self._num_cpus} if local else {}),
            )
            self._dashboard_url = context.dashboard_url or ""
            self._gcs_address = context.address_info.get("gcs_address", "") or ""
            self._logs_dir = self._read_session_logs_dir()

    @staticmethod
    def _read_session_logs_dir() -> str:
        """Path of the Ray session log directory (worker/driver logs)."""
        try:
            node = ray._private.worker.global_worker.node
            return node.get_logs_dir_path() or ""
        except Exception:
            return ""

    @staticmethod
    def _read_metrics_port() -> int:
        """Live Prometheus metrics port of the metrics agent (0 when down)."""
        try:
            node = ray._private.worker.global_worker.node
            return int(node.metrics_export_port) or 0
        except Exception:
            return 0

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
                "logs_dir": self._logs_dir,
                "metrics_port": self._metrics_port,
                "total_cpus": 0,
                "available_cpus": 0,
                "alive_nodes": 0,
                "running_tasks": 0,
                "alive_actors": 0,
            }
            if not ray.is_initialized():
                return base
            base["initialized"] = True
            base["metrics_port"] = self._read_metrics_port()
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
