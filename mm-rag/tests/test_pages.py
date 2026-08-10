import fitz
import pytest

from mm_rag import pages


@pytest.fixture
def pdf_path(tmp_path):
    doc = fitz.open()
    page1 = doc.new_page()
    page1.insert_text(
        (72, 72),
        "Table of contents\nChapter 1\nChapter 2\nResearch development\nFinancial overview",
    )
    page2 = doc.new_page()
    page2.insert_text(
        (72, 72),
        "Research and development spending rose from 100M to 150M over three years.",
    )
    path = tmp_path / "report.pdf"
    doc.save(str(path))
    doc.close()
    return path


def test_render_pdf_creates_page_images(pdf_path, tmp_path):
    records = pages.render_pdf(pdf_path, tmp_path / "pages", dpi=72)
    assert len(records) == 2
    assert records[0]["page_id"] == "p0001"
    assert records[0]["page_no"] == 1
    assert (tmp_path / "pages" / "page_0001.png").exists()


def test_build_page_units_attaches_text(pdf_path, tmp_path):
    records = pages.build_page_units(pdf_path, tmp_path, dpi=72)
    assert len(records) == 2
    assert "Research and development" in records[1]["text"]
    assert (tmp_path / "page_units.jsonl").exists()


def test_page_by_id(pdf_path, tmp_path):
    records = pages.build_page_units(pdf_path, tmp_path, dpi=72)
    page = pages.page_by_id(records, "p0002")
    assert page is not None
    assert page["page_no"] == 2
