import pytest

from mm_rag import evaluate, index, retrieve
from mm_rag.answer import answer
from mm_rag.prompt import build_messages, format_fallback_answer
from mm_rag.schema import write_jsonl


def _page_units():
    return [
        {
            "page_id": "p0001",
            "page_no": 1,
            "image_path": "pages/page_0001.png",
            "text": "Table of contents Chapter 1 Chapter 2 Research development Financial overview",
        },
        {
            "page_id": "p0002",
            "page_no": 2,
            "image_path": "pages/page_0002.png",
            "text": "Revenue increased 20 percent year over year to 200 million.",
        },
        {
            "page_id": "p0003",
            "page_no": 3,
            "image_path": "pages/page_0003.png",
            "text": "Research and development spending rose from 100 million to 150 million.",
        },
    ]


@pytest.fixture
def lex_index(tmp_path):
    write_jsonl(tmp_path / "page_units.jsonl", _page_units())
    path = tmp_path / "index.json"
    return index.build_index(tmp_path / "page_units.jsonl", path, backend="lexical")


def test_build_lexical_index_requires_page_units(tmp_path):
    write_jsonl(tmp_path / "page_units.jsonl", _page_units())
    idx = index.build_index(
        tmp_path / "page_units.jsonl", tmp_path / "index.json", backend="lexical"
    )
    assert idx["backend"] == "lexical"
    assert idx["n_pages"] == 3
    assert len(idx["pages"]) == 3
    assert idx["pages"][0]["tf"]["chapter"] > 0


def test_tokenize_handles_unicode():
    assert index._tokenize("研发投入 Ratio 2023") == ["研发投入", "ratio", "2023"]


def test_retrieve_filters_directory_page(lex_index):
    results = retrieve.retrieve(lex_index, "research and development spending", top_k=4)
    page_nos = [r["page_no"] for r in results]
    assert 3 in page_nos
    assert 1 not in page_nos  # directory page suppressed


def test_retrieve_no_filter_keeps_directory(lex_index):
    results = retrieve.retrieve(
        lex_index, "financial overview", top_k=4, filter_directory=False
    )
    assert any(r["page_no"] == 1 for r in results)


def test_is_directory_page():
    page = {"tf": {"目录": 10, "chapter": 5}, "doc_len": 100}
    assert retrieve.is_directory_page(page)
    page = {"tf": {"目录": 1}, "doc_len": 200}
    assert not retrieve.is_directory_page(page)


def test_build_messages_includes_image_tokens():
    evidence = [{"page_no": 2}, {"page_no": 3}]
    messages = build_messages("趋势如何？", evidence)
    assert messages[0]["role"] == "system"
    assert "table-of-contents" in messages[0]["content"]
    assert messages[1]["content"].count("<image>") == 2


def test_fallback_answer_organizes_evidence():
    result = format_fallback_answer("趋势？", [{"page_no": 2}, {"page_no": 3}])
    assert result["evidence_pages"] == [2, 3]
    assert "p2" in result["answer"]
    assert "p3" in result["answer"]


def test_answer_fallback_default(lex_index):
    evidence = retrieve.retrieve(lex_index, "research spending", top_k=2)
    result = answer("研发投入趋势？", evidence, backend="fallback")
    assert result["evidence_pages"]
    assert "结论" in result["answer"]


def test_answer_unknown_backend():
    with pytest.raises(ValueError):
        answer("q", [], backend="bogus")


def test_evaluate_metrics(lex_index):
    evalset = [
        {
            "question": "research and development spending trend",
            "relevant_pages": [3],
            "is_directory_page": False,
        },
        {
            "question": "revenue increase",
            "relevant_pages": [2],
            "is_directory_page": False,
        },
        {
            "question": "table of contents",
            "relevant_pages": [],
            "is_directory_page": True,
        },
    ]
    results = [
        {
            "question": item["question"],
            "retrieved": retrieve.retrieve(lex_index, item["question"], top_k=4),
        }
        for item in evalset
    ]
    report = evaluate.evaluate(results, evalset, top_k=4)
    assert report["n_questions"] == 3
    assert report["hit_at_k"] >= 0.5
    assert report["evidence_completeness"] > 0
    assert report["directory_suppression"] == 1.0
