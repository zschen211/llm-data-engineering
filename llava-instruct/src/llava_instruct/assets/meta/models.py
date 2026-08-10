"""Data models for the asset layer (mirror the SQLite schema)."""

from __future__ import annotations

from dataclasses import dataclass, field

ASSET_STATUS = ("pending", "downloading", "ready", "failed")


@dataclass
class Source:
    id: str
    name: str
    kind: str
    url: str = ""
    license: str = ""
    description: str = ""
    params: dict = field(default_factory=dict)
    enabled: bool = True
    created_at: str = ""
    updated_at: str = ""


@dataclass
class Asset:
    id: str
    source_id: str
    name: str
    asset_type: str = ""
    object_key: str = ""
    sha256: str = ""
    size: int = 0
    width: int | None = None
    height: int | None = None
    status: str = "pending"
    current_version: int = 1
    meta: dict = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""
    tags: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class AssetVersion:
    asset_id: str
    version: int
    sha256: str
    object_key: str
    change_note: str
    created_at: str


@dataclass
class Tag:
    id: str
    name: str
    group: str = "default"


@dataclass
class Download:
    id: int
    asset_id: str
    downloader: str
    status: str
    error: str = ""
    attempts: int = 0
    started_at: str = ""
    finished_at: str = ""


@dataclass
class Snapshot:
    id: str
    manifest_sha1: str
    asset_count: int
    created_at: str
