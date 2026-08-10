"""Stage 2: shot segmentation with PySceneDetect + ffmpeg split."""

from __future__ import annotations

from pathlib import Path

from scenedetect import ContentDetector, SceneManager, open_video

try:
    from scenedetect import split_video_ffmpeg  # scenedetect >= 0.7
except ImportError:  # pragma: no cover
    from scenedetect.video_splitter import split_video_ffmpeg

from .io import SafeJsonlWriter, read_jsonl, scan_done_ids


def split_one_video(
    record: dict, out_root: Path, threshold: float = 27.0, min_shot_len: float = 1.0
) -> list[dict]:
    """Detect scene boundaries and split one video into shot-level clips."""
    video = open_video(record["path"])
    manager = SceneManager()
    manager.add_detector(ContentDetector(threshold=threshold))
    manager.detect_scenes(video=video, show_progress=False)

    scenes = manager.get_scene_list()
    if not scenes:
        scenes = [(video.base_timecode, video.duration)]

    kept = [s for s in scenes if _seconds(s[1] - s[0]) >= min_shot_len]
    if not kept:
        return []

    shot_dir = out_root / "shots" / f"pexels_{record['video_id']}"
    shot_dir.mkdir(parents=True, exist_ok=True)
    split_video_ffmpeg(record["path"], kept, str(shot_dir / "shot_$SCENE_NUMBER.mp4"))

    clips = sorted(shot_dir.glob("*.mp4"))
    records = []
    for idx, (scene, clip_path) in enumerate(zip(kept, clips)):
        records.append(
            {
                "shot_id": f"pexels_{record['video_id']}_shot_{idx:04d}",
                "video_id": record["video_id"],
                "start_ts": round(_seconds(scene[0]), 3),
                "end_ts": round(_seconds(scene[1]), 3),
                "segment_path": str(clip_path),
                "status": "ok",
            }
        )
    return records


def _seconds(td) -> float:
    return td.get_seconds()


def run_scene_detect(
    source_videos_path: Path,
    out_root: Path,
    out_path: Path,
    threshold: float = 27.0,
    min_shot_len: float = 1.0,
    max_samples: int | None = None,
) -> list[dict]:
    """Process all videos into stage2_scenes.jsonl with resume support.

    Videos whose shots already appear in the output are skipped entirely.
    """
    videos = read_jsonl(source_videos_path)
    done = scan_done_ids(out_path, "shot_id") if out_path.exists() else set()
    done_videos = {_video_id_from_shot(sid) for sid in done}
    processed = 0
    with SafeJsonlWriter(out_path) as writer:
        for record in videos:
            if max_samples is not None and processed >= max_samples:
                break
            if str(record["video_id"]) in done_videos:
                continue
            shots = split_one_video(
                record, out_root, threshold=threshold, min_shot_len=min_shot_len
            )
            for shot in shots:
                if shot["shot_id"] in done:
                    continue
                writer.append(shot)
                done.add(shot["shot_id"])
            processed += 1
    return read_jsonl(out_path) if out_path.exists() else []


def _video_id_from_shot(shot_id: str) -> str:
    return shot_id.split("_shot_")[0].replace("pexels_", "")
