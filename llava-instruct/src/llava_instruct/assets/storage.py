"""Storage backends for multimodal blobs.

Object keys are content-addressed: ``blobs/<sha256[:2]>/<sha256><ext>`` so the
same content is stored exactly once and the key is bound to the content.
"""
from __future__ import annotations

import shutil
from abc import ABC, abstractmethod
from pathlib import Path

KEY_PREFIX = "blobs"


def object_key_for(sha256: str, ext: str) -> str:
    return f"{KEY_PREFIX}/{sha256[:2]}/{sha256}{ext}"


class StorageBackend(ABC):
    """Pluggable blob storage. Implementations: local disk, S3/RustFS."""

    @abstractmethod
    def put_file(self, local_path: Path, sha256: str, ext: str) -> str:
        """Store a file; return its object key. No-op when the key already exists."""

    @abstractmethod
    def get_file(self, object_key: str, target: Path) -> Path:
        """Fetch an object to a local path."""

    @abstractmethod
    def exists(self, object_key: str) -> bool:
        ...

    @abstractmethod
    def open_stream(self, object_key: str):
        """Return a binary file-like object for streaming reads (preview)."""


class LocalStorageBackend(StorageBackend):
    """Content-addressed directory on the local filesystem."""

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


class S3StorageBackend(StorageBackend):
    """S3-compatible object storage (RustFS / MinIO / cloud S3) via boto3.

    ``endpoint_url`` may be None for the default AWS endpoint (also used by
    S3 mocks such as moto).
    """

    def __init__(self, endpoint_url: str | None = None, access_key: str = "",
                 secret_key: str = "", bucket: str = "llava-assets",
                 region: str = "us-east-1"):
        import boto3
        from botocore.client import Config

        self.bucket = bucket
        client_kwargs = dict(
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
            config=Config(s3={"addressing_style": "path"}, retries={"max_attempts": 3}),
        )
        if endpoint_url:
            client_kwargs["endpoint_url"] = endpoint_url
        self.client = boto3.client("s3", **client_kwargs)
        self._ensure_bucket()

    def _ensure_bucket(self) -> None:
        from botocore.exceptions import ClientError

        try:
            self.client.head_bucket(Bucket=self.bucket)
        except ClientError:
            self.client.create_bucket(Bucket=self.bucket)

    def put_file(self, local_path: Path, sha256: str, ext: str) -> str:
        key = object_key_for(sha256, ext)
        if self.exists(key):
            return key
        self.client.upload_file(str(local_path), self.bucket, key)
        return key

    def get_file(self, object_key: str, target: Path) -> Path:
        target.parent.mkdir(parents=True, exist_ok=True)
        self.client.download_file(self.bucket, object_key, str(target))
        return target

    def exists(self, object_key: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self.client.head_object(Bucket=self.bucket, Key=object_key)
            return True
        except ClientError as exc:
            if exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode") == 404:
                return False
            raise

    def open_stream(self, object_key: str):
        return self.client.get_object(Bucket=self.bucket, Key=object_key)["Body"]
