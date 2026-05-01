from eidocs.jobs import JobStore
from eidocs.schema import QueryRequest
from eidocs.service import EiDocsService


def test_service_ingest_query_and_eimemory_shape(tmp_path):
    doc = tmp_path / "notes.md"
    doc.write_text("Alpha revenue table\n\n| key | value |\n| --- | --- |\n| revenue | 42 |\n", encoding="utf-8")
    service = EiDocsService(tmp_path / "store")
    parsed = service.ingest(doc)
    result = service.query(QueryRequest(query="revenue table", top_k=3))
    assert parsed.document.doc_id
    assert result.hits
    assert result.hits[0].doc_id == parsed.document.doc_id
    content_list = service.export_content_list(parsed.document.doc_id)
    assert content_list


def test_job_submit_and_run_once(tmp_path):
    doc = tmp_path / "job.md"
    doc.write_text("Job document with searchable Alpha token.", encoding="utf-8")
    store = JobStore(tmp_path / "store")
    job = store.submit_ingest(doc, source="openclaw")
    assert job.status == "pending"
    processed = store.run_once(limit=1)
    assert processed[0].status == "completed"
    assert processed[0].doc_id


def test_job_submit_deduplicates_same_file_sha(tmp_path):
    doc = tmp_path / "dup.md"
    doc.write_text("Duplicate document", encoding="utf-8")
    store = JobStore(tmp_path / "store")
    first = store.submit_ingest(doc, source="openclaw")
    second = store.submit_ingest(doc, source="openclaw")
    assert second.job_id == first.job_id
    assert len(list(store.jobs_dir.glob("*.json"))) == 1


def test_job_run_once_returns_empty_when_worker_lock_exists(tmp_path):
    store = JobStore(tmp_path / "store")
    lock = store.jobs_dir / ".worker.lock"
    lock.write_text("busy", encoding="utf-8")
    assert store.run_once(limit=1) == []
    lock.unlink()
