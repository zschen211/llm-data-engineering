from asset_management import assets


def test_classify_image_heuristics(tmp_path):
    assert assets.classify_image(tmp_path / "doc_page1.png") == "document_image"
    assert assets.classify_image(tmp_path / "chart_revenue.jpg") == "chart_image"
    assert assets.classify_image(tmp_path / "photo.png") == "general_image"


def test_classify_image_explicit_labels(tmp_path):
    assert (
        assets.classify_image(tmp_path / "photo.png", {"photo.png": "chart_image"})
        == "chart_image"
    )
    assert (
        assets.classify_image(tmp_path / "photo.png", {"photo.png": "bogus"})
        == "general_image"
    )


def test_balance_assets_keeps_per_type():
    records = [
        {"id": f"a{i}", "asset_type": t}
        for i in range(60)
        for t in ("general_image", "chart_image")
    ]
    balanced = assets.balance_assets(records, per_type=29)
    types = [r["asset_type"] for r in balanced]
    assert types.count("general_image") == 29
    assert types.count("chart_image") == 29
