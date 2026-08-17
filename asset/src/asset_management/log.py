"""Unified logging for asset.

Every log line uses one format that identifies the emitting file, line and
function:

    2026-08-10 10:00:00 | INFO    | [asset] | store.py:142:sync_source | message

Entry points (CLI main, web app, scripts) call ``setup_logging`` once; modules
use ``get_logger(__name__)``.

Persistence: ``setup_logging`` routes records through a small bounded queue
that a background thread drains into ``asset.log`` inside the log
dir. The queue keeps slow disk I/O off the hot path. It is deliberately
bounded: when the process is under memory pressure (an OOM is usually
preceded by a log burst) a full queue falls back to a synchronous write
instead of dropping records, so the last moments before the kill are on
disk. On normal exit (SIGTERM/SIGINT) the listener flushes the remaining
queue via atexit; a SIGKILL (OOM) can only lose the records still queued at
that instant, which is why the queue stays small.

Rolling: size-based rotation (``$ASSET_LOG_MAX_BYTES``, default 50 MB) with
``$ASSET_LOG_BACKUPS`` compressed backups (default 5, gzip: ``.log.N.gz``,
newest is ``.log.1.gz``). Compressed on rotation, so the active file stays
plain for tail -f.

Log dir resolution: ``$ASSET_LOG_DIR``, else ``$ASSET_DATA_DIR/logs``, else
``data/logs`` relative to the working directory; ``setup_logging(log_dir=None)``
disables file persistence (used by tests). ``ASSET_LOG_LEVEL`` overrides the
default level (INFO).
"""

from __future__ import annotations

import atexit
import copy
import gzip
import logging
import logging.handlers
import os
import queue
import shutil
import sys
from pathlib import Path

_FORMAT = "%(asctime)s | %(levelname)-7s | [asset] | %(filename)s:%(lineno)d:%(funcName)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_AUTO = object()  # sentinel: resolve the log dir from the environment

_MAX_QUEUE = 1024  # bounded: keeps the OOM-loss window small
_MAX_BYTES = 50 * 1024 * 1024
_BACKUP_COUNT = 5

_listener: logging.handlers.QueueListener | None = None
_file_handler: logging.Handler | None = None


class _NeverDropQueueHandler(logging.handlers.QueueHandler):
    """QueueHandler that falls back to a synchronous file write when the
    queue is full, so no record is dropped during a log burst."""

    def __init__(self, q: queue.Queue, fallback: logging.Handler):
        super().__init__(q)
        self._fallback = fallback

    def prepare(self, record: logging.LogRecord) -> logging.LogRecord:
        # Like the base, but keeps exc_info so tracebacks are rendered by the
        # listener's file handler instead of being stripped for pickling.
        msg = self.format(record)
        record = copy.copy(record)
        record.message = msg
        record.msg = msg
        record.args = None
        return record

    def enqueue(self, record: logging.LogRecord) -> None:
        try:
            self.queue.put_nowait(record)
        except queue.Full:
            self._fallback.handle(record)


class _GzipRotator:
    """Compress the rotated file into ``dest`` (``log.N.gz``) and remove it."""

    def __call__(self, source: str, dest: str) -> None:
        with open(source, "rb") as src, gzip.open(dest, "wb") as dst:
            shutil.copyfileobj(src, dst)
        os.remove(source)


class _GzipRotatingFileHandler(logging.handlers.RotatingFileHandler):
    """RotatingFileHandler whose backups are gzip-compressed.

    ``rotation_filename`` maps ``log.N`` to ``log.N.gz`` so the rename chain
    keeps working on the compressed files; ``rotator`` does the actual gzip.
    """

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


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


def _resolve_log_dir(log_dir: object) -> Path | None:
    if log_dir is _AUTO:
        env = os.environ.get("ASSET_LOG_DIR")
        log_dir = (
            Path(env)
            if env
            else Path(os.environ.get("ASSET_DATA_DIR", "data")) / "logs"
        )
    return Path(log_dir) if log_dir is not None else None


def _console_handler(stream) -> logging.Handler:
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(logging.Formatter(_FORMAT, datefmt=_DATE_FORMAT))
    return handler


def _file_handler_for(log_dir: Path) -> logging.Handler:
    log_dir.mkdir(parents=True, exist_ok=True)
    return _GzipRotatingFileHandler(
        log_dir / "asset.log",
        max_bytes=_env_int("ASSET_LOG_MAX_BYTES", _MAX_BYTES),
        backup_count=_env_int("ASSET_LOG_BACKUPS", _BACKUP_COUNT),
    )


def setup_logging(
    level: int | str | None = None,
    stream=None,
    log_dir: object = _AUTO,
) -> logging.Logger:
    """Configure the asset logger namespace once.

    ``level``: logging level; defaults to ``$ASSET_LOG_LEVEL`` or INFO.
    ``stream``: console sink (default stderr); tests pass a StringIO.
    ``log_dir``: log directory; defaults to ``$ASSET_LOG_DIR`` else
    ``$ASSET_DATA_DIR/logs`` else ``data/logs``; pass None to disable file
    persistence.
    """
    global _listener, _file_handler
    logger = logging.getLogger("asset_management")
    logger.setLevel(level or os.environ.get("ASSET_LOG_LEVEL", "INFO"))
    logger.propagate = False
    if _listener is not None:
        _listener.stop()
        _listener = None
    _file_handler = None
    logger.handlers.clear()
    logger.addHandler(_console_handler(stream))
    target = _resolve_log_dir(log_dir)
    if target is not None:
        file_handler = _file_handler_for(target)
        queue_handler = _NeverDropQueueHandler(queue.Queue(_MAX_QUEUE), file_handler)
        queue_handler.setFormatter(logging.Formatter(_FORMAT, datefmt=_DATE_FORMAT))
        logger.addHandler(queue_handler)
        _file_handler = file_handler
        _listener = logging.handlers.QueueListener(queue_handler.queue, file_handler)
        _listener.start()
    return logger


def persist_uvicorn_logs() -> None:
    """Attach the file handler to uvicorn's own loggers so access/error logs
    land in the same rotating file.

    Uvicorn reconfigures logging at startup, so call this from the FastAPI
    lifespan (which runs afterwards) rather than at app import time.
    """
    if _file_handler is None:
        return
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logging.getLogger(name).addHandler(_file_handler)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"asset_management.{name}")


def _shutdown() -> None:
    if _listener is not None:
        _listener.stop()


atexit.register(_shutdown)
