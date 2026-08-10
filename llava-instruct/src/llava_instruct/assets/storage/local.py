"""Local-disk storage backend: a content-addressed directory."""

from __future__ import annotations

import shutil
from pathlib import Path

from .base import StorageBackend, object_key_for


class LocalStorageBackend(StorageBackend):
    """Content-addressed directory on the local filesystem."""

    name = "local"

    def __init__(self, root: Path):
        self.root = Path(root)

    def local_path(self, object_key: str) -> Path:
        return self.root / object_key

    def put_file(self, local_path: Path, sha256: str, ext: str) -> str:
        key = object_key_for(sha256, ext)
        target = self.local_path(key)
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(local_path, target)
        return key

    def get_file(self, object_key: str, target: Path) -> Path:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.local_path(object_key), target)
        return target

    def exists(self, object_key: str) -> bool:
        return self.local_path(object_key).exists()

    def open_stream(self, object_key: str):
        return open(self.local_path(object_key), "rb")
