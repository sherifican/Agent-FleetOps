#!/usr/bin/env bash
# run.sh — launch the Fleet Fleet TUI.
#
# The terminal is now EMBEDDED in the app:  press  Ctrl+`  to toggle it (hidden by default),
# Ctrl+Q  to quit.  So there is NO tmux shell pane anymore, and NO persistent tmux session that
# could pin STALE code across "close and reopen".  Each launch runs the CURRENT code fresh, and
# Textual gets mouse events directly (clicks work — the old tmux layer was eating them).
#
# The old tmux + bottom-shell design is retired; the embedded terminal replaces it.
set -uo pipefail
cd "$(dirname "$0")"
PY="$(pwd)/.venv/bin/python"
[ -x "$PY" ] || { echo "run.sh: venv python missing at $PY" >&2; exit 1; }
exec "$PY" -m fleet_tui
