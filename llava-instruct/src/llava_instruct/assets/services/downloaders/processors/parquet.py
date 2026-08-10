"""Parquet processor: decode rows of a downloaded parquet into image assets.

Used for HF ``datasets`` repos such as lmms-lab-encoder/COCO-Caption where
images are embedded in the parquet (HF Image feature). Each row's image is
extracted and written as a JPEG/PNG file; broken rows are skipped.

Processor params:
  image_column: "image" (default)
  asset_type:   "general_image" (default)
  batch_size:   512 (default) — rows per streaming batch

Deletes the downloaded parquet after extraction to free disk space.
"""

from __future__ import annotations

import io
import os
import urllib.request
from pathlib import Path

import pyarrow.parquet as pq
from PIL import Image

from ...downloaders.base import Candidate, RemoteRef, sha256_of
from ...downloaders.process import Processor, register_processor


def _cell_bytes(cell, fallback_url: str = "") -> bytes | None:
    """Extract raw image bytes from an HF Image cell (dict / bytes / url)."""
    if isinstance(cell, dict):
        data = cell.get("bytes") or cell.get("path")
    elif isinstance(cell, (bytes, bytearray)):
        data = bytes(cell)
    elif isinstance(cell, str):
        data = cell
    else:
        data = None
    if isinstance(data, str):
        if data.startswith(("http://", "https://")):
            with urllib.request.urlopen(data, timeout=60) as response:
                return response.read()
        data = None
    return data if isinstance(data, (bytes, bytearray)) else None


@register_processor("parquet")
class ParquetProcessor(Processor):
    name = "parquet"

    def process(
        self, remote: RemoteRef, local_path: Path, work_dir: Path
    ) -> list[Candidate]:
        image_column = self.params.get("image_column", "image")
        asset_type = self.params.get("asset_type", "general_image")
        batch_size = int(self.params.get("batch_size", 512))
        prefix = Path(remote.name).stem
        images_dir = work_dir / "images"
        images_dir.mkdir(exist_ok=True)

        results: list[Candidate] = []
        skipped = 0
        try:
            for batch in pq.ParquetFile(local_path).iter_batches(
                columns=[image_column], batch_size=batch_size
            ):
                column = batch.column(0)
                for idx in range(len(column)):
                    data = _cell_bytes(column[idx].as_py())
                    if not data:
                        skipped += 1
                        continue
                    try:
                        image = Image.open(io.BytesIO(data))
                        image.load()
                    except Exception:
                        skipped += 1
                        continue
                    fmt = (image.format or "JPEG").upper()
                    ext = ".png" if fmt == "PNG" else ".jpg"
                    name = f"{prefix}_{idx:06d}{ext}"
                    out = images_dir / name
                    if fmt == "PNG":
                        image.save(out, format="PNG")
                    else:
                        image.convert("RGB").save(out, format="JPEG", quality=92)
                    results.append(
                        Candidate(
                            name=name,
                            path=str(out),
                            sha256=sha256_of(out),
                            size=out.stat().st_size,
                            ext=ext,
                            asset_type=asset_type,
                            width=image.width,
                            height=image.height,
                            meta={**remote.meta, "skipped_rows": skipped},
                        )
                    )
        finally:
            try:
                os.remove(local_path)  # release the (large) parquet temp file
            except OSError:
                pass
        return results
