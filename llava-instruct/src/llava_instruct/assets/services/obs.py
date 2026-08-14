"""Observability for the llava-instruct service: metrics, sampling, events.

Three zero-infrastructure layers, owned by the web-app lifespan:

- Prometheus metrics (``prometheus-client``): process gauges (psutil), Ray
  cluster / raylet / worker gauges, HTTP request counters and a task
  histogram. Exposed at ``/metrics`` for scraping by a Prometheus instance
  (docker-compose provides one). Metric names carry the ``llava_`` prefix so
  a scrape of this endpoint never collides with the Ray metrics-agent scrape
  (fixed port ``LLAVA_RAY_METRICS_PORT``, default 8080).
- JSON event stream: structured events (sync runs, Ray cluster lifecycle,
  per-file task results) appended to ``events.jsonl`` in the log dir, with
  the same gzip rotation as ``log.py``'s text log.
- A daemon sampler thread refreshes the gauges every ``$LLAVA_OBS_INTERVAL``
  seconds (default 5). It reuses ``cluster_manager.status()`` instead of
  touching Ray internals itself, so it stays correct for an attached
  external cluster too (no raylet/worker children -> zero gauges).

The sampler must never raise: a metrics glitch is not allowed to take the
web app down, so every collection path is guarded and the thread swallows
collection errors. ``start``/``stop`` are idempotent; ``reset`` exists for
tests to rebuild the singleton with a fresh sink and registry.
"""

from __future__ import annotations

import gzip
import http.client
import json
import logging
import logging.handlers
import os
import shutil
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import psutil
from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    make_asgi_app,
)
from starlette.middleware.base import BaseHTTPMiddleware

from .cluster import cluster_manager

_AUTO = object()  # sentinel: resolve the event dir from the environment
_DIR_ENV = "LLAVA_OBS_DIR"
_INTERVAL_ENV = "LLAVA_OBS_INTERVAL"
_MAX_BYTES_ENV = "LLAVA_OBS_MAX_BYTES"
_BACKUPS_ENV = "LLAVA_OBS_BACKUPS"
_EVENTS_FILE = "events.jsonl"
_MAX_BYTES = 50 * 1024 * 1024
_BACKUP_COUNT = 5
_TASK_BUCKETS = (0.1, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300)
_STAGE_BUCKETS = (1, 5, 10, 30, 60, 120, 300, 600, 1800, 3600)

_event_logger = logging.getLogger("llava_instruct.obs")


class _GzipRotator:
    """Compress the rotated file into ``dest`` (``events.jsonl.N.gz``)."""

    def __call__(self, source: str, dest: str) -> None:
        with open(source, "rb") as src, gzip.open(dest, "wb") as dst:
            shutil.copyfileobj(src, dst)
        os.remove(source)


