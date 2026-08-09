import pytest

from llava_instruct.cli import get_parser, main


def test_cli_missing_command_fails():
    with pytest.raises(SystemExit):
        get_parser().parse_args([])


def test_cli_split_end_to_end(tmp_path):
    from llava_instruct.schema import write_jsonl

    samples = [
        {
            "id": f"s{i}", "image": ["a.jpg"], "asset_type": "general_image",
            "task_type": "image_description", "source_id": "asset_1",
            "conversations": [{"from": "human", "value": "<image>\nWhat?"}, {"from": "gpt", "value": "A long enough answer."}],
            "split": "unsplit", "meta": {},
        }
        for i in range(6)
    ]
    samples_path = tmp_path / "samples.jsonl"
    write_jsonl(samples_path, samples)
    out_dir = tmp_path / "deliver"
    assert main(["split", str(samples_path), "--out-dir", str(out_dir), "--smoke", "2"]) == 0
    assert (out_dir / "train.jsonl").exists()
    assert (out_dir / "manifest.jsonl").exists()


def test_cli_qa_fails_on_bad_sample(tmp_path):
    from llava_instruct.schema import write_jsonl

    samples = [
        {
            "id": "s1", "image": ["a.jpg"], "asset_type": "general_image",
            "task_type": "image_description", "source_id": "asset_1",
            "conversations": [{"from": "human", "value": "<image>\nWhat?"}, {"from": "gpt", "value": "Yes"}],
            "split": "unsplit", "meta": {},
        }
    ]
    path = tmp_path / "samples.jsonl"
    write_jsonl(path, samples)
    assert main(["qa", str(path), "--out", str(tmp_path / "out.jsonl"), "--report", str(tmp_path / "report.md")]) == 1
