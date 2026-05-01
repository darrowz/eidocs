from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys

from .adapters import is_raganything_available
from .eimemory_sink import sync_to_eimemory
from .errors import EiDocsError
from .jobs import JobStore
from .schema import QueryRequest
from .security import DocumentPolicy
from .service import EiDocsService


def default_root() -> Path:
    return Path(os.environ.get("EIDOCS_ROOT", "~/.local/share/eidocs")).expanduser()


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", ""):
        parser.print_help()
        return 2
    try:
        result = args.func(args)
        if result is not None:
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": {"type": exc.__class__.__name__, "message": str(exc)}}, ensure_ascii=False), file=sys.stderr)
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="eidocs")
    sub = parser.add_subparsers(dest="command")

    ingest = sub.add_parser("ingest")
    ingest.add_argument("file", nargs="?")
    ingest.add_argument("--file", dest="file_opt")
    add_common(ingest)
    ingest.add_argument("--use-raganything", action="store_true")
    ingest.add_argument("--operator-large", action="store_true")
    ingest.set_defaults(func=cmd_ingest)

    query = sub.add_parser("query")
    query.add_argument("query")
    add_common(query)
    query.add_argument("--doc-id", action="append", default=[])
    query.add_argument("--mode", choices=["local", "hybrid", "raganything"], default="local")
    query.add_argument("--top-k", type=int, default=8)
    query.set_defaults(func=cmd_query)

    export = sub.add_parser("export-content-list")
    export.add_argument("doc_id")
    add_common(export)
    export.set_defaults(func=cmd_export_content_list)

    sync = sub.add_parser("sync-eimemory")
    sync.add_argument("doc_id")
    add_common(sync)
    sync.add_argument("--apply", action="store_true")
    sync.add_argument("--dry-run", action="store_true")
    sync.add_argument("--jsonl-path", default="")
    sync.add_argument("--eimemory-cmd", default="")
    sync.set_defaults(func=cmd_sync_eimemory)

    check = sub.add_parser("check-raganything")
    check.set_defaults(func=lambda args: {"ok": True, "available": is_raganything_available()})

    job = sub.add_parser("job")
    job_sub = job.add_subparsers(dest="job_command")
    submit = job_sub.add_parser("submit")
    submit.add_argument("file")
    add_common(submit)
    submit.add_argument("--source", default="cli")
    submit.add_argument("--collection", default="default")
    submit.add_argument("--use-raganything", action="store_true")
    submit.add_argument("--operator-large", action="store_true")
    submit.set_defaults(func=cmd_job_submit)
    status = job_sub.add_parser("status")
    status.add_argument("job_id")
    add_common(status)
    status.set_defaults(func=cmd_job_status)
    run = job_sub.add_parser("run-once")
    add_common(run)
    run.add_argument("--limit", type=int, default=1)
    run.add_argument("--operator-large", action="store_true")
    run.set_defaults(func=cmd_job_run_once)

    audit = sub.add_parser("audit")
    audit_sub = audit.add_subparsers(dest="audit_command")
    tail = audit_sub.add_parser("tail")
    add_common(tail)
    tail.add_argument("--limit", type=int, default=20)
    tail.set_defaults(func=cmd_audit_tail)

    prune = sub.add_parser("prune")
    add_common(prune)
    prune.add_argument("--older-than-days", type=int, default=7)
    prune.add_argument("--dry-run", action="store_true")
    prune.set_defaults(func=cmd_prune)
    return parser


def add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--storage", "--root", dest="storage", default=str(default_root()))


def _policy(args: argparse.Namespace) -> DocumentPolicy:
    return DocumentPolicy.operator_large() if getattr(args, "operator_large", False) else DocumentPolicy()


def _service(args: argparse.Namespace) -> EiDocsService:
    return EiDocsService(Path(args.storage), policy=_policy(args))


def cmd_ingest(args: argparse.Namespace) -> dict:
    file_path = args.file_opt or args.file
    if not file_path:
        raise EiDocsError("ingest requires a file path")
    parsed = _service(args).ingest(Path(file_path), use_raganything=args.use_raganything)
    return {"ok": True, "document": parsed.document.to_dict(), "counts": parsed.counts(), "warnings": parsed.warnings}


def cmd_query(args: argparse.Namespace) -> dict:
    service = _service(args)
    request = QueryRequest(query=args.query, doc_ids=args.doc_id or None, mode=args.mode, top_k=args.top_k)
    if args.mode == "raganything":
        result = asyncio.run(service.aquery(request))
    else:
        result = service.query(request)
    return {"ok": True, "result": result.to_dict()}


def cmd_export_content_list(args: argparse.Namespace) -> dict:
    return {"ok": True, "doc_id": args.doc_id, "content_list": _service(args).export_content_list(args.doc_id)}


def cmd_sync_eimemory(args: argparse.Namespace) -> dict:
    parsed = _service(args).load_parsed(args.doc_id)
    return sync_to_eimemory(
        parsed,
        root=Path(args.storage),
        apply=bool(args.apply and not args.dry_run),
        eimemory_cmd=args.eimemory_cmd or None,
        jsonl_path=Path(args.jsonl_path) if args.jsonl_path else None,
    )


def cmd_job_submit(args: argparse.Namespace) -> dict:
    store = JobStore(Path(args.storage), policy=_policy(args))
    record = store.submit_ingest(
        Path(args.file),
        source=args.source,
        collection=args.collection,
        use_raganything=args.use_raganything,
        actor=args.source,
    )
    return {"ok": True, "job": record.to_dict()}


def cmd_job_status(args: argparse.Namespace) -> dict:
    return {"ok": True, "job": JobStore(Path(args.storage)).get(args.job_id).to_dict()}


def cmd_job_run_once(args: argparse.Namespace) -> dict:
    records = JobStore(Path(args.storage), policy=_policy(args)).run_once(limit=args.limit)
    return {"ok": True, "jobs": [record.to_dict() for record in records]}


def cmd_audit_tail(args: argparse.Namespace) -> dict:
    return {"ok": True, "events": _service(args).audit.tail(args.limit)}


def cmd_prune(args: argparse.Namespace) -> dict:
    return JobStore(Path(args.storage)).prune(older_than_days=args.older_than_days, dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
