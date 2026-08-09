"""Download stage: resolve + fetch resources from a HuggingFace repo.

Responsibilities (network-bound, huggingface only):
  - resolve: enumerate the repo files (with subfolder/pattern filters)
  - download: single-file fetch with retry and backoff
  - fetch_all: parallel fetching across files (``workers``)

Requires the optional ``hf`` extra (huggingface_hub).
"""
from __future__ import annotations

import fnmatch
import hashlib
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .base import RemoteRef


def _require_hub():
    try:
        import huggingface_hub  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(
            "downloading requires the optional 'hf' extra (uv sync --extra hf)"
        ) from exc
    import huggingface_hub

    return huggingface_hub


def _matched(path: str, subfolder: str, allow: list[str] | None, ignore: list[str] | None) -> bool:
    if subfolder and not path.startswith(subfolder):
        return False
    if allow and not any(fnmatch.fnmatch(path, pattern) for pattern in allow):
        return False
    if ignore and any(fnmatch.fnmatch(path, pattern) for pattern in ignore):
        return False
    return True


class DownloadStage:
    def __init__(self, repo_id: str, repo_type: str = "dataset", subfolder: str = "",
                 allow_patterns: list[str] | None = None,
                 ignore_patterns: list[str] | None = None,
                 attempts: int = 3):
        self.repo_id = repo_id
        self.repo_type = repo_type
        self.subfolder = subfolder
        self.allow_patterns = allow_patterns
        self.ignore_patterns = ignore_patterns
        self.attempts = max(1, attempts)

    @staticmethod
    def from_source(source) -> "DownloadStage":
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
        )

    def resolve(self) -> list[RemoteRef]:
        hub = _require_hub()
        remotes: list[RemoteRef] = []
        for path in sorted(hub.list_repo_files(self.repo_id, repo_type=self.repo_type)):
            if not _matched(path, self.subfolder, self.allow_patterns, self.ignore_patterns):
                continue
            remotes.append(
                RemoteRef(
                    id=f"hf_{hashlib.sha1(path.encode('utf-8')).hexdigest()[:12]}",
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

    def download(self, remote: RemoteRef, target: Path) -> Path:
        """Fetch one file to ``target`` with retry + backoff."""
        hub = _require_hub()
        last_error: Exception | None = None
        for attempt in range(self.attempts):
            try:
                cached = hub.hf_hub_download(
                    self.repo_id, remote.path_in_repo,
                    repo_type=self.repo_type,
                    local_dir=str(target.parent / ".hf_cache"),
                )
                shutil.copyfile(cached, target)
                return target
            except Exception as exc:  # transient network/cache errors
                last_error = exc
                if attempt < self.attempts - 1:
                    time.sleep(2**attempt)
        raise RuntimeError(
            f"download failed after {self.attempts} attempts: {last_error}"
        ) from last_error

    def fetch_all(self, remotes: list[RemoteRef], work_root: Path,
                  workers: int = 2) -> tuple[dict[str, Path], dict[str, str]]:
        """Fetch files in parallel; return (downloaded: id->path, errors: id->msg)."""
        def work(remote: RemoteRef) -> tuple[str, Path | None, str]:
            work_dir = work_root / remote.id
            work_dir.mkdir(parents=True, exist_ok=True)
            try:
                return remote.id, self.download(remote, work_dir / remote.name), ""
            except Exception as exc:
                return remote.id, None, str(exc)

        downloaded: dict[str, Path] = {}
        errors: dict[str, str] = {}
        with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
            for remote_id, local, error in executor.map(work, remotes):
                if local is None:
                    errors[remote_id] = error
                else:
                    downloaded[remote_id] = local
        return downloaded, errors
