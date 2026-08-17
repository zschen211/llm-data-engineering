"""Local-disk storage backend (zero-dependency default)."""

from __future__ import annotations

from pathlib import Path

from .base import StorageBackend, content_key_for


class LocalStorageBackend(StorageBackend):
    """Store artifacts under a local root directory (``data/artifacts/``)."""

    name = "local"

    def __init__(self, root: Path):
        self.root = Path(root)

    def _resolve(self, key: str) -> Path:
        root = self.root.resolve()
        path = (self.root / key).resolve()
        if root not in path.parents and path != root:
            raise ValueError(f"key escapes storage root: {key}")
        return path

    def put_file(self, local_path: Path, sha256: str, ext: str) -> str:
        key = content_key_for(sha256, ext)
        if self.exists(key):
            return key
        return self.put_object(key, local_path)

    def put_object(self, key: str, local_path: Path) -> str:
        if self.exists(key):
            return key
        target = self._resolve(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(Path(local_path).read_bytes())
        return key

    def get_file(self, object_key: str, target: Path) -> Path:
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        Path(target).write_bytes(self._resolve(object_key).read_bytes())
        return Path(target)

    def exists(self, object_key: str) -> bool:
        return self._resolve(object_key).is_file()

    def open_stream(self, object_key: str):
        return open(self._resolve(object_key), "rb")
