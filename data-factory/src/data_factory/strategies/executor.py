"""Ray Data linear executor for workflow runs.

One node = one Ray Data transform; every stage boundary materializes to the
storage backend (``artifacts/<run_id>/<node_id>/out.jsonl``), which gives
independent timing, retry isolation and free resume: a re-run skips nodes
whose run_stage row is ``succeeded`` and whose artifact exists.

Row-level failures are isolated by the stage wrapper (see ``Stage.transform``
default); QC rejects flow through the rows as ``_qc`` marks and are only
dropped by the publish sink.
"""

from __future__ import annotations

from pathlib import Path

import ray

from .. import jsonl
from ..eval.models import model_to_cfg
from ..input import INPUT_NODE_ID, materialize_input
from ..log import get_logger
from ..meta import models as m
from ..meta.db import new_id
from ..storage.base import artifact_key_for
from . import dag
from .stages.base import (
    SinkStage,
    StageContext,
    build_stage,
    has_error,
)

logger = get_logger("executor")

OUT_FILENAME = "out.jsonl"


def ensure_ray() -> None:
    if not ray.is_initialized():
        ray.init(ignore_reinit_error=True, log_to_driver=False)


class PipelineExecutor:
    """Executes a run's workflow chain; idempotent wrt completed nodes."""

    def __init__(self, db, backend, tmp_dir: Path | None = None):
        self.db = db
        self.backend = backend
        self.tmp_dir = Path(tmp_dir or db.path.parent / "tmp")
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        ensure_ray()

    # -- public ---------------------------------------------------------------

    def run(self, run_id: str) -> m.Run:
        run = self._require_run(run_id)
        if run.status in (m.RUN_RUNNING, m.RUN_SUCCEEDED):
            raise RuntimeError(f"run {run_id} already {run.status}")
        if run.status == m.RUN_CANCELLED:
            raise RuntimeError("run cancelled")
        workflow = self.db.get_workflow(run.workflow_id)
        nodes = self.db.list_workflow_nodes(workflow.id)
        order = dag.validate(nodes, self.db.list_workflow_edges(workflow.id))
        by_id = {n.id: n for n in nodes}
        now = m._now()
        self.db.update_run(
            run_id, {"status": m.RUN_RUNNING, "started_at": now, "error": ""}
        )
        try:
            rows = self._load_input(run)
            for node_id in order:
                node = by_id[node_id]
                rows = self._execute_node(run, node, rows)
            stats = self._collect_stats(run.id)
            self.db.update_run(
                run_id,
                {"status": m.RUN_SUCCEEDED, "finished_at": m._now(), "stats": stats},
            )
        except Exception as exc:
            logger.error("run %s failed: %s", run_id, exc)
            self.db.update_run(
                run_id,
                {
                    "status": m.RUN_FAILED,
                    "finished_at": m._now(),
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )
            raise
        return self.db.get_run(run_id)

    # -- internals ------------------------------------------------------------

    def _require_run(self, run_id: str) -> m.Run:
        run = self.db.get_run(run_id)
        if run is None:
            raise ValueError(f"unknown run: {run_id}")
        return run

    def _artifact_for(self, run_id: str, node_id: str) -> m.Artifact | None:
        for art in self.db.list_artifacts(run_id):
            if art.node_id == node_id:
                return art
        return None

    def _load_input(self, run: m.Run) -> list[dict]:
        existing = self._artifact_for(run.id, INPUT_NODE_ID)
        if existing and self.backend.exists(existing.object_key):
            return jsonl.read_rows(self.backend, existing.object_key, self.tmp_dir)
        dataset = self.db.get_dataset(run.input_dataset_id)
        if dataset is None:
            raise ValueError(f"unknown dataset: {run.input_dataset_id}")
        rows = materialize_input(
            self.db, self.backend, dataset, run.input_dataset_version
        )
        self._write_artifact(run.id, INPUT_NODE_ID, rows, INPUT_NODE_ID)
        return rows

    def _execute_node(self, run: m.Run, node, rows: list[dict]) -> list[dict]:
        prior = self.db.get_run_stage(run.id, node.id)
        resumed = self._try_resume(run, node, prior)
        if resumed is not None:
            return resumed
        if self.db.get_run(run.id).status == m.RUN_CANCELLED:
            raise RuntimeError("run cancelled")
        stage = self._build_stage(node)
        attempts = (prior.attempts if prior else 0) + 1
        self.db.upsert_run_stage(
            m.RunStage(
                run_id=run.id,
                node_id=node.id,
                status=m.RUN_RUNNING,
                rows_in=len(rows),
                attempts=attempts,
                started_at=m._now(),
            )
        )
        logger.info("node %s (%s): %d rows in", node.id, node.stage_name, len(rows))
        if isinstance(stage, SinkStage):
            out_rows, published, failed = self._run_sink(run, node, stage, rows)
        else:
            out_rows, published, failed = self._run_transform(stage, rows)
        self._write_artifact(run.id, node.id, out_rows, node.id)
        self.db.upsert_run_stage(
            m.RunStage(
                run_id=run.id,
                node_id=node.id,
                status=m.RUN_SUCCEEDED,
                rows_in=len(rows),
                rows_out=published,
                failed_rows=failed,
                attempts=attempts,
                started_at=m._now(),
                finished_at=m._now(),
            )
        )
        return out_rows

    def _try_resume(self, run: m.Run, node, prior) -> list[dict] | None:
        """Skip a completed node when its artifact exists (resume)."""
        if not (prior and prior.status == m.RUN_SUCCEEDED):
            return None
        existing = self._artifact_for(run.id, node.id)
        if not (existing and self.backend.exists(existing.object_key)):
            return None
        logger.info("resume: skip %s", node.id)
        return jsonl.read_rows(self.backend, existing.object_key, self.tmp_dir)

    def _run_sink(self, run: m.Run, node, stage: SinkStage, rows: list[dict]):
        ctx = StageContext(self.db, self.backend, self.tmp_dir, run.id, node.id)
        stats = stage.write(rows, ctx)
        logger.info("node %s: published %s", node.id, stats)
        return (
            rows,
            stats.get("row_count", len(rows)),
            stats.get("rejected_count", 0),
        )

    def _run_transform(self, stage, rows: list[dict]):
        dataset = ray.data.from_items(rows)
        out_rows = stage.transform(dataset).take_all()
        failed = sum(1 for r in out_rows if has_error(r))
        return out_rows, len(out_rows), failed

    def _build_stage(self, node):
        config = dict(node.config or {})
        if node.stage_name == "qc_llm":
            judge = self.db.get_model(config.get("judge_model_id", ""))
            if judge is None:
                raise ValueError(
                    f"judge model not found: {config.get('judge_model_id')}"
                )
            config["_model"] = model_to_cfg(judge)
        return build_stage(node.stage_name, config)

    def _write_artifact(
        self, run_id: str, node_id: str, rows: list[dict], label: str
    ) -> None:
        key = artifact_key_for(run_id, node_id, OUT_FILENAME)
        info = jsonl.write_rows(self.backend, key, rows, self.tmp_dir)
        existing = self._artifact_for(run_id, node_id)
        if existing:
            self.db.update(
                m.Artifact,
                {"id": existing.id},
                {
                    "object_key": info["key"],
                    "sha256": info["sha256"],
                    "size": info["size"],
                    "row_count": info["row_count"],
                },
            )
        else:
            self.db.create_artifact(
                m.Artifact(
                    id=new_id("art_"),
                    run_id=run_id,
                    node_id=node_id,
                    kind="intermediate",
                    object_key=info["key"],
                    sha256=info["sha256"],
                    size=info["size"],
                    row_count=info["row_count"],
                )
            )

    def _collect_stats(self, run_id: str) -> dict:
        """Aggregate stage stats; ``rows_out`` is the final node's output."""
        run = self.db.get_run(run_id)
        workflow = self.db.get_workflow(run.workflow_id)
        nodes = self.db.list_workflow_nodes(workflow.id)
        final_node = max(nodes, key=lambda n: n.position).id if nodes else ""
        total_failed = 0
        rows_out = 0
        per_node = {}
        for rs in self.db.list_run_stages(run_id):
            total_failed += rs.failed_rows
            per_node[rs.node_id] = {
                "rows_in": rs.rows_in,
                "rows_out": rs.rows_out,
                "failed_rows": rs.failed_rows,
            }
            if rs.node_id == final_node:
                rows_out = rs.rows_out
        return {"rows_out": rows_out, "failed_rows": total_failed, "nodes": per_node}
