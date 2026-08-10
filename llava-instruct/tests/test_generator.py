from llava_instruct import generator, templates
from llava_instruct.schema import write_jsonl


def _asset(asset_type, path="img.png"):
    return {"id": "asset_1", "path": path, "asset_type": asset_type, "name": "img.png"}


def test_general_image_tasks():
    cap = {"id": "asset_1", "caption": "A busy street.", "subjects": {"cars": 3}, "place": "outdoor", "reason": "sky visible"}
    bbox = {"id": "asset_1", "bbox": [10, 10, 50, 50], "label": "car", "width": 100, "height": 100}
    samples = generator.build_samples_for_asset(_asset("general_image"), captions={"asset_1": cap}, bbox={"asset_1": bbox})
    tasks = {s["task_type"] for s in samples}
    assert {"image_description", "counting_vqa", "region_grounding"} <= tasks
    grounding = next(s for s in samples if s["task_type"] == "region_grounding" and "car" in s["conversations"][0]["value"])
    assert grounding["meta"]["bbox"] == [10, 10, 50, 50]


def test_document_image_tasks():
    ocr = {"id": "asset_1", "ocr_text": "Total revenue 100M."}
    cap = {"id": "asset_1", "doc_qa": [{"question": "What is revenue?", "answer": "100M."}]}
    samples = generator.build_samples_for_asset(_asset("document_image"), ocr={"asset_1": ocr}, captions={"asset_1": cap})
    assert {s["task_type"] for s in samples} == {"ocr_summary", "document_qa"}
    assert "100M." in samples[0]["conversations"][-1]["value"]


def test_chart_image_tasks():
    cap = {"id": "asset_1", "chart": {"kind": "bar chart", "trend": "Sales grew steadily.", "compared": "regions", "comparison": "Asia leads."}}
    samples = generator.build_samples_for_asset(_asset("chart_image"), captions={"asset_1": cap})
    assert {s["task_type"] for s in samples} == {"chart_reading", "chart_comparison"}


def test_interleaved_pair_task():
    pair = {"id": "asset_1", "question": "Compare the two images.", "answer": "Both show sunsets.", "image_a": "a.jpg", "image_b": "b.jpg"}
    samples = generator.build_samples_for_asset(_asset("interleaved_pair"), pairs={"asset_1": pair})
    assert samples[0]["task_type"] == "multi_image_comparison"
    assert samples[0]["image"] == ["a.jpg", "b.jpg"]
    assert samples[0]["conversations"][0]["value"].count("<image>") == 2


def test_generate_samples_pipeline(tmp_path):
    assets_path = tmp_path / "assets.jsonl"
    out_path = tmp_path / "samples.jsonl"
    pool = [{"id": "asset_1", "path": "img.png", "asset_type": "general_image", "name": "img.png"}]
    write_jsonl(assets_path, pool)
    cap_path = tmp_path / "captions.jsonl"
    write_jsonl(cap_path, [{"id": "asset_1", "caption": "A cat on a sofa.", "subjects": {"cats": 1}}])
    samples = generator.generate_samples(pool, out_path, captions_path=cap_path)
    assert len(samples) == 2
    assert all(templates.IMAGE_TOKEN in s["conversations"][0]["value"] for s in samples)
