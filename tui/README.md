# fleet_tui — configurable fleet monitor

## What this is
The [Textual](https://textual.textualize.io/) **monitor + inbox + quick-control** window reads existing state files and exposes thin owner-initiated controls. It is not an orchestrator, editor, or intervention gateway. The authoritative version is `VERSION = 4.0` in `fleet_tui/app.py`.

## Architecture — a strict one-way pipeline
So a local model can own the data/format layer in bounded, test-gated tasks without fighting the event loop:
| Layer | Rule |
|---|---|
| **`fleet_tui/sources/*.py`** (22 modules) | Pure **headless** readers — read files / shell-outs → records. **Zero `textual` import**, unit-testable against `tests/fixtures/`. Each reader wrapped in try/except returning a **safe default** (never raises). |
| **`fleet_tui/widgets/format.py`** | Dumb pure **formatters** — records → Rich-markup strings. No I/O, no state. |
| **`fleet_tui/app.py`** | The Textual **App** — layout, timers, key bindings, modals, the paint loop; an embedded `pyte` terminal (Ctrl+`). |
| **`fleet_tui/models.py`** | **Frozen** dataclass contracts (change a field → update every source + widget). |

Also: `fleet_tui/fleet_cli/` (control-plane runners), `tests/` + `tests/fixtures/` (hermetic), `specs/`, `AGENTS.md` (repo DOX root), `CHANGELOG.md`, `pyproject.toml`, `run.sh`, and `restart.sh`.

## Boxes configuration

Optional `~/.fleet_tui/boxes.json` is a list (or `{ "boxes": [...] }`) of box objects. Each has `name`, `kind` (`local` or `remote`), optional relay paths (`receipts_path`, `models_path`, `health_path`, `ledger_path`, `downloads_path`, `throughput_path`), and `device_labels`. A label maps a device key to `{ "badge", "color", "power_cap_w" }`. With no file, the TUI uses one box named `local` and its existing local readers. See [`docs/boxes.example.json`](docs/boxes.example.json).

## HARD contracts (never violated)
- **Never crash on a missing/malformed state file** — a source returns a safe default; the panel degrades to a muted cell. Degrade panel-by-panel, never die.
- **Sources are pure + headless** (no `textual`, unit-testable).
- **No model calls / no auto-actions in the monitor loop** — it's deterministic status reads only, never an LLM. Controls = the focus semaphore + thin runners over EXISTING gated scripts. It is not a 2nd orchestrator.
- **Light + cached** — refresh ≥ 1s; every subprocess reader cached (`fleet-doctor` ≥ 30s, `/api/ps` 5s, network ~20s).

## Build loop + testing
Features are built cheaply via the local lane: Claude writes a tight spec + the pytest gate (Claude-authored, un-gameable), a local coder writes the pure `sources/`/`format.py` (via `aider-edit`), the deterministic pytest is the real gate, Claude does the Textual wiring. See the **`fleet-tui-dev`** skill for the full recipe + Textual gotchas. Run tests: `cd ~/fleet_tui && .venv/bin/python -m pytest -q`. Launch: `./run.sh`.

## Backup & restore
Mirrored by `~/.claude/curation/backup_push.sh` (local `HEAD` → this `fleet_tui` branch). **Restore:** clone this branch back to `~/fleet_tui/`.
