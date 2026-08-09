"""Downloader registry: factories keyed by source kind.

Adding a new data source type = implement a BaseDownloader subclass and
decorate it with @register("kind"); nothing else changes.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from .downloaders.base import BaseDownloader

REGISTRY: dict[str, type] = {}


def register(kind: str | None = None):
    def deco(cls):
        key = kind or getattr(cls, "kind", "")
        if not key:
            raise ValueError(f"downloader {cls.__name__} must define a kind")
        REGISTRY[key] = cls
        return cls

    return deco


def get_downloader(kind: str):
    if kind not in REGISTRY:
        raise ValueError(
            f"no downloader registered for kind {kind!r}; available: {sorted(REGISTRY)}"
        )
    return REGISTRY[kind]()


def available_kinds() -> list[str]:
    return sorted(REGISTRY)
