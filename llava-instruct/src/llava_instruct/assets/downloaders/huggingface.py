"""HuggingFace dataset downloader (requires the optional ``hf`` extra).

Source params:
  repo_id: "liuhaotian/LLaVA-CC3M-Pretrain-595K"
  repo_type: "dataset" (default) | "model"
  subfolder: ""            # only files under this prefix
  allow_patterns: [...]    # optional fnmatch include filters
  ignore_patterns: [...]   # optional fnmatch exclude filters
"""
from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from ..models import Source
from ..registry import register
from .base import BaseDownloader, DownloadResult, RemoteAsset, ext_of, image_size, sha256_of


def _require_hub():
    try:
        import huggingface_hub  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(
            "the huggingface downloader requires the optional 'hf' extra "
            "(uv sync --extra hf)"
        ) from exc
    import huggingface_hub

    return huggingface_hub


def _matched(path: str, subfolder: str, allow: list[str] | None, ignore: list[str] | None) -> bool:
    import fnmatch

    if subfolder and not path.startswith(subfolder):
        return False
    if allow and not any(fnmatch.fnmatch(path, pattern) for pattern in allow):
        return False
    if ignore and any(fnmatch.fnmatch(path, pattern) for pattern in ignore):
        return False
    return True


@register("huggingface")
class HfDownloader(BaseDownloader):
    kind = "huggingface"

    def _params(self, source: Source) -> tuple[str, str, dict]:
        repo_id = source.params.get("repo_id")
        if not repo_id:
            raise ValueError("huggingface source requires params.repo_id")
        return repo_id, source.params.get("repo_type", "dataset"), source.params

    def resolve(self, source: Source) -> list[RemoteAsset]:
        hub = _require_hub()
        repo_id, repo_type, params = self._params(source)
        files = hub.list_repo_files(repo_id, repo_type=repo_type)
        remotes = []
        for path in sorted(files):
            if not _matched(path, params.get("subfolder", ""),
                            params.get("allow_patterns"), params.get("ignore_patterns")):
                continue
            remotes.append(
                RemoteAsset(
                    id=f"hf_{hashlib.sha1(path.encode('utf-8')).hexdigest()[:12]}",
                    name=Path(path).name,
                    url=path,
                    meta={"path_in_repo": path, "repo_id": repo_id, "repo_type": repo_type},
                )
            )
        return remotes

    def download(self, remote: RemoteAsset, target: Path) -> DownloadResult:
        hub = _require_hub()
        repo_id = remote.meta["repo_id"]
        repo_type = remote.meta.get("repo_type", "dataset")
        cached = hub.hf_hub_download(
            repo_id, remote.meta["path_in_repo"], repo_type=repo_type,
            local_dir=str(target.parent / ".hf_cache"),
        )
        shutil.copyfile(cached, target)
        width, height = image_size(target) or (None, None)
        return DownloadResult(
            sha256=sha256_of(target),
            size=target.stat().st_size,
            ext=ext_of(remote.name),
            width=width,
            height=height,
            meta=remote.meta,
        )
