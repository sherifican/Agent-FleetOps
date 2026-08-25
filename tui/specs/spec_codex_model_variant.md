# Spec — codex model/profile variant rows in the ☁ CLOUD section

## The defect

Every running codex process renders as one flat row: `codex (session)`. The fleet now runs codex under
several distinct variants, and the panel cannot tell them apart. The owner's ask:

> "add a distinction to the TUI so when different models are being used it doesn't just say codex for all
> of them. that way i can get a good idea of what type of task is being done at a glance by seeing what
> type of model / profile is loaded."

The variant IS the task type. Sol-at-xhigh is the fleet's most expensive configuration and means hard
reasoning; Luna-at-medium means a cheap lookup. Collapsing them into one label throws away the signal.

## The signal is already on the command line

Observed live cmdlines (verified 2026-08-08 by reading /proc, not assumed):

    codex exec --sandbox workspace-write -m gpt-5.6-sol -c model_reasoning_effort=xhigh -o /path -
    codex exec --sandbox workspace-write -p terra
    codex exec --sandbox workspace-write -p luna
    codex exec --sandbox workspace-write -p terra -c model_reasoning_effort=xhigh

Two different wrapper styles, both readable:
  - `codex-fleet` passes an explicit `-m <model-id>` plus a hardcoded `-c model_reasoning_effort=xhigh`.
  - `codex-terra` / `codex-luna` pass `-p <profile>` and let the profile toml own model + effort.
  - `codex-research` passes `-p terra` AND an effort override, so profile alone does not identify it.

## CRITICAL GOTCHA — `-p` means PROFILE in codex, not print

In the kimi and claude readers in this same module, a `-p` token means PRINT mode. In codex it means
PROFILE and takes a value. Do NOT copy the kimi mode logic. Reading `-p` as print here would mislabel
every profile-based invocation.

## Honesty rule (critical — do not "improve" this)

Report only what the command line actually says.

  - Effort is shown ONLY when `-c model_reasoning_effort=<x>` is literally present in the argv.
  - When a profile is used with no effort override, the effort lives in a toml file this reader does not
    read. In that case show NO effort at all. Do NOT guess it, do NOT hardcode "terra means high".
  - Do NOT map a profile name to a model id. `-p terra` says the profile is terra; it does not prove the
    model is gpt-5.6-terra, because the toml could be edited. Display the token that was actually given.

A row that invents an unobserved value is worse than a row that omits it.

## What to build in fleet_tui/sources/cloud_legs.py

Mirror the existing kimi variant reader in this same file (read_kimi_procs / build_kimi_rows /
kimi_status). Same shape, same purity contract, same 15s module cache pattern.

### Module constants

    CODEX_PROFILE_DISPLAY maps a lowercase profile token to a display label:
        "sol" -> "Sol", "terra" -> "Terra", "luna" -> "Luna"
    An unknown profile passes through capitalised.

    _codex_cache = {"t": 0.0, "v": []}     (same shape as _kimi_cache)

### _is_codex_invocation(argv) -> bool

True only when codex is the PROGRAM, never when "codex" merely appears as an argument.
Mirror _is_kimi_invocation exactly:
  - basename(argv[0]) == "codex", OR
  - some later token contains "/" and its basename == "codex" (the wrapper/timeout case).
A bare token "codex" that is not argv[0] is an argument (think `grep -r codex .`) and must not match.
Never raises.

### _codex_variant_from_argv(argv) -> tuple of (label, via)

Token EQUALITY only, never substring.
  - If an exact "-m" or "--model" token is present and has a following value, take that value as the
    model id. Strip a leading "gpt-5.6-" prefix if present, then capitalise the remainder, so
    "gpt-5.6-sol" becomes "Sol". If nothing remains after stripping, use the raw id unchanged.
    Return (label, "model").
  - Else if an exact "-p" or "--profile" token is present with a following value, or a joined
    "--profile=<x>" / "-p=<x>" form, look the lowercased value up in CODEX_PROFILE_DISPLAY, falling
    back to the value capitalised. Return (label, "profile").
  - Else return ("", "default") — no variant information on the command line.
Never raises.

### _codex_effort_from_argv(argv) -> str

Return the effort ONLY if literally present. Accept both spellings seen in the wrappers:
  - a "-c" token whose following value starts with "model_reasoning_effort=" -> take the part after "="
  - a single joined token starting with "model_reasoning_effort=" -> same
Strip surrounding double quotes from the value if present.
Return "" when absent. Never invent a default. Never raises.

### _codex_mode_from_argv(argv) -> str

"dispatch" when an exact "exec" token appears among argv[1:], else "session".
codex exec is the non-interactive fleet path; a plain `codex` is the owner's interactive session.
Do NOT use -p for this. Never raises.

### read_codex_procs() -> list

Uses the existing _iter_proc_cmdlines() generator — do NOT add a new /proc reader and do NOT shell out.
Honours a 15s module cache in _codex_cache exactly like read_kimi_procs.
One record per matching invocation:
    {"pid": int, "variant": str, "via": str, "effort": str, "mode": str}
Skip any single process that raises; return [] if the whole thing fails. Never raises.

### build_codex_rows(procs) -> list

PURE, no I/O. One display record per proc:
    name:     "codex " + variant when variant is non-empty, else just "codex"
    activity: "fleet dispatch" when mode is dispatch, else "interactive session";
              then, ONLY when effort is non-empty, append " · " + effort
    started:  None

Dedupe identical (name, activity) pairs so two workers of the same variant collapse into one row —
the panel answers "what is running", not "how many copies". Preserve first-seen order.

### codex_status() -> list

Convenience: read_codex_procs() then build_codex_rows(). Returns [] on any exception. Never raises.

### Wiring into cloud_snapshot(dispatches)

Follow the kimi precedent already in that function, which is the pattern to copy:
  - call codex_status() inside a try/except that degrades to [] (the panel must never die)
  - in the loop over external_cloud_procs(), when the base name is "codex" AND codex rows exist, skip
    the generic "codex (session)" row — it is superseded by the specific ones
  - append each codex row that is not already present by name

## Contracts that must hold

  - ZERO textual import. The module stays a pure headless source.
  - Nothing in here may raise. Every public function degrades to [] or "".
  - Do not modify the kimi, claude, or dispatch logic in this file. Codex rows only.
  - Do not add a subprocess call. _iter_proc_cmdlines() is the only seam.

## The gate

tests/test_codex_model_variant.py is Claude-authored and is the real gate. It patches
_iter_proc_cmdlines with raising=True so it never touches the live process table. Run:

    cd ~/fleet_tui && .venv/bin/python -m pytest -q

The whole suite must stay green, not just the new file.
