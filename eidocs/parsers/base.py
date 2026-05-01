from __future__ import annotations

from pathlib import Path
from typing import Protocol

from eidocs.schema import ParsedDocument


class DocumentParser(Protocol):
    name: str

    def supports(self, path: Path) -> bool:
        ...

    def parse(self, path: Path, *, doc_id: str | None = None) -> ParsedDocument:
        ...
