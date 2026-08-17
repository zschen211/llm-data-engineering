"""Eval tests: scorers, full eval loop with a mock LLM, reports."""

import json

import pytest
from conftest import write_eval_items

from data_factory.eval.scorers import score
from data_factory.meta import models as m


def test_scorer_exact():
    assert score("abc", "abc")["verdict"] is True
    assert (
        score("AbC", "abc", {"scorer": "exact", "params": {"case_sensitive": True}})[
            "verdict"
        ]
        is False
    )
    assert score("AbC", "abc")["verdict"] is True


def test_scorer_fuzzy():
    got = score(
        "hello world", "hello wordl", {"scorer": "fuzzy", "params": {"threshold": 0.8}}
    )
    assert got["verdict"] is True
    got = score("hello world", "goodbye", {"scorer": "fuzzy"})["verdict"]
    assert got is False


def test_scorer_numeric():
    got = score("3.14", "3.14159", {"scorer": "numeric"})
    assert got["verdict"] is True
    got = score("3.14", "9.99", {"scorer": "numeric"})["verdict"]
    assert got is False
    got = score("3.14", "N/A", {"scorer": "numeric"})
    assert got["verdict"] is False


def test_scorer_llm_judge(mock_llm):
    from data_factory.eval.models import OpenAIModelClient

    judge = OpenAIModelClient(mock_llm, "mock")
    got = score(
        "q", "a", {"scorer": "llm_judge", "params": {"threshold": 0.5}}, judge=judge
    )
    assert got["verdict"] is True
    assert got["score"] == 0.95


def test_scorer_llm_judge_requires_client():
    with pytest.raises(ValueError, match="judge client"):
        score("q", "a", {"scorer": "llm_judge"})


def test_unknown_scorer():
    with pytest.raises(ValueError, match="unknown scorer"):
        score("a", "b", {"scorer": "wat"})


def _seed_eval(factory, tmp_path, mock_llm, domain_id=""):
    evs = factory.import_eval_set(
        "chart-eval", write_eval_items(tmp_path), capability_domain_id=domain_id
    )
    model = factory.register_model(
        "mock", backend="api", base_url=mock_llm, model_id="mock"
    )
    factory.check_model(model.id)
    er = factory.create_eval_run(evs.id, model.id)
    return evs, model, er


def test_full_eval_loop(factory, tmp_path, mock_llm):
    _evs, _model, er = _seed_eval(factory, tmp_path, mock_llm)
    final = factory.run_eval(er.id)
    assert final.status == m.EVAL_SUCCEEDED

    view = factory.show_eval_run(er.id)
    results = view["results"]
    assert len(results) == 8
    assert results[0].model_output == "answer:q0: what is the bar height?"
    # the 'wrong' items (seq 3, 7) are badcases
    assert results[3].score["verdict"] is False
    assert results[7].score["verdict"] is False

    agg = final.aggregate
    assert agg["overall"]["items"] == 8
    assert agg["overall"]["passed"] == 6
    assert agg["by_category"]["chart_fact"]["passed"] == 2


def test_report_contents_and_export(factory, tmp_path, mock_llm):
    evs, _model, er = _seed_eval(factory, tmp_path, mock_llm)
    factory.run_eval(er.id)
    report = factory.list_reports(er.id)[0]
    assert report.json_key.startswith(f"evals/{evs.id}/")
    assert report.md_key.endswith(".md")
    assert len(report.badcases) == 2

    badcase = report.badcases[0]
    assert badcase["lineage"]["category"] == badcase["category"]
    assert badcase["score"]["verdict"] is False
    assert "capability_domain_id" in badcase["lineage"]

    exported = factory.export_report(report.id, tmp_path / "report.json")
    payload = json.loads(exported.read_text())
    assert payload["aggregate"]["overall"]["items"] == 8
    assert len(payload["badcases"]) == 2


def test_report_attribution_lists_strategy(factory, tmp_path, mock_llm):
    domain = factory.create_capability_domain("chart_fact_qa")
    factory.create_strategy("fact-qa", domain.id)
    _evs, _model, er = _seed_eval(factory, tmp_path, mock_llm, domain.id)
    factory.run_eval(er.id)
    report = factory.list_reports(er.id)[0]
    gaps = report.attribution["gaps"]
    assert gaps and gaps[0]["category"] in ("chart_fact", "chart_compare")
    assert any("fact-qa" in g["covered_by"] for g in gaps)


def test_item_level_rubric_override(factory, tmp_path, mock_llm):
    path = tmp_path / "fuzzy-eval.jsonl"
    rows = [
        {
            "question": f"q{i}: what is the bar height?",
            "expected": f"answer:q{i}: what is the bar height?",
            "rubric": {"scorer": "fuzzy", "params": {"threshold": 0.0}},
            "category": "chart_fact",
        }
        for i in range(4)
    ]
    with open(path, "w", encoding="utf-8") as fh:
        fh.writelines(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    evs = factory.import_eval_set("fuzzy", path)
    model = factory.register_model(
        "mock", backend="api", base_url=mock_llm, model_id="mock"
    )
    factory.check_model(model.id)
    er = factory.create_eval_run(evs.id, model.id)
    final = factory.run_eval(er.id)
    assert final.aggregate["overall"]["passed"] == 4  # threshold 0.0


def test_eval_item_image_support(factory, tmp_path, mock_llm):
    path = tmp_path / "img-eval.jsonl"
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {
                    "question": {
                        "text": "q1: describe",
                        "images": ["http://127.0.0.1/x.png"],
                    },
                    "expected": "answer:q1: describe",
                },
                ensure_ascii=False,
            )
            + "\n"
        )
    evs = factory.import_eval_set("img", path)
    model = factory.register_model(
        "mock", backend="api", base_url=mock_llm, model_id="mock"
    )
    factory.check_model(model.id)
    er = factory.create_eval_run(evs.id, model.id)
    assert factory.run_eval(er.id).status == m.EVAL_SUCCEEDED
