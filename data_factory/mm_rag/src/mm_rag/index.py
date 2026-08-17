"""Index building: visual index (ColPali + Byaldi) with a lexical fallback.

The index always keeps the original page images bound to it
(``store_collection_with_index`` equivalent), because the generation stage
feeds the retrieved page images back into the VLM.
"""

from __future__ import annotations

import math
import re
from pathlib import Path

from .schema import read_jsonl, write_json, write_jsonl

_TOKEN_RE = re.compile(r"[a-z0-9\u4e00-\u9fff]+")


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def byaldi_available() -> bool:
    try:
        # availability probe for the optional gpu extra
        import byaldi  # noqa: F401  # pylint: disable=unused-import

        return True
    except ImportError:
        return False


def build_byaldi_index(
    pdf_path: Path, index_dir: Path, model_name: str = "vidore/colpali-v1.2"
) -> None:
    """Visual index via Byaldi. Requires the optional ``gpu`` extra."""
    try:
        from byaldi import RAGMultiModalModel
    except ImportError as exc:
        raise RuntimeError(
            "visual indexing requires the optional 'gpu' extra (byaldi + colpali + torch)"
        ) from exc
    model = RAGMultiModalModel.from_pretrained(model_name)
    model.index(
        input_path=str(pdf_path),
        index_name=index_dir.name,
        index_root=str(index_dir.parent),
        store_collection_with_index=True,
        overwrite=True,
    )


def build_lexical_index(page_units: list[dict]) -> list[dict]:
    """Term-frequency index over extracted page text."""
    pages = []
    for page in page_units:
        tokens = _tokenize(page.get("text", ""))
        counts: dict[str, int] = {}
        for token in tokens:
            counts[token] = counts.get(token, 0) + 1
        pages.append(
            {
                "page_id": page["page_id"],
                "page_no": page["page_no"],
                "image_path": page["image_path"],
                "doc_len": max(len(tokens), 1),
                "tf": counts,
            }
        )
    return pages


def build_index(
    page_units_path: Path, out_path: Path, backend: str | None = None
) -> dict:
    """Build an index; ``backend`` in {"byaldi", "lexical"}, default byaldi if available."""
    page_units = read_jsonl(page_units_path)
    if backend is None:
        backend = "byaldi" if byaldi_available() else "lexical"
    if backend == "byaldi":
        build_byaldi_index(
            Path(page_units[0]["source"]), out_path.parent / out_path.stem
        )
    elif backend != "lexical":
        raise ValueError(f"unknown backend: {backend}")
    index = {
        "backend": backend,
        "pages": build_lexical_index(page_units),
        "n_pages": len(page_units),
    }
    write_json(out_path, index)
    write_jsonl(out_path.with_suffix(".pages.jsonl"), page_units)
    return index


def load_index(path: Path) -> dict:
    from .schema import read_json

    return read_json(path)


def _score_page(page: dict, query_tokens: list[str]) -> float:
    """BM25-lite score: query-token overlap weighted by inverse log length."""
    if not query_tokens:
        return 0.0
    tf = page["tf"]
    overlap = sum(math.log1p(tf.get(token, 0)) for token in query_tokens)
    return overlap / math.log1p(page["doc_len"])
