# SPEC — cloud_legs.py: stop counting the orchestrator + show "Claude (model)" tag

Edit ~/fleet_tui/fleet_tui/sources/cloud_legs.py IN PLACE. Preserve ALL existing behavior and
every function; make ONLY the changes below. Pure/headless, never raises. Must pass the FULL
tests/test_cloud_legs_claude.py + tests/test_cloud_legs.py unchanged.

## Bug — the interactive orchestrator is being shown as a running "claude (session)"
`external_cloud_procs()` iterates `CLOUD_MARKERS` and does `pgrep -x <marker>` for each, labelling any match
as an interactive "(session)". Since `CLOUD_MARKERS` now includes "claude", it matches THIS process (the
orchestrator's bare `claude`), which is NOT a leg. Fix by giving `external_cloud_procs()` its OWN marker
list that EXCLUDES claude:

    SESSION_MARKERS = ("codex", "grok", "kimi")

- Keep `CLOUD_MARKERS = ("codex", "grok", "kimi", "claude")` unchanged (is_cloud_leg still needs claude for
  DISPATCH-leg detection).
- In `external_cloud_procs()`, iterate `SESSION_MARKERS` instead of `CLOUD_MARKERS` (so it never pgreps
  "claude" → the orchestrator is never shown). Everything else in that function stays identical.
- (Claude WORKER legs — real `claude -p` sub-tasks — are still detected by `external_claude_workers()`,
  which correctly filters on the `-p`/`--print` token. That path is unchanged.)

## Feature — show "Claude (<model>)" so the owner sees which model is running
Add a helper that turns a DISPATCH leg id into a display name with the model tag:

    def _claude_leg_display(leg: str) -> str:
        """A claude-* dispatch leg id -> 'Claude (<Model>)'. Maps the known legs to their model, else best-effort."""
    # mapping: "claude-opus" -> model "claude-opus-4-8"; "claude-sonnet" -> "claude-sonnet-5";
    # "claude-haiku" -> "claude-haiku-4-5". Then name = f"Claude ({_pretty_claude_model(model)})".
    # For an unknown "claude-<x>", use _pretty_claude_model("claude-<x>") inside the parens (never raise).

Wire it in `active_cloud_legs()`: when a running leg `is_cloud_leg(leg)` AND the leg name starts with
"claude" (case-insensitive), set the record's `name` to `_claude_leg_display(leg)` instead of the raw leg
id. NON-claude cloud legs (codex/grok/kimi) keep their raw leg name EXACTLY as now.

Also update `external_claude_workers()` so its worker name uses the SAME "Claude (<model>)" tag form:
    name = f"Claude ({_pretty_claude_model(model)}) · worker"   (or "Claude (worker)" if no --model)
(i.e. the model tag comes right after "Claude", matching the dispatch legs; keep the "· worker"/"worker"
distinction so an external worker is still visibly a worker.)

Do NOT import textual. Do NOT change models.py. Gate = tests/test_cloud_legs_claude.py — match it exactly.
