#!/usr/bin/env bash
# Launch the browser bridge after installing the TUI dependencies.
set -euo pipefail
cd "$(dirname "$0")"
python_bin="${FLEET_TUI_PYTHON:-.venv/bin/python}"
if [[ -z "${FLEET_TUI_PYTHON:-}" && ! -x "$python_bin" ]]; then
    python_bin=python3
fi
exec "$python_bin" -m fleet_tui.serve "$@"
