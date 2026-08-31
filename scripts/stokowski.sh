#!/usr/bin/env bash
#
# Launcher so nobody has to remember where the virtualenv lives.
#
# Run from the directory holding your workflow.yaml — the operator directory,
# which is usually not this repo. Everything is resolved relative to the
# package.json that invoked this, so `pnpm start` works from wherever that is.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# ── Find the CLI ─────────────────────────────────────────────────────────────
# An activated venv wins, then a local .venv, then whatever is on PATH. The
# local .venv is checked before PATH deliberately: a stale global install
# silently running old code against new config is a genuinely confusing failure.
find_cli() {
  if [ -n "${VIRTUAL_ENV:-}" ] && [ -x "$VIRTUAL_ENV/bin/stokowski" ]; then
    printf '%s\n' "$VIRTUAL_ENV/bin/stokowski"; return 0
  fi
  if [ -x "$ROOT/.venv/bin/stokowski" ]; then
    printf '%s\n' "$ROOT/.venv/bin/stokowski"; return 0
  fi
  if command -v stokowski >/dev/null 2>&1; then
    command -v stokowski; return 0
  fi
  return 1
}

if ! CLI="$(find_cli)"; then
  cat >&2 <<'EOF'
Stokowski is not installed in this directory.

  python3 -m venv .venv
  ./.venv/bin/pip install -e ".[web]"

If Stokowski itself lives elsewhere, point the install at it:

  ./.venv/bin/pip install -e "/path/to/stokowski[web]"
EOF
  exit 1
fi

# ── Find the workflow ────────────────────────────────────────────────────────
# The CLI auto-detects too, but failing here gives a better message than a
# stack trace, and the port lookup below needs the path anyway.
WORKFLOW="${STOKOWSKI_WORKFLOW:-}"
if [ -z "$WORKFLOW" ]; then
  # workflow.example.yaml is last so an operator's real config always wins;
  # it means `pnpm check` still works in a fresh clone of this repo.
  for candidate in workflow.yaml workflow.yml WORKFLOW.md workflow.example.yaml; do
    if [ -f "$candidate" ]; then WORKFLOW="$candidate"; break; fi
  done
fi
if [ -z "$WORKFLOW" ]; then
  echo "No workflow.yaml here ($ROOT). Copy workflow.example.yaml to start." >&2
  exit 1
fi

# ── Dashboard port ───────────────────────────────────────────────────────────
# Read server.port out of the config so the browser opens on the right one.
# Only used to decide what URL to open; the server takes its port from config.
dashboard_port() {
  if [ -n "${STOKOWSKI_PORT:-}" ]; then printf '%s\n' "$STOKOWSKI_PORT"; return; fi
  awk '
    /^[^[:space:]#]/ { in_server = ($0 ~ /^server:/) }
    in_server && /^[[:space:]]+port:[[:space:]]*[0-9]+/ {
      gsub(/[^0-9]/, "", $0); print; exit
    }
  ' "$WORKFLOW"
}

open_browser() {
  local url="$1"
  if command -v open >/dev/null 2>&1; then open "$url"
  elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$url"
  fi
}

CMD="${1:-start}"
shift || true

case "$CMD" in
  start|studio)
    PORT="$(dashboard_port)"
    if [ -n "$PORT" ]; then
      # The dashboard and the studio are one process; `studio` differs only in
      # which page it opens. Give the server a moment to bind before opening.
      SUFFIX=""; [ "$CMD" = "studio" ] && SUFFIX="/studio"
      ( sleep 2; open_browser "http://127.0.0.1:${PORT}${SUFFIX}" ) &
    elif [ "$CMD" = "studio" ]; then
      echo "No server.port set in $WORKFLOW — add one to use the studio." >&2
      exit 1
    fi
    exec "$CLI" "$WORKFLOW" "$@"
    ;;
  check)   exec "$CLI" "$WORKFLOW" --dry-run "$@" ;;
  stats)   exec "$CLI" "$WORKFLOW" --stats "$@" ;;
  *)       exec "$CLI" "$WORKFLOW" "$CMD" "$@" ;;
esac
