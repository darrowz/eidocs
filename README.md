# eidocs

`eidocs` is a document-understanding sidecar for the EI stack.

It has a real RAG-Anything parsing and LightRAG indexing path for complex
documents:

- PDF/Office files are routed to an isolated RAG-Anything/MinerU subprocess.
- RAG-Anything emits a standard `content_list`.
- `eidocs` converts that list into stable `text/table/image/equation` blocks.
- Parsed content is inserted into RAG-Anything/LightRAG when `EIDOCS_RAG_INDEX=1`.
- Embeddings use honjia Ollama by default: `mxbai-embed-large:latest`.
- LLM calls use the existing DashScope/Bailian OpenAI-compatible endpoint from
  `/home/darrow/api-keys.env`.
- `eimemory` receives only meaningful document-level events.

Runtime paths:

```text
/dev-project/eidocs
/opt/eidocs/current
/opt/eidocs/rag-venv
/var/lib/eidocs
/etc/eidocs
/var/log/eidocs
```

The heavy parser venv is isolated from `eibrain/.venv`, so OpenClaw and the
memory runtime do not inherit MinerU/RAG-Anything dependencies.

## CLI

```bash
eidocs check-raganything
eidocs ingest ./report.pdf
eidocs query "what does the revenue table say?" --mode raganything --doc-id DOC_ID
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

## RAG configuration

Defaults are set in `/opt/eidocs/current/.venv/bin/eidocs` and the worker service:

```text
EIDOCS_OLLAMA_HOST=http://honjia:11434
EIDOCS_OLLAMA_EMBED_MODEL=mxbai-embed-large:latest
EIDOCS_EMBEDDING_DIM=1024
EIDOCS_RAG_INDEX=1
EIDOCS_ROOT=/var/lib/eidocs
EIDOCS_CONFIG_DIR=/etc/eidocs
EIDOCS_ENV_FILE=/etc/eidocs/api-keys.env
EIDOCS_RAG_PYTHON=/opt/eidocs/rag-venv/bin/python
```

## eimemory policy

`eidocs` does not push every chunk into `eimemory`. It only writes a redacted
document-level event containing the document id, hash, content counts, summary,
and index reference. Full blocks stay in the `eidocs` index.

`eidocs` does not compute memory quality itself. Scoring is assigned after the
event lands in `eimemory`, and downstream consumers should expect the stored
record contract to expose:

- preferred scoring: `meta.scoring.memory_score_v1`
- legacy compatibility: `meta.quality`

Expected scoring-v1 tiers for downstream consumers:

- `rejected`
- `candidate`
- `confirmed`
- `core`

When `eidocs`-originated records are later exported back out through
`eimemory`, consumers should prefer the scoring-v1 tier and still honor legacy
`meta.quality.capture_decision="reject"` for older data.
