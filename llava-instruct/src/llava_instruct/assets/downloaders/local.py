"""Local directory import: copy files from a local folder into the pool."""
from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from ..classify import IMAGE_SUFFIXES, classify_image
from ..models import Source
from ..registry import register
from .base import BaseDownloader, DownloadResult, RemoteAsset, ext_of, image_size, sha256_of


@register("local")
class LocalImportDownloader(BaseDownloader):
    kind = "local"

    def resolve(self, source: Source) -> list[RemoteAsset]:
        root = Path(source.params.get("path") or source.url)
        labels = source.params.get("labels") or {}
        remotes = []
        for path in sorted(root.iterdir()):
            if not (path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES):
                continue
            asset_id = hashlib.sha1(str(path).encode("utf-8")).hexdigest()[:12]
            remotes.append(
                RemoteAsset(
                    id=f"local_{asset_id}",
                    name=path.name,
                    url=str(path),
                    meta={"labels": labels.get(path.name, {})},
                )
            )
        return remotes

    def download(self, remote: RemoteAsset, target: Path) -> DownloadResult:
        src = Path(remote.url)
        if not src.exists():
            raise FileNotFoundError(f"local file missing: {src}")
        shutil.copyfile(src, target)
        width, height = image_size(target) or (None, None)
        return DownloadResult(
            sha256=sha256_of(target),
            size=target.stat().st_size,
            ext=ext_of(src.name),
            width=width,
            height=height,
            meta={**remote.meta, "asset_type": classify_image(src, remote.meta.get("labels"))},
        )
