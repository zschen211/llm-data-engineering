"""Download stage: resolve + fetch resources from a HuggingFace repo.

Responsibilities (network-bound, huggingface only):
  - resolve: enumerate the repo files (with subfolder/pattern filters)
  - download: single-file fetch with retry and backoff, reporting per-file
    byte progress through an ``on_event`` callback

Parallelism is provided by the caller: the Ray sync driver runs one task per
file (``ray_sync._sync_file_task``). Tests inject a fake hub via ``hub=``.
"""

from __future__ import annotations

import fnmatch
import hashlib
import shutil
import time
from collections.abc import Callable
from pathlib import Path

import huggingface_hub
from tqdm import tqdm

from .base import RemoteRef


def _matched(
    path: str, subfolder: str, allow: list[str] | None, ignore: list[str] | None
) -> bool:
    if subfolder and not path.startswith(subfolder):
        return False
    if allow and not any(fnmatch.fnmatch(path, pattern) for pattern in allow):
        return False
    return not (ignore and any(fnmatch.fnmatch(path, pattern) for pattern in ignore))


def _progress_tqdm_class(
    on_event: Callable,
    remote: str,
    min_interval_pct: float = 2.0,
    min_interval_sec: float = 1.0,
):
    """A tqdm subclass reporting byte progress through ``on_event``.

    huggingface_hub subclasses it via its own kwargs (total/unit/desc/disable);
    every ``update(n)`` may yield a progress event, throttled to either
    ``min_interval_pct`` percentage points or ``min_interval_sec`` seconds,
    whichever comes first. When ``total`` is unknown (gzip/chunked responses,
    Xet streams) events still fire — byte counts without a fraction.
    """

    class _ProgressTqdm(tqdm):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._on_event = on_event
            self._remote = remote
            self._min_pct = min_interval_pct
            self._min_sec = min_interval_sec
            self._last_pct = -1.0
            self._last_ts = time.monotonic()
            self._last_n = 0

        def update(self, n: int = 1):
            super().update(n)
            if not self._on_event:
                return
            now = time.monotonic()
            pct = self.n / self.total * 100 if self.total else None
            finished = bool(self.total) and self.n >= self.total
            if not (
                finished
                or (pct is not None and pct - self._last_pct >= self._min_pct)
                or now - self._last_ts >= self._min_sec
            ):
                return
            if pct is not None:
                self._last_pct = pct
            elapsed = max(now - self._last_ts, 1e-6)
            rate = (self.n - self._last_n) / elapsed
            self._last_ts = now
            self._last_n = self.n
            if pct is not None:
                message = f"下载中 {self.n}/{self.total} 字节 ({pct:.1f}%)"
            else:
                message = f"下载中 {self.n} 字节"
            if rate > 0:
                message += f" {rate / 1e6:.1f}MB/s"
            self._on_event(
                stage="download",
                remote=self._remote,
                message=message,
                fraction=self.n / self.total if self.total else None,
                n=self.n,
                total=self.total,
            )

    return _ProgressTqdm


class DownloadStage:
    def __init__(
        self,
        repo_id: str,
        repo_type: str = "dataset",
        subfolder: str = "",
        allow_patterns: list[str] | None = None,
        ignore_patterns: list[str] | None = None,
        attempts: int = 3,
        hub=None,
    ):
        self.repo_id = repo_id
        self.repo_type = repo_type
        self.subfolder = subfolder
        self.allow_patterns = allow_patterns
        self.ignore_patterns = ignore_patterns
        self.attempts = max(1, attempts)
        self.hub = hub  # injectable for tests; None → real huggingface_hub

    @staticmethod
    def from_source(source, hub=None) -> DownloadStage:
        params = source.params
        repo_id = params.get("repo_id")
        if not repo_id:
            raise ValueError("huggingface source requires params.repo_id")
        return DownloadStage(
            repo_id=repo_id,
            repo_type=params.get("repo_type", "dataset"),
            subfolder=params.get("subfolder", ""),
            allow_patterns=params.get("allow_patterns"),
            ignore_patterns=params.get("ignore_patterns"),
            attempts=params.get("attempts", 3),
            hub=hub,
        )

    def _hub(self):
        return self.hub or huggingface_hub

    def resolve(self) -> list[RemoteRef]:
        hub = self._hub()
        remotes: list[RemoteRef] = []
        for path in sorted(hub.list_repo_files(self.repo_id, repo_type=self.repo_type)):
            if not _matched(
                path, self.subfolder, self.allow_patterns, self.ignore_patterns
            ):
                continue
            remotes.append(
                RemoteRef(
                    id=f"hf_{hashlib.sha1(path.encode('utf-8'), usedforsecurity=False).hexdigest()[:12]}",
                    name=Path(path).name,
                    path_in_repo=path,
                    meta={
                        "repo_id": self.repo_id,
                        "repo_type": self.repo_type,
                        "path_in_repo": path,
                    },
                )
            )
        return remotes

    def download(
        self,
        remote: RemoteRef,
        target: Path,
        on_event: Callable | None = None,
        cache_dir: Path | None = None,
    ) -> Path:
        """Fetch one file to ``target`` with retry + backoff.

        ``cache_dir`` is the stable HF cache location for the whole source
        (never deleted between runs): huggingface_hub's local_dir mode skips
        files it already has (metadata commit_hash match), so completed
        downloads are reused across runs and after a crash. When None, the
        old per-task location (``target.parent/.hf_cache``) is used.
        """
        hub = self._hub()
        last_error: Exception | None = None
        tqdm_class = _progress_tqdm_class(on_event, remote.name) if on_event else None
        for attempt in range(self.attempts):
            try:
                cached = hub.hf_hub_download(
                    self.repo_id,
                    remote.path_in_repo,
                    repo_type=self.repo_type,
                    local_dir=str(cache_dir or target.parent / ".hf_cache"),
                    tqdm_class=tqdm_class,
                )
                shutil.copyfile(cached, target)
                return target
            except Exception as exc:  # transient network/cache errors
                last_error = exc
                if on_event:
                    on_event(
                        stage="download",
                        remote=remote.name,
                        message=f"第 {attempt + 1} 次尝试失败: {exc}",
                        level="error",
                    )
                if attempt < self.attempts - 1:
                    time.sleep(2**attempt)
        raise RuntimeError(
            f"download failed after {self.attempts} attempts: {last_error}"
        ) from last_error
