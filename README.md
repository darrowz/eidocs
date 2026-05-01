# eidocs

`eidocs` is a lightweight document-understanding sidecar for the EI stack.

It turns documents into stable multimodal content blocks, stores a local
index, answers lightweight queries, and writes only meaningful document-level
events to `eimemory`.

The first deployment intentionally keeps RAG-Anything optional:

- default path: no OCR/VLM/MinerU/Docling dependency
- fallback parser: `.txt`, `.md`, `.csv`, `.json`, and image metadata blocks
- RAG-Anything adapter: dynamically imported only when installed
- OpenClaw contract: submit jobs, check status, query existing indexes

Runtime paths:

```text
/dev-project/eidocs
/home/darrow/.local/share/eidocs
/home/darrow/.local/bin/eidocs
```

## CLI

```bash
eidocs ingest ./notes.md
eidocs query "what tables mention revenue?"
eidocs export-content-list DOC_ID
eidocs sync-eimemory DOC_ID --dry-run

eidocs job submit ./report.md --source openclaw
eidocs job status JOB_ID
eidocs job run-once --limit 1
```

## eimemory policy

`eidocs` does not push every chunk into `eimemory`. It only writes a redacted
document-level event containing the document id, hash, content counts, summary,
and index reference. Full blocks stay in the `eidocs` index.
