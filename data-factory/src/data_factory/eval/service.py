"""Eval services: eval-set import, eval runs, reports, exports.

Mixed into the ``DataFactory`` facade alongside ``ModelRegistryService``
(models). Eval items are JSONL rows: ``{"question": "<text>", "expected":
"<truth>", "rubric": {...}?, "category": "..."?}`` — ``question`` may also
be ``{"text": ..., "images": [...]}`` for multimodal items.
"""

from __future__ import annotations

import json
from pathlib import Path

from .. import jsonl
from ..meta import models as m
from ..meta.db import new_id
from .models import build_client_for_model
from .runner import EvalRunner
from .scorers import score


class EvalService:
    """Eval-set management + eval execution (mixin of DataFactory)."""

    # ---- eval sets ----------------------------------------------------------

    def import_eval_set(
        self,
        name: str,
        path: Path,
        capability_domain_id: str = "",
        source: str = "import",
        rubric: dict | None = None,
    ) -> m.EvalSet:
        rows = jsonl.read_rows_from_path(Path(path))
        if not rows:
            raise ValueError("eval set is empty")
        evs = m.EvalSet(
            id=new_id("es_"),
            name=name,
            capability_domain_id=capability_domain_id,
            source=source,
            rubric=rubric or {},
        )
        self._db.create_eval_set(evs)
        for seq, row in enumerate(rows):
            question = row.get("question")
            if isinstance(question, str):
                question = {"text": question}
            if not isinstance(question, dict) or "text" not in question:
                raise ValueError(
                    f"row {seq}: question must be text or {{text, images}}"
                )
            self._db.create_eval_item(
                m.EvalItem(
                    id=new_id("ei_"),
                    eval_set_id=evs.id,
                    seq=seq,
                    question=question,
                    expected=row.get("expected", ""),
                    rubric=row.get("rubric"),
                    category=row.get("category", ""),
                )
            )
        self._db.set_eval_item_count(evs.id)
        evs.item_count = self._db.get_eval_set(evs.id).item_count
        return evs

    def list_eval_sets(self) -> list[m.EvalSet]:
        return self._db.list_eval_sets()

    def show_eval_set(self, eval_set_id: str) -> dict:
        evs = self._db.get_eval_set(eval_set_id)
        if evs is None:
            raise ValueError(f"unknown eval set: {eval_set_id}")
        return {
            "eval_set": evs,
            "items": [
                self._item_view(i) for i in self._db.list_eval_items(eval_set_id)
            ],
        }

    def _item_view(self, item: m.EvalItem) -> dict:
        return {
            "seq": item.seq,
            "question": item.question,
            "expected": item.expected,
            "category": item.category,
            "rubric": item.rubric,
        }

    # ---- eval runs ----------------------------------------------------------

    def create_eval_run(self, eval_set_id: str, model_id: str) -> m.EvalRun:
        if self._db.get_eval_set(eval_set_id) is None:
            raise ValueError(f"unknown eval set: {eval_set_id}")
        model = self._db.get_model(model_id)
        if model is None:
            raise ValueError(f"unknown model: {model_id}")
        if model.status != m.MODEL_READY:
            raise RuntimeError(
                f"model {model.name} is {model.status}; run 'dfac model check' first"
            )
        er = m.EvalRun(id=new_id("evr_"), eval_set_id=eval_set_id, model_id=model_id)
        self._db.create_eval_run(er)
        return er

    def run_eval(self, eval_run_id: str, concurrency: int = 4) -> m.EvalRun:
        return EvalRunner(self._db, self.backend, self.tmp_dir).run(
            eval_run_id, concurrency=concurrency
        )

    def list_eval_runs(self, eval_set_id: str = "") -> list[m.EvalRun]:
        return self._db.list_eval_runs(eval_set_id)

    def show_eval_run(self, eval_run_id: str) -> dict:
        er = self._db.get_eval_run(eval_run_id)
        if er is None:
            raise ValueError(f"unknown eval run: {eval_run_id}")
        model = self._db.get_model(er.model_id)
        return {
            "eval_run": er,
            "model": model.name if model else "",
            "results": self._db.list_eval_results(eval_run_id),
        }

    def score_item(
        self,
        question: str,
        output: str,
        expected: str,
        rubric: dict | None = None,
        judge_model_id: str = "",
    ) -> dict:
        """Score one answer directly (CLI/notebook convenience)."""
        judge = None
        if rubric and rubric.get("scorer") == "llm_judge":
            model = self._db.get_model(judge_model_id)
            if model is None:
                raise ValueError(f"unknown judge model: {judge_model_id}")
            judge = build_client_for_model(model)
        return score(expected, output, rubric, judge=judge)

    # ---- reports ------------------------------------------------------------

    def show_report(self, report_id: str) -> m.Report:
        report = self._db.get_report(report_id)
        if report is None:
            raise ValueError(f"unknown report: {report_id}")
        return report

    def list_reports(self, eval_run_id: str = "") -> list[m.Report]:
        return self._db.list_reports(eval_run_id)

    def export_report(self, report_id: str, out_path: Path) -> Path:
        """Export the report payload (JSON) to a local path."""
        report = self.show_report(report_id)
        payload = jsonl.read_object(self.backend, report.json_key)
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return out_path
