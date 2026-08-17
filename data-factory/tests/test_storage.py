"""Storage backend tests: local + S3 (moto), content addressing, jsonl."""

from pathlib import Path

import moto
import pytest

from data_factory import jsonl
from data_factory.storage.base import (
    artifact_key_for,
    content_key_for,
    manifest_key_for,
    report_key_for,
)
from data_factory.storage.local import LocalStorageBackend
from data_factory.storage.s3 import S3StorageBackend


@pytest.fixture()
def backend(tmp_path):
    return LocalStorageBackend(tmp_path / "artifacts")


def test_content_addressing_dedup(backend):
    blob = b"hello world"
    p1 = _write_tmp("a.txt", blob)
    p2 = _write_tmp("b.txt", blob)
    key1 = backend.put_file(p1, jsonl.sha256_of(p1), ".txt")
    key2 = backend.put_file(p2, jsonl.sha256_of(p2), ".txt")
    assert key1 == key2
    assert key1.startswith("blobs/")
    assert backend.exists(key1)


def test_put_get_roundtrip(backend):
    key = artifact_key_for("run_1", "nd_1", "out.jsonl")
    src = _write_tmp("src.txt", b"data")
    backend.put_object(key, src)
    target = backend.get_file(key, Path("/tmp/opencode/target.txt"))
    assert target.read_bytes() == b"data"


def test_key_escape_rejected(tmp_path):
    backend = LocalStorageBackend(tmp_path / "root")
    with pytest.raises(ValueError):
        backend._resolve("../../etc/passwd")


def test_jsonl_roundtrip(backend, tmp_path):
    rows = [{"a": 1, "b": {"c": [1, 2]}}, {"a": 2}]
    info = jsonl.write_rows(
        backend, "artifacts/r1/n1/out.jsonl", rows, tmp_dir=tmp_path
    )
    assert info["row_count"] == 2
    assert jsonl.read_rows(backend, info["key"], tmp_path) == rows


def test_jsonl_content_addressed(backend, tmp_path):
    rows = [{"q": "x"}]
    info = jsonl.write_rows_ca(backend, rows, tmp_dir=tmp_path)
    assert info["key"] == content_key_for(info["sha256"], ".jsonl")
    assert jsonl.read_rows(backend, info["key"]) == rows


def test_manifest_roundtrip(backend):
    key = manifest_key_for("ds_1", 3)
    assert key == "datasets/ds_1/v3/manifest.json"
    jsonl.write_manifest(backend, key, {"version": 3, "files": []})
    assert jsonl.read_manifest(backend, key) == {"version": 3, "files": []}


def test_key_helpers():
    assert content_key_for("abc", ".jsonl").endswith("/abc.jsonl")
    assert artifact_key_for("r", "n", "f").startswith("artifacts/r/n/")
    assert report_key_for("es", "rep", "md").startswith("evals/es/rep.md")


def test_s3_backend_with_moto():
    with moto.mock_aws():
        backend = S3StorageBackend(None, "ak", "sk", bucket="dfac-datasets")
        assert backend.bucket == "dfac-datasets"
        src = _write_tmp("x.txt", b"payload")
        key = backend.put_file(src, jsonl.sha256_of(src), ".txt")
        assert backend.exists(key)
        target = backend.get_file(key, Path("/tmp/opencode/s3-target.txt"))
        assert target.read_bytes() == b"payload"
        assert backend.exists("missing-key") is False


def _write_tmp(name: str, data: bytes) -> Path:
    path = Path("/tmp/opencode") / name
    path.write_bytes(data)
    return path
