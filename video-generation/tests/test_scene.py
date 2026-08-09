import pytest

from video_generation import scene


class _TC:
    def __init__(self, seconds):
        self._seconds = seconds

    def get_seconds(self):
        return self._seconds

    def __sub__(self, other):
        return _TC(self._seconds - other._seconds)


def test_split_one_video_calls_splitter(tmp_path, monkeypatch):
    record = {"path": "/v.mp4", "video_id": 42}
    monkeypatch.setattr(
        "video_generation.scene.open_video",
        lambda path: type("V", (), {"base_timecode": _TC(0), "duration": _TC(10)})(),
    )
    monkeypatch.setattr("video_generation.scene.SceneManager", _FakeSceneManager)
    calls = {"split": 0}

    def fake_split(path, scenes, out_pattern):
        calls["split"] += 1
        shot_dir = tmp_path / "shots" / "pexels_42"
        shot_dir.mkdir(parents=True, exist_ok=True)
        (shot_dir / "shot_1.mp4").write_bytes(b"x")
        (shot_dir / "shot_2.mp4").write_bytes(b"x")

    monkeypatch.setattr(scene, "split_video_ffmpeg", fake_split)
    shots = scene.split_one_video(record, tmp_path, threshold=27.0, min_shot_len=1.0)
    assert calls["split"] == 1
    assert len(shots) == 2
    assert shots[0]["shot_id"] == "pexels_42_shot_0000"
    assert shots[1]["shot_id"] == "pexels_42_shot_0001"
    assert shots[1]["start_ts"] == 4.0
    assert shots[1]["end_ts"] == 10.0


class _FakeSceneManager:
    def __init__(self):
        self.detector = None

    def add_detector(self, detector):
        self.detector = detector

    def detect_scenes(self, video, show_progress=True):
        self.scenes = [(_TC(0), _TC(4)), (_TC(4), _TC(10))]

    def get_scene_list(self):
        return self.scenes


def test_run_scene_detect_resumes(tmp_path, monkeypatch):
    from video_generation.io import SafeJsonlWriter, read_jsonl

    source = tmp_path / "source.jsonl"
    source.write_text('{"video_id": 1, "path": "/v.mp4"}\n{"video_id": 2, "path": "/w.mp4"}\n')
    monkeypatch.setattr(
        scene, "split_one_video",
        lambda record, root, threshold=27.0, min_shot_len=1.0: [
            {"shot_id": f"pexels_{record['video_id']}_shot_0000", "video_id": record["video_id"],
             "start_ts": 0.0, "end_ts": 1.0, "segment_path": "/shots/x.mp4", "status": "ok"}
        ],
    )
    out = tmp_path / "stage2_scenes.jsonl"
    first = scene.run_scene_detect(source, tmp_path, out)
    assert len(first) == 2
    with SafeJsonlWriter(out) as w:
        w.append({"shot_id": "pexels_2_shot_0000", "video_id": 2, "start_ts": 0.0,
                  "end_ts": 1.0, "segment_path": "/shots/x.mp4", "status": "ok"})

    def should_not_run(*args, **kwargs):
        raise AssertionError("should not run")

    monkeypatch.setattr(scene, "split_one_video", should_not_run)
    second = scene.run_scene_detect(source, tmp_path, out)
    assert second[-1]["shot_id"] == "pexels_2_shot_0000"
