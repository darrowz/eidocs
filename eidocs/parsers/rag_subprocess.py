from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from eidocs.errors import UnsupportedDocumentType
from eidocs.ids import document_id_for, sha256_file, stable_id
from eidocs.schema import ContentBlock, DocumentRef, ParsedDocument
from eidocs.security import detect_magic, read_prefix


COMPLEX_EXTENSIONS = {".pdf", ".docx", ".pptx", ".xlsx"}


class RAGSubprocessParser:
    name = "raganything"

    def __init__(
        self,
        *,
        storage_dir: Path,
        python_executable: str | None = None,
        timeout_seconds: int | None = None,
    ) -> None:
        self.storage_dir = Path(storage_dir).expanduser()
        self.python_executable = python_executable or os.environ.get("EIDOCS_RAG_PYTHON") or "/dev-project/eidocs/.venv-rag/bin/python"
        self.timeout_seconds = timeout_seconds or int(os.environ.get("EIDOCS_RAG_TIMEOUT_SECONDS", "1200"))

    def supports(self, path: Path) -> bool:
        return Path(path).suffix.lower() in COMPLEX_EXTENSIONS

    def parse(self, path: Path, *, doc_id: str | None = None) -> ParsedDocument:
        source = Path(path).expanduser().resolve()
        if not self.supports(source):
            raise UnsupportedDocumentType(f"RAG subprocess parser does not support {source.suffix}")
        sha = sha256_file(source)
        doc_id = doc_id or document_id_for(source, sha)
        output_dir = self.storage_dir / "raganything-output" / doc_id
        working_dir = self.storage_dir / "indexes" / "raganything" / doc_id
        result = self._run_worker(source, output_dir=output_dir, working_dir=working_dir)
        content_list = result.get("content_list") or []
        if not content_list:
            raise UnsupportedDocumentType("RAG-Anything returned an empty content_list")
        document = DocumentRef(
            doc_id=doc_id,
            source_path=str(source),
            filename=source.name,
            mime_type=detect_magic(read_prefix(source, 4096)),
            sha256=sha,
            size_bytes=source.stat().st_size,
        )
        blocks = _blocks_from_content_list(doc_id, content_list, asset_base_dir=Path(result.get("output_dir") or output_dir))
        warnings = list(result.get("warnings") or [])
        metadata_warning = f"raganything_output_dir:{result.get('output_dir') or ''}"
        if result.get("output_dir"):
            warnings.append(metadata_warning)
        return ParsedDocument(document=document, content=blocks, parser=str(result.get("parser") or self.name), warnings=warnings)

    def status(self) -> dict[str, Any]:
        env = self._env()
        proc = subprocess.run(
            [self.python_executable, "-m", "eidocs.rag_worker", "status"],
            text=True,
            capture_output=True,
            timeout=60,
            env=env,
        )
        return _parse_worker_json(proc)

    def _run_worker(self, source: Path, *, output_dir: Path, working_dir: Path) -> dict[str, Any]:
        output_dir.mkdir(parents=True, exist_ok=True)
        working_dir.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run(
            [
                self.python_executable,
                "-m",
                "eidocs.rag_worker",
                "parse",
                "--file",
                str(source),
                "--output-dir",
                str(output_dir),
                "--working-dir",
                str(working_dir),
                "--parse-method",
                os.environ.get("EIDOCS_RAG_PARSE_METHOD", "auto"),
                "--parser",
                os.environ.get("EIDOCS_RAG_PARSER", "mineru"),
                "--fallback-pypdf",
            ],
            text=True,
            capture_output=True,
            timeout=self.timeout_seconds,
            env=self._env(),
        )
        payload = _parse_worker_json(proc)
        if proc.returncode != 0 or not payload.get("ok"):
            message = payload.get("errors") or proc.stderr[-2000:] or proc.stdout[-2000:]
            raise UnsupportedDocumentType(f"RAG-Anything parse failed: {message}")
        return payload

    def _env(self) -> dict[str, str]:
        env = dict(os.environ)
        source_dir = os.environ.get("EIDOCS_SOURCE_DIR", "/dev-project/eidocs")
        rag_bin = str(Path(self.python_executable).expanduser().parent.resolve())
        env["PYTHONPATH"] = source_dir + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        env["PATH"] = rag_bin + os.pathsep + env.get("PATH", "")
        return env


