import json

from PIL import Image

from llava_instruct.cli import main


def _images(root):
    root.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (10, 10), "red").save(root / "photo_red.png")
    Image.new("RGB", (10, 10), "gray").save(root / "doc_page1.png")
    Image.new("RGB", (10, 10), "white").save(root / "chart_rev.png")


def test_asset_init(tmp_path):
    assert main(["asset", "init", "--data-dir", str(tmp_path / "data")]) == 0
    assert (tmp_path / "data" / "assets.db").exists()


def test_asset_source_crud(tmp_path):
    assert main(["asset", "source", "add", "--name", "coco", "--kind", "huggingface",
                 "--url", "https://huggingface.co",
                 "--params", '{"repo_id": "org/coco"}', "--data-dir", str(tmp_path / "data")]) == 0
    assert main(["asset", "source", "list", "--data-dir", str(tmp_path / "data")]) == 0


def test_asset_import_ls_tag_snapshot_materialize(tmp_path, capsys):
    src = tmp_path / "imgs"
    _images(src)
    data_dir = tmp_path / "data"
    assert main(["asset", "import", str(src), "--source-name", "test",
                 "--data-dir", str(data_dir), "--out", str(tmp_path / "assets.jsonl"),
                 "--per-type", "10"]) == 0
    assert main(["asset", "ls", "--data-dir", str(data_dir)]) == 0
    out = capsys.readouterr().out
    assert "photo_red.png" in out

    # tag the chart asset and filter by tag
    assert main(["asset", "ls", "--type", "chart_image", "--data-dir", str(data_dir), "--json"]) == 0
    chart = json.loads(capsys.readouterr().out)[0]
    assert main(["asset", "tag", "add", chart["id"], "chart", "--group", "task",
                 "--data-dir", str(data_dir)]) == 0
    assert main(["asset", "tag", "list", "--data-dir", str(data_dir)]) == 0
    assert "task=chart" in capsys.readouterr().out

    assert main(["asset", "version", "snapshot", "--name", "v1", "--data-dir", str(data_dir)]) == 0
    assert "snapshot: v1" in capsys.readouterr().out
    assert main(["asset", "version", "snapshot-list", "--data-dir", str(data_dir)]) == 0

    out_dir = tmp_path / "pool2"
    assert main(["asset", "materialize", str(out_dir), "--tag", "task=chart",
                 "--data-dir", str(data_dir)]) == 0
    assert len(list(out_dir.iterdir())) == 1


def test_asset_ls_filters(tmp_path, capsys):
    src = tmp_path / "imgs"
    _images(src)
    data_dir = tmp_path / "data"
    main(["asset", "import", str(src), "--data-dir", str(data_dir)])
    capsys.readouterr()  # discard import output
    main(["asset", "ls", "--status", "ready", "--data-dir", str(data_dir), "--json"])
    assets = json.loads(capsys.readouterr().out)
    assert len(assets) == 3


def test_asset_sync_unknown_source(tmp_path):
    result = main(["asset", "sync", "nope", "--data-dir", str(tmp_path / "data")])
    assert result == 1
