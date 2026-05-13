"""Transport adapter for syncing eidocs ingest events into eimemory."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from .schema import ParsedDocument


EI_DOCS_EVENT_SOURCE = "eidocs"
EI_DOCS_DOCUMENT_INGESTED_EVENT = "document_ingested_v1"


def build_memory_event(parsed: ParsedDocument, *, index_ref: str) -> dict[str, Any]:
    """Build a boundary event payload for external eimemory consumers."""
    counts = parsed.counts()
    summary = _summary(parsed)
    return {
        "type": EI_DOCS_DOCUMENT_INGESTED_EVENT,
        "source": EI_DOCS_EVENT_SOURCE,
        "doc_id": parsed.document.doc_id,
        "filename": parsed.document.filename,
        "sha256": parsed.document.sha256,
        "content_counts": counts,
        "summary": summary,
        "index_ref": index_ref,
        "parser": parsed.parser,
    }


def write_jsonl_event(event: dict[str, Any], path: Path) -> None:
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")


def sync_to_eimemory(
    parsed: ParsedDocument,
    *,
    root: Path,
    apply: bool = False,
    eimemory_cmd: str | None = None,
    jsonl_path: Path | None = None,
) -> dict[str, Any]:
    """Run a CLI sync in dry-run or apply mode.

    Keep transport responsibilities here; parsing and replay/evolution remain in
    their owning domains (`eidocs` schema, `eitraining`/`eiskills` replay logic).
    """
    event = build_memory_event(parsed, index_ref=str(Path(root).expanduser() / "indexes"))
    if jsonl_path:
        write_jsonl_event(event, jsonl_path)
    if not apply:
        return {"ok": True, "dry_run": True, "event": event}
    command = eimemory_cmd or _default_eimemory_cmd()
    text = json.dumps(event, ensure_ascii=False, sort_keys=True)
    proc = subprocess.run(
        [command, "ingest", text, "--title", f"Document ingested: {parsed.document.filename}", "--memory-type", "document"],
        check=False,
        text=True,
        capture_output=True,
        timeout=30,
    )
    return {
        "ok": proc.returncode == 0,
        "dry_run": False,
        "event": event,
        "returncode": proc.returncode,
        "stdout": proc.stdout[-2000:],
        "stderr": proc.stderr[-2000:],
    }


def _default_eimemory_cmd() -> str:
    candidate = Path("/opt/eimemory/current/.venv/bin/eimemory")
    if candidate.exists():
        return str(candidate)
    return "eimemory"


def _summary(parsed: ParsedDocument) -> str:
    text_parts: list[str] = []
    for block in parsed.content:
        if block.type == "text" and block.text:
            text_parts.append(block.text.strip())
        elif block.type == "table" and block.table_body:
            text_parts.append("Table: " + block.table_body.strip().splitlines()[0][:160])
        if len(" ".join(text_parts)) > 800:
            break
    summary = " ".join(part for part in text_parts if part)
    if not summary:
        summary = f"Document {parsed.document.filename} contains {sum(parsed.counts().values())} parsed blocks."
    return summary[:1200]
