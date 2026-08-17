from asset_management import schema


def test_asset_types_are_closed_set():
    assert "general_image" in schema.ASSET_TYPES
    assert "chart_image" in schema.ASSET_TYPES
    assert len(schema.ASSET_TYPES) == 4


def test_jsonl_roundtrip(tmp_path):
    path = tmp_path / "out.jsonl"
    schema.write_jsonl(path, [{"a": 1}, {"a": 2}])
    assert path.read_text(encoding="utf-8").splitlines() == ['{"a": 1}', '{"a": 2}']
