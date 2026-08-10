"""Resumable, sharded JSONL I/O shared by all pipeline stages."""

from __future__ import annotations

import json
from pathlib import Path


def read_jsonl(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


class SafeJsonlWriter:
    """Append-only JSONL writer that never corrupts existing records.

    Must be used as a context manager (``with SafeJsonlWriter(path) as w``);
    the file is opened on ``__enter__``.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = None  # opened in __enter__ (context-manager contract)

    def append(self, record: dict) -> None:
        self._fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._fh.flush()

    def close(self) -> None:
        self._fh.close()

    def __enter__(self):
        self._fh = open(self.path, "a", encoding="utf-8")
        return self

    def __exit__(self, *exc):
        self.close()


def repair_tail(path: Path) -> int:
    """Drop trailing corrupted lines (e.g. killed mid-write); return bytes dropped."""
    if not path.exists():
        return 0
    data = path.read_bytes()
    lines = data.splitlines()
    valid = [line for line in lines if line.strip() and _valid_json(line)]
    removed = len(lines) - len(valid)
    if removed:
        path.write_bytes(b"\n".join(valid) + (b"\n" if valid else b""))
    return removed


def _valid_json(line: bytes) -> bool:
    try:
        json.loads(line)
        return True
    except ValueError:
        return False


def scan_done_ids(path: Path, key: str) -> set[str]:
    """Return the set of already-processed ids (video_id or shot_id)."""
    if not path.exists():
        return set()
    done: set[str] = set()
    for line in path.read_bytes().splitlines():
        if not line.strip():
            continue
        try:
            done.add(str(json.loads(line)[key]))
        except (ValueError, KeyError):
            continue
    return done


def shard_for(index: int, num_shards: int) -> int:
    return index % num_shards


def merge_shards(in_dir: Path, pattern: str, out_path: Path) -> int:
    """Deterministically merge per-shard JSONL files into one output."""
    files = sorted(in_dir.glob(pattern))
    total = 0
    with SafeJsonlWriter(out_path) as writer:
        for file in files:
            for record in read_jsonl(file):
                writer.append(record)
                total += 1
    return total
