from __future__ import annotations

import datetime as dt
import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .audit import AuditLogger
from .ids import stable_id
from .schema import utc_now
from .security import DocumentPolicy
from .service import EiDocsService


@dataclass
class JobRecord:
    job_id: str
    status: str
    source_path: str
    staged_path: str
    source: str
    collection: str
    created_at: str
    updated_at: str
    use_raganything: bool = False
    actor: str = "cli"
    doc_id: str | None = None
    error: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "status": self.status,
            "source_path": self.source_path,
            "staged_path": self.staged_path,
            "source": self.source,
            "collection": self.collection,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "use_raganything": self.use_raganything,
            "actor": self.actor,
            "doc_id": self.doc_id,
            "error": self.error,
            "meta": dict(self.meta),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "JobRecord":
        return cls(**payload)


class JobStore:
    def __init__(self, root: Path, *, policy: DocumentPolicy | None = None) -> None:
        self.root = Path(root).expanduser()
        self.jobs_dir = self.root / "jobs"
        self.incoming_dir = self.root / "incoming"
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.incoming_dir.mkdir(parents=True, exist_ok=True)
        self.policy = policy or DocumentPolicy()
        self.audit = AuditLogger(self.root)

    def submit_ingest(
        self,
        path: Path,
        *,
        source: str = "cli",
        collection: str = "default",
        use_raganything: bool = False,
        actor: str = "cli",
    ) -> JobRecord:
        assessment = self.policy.validate_path(Path(path))
        existing = self._find_existing(assessment.sha256)
        if existing and existing.status in {"pending", "running", "completed"}:
            self.audit.write(
                actor=actor,
                op="ingest.deduplicated",
                decision="accepted",
                job_id=existing.job_id,
                sha256=assessment.sha256,
                source=source,
            )
            return existing
        job_id = stable_id("job", {"path": assessment.path, "sha256": assessment.sha256, "ts": utc_now()}, 24)
        staged = self.incoming_dir / f"{job_id}{Path(assessment.path).suffix.lower()}"
        shutil.copy2(assessment.path, staged)
        os.chmod(staged, 0o600)
        now = utc_now()
        record = JobRecord(
            job_id=job_id,
            status="pending",
            source_path=assessment.path,
            staged_path=str(staged),
            source=source,
            collection=collection,
            created_at=now,
            updated_at=now,
            use_raganything=use_raganything,
            actor=actor,
            meta={"assessment": assessment.to_dict()},
        )
        self._write(record)
        self.audit.write(
            actor=actor,
            op="ingest.accepted",
            decision="accepted",
            job_id=job_id,
            sha256=assessment.sha256,
            ext=assessment.ext,
            size_bytes=assessment.size_bytes,
            source=source,
        )
        return record

    def get(self, job_id: str) -> JobRecord:
        path = self.jobs_dir / f"{job_id}.json"
        if not path.exists():
            raise KeyError(f"unknown job_id: {job_id}")
        return JobRecord.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def run_once(self, *, limit: int = 1) -> list[JobRecord]:
        lock = self.jobs_dir / ".worker.lock"
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return []
        os.close(fd)
        try:
            processed: list[JobRecord] = []
            for record in self._pending()[: max(1, limit)]:
                processed.append(self._run_record(record))
            return processed
        finally:
            lock.unlink(missing_ok=True)

    def _run_record(self, record: JobRecord) -> JobRecord:
        service = EiDocsService(self.root, policy=self.policy)
        record.status = "running"
        record.updated_at = utc_now()
        self._write(record)
        try:
            parsed = service.ingest(Path(record.staged_path), use_raganything=record.use_raganything, actor=record.actor)
            record.status = "completed"
            record.doc_id = parsed.document.doc_id
            record.error = ""
            record.updated_at = utc_now()
            self.audit.write(actor=record.actor, op="job.completed", decision="accepted", job_id=record.job_id, doc_id=record.doc_id)
        except Exception as exc:
            record.status = "failed"
            record.error = str(exc)
            record.updated_at = utc_now()
            self.audit.write(actor=record.actor, op="job.failed", decision="rejected", job_id=record.job_id, reason=str(exc))
        self._write(record)
        return record

    def prune(self, *, older_than_days: int = 7, dry_run: bool = True) -> dict[str, Any]:
        cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=older_than_days)
        candidates: list[str] = []
        for record in self._all():
            if record.status not in {"failed", "completed"}:
                continue
            updated = dt.datetime.fromisoformat(record.updated_at)
            if updated < cutoff:
                candidates.append(record.job_id)
        if not dry_run:
            for job_id in candidates:
                self.jobs_dir.joinpath(f"{job_id}.json").unlink(missing_ok=True)
        return {"ok": True, "dry_run": dry_run, "candidates": candidates}

    def _pending(self) -> list[JobRecord]:
        return sorted([item for item in self._all() if item.status == "pending"], key=lambda item: item.created_at)

    def _find_existing(self, sha256: str) -> JobRecord | None:
        matches: list[JobRecord] = []
        for record in self._all():
            assessment = dict(record.meta.get("assessment") or {})
            if assessment.get("sha256") == sha256:
                matches.append(record)
        for status in ["running", "pending", "completed"]:
            for record in matches:
                if record.status == status:
                    return record
        return matches[0] if matches else None

    def _all(self) -> list[JobRecord]:
        records: list[JobRecord] = []
        for path in sorted(self.jobs_dir.glob("*.json")):
            records.append(JobRecord.from_dict(json.loads(path.read_text(encoding="utf-8"))))
        return records

    def _write(self, record: JobRecord) -> None:
        path = self.jobs_dir / f"{record.job_id}.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(record.to_dict(), ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(path)
