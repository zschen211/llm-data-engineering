"""Evaluation: retrieval hit rate, directory-page suppression and evidence coverage.

Ground truth format (eval.jsonl):
  {"question": "...", "relevant_pages": [1, 5, 12], "is_directory_page": false}
"""

from __future__ import annotations

from .schema import read_jsonl


def evaluate(
    retrieval_results: list[dict], ground_truth: list[dict], top_k: int = 4
) -> dict:
    """Compute hit@k, evidence completeness and directory-page suppression.

    ``retrieval_results`` is a list of {"question", "retrieved": [page dicts]}.
    """
    n = len(ground_truth)
    if n == 0:
        return {}
    hits = 0
    evidence_hits = 0
    evidence_total = 0
    dir_leaks = 0
    dir_total = 0

    for item, truth in zip(retrieval_results, ground_truth):
        retrieved = {r["page_no"] for r in item["retrieved"]}
        relevant = set(truth["relevant_pages"])
        hits += int(bool(retrieved & relevant))
        evidence_total += len(relevant)
        evidence_hits += len(retrieved & relevant)
        if truth.get("is_directory_page"):
            dir_total += 1
            dir_leaks += int(bool(retrieved))

    return {
        "n_questions": n,
        "top_k": top_k,
        "hit_at_k": round(hits / n, 4),
        "evidence_completeness": round(evidence_hits / max(evidence_total, 1), 4),
        "directory_suppression": round(1.0 - (dir_leaks / max(dir_total, 1)), 4)
        if dir_total
        else 1.0,
        "directory_leaks": dir_leaks,
        "directory_pages_total": dir_total,
    }


def load_eval_set(path) -> list[dict]:
    return read_jsonl(path)
