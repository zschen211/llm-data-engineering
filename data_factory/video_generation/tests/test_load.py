from video_generation import load


def test_parse_pexels_id():
    assert load.parse_pexels_id("pexels_12345.mp4") == 12345
    assert load.parse_pexels_id("pexels-9876.mp4") == 9876
    assert load.parse_pexels_id("movie.mp4") is None


def test_normalize_video_record():
    raw = {
        "video_id": 1,
        "page_url": "https://x",
        "author_name": "a",
        "license": "pexels",
    }
    info = {
        "duration": 10.5,
        "fps": 25.0,
        "width": 1920,
        "height": 1080,
        "nb_frames": 262,
        "file_size": 100,
    }
    record = load.normalize_video_record(raw, "/tmp/v.mp4", info)
    assert record["video_id"] == 1
    assert record["duration"] == 10.5
    assert record["status"] == "ok"


def test_load_source_videos_resumes(tmp_path, monkeypatch):
    (tmp_path / "pexels_1.mp4").write_bytes(b"fake")
    (tmp_path / "pexels_2.mp4").write_bytes(b"fake")
    info = {
        "duration": 5.0,
        "fps": 24.0,
        "width": 640,
        "height": 480,
        "nb_frames": 120,
        "file_size": 42,
    }
    calls = {"n": 0}

    def fake_ffprobe(path):
        calls["n"] += 1
        return info

    monkeypatch.setattr(load, "ffprobe", fake_ffprobe)
    out = tmp_path / "source_videos.jsonl"
    first = load.load_source_videos(tmp_path, out)
    assert len(first) == 2
    calls["n"] = 0
    second = load.load_source_videos(tmp_path, out)
    assert len(second) == 2
    assert calls["n"] == 0  # resume: nothing re-probed


def test_load_source_videos_manifest_preferred(tmp_path, monkeypatch):
    (tmp_path / "pexels_1.mp4").write_bytes(b"fake")
    (tmp_path / "pexels_manifest.jsonl").write_text(
        '{"video_id": 1, "saved_as": "pexels_1.mp4", "page_url": "https://example.com", "author_name": "bob", "license": "pexels"}\n'
    )
    monkeypatch.setattr(
        load,
        "ffprobe",
        lambda path: {
            "duration": 5.0,
            "fps": 24.0,
            "width": 640,
            "height": 480,
            "nb_frames": 120,
            "file_size": 42,
        },
    )
    out = tmp_path / "source_videos.jsonl"
    records = load.load_source_videos(tmp_path, out)
    assert records[0]["author"] == "bob"
    assert records[0]["page_url"] == "https://example.com"


def test_ffprobe_missing_binary(tmp_path, monkeypatch):
    def boom(cmd, **kwargs):
        raise FileNotFoundError("ffprobe")

    monkeypatch.setattr("subprocess.run", boom)

    assert load.ffprobe(tmp_path / "x.mp4") is None
