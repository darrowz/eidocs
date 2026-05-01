from __future__ import annotations

import argparse
import asyncio
from functools import partial
import json
import os
from pathlib import Path
import urllib.error
import urllib.request
from typing import Any


DEFAULT_ENV_FILE = "/home/darrow/api-keys.env"


def main(argv: list[str] | None = None) -> int:
    _load_env_file(Path(os.environ.get("EIDOCS_ENV_FILE", DEFAULT_ENV_FILE)))
    parser = argparse.ArgumentParser(prog="python -m eidocs.rag_worker")
    sub = parser.add_subparsers(dest="command")
    status = sub.add_parser("status")
    status.set_defaults(func=cmd_status)

    parse = sub.add_parser("parse")
    parse.add_argument("--file", required=True)
    parse.add_argument("--output-dir", required=True)
    parse.add_argument("--working-dir", required=True)
    parse.add_argument("--doc-id", default="")
    parse.add_argument("--parse-method", default="auto")
    parse.add_argument("--parser", default="mineru")
    parse.add_argument("--fallback-pypdf", action="store_true")
    parse.add_argument("--insert-lightrag", action="store_true")
    parse.set_defaults(func=cmd_parse)

    query = sub.add_parser("query")
    query.add_argument("--working-dir", required=True)
    query.add_argument("--query", required=True)
    query.add_argument("--mode", default="hybrid")
    query.set_defaults(func=cmd_query)

    args = parser.parse_args(argv)
    if not getattr(args, "command", ""):
        parser.print_help()
        return 2
    result = args.func(args)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("ok") else 1


def cmd_status(_args: argparse.Namespace) -> dict[str, Any]:
    status: dict[str, Any] = {
        "ok": True,
        "raganything": False,
        "mineru": False,
        "pypdf": False,
        "embedding": {
            "provider": os.environ.get("EIDOCS_EMBEDDING_PROVIDER", "ollama"),
            "host": os.environ.get("EIDOCS_OLLAMA_HOST", "http://honjia:11434"),
            "model": os.environ.get("EIDOCS_OLLAMA_EMBED_MODEL", "mxbai-embed-large:latest"),
            "dim": int(os.environ.get("EIDOCS_EMBEDDING_DIM", "1024")),
        },
        "llm": {
            "provider": "openai-compatible",
            "endpoint": _chat_endpoint(),
            "model": _llm_model(),
            "api_key_present": bool(_api_key()),
        },
    }
    try:
        import raganything  # noqa: F401

        status["raganything"] = True
    except Exception as exc:
        status["raganything_error"] = f"{exc.__class__.__name__}: {exc}"
    try:
        import pypdf  # noqa: F401

        status["pypdf"] = True
    except Exception as exc:
        status["pypdf_error"] = f"{exc.__class__.__name__}: {exc}"
    mineru_bin = _find_on_path("mineru")
    status["mineru"] = bool(mineru_bin)
    status["mineru_bin"] = mineru_bin or ""
    status["ollama_reachable"] = _ollama_reachable(status["embedding"]["host"])
    return status


def cmd_parse(args: argparse.Namespace) -> dict[str, Any]:
    return asyncio.run(_parse_async(args))


def cmd_query(args: argparse.Namespace) -> dict[str, Any]:
    return asyncio.run(_query_async(args))


async def _parse_async(args: argparse.Namespace) -> dict[str, Any]:
    file_path = str(Path(args.file).expanduser().resolve())
    output_dir = str(Path(args.output_dir).expanduser().resolve())
    working_dir = str(Path(args.working_dir).expanduser().resolve())
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    Path(working_dir).mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    try:
        rag = _build_rag(working_dir=working_dir, output_dir=output_dir, parser=args.parser, parse_method=args.parse_method)
        content_list, content_doc_id = await rag.parse_document(
            file_path=file_path,
            output_dir=output_dir,
            parse_method=args.parse_method,
            display_stats=False,
        )
        insert_result = None
        if args.insert_lightrag:
            insert_result = await rag.insert_content_list(
                content_list=content_list,
                file_path=file_path,
                doc_id=args.doc_id or None,
                display_stats=False,
            )
        return {
            "ok": True,
            "parser": f"raganything:{args.parser}",
            "content_doc_id": content_doc_id,
            "content_list": _normalize_content_list(content_list),
            "lightrag_inserted": bool(args.insert_lightrag),
            "insert_result": _short_value(insert_result),
            "output_dir": output_dir,
            "warnings": [],
        }
    except Exception as exc:
        errors.append(f"raganything_parse_failed:{exc.__class__.__name__}:{exc}")
    if args.fallback_pypdf and Path(file_path).suffix.lower() == ".pdf":
        try:
            return _parse_with_pypdf(file_path, errors)
        except Exception as exc:
            errors.append(f"pypdf_parse_failed:{exc.__class__.__name__}:{exc}")
    return {"ok": False, "errors": errors, "content_list": []}


