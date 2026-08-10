# SPEC — cloud_legs.py: detect cloud legs by their REAL process names (kimi runs as `kimi-code`)

Edit ~/fleet_tui/fleet_tui/sources/cloud_legs.py IN PLACE. Preserve ALL existing behavior.
Pure/headless, never raises. Must pass the FULL tests/test_cloud_legs_claude.py + tests/test_cloud_legs.py
unchanged, plus the new tests/test_cloud_legs_procnames.py.

## Bug
`external_cloud_procs()` does `pgrep -x <marker>` for each SESSION_MARKER (codex/grok/kimi). But a CLI's
actual OS process name can differ from the marker — **kimi's headless CLI runs as `kimi-code`**, so
`pgrep -x kimi` never matches and a running kimi leg is INVISIBLE in the TUI (owner-reported 2026-07-08).

## Fix
Add a mapping of each leg to the set of REAL process names (comm) to check, and iterate it in
external_cloud_procs():

    # each cloud leg -> the exact process names (comm) its CLI may run as. `pgrep -x` needs the real exe
    # name; the kimi CLI runs as 'kimi-code', codex/grok as themselves. (owner-reported 2026-07-08)
    SESSION_PROCS = {
        "codex": ("codex",),
        "grok":  ("grok",),
        "kimi":  ("kimi", "kimi-code"),
    }

Rewrite the loop in `external_cloud_procs()` to iterate `SESSION_PROCS.items()`; for each `(label, names)`,
run `pgrep -x <name>` for each name in `names`, and if ANY matches (returncode 0 + nonempty stdout), append
ONE entry `{"name": f"{label} (session)", "activity": "interactive session", "started": None}` for that
label (do NOT double-add the same label if multiple of its names match). Keep the ~15s cache (`_ext_cache`),
the timeout=3, and the try/except-per-name exactly as now. Keep `SESSION_MARKERS` defined (other code/tests
may reference it) — you may derive it as `tuple(SESSION_PROCS.keys())` or leave it as-is; either is fine as
long as `"claude" not in SESSION_MARKERS` still holds and both existing test files pass.

Do NOT import textual. Do NOT change models.py. Gate = tests/test_cloud_legs_procnames.py + the two existing
cloud_legs test files — match them exactly.
