"""Stage 1: source video loading.

Reads ``pexels_manifest.jsonl`` (or recovers minimal records from
``pexels_*.mp4`` filenames), re-probes each file with ffprobe and writes
``source_videos.jsonl`` with resume support.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from .io import SafeJsonlWriter, read_jsonl, scan_done_ids

_VIDEO_ID_RE = re.compile(r"pexels[-_]?(\d+)")


def parse_pexels_id(name: str) -> int | None:
    match = _VIDEO_ID_RE.search(name)
    return int(match.group(1)) if match else None


def _parse_fps(rate: str) -> float:
    if not rate:
        return 0.0
    if "/" in rate:
        num, den = rate.split("/")
        return float(num) / float(den) if float(den) else 0.0
    return float(rate)


def ffprobe(path: Path) -> dict | None:
    """Probe duration/fps/size via ffprobe; None when ffprobe is unavailable."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
            capture_output=True, text=True, timeout=60,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    try:
        data = json.loads(result.stdout)
    except ValueError:
        return None
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    fmt = data.get("format", {})
    if not video:
        return None
    return {
        "duration": round(float(fmt.get("duration", 0.0)), 3),
        "fps": round(_parse_fps(video.get("avg_frame_rate", "")), 3),
        "width": int(video.get("width", 0)),
        "height": int(video.get("height", 0)),
        "nb_frames": int(video.get("nb_frames", 0)),
        "file_size": int(fmt.get("size", 0)),
    }


def normalize_video_record(raw: dict, video_path: Path, info: dict) -> dict:
    return {
        "video_id": raw["video_id"],
        "path": str(video_path),
        "page_url": raw.get("page_url", ""),
        "author": raw.get("author_name", ""),
        "license": raw.get("license", "pexels"),
        **info,
        "status": "ok",
    }


def _iter_manifest(src_dir: Path) -> list[dict]:
    manifest_path = src_dir / "pexels_manifest.jsonl"
    if manifest_path.exists():
        records = read_jsonl(manifest_path)
        if records:
            return records
    return [
        {"saved_as": p.name, "video_id": parse_pexels_id(p.name), "page_url": ""}
        for p in sorted(src_dir.glob("pexels_*.mp4"))
        if parse_pexels_id(p.name) is not None
    ]


def load_source_videos(src_dir: Path, out_path: Path, max_samples: int | None = None) -> list[dict]:
    """Scan + probe videos into source_videos.jsonl; skip ids already written."""
    done = {str(v) for v in (scan_done_ids(out_path, "video_id") if out_path.exists() else set())}
    written = 0
    with SafeJsonlWriter(out_path) as writer:
        for raw in _iter_manifest(src_dir):
            video_id = raw["video_id"]
            if str(video_id) in done:
                continue
            if max_samples is not None and written >= max_samples:
                break
            video_path = src_dir / raw["saved_as"] if raw.get("saved_as") else src_dir / f"pexels_{video_id}.mp4"
            if not video_path.exists():
                continue
            info = ffprobe(video_path)
            if info is None:
                continue
            writer.append(normalize_video_record(raw, video_path, info))
            done.add(video_id)
            written += 1
    return read_jsonl(out_path) if out_path.exists() else []
