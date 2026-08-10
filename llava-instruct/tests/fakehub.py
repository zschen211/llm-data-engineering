"""Picklable fake huggingface_hub for Ray sync tests.

Ray tasks run in separate processes; the hub instance is shipped to workers
via cloudpickle, so it must be a plain picklable class (no threads/events).
Blocking gates are file-based: a worker polls until ``gate_path`` exists.
"""

import os
import shutil
import time
from pathlib import Path
from typing import ClassVar


class FakeHub:
    FILES: ClassVar[list[str]] = ["data/a.png", "data/b.png", "data/c.png"]

    def __init__(
        self,
        files: list[str] | None = None,
        gate_path: str | None = None,
        gated_suffix: str = "c.png",
        fail: bool = False,
        timeout: float = 30.0,
        copies: dict | None = None,
    ):
        self.files = list(files or self.FILES)
        self.gate_path = gate_path
        self.gated_suffix = gated_suffix
        self.fail = fail
        self.timeout = timeout
        self.copies = copies or {}

    def list_repo_files(self, repo_id, repo_type="dataset"):
        return self.files

    def _wait_gate(self, filename: str) -> None:
        if not self.gate_path or not filename.endswith(self.gated_suffix):
            return
        deadline = time.time() + self.timeout
        while time.time() < deadline:
            if Path(self.gate_path).exists():
                return
            time.sleep(0.02)
        raise RuntimeError(f"gate timed out for {filename}")

    def hf_hub_download(
        self, repo_id, filename, repo_type="dataset", local_dir=None, **kwargs
    ):
        if self.fail:
            raise RuntimeError("connection reset")
        self._wait_gate(filename)
        target = Path(local_dir) / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        source = self.copies.get(filename)
        if source:
            shutil.copyfile(source, target)
        else:
            target.write_bytes(b"\x89PNG\r\n\x1a\n" + filename.encode("utf-8") * 16)
        tqdm_cls = kwargs.get("tqdm_class")
        if tqdm_cls is not None:
            bar = tqdm_cls(desc="Downloading", total=100, unit="B")
            bar.update(40)
            bar.update(40)
            bar.update(20)
        return target


class FailingHub(FakeHub):
    """Every download raises; used for per-file failure accounting."""

    def __init__(self, files: list[str] | None = None):
        super().__init__(files=files, fail=True)


class CrashingHub(FakeHub):
    """Kills the worker process mid-task; Ray must retry, then give up."""

    def hf_hub_download(
        self, repo_id, filename, repo_type="dataset", local_dir=None, **kwargs
    ):
        os._exit(1)
