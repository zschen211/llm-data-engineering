"""Retrieval: Top-K multi-page recall with directory-page suppression.

Directory (table-of-contents) pages are high-frequency false positives because
they cover most keywords of the document; they carry almost no data.
"""
from __future__ import annotations

from .index import _tokenize, _score_page

DIRECTORY_HINTS = ("目录", "contents", "table of contents", "chapter", "section", "contents:")
MIN_PAGE_LEN = 8


def is_directory_page(page: dict, query_tokens: list[str] | None = None) -> bool:
    """Heuristic: page text dominated by TOC markers and low on data content."""
    tf = page["tf"]
    doc_len = page["doc_len"]
    if doc_len < MIN_PAGE_LEN:
        return False
    hint_hits = sum(tf.get(token, 0) for token in DIRECTORY_HINTS)
    coverage = hint_hits / doc_len
    return coverage > 0.1


def retrieve(index: dict, query: str, top_k: int = 4, filter_directory: bool = True) -> list[dict]:
    """Return ranked evidence pages: [{"page_id", "page_no", "image_path", "score"}].

    Runs against the lexical index; for a Byaldi index, use ``retrieve_visual``.
    """
    query_tokens = _tokenize(query)
    scored = []
    for page in index["pages"]:
        if filter_directory and is_directory_page(page, query_tokens):
            continue
        score = _score_page(page, query_tokens)
        if score <= 0:
            continue
        scored.append((score, page))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [
        {
            "page_id": page["page_id"],
            "page_no": page["page_no"],
            "image_path": page["image_path"],
            "score": round(score, 6),
        }
        for score, page in scored[:top_k]
    ]


def retrieve_visual(index: dict, query: str, top_k: int = 4, filter_directory: bool = True) -> list[dict]:
    """Visual retrieval via Byaldi (requires the ``gpu`` extra)."""
    if index["backend"] != "byaldi":
        return retrieve(index, query, top_k=top_k, filter_directory=filter_directory)
    try:
        from byaldi import RAGMultiModalModel
    except ImportError as exc:
        raise RuntimeError("visual retrieval requires the optional 'gpu' extra (byaldi + torch)") from exc
    model = RAGMultiModalModel.from_pretrained(index["model_name"] or "vidore/colpali-v1.2")
    results = model.search(query, k=top_k)
    return [
        {
            "page_id": item["page_id"],
            "page_no": item.get("page_num", item["page_id"]),
            "image_path": item["metadata"].get("path", ""),
            "score": float(item.get("score", 0.0)),
        }
        for item in results
    ]
