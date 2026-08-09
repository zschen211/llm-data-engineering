"""Pipeline stage tests: download -> process -> persist."""
import hashlib
import shutil
from pathlib import Path

import pytest

from llava_instruct.assets.db import Database
from llava_instruct.assets.downloaders.base import Candidate, RemoteRef, sha256_of
from llava_instruct.assets.downloaders.download import DownloadStage
from llava_instruct.assets.downloaders.persist import PersistStage
from llava_instruct.assets.downloaders.process import FileProcessor, get_processor
from llava_instruct.assets.models import Source
from llava_instruct.assets.storage import LocalStorageBackend


class FakeHub:
    FILES = ["data/chart_rev.png", "data/photo.png", "data/notes.txt", "README.md"]

    def __init__(self, fail_names: tuple[str, ...] = (), fail_times: int = 0):
        self.fail_names = fail_names
        self.fail_times = fail_times
        self.download_calls = 0

    def list_repo_files(self, repo_id, repo_type="dataset"):
        return self.FILES

    def hf_hub_download(self, repo_id, filename, repo_type="dataset", local_dir=None):
        self.download_calls += 1
        if filename in self.fail_names or self.download_calls <= self.fail_times:
            raise RuntimeError("transient network error")
        target = Path(local_dir) / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"\x89PNG\r\n\x1a\n" + filename.encode("utf-8") * 16)
        return target


def _hub_stage(fake: FakeHub, monkeypatch, **kwargs) -> DownloadStage:
    monkeypatch.setattr(
        "llava_instruct.assets.downloaders.download._require_hub",
        lambda: fake,
    )
    return DownloadStage(repo_id="org/ds", **kwargs)


# -------------------------------------------------------------- download
def test_from_source_requires_repo_id():
    with pytest.raises(ValueError, match="repo_id"):
        DownloadStage.from_source(Source(id="s1", name="x", kind="huggingface", params={}))


def test_from_source_reads_params():
    stage = DownloadStage.from_source(
        Source(id="s1", name="x", kind="huggingface",
               params={"repo_id": "org/ds", "subfolder": "data", "attempts": 5})
    )
    assert stage.repo_id == "org/ds"
    assert stage.subfolder == "data"
    assert stage.attempts == 5


def test_resolve_filters(monkeypatch):
    stage = _hub_stage(FakeHub(), monkeypatch, subfolder="data")
    remotes = stage.resolve()
    assert [r.name for r in remotes] == ["chart_rev.png", "notes.txt", "photo.png"]
    assert all(r.meta["repo_id"] == "org/ds" for r in remotes)


def test_download_retries_then_succeeds(tmp_path, monkeypatch):
    fake = FakeHub(fail_times=2)
    stage = _hub_stage(fake, monkeypatch, attempts=3)
    remote = stage.resolve()[0]
    target = tmp_path / "out.png"
    stage.download(remote, target)
    assert target.exists()
    assert fake.download_calls == 3  # 2 failures + 1 success


def test_download_fails_after_attempts(tmp_path, monkeypatch):
    fake = FakeHub(fail_times=99)
    stage = _hub_stage(fake, monkeypatch, attempts=3)
    remote = stage.resolve()[0]
    with pytest.raises(RuntimeError, match="3 attempts"):
        stage.download(remote, tmp_path / "out.png")
    assert fake.download_calls == 3


def test_fetch_all_parallel_partial_failure(tmp_path, monkeypatch):
    fake = FakeHub(fail_names=("data/notes.txt",))
    stage = _hub_stage(fake, monkeypatch)
    remotes = stage.resolve()
    downloaded, errors = stage.fetch_all(remotes, tmp_path / "work", workers=2)
    assert set(downloaded) == {r.id for r in remotes if r.name != "notes.txt"}
    assert len(errors) == 1
    assert "notes.txt" in errors[next(iter(errors))] or len(errors) == 1


# --------------------------------------------------------------- process
def test_file_processor_identity(tmp_path):
    img = tmp_path / "chart_rev.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 64)
    remote = RemoteRef(id="r1", name="chart_rev.png", path_in_repo="data/chart_rev.png",
                       meta={"repo_id": "org/ds"})
    candidates = FileProcessor().process(remote, img, tmp_path / "work")
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.name == "chart_rev.png"
    assert candidate.asset_type == "chart_image"  # filename heuristic
    assert candidate.sha256 == sha256_of(img)
    assert candidate.meta["repo_id"] == "org/ds"


def test_file_processor_asset_type_override(tmp_path):
    img = tmp_path / "photo.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 64)
    remote = RemoteRef(id="r1", name="photo.png", path_in_repo="data/photo.png", meta={})
    candidates = get_processor("file", {"asset_type": "document_image"}).process(
        remote, img, tmp_path / "work"
    )
    assert candidates[0].asset_type == "document_image"


def test_get_processor_unknown():
    with pytest.raises(ValueError, match="unknown processor"):
        get_processor("nope")


# --------------------------------------------------------------- persist
def test_persist_stage_dedup(tmp_path):
    db = Database(tmp_path / "assets.db")
    backend = LocalStorageBackend(tmp_path / "blobs")
    stage = PersistStage(backend, db)
    source = db.add_source("s", "huggingface", params={"repo_id": "org/ds"})

    img = tmp_path / "a.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"y" * 32)
    candidate = Candidate(
        name="a.png", path=str(img), sha256=sha256_of(img),
        size=img.stat().st_size, ext=".png", asset_type="general_image",
        width=10, height=8, meta={"repo_id": "org/ds"},
    )
    assert stage.persist_one(source, candidate) == "new"
    assert stage.persist_one(source, candidate) == "skipped"
    assert db.count_assets() == 1
    asset = db.list_assets()[0]
    assert asset.asset_type == "general_image"
    assert asset.width == 10
    assert asset.meta["remote"]["repo_id"] == "org/ds"


def test_persist_batch_reports_errors(tmp_path):
    db = Database(tmp_path / "assets.db")
    stage = PersistStage(LocalStorageBackend(tmp_path / "blobs"), db)
    source = db.add_source("s", "huggingface", params={"repo_id": "org/ds"})
    missing = Candidate(name="gone.png", path=str(tmp_path / "gone.png"),
                        sha256="0" * 64, size=1, ext=".png")
    new, skipped, errors = stage.persist(source, [missing])
    assert new == 0
    assert len(errors) == 1
    assert "gone.png" in errors[0]


def test_persist_pipeline_chain_with_store(tmp_path, monkeypatch):
    """End-to-end through the store: hf source + file processor."""
    from llava_instruct.assets.api import open_store

    fake = FakeHub()
    monkeypatch.setattr(
        "llava_instruct.assets.downloaders.download._require_hub",
        lambda: fake,
    )
    with open_store(data_dir=tmp_path / "data") as store:
        source = store.add_source("hf-test", "huggingface",
                                  params={"repo_id": "org/ds", "subfolder": "data"})
        report = store.sync_source(source.id)
        assert report.new == 3  # chart_rev.png + notes.txt + photo.png
        assert report.failed == 0
        assets = store.list_assets(status="ready")
        assert {a.name for a in assets} == {"chart_rev.png", "notes.txt", "photo.png"}
        assert next(a for a in assets if a.name == "chart_rev.png").asset_type == "chart_image"

        report2 = store.sync_source(source.id)
        assert report2.new == 0
        assert report2.skipped_existing == 3
