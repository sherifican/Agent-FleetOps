# SPEC — extend fleet_tui/sources/cloud_legs.py: show running CLAUDE worker legs + their model

Edit ~/fleet_tui/fleet_tui/sources/cloud_legs.py IN PLACE, preserving every existing function
and behavior EXACTLY, and ADDING Claude-worker-leg support. Pure/headless (no textual), never raises.
Must pass tests/test_cloud_legs_claude.py AND the existing tests/test_cloud_legs.py unchanged.

## Change 1 — add "claude" to the cloud markers
The dispatch legs `claude-opus` / `claude-sonnet` (subscription Claude worker legs) must count as cloud legs.
Change the CLOUD_MARKERS tuple to include "claude":
    CLOUD_MARKERS = ("codex", "grok", "kimi", "claude")
(This makes is_cloud_leg("claude-opus")/("claude-sonnet") return True, so a running claude-* DISPATCH shows
up via the existing active_cloud_legs() path with its leg name — which already names the model.)

## Change 2 — a model-name prettifier
Add a pure helper:
    def _pretty_claude_model(model_id: str) -> str:
Map a Claude model id to a short display name; return the input unchanged if unknown; "" -> "".
Required mappings (case-insensitive contains is fine, but exact ids must map):
    "claude-opus-4-8"   -> "Opus 4.8"
    "claude-sonnet-5"   -> "Sonnet 5"
    "claude-haiku-4-5"  -> "Haiku 4.5"
Suggested impl: a small dict of exact ids; else if it contains "opus"/"sonnet"/"haiku", title-case that word
+ any trailing version digits; else return the id as-is. Keep it simple and never raise.

## Change 3 — detect EXTERNAL claude -p worker processes (with their model)
A claude worker leg runs as `claude -p ... --model <id> ...` (print/headless mode). The interactive
ORCHESTRATOR runs as plain `claude` (NO -p) and MUST NOT be shown. Add two functions:

    def _claude_cmdlines() -> list:
        """Best-effort list of full command-line strings for every running `claude` process. Read via
        `pgrep -x claude` then /proc/<pid>/cmdline (NUL-separated -> spaces). Cached ~15s in a module cache
        (a subprocess — NEVER per refresh tick, same pattern as external_cloud_procs). Returns [] on any
        error. This is the ONLY function that does process I/O here (so tests monkeypatch it)."""

    def external_claude_workers() -> list:
        """From _claude_cmdlines(), return the running Claude WORKER legs (print-mode only) as
        [{name, activity, started}]. A cmdline is a worker iff it contains a '-p' or '--print' token
        (the orchestrator has neither -> excluded). Extract the model from a '--model <id>' token and
        surface it: name = f"claude {_pretty_claude_model(model)} (worker)" (or just "claude (worker)" if
        no --model). activity = "worker leg". started = None. Never raises -> [] on error."""

Use a module-level cache dict (e.g. `_claude_cache = {"t":0.0,"v":[]}`) for _claude_cmdlines, mirroring the
existing `_ext_cache` 15s pattern. In _claude_cmdlines, tokenize by splitting on whitespace is fine for the
'-p'/'--print'/'--model' checks; be robust to a missing value after --model.

## Change 4 — integrate into cloud_snapshot()
In cloud_snapshot(), after adding external_cloud_procs(), ALSO append external_claude_workers() results
(dedupe is not required for these — they're distinct worker entries; but do not double-add if a name is
already present). The existing dispatch-based + external_cloud_procs behavior stays exactly as-is.

Do NOT import textual. Do NOT change models.py. The gate is tests/test_cloud_legs_claude.py — match it.
