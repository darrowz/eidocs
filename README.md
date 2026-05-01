# eidocs

`eidocs` is a document-understanding sidecar for the EI stack.

It now has a real RAG-Anything parsing path for complex documents:

- PDF/Office files are routed to an isolated RAG-Anything/MinerU subprocess.
- RAG-Anything emits a standard `content_list`.
- `eidocs` converts that list into stable `text/table/image/equation` blocks.
- The local index answers lightweight cited queries.
- `eimemory` receives only meaningful document-level events.

Runtime paths:

```text
/dev-project/eidocs
/dev-project/eidocs/.venv-rag
/home/darrow/.local/share/eidocs
/home/darrow/.local/bin/eidocs
```

The heavy parser venv is isolated from `eibrain/.venv`, so OpenClaw and the
memory runtime do not inherit MinerU/RAG-Anything dependencies.

## CLI

```bash
eidocs check-raganything
eidocs ingest ./report.pdf
eidocs query "what does the revenue table say?"
eidocs export-content-list DOC_ID
eidocs sync-eimemory DOC_ID --dry-run

eidocs job submit ./report.pdf --source openclaw
eidocs job status JOB_ID
eidocs job run-once --limit 1
```

## Processing policy

OpenClaw tools only create jobs, check status, and query completed indexes.
They do not parse PDFs synchronously.

Default safety gates:

- max file size: `25MB`
- max estimated PDF pages: `120`
- reject symlinks, encrypted PDFs, macro Office formats, archives, executables
- reject MIME/extension mismatch

## eimemory policy

`eidocs` does not push every chunk into `eimemory`. It only writes a redacted
document-level event containing the document id, hash, content counts, summary,
and index reference. Full blocks stay in the `eidocs` index.
