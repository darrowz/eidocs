from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def stable_id(prefix: str, value: Any, length: int = 20) -> str:
    digest = hashlib.sha256(stable_json(value).encode("utf-8")).hexdigest()[:length]
    return f"{prefix}_{digest}"


def document_id_for(path: Path, sha256: str) -> str:
    return stable_id("doc", {"name": path.name, "sha256": sha256}, 24)
