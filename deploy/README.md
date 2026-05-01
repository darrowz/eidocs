# eidocs deployment

The production deployment keeps document processing out of the OpenClaw request
path.

```bash
/dev-project/eibrain/.venv/bin/python -m pip install -e /dev-project/eidocs
python3 -m venv /dev-project/eidocs/.venv-rag
/dev-project/eidocs/.venv-rag/bin/python -m pip install -U pip setuptools wheel
/dev-project/eidocs/.venv-rag/bin/python -m pip install -r /dev-project/eidocs/requirements-rag.txt
install -m 0755 /dev-project/eidocs/scripts/eidocs /home/darrow/.local/bin/eidocs
mkdir -p /home/darrow/.config/systemd/user
cp deploy/systemd/eidocs-*.service deploy/systemd/eidocs-*.timer /home/darrow/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now eidocs-worker.timer eidocs-prune.timer
```

If the user systemd bus is unavailable in a non-interactive SSH session, the
unit files are still installed and can be enabled from a logged-in session.

The RAG-Anything/MinerU environment is intentionally isolated in
`/dev-project/eidocs/.venv-rag`; the main CLI continues to run in
`/dev-project/eibrain/.venv`.

## OpenClaw plugin

Install or refresh the eidocs OpenClaw extension, then enable it in the gateway
configuration:

```bash
mkdir -p /home/darrow/.openclaw/extensions/eidocs-tools
cp integrations/openclaw/eidocs-tools/* /home/darrow/.openclaw/extensions/eidocs-tools/
python3 - <<'PY'
import json
from pathlib import Path
path = Path('/home/darrow/.openclaw/openclaw.json')
data = json.loads(path.read_text(encoding='utf-8'))
plugins = data.setdefault('plugins', {})
entries = plugins.setdefault('entries', {})
entries.setdefault('eidocs-tools', {})['enabled'] = True
allow = plugins.setdefault('allow', [])
if 'eidocs-tools' not in allow:
    allow.append('eidocs-tools')
path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
PY
systemctl --user restart openclaw-gateway.service
```

A healthy gateway log should include `eidocs-tools` in the loaded plugin list,
for example `http server listening (7 plugins: browser, eidocs-tools, ...)`.

The extension also provides the document cognition loop:

- `eidocs_recent` lists recent background OpenClaw/eidocs jobs.
- `before_prompt_build` prepends recent eidocs job state so the agent does not
  claim a PDF skipped eidocs just because the current reply used another parser.
- auto-ingest writes `/home/darrow/.openclaw/workspace/runtime/eidocs-status.md`
  as a human-readable runtime note.