def _parse_worker_json(proc: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    raw = (proc.stdout or "").strip()
    last_line = raw.splitlines()[-1] if raw else "{}"
    try:
        payload = json.loads(last_line)
    except json.JSONDecodeError:
        payload = {"ok": False, "stdout": proc.stdout[-2000:], "stderr": proc.stderr[-2000:]}
    if proc.stderr:
        payload.setdefault("stderr_tail", proc.stderr[-2000:])
    return payload


def _blocks_from_content_list(doc_id: str, content_list: list[dict[str, Any]], asset_base_dir: Path | None = None) -> list[ContentBlock]:
    blocks: list[ContentBlock] = []
    for order, item in enumerate(content_list):
        block_type = _normalize_type(str(item.get("type") or "custom"))
        kwargs: dict[str, Any] = {
            "page_idx": item.get("page_idx"),
            "metadata": {
                key: value
                for key, value in item.items()
                if key
                not in {
                    "type",
                    "text",
                    "img_path",
                    "image_caption",
                    "image_footnote",
                    "table_body",
                    "table_caption",
                    "table_footnote",
                    "latex",
                    "equation_caption",
                    "equation_footnote",
                    "content",
                    "page_idx",
                }
            },
        }
        if block_type == "text":
            kwargs["text"] = str(item.get("text") or item.get("content") or "")
        elif block_type in {"image", "chart"}:
            image_path = str(item.get("img_path") or "")
            captions = _listify(item.get("image_caption") or item.get("caption"))
            footnotes = _listify(item.get("image_footnote") or item.get("footnote"))
            if image_path and not Path(image_path).is_absolute():
                image_path = str(((asset_base_dir or Path.cwd()) / image_path).resolve())
            if not image_path:
                block_type = "custom"
                kwargs["text"] = str(item.get("content") or item.get("text") or " ".join(captions))
            else:
                kwargs["img_path"] = image_path
                kwargs["caption"] = captions
                kwargs["footnote"] = footnotes
                kwargs["text"] = " ".join(captions)
        elif block_type == "table":
            kwargs["table_body"] = str(item.get("table_body") or item.get("text") or item.get("content") or "")
            kwargs["caption"] = _listify(item.get("table_caption") or item.get("caption"))
            kwargs["footnote"] = _listify(item.get("table_footnote") or item.get("footnote"))
            kwargs["text"] = kwargs["table_body"]
        elif block_type == "equation":
            kwargs["latex"] = str(item.get("latex") or item.get("text") or item.get("content") or "")
            kwargs["text"] = str(item.get("text") or kwargs["latex"])
            kwargs["caption"] = _listify(item.get("equation_caption") or item.get("caption"))
            kwargs["footnote"] = _listify(item.get("equation_footnote") or item.get("footnote"))
        else:
            kwargs["text"] = str(item.get("content") or item.get("text") or "")
        payload = {"doc_id": doc_id, "type": block_type, "order": order, **kwargs}
        block_id = stable_id("blk", payload, 24)
        blocks.append(ContentBlock(block_id=block_id, doc_id=doc_id, type=block_type, order=order, **kwargs))
    return blocks


def _normalize_type(value: str) -> str:
    lowered = value.lower()
    if lowered in {"text", "image", "table", "equation", "chart"}:
        return lowered
    if lowered in {"formula"}:
        return "equation"
    if lowered in {"figure", "img"}:
        return "image"
    return "custom"


def _listify(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if item is not None]
    return [str(value)]
