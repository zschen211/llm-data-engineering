import pytest

from video_generation import load
from video_generation.cli import main


def test_cli_load_sources(tmp_path, monkeypatch):
    (tmp_path / "pexels_1.mp4").write_bytes(b"fake")
    monkeypatch.setattr(
        load, "ffprobe",
        lambda path: {"duration": 5.0, "fps": 24.0, "width": 640, "height": 480, "nb_frames": 120, "file_size": 42},
    )
    out = tmp_path / "source_videos.jsonl"
    assert main(["load-sources", str(tmp_path), "--out", str(out)]) == 0
    assert out.exists()


def test_cli_motion_filter(tmp_path, monkeypatch):
    from video_generation import motion

    scenes = tmp_path / "scenes.jsonl"
    scenes.write_text('{"shot_id": "s1", "segment_path": "/x.mp4"}\n')
    monkeypatch.setattr(motion, "compute_motion_magnitude",
                        lambda path: motion.MotionStats(motion_strength=0.8, n_pairs=5))
    out = tmp_path / "motion.jsonl"
    assert main(["motion-filter", str(scenes), "--out", str(out)]) == 0
    from video_generation.io import read_jsonl

    assert read_jsonl(out)[0]["pass_motion"] is True


def test_cli_tag_shot_language(tmp_path, monkeypatch):
    import cv2
    import numpy as np

    from video_generation import tag

    shot_path = tmp_path / "x.avi"
    writer = cv2.VideoWriter(str(shot_path), cv2.VideoWriter_fourcc(*"MJPG"), 10.0, (64, 64))
    for _ in range(5):
        writer.write(np.zeros((64, 64, 3), dtype=np.uint8))
    writer.release()
    scenes = tmp_path / "scenes.jsonl"
    scenes.write_text(f'{{"shot_id": "s1", "segment_path": "{shot_path}"}}\n')
    out = tmp_path / "tags.jsonl"
    assert main(["tag-shot-language", str(scenes), "--out", str(out)]) == 0
    from video_generation.io import read_jsonl

    assert read_jsonl(out)[0]["camera_motion"] == "static"


def test_cli_build_manifest(tmp_path):
    for name in ("source_videos.jsonl", "stage2_scenes.jsonl", "stage3_motion.jsonl",
                 "stage4_aesthetic.jsonl", "stage5_captions.jsonl", "stage6_shot_language.jsonl"):
        (tmp_path / name).write_text("")
    (tmp_path / "source_videos.jsonl").write_text('{"video_id": 1, "license": "pexels"}\n')
    (tmp_path / "stage2_scenes.jsonl").write_text(
        '{"shot_id": "s1", "video_id": 1, "start_ts": 0.0, "end_ts": 1.0, "segment_path": "shots/x.mp4", "status": "ok"}\n'
    )
    (tmp_path / "stage5_captions.jsonl").write_text('{"shot_id": "s1", "caption_en": "A sunny beach."}\n')
    (tmp_path / "stage6_shot_language.jsonl").write_text('{"shot_id": "s1", "camera_motion": "static"}\n')
    out = tmp_path / "final.jsonl"
    assert main(["build-manifest", "--sources", str(tmp_path / "source_videos.jsonl"),
                 "--scenes", str(tmp_path / "stage2_scenes.jsonl"),
                 "--motion", str(tmp_path / "stage3_motion.jsonl"),
                 "--aesthetic", str(tmp_path / "stage4_aesthetic.jsonl"),
                 "--captions", str(tmp_path / "stage5_captions.jsonl"),
                 "--shot-language", str(tmp_path / "stage6_shot_language.jsonl"),
                 "--out", str(out)]) == 0
    assert out.exists()
