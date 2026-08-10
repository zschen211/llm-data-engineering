import hashlib

import moto
import pytest
from PIL import Image

from llava_instruct.assets.storage import LocalStorageBackend, S3StorageBackend, object_key_for


@pytest.fixture
def sample_file(tmp_path):
    path = tmp_path / "a.png"
    Image.new("RGB", (10, 10), "red").save(path)
    return path


def test_object_key_layout():
    key = object_key_for("abcdef123456", ".png")
    assert key == "blobs/ab/abcdef123456.png"


def test_local_put_get_dedup(tmp_path, sample_file):
    backend = LocalStorageBackend(tmp_path / "blobs")
    sha = hashlib.sha256(sample_file.read_bytes()).hexdigest()
    key1 = backend.put_file(sample_file, sha, ".png")
    key2 = backend.put_file(sample_file, sha, ".png")
    assert key1 == key2
    assert backend.exists(key1)
    target = tmp_path / "out.png"
    backend.get_file(key1, target)
    assert target.read_bytes() == sample_file.read_bytes()
    with backend.open_stream(key1) as stream:
        assert stream.read(4) == b"\x89PNG"


def test_local_put_missing(tmp_path):
    backend = LocalStorageBackend(tmp_path / "blobs")
    assert not backend.exists("blobs/ab/abc.png")


def test_s3_backend_with_moto(tmp_path, sample_file):
    with moto.mock_aws():
        backend = S3StorageBackend(
            access_key="rustfsadmin",
            secret_key="rustfsadmin",
            bucket="llava-assets",
        )
        sha = hashlib.sha256(sample_file.read_bytes()).hexdigest()
        key = backend.put_file(sample_file, sha, ".png")
        assert key == object_key_for(sha, ".png")
        assert backend.exists(key)
        assert backend.exists("blobs/nope/nope.png") is False

        target = tmp_path / "out.png"
        backend.get_file(key, target)
        assert target.read_bytes() == sample_file.read_bytes()

        stream = backend.open_stream(key)
        assert stream.read(4) == b"\x89PNG"

        key2 = backend.put_file(sample_file, sha, ".png")  # dedup
        assert key2 == key
