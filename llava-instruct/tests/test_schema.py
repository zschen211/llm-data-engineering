import pytest

from llava_instruct import schema
from llava_instruct.templates import build_conversations


def test_build_conversations_single_image():
    convs = build_conversations("What is this?", "A dog.", n_images=1)
    assert convs[0]["from"] == "human"
    assert convs[0]["value"].startswith("<image>")
    assert convs[-1] == {"from": "gpt", "value": "A dog."}


def test_build_conversations_multi_image_interleaves_tokens():
    convs = build_conversations("Compare", "Same.", n_images=2)
    assert convs[0]["value"].count("<image>") == 2


def test_validate_sample_ok():
    sample = {
        "id": "s1", "image": ["a.jpg"], "asset_type": "general_image",
        "task_type": "image_description", "source_id": "asset_1",
        "conversations": build_conversations("Q?", "An answer."),
        "split": "train", "meta": {},
    }
    assert schema.validate_sample(sample) == []


def test_validate_sample_errors():
    sample = {
        "id": "s1", "image": ["a.jpg"], "asset_type": "nope",
        "task_type": "image_description", "source_id": "asset_1",
        "conversations": [{"from": "human", "value": "Q?"}],
        "split": "oops", "meta": {},
    }
    errors = schema.validate_sample(sample)
    assert any("asset_type" in e for e in errors)
    assert any("split" in e for e in errors)
    assert any("conversation" in e for e in errors)


def test_validate_bbox_and_clamp():
    assert schema.validate_bbox([0, 0, 10, 10]) is None
    assert schema.validate_bbox([0, 0, -1, 10]) is not None
    assert schema.validate_bbox([1, 2]) is not None
    assert schema.clamp_bbox([950, 950, 100, 100], 1000, 1000) == [950, 950, 50, 50]
    assert schema.clamp_bbox([-5, -5, 10, 10], 100, 100) == [0, 0, 10, 10]


def test_jsonl_roundtrip(tmp_path):
    path = tmp_path / "out.jsonl"
    schema.write_jsonl(path, [{"a": 1}, {"a": 2}])
    assert schema.read_jsonl(path) == [{"a": 1}, {"a": 2}]
