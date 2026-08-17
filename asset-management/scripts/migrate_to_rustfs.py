#!/usr/bin/env python3
"""Migrate locally-stored blobs into the RustFS bucket (same object keys).

The SQLite metadata (assets / asset_versions / raw_files) already carries the
object keys; this script only moves the bytes, so after it completes the
service can be restarted with ``ASSET_STORAGE_BACKEND=rustfs`` (the default
of scripts/serve.sh) and keeps serving the same objects. Safe to re-run:
objects already present in the bucket are skipped.

Usage (requires the RustFS container: docker compose up -d):

    uv run python scripts/migrate_to_rustfs.py \
        [--data-dir data] [--endpoint http://localhost:9000] \
        [--access-key rustfsadmin] [--secret-key rustfsadmin] \
        [--bucket asset-assets] [--dry-run]
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

DB_TABLES = ("assets", "asset_versions", "raw_files")
WORKERS = 16


def keys_from_db(db_path: Path) -> set[str]:
    """Object keys referenced by the metadata tables (may point at objects
    whose local file was already cleaned up — reported as missing)."""
    conn = sqlite3.connect(db_path)
    try:
        keys: set[str] = set()
        for table in DB_TABLES:
            rows = conn.execute(
                # table names come from the allowlist above
                f"SELECT DISTINCT object_key FROM {table} "  # nosec B608
                "WHERE object_key IS NOT NULL AND object_key != ''"
            )
            keys.update(r[0] for r in rows)
        return keys
    finally:
        conn.close()


def collect_keys(local_root: Path, db_path: Path | None) -> set[str]:
    """Union of the files on local disk (relative path = object key) and the
    keys referenced by the metadata index."""
    keys = {
        str(path.relative_to(local_root))
        for path in local_root.rglob("*")
        if path.is_file()
    }
    if db_path is not None and db_path.exists():
        keys |= keys_from_db(db_path)
    return keys


def object_exists(client, bucket: str, key: str) -> bool:
    try:
        client.head_object(Bucket=bucket, Key=key)
        return True
    except ClientError as exc:
        if exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode") == 404:
            return False
        raise


def make_client(args) -> object:
    return boto3.client(
        "s3",
        endpoint_url=args.endpoint,
        aws_access_key_id=args.access_key,
        aws_secret_access_key=args.secret_key,
        region_name="us-east-1",
        config=Config(s3={"addressing_style": "path"}, retries={"max_attempts": 3}),
    )


def transfer_one(args, client, local_root: Path, bucket: str, key: str) -> str:
    """Upload one object; returns 'uploaded' | 'skipped' | 'missing' | 'failed'."""
    source = local_root / key
    if not source.is_file():
        return "missing"
    try:
        if object_exists(client, bucket, key):
            return "skipped"
        if not args.dry_run:
            client.upload_file(str(source), bucket, key)
        return "uploaded"
    except ClientError as exc:
        print(f"  FAILED {key}: {exc}", file=sys.stderr)
        return "failed"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Move local blobs into the RustFS bucket (same object keys)."
    )
    parser.add_argument("--data-dir", default=os.environ.get("ASSET_DATA_DIR", "data"))
    parser.add_argument(
        "--endpoint", default=os.environ.get("RUSTFS_ENDPOINT", "http://localhost:9000")
    )
    parser.add_argument(
        "--access-key", default=os.environ.get("RUSTFS_ACCESS_KEY", "rustfsadmin")
    )
    parser.add_argument(
        "--secret-key", default=os.environ.get("RUSTFS_SECRET_KEY", "rustfsadmin")
    )
    parser.add_argument(
        "--bucket", default=os.environ.get("RUSTFS_BUCKET", "asset-assets")
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--workers", type=int, default=WORKERS)
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    local_root = data_dir / "blobs"
    db_path = data_dir / "assets.db"
    if not local_root.is_dir():
        print(f"error: local blob dir not found: {local_root}", file=sys.stderr)
        return 1

    probe = make_client(args)
    try:
        probe.head_bucket(Bucket=args.bucket)
    except ClientError as exc:
        print(
            f"error: bucket {args.bucket!r} not reachable at {args.endpoint}: {exc}",
            file=sys.stderr,
        )
        print("is the RustFS container up? (docker compose up -d)", file=sys.stderr)
        return 1

    keys = sorted(collect_keys(local_root, db_path if db_path.exists() else None))
    print(f"target: {args.endpoint} bucket={args.bucket} objects={len(keys)}")
    counters = {"uploaded": 0, "skipped": 0, "missing": 0, "failed": 0}
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {}
        clients = {}
        for index, key in enumerate(keys):
            thread = index % max(1, args.workers)
            client = clients.setdefault(thread, make_client(args))
            futures[pool.submit(transfer_one, args, client, local_root, args.bucket, key)] = key
        for future in as_completed(futures):
            result = future.result()
            counters[result] += 1
            if result == "uploaded" and counters["uploaded"] % 1000 == 0:
                print(f"  ... {counters['uploaded']} uploaded")
    print(
        f"done: uploaded={counters['uploaded']} skipped(exists)={counters['skipped']} "
        f"missing_locally={counters['missing']} failed={counters['failed']}"
    )
    if args.dry_run:
        print("dry-run: nothing was written")
    return 1 if counters["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
