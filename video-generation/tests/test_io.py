import json

from video_generation import io


def test_safe_jsonl_writer_append(tmp_path):
    path = tmp_path / "out.jsonl"
    with io.SafeJsonlWriter(path) as writer:
        writer.append({"a": 1})
        writer.append({"b": 2})
    assert io.read_jsonl(path) == [{"a": 1}, {"b": 2}]


def test_repair_tail_removes_corrupted_lines(tmp_path):
    path = tmp_path / "out.jsonl"
    path.write_text('{"a": 1}\n{"broken\n{"b": 2}\n')
    removed = io.repair_tail(path)
    assert removed == 1
    assert io.read_jsonl(path) == [{"a": 1}, {"b": 2}]


def test_scan_done_ids(tmp_path):
    path = tmp_path / "out.jsonl"
    path.write_text('{"shot_id": "s1"}\n{"shot_id": "s2"}\ncorrupt\n')
    assert io.scan_done_ids(path, "shot_id") == {"s1", "s2"}
    assert io.scan_done_ids(tmp_path / "missing.jsonl", "shot_id") == set()


def test_shard_and_merge(tmp_path):
    assert io.shard_for(0, 2) == 0
    assert io.shard_for(1, 2) == 1
    shard0 = tmp_path / "s0.jsonl"
    shard1 = tmp_path / "s1.jsonl"
    shard0.write_text('{"id": 0}\n{"id": 2}\n')
    shard1.write_text('{"id": 1}\n')
    merged = tmp_path / "merged.jsonl"
    total = io.merge_shards(tmp_path, "s*.jsonl", merged)
    assert total == 3
    assert [r["id"] for r in io.read_jsonl(merged)] == [0, 2, 1]
