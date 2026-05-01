import pytest

from eidocs.errors import UnsupportedDocumentType
from eidocs.parsers import FallbackParser


def test_markdown_parser_extracts_modal_blocks(tmp_path):
    image = tmp_path / "chart.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 20)
    doc = tmp_path / "report.md"
    doc.write_text(
        "# Report\n\nRevenue table below.\n\n| quarter | revenue |\n| --- | --- |\n| Q1 | 10 |\n\n![chart](chart.png)\n\nFormula $x+y$.\n",
        encoding="utf-8",
    )
    parsed = FallbackParser().parse(doc)
    types = [block.type for block in parsed.content]
    assert "text" in types
    assert "table" in types
    assert "image" in types
    assert "equation" in types


def test_csv_parser_generates_table(tmp_path):
    doc = tmp_path / "data.csv"
    doc.write_text("name,value\nalpha,10\n", encoding="utf-8")
    parsed = FallbackParser().parse(doc)
    assert parsed.content[0].type == "table"
    assert "alpha" in parsed.content[0].table_body


def test_pdf_is_not_faked_by_fallback(tmp_path):
    doc = tmp_path / "paper.pdf"
    doc.write_bytes(b"%PDF-1.7\n")
    with pytest.raises(UnsupportedDocumentType):
        FallbackParser().parse(doc)
