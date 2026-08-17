"""Eval runner: model x eval-set -> per-item results -> report.

The runner drives the uniform ModelClient adapter (backend-agnostic), scores
every item with its rubric (rule scorers zero-GPU; ``llm_judge`` uses a judge
model from the registry), records per-item results, then builds the aggregate
+ badcase report via ``report.py``.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ..log import get_logger
from ..meta import models as m
from .models import build_client_for_model
from .report import build_report
from .scorers import score

logger = get_logger("eval.runner")

DEFAULT_CONCURRENCY = 4


class EvalRunner:
    """Executes one eval run; state lives in the DB (resumable via a fresh
    run — per-item results are append-only)."""

    def __init__(self, db, backend, tmp_dir: Path | None = None):
        self.db = db
        self.backend = backend
        self.tmp_dir = Path(tmp_dir or db.path.parent / "tmp")
        self.tmp_dir.mkdir(parents=True, exist_ok=True)

    def run(
        self, eval_run_id: str, concurrency: int = DEFAULT_CONCURRENCY
    ) -> m.EvalRun:
        er = self.db.get_eval_run(eval_run_id)
        if er is None:
            raise ValueError(f"unknown eval run: {eval_run_id}")
        if er.status != m.EVAL_RUNNING:
            raise RuntimeError(f"eval run {eval_run_id} already {er.status}")
        evs = self.db.get_eval_set(er.eval_set_id)
        model = self.db.get_model(er.model_id)
        items = self.db.list_eval_items(er.eval_set_id)
        client = build_client_for_model(model)
        judge = self._judge_client(items, evs)
        try:
            results = self._score_items(er.id, items, evs, client, judge, concurrency)
            report = build_report(
                self.db,
                self.backend,
                self.tmp_dir,
                er,
                evs,
                model,
                items,
                results,
            )
            self.db.update_eval_run(
                er.id,
                {
                    "status": m.EVAL_SUCCEEDED,
                    "finished_at": m._now(),
                    "aggregate": report.aggregate,
                },
            )
        except Exception as exc:
            logger.error("eval run %s failed: %s", eval_run_id, exc)
            self.db.update_eval_run(
                er.id,
                {
                    "status": m.EVAL_FAILED,
                    "finished_at": m._now(),
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )
            raise
        return self.db.get_eval_run(eval_run_id)

    def _judge_client(self, items, evs):
        for item in items:
            rubric = item.rubric or evs.rubric
            if rubric.get("scorer") == "llm_judge":
                judge_id = rubric.get("judge_model_id", "")
                model = self.db.get_model(judge_id)
                if model is None:
                    raise ValueError(f"judge model not found: {judge_id}")
                return build_client_for_model(model)
        return None

    def _score_items(self, run_id, items, evs, client, judge, concurrency) -> list:
        def _one(item: m.EvalItem) -> m.EvalResult:
            question = (
                item.question.get("text", "")
                if isinstance(item.question, dict)
                else item.question
            )
            images = (
                item.question.get("images") if isinstance(item.question, dict) else None
            )
            rubric = item.rubric or evs.rubric
            started = time.monotonic()
            error = ""
            try:
                output = client.generate(question, images)
                scored = score(item.expected, output, rubric, judge=judge)
            except Exception as exc:
                output = ""
                scored = {
                    "score": 0.0,
                    "verdict": False,
                    "reason": f"{type(exc).__name__}: {exc}",
                }
                error = f"{type(exc).__name__}: {exc}"
            latency = int((time.monotonic() - started) * 1000)
            result = m.EvalResult(
                id=0,
                eval_run_id=run_id,
                item_id=item.id,
                model_output=output,
                score=scored,
                latency_ms=latency,
                error=error,
            )
            self.db.create_eval_result(result)
            return result

        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            return list(pool.map(_one, items))
