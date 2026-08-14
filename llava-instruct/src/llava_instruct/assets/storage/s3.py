"""S3-compatible storage backend (RustFS / MinIO / cloud S3) via boto3."""

from __future__ import annotations

from pathlib import Path

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from .base import StorageBackend, object_key_for


class S3StorageBackend(StorageBackend):
    """S3-compatible object storage (RustFS / MinIO / cloud S3) via boto3.

    ``endpoint_url`` may be None for the default AWS endpoint (also used by
    S3 mocks such as moto).
    """

    name = "s3"

    def __init__(  # nosec B107: empty placeholder defaults; real creds from env
        self,
        endpoint_url: str | None = None,
        access_key: str = "",
        secret_key: str = "",
        bucket: str = "llava-assets",
        region: str = "us-east-1",
    ):
        self.endpoint_url = endpoint_url
        self.access_key = access_key
        self.secret_key = secret_key
        self.bucket = bucket
        self.region = region
        client_kwargs = {
            "aws_access_key_id": access_key,
            "aws_secret_access_key": secret_key,
            "region_name": region,
            "config": Config(
                s3={"addressing_style": "path"}, retries={"max_attempts": 3}
            ),
        }
        if endpoint_url:
            client_kwargs["endpoint_url"] = endpoint_url
        self.client = boto3.client("s3", **client_kwargs)
        self._ensure_bucket()

    def _ensure_bucket(self) -> None:
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

    def put_object(self, key: str, local_path: Path) -> str:
        if self.exists(key):
            return key
        self.client.upload_file(str(local_path), self.bucket, key)
        return key

    def copy_object(self, src_key: str, dst_key: str) -> str:
        if self.exists(dst_key):
            return dst_key
        self.client.copy_object(
            Bucket=self.bucket,
            Key=dst_key,
            CopySource={"Bucket": self.bucket, "Key": src_key},
        )
        return dst_key

    def get_file(self, object_key: str, target: Path) -> Path:
        target.parent.mkdir(parents=True, exist_ok=True)
        self.client.download_file(self.bucket, object_key, str(target))
        return target

    def exists(self, object_key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=object_key)
            return True
        except ClientError as exc:
            if exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode") == 404:
                return False
            raise

    def open_stream(self, object_key: str):
        return self.client.get_object(Bucket=self.bucket, Key=object_key)["Body"]
