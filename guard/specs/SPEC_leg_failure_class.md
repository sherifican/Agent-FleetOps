# SPEC — leg_failure_class.py (classify a leg failure: TERMINAL vs RETRYABLE)

Write the file COMPLETELY at `../guard/leg_failure_class.py`.

## Why

This repo's dispatch loop (`dispatch_video_research.sh:109-115`) is `for attempt in 1 2` with **no delay and
no classification** — every failure retried identically. Two measured costs:

- **2026-07-31:** kimi's Allegretto quota cap burned both attempts. The cap presents as `rc=1` with
  **empty stdout AND empty stderr**, failing in seconds. Retrying it is guaranteed waste.
- A **timeout** failure costs `2 × 1500s = 50 minutes` before failover fires — and failover to codex
  is the actual remedy, so every retry *delays the fix*.

Adapted from opencode's `packages/opencode/src/session/retry.ts:75` — *not retryable AND not 5xx →
stop*. This repo deliberately took ONLY that decision rule. Its header-driven timing and its 2s–30s
exponential backoff were rejected: these are subprocess wrappers with no access to HTTP headers, and this repo's
unit of work is a 5–25 minute CLI run, against which a 30-second cap is inert.

**A decision rule ports across architectures. Timing constants do not.**

## Public interface

    TERMINAL  = "TERMINAL"   # do not retry — fail over NOW
    RETRYABLE = "RETRYABLE"  # one more attempt is reasonable
    OK        = "OK"         # the artifact is good; nothing to do

    @dataclass(frozen=True)
    class Verdict:
        outcome: str     # TERMINAL | RETRYABLE | OK
        reason: str      # human-readable, names the evidence that decided it
        signature: str   # short machine key, e.g. "quota-cap", "timeout", "short-artifact"

    def classify(*, rc: int, stdout_bytes: int, stderr_bytes: int,
                 artifact_bytes: int, elapsed_s: float, timeout_s: float,
                 min_artifact_bytes: int = 6000) -> Verdict

    def main(argv=None) -> int   # CLI for the bash caller

Pure: no I/O, no clock, no network. `classify` takes measurements, returns a verdict.

## The rules, in evaluation order

1. **OK** — `artifact_bytes >= min_artifact_bytes`. A good artifact ends the question regardless of
   rc. *Judge the artifact, never the exit code* — grok exits `rc=2` on healthy runs.
   signature `"ok"`.

2. **TERMINAL / `"quota-cap"`** — `rc != 0` AND `stdout_bytes == 0` AND `stderr_bytes == 0` AND
   `elapsed_s < 60`. The observed Allegretto signature: fails fast and silent. Nothing to retry into.

3. **TERMINAL / `"timeout"`** — `elapsed_s >= timeout_s * 0.95`. A brief that exhausted its window
   will almost certainly exhaust it again; a second attempt costs another full timeout before the
   failover that actually fixes it. The 0.95 margin absorbs measurement slop.

4. **TERMINAL / `"no-such-command"`** — `rc == 127`. A missing binary never fixes itself.

5. **RETRYABLE / `"short-artifact"`** — the artifact exists but is under the minimum
   (`0 < artifact_bytes < min_artifact_bytes`). A genuine truncation/partial write; a retry can help.

6. **RETRYABLE / `"empty-with-output"`** — no artifact, but the leg produced stdout or stderr. It ran
   and said something; the failure may be transient.

7. **RETRYABLE / `"unclassified"`** — anything left. **Default to RETRYABLE, not TERMINAL.** Wrongly
   retrying costs one attempt; wrongly failing over abandons a leg that might have succeeded. The
   cheaper error is the retry, so unknown states retry once. Say `unclassified` in the reason so the
   gap is visible rather than silently absorbed.

`reason` must always name the evidence, e.g.
`"rc=1, no stdout, no stderr, failed in 4.2s — the quota-cap signature; retrying cannot help"`.

## CLI (for the bash dispatch loop)

    leg_failure_class.py --rc N --stdout-bytes N --stderr-bytes N --artifact-bytes N \
                         --elapsed 12.5 --timeout 1500 [--min-artifact-bytes 6000]

Prints one line: `<OUTCOME> <signature> <reason>`.
Exit codes so bash can branch with `if`: **0 = OK · 1 = RETRYABLE · 2 = TERMINAL**.

## Hard rules

- Pure, deterministic, never raises on odd input (negative or absurd values still return a Verdict).
- Missing/unparseable CLI args → a clear message and exit 1 (RETRYABLE), never a traceback: a broken
  classifier must degrade to the existing retry behaviour, not abandon the leg.

## Verification

Gate: `../guard/tests/test_leg_failure_class.py` — do NOT edit it.

    cd .. && python3 -m pytest guard/tests/test_leg_failure_class.py -q

Must be fully green. Do not finish on red.
