import cv2
import numpy as np
import pytest

from video_generation import motion, tag


def _make_video(path, frames=12, moving=True):
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 10.0, (64, 64))
    for i in range(frames):
        frame = np.zeros((64, 64, 3), dtype=np.uint8)
        x = i * 4 if moving else 30
        cv2.circle(frame, (x, 32), 8, (255, 255, 255), -1)
        writer.write(frame)
    writer.release()


def test_compute_motion_magnitude_moving(tmp_path):
    path = tmp_path / "moving.avi"
    _make_video(path, moving=True)
    stats = motion.compute_motion_magnitude(str(path), proxy_wh=(32, 32), stride=1, max_pairs=60)
    assert stats.n_pairs > 0
    assert stats.motion_strength > 0.05


def test_compute_motion_magnitude_static(tmp_path):
    path = tmp_path / "static.avi"
    _make_video(path, moving=False)
    stats = motion.compute_motion_magnitude(str(path), proxy_wh=(32, 32), stride=1, max_pairs=60)
    assert stats.motion_strength < 0.05


def test_motion_filter_one_pass_and_fail(tmp_path, monkeypatch):
    shot = {"shot_id": "s1", "segment_path": str(tmp_path / "x.mp4")}
    monkeypatch.setattr(motion, "compute_motion_magnitude",
                        lambda path: motion.MotionStats(motion_strength=1.2, n_pairs=10))
    assert motion.motion_filter_one(shot)["pass_motion"] is True
    monkeypatch.setattr(motion, "compute_motion_magnitude",
                        lambda path: motion.MotionStats(motion_strength=0.1, n_pairs=10))
    assert motion.motion_filter_one(shot)["pass_motion"] is False


def test_motion_filter_one_error_record(tmp_path, monkeypatch):
    shot = {"shot_id": "s1", "segment_path": str(tmp_path / "missing.mp4")}
    def boom(path):
        raise ValueError("cannot open")

    monkeypatch.setattr(motion, "compute_motion_magnitude", boom)
    record = motion.motion_filter_one(shot)
    assert record["status"] == "error"
    assert record["pass_motion"] is False


def test_flow_statistics_and_camera_motion(tmp_path):
    path = tmp_path / "pan.avi"
    _make_video(path, moving=True)
    stats = tag.flow_statistics(str(path), proxy_wh=(32, 32))
    assert stats["mean_magnitude"] > 0
    kind = tag.summarize_camera_motion(stats)
    assert kind in {"static", "pan", "tilt", "jitter", "complex"}


def test_summarize_camera_motion_static():
    assert tag.summarize_camera_motion({"mean_magnitude": 0.0, "std_magnitude": 0.0}) == "static"


def test_sanitize_and_coerce_to_vocab():
    raw = {"shot_size": "Close Up", "camera_angle": "eye level", "composition": "rule-of-thirds",
           "lighting": "not a real value", "style": "cinematic"}
    cleaned = tag.sanitize_and_coerce_to_vocab(raw)
    assert cleaned["shot_size"] == "close_up"
    assert cleaned["camera_angle"] == "eye_level"
    assert cleaned["composition"] == "rule_of_thirds"
    assert cleaned["lighting"] == "unknown"
    assert cleaned["style"] == "cinematic"


def test_tag_shot_language_without_vlm(tmp_path):
    path = tmp_path / "x.avi"
    _make_video(path, moving=False)
    record = tag.tag_shot_language("shot_1", str(path), [str(path)], vlm_fn=None)
    assert record["shot_id"] == "shot_1"
    assert record["vlm_tags"]["shot_size"] == "unknown"
    assert record["camera_motion"] in {"static", "pan", "tilt", "jitter", "complex"}


def test_tag_shot_language_with_vlm(tmp_path):
    path = tmp_path / "x.avi"
    _make_video(path, moving=False)

    def fake_vlm(frame_paths, allowed_vocab):
        return {"shot_size": "wide", "camera_angle": "overhead", "composition": "centered",
                "lighting": "natural", "style": "documentary"}

    record = tag.tag_shot_language("shot_1", str(path), [str(path)], vlm_fn=fake_vlm)
    assert record["vlm_tags"]["shot_size"] == "wide"
    assert record["vlm_tags"]["style"] == "documentary"
