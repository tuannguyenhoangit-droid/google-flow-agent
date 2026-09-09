#!/usr/bin/env bash
# Start the standalone warm OmniVoice inference API.
set -euo pipefail

FLOWKIT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${OMNIVOICE_PYTHON:-${TTS_PYTHON_BIN:-$HOME/.venvs/flowkit/bin/python}}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "OmniVoice Python not found at $PYTHON_BIN" >&2
  echo "Install FlowKit's Python 3.10+ environment or set OMNIVOICE_PYTHON." >&2
  exit 1
fi

if ! "$PYTHON_BIN" -c 'import multipart' >/dev/null 2>&1; then
  echo "python-multipart is missing from $PYTHON_BIN" >&2
  echo "Install it with: $PYTHON_BIN -m pip install python-multipart" >&2
  exit 1
fi

cd "$FLOWKIT_DIR"
exec "$PYTHON_BIN" -m uvicorn agent.omnivoice_api:app \
  --host "${OMNIVOICE_API_HOST:-127.0.0.1}" \
  --port "${OMNIVOICE_API_PORT:-8200}"
