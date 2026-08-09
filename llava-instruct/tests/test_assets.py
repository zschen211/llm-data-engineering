from llava_instruct import assets


def test_classify_image_heuristics(tmp_path):
    (tmp_path / "doc_page1.png").write_bytes(b"x")
    (tmp_path / "chart_revenue.jpg").write_bytes(b"x")
    (tmp_path / "photo.png").write_bytes(b"x")
    (tmp_path / "notes.txt").write_bytes(b"x")
    records = assets.scan_image_dir(tmp_path)
    by_name = {r["name"]: r["asset_type"] for r in records}
    assert by_name["doc_page1.png"] == "document_image"
    assert by_name["chart_revenue.jpg"] == "chart_image"
    assert by_name["photo.png"] == "general_image"
    assert len(records) == 3


def test_classify_image_explicit_labels(tmp_path):
    assert assets.classify_image(tmp_path / "photo.png", {"photo.png": "chart_image"}) == "chart_image"
    assert assets.classify_image(tmp_path / "photo.png", {"photo.png": "bogus"}) == "general_image"


def test_balance_assets_keeps_per_type():
    records = [{"id": f"a{i}", "asset_type": t} for i in range(60) for t in ("general_image", "chart_image")]
    balanced = assets.balance_assets(records, per_type=29)
    types = [r["asset_type"] for r in balanced]
    assert types.count("general_image") == 29
    assert types.count("chart_image") == 29


def test_build_asset_pool_writes_manifest(tmp_path):
    src = tmp_path / "imgs"
    src.mkdir()
    (src / "doc_1.png").write_bytes(b"x")
    (src / "photo.png").write_bytes(b"x")
    out = tmp_path / "assets.jsonl"
    records = assets.build_asset_pool(src, out, per_type=10)
    assert len(records) == 2
    assert assets.load_asset_pool(out) == records
