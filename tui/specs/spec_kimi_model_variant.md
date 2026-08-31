# SPEC — identify WHICH Kimi model is running (K3 vs K2.7 Code) and in what MODE

## The problem
The MODELS panel currently shows any running kimi process as a single generic row:
"kimi (session)" / "interactive session". Two things are wrong with that:

1. **The model variant is invisible.** The kimi CLI can run K3 or K2.7 Code. K3 is selected
   only by an explicit `-m k3`; with no `-m` the CLI falls through to the config default
   (`kimi-code/kimi-for-coding`, display name "K2.7 Code"). The owner has a standing directive
   to trial K3 specifically, so "which one is actually running" is the whole point.
2. **A headless fleet dispatch is mislabelled "interactive session".** Fleet dispatches run
   `kimi -p <prompt>` (via the kimi-cli wrapper). Those are fleet work, not the owner typing.

## What to build
Add to `fleet_tui/sources/cloud_legs.py` (do not create a new module):

- `_iter_proc_cmdlines()` — a generator yielding `(pid:int, argv:list[str])` for every readable
  process on the box. This is the ONLY place that touches `/proc` (or `ps`), so the rest of the
  logic is pure and the tests monkeypatch this single seam. It must never raise: skip any pid it
  cannot read and keep going.
- a module-level dict `KIMI_MODEL_DISPLAY` mapping a raw model id to a display label:
      "k3" maps to "K3"
      "kimi-code/kimi-for-coding" maps to "K2.7 Code"
      "kimi-for-coding" maps to "K2.7 Code"
- a module-level string `KIMI_DEFAULT_MODEL_ID` set to "kimi-code/kimi-for-coding"
  (what the CLI uses when no -m flag is given).
- `read_kimi_procs()` returning a list of dicts, newest-agnostic order, each:
      {"pid": int, "model": str, "mode": str, "raw_model": str}
  where `mode` is exactly "dispatch" or "session", and `model` is a display label.
- `build_kimi_rows(procs)` — a PURE composer (no I/O) turning that list into display records:
      {"name": "kimi <MODEL>", "activity": "<fleet dispatch|interactive session>", "started": None}
  For example a K3 dispatch becomes name "kimi K3", activity "fleet dispatch".
- `kimi_status()` — convenience that calls `read_kimi_procs()` then `build_kimi_rows()`.

## ⚠ MEASURED PROCESS SHAPE — read this before writing any detection

This was measured live on 2026-07-28 while a real dispatch was running. It is NOT what you would
guess, and the obvious implementation silently detects nothing:

      pid 1739966  comm=timeout    argv: timeout 600 ~/.kimi-code/bin/kimi -m k3 -p "<prompt>" --output-format stream-json
      pid 1739968  comm=kimi-code  argv: kimi-code

**The process that `pgrep -x kimi-code` matches carries NO flags.** Its entire argv is the single
token "kimi-code". The `-m k3` and `-p` flags live on a DIFFERENT process — the wrapper that
launched it (here `timeout`, whose comm is not kimi-anything).

So: reading `/proc/<matched-pid>/cmdline` finds no `-m` and no `-p`, and would report every run as
default-model/interactive — reproducing the exact bug this feature exists to fix.

## How to detect (exact rules — these matter)

**Finding the invocation.** Do NOT rely on `pgrep -x` alone. Scan the process table for the
INVOCATION. A process counts as an invocation when EITHER:
  (a) the basename of argv[0] is exactly `kimi` or `kimi-code`, OR
  (b) some LATER token contains a path separator "/" AND its basename is exactly `kimi`
      (this is the wrapper case: `timeout 600 /home/…/.kimi-code/bin/kimi -m k3 -p …`).

**A bare token `kimi` that is NOT argv[0] does NOT count.** It is an argument, not the program.
Counter-examples that MUST NOT match, both real commands seen on this box:
      grep -r kimi ~            (bare "kimi" is a search string, not argv[0])
      less ~/.kimi-code/README.md   (basename is README.md, not kimi)

Implement by iterating `/proc/<pid>/cmdline` for numeric entries of `/proc`, or by shelling out
once to `ps -eo pid,args` — either is fine, but it must be behind the 15 second cache.

**Fallback.** If no invocation argv is found but a process with comm `kimi-code` exists (via
`pgrep -x kimi-code`), emit ONE record for it with the default model and mode "session" — kimi is
running but how it was started cannot be seen. Never emit nothing when kimi is demonstrably up.

**De-duplication.** One record per invocation. The `kimi-code` child of an invocation already
counted must NOT produce a second row.

**Reading the cmdline.** Read `/proc/<pid>/cmdline` and split on the NUL byte, dropping empty
trailing entries. This yields an argv token LIST.

**Model.** Walk the token list. If a token is exactly `-m` or `--model`, the NEXT token is the
raw model id. Also accept a joined form `--model=<id>`. If no such flag is present, the raw
model id is `KIMI_DEFAULT_MODEL_ID`. Map the raw id through `KIMI_MODEL_DISPLAY`; if the id is
unknown, use the raw id itself as the display label (never drop it, never guess).

**Mode.** `mode` is "dispatch" if the token list contains a token exactly equal to `-p` or
`--print`, else "session".

**CRITICAL — token equality, never substring.** Compare whole argv tokens for `-m`, `-p`,
`--print`. Do NOT use `"-p" in cmdline_string`. A substring test false-positives on things like
`--json-path` or any path containing "-p", which is exactly how a previous version of this file
mistook the orchestrator for a worker leg.

## Hard contracts (the TUI will not tolerate violations)
- **Never raise.** Every reader is wrapped so a missing `/proc` entry, a permission error, a
  dead pid mid-read, or a malformed cmdline yields a skipped entry, and total failure yields an
  empty list. A source that raises takes the whole panel down.
- **Cache the pgrep.** Reuse the existing 15 second cache pattern in this module (see
  `_ext_cache`) with its own dict, e.g. `_kimi_cache`. The refresh loop ticks every 1 second and
  must never spawn a subprocess per tick.
- **`build_kimi_rows` does zero I/O.** It must be unit-testable on hand-built input.
- **Do not change** `SESSION_PROCS`, `CLOUD_MARKERS`, `SESSION_MARKERS`, `is_cloud_leg`, or the
  existing `external_cloud_procs` behaviour for codex/grok/antigravity. Kimi rows will be
  swapped in by the caller; that wiring is not your job.

## The gate
`tests/test_kimi_model_variant.py` is authored separately and is the acceptance gate. Make it
pass without editing it. Run the whole suite; it must stay green.
