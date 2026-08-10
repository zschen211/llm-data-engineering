"""Async file-log persistence tests (bounded queue, sync fallback, reset)."""

from __future__ import annotations

import gzip
import io
import logging
import queue
import re
import time

from llava_instruct.assets.api import AssetStore
from llava_instruct.assets.routes import create_app
from llava_instruct.assets.storage import LocalStorageBackend
from llava_instruct.log import _NeverDropQueueHandler, get_logger, setup_logging


def _wait_for(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def test_async_file_persistence(tmp_path):
    setup_logging(level=logging.INFO, stream=io.StringIO(), log_dir=tmp_path)
    get_logger("test.persist").error("explode %d", 42)

    path = tmp_path / "llava-instruct.log"
    assert _wait_for(path.exists)
    assert _wait_for(lambda: "explode 42" in path.read_text())
    content = path.read_text()
    assert "[llava-instruct]" in content
    assert "test_log_persistence.py" in content
    assert "test_async_file_persistence" in content


def test_exception_traceback_persisted(tmp_path):
    setup_logging(level=logging.INFO, stream=io.StringIO(), log_dir=tmp_path)
    try:
        raise ValueError("boom")
    except ValueError:
        get_logger("test.persist").exception("task failed")

    path = tmp_path / "llava-instruct.log"
    assert _wait_for(lambda: path.exists() and "Traceback" in path.read_text())
    assert "ValueError: boom" in path.read_text()


def test_queue_full_falls_back_to_sync_write():
    buf = io.StringIO()
    fallback = logging.StreamHandler(buf)
    fallback.setFormatter(logging.Formatter("%(message)s"))
    queue_handler = _NeverDropQueueHandler(queue.Queue(1), fallback)

    def emit(msg):
        queue_handler.emit(logging.makeLogRecord({"msg": msg, "levelno": logging.INFO}))

    emit("first")
    emit("second")
    emit("third")
    # the queued record was never drained; the rest went straight to disk
    assert "second" in buf.getvalue()
    assert "third" in buf.getvalue()


def test_env_log_level(monkeypatch, tmp_path):
    monkeypatch.setenv("LLAVA_LOG_LEVEL", "WARNING")
    buf = io.StringIO()
    setup_logging(stream=buf, log_dir=tmp_path)
    logger = get_logger("test.level")
    logger.info("dropped")
    logger.warning("kept")
    assert "kept" in buf.getvalue()
    assert "dropped" not in buf.getvalue()


def test_reset_drains_previous_listener(tmp_path, tmp_path_factory):
    other_dir = tmp_path_factory.mktemp("other-logs")
    setup_logging(level=logging.INFO, stream=io.StringIO(), log_dir=other_dir)
    get_logger("test.persist").warning("before reset")
    assert _wait_for((other_dir / "llava-instruct.log").exists)

    setup_logging(level=logging.INFO, stream=io.StringIO())  # file persistence off
    get_logger("test.persist").warning("after reset")
    assert _wait_for(
        lambda: "before reset" in (other_dir / "llava-instruct.log").read_text()
    )
    time.sleep(0.3)
    assert "after reset" not in (other_dir / "llava-instruct.log").read_text()


def test_create_app_configures_logging(tmp_path, monkeypatch):
    log_dir = tmp_path / "app-logs"
    monkeypatch.setenv("LLAVA_LOG_DIR", str(log_dir))
    store = AssetStore(
        tmp_path / "assets.db",
        LocalStorageBackend(tmp_path / "blobs"),
        tmp_dir=tmp_path / "tmp",
    )
    create_app(store)
    get_logger("assets.api").error("app wired")

    path = log_dir / "llava-instruct.log"
    assert _wait_for(lambda: path.exists() and "app wired" in path.read_text())


def test_rotation_compresses_backups(tmp_path, monkeypatch):
    monkeypatch.setenv("LLAVA_LOG_MAX_BYTES", "300")
    monkeypatch.setenv("LLAVA_LOG_BACKUPS", "3")
    setup_logging(level=logging.INFO, stream=io.StringIO(), log_dir=tmp_path)
    logger = get_logger("test.rotate")
    for i in range(60):
        logger.error("rot %04d %s", i, "x" * 50)

    path = tmp_path / "llava-instruct.log"

    def backups():
        return sorted(tmp_path.glob("llava-instruct.log.*.gz"))

    assert _wait_for(lambda: len(backups()) == 3)
    # the last record triggers the final rollover and lands in the active file
    assert _wait_for(lambda: "rot 0059" in path.read_text())

    def ids(content):
        return [int(m) for m in re.findall(r"rot (\d{4})", content)]

    # backups are valid gzip holding real records
    contents = []
    for backup in backups():
        with gzip.open(backup, "rt") as f:
            contents.append(f.read())
    assert all("rot 00" in c for c in contents)
    # chain order: .1.gz is the newest chunk, .3.gz the oldest retained
    assert min(ids(contents[0])) > max(ids(contents[-1]))
    # the active tail is never compressed away
    assert "rot 0059" in path.read_text() and "rot 0059" not in contents[0]
