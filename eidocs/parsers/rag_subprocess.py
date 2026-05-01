from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

from eidocs.errors import UnsupportedDocumentType
from eidocs.ids import document_id_for, sha256_file, stable_id
from eidocs.schema import ContentBlock, DocumentRef, ParsedDocument, QueryHit, QueryRequest, QueryResult
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
        self.timeout_seconds = timeout_seconds or int(os.environ.get("EIDOCS_RAG_TIMEOUT_SECONDS", "300"))

    def supports(self, path: Path) -> bool:
        return Path(path).suffix.lower() in COMPLEX_EXTENSIONS

    def parse(self, path: Path, *, doc_id: str | None = None) -> ParsedDocument:
        source = Path(path).expanduser().resolve()
        if not self.supports(source):
            raise UnsupportedDocumentType(f"RAG subprocess parser does not support {source.suffix}")
        sha = sha256_file(source)
        doc_id = doc_id or document_id_for(source, sha)
        output_dir = self.storage_dir / "raganything-output" / doc_id
        working_dir = self._working_dir(doc_id)
        result = self._run_parse_worker(source, output_dir=output_dir, working_dir=working_dir, doc_id=doc_id)
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
        if result.get("output_dir"):
            warnings.append(f"raganything_output_dir:{result.get('output_dir')}")
        if result.get("lightrag_inserted"):
            warnings.append(f"lightrag_index_dir:{working_dir}")
        return ParsedDocument(document=document, content=blocks, parser=str(result.get("parser") or self.name), warnings=warnings)

    def query(self, request: QueryRequest) -> QueryResult:
        if not request.doc_ids or len(request.doc_ids) != 1:
            return QueryResult(answer="", hits=[], mode="raganything", degraded=True, warnings=["raganything_query_requires_one_doc_id"])
        doc_id = request.doc_ids[0]
        payload = self._run_query_worker(doc_id=doc_id, query=request.query, mode=request.mode)
        answer = str(payload.get("answer") or "")
        return QueryResult(
            answer=answer,
            hits=[
                QueryHit(
                    doc_id=doc_id,
                    block_id="lightrag_answer",
                    type="raganswer",
                    score=1.0 if answer else 0.0,
                    page_idx=None,
                    snippet=answer[:800],
                    source_path=str(self._working_dir(doc_id)),
                )
            ]
            if answer
            else [],
            mode="raganything",
            degraded=False,
            warnings=[],
        )

    def status(self) -> dict[str, Any]:
        proc = subprocess.run(
            [self.python_executable, "-m", "eidocs.rag_worker", "status"],
            text=True,
            capture_output=True,
            timeout=60,
            env=self._env(),
        )
        return _parse_worker_json(proc)

    def _run_parse_worker(self, source: Path, *, output_dir: Path, working_dir: Path, doc_id: str) -> dict[str, Any]:
        output_dir.mkdir(parents=True, exist_ok=True)
        working_dir.mkdir(parents=True, exist_ok=True)
        command = [
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
            "--doc-id",
            doc_id,
            "--parse-method",
            os.environ.get("EIDOCS_RAG_PARSE_METHOD", "auto"),
            "--parser",
            os.environ.get("EIDOCS_RAG_PARSER", "mineru"),
            "--fallback-pypdf",
        ]
        if os.environ.get("EIDOCS_RAG_INDEX", "1") != "0":
            command.append("--insert-lightrag")
        try:
            proc = self._run_worker(command, timeout=self.timeout_seconds)
        except subprocess.TimeoutExpired:
            return self._run_pypdf_fallback_worker(
                source,
                output_dir=output_dir,
                working_dir=working_dir,
                warning=f"raganything_timeout_after:{self.timeout_seconds}",
            )
        payload = _parse_worker_json(proc)
        if proc.returncode != 0 or not payload.get("ok"):
            message = payload.get("errors") or proc.stderr[-2000:] or proc.stdout[-2000:]
            raise UnsupportedDocumentType(f"RAG-Anything parse failed: {message}")
        return payload

    def _run_pypdf_fallback_worker(self, source: Path, *, output_dir: Path, working_dir: Path, warning: str) -> dict[str, Any]:
        command = [
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
            "--parser",
            "pypdf",
            "--fallback-pypdf",
        ]
        proc = self._run_worker(command, timeout=int(os.environ.get("EIDOCS_RAG_FALLBACK_TIMEOUT_SECONDS", "600")))
        payload = _parse_worker_json(proc)
        if proc.returncode != 0 or not payload.get("ok"):
            message = payload.get("errors") or proc.stderr[-2000:] or proc.stdout[-2000:]
            raise UnsupportedDocumentType(f"RAG-Anything timeout and pypdf fallback failed: {message}")
        warnings = list(payload.get("warnings") or [])
        warnings.append(warning)
        payload["warnings"] = warnings
        return payload

    def _run_worker(self, command: list[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
        proc = subprocess.Popen(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=self._env(),
            start_new_session=True,
        )
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            self._terminate_process_tree(proc)
            exc.output = exc.output or ""
            exc.stderr = exc.stderr or ""
            raise
        return subprocess.CompletedProcess(command, proc.returncode, stdout, stderr)

    def _terminate_process_tree(self, proc: subprocess.Popen[str]) -> None:
        descendants = self._descendant_pids(proc.pid)
        self._signal_process_group(proc.pid, signal.SIGTERM)
        self._signal_pids(descendants, signal.SIGTERM)
        try:
            proc.communicate(timeout=10)
        except Exception:
            pass
        time.sleep(1)
        remaining = [pid for pid in descendants if self._pid_exists(pid)]
        if proc.poll() is None or remaining:
            self._signal_process_group(proc.pid, signal.SIGKILL)
            self._signal_pids(remaining, signal.SIGKILL)
            if proc.poll() is None:
                proc.kill()
            proc.communicate()

    def _descendant_pids(self, root_pid: int) -> list[int]:
        try:
            proc = subprocess.run(["ps", "-eo", "pid=,ppid="], text=True, capture_output=True, timeout=5)
        except Exception:
            return []
        children: dict[int, list[int]] = {}
        for line in proc.stdout.splitlines():
            parts = line.split()
            if len(parts) != 2:
                continue
            pid, ppid = int(parts[0]), int(parts[1])
            children.setdefault(ppid, []).append(pid)
        found: list[int] = []
        stack = list(children.get(root_pid, []))
        while stack:
            pid = stack.pop()
            found.append(pid)
            stack.extend(children.get(pid, []))
        return found

    def _signal_process_group(self, pid: int, signum: int) -> None:
        try:
            os.killpg(pid, signum)
        except Exception:
            pass

    def _signal_pids(self, pids: list[int], signum: int) -> None:
        for pid in sorted(set(pids), reverse=True):
            try:
                os.kill(pid, signum)
            except Exception:
                pass

    def _pid_exists(self, pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    def _run_query_worker(self, *, doc_id: str, query: str, mode: str) -> dict[str, Any]:
        proc = subprocess.run(
            [
                self.python_executable,
                "-m",
                "eidocs.rag_worker",
                "query",
                "--working-dir",
                str(self._working_dir(doc_id)),
                "--query",
                query,
                "--mode",
                mode,
            ],
            text=True,
            capture_output=True,
            timeout=int(os.environ.get("EIDOCS_RAG_QUERY_TIMEOUT_SECONDS", "240")),
            env=self._env(),
        )
        payload = _parse_worker_json(proc)
        if proc.returncode != 0 or not payload.get("ok"):
            message = payload.get("errors") or proc.stderr[-2000:] or proc.stdout[-2000:]
            raise UnsupportedDocumentType(f"RAG-Anything query failed: {message}")
        return payload

    def _working_dir(self, doc_id: str) -> Path:
        return self.storage_dir / "indexes" / "raganything" / doc_id

    def _env(self) -> dict[str, str]:
        env = dict(os.environ)
        source_dir = os.environ.get("EIDOCS_SOURCE_DIR", "/dev-project/eidocs")
        rag_bin = str(Path(self.python_executable).expanduser().parent.resolve())
        env["PYTHONPATH"] = source_dir + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        env["PATH"] = rag_bin + os.pathsep + env.get("PATH", "")
        env.setdefault("EIDOCS_ENV_FILE", "/home/darrow/api-keys.env")
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