class _GzipRotatingFileHandler(logging.handlers.RotatingFileHandler):
    """RotatingFileHandler whose backups are gzip-compressed; mirrors the
    handler in ``log.py`` (both stay private to their module)."""

    def rotation_filename(self, default_name: str) -> str:
        if default_name == self.baseFilename:
            return default_name
        return default_name + ".gz"

    def __init__(self, filename, max_bytes: int, backup_count: int):
        super().__init__(
            filename,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        self.rotator = _GzipRotator()


class EventSink:
    """Append JSON-lines events to ``events.jsonl`` under a lock.

    One line per event; ``default=str`` keeps non-JSON field values (Path,
    datetime, exceptions) out of the failure path.
    """

    def __init__(self, log_dir: Path | None):
        self._handler: logging.Handler | None = None
        if log_dir is not None:
            log_dir.mkdir(parents=True, exist_ok=True)
            handler = _GzipRotatingFileHandler(
                log_dir / _EVENTS_FILE,
                max_bytes=_env_int(_MAX_BYTES_ENV, _MAX_BYTES),
                backup_count=_env_int(_BACKUPS_ENV, _BACKUP_COUNT),
            )
            handler.setFormatter(logging.Formatter("%(message)s"))
            self._handler = handler

    def write(self, name: str, level: str, fields: dict) -> None:
        if self._handler is None:
            return
        record = {
            "ts": datetime.now(UTC).isoformat(timespec="seconds"),
            "project": "llava-instruct",
            "pid": os.getpid(),
            "event": name,
            "level": level,
            "fields": fields,
        }
        line = json.dumps(record, ensure_ascii=False, default=str)
        log = logging.LogRecord(
            "llava_instruct.obs", logging.INFO, __file__, 0, line, None, None
        )
        self._handler.emit(log)

    def close(self) -> None:
        if self._handler is not None:
            self._handler.close()
            self._handler = None


class _Sampler(threading.Thread):
    """Daemon thread re-running ``collect`` every ``interval`` seconds."""

    def __init__(self, interval: float, collect):
        super().__init__(name="obs-sampler", daemon=True)
        self._interval = interval
        self._collect = collect
        self._stop_event = threading.Event()

    def run(self) -> None:
        while not self._stop_event.wait(self._interval):
            try:
                self._collect()
            except Exception as exc:
                # A sampler must never take the app down; log and continue.
                _event_logger.warning("obs sampler error: %s", exc)

    def stop(self) -> None:
        self._stop_event.set()
        self.join(timeout=5)


class MetricsMiddleware(BaseHTTPMiddleware):
    """Starlette middleware recording request count and latency."""

    def __init__(self, app, obs=None):
        super().__init__(app)
        self._obs = obs or observability

    async def dispatch(self, request, call_next):
        start = time.perf_counter()
        response = await call_next(request)
        route = getattr(request.scope.get("route"), "path", None) or request.url.path
        self._obs.observe_http(
            request.method, route, response.status_code, time.perf_counter() - start
        )
        return response


class Observability:
    """Process-wide metrics registry, sampler and event sink."""

    def __init__(self, log_dir: object = _AUTO, interval: float | None = None):
        self._lock = threading.Lock()
        self._dir = log_dir
        self._interval = max(1.0, interval or _env_float(_INTERVAL_ENV, 5.0))
        self._thread: _Sampler | None = None
        self._sink = EventSink(None)
        self._ray_procs: dict[int, psutil.Process] = {}
        self._build_metrics()

    def _build_metrics(self) -> None:
        self._registry = CollectorRegistry()
        gauges = {
            "llava_process_cpu_percent": "CPU usage of the uvicorn process, percent of one core",
            "llava_process_rss_bytes": "Resident set size of the uvicorn process",
            "llava_process_vms_bytes": "Virtual memory size of the uvicorn process",
            "llava_process_threads": "Thread count of the uvicorn process",
            "llava_process_open_fds": "Open file descriptors of the uvicorn process",
            "llava_process_children": "Child processes of the uvicorn process (raylet, workers)",
            "llava_ray_total_cpus": "CPU resources of the Ray cluster",
            "llava_ray_available_cpus": "CPU resources currently free in the Ray cluster",
            "llava_ray_alive_nodes": "Alive nodes in the Ray cluster",
            "llava_ray_running_tasks": "Tasks currently RUNNING in the Ray cluster",
            "llava_ray_alive_actors": "Actors currently ALIVE in the Ray cluster",
            "llava_raylet_cpu_percent": "CPU usage of the raylet process",
            "llava_raylet_rss_bytes": "Resident set size of the raylet process",
            "llava_ray_workers_total": "Number of live Ray worker processes",
            "llava_ray_workers_cpu_percent": "Aggregated CPU usage of Ray worker processes",
            "llava_ray_workers_rss_bytes": "Aggregated RSS of Ray worker processes",
            "llava_ray_metrics_up": "1 when the Ray metrics agent /metrics endpoint responds",
            "llava_ray_session_logs_bytes": "Total size of the Ray session log directory",
        }
        self._gauges = {
            name: Gauge(name, doc, registry=self._registry)
            for name, doc in gauges.items()
        }
        self._http_requests = Counter(
            "llava_http_requests_total",
            "HTTP requests handled by the web app",
            ["method", "route", "status"],
            registry=self._registry,
        )
        self._http_duration = Histogram(
            "llava_http_request_duration_seconds",
            "HTTP request handling latency",
            ["method", "route"],
            registry=self._registry,
        )
        self._stage_duration = Histogram(
            "llava_sync_stage_duration_seconds",
            "Wall time of one sync stage per run",
            ["run_id", "stage"],
            buckets=_STAGE_BUCKETS,
            registry=self._registry,
        )
        self._item_duration = Histogram(
            "llava_sync_item_duration_seconds",
            "Wall time of one item (file/asset) within a sync stage",
            ["run_id", "stage"],
            buckets=_TASK_BUCKETS,
            registry=self._registry,
        )
        self._sync_items = Counter(
            "llava_sync_items_total",
            "Items finished per sync stage (files for download_raw/process, "
            "assets for persist)",
            ["run_id", "stage", "status"],
            registry=self._registry,
        )
        self._sync_failures = Counter(
            "llava_sync_failures_total",
            "Sync tasks that failed (including retry exhaustion)",
            ["run_id", "stage"],
            registry=self._registry,
        )
        self._sync_retries = Counter(
            "llava_sync_retries_total",
            "Retries observed per sync stage: app-level backoff attempts or "
            "Ray task restarts",
            ["run_id", "stage", "kind"],
            registry=self._registry,
        )

    def metrics_app(self):
        """ASGI app serving the Prometheus text exposition."""
        return make_asgi_app(self._registry)

    def start(self) -> None:
        """Start sampling and enable the event stream; idempotent."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._sink = EventSink(_resolve_log_dir(self._dir))
            self._thread = _Sampler(self._interval, self._sample)
            self._thread.start()

    def stop(self) -> None:
        """Stop sampling and close the event sink; idempotent."""
        thread = self._thread
        self._thread = None
        if thread is not None:
            thread.stop()
        with self._lock:
            self._sink.close()

    def reset(self, log_dir: object = None, interval: float | None = None) -> None:
        """Test support: stop everything and rebuild with a fresh state."""
        self.stop()
        with self._lock:
            self._dir = log_dir
            if interval is not None:
                self._interval = max(1.0, interval)
            self._thread = None
            self._sink = EventSink(None)
            self._ray_procs = {}
            self._build_metrics()

    def event(self, name: str, level: str = "info", **fields) -> None:
        """Append one structured event to the stream (no-op before start)."""
        self._sink.write(name, level, fields)

    def observe_http(
        self, method: str, route: str, status: int, seconds: float
    ) -> None:
        self._http_requests.labels(method=method, route=route, status=str(status)).inc()
        self._http_duration.labels(method=method, route=route).observe(seconds)

    def stage_finished(
        self,
        run_id: str,
        stage: str,
        duration_s: float,
        item_count: int = 0,
        failed_count: int = 0,
        retry_app: int = 0,
        retry_ray: int = 0,
    ) -> None:
        """Record one run's stage wall time; item/retry counters land in
        ``item_finished``/``retry`` as items stream in."""
        self._stage_duration.labels(run_id=run_id, stage=stage).observe(duration_s)
        if failed_count:
            self._sync_failures.labels(run_id=run_id, stage=stage).inc(failed_count)
        if retry_app:
            self._sync_retries.labels(run_id=run_id, stage=stage, kind="app").inc(
                retry_app
            )
        if retry_ray:
            self._sync_retries.labels(run_id=run_id, stage=stage, kind="ray").inc(
                retry_ray
            )
        self.event(
            "sync_stage_finished",
            run_id=run_id,
            stage=stage,
            duration_s=round(duration_s, 3),
            item_count=item_count,
            failed_count=failed_count,
            retry_app=retry_app,
            retry_ray=retry_ray,
        )

    def item_finished(
        self, run_id: str, stage: str, status: str, duration_s: float
    ) -> None:
        """One item (file/asset) finished within a stage."""
        self._sync_items.labels(run_id=run_id, stage=stage, status=status).inc()
        self._item_duration.labels(run_id=run_id, stage=stage).observe(duration_s)

    def _sample(self) -> None:
        self._sample_process()
        self._sample_ray()

    def _sample_process(self) -> None:
        try:
            proc = psutil.Process()
            self._gauges["llava_process_cpu_percent"].set(proc.cpu_percent(None))
            memory = proc.memory_info()
            self._gauges["llava_process_rss_bytes"].set(memory.rss)
            self._gauges["llava_process_vms_bytes"].set(memory.vms)
            self._gauges["llava_process_threads"].set(proc.num_threads())
            self._gauges["llava_process_open_fds"].set(len(proc.open_files()))
            self._gauges["llava_process_children"].set(
                len(proc.children(recursive=True))
            )
        except psutil.Error:
            pass

    def _sample_ray(self) -> None:
        status = cluster_manager.status()
        prefix = "llava_ray_"
        for name in (
            "llava_ray_total_cpus",
            "llava_ray_available_cpus",
            "llava_ray_alive_nodes",
            "llava_ray_running_tasks",
            "llava_ray_alive_actors",
        ):
            self._gauges[name].set(status[name[len(prefix) :]])
        self._gauges["llava_ray_metrics_up"].set(
            _metrics_reachable(status.get("metrics_port", 0))
        )
        self._gauges["llava_ray_session_logs_bytes"].set(
            _dir_bytes(status.get("logs_dir", ""))
        )
        raylet, workers = self._ray_process_stats()
        self._gauges["llava_raylet_cpu_percent"].set(sum(_cpu(p) for p in raylet))
        self._gauges["llava_raylet_rss_bytes"].set(sum(_rss(p) for p in raylet))
        self._gauges["llava_ray_workers_total"].set(len(workers))
        self._gauges["llava_ray_workers_cpu_percent"].set(sum(_cpu(p) for p in workers))
        self._gauges["llava_ray_workers_rss_bytes"].set(sum(_rss(p) for p in workers))

    def _ray_process_stats(self) -> tuple[list[psutil.Process], list[psutil.Process]]:
        """Locate the raylet and its workers in the uvicorn child tree.

        Process objects are cached per pid so ``cpu_percent`` keeps reporting
        deltas between samples instead of 0.0 (its first-call value).
        """
        try:
            children = psutil.Process().children(recursive=True)
        except psutil.Error:
            return [], []
        fresh: dict[int, psutil.Process] = {}
        raylet: list[psutil.Process] = []
        workers: list[psutil.Process] = []
        for child in children:
            try:
                cmdline = " ".join(child.cmdline())
            except psutil.Error:
                continue
            cached = self._ray_procs.get(child.pid, child)
            if "raylet" in cmdline:
                raylet.append(cached)
            elif "DefaultWorker" in cmdline:
                workers.append(cached)
            fresh[child.pid] = cached
        self._ray_procs = fresh
        return raylet, workers


_singleton: Observability


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


def _resolve_log_dir(log_dir: object) -> Path | None:
    if log_dir is _AUTO:
        env = os.environ.get(_DIR_ENV)
        log_dir = env if env else os.environ.get("LLAVA_LOG_DIR", "")
        if not log_dir:
            log_dir = str(Path(os.environ.get("LLAVA_DATA_DIR", "data")) / "logs")
    return Path(log_dir) if log_dir is not None else None


def _cpu(proc: psutil.Process) -> float:
    try:
        return proc.cpu_percent(None)
    except psutil.Error:
        return 0.0


def _rss(proc: psutil.Process) -> int:
    try:
        return proc.memory_info().rss
    except psutil.Error:
        return 0


def _dir_bytes(path: str) -> float:
    try:
        root = Path(path)
        if not root.is_dir():
            return 0.0
        return float(sum(p.stat().st_size for p in root.rglob("*") if p.is_file()))
    except OSError:
        return 0.0


def _metrics_reachable(port: int) -> float:
    """Probe the Ray metrics agent /metrics endpoint; 1.0 when it responds."""
    try:
        conn = http.client.HTTPConnection("127.0.0.1", int(port), timeout=1)
    except (ValueError, OSError):
        return 0.0
    try:
        conn.request("GET", "/metrics")
        return 1.0 if conn.getresponse().status < 500 else 0.0
    except OSError:
        return 0.0
    finally:
        conn.close()


observability = Observability()
