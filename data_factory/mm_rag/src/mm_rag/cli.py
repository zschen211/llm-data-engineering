"""mm-rag CLI: render-pdf | build-index | ask | evaluate."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from . import answer as answer_mod
from . import evaluate as eval_mod
from . import index as index_mod
from . import pages as pages_mod
from . import retrieve as retrieve_mod


def get_parser():
    parser = argparse_parser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_render = subparsers.add_parser(
        "render-pdf", help="Render PDF pages into page image assets"
    )
    p_render.add_argument("pdf", type=Path)
    p_render.add_argument("--out-dir", type=Path, default=Path("page_assets"))
    p_render.add_argument("--dpi", type=int, default=144)

    p_index = subparsers.add_parser(
        "build-index", help="Build page assets + index from a PDF"
    )
    p_index.add_argument("pdf", type=Path)
    p_index.add_argument("--out", type=Path, default=Path("rag_index.json"))
    p_index.add_argument("--assets-dir", type=Path, default=Path("page_assets"))
    p_index.add_argument("--backend", choices=["byaldi", "lexical"], default=None)

    p_ask = subparsers.add_parser(
        "ask", help="Retrieve evidence pages and answer a question"
    )
    p_ask.add_argument("index", type=Path)
    p_ask.add_argument("query", type=str)
    p_ask.add_argument("--top-k", type=int, default=4)
    p_ask.add_argument("--backend", choices=["fallback", "vlm"], default="fallback")

    p_eval = subparsers.add_parser(
        "evaluate", help="Evaluate retrieval over an eval set"
    )
    p_eval.add_argument("index", type=Path)
    p_eval.add_argument("eval", type=Path)
    p_eval.add_argument("--top-k", type=int, default=4)
    p_eval.add_argument("--out", type=Path, default=Path("eval_report.json"))
    return parser


def argparse_parser():
    import argparse

    return argparse.ArgumentParser(
        prog="mm-rag",
        description="Multimodal RAG assistant for financial reports",
    )


def cmd_render(args):
    records = pages_mod.build_page_units(args.pdf, args.out_dir, dpi=args.dpi)
    print(f"pages: {len(records)} -> {args.out_dir}")
    return 0


def cmd_index(args):
    assets_dir = args.assets_dir
    pages_mod.build_page_units(args.pdf, assets_dir, dpi=144)
    index_mod.build_index(
        assets_dir / "page_units.jsonl", args.out, backend=args.backend
    )
    print(f"index: {args.out}")
    return 0


def cmd_ask(args):
    index = index_mod.load_index(args.index)
    if index["backend"] == "byaldi" and args.backend == "vlm":
        evidence = retrieve_mod.retrieve_visual(index, args.query, top_k=args.top_k)
    else:
        evidence = retrieve_mod.retrieve(index, args.query, top_k=args.top_k)
    result = answer_mod.answer(args.query, evidence, backend=args.backend)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_evaluate(args):
    index = index_mod.load_index(args.index)
    evalset = eval_mod.load_eval_set(args.eval)
    results = []
    for item in evalset:
        evidence = retrieve_mod.retrieve(index, item["question"], top_k=args.top_k)
        results.append({"question": item["question"], "retrieved": evidence})
    report = eval_mod.evaluate(results, evalset, top_k=args.top_k)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def main(argv=None):
    args = get_parser().parse_args(argv)
    handlers = {
        "render-pdf": cmd_render,
        "build-index": cmd_index,
        "ask": cmd_ask,
        "evaluate": cmd_evaluate,
    }
    return handlers[args.command](args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
