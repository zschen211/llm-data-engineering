"""Minimal end-to-end example: data strategy + data eval, all CPU.

Runs the full data flywheel on synthetic data with the local storage
backend: a strategy pipeline produces a versioned QA dataset, then a model
(here: a tiny in-process OpenAI-compatible mock) is evaluated against an
eval set, producing a report with badcase attribution.

    cd data_factory
    uv run python examples/minimal.py

Everything is deterministic and offline; no GPU, no RustFS.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from data_factory.api import open_factory
from data_factory import lineage
from data_factory.log import setup_logging
from data_factory.storage import LocalStorageBackend


# --------------------------------------------------------------------------
# Part A: data strategy — QC chain producing a versioned QA dataset
# --------------------------------------------------------------------------

def _write_import_rows(path: Path, count: int = 40) -> None:
    rows = []
    for i in range(count):
        question = f"q{i}: what is the bar height in the chart?"
        rows.append({
            "question": question,
            "answer": f"answer:{question}",
            "image_id": f"img-{i % 8}",
            "category": "bar" if i % 2 else "pie",
        })
    for i in range(3):  # duplicates to be caught by dedup
        rows.append(dict(rows[i]))
    rows.append({"question": "long", "answer": "x" * 300, "image_id": "img-x"})
    with open(path, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def run_strategy(factory) -> str:
    print("== [1] data strategy: capability domain + QC chain ==")
    domain = factory.create_capability_domain(
        "chart_fact_qa", description="factual Q&A over chart images"
    )
    strategy = factory.create_strategy(
        "fact-qa", domain.id, description="QA pairs for chart fact questions"
    )

    manifest = factory.data_dir / "import.jsonl"
    _write_import_rows(manifest)
    dataset = factory.create_dataset(
        "chart-qa-v1", source_type="import", import_manifest=str(manifest)
    )
    print(f"  dataset {dataset.id}: 44 rows (40 + 3 dups + 1 oversize)")

    workflow = factory.define_workflow(
        strategy.id,
        "chart-qa-qc",
        [
            ("schema_check", None),
            ("dedup", {"fields": ["question"]}),
            ("field_range", {"fields": {"answer": {"max": 200}}}),
            ("filter", None),
            ("publish", {"dataset_id": dataset.id,
                         "note": "chart QA v1"}),
        ],
    )
    print("  workflow defined:", factory.show_workflow(workflow.id)["order"])

    run = factory.create_run(workflow.id, dataset.id)
    final = factory.run_workflow(run.id)
    print(f"  run {final.id}: {final.status}, stats={final.stats}")

    version = factory._db.list_dataset_versions(dataset.id)[0]
    print(f"  dataset version v{version.version}: {version.row_count} rows,"
          f" manifest={version.manifest_key}")
    return run.id


# --------------------------------------------------------------------------
# Part B: data eval — model x eval-set -> report with badcase attribution
# --------------------------------------------------------------------------

class _MockModel(BaseHTTPRequestHandler):
    """Deterministic OpenAI-compatible chat completions.

    Answers ``answer:<question>`` (matching the expected answers) except for
    questions containing "wrong", where it hallucinates — so the eval run
    produces real badcases for the attribution step.
    """

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        question = body["messages"][0]["content"][0]["text"]
        if "wrong" in question:
            content = "i do not know"
        else:
            content = f"answer:{question}"
        payload = {"choices": [{"message": {"content": content}}]}
        data = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        data = b'{"models": ["mock"]}'
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *_args):
        pass


def _start_mock_model() -> str:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _MockModel)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_address[1]}"


def _write_eval_items(path: Path) -> None:
    rows = []
    for i in range(10):
        if i % 5 == 4:
            rows.append({
                "question": f"q{i}: what is wrong here?",
                "expected": "answer:WRONG",
                "category": "chart_fact",
            })
        else:
            question = f"q{i}: what is the bar height in the chart?"
            rows.append({
                "question": question,
                "expected": f"answer:{question}",
                "category": "chart_fact" if i % 2 else "chart_compare",
            })
    with open(path, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def run_eval(factory) -> None:
    print("\n== [2] data eval: model registry -> eval run -> report ==")
    base_url = _start_mock_model()
    model = factory.register_model(
        "mock-qwen-vl", backend="api", base_url=base_url, model_id="mock"
    )
    factory.check_model(model.id)
    print(f"  model {model.id}: {factory._db.get_model(model.id).status}")

    evalset_path = factory.data_dir / "eval.jsonl"
    _write_eval_items(evalset_path)
    eval_set = factory.import_eval_set(
        "chart-fact-10", evalset_path, capability_domain_id=_domain_id(factory)
    )
    print(f"  eval set {eval_set.id}: {eval_set.item_count} items")

    eval_run = factory.create_eval_run(eval_set.id, model.id)
    factory.run_eval(eval_run.id, concurrency=4)
    final = factory._db.get_eval_run(eval_run.id)
    print(f"  eval run {eval_run.id}: {final.status}")
    print(f"  aggregate: {json.dumps(final.aggregate, ensure_ascii=False)}")

    report = factory.list_reports(eval_run.id)[0]
    print(f"  report {report.id}: {len(report.badcases)} badcases")
    print(f"  attribution: {json.dumps(report.attribution, ensure_ascii=False)}")

    out = factory.data_dir / "report.md"
    factory.export_report(report.id, out.with_suffix(".json"))
    print(f"  report payload exported to {out.with_suffix('.json')}")


def _domain_id(factory) -> str:
    return factory.list_capability_domains()[0].id


def main() -> None:
    setup_logging(level="WARNING")
    workdir = Path(tempfile.mkdtemp(prefix="dfac-minimal-"))
    print(f"workdir: {workdir}")
    try:
        with open_factory(
            data_dir=workdir / "data",
            backend=LocalStorageBackend(workdir / "artifacts"),
            models_dir=workdir / "models",
        ) as factory:
            run_id = run_strategy(factory)
            print("\nlineage of the strategy run:")
            print(json.dumps(lineage.by_run(factory._db, run_id),
                             ensure_ascii=False, indent=2, default=str))
            run_eval(factory)
            print("\ndone — full data flywheel ran end to end.")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