async def _query_async(args: argparse.Namespace) -> dict[str, Any]:
    working_dir = str(Path(args.working_dir).expanduser().resolve())
    rag = _build_rag(working_dir=working_dir, output_dir=str(Path(working_dir) / "parser-output"))
    init_result = await rag._ensure_lightrag_initialized()
    if not init_result.get("success"):
        return {"ok": False, "errors": [init_result.get("error") or "lightrag_init_failed"]}
    result = await rag.aquery(args.query, mode=_map_query_mode(args.mode), vlm_enhanced=False)
    return {"ok": True, "answer": str(result), "mode": args.mode}


def _build_rag(*, working_dir: str, output_dir: str, parser: str = "mineru", parse_method: str = "auto"):
    from raganything import RAGAnything, RAGAnythingConfig

    config = RAGAnythingConfig(
        working_dir=working_dir,
        parser_output_dir=output_dir,
        parser=parser,
        parse_method=parse_method,
        display_content_stats=False,
        use_full_path=True,
        max_concurrent_files=1,
    )
    return RAGAnything(
        llm_model_func=_llm_model_func,
        vision_model_func=_vision_model_func,
        embedding_func=_embedding_func(),
        config=config,
        lightrag_kwargs={
            "llm_model_name": _llm_model(),
            "llm_model_max_async": int(os.environ.get("EIDOCS_LLM_MAX_ASYNC", "1")),
            "embedding_func_max_async": int(os.environ.get("EIDOCS_EMBEDDING_MAX_ASYNC", "2")),
            "default_llm_timeout": int(os.environ.get("EIDOCS_LLM_TIMEOUT", "180")),
            "default_embedding_timeout": int(os.environ.get("EIDOCS_EMBEDDING_TIMEOUT", "60")),
            "chunk_token_size": int(os.environ.get("EIDOCS_RAG_CHUNK_TOKEN_SIZE", "900")),
            "chunk_overlap_token_size": int(os.environ.get("EIDOCS_RAG_CHUNK_OVERLAP", "80")),
            "entity_extract_max_gleaning": int(os.environ.get("EIDOCS_ENTITY_EXTRACT_MAX_GLEANING", "0")),
            "max_parallel_insert": 1,
        },
    )


async def _llm_model_func(prompt, system_prompt=None, history_messages=None, **kwargs):
    return await _openai_compatible_complete(prompt, system_prompt=system_prompt, history_messages=history_messages, **kwargs)


async def _vision_model_func(prompt, system_prompt=None, history_messages=None, image_data=None, messages=None, **kwargs):
    return await _openai_compatible_complete(
        prompt,
        system_prompt=system_prompt,
        history_messages=history_messages,
        image_data=image_data,
        messages=messages,
        **kwargs,
    )


def _embedding_func():
    from lightrag.llm.ollama import ollama_embed
    from lightrag.utils import EmbeddingFunc

    return EmbeddingFunc(
        embedding_dim=int(os.environ.get("EIDOCS_EMBEDDING_DIM", "1024")),
        max_token_size=int(os.environ.get("EIDOCS_EMBEDDING_MAX_TOKENS", "8192")),
        model_name=os.environ.get("EIDOCS_OLLAMA_EMBED_MODEL", "mxbai-embed-large:latest"),
        func=partial(
            ollama_embed.func,
            embed_model=os.environ.get("EIDOCS_OLLAMA_EMBED_MODEL", "mxbai-embed-large:latest"),
            host=os.environ.get("EIDOCS_OLLAMA_HOST", "http://honjia:11434"),
            timeout=int(os.environ.get("EIDOCS_OLLAMA_TIMEOUT", "120")),
        ),
    )


