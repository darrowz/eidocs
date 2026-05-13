# eidocs deployment

`eidocs` is deployed as a production sidecar, not as an editable checkout inside
the OpenClaw request path. The main CLI runs from `/opt/eidocs/current/.venv`.
The heavy RAG-Anything/MinerU environment stays isolated in `/opt/eidocs/rag-venv`.

## Canonical paths

| Purpose | Path |
| --- | --- |
| Source repository | `/dev-project/eidocs` |
| Immutable releases | `/opt/eidocs/releases/<commit>` |
| Active release | `/opt/eidocs/current` |
| Main virtual environment | `/opt/eidocs/current/.venv` |
| RAG parser virtual environment | `/opt/eidocs/rag-venv` |
| Runtime data and indexes | `/var/lib/eidocs` |
| Runtime configuration | `/etc/eidocs` |
| Logs | `/var/log/eidocs` |

## Release

```bash
/dev-project/eidocs/deploy/install_immutable_release.sh

# Optional heavy parser install or refresh:
INSTALL_RAG=1 /dev-project/eidocs/deploy/install_immutable_release.sh
```

The script creates a release directory, installs the package into a release-local
venv, prepares runtime directories, and atomically updates `/opt/eidocs/current`.

## Services

```bash
mkdir -p /home/darrow/.config/systemd/user
cp /dev-project/eidocs/deploy/systemd/eidocs-*.service /home/darrow/.config/systemd/user/
cp /dev-project/eidocs/deploy/systemd/eidocs-*.timer /home/darrow/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now eidocs-worker.timer eidocs-prune.timer
```

The worker service uses these production defaults:

```text
EIDOCS_ROOT=/var/lib/eidocs
EIDOCS_CONFIG_DIR=/etc/eidocs
EIDOCS_ENV_FILE=/etc/eidocs/api-keys.env
EIDOCS_RAG_PYTHON=/opt/eidocs/rag-venv/bin/python
```

## OpenClaw plugin

Install or refresh the eidocs OpenClaw extension, then enable it in the gateway
configuration:

```bash
mkdir -p /home/darrow/.openclaw/extensions/eidocs-tools
cp /opt/eidocs/current/integrations/openclaw/eidocs-tools/* /home/darrow/.openclaw/extensions/eidocs-tools/
systemctl --user restart openclaw-gateway.service
```

Set `EIDOCS_CLI=/opt/eidocs/current/.venv/bin/eidocs` in the OpenClaw gateway
environment when overriding the extension default is needed.

## Verification

```bash
/opt/eidocs/current/.venv/bin/eidocs check-raganything
systemctl --user start eidocs-worker.service
journalctl --user -u eidocs-worker.service -n 100 --no-pager
```

`eidocs` writes document-level events to `eimemory`; full blocks remain in the
eidocs index under `/var/lib/eidocs`.
