"""Parquet processor tests: decode downloaded parquet into image assets."""
import io
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from PIL import Image

from llava_instruct.assets.downloaders.base import RemoteRef, sha256_of
from llava_instruct.assets.downloaders.process import get_processor
from llava_instruct.assets.downloaders.processors.parquet import _cell_bytes


def _png_bytes(width=12, height=10, color="red") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buf, "PNG")
    return buf.getvalue()


def _make_parquet(path: Path, n: int = 3) -> Path:
    table = pa.table(
        {"image": pa.array([_png_bytes(10 + i, 8 + i) for i in range(n)], type=pa.binary())}
    )
    pq.write_table(table, path)
    return path


def _remote(name="val.parquet") -> RemoteRef:
    return RemoteRef(id="r1", name=name, path_in_repo=f"data/{name}",
                     meta={"repo_id": "org/coco"})


def test_parquet_processor_decodes_images(tmp_path):
    parquet = _make_parquet(tmp_path / "val.parquet", n=3)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    processor = get_processor("parquet", {"image_column": "image"})
    results = processor.process(_remote(), parquet, out_dir)

    assert len(results) == 3
    for i, candidate in enumerate(results):
        assert candidate.ext == ".png"
        assert candidate.width == 10 + i
        assert candidate.height == 8 + i
        assert candidate.asset_type == "general_image"
        assert candidate.meta["repo_id"] == "org/coco"
        local = Path(candidate.path)
        assert local.exists()
        assert candidate.sha256 == sha256_of(local)
    assert not parquet.exists()  # processor frees the downloaded parquet


def test_parquet_processor_skips_bad_rows(tmp_path):
    table = pa.table(
        {"image": pa.array([_png_bytes(), b"not an image", _png_bytes()], type=pa.binary())}
    )
    parquet = tmp_path / "mixed.parquet"
    pq.write_table(table, parquet)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    results = get_processor("parquet", {}).process(_remote("mixed.parquet"), parquet, out_dir)
    assert len(results) == 2


def test_parquet_processor_asset_type_param(tmp_path):
    parquet = _make_parquet(tmp_path / "val.parquet", n=1)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    results = get_processor("parquet", {"asset_type": "document_image"}).process(
        _remote(), parquet, out_dir
    )
    assert results[0].asset_type == "document_image"


def test_cell_bytes_variants(monkeypatch):
    assert _cell_bytes({"bytes": b"ab", "path": "x.png"}) == b"ab"
    assert _cell_bytes(b"raw") == b"raw"
    assert _cell_bytes({"bytes": None, "path": "/some/local.png"}) is None
    assert _cell_bytes(None) is None

    def fake_urlopen(url, timeout=60):
        class _Resp:
            def read(self):
                return b"from-url"

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        return _Resp()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    assert _cell_bytes("https://example.com/img.png") == b"from-url"
