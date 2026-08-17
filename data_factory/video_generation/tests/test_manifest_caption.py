from video_generation import caption, manifest
from video_generation.io import read_jsonl


def test_sample_frames_in_time_order():
    paths = [f"f{i:03d}.jpg" for i in range(20)]
    selected = caption.sample_frames_in_time_order(paths, k=8)
    assert len(selected) == 8
    assert selected[0] == "f000.jpg"
    assert selected[-1] == "f019.jpg"
    assert selected == sorted(selected)


def test_sample_frames_short_shot():
    paths = ["a.jpg", "b.jpg"]
    assert caption.sample_frames_in_time_order(paths, k=8) == paths


def test_caption_prompt_no_frame_enumeration():
    assert "Do not enumerate frames" in caption.CAPTION_PROMPT


def test_build_manifest_joins_on_shot_id(tmp_path):
    sources = tmp_path / "source.jsonl"
    scenes = tmp_path / "scenes.jsonl"
    motion_path = tmp_path / "motion.jsonl"
    aesthetic_path = tmp_path / "aesthetic.jsonl"
    captions_path = tmp_path / "captions.jsonl"
    shot_lang = tmp_path / "shot_language.jsonl"
    out = tmp_path / "final.jsonl"

    sources.write_text(
        '{"video_id": 1, "license": "pexels", "page_url": "https://x"}\n'
    )
    scenes.write_text(
        '{"shot_id": "pexels_1_shot_0000", "video_id": 1, "start_ts": 0.0, "end_ts": 2.0, '
        '"segment_path": "shots/pexels_1/shot_0000.mp4", "status": "ok"}\n'
    )
    motion_path.write_text(
        '{"shot_id": "pexels_1_shot_0000", "motion_strength": 1.3, "pass_motion": true}\n'
    )
    aesthetic_path.write_text(
        '{"shot_id": "pexels_1_shot_0000", "aesthetic_score": 6.2, "pass_aesthetic": true}\n'
    )
    captions_path.write_text(
        '{"shot_id": "pexels_1_shot_0000", "caption_en": "A person walks down a street.", "n_words": 8}\n'
    )
    shot_lang.write_text(
        '{"shot_id": "pexels_1_shot_0000", "camera_motion": "pan_right", "status": "ok"}\n'
    )

    samples = manifest.build_manifest(
        {
            "source": sources,
            "scenes": scenes,
            "motion": motion_path,
            "aesthetic": aesthetic_path,
            "captions": captions_path,
            "shot_language": shot_lang,
        },
        out,
    )
    assert len(samples) == 1
    sample = samples[0]
    assert sample["shot_id"] == "pexels_1_shot_0000"
    assert sample["source"]["license"] == "pexels"
    assert sample["filters"]["pass_motion"] is True
    assert sample["filters"]["pass_aesthetic"] is True
    assert sample["caption"]["n_words"] == 8
    assert sample["shot_language"]["camera_motion"] == "pan_right"
    assert read_jsonl(out) == samples


def test_validate_manifest_flags_missing_fields():
    bad = [
        {
            "shot_id": "s1",
            "source": {},
            "video": {"segment_path": ""},
            "filters": {},
            "caption": {},
            "shot_language": {"camera_motion": None},
        }
    ]
    errors = manifest.validate_manifest(bad)
    assert any("segment_path" in e for e in errors)
    assert any("caption_en" in e for e in errors)
    assert any("license" in e for e in errors)
