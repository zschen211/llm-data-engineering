import hashlib
import http.server
import threading
from pathlib import Path

import pytest
from PIL import Image

from llava_instruct.assets.downloaders.base import sha256_of
from llava_instruct.assets.downloaders.huggingface import HfDownloader
from llava_instruct.assets.downloaders.http import HttpDownloader
from llava_instruct.assets.downloaders.local import LocalImportDownloader
from llava_instruct.assets.models import Source


def _image(path, color="red"):
    Image.new("RGB", (20, 20), color).save(path)


def test_local_resolve_and_download(tmp_path):
    src = tmp_path / "imgs"
    src.mkdir()
    _image(src / "doc_page1.png")
    _image(src / "chart_revenue.jpg")
    _image(src / "photo.png")
    (src / "note.txt").write_text("x")
    source = Source(id="s1", name="local", kind="local", url=str(src))
    downloader = LocalImportDownloader()
    remotes = downloader.resolve(source)
    assert len(remotes) == 3
    by_name = {r.name: r for r in remotes}
    target = tmp_path / "out.png"
    result = downloader.download(by_name["doc_page1.png"], target)
    assert result.size > 0
    assert result.width == 20
    assert result.ext == ".png"
    assert sha256_of(target) == result.sha256
    assert result.meta["asset_type"] == "document_image"
    result2 = downloader.download(by_name["chart_revenue.jpg"], tmp_path / "o2.jpg")
    assert result2.meta["asset_type"] == "chart_image"
    result3 = downloader.download(by_name["photo.png"], tmp_path / "o3.png")
    assert result3.meta["asset_type"] == "general_image"


def _make_handler(directory):
    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(directory), **kwargs)

        def log_message(self, *args):
            pass

    return Handler


@pytest.fixture
def http_server(tmp_path):
    (tmp_path / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 100)
    (tmp_path / "b.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"y" * 100)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(tmp_path))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    yield base, tmp_path
    server.shutdown()


def test_http_resolve_download_and_resume(tmp_path, http_server):
    base, root = http_server
    url = f"{base}/a.png"
    source = Source(id="s1", name="http", kind="http",
                    params={"urls": [{"name": "a.png", "url": url}]})
    downloader = HttpDownloader()
    remotes = downloader.resolve(source)
    assert len(remotes) == 1
    assert remotes[0].name == "a.png"

    target = tmp_path / "a.png"
    result = downloader.download(remotes[0], target)
    assert result.size == (root / "a.png").stat().st_size
    assert result.sha256 == sha256_of(root / "a.png")

    # resume: pre-create partial file then re-download
    partial = tmp_path / "a2.png"
    partial.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 50)
    remotes[0].expected_sha256 = sha256_of(root / "a.png")
    result2 = downloader.download(remotes[0], partial)
    assert result2.sha256 == sha256_of(root / "a.png")


def test_http_sha256_mismatch_fails(tmp_path, http_server):
    base, _ = http_server
    source = Source(id="s1", name="http", kind="http",
                    params={"urls": [{"name": "a.png", "url": f"{base}/a.png",
                                      "sha256": "f" * 64}]})
    downloader = HttpDownloader()
    remote = downloader.resolve(source)[0]
    with pytest.raises(ValueError, match="mismatch"):
        downloader.download(remote, tmp_path / "a.png")


def test_http_unreachable_retries(tmp_path):
    source = Source(id="s1", name="http", kind="http",
                    params={"urls": ["http://127.0.0.1:1/nope.png"]})
    downloader = HttpDownloader()
    remote = downloader.resolve(source)[0]
    with pytest.raises(RuntimeError, match="attempts"):
        downloader.download(remote, tmp_path / "nope.png")


class _FakeHub:
    FILES = ["images/a.png", "images/b.jpg", "metadata.json"]

    def list_repo_files(self, repo_id, repo_type="dataset"):
        return self.FILES

    def hf_hub_download(self, repo_id, filename, repo_type="dataset", local_dir=None):
        path = Path(local_dir) / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"z" * 64)
        return path


def test_hf_resolve_filters(tmp_path, monkeypatch):
    monkeypatch.setattr("llava_instruct.assets.downloaders.huggingface._require_hub", lambda: _FakeHub())
    source = Source(id="s1", name="hf", kind="huggingface",
                    params={"repo_id": "org/ds", "allow_patterns": ["*.png"], "subfolder": "images"})
    downloader = HfDownloader()
    remotes = downloader.resolve(source)
    assert [r.name for r in remotes] == ["a.png"]
    result = downloader.download(remotes[0], tmp_path / "a.png")
    assert result.sha256 == sha256_of(tmp_path / "a.png")
    assert result.ext == ".png"
