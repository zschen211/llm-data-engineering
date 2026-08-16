"""``dfac`` CLI: capabilities / strategies / datasets / workflows / runs /
stages / lineage / models / eval sets / eval runs / reports (spec §10).

Every subcommand opens the factory via ``open_factory()`` (env-configured),
so ``DFAC_DATA_DIR`` / ``DFAC_STORAGE_BACKEND`` / ``RUSTFS_*`` apply here
too. Output is plain text for grep-ability; JSON detail via ``--json`` on
the show/lineage subcommands.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .api import open_factory
from .log import setup_logging


def _factory(args):
    return open_factory(data_dir=Path(args.data_dir) if args.data_dir else None)


def _print_json(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


def _print_table(header: list[str], rows: list[list]) -> None:
    widths = [
        max(len(h), *(len(str(r[i])) for r in rows)) if rows else len(h)
        for i, h in enumerate(header)
    ]
    print("  ".join(h.ljust(widths[i]) for i, h in enumerate(header)))
    print("  ".join("-" * w for w in widths))
    for row in rows:
        print("  ".join(str(c).ljust(widths[i]) for i, c in enumerate(row)))


def _domain_id(factory, name_or_id: str) -> str:
    if name_or_id.startswith("cd_"):
        return name_or_id
    for domain in factory.list_capability_domains():
        if domain.name == name_or_id:
            return domain.id
    raise SystemExit(f"unknown capability domain: {name_or_id}")


# ---- subcommand handlers ---------------------------------------------------


def _cmd_init(args) -> None:
    with _factory(args) as factory:
        print(f"data dir:   {factory.data_dir}")
        print(f"db:         {factory.db_path}")
        print(f"backend:    {factory.backend_name}")
        print(f"stages:     {[s.name for s in factory._db.list_stage_types()]}")


def _cmd_capability(args) -> None:
    with _factory(args) as factory:
        if args.action == "add":
            domain = factory.create_capability_domain(
                args.name, description=args.description or ""
            )
            print(domain.id)
        else:
            _print_table(
                ["id", "name", "description", "parent"],
                [
                    [d.id, d.name, d.description, d.parent_id]
                    for d in factory.list_capability_domains()
                ],
            )


def _cmd_strategy(args) -> None:
    with _factory(args) as factory:
        if args.action == "add":
            strategy = factory.create_strategy(
                args.name,
                _domain_id(factory, args.domain),
                description=args.description or "",
            )
            print(strategy.id)
        else:
            _print_table(
                ["id", "name", "domain", "enabled"],
                [
                    [s.id, s.name, s.capability_domain_id, int(s.enabled)]
                    for s in factory.list_strategies()
                ],
            )


def _cmd_dataset(args) -> None:
    with _factory(args) as factory:
        if args.action == "add":
            ds = factory.create_dataset(
                args.name,
                args.source_type,
                snapshot_id=args.snapshot or "",
                tag_filters=_parse_tag_filters(args.tag),
                import_manifest=args.manifest or "",
                derived_from=args.derived or "",
            )
            print(ds.id)
        else:
            _print_table(
                ["id", "name", "source_type", "ref"],
                [
                    [
                        d.id,
                        d.name,
                        d.source_type,
                        d.snapshot_id or d.import_manifest or d.derived_from,
                    ]
                    for d in factory.list_datasets()
                ],
            )


def _parse_tag_filters(tags: list[str]) -> list[dict]:
    filters = []
    for tag in tags or []:
        group, _, name = tag.partition("=")
        filters.append({"group": group, "name": name or group})
    return filters


def _parse_stages(pairs: list[str]) -> list[tuple[str, dict | None]]:
    stages = []
    for pair in pairs or []:
        name, _, cfg = pair.partition("=")
        stages.append((name, json.loads(cfg) if cfg else None))
    return stages


def _cmd_workflow(args) -> None:
    with _factory(args) as factory:
        if args.action == "define":
            strategy_id = args.strategy
            if not strategy_id.startswith("st_"):
                matches = [
                    s for s in factory.list_strategies() if s.name == strategy_id
                ]
                if not matches:
                    raise SystemExit(f"unknown strategy: {strategy_id}")
                strategy_id = matches[0].id
            wf = factory.define_workflow(
                strategy_id,
                args.name,
                _parse_stages(args.stages),
                description=args.description or "",
            )
            print(wf.id)
        elif args.action == "validate":
            wf_id = args.workflow_id or args.name
            order = factory.validate_workflow(wf_id)
            print("chain:", " -> ".join(order))
        else:
            for wf in factory.list_workflows():
                print(f"{wf.id}  {wf.name}  strategy={wf.strategy_id}")


def _cmd_run(args) -> None:
    with _factory(args) as factory:
        if args.action == "start":
            run = factory.create_run(args.workflow_id, args.dataset)
            print(run.id)
            factory.run_workflow(run.id)
            final = factory._db.get_run(run.id)
            print(f"status={final.status} stats={final.stats}")
        elif args.action == "cancel":
            factory.cancel_run(args.run_id or args.workflow_id)
            print("cancelled")
        elif args.action == "show":
            run_id = args.run_id or args.workflow_id
            _print_json(factory.show_run(run_id))
        else:
            _print_table(
                ["id", "workflow", "dataset", "status"],
                [
                    [r.id, r.workflow_id, r.input_dataset_id, r.status]
                    for r in factory.list_runs()
                ],
            )


def _cmd_stage(args) -> None:
    with _factory(args) as factory:
        if args.action == "list":
            _print_table(
                ["name", "kind", "module", "description"],
                [
                    [s.name, s.kind, s.module, s.description]
                    for s in factory._db.list_stage_types()
                ],
            )
        else:
            out = factory.stage_run(
                args.stage_name,
                Path(args.input),
                json.loads(args.config) if args.config else None,
            )
            print(json.dumps(out, ensure_ascii=False, indent=2))


def _cmd_lineage(args) -> None:
    from . import lineage

    with _factory(args) as factory:
        if args.run:
            result = lineage.by_run(factory._db, args.run)
        elif args.dataset:
            did, _, ver = args.dataset.partition("@")
            result = lineage.by_dataset(factory._db, did, int(ver) if ver else None)
        else:
            result = lineage.by_strategy(factory._db, args.strategy)
        _print_json(result)


def _cmd_model(args) -> None:
    with _factory(args) as factory:
        handler = {
            "register": _model_register,
            "scan": _model_scan,
            "check": _model_check,
            "rm": _model_rm,
            "list": _model_list,
        }[args.action]
        handler(factory, args)


def _model_register(factory, args) -> None:
    model = factory.register_model(
        args.name,
        args.backend,
        model_id=args.model_id or "",
        weights_dir=args.weights_dir or "",
        base_url=args.base_url or "",
        api_key_env=args.api_key_env or "",
    )
    print(model.id)


def _model_scan(factory, args) -> None:
    factory.scan_models()
    print("scan done")


def _model_check(factory, args) -> None:
    model = factory.check_model(args.model_id or args.name)
    print(f"{model.status}  {model.last_error}")


def _model_rm(factory, args) -> None:
    factory.remove_model(args.model_id or args.name)
    print("removed")


def _model_list(factory, args) -> None:
    _print_table(
        ["id", "name", "backend", "status", "target"],
        [
            [
                mo.id,
                mo.name,
                mo.backend,
                mo.status,
                mo.base_url or mo.weights_dir or mo.model_id,
            ]
            for mo in factory.list_models()
        ],
    )


def _cmd_evalset(args) -> None:
    with _factory(args) as factory:
        if args.action == "import":
            evs = factory.import_eval_set(
                args.name,
                Path(args.file),
                capability_domain_id=args.domain or "",
                rubric=json.loads(args.rubric) if args.rubric else None,
            )
            print(f"{evs.id}  items={evs.item_count}")
        elif args.action == "show":
            evs_id = args.eval_set_id or args.name
            _print_json(factory.show_eval_set(evs_id))
        else:
            _print_table(
                ["id", "name", "domain", "items", "source"],
                [
                    [e.id, e.name, e.capability_domain_id, e.item_count, e.source]
                    for e in factory.list_eval_sets()
                ],
            )


def _cmd_eval(args) -> None:
    with _factory(args) as factory:
        if args.action == "run":
            er = factory.create_eval_run(args.eval_set_id, args.model)
            print(er.id)
            factory.run_eval(er.id, concurrency=args.concurrency)
            final = factory._db.get_eval_run(er.id)
            print(f"status={final.status} aggregate={final.aggregate}")
        elif args.action == "show":
            er_id = args.eval_run_id or args.eval_set_id
            _print_json(factory.show_eval_run(er_id))
        else:
            _print_table(
                ["id", "eval_set", "model", "status", "created"],
                [
                    [e.id, e.eval_set_id, e.model_id, e.status, e.created_at]
                    for e in factory.list_eval_runs()
                ],
            )


def _cmd_report(args) -> None:
    with _factory(args) as factory:
        if args.action == "export":
            path = factory.export_report(args.report_id, Path(args.out))
            print(path)
        elif args.action == "show":
            report = factory.show_report(args.report_id)
            _print_json(
                {
                    "id": report.id,
                    "aggregate": report.aggregate,
                    "badcase_count": len(report.badcases),
                    "attribution": report.attribution,
                }
            )
        else:
            _print_table(
                ["id", "eval_run", "domain", "badcases"],
                [
                    [r.id, r.eval_run_id, r.capability_domain_id, len(r.badcases)]
                    for r in factory.list_reports()
                ],
            )


# ---- parser ----------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dfac", description="data-factory CLI")
    parser.add_argument("--data-dir", help="factory root (default $DFAC_DATA_DIR)")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="show factory configuration")

    cap = sub.add_parser("capability", help="capability domains")
    cap.add_argument("action", choices=["add", "list"])
    cap.add_argument("name", nargs="?")
    cap.add_argument("--description", default="")
    cap.set_defaults(func=_cmd_capability)

    strategy = sub.add_parser("strategy", help="data strategies")
    strategy.add_argument("action", choices=["add", "list"])
    strategy.add_argument("name", nargs="?")
    strategy.add_argument("--domain", default="")
    strategy.add_argument("--description", default="")
    strategy.set_defaults(func=_cmd_strategy)

    dataset = sub.add_parser("dataset", help="dataset definitions")
    dataset.add_argument("action", choices=["add", "list"])
    dataset.add_argument("name", nargs="?")
    dataset.add_argument(
        "--source-type", choices=["snapshot", "import", "derived"], default="import"
    )
    dataset.add_argument("--snapshot", default="")
    dataset.add_argument(
        "--tag", action="append", default=[], help="group=name filter (repeatable)"
    )
    dataset.add_argument("--manifest", default="")
    dataset.add_argument("--derived", default="", help="dataset_id@version")
    dataset.set_defaults(func=_cmd_dataset)

    workflow = sub.add_parser("workflow", help="workflows")
    workflow.add_argument("action", choices=["define", "validate", "list"])
    workflow.add_argument("name", nargs="?")
    workflow.add_argument("--strategy", default="")
    workflow.add_argument(
        "--stages",
        action="append",
        default=[],
        help="name[=json-config] (in order, repeatable)",
    )
    workflow.add_argument("--description", default="")
    workflow.add_argument("workflow_id", nargs="?")
    workflow.set_defaults(func=_cmd_workflow)

    run = sub.add_parser("run", help="workflow runs")
    run.add_argument("action", choices=["start", "list", "show", "cancel"])
    run.add_argument("workflow_id", nargs="?")
    run.add_argument("--dataset", default="")
    run.add_argument("run_id", nargs="?")
    run.set_defaults(func=_cmd_run)

    stage = sub.add_parser("stage", help="stage registry / single-stage debug")
    stage.add_argument("action", choices=["list", "run"])
    stage.add_argument("stage_name", nargs="?")
    stage.add_argument("--input", default="")
    stage.add_argument("--config", default="")
    stage.set_defaults(func=_cmd_stage)

    lineage = sub.add_parser("lineage", help="lineage tracing")
    lineage.add_argument("--run", default="")
    lineage.add_argument("--dataset", default="", help="dataset_id@version")
    lineage.add_argument("--strategy", default="")
    lineage.set_defaults(func=_cmd_lineage)

    model = sub.add_parser("model", help="model registry")
    model.add_argument("action", choices=["register", "list", "scan", "check", "rm"])
    model.add_argument("name", nargs="?")
    model.add_argument("--backend", choices=["local", "vllm", "api"], default="api")
    model.add_argument("--model-id", default="")
    model.add_argument("--weights-dir", default="")
    model.add_argument("--base-url", default="")
    model.add_argument("--api-key-env", default="")
    model.add_argument("model_id", nargs="?")
    model.set_defaults(func=_cmd_model)

    evalset = sub.add_parser("evalset", help="eval sets")
    evalset.add_argument("action", choices=["import", "list", "show"])
    evalset.add_argument("name", nargs="?")
    evalset.add_argument("--file", default="")
    evalset.add_argument("--domain", default="")
    evalset.add_argument("--rubric", default="")
    evalset.add_argument("eval_set_id", nargs="?")
    evalset.set_defaults(func=_cmd_evalset)

    evalrun = sub.add_parser("eval", help="eval runs")
    evalrun.add_argument("action", choices=["run", "list", "show"])
    evalrun.add_argument("eval_set_id", nargs="?")
    evalrun.add_argument("--model", default="")
    evalrun.add_argument("--concurrency", type=int, default=4)
    evalrun.add_argument("eval_run_id", nargs="?")
    evalrun.set_defaults(func=_cmd_eval)

    report = sub.add_parser("report", help="eval reports")
    report.add_argument("action", choices=["list", "show", "export"])
    report.add_argument("report_id", nargs="?")
    report.add_argument("--out", default="report.json")
    report.set_defaults(func=_cmd_report)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    setup_logging(level="DEBUG" if args.verbose else "INFO")
    try:
        if args.command == "init":
            _cmd_init(args)
        else:
            args.func(args)
        return 0
    except Exception as exc:  # CLI: any failure is a user-facing message
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
