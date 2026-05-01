from __future__ import annotations

import importlib
import importlib.util
import inspect
from pathlib import Path
from typing import Any, Callable

from eidocs.errors import RAGAnythingUnavailable
from eidocs.schema import ParsedDocument, QueryHit, QueryRequest, QueryResult, to_raganything_content_list


def is_raganything_available() -> bool:
    return importlib.util.find_spec("raganything") is not None


class RAGAnythingAdapter:
    def __init__(
        self,
        working_dir: Path,
        llm_model_func: Callable | None = None,
        vision_model_func: Callable | None = None,
        embedding_func: Any | None = None,
        config_overrides: dict[str, Any] | None = None,
    ) -> None:
        if not is_raganything_available():
            raise RAGAnythingUnavailable("raganything is not installed; install eidocs[raganything] to enable it")
        module = importlib.import_module("raganything")
        rag_cls = getattr(module, "RAGAnything")
        config_cls = getattr(module, "RAGAnythingConfig", None)
        working_dir = Path(working_dir).expanduser().resolve()
        working_dir.mkdir(parents=True, exist_ok=True)
        config_kwargs = {"working_dir": str(working_dir)}
        config_kwargs.update(config_overrides or {})
        config = config_cls(**config_kwargs) if config_cls else config_kwargs
        kwargs = {
            "config": config,
            "llm_model_func": llm_model_func,
            "vision_model_func": vision_model_func,
            "embedding_func": embedding_func,
        }
        kwargs = {k: v for k, v in kwargs.items() if v is not None}
        try:
            self._rag = rag_cls(**kwargs)
        except TypeError:
            kwargs.pop("config", None)
            kwargs.update(config_kwargs)
            self._rag = rag_cls(**kwargs)

    async def insert_content_list(self, parsed: ParsedDocument, *, split_by_character: str | None = None) -> Any:
        content = to_raganything_content_list(parsed.content)
        kwargs: dict[str, Any] = {
            "content_list": content,
            "file_path": parsed.document.source_path,
            "doc_id": parsed.document.doc_id,
        }
        if split_by_character is not None:
            kwargs["split_by_character"] = split_by_character
        func = getattr(self._rag, "insert_content_list")
        result = func(**kwargs)
        if inspect.isawaitable(result):
            result = await result
        return result

    async def process_document_complete(
        self,
        path: Path,
        *,
        output_dir: Path,
        parser: str = "mineru",
        parse_method: str = "auto",
        doc_id: str | None = None,
        **kwargs: Any,
    ) -> Any:
        output_dir = Path(output_dir).expanduser().resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        func = getattr(self._rag, "process_document_complete")
        result = func(
            str(Path(path).expanduser().resolve()),
            output_dir=str(output_dir),
            parser=parser,
            parse_method=parse_method,
            doc_id=doc_id,
            **kwargs,
        )
        if inspect.isawaitable(result):
            result = await result
        return result

    async def query(self, request: QueryRequest) -> QueryResult:
        if request.multimodal_content:
            func = getattr(self._rag, "aquery_with_multimodal")
            result = func(request.query, multimodal_content=request.multimodal_content, mode=request.mode)
        else:
            func = getattr(self._rag, "aquery")
            result = func(request.query, mode=request.mode if request.mode != "raganything" else "hybrid")
        if inspect.isawaitable(result):
            result = await result
        return QueryResult(
            answer=str(result),
            hits=[
                QueryHit(
                    doc_id="raganything",
                    block_id="raganything_answer",
                    type="answer",
                    score=1.0,
                    page_idx=None,
                    snippet=str(result)[:500],
                    source_path="raganything",
                )
            ],
            mode="raganything",
            degraded=False,
        )
