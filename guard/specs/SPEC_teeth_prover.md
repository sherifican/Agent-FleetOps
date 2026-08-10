# SPEC — teeth_prover.py (prove every guard can actually fail)

Write the file COMPLETELY at `../guard/teeth_prover.py`.

## Why this exists

Every guard we own was written after its incident, and nothing tests that any of them still works. That
is how you get a check that never fires — not by writing a bad check, but by writing a good check whose
input shape later moved. Our `grep -c … || echo 0` monitor was silently inert for its entire life. A
sister system shipped the identical class: a line-ending mismatch left **26 of 28 mutations bound to
nothing**, while the harness cheerfully reported "28/28 guards have teeth."

The rule this module enforces:

> **Every guard ships with a mutation that makes it go red. If you cannot write a mutation that makes
> your check fail, you have not written a check.**

Note what it does NOT do: it never proves a guard is *correct*. It proves a guard is *reachable*. Those
are different claims and the output must not blur them.

## Public interface

    from dataclasses import dataclass
    from typing import Callable

    @dataclass(frozen=True)
    class Mutation:
        name: str                             # e.g. "drop-one-relevance-line"
        target: str                           # the property name it must make fire
        apply: Callable[[str], "str | None"]  # mutated text, or None when it cannot bind

    @dataclass(frozen=True)
    class Result:
        mutation: str
        target: str
        outcome: str                # one of the OUTCOMES below
        collateral: tuple           # other property names that also flipped, sorted
        detail: str

    def prove(text: str, mutations=None, *, id_registry=None) -> list[Result]
    def coverage(text: str, mutations=None, *, id_registry=None) -> tuple  # unprovable property names
    def main(argv=None) -> int

`mutations=None` means the module-level `MUTATIONS` list.

## The outcomes — five, and the distinctions are load-bearing

    HAS_TEETH    the mutation applied, the target property flipped ok True -> False,
                 and no other property flipped
    OVERBROAD    the target flipped, but other properties flipped too. Informational, not a
                 failure — some mutations legitimately disturb two properties. Report the collateral.
    VACUOUS      the mutation applied but the target property did NOT flip.
                 The guard has been lying. This is the LOUDEST outcome.
    NOT_APPLIED  apply() returned None or returned the text unchanged — the mutation could not bind.
                 **Just as loud as VACUOUS.** A mutation that fails to APPLY is reported as loudly as
                 one that fails to FIRE; this is the exact failure that hid 26 dead mutations elsewhere.
    BAD_FIXTURE  the baseline text ALREADY violates the target property, so flipping it proves nothing.
    UNMEASURED   extraction raised on either baseline or mutant.

**Never collapse NOT_APPLIED into a pass, and never collapse it into VACUOUS.** They have different
causes (a mutation whose anchor text moved vs a guard whose logic went inert) and different fixes.

### The subtlety that makes this honest

Mutating a *known-clean* fixture is the only valid test. "It fails on an old broken artifact" proves
nothing, because the old artifact was missing every property at once — a red light there says nothing
about which check watched it. So `prove()` MUST:

  1. extract the baseline first,
  2. assert the target property is ok=True in the baseline (else BAD_FIXTURE),
  3. only then apply the mutation and compare.

Compare by `ok` flags only. Do not compare `value` — `row_count` and `word_count` change value for
almost any edit while remaining ok=True, and treating that as a flip would make every result OVERBROAD.

## The mutations (write one per judging property, minimum)

Each takes the fixture text and returns a mutated copy, or None if its anchor is absent. Never mutate
blindly with `str.replace` and assume it bound — check the anchor is present first and return None if
it is not. That check is the whole point of NOT_APPLIED.

    drop-one-relevance-line     -> relevance_lines_present   remove the "RELEVANCE: Memory = ..." line
    bogus-relevance-rating      -> relevance_values_valid    rewrite one rating to CRITICAL
    all-high-relevance          -> relevance_calibrated      rewrite every rating to HIGH
    remove-actionable-table     -> table_present             cut from the §5 heading to EOF
    invented-action-verb        -> actions_in_vocab          rewrite one action cell to YOINK
    banned-action-verb          -> no_banned_verbs           rewrite one action cell to TRY
    invented-bucket             -> buckets_in_vocab          rewrite one bucket cell to DevOps
    out-of-range-priority       -> priorities_in_vocab       rewrite one priority cell to P3
    fabricated-video-id         -> video_ids_resolve         rewrite the source id to aNiBFVjvoqk
    strip-source-link           -> has_source_link           remove every youtu.be / youtube.com URL

`aNiBFVjvoqk` is deliberate: it is a real fabricated id this pipeline emitted on 2026-07-31. The
mutation reproduces an actual defect, not a hypothetical one.

`row_count` and `word_count` are REPORTING properties (always ok=True) and are correctly unprovable —
they have no failure mode to reach. `coverage()` must classify them as EXPECTED-UNPROVABLE via a
module constant `REPORTING_ONLY = {"row_count", "word_count"}` rather than silently ignoring them.

## coverage() — what the prover physically cannot reach

A harness that can only reach part of the system while reporting "N/N guards have teeth" is the same
defect one level up. `coverage()` returns the sorted tuple of property names that appear in a baseline
extraction but have NO mutation targeting them and are not in `REPORTING_ONLY`. `main` prints these as
`UNPROVABLE: <name>` and they make the run fail. A property nobody can make fire is indistinguishable
from a property that does not work.

## main / CLI

    teeth_prover.py [--fixture PATH] [--registry PATH]...

Default fixture: the module constant `DEFAULT_FIXTURE`, a clean in-file report string (so the prover
runs with zero external dependencies). `--fixture` replays a real cached `RESULT_*.md` instead.
When `--fixture` is given and no `--registry`, default the registry to the tmp backlogs
`~/.claude/jobs/c08a8bf5/tmp/backlog_all.txt` so `video_ids_resolve` is actually evaluated.

Print one line per mutation: `<outcome padded>  <mutation>  -> <target>` plus collateral/detail when
present, then a summary counting each outcome.

Exit codes:

    0 = every mutation HAS_TEETH or OVERBROAD, and coverage is complete
    1 = any VACUOUS, NOT_APPLIED, BAD_FIXTURE, or any UNPROVABLE property
    2 = any UNMEASURED

## Hard rules

- Pure and offline. No network, no clock, no randomness. Reads files only via `--fixture`/`--registry`.
- The prover must not import or depend on anything that would let a mutation leak to disk. It operates
  on strings in memory only; it never writes an artifact.
- `prove()` must never raise; internal errors become UNMEASURED results.

## Verification

Gate: `../guard/tests/test_teeth_prover.py` — do NOT edit it.

    cd .. && python3 -m pytest guard/tests/test_teeth_prover.py -q

Must be fully green. Do not finish on red.
