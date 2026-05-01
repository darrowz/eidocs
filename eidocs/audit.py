from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


class AuditLogger:
    def __init__(self, root: Path) -> None:
        self.path = Path(root).expanduser() / "audit" / "audit.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, *, actor: str, op: str, decision: str, reason: str = "", **fields: Any) -> dict[str, Any]:
        event = {
            "ts": _now(),
            "actor": actor,
            "op": op,
            "decision": decision,
            "reason": reason,
        }
        event.update(_safe_fields(fields))
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
        return event

    def tail(self, limit: int = 20) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        lines = self.path.read_text(encoding="utf-8").splitlines()[-max(0, limit) :]
        return [json.loads(line) for line in lines if line.strip()]


def _safe_fields(fields: dict[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in fields.items():
        if key in {"text", "content", "prompt", "raw", "ocr"}:
            continue
        safe[key] = value
    return safe
