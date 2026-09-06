# fleet_tui — configurable fleet monitor

## What this is
The [Textual](https://textual.textualize.io/) **monitor + inbox + quick-control** window reads existing state files and exposes thin owner-initiated controls. It is not an orchestrator, editor, or intervention gateway. The authoritative version is `VERSION = 4.0` in `fleet_tui/app.py`.

## Architecture — a strict one-way pipeline
So a local model can own the data/format layer in bounded, test-gated tasks without fighting the event loop:
| Layer | Rule |
|---|---|
| **`fleet_tui/sources/*.py`** (27 modules, excluding `__init__.py`) | Pure **headless** readers — read files / shell-outs → records. **Zero `textual` import**, unit-testable against `tests/fixtures/`. Each reader wrapped in try/except returning a **safe default** (never raises). |
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
Features are built cheaply via the local lane: Claude writes a tight spec + the pytest gate (Claude-authored, un-gameable), a local coder writes the pure `sources/`/`format.py` (via `aider-edit`), the deterministic pytest is the real gate, Claude does the Textual wiring. See the shipped [local-lane-build-loop skill](../skills/local-lane-build-loop/SKILL.md) for the build recipe; provision its optional local tools separately. Run tests: `cd tui && .venv/bin/python -m pytest -q`. Launch: `./run.sh`.

## Backup & restore
Mirrored by the authors' backup job. **Restore:** clone this branch to your chosen checkout directory, then run the TUI from its `tui/` subdirectory.


## Point the TUI at your fleet

External inputs are **not configured** until you choose their paths. Each key resolves
from `FLEET_TUI_<KEY>` (uppercase), then `paths.json` in
`${XDG_CONFIG_HOME:-~/.config}/fleet_tui/`; an absent, blank or invalid value resolves
to `None`. Environment settings override JSON, including a blank setting that disables
an input. Malformed JSON fails closed. Restart the TUI after changing these settings.

Copy `tui/paths.example.json` to that config location and **EDIT** its values first:
the example is the authors' instance, not a portable default. For the authors only,
the one-time migration from the repository root is:

```bash
mkdir -p ~/.config/fleet_tui
cp tui/paths.example.json ~/.config/fleet_tui/paths.json
```

Adopters using `XDG_CONFIG_HOME` should copy into its `fleet_tui` subdirectory instead.
Alternatively set individual keys, for example
`FLEET_TUI_CURATION_DIR=./my-fleet/curation`. Relative paths use the launch directory;
`~` expands to the current user's home. Legacy `FLEET_RESEARCH_DIR`,
`FLEET_HEALTH_FILE`, `FLEET_WATCHERS_LOCK` and `PASSBACK_PC_GLOB` settings must migrate
to the corresponding keys below.

| Key | Input |
| --- | --- |
| `curation_dir` | Curation ledger, triggers, alerts, focus lock, action queue, feedback ledger and notification script |
| `hermes_cron_dir` | `jobs.json` and the `output/` subdirectory |
| `hermes_state_db` | Read-only failure history database |
| `research_dir` | Research briefs and `viz_assets/reports/fleet_pairings_scorecard.html` |
| `comms_inbound_glob` | Peer passback file glob |
| `passback_docs_glob` | Document passback file glob |
| `gpu_forensics_log` | GPU stability log |
| `hive_alert` | Hive drift alert file |
| `reliability_file` | Reliability summary file |
| `external_ssd_temp` | External SSD temperature cache |
| `disk_path` | Filesystem to measure for free space |
| `screenshots_dir` | Screenshot output directory |

Unconfigured inputs return their source's safe default. Affected panels show muted
`not configured: <key>` cells; independently configured inputs keep rendering.
Controls requiring an absent path refuse with that message. The application's own
state under `~/.fleet_tui/` and `~/.config/fleet_tui` is unchanged; OS interfaces such
as `/proc` and `/sys` still supply native system metrics.
