"""LLM-as-judge QC stage: the judge model comes from the model registry.

The executor injects a JSON-safe snapshot of the judge model row into the
stage config (``_model``) before instantiation; the row map function runs in
Ray workers, which build their own (cached) client from that dict — the
driver's connection is never serialized.
"""

from __future__ import annotations

import json
from typing import ClassVar

from ...eval.models import build_client
from .base import Stage, qc_mark, register

DEFAULT_PROMPT = (
    "You are a strict data-quality judge. Score how well the answer responds "
    "to the question, from 0.0 to 1.0. Return ONLY a JSON object: "
    '{"score": 0.0, "verdict": "ok"|"bad", "reason": "short reason"}.\n'
    "Question: {question}\nAnswer: {answer}"
)


def _parse_judge_output(text: str) -> dict:
    """Extract a JSON verdict from the judge's (possibly chatty) reply."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n", 1)
        cleaned = lines[1] if len(lines) > 1 else ""
        cleaned = cleaned.rsplit("```", 1)[0]
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(cleaned[start : end + 1])
            except json.JSONDecodeError:
                pass
    return {"score": 0.0, "verdict": "bad", "reason": "unparseable judge output"}


@register
class QcLlmStage(Stage):
    """qc_llm: LLM-as-judge quality gate.

    Config: ``{"judge_model_id", "threshold": 0.7, "prompt", "question_field",
    "answer_field"}``. The executor resolves ``judge_model_id`` against the
    model registry and injects the model snapshot as ``_model``.
    """

    name = "qc_llm"
    kind = "qc_llm"
    description = "LLM-as-judge quality gate (judge model from registry)"
    config_schema: ClassVar[dict] = {
        "judge_model_id": "",
        "threshold": 0.7,
        "prompt": "",
    }

    def __init__(self, config: dict | None = None):
        super().__init__(config)
        if not self.config.get("judge_model_id"):
            raise ValueError("qc_llm requires config judge_model_id")
        if not self.config.get("_model"):
            raise ValueError("qc_llm: executor must inject the judge model snapshot")
        self.model_cfg = self.config["_model"]
        self.question_field = self.config.get("question_field", "question")
        self.answer_field = self.config.get("answer_field", "answer")
        self.threshold = float(self.config.get("threshold", 0.7))
        self.prompt = self.config.get("prompt") or DEFAULT_PROMPT

    def row_fn(self, row: dict) -> dict:
        question = str(row.get(self.question_field, ""))
        answer = str(row.get(self.answer_field, ""))
        # .format() cannot be used: the prompt template contains JSON braces
        prompt = self.prompt.replace("{question}", question).replace("{answer}", answer)
        try:
            output = build_client(self.model_cfg).generate(prompt)
        except Exception as exc:  # judge outage: reject the row, keep the batch
            return qc_mark(row, False, llm={"error": f"{type(exc).__name__}: {exc}"})
        verdict = _parse_judge_output(output)
        try:
            score = float(verdict.get("score", 0.0))
        except (TypeError, ValueError):
            score = 0.0
        ok = score >= self.threshold
        return qc_mark(
            row,
            ok,
            llm={
                "score": score,
                "verdict": verdict.get("verdict", ""),
                "reason": verdict.get("reason", ""),
            },
        )
