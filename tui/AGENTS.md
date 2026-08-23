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
- `tests/fixtures/`       — real captured state files (deterministic test inputs)
- `tests/test_*.py`       — one per source, headless (`pytest`)
- `TUI_OPERATOR_NOTES.md` — how to operate + troubleshoot; each builder writes its module's section

## State contracts (frozen 2026-07-02 — build against these, do NOT re-guess paths)
See BUILD_PLAN §2. Summary:
- **jobs**: `~/.hermes/cron/jobs.json` + `crontab -l` + `~/.hermes/cron/output/<name>/` + logs
- **inbox**: `~/.claude/curation/.dep_update_trigger` · `.trigger` · `.github_action_alert` ·
  `~/fleet_optests/HF_WATCH_DIGEST.md` · `~/.claude/curation/CURATION_REJECTS_REVIEW.md`
- **health**: `fleet-doctor --json` + `GET http://localhost:11434/api/ps` + `systemctl --user is-active …`
- **focus**: `~/.claude/curation/watchers.lock` — presence = ON. **Scope = `noisy`**
  (github-watch + harvester + curation-watcher). **Default = OFF** (no file).

## Verify-before-finish
Each source ships with its `tests/test_*.py` GREEN (`.venv/bin/pytest tests/test_<x>.py`) before finish.
Never finish on a red or un-run test. On a real block after ≤2 targeted fixes, STOP and report the
blocker in one line (do not thrash) — Claude fixes that module only.

## v4.0 box schema

`~/.fleet_tui/boxes.json` is optional. It accepts either a top-level list or `{ "boxes": [...] }`; each box requires `name` and `kind` (`local` or `remote`). Relay paths are local file paths and are read-only: `receipts_path`, `models_path`, `health_path`, `ledger_path`, `downloads_path`, and `throughput_path`. `device_labels` maps a relay device key to `{ "badge", "color", "power_cap_w" }`. Missing or malformed configuration returns one usable `local` box. Use `docs/boxes.example.json` as a neutral two-box dGPU/iGPU/eGPU example.