async def _openai_compatible_complete(
    prompt: str,
    *,
    system_prompt: str | None = None,
    history_messages: list[dict[str, Any]] | None = None,
    image_data: str | None = None,
    messages: list[dict[str, Any]] | None = None,
    **kwargs: Any,
) -> str:
    payload_messages: list[dict[str, Any]]
    if messages:
        payload_messages = [item for item in messages if item]
    else:
        payload_messages = []
        if system_prompt:
            payload_messages.append({"role": "system", "content": system_prompt})
        payload_messages.extend(history_messages or [])
        if image_data:
            payload_messages.append(
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_data}"}},
                    ],
                }
            )
        else:
            payload_messages.append({"role": "user", "content": prompt})
    payload: dict[str, Any] = {
        "model": _llm_model(),
        "messages": payload_messages,
        "temperature": float(os.environ.get("EIDOCS_LLM_TEMPERATURE", "0")),
        "max_tokens": int(kwargs.get("max_tokens") or os.environ.get("EIDOCS_LLM_MAX_TOKENS", "2048")),
    }
    if kwargs.get("response_format") is not None:
        payload["response_format"] = kwargs["response_format"]
    return await asyncio.to_thread(_post_chat_completion, payload)


def _post_chat_completion(payload: dict[str, Any]) -> str:
    key = _api_key()
    if not key:
        raise RuntimeError("missing DashScope/Bailian API key for eidocs RAG LLM")
    req = urllib.request.Request(
        _chat_endpoint(),
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
    )
    with urllib.request.urlopen(req, timeout=int(os.environ.get("EIDOCS_LLM_TIMEOUT", "180"))) as response:
        data = json.loads(response.read().decode("utf-8"))
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError(f"LLM response has no choices: {data}")
    message = choices[0].get("message") or {}
    content = message.get("content") or message.get("reasoning_content") or ""
    if isinstance(content, list):
        content = "".join(str(item.get("text", item)) if isinstance(item, dict) else str(item) for item in content)
    return str(content)


def _parse_with_pypdf(file_path: str, errors: list[str]) -> dict[str, Any]:
    from pypdf import PdfReader

    reader = PdfReader(file_path)
    content: list[dict[str, Any]] = []
    for page_idx, page in enumerate(reader.pages):
        text = (page.extract_text() or "").strip()
        if text:
            content.append({"type": "text", "text": text, "page_idx": page_idx})
    return {
        "ok": True,
        "parser": "pypdf:fallback",
        "content_doc_id": "",
        "content_list": content,
        "lightrag_inserted": False,
        "output_dir": "",
        "warnings": errors + ["raganything_degraded_to_pypdf"],
    }


def _normalize_content_list(content_list: Any) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    if not isinstance(content_list, list):
        return normalized
    for item in content_list:
        if not isinstance(item, dict):
            continue
        payload = dict(item)
        if "page_idx" in payload and payload["page_idx"] is not None:
            try:
                payload["page_idx"] = int(payload["page_idx"])
            except (TypeError, ValueError):
                payload["page_idx"] = None
        normalized.append(payload)
    return normalized


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"").strip("'")
        os.environ.setdefault(key, value)


def _chat_endpoint() -> str:
    return (
        os.environ.get("EIDOCS_LLM_ENDPOINT")
        or os.environ.get("BAILIAN_BASE_URL")
        or "https://coding.dashscope.aliyuncs.com/v1/chat/completions"
    )


def _llm_model() -> str:
    return os.environ.get("EIDOCS_LLM_MODEL") or os.environ.get("LLM_MODEL") or "qwen3-max-2026-01-23"


def _api_key() -> str:
    return os.environ.get("EIDOCS_LLM_API_KEY") or os.environ.get("DASHSCOPE_API_KEY") or os.environ.get("BAILIAN_API_KEY") or ""


def _find_on_path(name: str) -> str:
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        candidate = Path(entry) / name
        if candidate.exists() and os.access(candidate, os.X_OK):
            return str(candidate)
    return ""


def _ollama_reachable(host: str) -> bool:
    try:
        with urllib.request.urlopen(host.rstrip("/") + "/api/tags", timeout=5) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False


def _map_query_mode(mode: str) -> str:
    if mode in {"hybrid", "raganything"}:
        return "hybrid"
    return mode


def _short_value(value: Any) -> str:
    if value is None:
        return ""
    return str(value)[:500]


if __name__ == "__main__":
    raise SystemExit(main())
