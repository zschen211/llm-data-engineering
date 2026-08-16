"""Shared fixtures for data-factory tests."""

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# Ray reads RAY_ENABLE_UV_RUN_RUNTIME_ENV once, at `import ray` time (see
# ray/_private/ray_constants.py). Under `uv run` the uv-run runtime-env hook
# would repackage the working dir for workers; data-factory's pyproject has a
# path dependency (../llava-instruct) that cannot be rebuilt inside Ray's
# runtime-env staging dir, which makes every worker crash on startup. The
# package sets the same flag at import (CLI drivers); tests set it here so it
# is effective even when `ray` is imported before `data_factory`.
os.environ.setdefault("RAY_ENABLE_UV_RUN_RUNTIME_ENV", "0")

import pytest
import ray

from data_factory.storage import LocalStorageBackend


@pytest.fixture(scope="session", autouse=True)
def ray_runtime():
    """Session-scoped local Ray cluster (pipeline executor runs on it)."""
    ray.init(num_cpus=2, ignore_reinit_error=True, log_to_driver=False)
    yield ray
    ray.shutdown()


@pytest.fixture()
def factory(tmp_path):
    """DataFactory over a temp data dir with the local storage backend."""
    from data_factory.api import DataFactory

    backend = LocalStorageBackend(tmp_path / "data" / "artifacts")
    factory = DataFactory(tmp_path / "data", backend, models_dir=tmp_path / "models")
    yield factory
    factory.close()


@pytest.fixture()
def factory_kwargs(tmp_path):
    """kwargs for open_factory() pointing at a temp dir (CLI-style)."""
    return {"data_dir": tmp_path / "data", "models_dir": tmp_path / "models"}


class _MockHandler(BaseHTTPRequestHandler):
    """Deterministic OpenAI-compatible chat completions.

    Judge prompts (containing 'data-quality judge') get a JSON verdict;
    anything else gets ``answer:<question>`` so expected answers of that
    shape score exact-match.
    """

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        question = body["messages"][0]["content"][0]["text"]
        if "data-quality judge" in question or "answer evaluator" in question:
            content = json.dumps({"score": 0.95, "verdict": "ok", "reason": "good"})
        elif "wrong" in question:
            content = "i do not know"
        else:
            content = f"answer:{question}"
        payload = {"choices": [{"message": {"content": content}}]}
        data = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        data = json.dumps({"models": ["mock"]}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *_args):
        pass


@pytest.fixture()
def mock_llm():
    """In-process mock OpenAI-compatible server; yields its base_url."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _MockHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    thread.join()


def make_import_rows(count: int = 20, dup: int = 0, bad_len: int = 0) -> list[dict]:
    """Synthetic QA rows for import datasets (count with duplicates/long)."""
    rows = []
    for i in range(count):
        question = f"q{i}: what is the bar height?"
        rows.append(
            {
                "question": question,
                "answer": f"answer:{question}",
                "image_id": f"img-{i % 5}",
                "category": "bar" if i % 2 else "pie",
            }
        )
    for _ in range(dup):
        rows.append(dict(rows[0]))
    for _ in range(bad_len):
        rows.append({"question": "long", "answer": "x" * 300, "image_id": "img-x"})
    return rows


def write_import_manifest(tmp_path: Path, rows: list[dict]) -> Path:
    """Write rows as JSONL for an import dataset."""
    path = tmp_path / "import.jsonl"
    with open(path, "w", encoding="utf-8") as fh:
        fh.writelines(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    return path


def write_eval_items(tmp_path: Path, count: int = 8) -> Path:
    """Eval items whose expected answer matches the mock LLM's echo rule."""
    path = tmp_path / "eval.jsonl"
    rows = []
    for i in range(count):
        question = f"q{i}: what is the bar height?"
        row = {
            "question": question,
            "expected": f"answer:{question}",
            "category": "chart_fact" if i % 2 else "chart_compare",
        }
        if i % 4 == 3:
            row["question"] = f"q{i}: what is wrong here?"
            row["expected"] = "answer:WRONG"
            row["category"] = "chart_fact"
        rows.append(row)
    with open(path, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return path
