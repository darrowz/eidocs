from __future__ import annotations

import csv
import json
import re
from pathlib import Path

from eidocs.errors import UnsupportedDocumentType
from eidocs.ids import document_id_for, sha256_file, stable_id
from eidocs.schema import ContentBlock, DocumentRef, ParsedDocument
from eidocs.security import detect_magic, read_prefix


class FallbackParser:
    name = "fallback"
    text_extensions = {".txt", ".md"}
    table_extensions = {".csv"}
    json_extensions = {".json"}
    image_extensions = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"}

    def supports(self, path: Path) -> bool:
        return path.suffix.lower() in self.text_extensions | self.table_extensions | self.json_extensions | self.image_extensions

    def parse(self, path: Path, *, doc_id: str | None = None) -> ParsedDocument:
        source = Path(path).expanduser().resolve()
        ext = source.suffix.lower()
        if not self.supports(source):
            raise UnsupportedDocumentType(
                f"fallback parser supports txt/md/csv/json/images only; got {ext}. "
                "Use the RAG-Anything adapter for PDF/Office parsing."
            )
        sha = sha256_file(source)
        doc_id = doc_id or document_id_for(source, sha)
        document = DocumentRef(
            doc_id=doc_id,
            source_path=str(source),
            filename=source.name,
            mime_type=detect_magic(read_prefix(source, 4096)),
            sha256=sha,
            size_bytes=source.stat().st_size,
        )
        if ext in self.text_extensions:
            blocks = self._parse_markdown_like(source, doc_id)
        elif ext in self.table_extensions:
            blocks = self._parse_csv(source, doc_id)
        elif ext in self.json_extensions:
            blocks = self._parse_json(source, doc_id)
        else:
            blocks = [
                self._block(
                    doc_id,
                    "image",
                    0,
                    img_path=str(source),
                    caption=[source.name],
                    metadata={"source_ext": ext},
                )
            ]
        return ParsedDocument(document=document, content=blocks, parser=self.name)

    def _parse_markdown_like(self, source: Path, doc_id: str) -> list[ContentBlock]:
        text = source.read_text(encoding="utf-8", errors="replace")
        blocks: list[ContentBlock] = []
        paragraph: list[str] = []
        in_equation = False
        equation_lines: list[str] = []
        table_lines: list[str] = []

        def flush_paragraph() -> None:
            nonlocal paragraph
            joined = "\n".join(line.strip() for line in paragraph if line.strip()).strip()
            paragraph = []
            if joined:
                for chunk in _chunk_text(joined, 1600):
                    blocks.append(self._block(doc_id, "text", len(blocks), text=chunk))

        def flush_table() -> None:
            nonlocal table_lines
            if table_lines:
                table = "\n".join(table_lines)
                blocks.append(self._block(doc_id, "table", len(blocks), table_body=table, text=_table_to_text(table)))
                table_lines = []

        for raw_line in text.splitlines():
            line = raw_line.rstrip()
            if line.strip() == "$$":
                flush_paragraph()
                flush_table()
                if in_equation:
                    latex = "\n".join(equation_lines).strip()
                    if latex:
                        blocks.append(self._block(doc_id, "equation", len(blocks), latex=latex, text=latex))
                    equation_lines = []
                    in_equation = False
                else:
                    in_equation = True
                continue
            if in_equation:
                equation_lines.append(line)
                continue

            image_match = re.search(r"!\[([^\]]*)\]\(([^)]+)\)", line)
            if image_match:
                flush_paragraph()
                flush_table()
                caption = image_match.group(1).strip()
                image_target = (source.parent / image_match.group(2).strip()).resolve()
                blocks.append(
                    self._block(
                        doc_id,
                        "image",
                        len(blocks),
                        img_path=str(image_target),
                        caption=[caption] if caption else [],
                        metadata={"declared_in": str(source)},
                    )
                )
                continue

            if line.strip().startswith("|") and line.strip().endswith("|"):
                flush_paragraph()
                table_lines.append(line)
                continue
            flush_table()

            inline_equations = re.findall(r"(?<!\\)\$([^$]+)(?<!\\)\$", line)
            for latex in inline_equations:
                blocks.append(self._block(doc_id, "equation", len(blocks), latex=latex.strip(), text=latex.strip()))
            clean_line = re.sub(r"(?<!\\)\$([^$]+)(?<!\\)\$", r"\1", line)
            if clean_line.strip():
                paragraph.append(clean_line)
            else:
                flush_paragraph()

        flush_paragraph()
        flush_table()
        if in_equation and equation_lines:
            latex = "\n".join(equation_lines).strip()
            blocks.append(self._block(doc_id, "equation", len(blocks), latex=latex, text=latex))
        return blocks or [self._block(doc_id, "text", 0, text="")]

    def _parse_csv(self, source: Path, doc_id: str) -> list[ContentBlock]:
        rows: list[list[str]] = []
        with source.open("r", encoding="utf-8", errors="replace", newline="") as fh:
            reader = csv.reader(fh)
            for idx, row in enumerate(reader):
                if idx >= 200:
                    break
                rows.append([str(cell) for cell in row])
        table_body = "\n".join("| " + " | ".join(row) + " |" for row in rows)
        summary = f"CSV table with {max(0, len(rows) - 1)} data rows and {len(rows[0]) if rows else 0} columns."
        return [
            self._block(doc_id, "table", 0, table_body=table_body, text=_table_to_text(table_body), caption=[source.name]),
            self._block(doc_id, "text", 1, text=summary),
        ]

    def _parse_json(self, source: Path, doc_id: str) -> list[ContentBlock]:
        raw = source.read_text(encoding="utf-8", errors="replace")
        try:
            value = json.loads(raw)
            pretty = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
        except json.JSONDecodeError:
            pretty = raw
        return [self._block(doc_id, "text", idx, text=chunk) for idx, chunk in enumerate(_chunk_text(pretty, 1800))]

    def _block(self, doc_id: str, block_type: str, order: int, **kwargs) -> ContentBlock:
        payload = {"doc_id": doc_id, "type": block_type, "order": order, **kwargs}
        block_id = stable_id("blk", payload, 24)
        return ContentBlock(block_id=block_id, doc_id=doc_id, type=block_type, page_idx=None, order=order, **kwargs)


def _chunk_text(text: str, max_chars: int) -> list[str]:
    text = text.strip()
    if len(text) <= max_chars:
        return [text] if text else []
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + max_chars)
        cut = text.rfind("\n", start, end)
        if cut <= start:
            cut = end
        chunks.append(text[start:cut].strip())
        start = cut
    return [chunk for chunk in chunks if chunk]


def _table_to_text(table: str) -> str:
    cells = re.sub(r"[|:-]+", " ", table)
    return re.sub(r"\s+", " ", cells).strip()
