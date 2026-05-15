#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="${REPO_DIR:-/dev-project/eidocs}"
INSTALL_ROOT="${INSTALL_ROOT:-/opt/eidocs}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
COMMIT="${1:-$(git -C "$REPO_DIR" rev-parse --short HEAD)}"
RELEASE_DIR="$INSTALL_ROOT/releases/$COMMIT"
CURRENT_LINK="$INSTALL_ROOT/current"
RAG_VENV="${RAG_VENV:-$INSTALL_ROOT/rag-venv}"

if ! git -C "$REPO_DIR" rev-parse --verify "$COMMIT^{commit}" >/dev/null 2>&1; then
  echo "Unknown commit: $COMMIT" >&2
  exit 2
fi

mkdir -p "$INSTALL_ROOT/releases" "$INSTALL_ROOT/run" "$INSTALL_ROOT/logs" /var/lib/eidocs /var/log/eidocs /etc/eidocs

if [ ! -d "$RELEASE_DIR" ]; then
  mkdir -p "$RELEASE_DIR"
  git -C "$REPO_DIR" archive "$COMMIT" | tar -C "$RELEASE_DIR" -xf -
fi

if [ ! -x "$RELEASE_DIR/.venv/bin/python" ]; then
  "$PYTHON_BIN" -m venv "$RELEASE_DIR/.venv"
fi

"$RELEASE_DIR/.venv/bin/python" -m pip install --upgrade pip
"$RELEASE_DIR/.venv/bin/python" -m pip install "$RELEASE_DIR"

if [ "${INSTALL_RAG:-0}" = "1" ]; then
  if [ ! -x "$RAG_VENV/bin/python" ]; then
    "$PYTHON_BIN" -m venv "$RAG_VENV"
  fi
  "$RAG_VENV/bin/python" -m pip install --upgrade pip setuptools wheel
  "$RAG_VENV/bin/python" -m pip install -r "$RELEASE_DIR/requirements-rag.txt"
fi

ln -sfn "$RELEASE_DIR" "$CURRENT_LINK.next"
mv -Tf "$CURRENT_LINK.next" "$CURRENT_LINK"

echo "release=$RELEASE_DIR"
echo "current=$CURRENT_LINK"
echo "commit=$COMMIT"
echo "rag_venv=$RAG_VENV"
