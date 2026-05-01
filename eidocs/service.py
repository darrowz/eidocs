from __future__ import annotations

import json
from pathlib import Path

from .adapters.raganything_adapter import RAGAnythingAdapter, is_raganything_available
from .audit import AuditLogger
from .errors import RAGAnythingUnavailable
from .index import LocalJsonlIndex
from .parsers import FallbackParser, RAGSubprocessParser
from .parsers.rag_subprocess import COMPLEX_EXTENSIONS
from .schema import ParsedDocument, QueryRequest, QueryResult, to_raganything_content_list
from .security import DocumentPolicy


class EiDocsService:
    def __init__(
        self,
        storage_dir: Path,
        *,
        parser: FallbackParser | None = None,
        rag_adapter: RAGAnythingAdapter | None = None,
        policy: DocumentPolicy | None = None,
    ) -> None:
        self.storage_dir = Path(storage_dir).expanduser()
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        for child in [
            "incoming",
            "raw",
            "jobs",
            "content-lists",
            "indexes",
            "cache",
            "audit",
            "tmp",
            "raganything-output",
        ]:
            (self.storage_dir / child).mkdir(parents=True, exist_ok=True)
        self.parser = parser or FallbackParser()
        self.rag_parser = RAGSubprocessParser(storage_dir=self.storage_dir)
        self.rag_adapter = rag_adapter
        self.policy = policy or DocumentPolicy()
        self.index = LocalJsonlIndex(self.storage_dir)
        self.audit = AuditLogger(self.storage_dir)

    def ingest(
        self,
        path: Path,
        *,
        doc_id: str | None = None,
        use_raganything: bool = False,
        actor: str = "cli",
    ) -> ParsedDocument:
        try:
            assessment = self.policy.validate_path(Path(path))
            source = Path(assessment.path)
            if source.suffix.lower() in COMPLEX_EXTENSIONS or use_raganything:
                parsed = self.rag_parser.parse(source, doc_id=doc_id)
            else:
                parsed = self.parser.parse(source, doc_id=doc_id)
            self.index.add(parsed)
            self._write_content_list(parsed)
            self.audit.write(
                actor=actor,
                op="ingest.completed",
                decision="accepted",
                doc_id=parsed.document.doc_id,
                sha256=parsed.document.sha256,
                ext=assessment.ext,
                size_bytes=assessment.size_bytes,
                parser=parsed.parser,
                counts=parsed.counts(),
                warnings=assessment.warnings + parsed.warnings,
            )
            return parsed
        except Exception as exc:
            self.audit.write(actor=actor, op="ingest.failed", decision="rejected", reason=str(exc), path=str(path))
            raise

    async def ainsert_into_raganything(self, parsed: ParsedDocument) -> object:
        if not self.rag_adapter:
            if not is_raganything_available():
                raise RAGAnythingUnavailable("raganything is not installed in the active Python environment")
            self.rag_adapter = RAGAnythingAdapter(self.storage_dir / "indexes" / "raganything")
        return await self.rag_adapter.insert_content_list(parsed)

    def query(self, request: QueryRequest) -> QueryResult:
        return self.index.query(request)

    async def aquery(self, request: QueryRequest) -> QueryResult:
        if request.mode == "raganything" or request.multimodal_content:
            if not self.rag_adapter:
                return QueryResult(answer="", hits=[], mode="local", degraded=True, warnings=["raganything_unavailable"])
            return await self.rag_adapter.query(request)
        return self.query(request)

    def load_parsed(self, doc_id: str) -> ParsedDocument:
        document = self.index.get_document(doc_id)
        if not document:
            raise KeyError(f"unknown doc_id: {doc_id}")
        return ParsedDocument(document=document, content=self.index.get_blocks(doc_id), parser="local_jsonl")

    def export_content_list(self, doc_id: str) -> list[dict]:
        parsed = self.load_parsed(doc_id)
        return to_raganything_content_list(parsed.content)

    def rag_status(self) -> dict:
        return self.rag_parser.status()

    def _write_content_list(self, parsed: ParsedDocument) -> None:
        payload = {
            "document": parsed.document.to_dict(),
            "content_list": to_raganything_content_list(parsed.content),
            "parser": parsed.parser,
            "warnings": parsed.warnings,
        }
        path = self.storage_dir / "content-lists" / f"{parsed.document.doc_id}.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(path)
