"""llava-instruct CLI: prepare-assets | generate | qa | render | split."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import assets, generator, qa as qa_mod, render, split
from .schema import read_jsonl


def get_parser():
    parser = argparse.ArgumentParser(
        prog="llava-instruct",
        description="LLaVA multimodal instruction data factory",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_assets = subparsers.add_parser("prepare-assets", help="Scan + balance an image dir into an asset pool")
    p_assets.add_argument("src", type=Path, help="Directory of images")
    p_assets.add_argument("--out", type=Path, default=Path("assets.jsonl"))
    p_assets.add_argument("--labels", type=Path, default=None, help="JSON map filename -> asset_type")
    p_assets.add_argument("--per-type", type=int, default=29)

    p_gen = subparsers.add_parser("generate", help="Build LLaVA samples from assets + evidence files")
    p_gen.add_argument("assets", type=Path, help="asset pool jsonl")
    p_gen.add_argument("--out", type=Path, default=Path("samples.jsonl"))
    p_gen.add_argument("--captions", type=Path, default=None)
    p_gen.add_argument("--ocr", type=Path, default=None)
    p_gen.add_argument("--bbox", type=Path, default=None)
    p_gen.add_argument("--pairs", type=Path, default=None)

    p_qa = subparsers.add_parser("qa", help="Run quality checks on samples")
    p_qa.add_argument("samples", type=Path)
    p_qa.add_argument("--out", type=Path, default=Path("samples_qa.jsonl"))
    p_qa.add_argument("--image-root", type=Path, default=None)
    p_qa.add_argument("--report", type=Path, default=Path("qa_report.md"))

    p_render = subparsers.add_parser("render", help="Render sample bboxes onto original images")
    p_render.add_argument("samples", type=Path)
    p_render.add_argument("--image-root", type=Path, required=True)
    p_render.add_argument("--out-dir", type=Path, default=Path("render"))

    p_split = subparsers.add_parser("split", help="train/val/smoke split + manifest + report")
    p_split.add_argument("samples", type=Path)
    p_split.add_argument("--out-dir", type=Path, default=Path("deliver"))
    p_split.add_argument("--seed", type=int, default=42)
    p_split.add_argument("--smoke", type=int, default=4)
    return parser


def cmd_prepare_assets(args):
    labels = {}
    if args.labels:
        labels = json.loads(args.labels.read_text(encoding="utf-8"))
    records = assets.build_asset_pool(args.src, args.out, labels=labels, per_type=args.per_type)
    from collections import Counter

    print(f"assets: {len(records)} {dict(Counter(r['asset_type'] for r in records))}")
    return 0


def cmd_generate(args):
    pool = read_jsonl(args.assets)
    samples = generator.generate_samples(
        pool, args.out,
        captions_path=args.captions, ocr_path=args.ocr,
        bbox_path=args.bbox, pairs_path=args.pairs,
    )
    print(f"samples: {len(samples)}")
    return 0


def cmd_qa(args):
    samples = read_jsonl(args.samples)
    report = qa_mod.run_qa(samples, image_root=args.image_root)
    qa_mod.mark_and_export(samples, report, args.out)
    report_path = args.report
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        "\n".join(
            [
                "# QA report",
                f"- total: {report['total']}",
                f"- passed: {report['passed']}",
                f"- failed: {report['failed']}",
                f"- error breakdown: {report['errors_by_type']}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"passed: {report['passed']}/{report['total']}")
    return 0 if report["failed"] == 0 else 1


def cmd_render(args):
    samples = read_jsonl(args.samples)
    rendered = []
    for sample in samples:
        try:
            out = render.render_sample_boxes(sample, args.image_root, args.out_dir)
            rendered.append(str(out))
        except ValueError:
            pass
    print(f"rendered: {len(rendered)}")
    return 0


def cmd_split(args):
    samples = read_jsonl(args.samples)
    splits = split.split_samples(samples, seed=args.seed, smoke=args.smoke)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    split.write_split_files(splits, args.out_dir)
    split.build_manifest(splits, args.out_dir / "manifest.jsonl")
    print(f"train={len(splits['train'])} val={len(splits['val'])} smoke={len(splits['smoke'])}")
    return 0


def main(argv=None):
    args = get_parser().parse_args(argv)
    handlers = {
        "prepare-assets": cmd_prepare_assets,
        "generate": cmd_generate,
        "qa": cmd_qa,
        "render": cmd_render,
        "split": cmd_split,
    }
    return handlers[args.command](args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
