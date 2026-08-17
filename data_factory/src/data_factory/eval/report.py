"""Eval report: aggregate, badcases with lineage, attribution, exports.

Report payload = aggregate (overall + per-category) + per-item detail +
badcase list. Every badcase carries its attribution chain
(category -> capability domain -> strategies with their latest runs), so a
data strategist can see exactly which capability gap which strategy should
have covered. Exported as JSON + Markdown into the storage backend and
indexed in the ``reports`` table.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..meta import models as m
from ..meta.db import new_id
from ..storage.base import report_key_for


def _verdict_passed(scored: dict) -> bool:
    return bool(scored.get("verdict"))


def _aggregate(evs, items, results) -> dict:
    by_id = {r.item_id: r for r in results}
    per_category: dict[str, list[float]] = {}
    passes: dict[str, int] = {}
    errors = 0
    for item in items:
        result = by_id.get(item.id)
        if result is None:
            continue
        if result.error:
            errors += 1
        score_val = float(result.score.get("score", 0.0))
        cat = item.category or "uncategorized"
        per_category.setdefault(cat, []).append(score_val)
        passes[cat] = passes.get(cat, 0) + (1 if _verdict_passed(result.score) else 0)

    def _block(entries):
        n = len(entries)
        return {"items": n, "avg_score": round(sum(entries) / n, 4) if n else 0.0}

    overall = _block([v for vs in per_category.values() for v in vs])
    overall["passed"] = sum(passes.values())
    overall["errors"] = errors
    return {
        "overall": overall,
        "by_category": {
            cat: {**_block(scores), "passed": passes.get(cat, 0)}
            for cat, scores in sorted(per_category.items())
        },
        "capability_domain_id": evs.capability_domain_id,
    }


def _lineage_chain(db, evs, category: str) -> dict:
    domain = None
    if evs.capability_domain_id:
        domain = db.get_capability_domain(evs.capability_domain_id)
    strategies = []
    if evs.capability_domain_id:
        for strategy in db.list_strategies_by_domain(evs.capability_domain_id):
            latest = ""
            for wf in db.list_workflows():
                if wf.strategy_id != strategy.id:
                    continue
                for run in db.list_runs(wf.id):
                    if run.status == m.RUN_SUCCEEDED:
                        latest = run.id
            strategies.append(
                {
                    "strategy_id": strategy.id,
                    "name": strategy.name,
                    "latest_run_id": latest,
                }
            )
    return {
        "category": category,
        "capability_domain_id": evs.capability_domain_id,
        "capability_domain": domain.name if domain else "",
        "strategies": strategies,
    }


def _badcases(db, evs, items, results) -> list[dict]:
    by_id = {r.item_id: r for r in results}
    out = []
    for item in items:
        result = by_id.get(item.id)
        if result is None:
            continue
        passed = _verdict_passed(result.score)
        if passed and not result.error:
            continue
        out.append(
            {
                "item_id": item.id,
                "seq": item.seq,
                "category": item.category or "uncategorized",
                "question": item.question,
                "expected": item.expected,
                "model_output": result.model_output,
                "score": result.score,
                "latency_ms": result.latency_ms,
                "error": result.error,
                "lineage": _lineage_chain(db, evs, item.category or "uncategorized"),
            }
        )
    return out


def _attribution(badcases: list[dict]) -> dict:
    """Gap list: badcase categories that no strategy covers."""
    gaps: dict[str, dict] = {}
    for badcase in badcases:
        lineage = badcase["lineage"]
        cat = lineage["category"]
        if cat not in gaps:
            gaps[cat] = {
                "category": cat,
                "badcase_count": 0,
                "covered_by": [s["name"] for s in lineage["strategies"]],
            }
        gaps[cat]["badcase_count"] += 1
    return {
        "gaps": [
            {"category": g, **info, "uncovered": not info["covered_by"]}
            for g, info in sorted(gaps.items())
        ],
        "suggestion": "Prefer a new strategy for uncovered categories, or add "
        "more samples to the strategies listed above.",
    }


def build_report(db, backend, tmp_dir, er, evs, model, items, results) -> m.Report:
    """Compute aggregate + badcases + attribution and export JSON/Markdown."""
    aggregate = _aggregate(evs, items, results)
    badcases = _badcases(db, evs, items, results)
    attribution = _attribution(badcases)
    payload = {
        "eval_run_id": er.id,
        "model": {"id": model.id, "name": model.name, "backend": model.backend},
        "eval_set": {
            "id": evs.id,
            "name": evs.name,
            "capability_domain_id": evs.capability_domain_id,
        },
        "aggregate": aggregate,
        "badcases": badcases,
        "attribution": attribution,
        "created_at": m._now(),
    }
    report = m.Report(
        id=new_id("rep_"),
        eval_run_id=er.id,
        capability_domain_id=evs.capability_domain_id,
        aggregate=aggregate,
        badcases=badcases,
        attribution=attribution,
    )
    json_key = report_key_for(evs.id, report.id, "json")
    md_key = report_key_for(evs.id, report.id, "md")
    _export_json(backend, json_key, payload, tmp_dir)
    _export_markdown(backend, md_key, payload, tmp_dir)
    report.json_key = json_key
    report.md_key = md_key
    db.create_report(report)
    return report


def _export_json(backend, key: str, payload: dict, tmp_dir) -> None:
    tmp_name = _write_temp(
        tmp_dir, ".json", json.dumps(payload, ensure_ascii=False, indent=2)
    )
    try:
        backend.put_object(key, Path(tmp_name))
    finally:
        Path(tmp_name).unlink(missing_ok=True)


def _export_markdown(backend, key: str, payload: dict, tmp_dir) -> None:
    lines = [
        "# Eval Report",
        "",
        f"- eval run: `{payload['eval_run_id']}`",
        f"- model: `{payload['model']['name']}` ({payload['model']['backend']})",
        f"- eval set: `{payload['eval_set']['name']}`",
        "",
        "## Aggregate",
        "",
        f"- items: {payload['aggregate']['overall']['items']}",
        f"- avg score: {payload['aggregate']['overall']['avg_score']}",
        f"- passed: {payload['aggregate']['overall'].get('passed', 0)}",
        f"- errors: {payload['aggregate']['overall'].get('errors', 0)}",
        "",
        "| category | items | avg_score | passed |",
        "| --- | --- | --- | --- |",
    ]
    for cat, stats in payload["aggregate"]["by_category"].items():
        lines.append(
            f"| {cat} | {stats['items']} | {stats['avg_score']} | {stats['passed']} |"
        )
    lines += ["", "## Badcases", ""]
    for badcase in payload["badcases"]:
        lines.append(
            f"### #{badcase['seq']} [{badcase['category']}] "
            f"{badcase['lineage']['capability_domain']}"
        )
        lines.append(f"- expected: {badcase['expected']}")
        lines.append(f"- output: {badcase['model_output'][:200]}")
        lines.append(f"- score: {badcase['score']}")
        lines.append(f"- lineage: {badcase['lineage']}")
        lines.append("")
    lines += ["", "## Attribution", ""]
    lines.append("```json")
    lines.append(json.dumps(payload["attribution"], ensure_ascii=False, indent=2))
    lines.append("```")
    tmp_name = _write_temp(tmp_dir, ".md", "\n".join(lines))
    try:
        backend.put_object(key, Path(tmp_name))
    finally:
        Path(tmp_name).unlink(missing_ok=True)


def _write_temp(tmp_dir: Path, suffix: str, text: str) -> str:
    """Write text to a temp file; return its path (caller removes it)."""
    import tempfile

    fd, tmp_name = tempfile.mkstemp(prefix="dfac-", suffix=suffix, dir=tmp_dir or None)
    with open(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    return tmp_name
