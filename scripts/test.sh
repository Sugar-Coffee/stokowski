#!/usr/bin/env bash
# Run the test suite with whichever interpreter is available.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [ -n "${VIRTUAL_ENV:-}" ]; then PY="$VIRTUAL_ENV/bin/python"
elif [ -x "$ROOT/.venv/bin/python" ]; then PY="$ROOT/.venv/bin/python"
else PY="$(command -v python3)"; fi

if ! "$PY" -c "import pytest" 2>/dev/null; then
  echo "pytest is not installed: $PY -m pip install pytest" >&2
  exit 1
fi
exec "$PY" -m pytest tests/ "$@"
