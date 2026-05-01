from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

from eidocs.schema import ContentBlock, DocumentRef, ParsedDocument, QueryHit, QueryRequest, QueryResult


TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]")


class LocalJsonlIndex:
    def __init__(self, root: Path) -> None:
        self.root = Path(root).expanduser()
        self.index_dir = self.root / "indexes" / "local_jsonl"
        self.index_dir.mkdir(parents=True, exist_ok=True)
        self.documents_path = self.index_dir / "documents.jsonl"
        self.blocks_path = self.index_dir / "blocks.jsonl"

    def add(self, parsed: ParsedDocument) -> None:
        documents = [doc for doc in self._load_documents() if doc.doc_id != parsed.document.doc_id]
        blocks = [block for block in self._load_blocks() if block.doc_id != parsed.document.doc_id]
        documents.append(parsed.document)
        blocks.extend(parsed.content)
        self._write_jsonl(self.documents_path, [doc.to_dict() for doc in documents])
        self._write_jsonl(self.blocks_path, [block.to_dict() for block in blocks])

    def query(self, request: QueryRequest) -> QueryResult:
        query = request.query.strip()
        if not query:
            return QueryResult(answer="", hits=[], mode="local", degraded=False, warnings=["empty_query"])
        docs = {doc.doc_id: doc for doc in self._load_documents()}
        wanted = set(request.doc_ids or [])
        q_tokens = set(tokenize(query.lower()))
        scored: list[tuple[float, ContentBlock]] = []
        for block in self._load_blocks():
            if wanted and block.doc_id not in wanted:
                continue
            text = block.searchable_text()
            tokens = set(tokenize(text.lower()))
            if not tokens:
                continue
            overlap = len(q_tokens & tokens)
            if overlap == 0:
                continue
            score = overlap / math.sqrt(len(tokens) + 1)
            score += _modality_bonus(query, block.type)
            scored.append((score, block))
        scored.sort(key=lambda item: (-item[0], item[1].order))
        hits: list[QueryHit] = []
        for score, block in scored[: max(1, min(request.top_k, 20))]:
            doc = docs.get(block.doc_id)
            hits.append(
                QueryHit(
                    doc_id=block.doc_id,
                    block_id=block.block_id,
                    type=block.type,
                    score=round(float(score), 6),
                    page_idx=block.page_idx,
                    snippet=_snippet(block.searchable_text(), query),
                    source_path=doc.source_path if doc else "",
                )
            )
        answer = "\n\n".join(hit.snippet for hit in hits)
        return QueryResult(answer=answer, hits=hits, mode="local", degraded=False)

    def get_document(self, doc_id: str) -> DocumentRef | None:
        for doc in self._load_documents():
            if doc.doc_id == doc_id:
                return doc
        return None

    def get_blocks(self, doc_id: str) -> list[ContentBlock]:
        return [block for block in self._load_blocks() if block.doc_id == doc_id]

    def _load_documents(self) -> list[DocumentRef]:
        if not self.documents_path.exists():
            return []
        return [DocumentRef.from_dict(item) for item in _read_jsonl(self.documents_path)]

    def _load_blocks(self) -> list[ContentBlock]:
        if not self.blocks_path.exists():
            return []
        return [ContentBlock.from_dict(item) for item in _read_jsonl(self.blocks_path)]

    def _write_jsonl(self, path: Path, rows: list[dict[str, Any]]) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        tmp.replace(path)


def tokenize(text: str) -> list[str]:
    return TOKEN_RE.findall(text)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _modality_bonus(query: str, block_type: str) -> float:
    q = query.lower()
    if block_type == "table" and any(term in q for term in ["table", "csv", "data", "数据", "表格"]):
        return 0.35
    if block_type in {"image", "chart"} and any(term in q for term in ["image", "figure", "chart", "图片", "图表"]):
        return 0.35
    if block_type == "equation" and any(term in q for term in ["equation", "formula", "公式"]):
        return 0.35
    return 0.0


def _snippet(text: str, query: str, max_chars: int = 500) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= max_chars:
        return text
    q = next(iter(tokenize(query.lower())), "")
    idx = text.lower().find(q) if q else 0
    if idx < 0:
        idx = 0
    start = max(0, idx - max_chars // 3)
    return text[start : start + max_chars].strip()
