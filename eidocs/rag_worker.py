from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import Any


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eidocs.rag_worker")
    sub = parser.add_subparsers(dest="command")
    status = sub.add_parser("status")
    status.set_defaults(func=cmd_status)
    parse = sub.add_parser("parse")
    parse.add_argument("--file", required=True)
    parse.add_argument("--output-dir", required=True)
    parse.add_argument("--working-dir", required=True)
    parse.add_argument("--parse-method", default="auto")
    parse.add_argument("--parser", default="mineru")
    parse.add_argument("--fallback-pypdf", action="store_true")
    parse.set_defaults(func=cmd_parse)
    args = parser.parse_args(argv)
    if not getattr(args, "command", ""):
        parser.print_help()
        return 2
    result = args.func(args)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("ok") else 1


def cmd_status(_args: argparse.Namespace) -> dict[str, Any]:
    status: dict[str, Any] = {"ok": True, "raganything": False, "mineru": False, "pypdf": False}
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
    return status


def cmd_parse(args: argparse.Namespace) -> dict[str, Any]:
    return asyncio.run(_parse_async(args))


async def _parse_async(args: argparse.Namespace) -> dict[str, Any]:
    file_path = str(Path(args.file).expanduser().resolve())
    output_dir = str(Path(args.output_dir).expanduser().resolve())
    working_dir = str(Path(args.working_dir).expanduser().resolve())
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    Path(working_dir).mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    try:
        from raganything import RAGAnything, RAGAnythingConfig

        config = RAGAnythingConfig(
            working_dir=working_dir,
            parser_output_dir=output_dir,
            parser=args.parser,
            parse_method=args.parse_method,
            display_content_stats=False,
            use_full_path=True,
        )
        rag = RAGAnything(config=config)
        content_list, content_doc_id = await rag.parse_document(
            file_path=file_path,
            output_dir=output_dir,
            parse_method=args.parse_method,
            display_stats=False,
        )
        return {
            "ok": True,
            "parser": f"raganything:{args.parser}",
            "content_doc_id": content_doc_id,
            "content_list": _normalize_content_list(content_list),
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


def _find_on_path(name: str) -> str:
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        candidate = Path(entry) / name
        if candidate.exists() and os.access(candidate, os.X_OK):
            return str(candidate)
    return ""


if __name__ == "__main__":
    raise SystemExit(main())
