"""Scorers: rule-based (zero GPU) and LLM-judge, per rubric.

Rubric: ``{"scorer": "exact|fuzzy|numeric|llm_judge", "params": {...}}`` —
set-level defaults, item-level overrides. A scorer returns
``{"score": 0-1, "verdict": bool, "reason": str}``.
"""

from __future__ import annotations

import difflib
import re

SCORER_REGISTRY: dict[str, object] = {}


def _register(name: str):
    def deco(fn):
        SCORER_REGISTRY[name] = fn
        return fn

    return deco


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


@_register("exact")
def _exact(expected: str, output: str, params: dict) -> dict:
    a, b = _clean(expected), _clean(output)
    if params.get("case_sensitive", False):
        ok = a == b
    else:
        ok = a.lower() == b.lower()
    return {
        "score": 1.0 if ok else 0.0,
        "verdict": ok,
        "reason": "exact match" if ok else "mismatch",
    }


@_register("fuzzy")
def _fuzzy(expected: str, output: str, params: dict) -> dict:
    threshold = float(params.get("threshold", 0.8))
    ratio = difflib.SequenceMatcher(
        None, _clean(expected).lower(), _clean(output).lower()
    ).ratio()
    ok = ratio >= threshold
    return {
        "score": ratio,
        "verdict": ok,
        "reason": f"similarity {ratio:.2f} >= {threshold}"
        if ok
        else f"similarity {ratio:.2f} < {threshold}",
    }


@_register("numeric")
def _numeric(expected: str, output: str, params: dict) -> dict:
    tolerance = float(params.get("tolerance", 0.01))
    try:
        target = float(expected)
        value = float(re.sub(r"[^\d.eE+-]", "", output))
    except ValueError:
        return {"score": 0.0, "verdict": False, "reason": "unparseable number"}
    diff = abs(target - value)
    ok = diff <= tolerance
    return {
        "score": 1.0 if ok else 0.0,
        "verdict": ok,
        "reason": f"diff {diff:.4f} <= {tolerance}"
        if ok
        else f"diff {diff:.4f} > {tolerance}",
    }


def _llm_judge(expected: str, output: str, params: dict, judge) -> dict:
    prompt = (
        (
            "You are an answer evaluator. Compare the model output to the "
            'expected answer and return ONLY JSON: {"score": 0.0, "verdict":'
            ' "ok"|"bad", "reason": "..."}.\nExpected: {expected}\nOutput: {output}'
        )
        .replace("{expected}", expected)
        .replace("{output}", output)
    )
    reply = judge.generate(prompt)
    try:
        import json

        payload = json.loads(reply.strip())
    except Exception:
        return {"score": 0.0, "verdict": False, "reason": "unparseable judge reply"}
    score = float(payload.get("score", 0.0))
    verdict = payload.get("verdict") == "ok" and score >= float(
        params.get("threshold", 0.5)
    )
    return {"score": score, "verdict": verdict, "reason": payload.get("reason", "")}


def score(expected: str, output: str, rubric: dict | None = None, judge=None) -> dict:
    """Score one answer against the rubric (default: exact)."""
    rubric = rubric or {}
    name = rubric.get("scorer", "exact")
    params = rubric.get("params") or {}
    if name == "llm_judge":
        if judge is None:
            raise ValueError("llm_judge scorer requires a judge client")
        return _llm_judge(expected, output, params, judge)
    fn = SCORER_REGISTRY.get(name)
    if fn is None:
        raise ValueError(f"unknown scorer: {name}")
    return fn(expected, output, params)
