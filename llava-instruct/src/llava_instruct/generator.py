"""Supervision construction: from assets + evidence files to LLaVA samples.

Evidence files are JSONL keyed by asset id:
  captions.jsonl : {"id": ..., "caption": ..., "subjects": {label: count}, "place": ..., "reason": ...}
  ocr.jsonl      : {"id": ..., "ocr_text": ...}
  bbox.jsonl     : {"id": ..., "bbox": [x,y,w,h], "label": ..., "width": ..., "height": ...}
  pairs.jsonl    : {"id": ..., "image_a": ..., "image_b": ..., "question": ..., "answer": ...}
"""
from __future__ import annotations

from . import templates
from .schema import TASK_TYPES, read_jsonl, write_jsonl

GENERAL_TASKS = ("image_description", "counting_vqa", "region_grounding")
DOCUMENT_TASKS = ("ocr_summary", "document_qa")
CHART_TASKS = ("chart_reading", "chart_comparison")
PAIR_TASKS = ("multi_image_comparison",)


def _load_evidence(path) -> dict[str, dict]:
    if path is None:
        return {}
    return {row["id"]: row for row in read_jsonl(path)}


def _task_types_for(asset_type: str) -> tuple[str, ...]:
    if asset_type == "document_image":
        return DOCUMENT_TASKS
    if asset_type == "chart_image":
        return CHART_TASKS
    if asset_type == "interleaved_pair":
        return PAIR_TASKS
    return GENERAL_TASKS


def build_samples_for_asset(
    asset: dict,
    captions: dict[str, dict] | None = None,
    ocr: dict[str, dict] | None = None,
    bbox: dict[str, dict] | None = None,
    pairs: dict[str, dict] | None = None,
) -> list[dict]:
    """Build all applicable task samples for one asset using evidence files."""
    captions = captions or {}
    ocr = ocr or {}
    bbox = bbox or {}
    pairs = pairs or {}
    samples: list[dict] = []
    asset_type = asset["asset_type"]

    def emit(task_type: str, template: dict, image: str, n_images: int = 1, extra: dict | None = None) -> None:
        meta = {"asset_id": asset["id"], "template": template["template"], **(extra or {})}
        samples.append(
            {
                "id": f"{asset['id']}_{task_type}_{len(samples)}",
                "image": [image] if isinstance(image, str) else image,
                "asset_type": asset_type,
                "task_type": task_type,
                "source_id": asset["id"],
                "conversations": templates.build_conversations(
                    template["question"], template["answer"], n_images=n_images
                ),
                "split": "unsplit",
                "meta": meta,
            }
        )

    if asset_type == "general_image":
        cap = captions.get(asset["id"], {})
        if cap.get("caption"):
            emit("image_description", templates.describe_scene(cap["caption"]), asset["path"])
        for subject, count in (cap.get("subjects") or {}).items():
            emit("counting_vqa", templates.count_objects(subject, count), asset["path"])
        if cap.get("place"):
            emit("region_grounding", templates.infer_scene(cap["place"], cap["reason"]), asset["path"])

    if asset_type == "document_image":
        if ocr.get(asset["id"], {}).get("ocr_text"):
            emit("ocr_summary", templates.ocr_summary(ocr[asset["id"]]["ocr_text"]), asset["path"])
        if captions.get(asset["id"], {}).get("doc_qa"):
            for item in captions[asset["id"]]["doc_qa"]:
                emit("document_qa", templates.document_qa(item["question"], item["answer"]), asset["path"])

    if asset_type == "chart_image":
        if captions.get(asset["id"], {}).get("chart"):
            chart = captions[asset["id"]]["chart"]
            emit("chart_reading", templates.chart_reading(chart["kind"], chart["trend"]), asset["path"])
            if chart.get("comparison"):
                emit("chart_comparison", templates.chart_comparison(chart["compared"], chart["comparison"]), asset["path"])

    if asset_type == "interleaved_pair":
        pair = pairs.get(asset["id"], {})
        if pair.get("question") and pair.get("answer"):
            emit(
                "multi_image_comparison",
                templates.multi_image_comparison(pair["question"], pair["answer"]),
                [pair["image_a"], pair["image_b"]],
                n_images=2,
                extra={"pair_id": pair.get("id")},
            )

    if asset_type in ("general_image", "document_image", "chart_image"):
        b = bbox.get(asset["id"])
        if b and b.get("bbox") and b.get("label"):
            question = f"Locate the {b['label']} in this image."
            answer = f"The {b['label']} is located at [{', '.join(f'{v:.1f}' for v in b['bbox'])}]."
            emit("region_grounding", templates.region_grounding(question, answer), asset["path"],
                 extra={"bbox": b["bbox"], "label": b["label"]})
    return samples


def generate_samples(
    assets: list[dict],
    out_path,
    captions_path=None,
    ocr_path=None,
    bbox_path=None,
    pairs_path=None,
) -> list[dict]:
    """Build the full sample set from the asset pool and evidence files."""
    captions = _load_evidence(captions_path)
    ocr = _load_evidence(ocr_path)
    bbox = _load_evidence(bbox_path)
    pairs = _load_evidence(pairs_path)
    samples: list[dict] = []
    for asset in assets:
        samples.extend(
            build_samples_for_asset(asset, captions=captions, ocr=ocr, bbox=bbox, pairs=pairs)
        )
    write_jsonl(out_path, samples)
    return samples
