# fleet_tui — Fleet-control TUI (AGENTS.md / DOX root)

## Purpose
A lightweight **Textual** MONITOR + INBOX + QUICK-CONTROL window over the EXISTING Fleet fleet stack.
It READS state files the fleet already produces + one control semaphore (`watchers.lock`). It is NOT
an orchestrator, NOT a code editor, NOT an intervention gateway.
Full plan: kept in the origin fleet's private notes; this file is self-contained.

## HARD contracts (never violate)
1. **Never crash on a missing/malformed state file.** A source returns `[]`/`unavailable`; the widget
   shows a muted "n/a" cell. Degrade panel-by-panel, never die. (This is the #1 trust requirement.)
2. **Sources are PURE + headless.** `sources/*.py` read files / shell-outs and return the dataclass
   records in `models.py`. **NO `textual` import in `sources/`.** They must be unit-testable with no
   GPU, no Textual, no live fleet — only the fixture files in `tests/fixtures/`.
3. **No model calls, no routing, no auto-actions on gated items.** The monitoring loop is all
   deterministic status reads (files, `/api/ps`, `systemctl`) — never an LLM. Controls = the focus
   semaphore (MVP) + (v2) one-key approve that runs the EXISTING gated path.
4. **Light:** `textual` only; `max_lines` cap on scroll logs; async `@work` for every shell-out;
   ANSI-strip all log text; refresh timer ≥ 3 s, health shell-out cached ≥ 5 s (`fleet-doctor` ≥ 30 s).
   Target < 50 MB RSS. Read cached state; never hammer `:11434` in a tight loop.
5. `models.py` is a **FROZEN interface** — do not change a field without updating every source AND
   widget that uses it (both sides compile against it).

## Layout
- `fleet_tui/models.py`   — dataclass CONTRACTS (frozen)
- `fleet_tui/sources/`    — pure readers including `boxes.py`, `receipts.py`, `throughput.py`, `lanes.py`, `downloads.py`, and `bg_agents.py`
- `fleet_tui/widgets/`    — Textual renderers (dumb; render records only)
- `fleet_tui/app.py`      — the App: layout, refresh timer, key bindings, focus-toggle write
- `fleet_tui/fleet_cli/`  — the `fleet` control-plane CLI (reuses `sources/*`; run via `~/.local/bin/fleet`).
  Verbs: status/targets/tail/route/feedback/preflight/summarize/digest. Spec: `../FLEET_CONTROL_BUILD_PLAN.md`.
  Same HARD contract: never crash on bad state; every verb degrades to stderr + exit(2).
- `tests/fixtures/`       — deterministic state inputs; the crontab and job roster are synthetic
- `tests/test_*.py`       — one per source, headless (`pytest`)
- `TUI_OPERATOR_NOTES.md` — how to operate + troubleshoot; each builder writes its module's section

## State contracts (frozen 2026-07-02 — build against these, do NOT re-guess paths)
See BUILD_PLAN §2. Summary:
- **jobs**: `~/.hermes/cron/jobs.json` + `crontab -l` + `~/.hermes/cron/output/<name>/` + logs
- **inbox**: `curation_dir/.dep_update_trigger` · `.trigger` · `.github_action_alert` ·
  `curation_dir/HF_WATCH_DIGEST.md` · `curation_dir/CURATION_REJECTS_REVIEW.md`
- **health**: `fleet-doctor --json` + `GET http://localhost:11434/api/ps` + `systemctl --user is-active …`
- **focus**: `curation_dir/watchers.lock` — presence = ON. **Scope = `noisy`**
  (github-watch + harvester + curation-watcher). **Default = OFF** (no file).

## Verify-before-finish
Each source ships with its `tests/test_*.py` GREEN (`.venv/bin/pytest tests/test_<x>.py`) before finish.
Never finish on a red or un-run test. On a real block after ≤2 targeted fixes, STOP and report the
blocker in one line (do not thrash) — the orchestrator fixes that module only.

## v4.0 box schema

`~/.fleet_tui/boxes.json` is optional. It accepts either a top-level list or `{ "boxes": [...] }`; each box requires `name` and `kind` (`local` or `remote`). Relay paths are local file paths and are read-only: `receipts_path`, `models_path`, `health_path`, `ledger_path`, `downloads_path`, and `throughput_path`. `device_labels` maps a relay device key to `{ "badge", "color", "power_cap_w" }`. Missing or malformed configuration returns one usable `local` box. Use `docs/boxes.example.json` as a neutral two-box dGPU/iGPU/eGPU example.

## Peer passback labels

Passback prose calls its sender the peer orchestrator. `PEER_BOX_LABEL = "peer orchestrator"` is the single source for displayed box labels and the modal title; passback paths, readers, and seen-state handling are unchanged.


## External path configuration

`fleet_tui/paths.py` owns external layout keys: environment then XDG-aware JSON,
otherwise `None`. Never introduce an author-layout fallback. The earlier State
contracts list describes only the authors' instance, now in `paths.example.json`;
copy and edit that example explicitly. Preserve each reader's safe default and guard
absent paths before I/O; controls refuse absent configuration. `panel_notices()` is
sampled during data gathering, not the fast paint loop. Source constants resolve at
startup, so configuration changes require restart. Headless verification includes
`tests/test_paths.py`; shared fixtures must not import Textual when it is absent.


## Review regression boundaries

Passback composition emits one notice only when passback keys are missing and no
empty Static when configured. `tests/test_passback_compose.py` executes that body
with headless widget stand-ins; Textual layout still requires UI verification.
Configured-reader controls use both environment and XDG JSON discovery, reload the
source, and call its no-argument reader. Playlist config/state/request paths remain
TUI-owned; only its optional notification uses external `curation_dir`.

Headless collection currently reaches 345 tests; the 383-test declared inventory
includes UI modules requiring Textual/textual-serve. Do not report the full suite
as executed when those modules cannot import.


## Gathered notice contract

`tests/test_app.py::test_gather_data` pins `path_notices` in the gathered key set,
its dictionary shape, the five panel IDs with external path dependencies, and
string notice values. Keep this contract aligned when adding or removing such
panel dependencies; the notice mapping remains present when its strings are empty.


## Browser serving contract

`./serve.sh` launches `fleet_tui.serve` from this directory using `FLEET_TUI_PYTHON`,
the local venv when present, or `python3`. Dependencies must be installed first.
The server has no authentication and defaults to all interfaces; deployment needs
a deliberate bind policy and host firewall. Never describe host restrictions as
provided or checked by this package.


## Roster configuration

The bridge `host_label` in `codex_link.json` defaults to `peer`; probing and the
`enabled` switch are unchanged. `FLEET_TUI_SERVICES` is a JSON list of service
unit names (`[]` disables probes); malformed values retain the labelled authors'
instance defaults. `FLEET_TUI_CLOUD_MARKERS` is a comma-separated classification
roster; an empty value disables those matches. Cloud process names and display
aliases are labelled authors' instance constants. Unknown profiles keep their
observed name, and absent model/profile data displays only the provider label.
Restart after changing the cloud roster. Headless gates cover neutral bridge
labels, configured services, classification overrides and unknown profiles.


## Orchestrator copy

Runtime handoff, pending-action and gate descriptions use the orchestrator role.
Provider names in model IDs, CLI detection and worker labels remain product
identifiers; never rewrite them as roles. Skills retain their adopting-team voice.


## Technical provenance

Comments and specs state detection and verification contracts without private
report filenames or dated approval stories. Preserve timestamps that are parser
fixtures or release versions; those are data, not operating instructions.


## Specification voice

`guard/voice_check.py` covers both nested specification directories. Specs use
impersonal prose or the adopting team; the skills remain outside that scope.
The scope tests plant plural prose in each nested directory before checking its
neutral counterpart, so an unread directory cannot produce an unearned pass.


## Job fixtures

`tests/fixtures/crontab.txt` and `jobs.json` contain obviously synthetic names,
IDs and paths. Preserve cron command forms and JSON field/type coverage when
changing them; never replace them with live roster dumps.
