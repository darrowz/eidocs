"""Core data models for eidocs parsing and query results.

Boundary note:
- These models describe parsed document artifacts (document, block, query, hit/result).
- They intentionally do not depend on `eiskills`, `eitraining`, or eimemory internals.
- Cross-domain consumers should interact with these structures as data contracts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal
import datetime as dt


ContentType = Literal["text", "image", "table", "equation", "chart", "custom"]


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


@dataclass(frozen=True)
class DocumentRef:
    doc_id: str
    source_path: str
    filename: str
    mime_type: str | None
    sha256: str
    size_bytes: int
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "doc_id": self.doc_id,
            "source_path": self.source_path,
            "filename": self.filename,
            "mime_type": self.mime_type,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "DocumentRef":
        return cls(
            doc_id=str(payload["doc_id"]),
            source_path=str(payload["source_path"]),
            filename=str(payload["filename"]),
            mime_type=payload.get("mime_type"),
            sha256=str(payload["sha256"]),
            size_bytes=int(payload["size_bytes"]),
            created_at=str(payload.get("created_at") or utc_now()),
        )


@dataclass(frozen=True)
class ContentBlock:
    block_id: str
    doc_id: str
    type: ContentType
    page_idx: int | None
    order: int
    text: str | None = None
    img_path: str | None = None
    table_body: str | None = None
    latex: str | None = None
    caption: list[str] = field(default_factory=list)
    footnote: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def searchable_text(self) -> str:
        parts = [
            self.text or "",
            self.table_body or "",
            self.latex or "",
            " ".join(self.caption),
            " ".join(self.footnote),
            str(self.metadata.get("summary") or ""),
        ]
        return "\n".join(part for part in parts if part).strip()

    def to_dict(self) -> dict[str, Any]:
        return {
            "block_id": self.block_id,
            "doc_id": self.doc_id,
            "type": self.type,
            "page_idx": self.page_idx,
            "order": self.order,
            "text": self.text,
            "img_path": self.img_path,
            "table_body": self.table_body,
            "latex": self.latex,
            "caption": list(self.caption),
            "footnote": list(self.footnote),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ContentBlock":
        return cls(
            block_id=str(payload["block_id"]),
            doc_id=str(payload["doc_id"]),
            type=payload["type"],
            page_idx=payload.get("page_idx"),
            order=int(payload.get("order", 0)),
            text=payload.get("text"),
            img_path=payload.get("img_path"),
            table_body=payload.get("table_body"),
            latex=payload.get("latex"),
            caption=list(payload.get("caption") or []),
            footnote=list(payload.get("footnote") or []),
            metadata=dict(payload.get("metadata") or {}),
        )

    def to_raganything_content(self) -> dict[str, Any]:
        base: dict[str, Any] = {"type": self.type, "page_idx": self.page_idx}
        if self.type == "text":
            base["text"] = self.text or ""
        elif self.type in {"image", "chart"}:
            if not self.img_path:
                raise ValueError(f"image block {self.block_id} has no img_path")
            image_path = Path(self.img_path)
            if not image_path.is_absolute():
                raise ValueError(f"RAG-Anything image paths must be absolute: {self.img_path}")
            base = {"type": "image", "img_path": str(image_path), "page_idx": self.page_idx}
            if self.caption:
                base["image_caption"] = list(self.caption)
        elif self.type == "table":
            base["table_body"] = self.table_body or self.text or ""
            if self.caption:
                base["table_caption"] = list(self.caption)
        elif self.type == "equation":
            base["latex"] = self.latex or self.text or ""
            if self.text:
                base["text"] = self.text
        else:
            base = {
                "type": "custom",
                "content": self.text or self.table_body or self.latex or "",
                "page_idx": self.page_idx,
            }
        if self.footnote:
            base["footnote"] = list(self.footnote)
        if self.metadata:
            base["metadata"] = dict(self.metadata)
        return base


@dataclass(frozen=True)
class ParsedDocument:
    document: DocumentRef
    content: list[ContentBlock]
    parser: str
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "document": self.document.to_dict(),
            "content": [block.to_dict() for block in self.content],
            "parser": self.parser,
            "warnings": list(self.warnings),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ParsedDocument":
        return cls(
            document=DocumentRef.from_dict(payload["document"]),
            content=[ContentBlock.from_dict(item) for item in payload.get("content", [])],
            parser=str(payload.get("parser") or "unknown"),
            warnings=list(payload.get("warnings") or []),
        )

    def counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for block in self.content:
            counts[block.type] = counts.get(block.type, 0) + 1
        return counts


@dataclass(frozen=True)
class QueryRequest:
    query: str
    doc_ids: list[str] | None = None
    mode: Literal["local", "hybrid", "raganything"] = "local"
    top_k: int = 8
    multimodal_content: list[dict[str, Any]] | None = None


@dataclass(frozen=True)
class QueryHit:
    doc_id: str
    block_id: str
    type: str
    score: float
    page_idx: int | None
    snippet: str
    source_path: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "doc_id": self.doc_id,
            "block_id": self.block_id,
            "type": self.type,
            "score": self.score,
            "page_idx": self.page_idx,
            "snippet": self.snippet,
            "source_path": self.source_path,
        }


@dataclass(frozen=True)
class QueryResult:
    answer: str
    hits: list[QueryHit]
    mode: str
    degraded: bool
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "answer": self.answer,
            "hits": [hit.to_dict() for hit in self.hits],
            "mode": self.mode,
            "degraded": self.degraded,
            "warnings": list(self.warnings),
        }


def to_raganything_content_list(blocks: list[ContentBlock]) -> list[dict[str, Any]]:
    return [block.to_raganything_content() for block in blocks]
