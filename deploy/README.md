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
