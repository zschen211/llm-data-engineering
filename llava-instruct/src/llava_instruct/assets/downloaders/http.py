"""HTTP downloader: direct URL fetch with Range resume, retries and sha256 check.

Source params:
  urls: [{"name": "...", "url": "...", "sha256": "..."} | "https://..."]
"""
from __future__ import annotations

import hashlib
import time
import urllib.error
import urllib.request
from pathlib import Path

from ..models import Source
from ..registry import register
from .base import BaseDownloader, DownloadResult, RemoteAsset, ext_of, image_size, sha256_of

MAX_ATTEMPTS = 3
CHUNK = 1024 * 256


@register("http")
class HttpDownloader(BaseDownloader):
    kind = "http"

    def resolve(self, source: Source) -> list[RemoteAsset]:
        remotes = []
        for item in source.params.get("urls") or []:
            if isinstance(item, dict):
                url = item["url"]
                remotes.append(
                    RemoteAsset(
                        id=f"http_{hashlib.sha1(url.encode('utf-8')).hexdigest()[:12]}",
                        name=item.get("name") or Path(url).name,
                        url=url,
                        expected_sha256=item.get("sha256"),
                    )
                )
            else:
                remotes.append(
                    RemoteAsset(
                        id=f"http_{hashlib.sha1(str(item).encode('utf-8')).hexdigest()[:12]}",
                        name=Path(item).name,
                        url=str(item),
                    )
                )
        return remotes

    def download(self, remote: RemoteAsset, target: Path) -> DownloadResult:
        last_error: Exception | None = None
        for attempt in range(MAX_ATTEMPTS):
            try:
                self._fetch(remote.url, target)
                break
            except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
                last_error = exc
                if attempt < MAX_ATTEMPTS - 1:
                    time.sleep(2**attempt)
        else:
            raise RuntimeError(f"download failed after {MAX_ATTEMPTS} attempts: {last_error}") from last_error

        sha256 = sha256_of(target)
        if remote.expected_sha256 and sha256 != remote.expected_sha256:
            raise ValueError(
                f"sha256 mismatch for {remote.name}: expected {remote.expected_sha256}, got {sha256}"
            )
        width, height = image_size(target) or (None, None)
        return DownloadResult(
            sha256=sha256,
            size=target.stat().st_size,
            ext=ext_of(remote.name),
            width=width,
            height=height,
        )

    @staticmethod
    def _fetch(url: str, target: Path) -> None:
        headers = {}
        if target.exists() and target.stat().st_size > 0:
            headers["Range"] = f"bytes={target.stat().st_size}-"
        request = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(request, timeout=60) as response:
            status = getattr(response, "status", 200)
            mode = "ab" if (status == 206 and target.exists()) else "wb"
            with open(target, mode) as fh:
                while True:
                    chunk = response.read(CHUNK)
                    if not chunk:
                        break
                    fh.write(chunk)
