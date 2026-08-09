"""llava-instruct CLI: prepare-assets | generate | qa | render | split | asset."""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

from . import generator, qa as qa_mod, render, split
from .assets import balance_assets
from .assets.store import AssetStore
from .assets.storage import LocalStorageBackend, S3StorageBackend
from .schema import read_jsonl, write_jsonl

DEFAULT_DATA_DIR = Path(os.environ.get("LLAVA_DATA_DIR", "data"))


def default_store(data_dir: Path | None = None) -> AssetStore:
    """Build the asset store; RustFS backend when RUSTFS_ENDPOINT is set."""
    data_dir = Path(data_dir or DEFAULT_DATA_DIR)
    endpoint = os.environ.get("RUSTFS_ENDPOINT")
    if endpoint:
        if not (os.environ.get("RUSTFS_ACCESS_KEY") and os.environ.get("RUSTFS_SECRET_KEY")):
            raise SystemExit(
                "RUSTFS_ENDPOINT is set but RUSTFS_ACCESS_KEY / RUSTFS_SECRET_KEY are missing"
            )
        backend = S3StorageBackend(
            endpoint,
            os.environ["RUSTFS_ACCESS_KEY"],
            os.environ["RUSTFS_SECRET_KEY"],
            os.environ.get("RUSTFS_BUCKET", "llava-assets"),
        )
    else:
        backend = LocalStorageBackend(data_dir / "blobs")
    return AssetStore(data_dir / "assets.db", backend, tmp_dir=data_dir / "tmp")


