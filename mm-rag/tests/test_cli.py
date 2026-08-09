import fitz

from mm_rag.cli import main


def _make_pdf(path):
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Revenue rose 20 percent to 200 million. Research spending grew steadily.")
    doc.save(str(path))
    doc.close()


def test_cli_render_and_index_and_ask(tmp_path):
    pdf = tmp_path / "r.pdf"
    _make_pdf(pdf)
    assert main(["render-pdf", str(pdf), "--out-dir", str(tmp_path / "pages")]) == 0
    assert main(["build-index", str(pdf), "--out", str(tmp_path / "index.json"),
                 "--assets-dir", str(tmp_path / "assets"), "--backend", "lexical"]) == 0
    assert main(["ask", str(tmp_path / "index.json"), "revenue rose", "--top-k", "2"]) == 0


def test_cli_evaluate(tmp_path):
    from mm_rag.schema import write_jsonl

    pdf = tmp_path / "r.pdf"
    _make_pdf(pdf)
    main(["build-index", str(pdf), "--out", str(tmp_path / "index.json"),
          "--assets-dir", str(tmp_path / "assets"), "--backend", "lexical"])
    write_jsonl(tmp_path / "eval.jsonl", [{"question": "revenue rose", "relevant_pages": [1], "is_directory_page": False}])
    assert main(["evaluate", str(tmp_path / "index.json"), str(tmp_path / "eval.jsonl"),
                 "--out", str(tmp_path / "report.json")]) == 0
    assert (tmp_path / "report.json").exists()
