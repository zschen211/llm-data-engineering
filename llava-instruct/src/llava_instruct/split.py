"""Training delivery: train/val/smoke split, manifest and report (P03 section 16)."""
from __future__ import annotations

import hashlib
import json
import random
from collections import Counter
from pathlib import Path

from .schema import SPLITS, read_jsonl, write_jsonl


def split_samples(samples: list[dict], ratios: dict[str, float] | None = None,
                  seed: int = 42, smoke: int = 4) -> dict[str, list[dict]]:
    """Split samples into train/val/smoke.

    ratios defaults to {"train": 0.8, "val": 0.2}; smoke is a small fixed
    subset drawn from train.
    """
    ratios = ratios or {"train": 0.8, "val": 0.2}
    rng = random.Random(seed)
    shuffled = samples.copy()
    rng.shuffle(shuffled)
    n = len(shuffled)
    n_train = int(n * ratios.get("train", 0.8))
    train = shuffled[:n_train]
    val = shuffled[n_train:]
    smoke_slice = train[: min(smoke, len(train))]
    result = {"train": train, "val": val, "smoke": smoke_slice}
    for split, records in result.items():
        for record in records:
            record["split"] = split
    return result


def write_split_files(result: dict[str, list[dict]], out_dir: Path) -> None:
    for split in SPLITS:
        write_jsonl(out_dir / f"{split}.jsonl", result[split])


def build_manifest(splits: dict[str, list[dict]], out_path: Path) -> dict:
    """Produce a manifest summarizing counts, task distribution and hashes."""
    all_samples = list({s["id"]: s for group in splits.values() for s in group}.values())
    task_dist = Counter(s["task_type"] for s in all_samples)
    asset_dist = Counter(s["asset_type"] for s in all_samples)
    digest = hashlib.sha1(
        "\n".join(json_bytes(s) for s in all_samples).encode("utf-8")
    ).hexdigest()
    manifest = {
        "total": len(all_samples),
        "by_split": {name: len(records) for name, records in splits.items()},
        "by_task": dict(task_dist),
        "by_asset_type": dict(asset_dist),
        "content_sha1": digest,
        "generated_by": "llava-instruct",
    }
    write_jsonl(out_path, [manifest])
    return manifest


def json_bytes(sample: dict) -> str:
    return json.dumps(sample, sort_keys=True, ensure_ascii=False)


def write_report(manifest: dict, qa_report: dict, out_path: Path) -> None:
    lines = [
        "# LLaVA instruction factory report",
        "",
        f"- total samples: {manifest['total']}",
        f"- by split: {manifest['by_split']}",
        f"- by task: {manifest['by_task']}",
        f"- by asset type: {manifest['by_asset_type']}",
        f"- content sha1: {manifest['content_sha1']}",
        "",
        "## QA",
        f"- passed: {qa_report['passed']} / {qa_report['total']}",
        f"- failed: {qa_report['failed']}",
        f"- error breakdown: {qa_report['errors_by_type']}",
        "",
        "## Low-quality samples",
    ]
    lines += [f"- {sample_id}" for sample_id in qa_report["low_quality_ids"]] or ["- none"]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
