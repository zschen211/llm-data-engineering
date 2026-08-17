"""JSONL row artifacts: write/read dict rows to/from a storage backend.

Rows are JSON-serializable dicts (the pipeline row contract). Files are
staged locally then uploaded so the backend only ever sees complete objects.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
from collections.abc import Iterable
from pathlib import Path

from .storage.base import StorageBackend, content_key_for

DEFAULT_EXT = ".jsonl"


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _stage_rows(
    rows: Iterable[dict], tmp_dir: Path | None, suffix: str
) -> tuple[Path, int]:
    """Write rows to a temp JSONL file; return (path, row_count)."""
    fd, tmp_name = tempfile.mkstemp(prefix="dfac-", suffix=suffix, dir=tmp_dir or None)
    count = 0
    with open(fd, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            count += 1
    return Path(tmp_name), count


def write_rows(
    backend: StorageBackend,
    key: str,
    rows: Iterable[dict],
    tmp_dir: Path | None = None,
) -> dict:
    """Write rows as JSONL to ``key``; return {key, sha256, size, row_count}.

    Path-addressed (intermediate) artifacts use this; content-addressed
    results use ``write_rows_ca``.
    """
    tmp, count = _stage_rows(rows, tmp_dir, ".jsonl")
    try:
        sha256 = sha256_of(tmp)
        size = tmp.stat().st_size
        backend.put_object(key, tmp)
    finally:
        tmp.unlink(missing_ok=True)
    return {"key": key, "sha256": sha256, "size": size, "row_count": count}


def write_rows_ca(
    backend: StorageBackend,
    rows: Iterable[dict],
    tmp_dir: Path | None = None,
    ext: str = DEFAULT_EXT,
) -> dict:
    """Write rows as JSONL at their content-addressed key (dedup)."""
    tmp, count = _stage_rows(rows, tmp_dir, ext)
    try:
        sha256 = sha256_of(tmp)
        size = tmp.stat().st_size
        key = content_key_for(sha256, ext)
        backend.put_file(tmp, sha256, ext)
    finally:
        tmp.unlink(missing_ok=True)
    return {"key": key, "sha256": sha256, "size": size, "row_count": count}


def read_rows_from_path(path: Path) -> list[dict]:
    """Parse a local JSONL file into rows."""
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _mk_temp(tmp_dir: Path | None, suffix: str) -> Path:
    """Reserve a temp file path (caller removes it)."""
    _fd, tmp_name = tempfile.mkstemp(prefix="dfac-", suffix=suffix, dir=tmp_dir or None)
    return Path(tmp_name)


def read_rows(
    backend: StorageBackend, key: str, tmp_dir: Path | None = None
) -> list[dict]:
    """Fetch a JSONL object and parse it into rows."""
    tmp_name = _mk_temp(tmp_dir, ".jsonl")
    try:
        backend.get_file(key, tmp_name)
        return read_rows_from_path(tmp_name)
    finally:
        tmp_name.unlink(missing_ok=True)


def write_manifest(backend: StorageBackend, key: str, payload: dict) -> str:
    """Write a JSON manifest object; return its key."""
    tmp_name = _mk_temp(None, ".json")
    try:
        tmp_name.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        backend.put_object(key, tmp_name)
    finally:
        tmp_name.unlink(missing_ok=True)
    return key


def read_manifest(backend: StorageBackend, key: str) -> dict:
    return read_object(backend, key)


def read_object(backend: StorageBackend, key: str) -> dict:
    """Fetch a JSON object (manifest/report) from the backend."""
    tmp_name = _mk_temp(None, ".json")
    try:
        backend.get_file(key, tmp_name)
        return json.loads(tmp_name.read_text(encoding="utf-8"))
    finally:
        tmp_name.unlink(missing_ok=True)