# ------------------------------------------------------------------ parser
def get_parser():
    parser = argparse.ArgumentParser(
        prog="llava-instruct",
        description="LLaVA multimodal instruction data factory",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_assets = subparsers.add_parser("prepare-assets", help="Import + balance an image dir into an asset pool")
    p_assets.add_argument("src", type=Path, help="Directory of images")
    p_assets.add_argument("--out", type=Path, default=Path("assets.jsonl"))
    p_assets.add_argument("--labels", type=Path, default=None, help="JSON map filename -> asset_type")
    p_assets.add_argument("--per-type", type=int, default=29)
    p_assets.add_argument("--source-name", default=None)
    p_assets.add_argument("--data-dir", type=Path, default=None)

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

    _add_asset_parser(subparsers)
    return parser


def _add_asset_parser(subparsers) -> None:
    p_asset = subparsers.add_parser("asset", help="Manage the data asset layer (sources/download/storage/versions/tags)")
    asset_sub = p_asset.add_subparsers(dest="asset_command", required=True)

    a_init = asset_sub.add_parser("init", help="Show/validate the asset layer configuration")
    a_init.add_argument("--data-dir", type=Path, default=None)

    a_source = asset_sub.add_parser("source", help="Data source CRUD")
    source_sub = a_source.add_subparsers(dest="source_command", required=True)
    s_add = source_sub.add_parser("add", help="Add a data source")
    s_add.add_argument("--name", required=True)
    s_add.add_argument("--kind", required=True, choices=sorted(("local", "http", "huggingface")))
    s_add.add_argument("--url", default="")
    s_add.add_argument("--license", default="")
    s_add.add_argument("--description", default="")
    s_add.add_argument("--params", default=None, help="JSON string of downloader params")
    s_add.add_argument("--params-file", type=Path, default=None)
    s_add.add_argument("--data-dir", type=Path, default=None)
    s_list = source_sub.add_parser("list")
    s_list.add_argument("--data-dir", type=Path, default=None)
    s_rm = source_sub.add_parser("rm")
    s_rm.add_argument("source_id")
    s_rm.add_argument("--data-dir", type=Path, default=None)

    a_import = asset_sub.add_parser("import", help="Import a local image directory into the pool")
    a_import.add_argument("src", type=Path)
    a_import.add_argument("--labels", type=Path, default=None)
    a_import.add_argument("--source-name", default=None)
    a_import.add_argument("--out", type=Path, default=Path("assets.jsonl"))
    a_import.add_argument("--per-type", type=int, default=29)
    a_import.add_argument("--data-dir", type=Path, default=None)

    a_sync = asset_sub.add_parser("sync", help="Download all resources of a source")
    a_sync.add_argument("source_id")
    a_sync.add_argument("--data-dir", type=Path, default=None)

    a_ls = asset_sub.add_parser("ls", help="List assets (with filters)")
    a_ls.add_argument("--tag", action="append", default=None, help="group=name (repeatable)")
    a_ls.add_argument("--type", default=None)
    a_ls.add_argument("--status", default=None)
    a_ls.add_argument("--source", default=None)
    a_ls.add_argument("--json", action="store_true")
    a_ls.add_argument("--data-dir", type=Path, default=None)

    a_mat = asset_sub.add_parser("materialize", help="Download selected assets to a local dir (for downstream pipeline)")
    a_mat.add_argument("out_dir", type=Path)
    a_mat.add_argument("--tag", action="append", default=None)
    a_mat.add_argument("--source", default=None)
    a_mat.add_argument("--json", action="store_true")
    a_mat.add_argument("--data-dir", type=Path, default=None)

    a_tag = asset_sub.add_parser("tag", help="Tag management")
    tag_sub = a_tag.add_subparsers(dest="tag_command", required=True)
    t_add = tag_sub.add_parser("add")
    t_add.add_argument("asset_id")
    t_add.add_argument("name")
    t_add.add_argument("--group", default="default")
    t_add.add_argument("--data-dir", type=Path, default=None)
    t_rm = tag_sub.add_parser("rm")
    t_rm.add_argument("asset_id")
    t_rm.add_argument("name")
    t_rm.add_argument("--data-dir", type=Path, default=None)
    t_list = tag_sub.add_parser("list")
    t_list.add_argument("--group", default=None)
    t_list.add_argument("--data-dir", type=Path, default=None)

    a_version = asset_sub.add_parser("version", help="Version history / rollback / snapshots")
    version_sub = a_version.add_subparsers(dest="version_command", required=True)
    v_ls = version_sub.add_parser("ls")
    v_ls.add_argument("asset_id")
    v_ls.add_argument("--data-dir", type=Path, default=None)
    v_rb = version_sub.add_parser("rollback")
    v_rb.add_argument("asset_id")
    v_rb.add_argument("version", type=int)
    v_rb.add_argument("--data-dir", type=Path, default=None)
    v_snap = version_sub.add_parser("snapshot")
    v_snap.add_argument("--name", default="")
    v_snap.add_argument("--data-dir", type=Path, default=None)
    v_snap_ls = version_sub.add_parser("snapshot-list")
    v_snap_ls.add_argument("--data-dir", type=Path, default=None)

    a_serve = asset_sub.add_parser("serve", help="Start the Web management UI (requires web extra)")
    a_serve.add_argument("--host", default="0.0.0.0")
    a_serve.add_argument("--port", type=int, default=8000)
    a_serve.add_argument("--data-dir", type=Path, default=None)


# ----------------------------------------------------------- old pipeline
def _import_and_export(args) -> int:
    labels = {}
    if getattr(args, "labels", None):
        labels = json.loads(args.labels.read_text(encoding="utf-8"))
    with default_store(args.data_dir) as store:
        report = store.import_dir(args.src, labels=labels, source_name=args.source_name)
        print(
            f"source={report.source_id} resolved={report.resolved} "
            f"new={report.new} skipped={report.skipped_existing} failed={report.failed}"
        )
        for error in report.errors:
            print(f"  ! {error}", file=sys.stderr)
        records = store.export_pool(args.out, tags=None)
        balanced = balance_assets(records, per_type=args.per_type)
        write_jsonl(args.out, balanced)
        print(f"assets: {len(balanced)} {dict(Counter(r['asset_type'] for r in balanced))}")
        return 1 if report.failed else 0


def cmd_prepare_assets(args):
    return _import_and_export(args)


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
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
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


# ------------------------------------------------------------ asset layer
def cmd_asset(args):
    handlers = {
        "init": cmd_asset_init,
        "source": cmd_asset_source,
        "import": cmd_asset_import,
        "sync": cmd_asset_sync,
        "ls": cmd_asset_ls,
        "materialize": cmd_asset_materialize,
        "tag": cmd_asset_tag,
        "version": cmd_asset_version,
        "serve": cmd_asset_serve,
    }
    return handlers[args.asset_command](args)


def cmd_asset_init(args):
    data_dir = Path(args.data_dir or DEFAULT_DATA_DIR)
    endpoint = os.environ.get("RUSTFS_ENDPOINT")
    with default_store(args.data_dir) as store:
        backend_name = f"rustfs ({endpoint})" if endpoint else "local"
        print(f"data dir  : {data_dir}")
        print(f"metadata  : {data_dir / 'assets.db'}")
        print(f"backend   : {backend_name}")
        print(f"bucket    : {os.environ.get('RUSTFS_BUCKET', 'llava-assets')}")
        print(f"sources   : {len(store.list_sources())}")
    return 0


def cmd_asset_source(args):
    if args.source_command == "add":
        params = None
        if args.params_file:
            params = json.loads(args.params_file.read_text(encoding="utf-8"))
        elif args.params:
            params = json.loads(args.params)
        with default_store(args.data_dir) as store:
            source = store.add_source(args.name, args.kind, url=args.url,
                                      license=args.license, description=args.description,
                                      params=params)
        print(f"source added: {source.id} ({source.kind})")
        return 0
    with default_store(args.data_dir) as store:
        if args.source_command == "list":
            for source in store.list_sources():
                print(f"{source.id}\t{source.kind}\t{source.name}\t{source.url}")
            return 0
        if args.source_command == "rm":
            store.delete_source(args.source_id)
            print(f"source removed: {args.source_id}")
            return 0
    return 1


def cmd_asset_import(args):
    args.source_name = getattr(args, "source_name", None)
    return _import_and_export(args)


def cmd_asset_sync(args):
    with default_store(args.data_dir) as store:
        try:
            report = store.sync_source(args.source_id)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(
            f"source={report.source_id} resolved={report.resolved} "
            f"new={report.new} skipped={report.skipped_existing} failed={report.failed}"
        )
        for error in report.errors:
            print(f"  ! {error}", file=sys.stderr)
        return 1 if report.failed else 0


def cmd_asset_ls(args):
    with default_store(args.data_dir) as store:
        assets = store.list_assets(asset_type=args.type, status=args.status,
                                   source_id=args.source, tags=args.tag)
        if args.json:
            print(json.dumps([{**a.__dict__, "tags": a.tags} for a in assets], ensure_ascii=False, indent=2))
        else:
            for a in assets:
                tags = ",".join(f"{g}={n}" for g, n in a.tags)
                print(f"{a.id}\t{a.status}\t{a.asset_type}\t{a.name}\t{tags}")
        print(f"total: {len(assets)}", file=sys.stderr)
        return 0


def cmd_asset_materialize(args):
    with default_store(args.data_dir) as store:
        records = store.materialize(args.out_dir, tags=args.tag, source_id=args.source)
        if args.json:
            print(json.dumps(records, ensure_ascii=False, indent=2))
        print(f"materialized: {len(records)} -> {args.out_dir}")
        return 0


def cmd_asset_tag(args):
    with default_store(args.data_dir) as store:
        if args.tag_command == "add":
            try:
                store.tag_asset(args.asset_id, args.name, args.group)
            except ValueError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 1
            print(f"tagged: {args.asset_id} {args.group}={args.name}")
            return 0
        if args.tag_command == "rm":
            store.untag_asset(args.asset_id, args.name)
            print(f"untagged: {args.asset_id} {args.name}")
            return 0
        if args.tag_command == "list":
            for tag in store.list_tags(group=args.group):
                print(f"{tag['group']}={tag['name']} ({tag['id']})")
            return 0
    return 1


def cmd_asset_version(args):
    with default_store(args.data_dir) as store:
        if args.version_command == "ls":
            for v in store.version_history(args.asset_id):
                print(f"v{v['version']}\t{v['sha256'][:12]}\t{v['created_at']}\t{v['change_note']}")
            return 0
        if args.version_command == "rollback":
            asset = store.rollback(args.asset_id, args.version)
            if asset is None:
                print(f"version {args.version} not found for {args.asset_id}", file=sys.stderr)
                return 1
            print(f"rolled back: {args.asset_id} -> v{asset.current_version} ({asset.sha256[:12]})")
            return 0
        if args.version_command == "snapshot":
            snapshot = store.create_snapshot(name=args.name)
            print(f"snapshot: {snapshot['id']} assets={snapshot['asset_count']} sha1={snapshot['manifest_sha1'][:12]}")
            return 0
        if args.version_command == "snapshot-list":
            for s in store.list_snapshots():
                print(f"{s['id']}\t{s['asset_count']}\t{s['created_at']}\t{s['manifest_sha1'][:12]}")
            return 0
    return 1


def cmd_asset_serve(args):
    try:
        import uvicorn  # noqa: F401
    except ImportError as exc:
        raise SystemExit("the Web UI requires the optional 'web' extra (uv sync --extra web)") from exc
    from .assets.web import default_app

    app = default_app(args.data_dir)
    print(f"serving asset manager on http://{args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


def main(argv=None):
    args = get_parser().parse_args(argv)
    if args.command == "asset":
        return cmd_asset(args)
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
