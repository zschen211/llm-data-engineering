"""Page asset layer: render PDF pages into stable, trackable evidence objects.

Each page gets a stable image file, a page_id, a page_no mapping and the
extracted text (used as fallback for lexical indexing). Page images are bound
to the index so retrieval results can always point back to the original page.
"""

from __future__ import annotations

from pathlib import Path

import fitz

from .schema import write_jsonl


def render_page(
    page, out_dir: Path, dpi: int = 144, page_no: int = 1, source: str = ""
) -> dict:
    pix = page.get_pixmap(dpi=dpi)
    image_name = f"page_{page_no:04d}.png"
    image_path = out_dir / image_name
    pix.save(str(image_path))
    return {
        "page_id": f"p{page_no:04d}",
        "page_no": page_no,
        "image_path": str(image_path),
        "width": pix.width,
        "height": pix.height,
        "source": source,
    }


def render_pdf(pdf_path: Path, out_dir: Path, dpi: int = 144) -> list[dict]:
    """Render every PDF page to PNG and return one record per page."""
    out_dir.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(str(pdf_path))
    records = []
    try:
        for page_no, page in enumerate(doc, start=1):
            records.append(
                render_page(
                    page, out_dir, dpi=dpi, page_no=page_no, source=str(pdf_path)
                )
            )
    finally:
        doc.close()
    return records


def build_page_units(pdf_path: Path, out_dir: Path, dpi: int = 144) -> list[dict]:
    """Render pages, attach extracted text, persist page_units.jsonl."""
    records = render_pdf(pdf_path, out_dir, dpi=dpi)
    doc = fitz.open(str(pdf_path))
    try:
        for page_no, rec in enumerate(records, start=1):
            rec["text"] = doc[page_no - 1].get_text()
    finally:
        doc.close()
    write_jsonl(out_dir / "page_units.jsonl", records)
    return records


def page_by_id(page_units: list[dict], page_id: str) -> dict | None:
    for page in page_units:
        if page["page_id"] == page_id:
            return page
    return None
