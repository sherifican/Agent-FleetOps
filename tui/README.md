# fleet_tui — off-box backup (branch: `fleet_tui`)

> Source: `~/fleet_tui/` on the Fleet box. Backed up here by `backup_push.sh` (`HEAD:fleet_tui`).
> Part of the [a private backup repository](../../tree/main) repo — see `main` for the fleet overview.

## What this is
The **Fleet fleet TUI** — a [Textual](https://textual.textualize.io/) **monitor + inbox + quick-control** window over the *existing* fleet stack. It **reads** state the fleet already produces (state files, `/api/ps`, `systemctl`) and exposes a couple of thin, owner-initiated control hooks. It is deliberately **not** a second orchestrator, not a code editor, and not an intervention gateway. Current `VERSION = 3.43` (shown in the header next to the clock — the fresh-code signal). **Authoritative source is `fleet_tui/app.py`, not this line** — a version pinned in prose drifts the moment it ships; check the constant if it matters.

## Architecture — a strict one-way pipeline
So a local model can own the data/format layer in bounded, test-gated tasks without fighting the event loop:
| Layer | Rule |
|---|---|
| **`fleet_tui/sources/*.py`** (22 modules) | Pure **headless** readers — read files / shell-outs → records. **Zero `textual` import**, unit-testable against `tests/fixtures/`. Each reader wrapped in try/except returning a **safe default** (never raises). |
| **`fleet_tui/widgets/format.py`** | Dumb pure **formatters** — records → Rich-markup strings. No I/O, no state. |
| **`fleet_tui/app.py`** | The Textual **App** — layout, timers, key bindings, modals, the paint loop; an embedded `pyte` terminal (Ctrl+`). |
| **`fleet_tui/models.py`** | **Frozen** dataclass contracts (change a field → update every source + widget). |

Also: `fleet_tui/fleet_cli/` (control-plane runners), `tests/` + `tests/fixtures/` (**346 tests** as of 2026-08-08, hermetic), `specs/`, `AGENTS.md` (repo DOX root), `CHANGELOG.md`, `TUI_OPERATOR_NOTES.md`, `FLEET_CONTROL_BUILD_PLAN.md`, `pyproject.toml`, `run.sh` (launches the TUI directly — tmux retired), `restart.sh` (stop/reap helper).

## HARD contracts (never violated)
- **Never crash on a missing/malformed state file** — a source returns a safe default; the panel degrades to a muted cell. Degrade panel-by-panel, never die.
- **Sources are pure + headless** (no `textual`, unit-testable).
- **No model calls / no auto-actions in the monitor loop** — it's deterministic status reads only, never an LLM. Controls = the focus semaphore + thin runners over EXISTING gated scripts. It is not a 2nd orchestrator.
- **Light + cached** — refresh ≥ 1s; every subprocess reader cached (`fleet-doctor` ≥ 30s, `/api/ps` 5s, network ~20s).

## Build loop + testing
Features are built cheaply via the local lane: Claude writes a tight spec + the pytest gate (Claude-authored, un-gameable), a local coder writes the pure `sources/`/`format.py` (via `aider-edit`), the deterministic pytest is the real gate, Claude does the Textual wiring. See the **`fleet-tui-dev`** skill for the full recipe + Textual gotchas. Run tests: `cd ~/fleet_tui && .venv/bin/python -m pytest -q`. Launch: `./run.sh`.

## Backup & restore
Mirrored by `~/.claude/curation/backup_push.sh` (local `HEAD` → this `fleet_tui` branch). **Restore:** clone this branch back to `~/fleet_tui/`.
