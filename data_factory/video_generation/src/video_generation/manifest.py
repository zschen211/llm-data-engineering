"""Final manifest: join all stages on shot_id and validate the sample contract."""

from __future__ import annotations

from pathlib import Path

from .io import SafeJsonlWriter, read_jsonl

REQUIRED_GROUPS = ("source", "video", "filters", "caption", "shot_language")


def build_manifest(stage_files: dict[str, Path], out_path: Path) -> list[dict]:
    """Merge stage JSONLs keyed by shot_id into final T2V samples.

    ``stage_files`` maps stage names ("source", "scenes", "motion",
    "aesthetic", "captions", "shot_language") to their JSONL paths.
    """
    stages: dict[str, dict[str, dict]] = {}
    for name, path in stage_files.items():
        records = read_jsonl(path) if path.exists() else []
        stages[name] = {str(r.get("shot_id", r.get("video_id"))): r for r in records}

    scenes = stages.get("scenes", {})
    samples: list[dict] = []
    for shot_id, scene in scenes.items():
        sample = {
            "shot_id": shot_id,
            "source": stages.get("source", {}).get(str(scene.get("video_id")), {}),
            "video": {
                "segment_path": scene.get("segment_path", ""),
                "start_ts": scene.get("start_ts"),
                "end_ts": scene.get("end_ts"),
            },
            "filters": {
                **(stages.get("motion", {}).get(shot_id, {})),
                **(stages.get("aesthetic", {}).get(shot_id, {})),
            },
            "caption": stages.get("captions", {}).get(shot_id, {}),
            "shot_language": stages.get("shot_language", {}).get(shot_id, {}),
            "audit": {
                "status": scene.get("status", "ok"),
                "error": scene.get("error", ""),
            },
        }
        samples.append(sample)

    with SafeJsonlWriter(out_path) as writer:
        for sample in samples:
            writer.append(sample)
    return samples


def validate_manifest(samples: list[dict]) -> list[str]:
    """Structural checks: sample contract fields and per-group presence."""
    errors: list[str] = []
    for sample in samples:
        if not sample["shot_id"]:
            errors.append("empty shot_id")
        if not sample["video"].get("segment_path"):
            errors.append(f"{sample['shot_id']}: missing segment_path")
        if not sample["caption"].get("caption_en"):
            errors.append(f"{sample['shot_id']}: missing caption_en")
        if not sample["source"].get("license"):
            errors.append(f"{sample['shot_id']}: missing license")
        if sample["shot_language"].get("camera_motion") is None:
            errors.append(f"{sample['shot_id']}: missing camera_motion")
    return errors
