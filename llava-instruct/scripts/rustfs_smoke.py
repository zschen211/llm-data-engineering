#!/usr/bin/env python3
"""RustFS integration smoke test for the llava-instruct asset layer.

Requires a running RustFS (docker compose up -d) and the ``rustfs`` extra:

    uv sync --extra dev --extra rustfs
    RUSTFS_ENDPOINT=http://localhost:9000 RUSTFS_ACCESS_KEY=rustfsadmin \
    RUSTFS_SECRET_KEY=rustfsadmin uv run python scripts/rustfs_smoke.py

Exercises the real path: local import -> upload (content-addressed) ->
re-sync dedup -> materialize -> snapshot -> preview stream.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from llava_instruct.assets.storage import S3StorageBackend
from llava_instruct.assets.store import AssetStore


def make_images(root: Path) -> Path:
    from PIL import Image

    src = root / "sample_images"
    src.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (120, 80), "red").save(src / "photo_red.png")
    Image.new("RGB", (120, 80), "blue").save(src / "photo_blue.png")
    Image.new("RGB", (160, 90), "white").save(src / "chart_revenue.png")
    Image.new("RGB", (140, 100), "gray").save(src / "doc_page1.png")
    return src


def main() -> int:
    endpoint = os.environ.get("RUSTFS_ENDPOINT", "http://localhost:9000")
    access = os.environ.get("RUSTFS_ACCESS_KEY", "rustfsadmin")
    secret = os.environ.get("RUSTFS_SECRET_KEY", "rustfsadmin")
    bucket = os.environ.get("RUSTFS_BUCKET", "llava-assets")

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        src = make_images(tmp)
        backend = S3StorageBackend(endpoint, access, secret, bucket)
        store = AssetStore(tmp / "assets.db", backend, tmp_dir=tmp / "tmp")
        with store:
            print(f"[1] backend ready: {endpoint} bucket={bucket}")
            report = store.import_dir(src, source_name="smoke-import")
            print(f"[2] import: resolved={report.resolved} new={report.new} failed={report.failed}")
            assert report.new == 4 and report.failed == 0, report

            report2 = store.import_dir(src, source_name="smoke-import")
            print(f"[3] re-sync: skipped={report2.skipped_existing} new={report2.new}")
            assert report2.new == 0 and report2.skipped_existing == 4, report2

            assets = store.list_assets()
            assert all(a.object_key for a in assets)
            keys = {a.object_key for a in assets}
            assert len(keys) == 4, "content-addressed keys must be unique per content"
            print(f"[4] assets registered: {len(assets)}, keys unique: {len(keys)}")

            store.tag_asset(assets[0].id, "smoke", group="test")
            assert len(store.list_assets(tags=["test=smoke"])) == 1
            print("[5] tagging works")

            snapshot = store.create_snapshot(name="smoke-v1")
            assert snapshot["asset_count"] == 4
            print(f"[6] snapshot: {snapshot['id']} sha1={snapshot['manifest_sha1'][:12]}")

            out_dir = tmp / "pool"
            records = store.materialize(out_dir)
            assert len(records) == 4 and all(Path(r["path"]).exists() for r in records)
            print(f"[7] materialize ok: {len(records)} files")

            stream = backend.open_stream(assets[0].object_key)
            head = stream.read(8)
            assert head[:4] == b"\x89PNG", head
            print("[8] preview stream ok")

            store.delete_asset(assets[0].id)
            print(f"[9] delete ok, remaining: {store.count_assets()}")

    print("\nRustFS smoke test PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
