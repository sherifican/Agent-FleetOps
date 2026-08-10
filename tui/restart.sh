#!/usr/bin/env bash
# restart.sh — the TUI now runs DIRECTLY (no tmux; see run.sh), so there is no "in-place" restart:
# each launch loads current code fresh. This helper just STOPS any running/orphaned `-m fleet_tui`
# instance(s) so none pile up — then you relaunch via ./run.sh or the desktop icon.
set -uo pipefail
cd "$(dirname "$0")"
PY="$(pwd)/.venv/bin/python"
reaped=0
# Precise match (hard-learned): exactly our venv python running `-m fleet_tui` — never a substring grep
# (that used to also match the launcher's argv and tear down more than intended).
for pid in $(ps -eo pid,args --no-headers | awk -v py="$PY" '$2==py && $3=="-m" && $4=="fleet_tui"{print $1}'); do
  kill "$pid" 2>/dev/null && { echo "  stopped TUI instance $pid"; reaped=$((reaped+1)); }
done
[ "$reaped" -eq 0 ] && echo "no running TUI instance found."
echo "→ relaunch with ./run.sh or the desktop icon (loads current code fresh; press Ctrl+\` for the terminal)."
