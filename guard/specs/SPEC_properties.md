# SPEC — research_properties.py (the property extractor)

Write the file COMPLETELY at `../guard/research_properties.py`.

## Why this exists

This repo's research pipeline emits nondeterministic text (LLM reports). Prose cannot be diffed against prose.
So the pipeline does NOT store a golden baseline. Instead it extracts **deterministic properties** — functions of
the text whose answers are stable even though the text is not — and those are what get watched.

This module is the foundation. A teeth-prover, a stage-closure checker and a regression differ all
consume its output. It must be PURE: no network, no clock, no randomness, no writes. Same input bytes
must always give the same output.

## Public interface (exact — other modules compile against this)

    from dataclasses import dataclass

    @dataclass(frozen=True)
    class Property:
        name: str        # stable machine key, e.g. "relevance_lines_present"
        ok: bool         # True = the artifact satisfies this property
        value: object    # the measured value (bool, int, or tuple of str) - for reporting
        detail: str      # human-readable; "" when ok is True

    def extract(text: str, *, id_registry: frozenset[str] | None = None) -> dict[str, Property]

`extract` takes the artifact TEXT (not a path) so it is trivially testable and replayable.
It returns a dict keyed by `Property.name`. It must NEVER raise on malformed input — a property that
cannot be evaluated returns `ok=False` with a detail explaining why. Garbage in must give a verdict,
not a traceback.

Also provide:

    def load_id_registry(paths: list[str]) -> frozenset[str]

Reads the given files and returns every distinct 11-char YouTube id found in them. Missing/unreadable
file is skipped silently (it is a registry, not an assertion). Ids are matched as a run of exactly 11
chars from `[A-Za-z0-9_-]` that is bounded by a non-id char on both sides.

    def extract_file(path: str, *, id_registry=None) -> dict[str, Property]

Thin convenience wrapper: read the file (utf-8, errors=replace) and call `extract`. On OSError return
a single-entry dict `{"readable": Property("readable", False, None, str(err))}`.

## The vocabularies (import them, do NOT re-declare)

`check_leg_contract.py` in the parent directory is the EXISTING owner of these sets. Import them so
there is exactly one definition:

    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from check_leg_contract import BUCKETS, ACTIONS, BANNED, PRIOS, PROJECTS, RATINGS

If that import fails, raise ImportError at module import time. Do NOT fall back to a local copy — a
silently-duplicated vocabulary is the exact drift bug this whole subsystem exists to prevent.

## The properties to extract

Every one of these is a named key in the returned dict. All must be present in EVERY result, even when
the section they describe is absent (then `ok=False`).

1. `relevance_lines_present` — all six `RELEVANCE: <Project> = <RATING>` lines found, one per entry in
   PROJECTS. value = tuple of the project names actually found, sorted. detail names the missing ones.

2. `relevance_values_valid` — every rating found is in RATINGS (compare uppercased).
   value = tuple of the offending "Project=Rating" strings. ok=True when none offend.

3. `relevance_calibrated` — NOT every rating is the same non-NONE value. Concretely: ok=False when at
   least one rating was found AND the set of distinct ratings has no intersection with {"NONE","LOW"}.
   (Mirrors the existing "everything HIGH is uncalibrated" rule.) value = the sorted distinct ratings.

4. `table_present` — a §5 actionable table was found, OR the text contains an explicit `NONE —` /
   `NONE -` no-items declaration. value = True/False.

5. `row_count` — number of data rows parsed from the §5 table. value = int. **ok is True whenever the
   count could be determined at all**, including zero — this property REPORTS, it does not judge.
   (A count is evidence; only the closure checks turn a count into a verdict.)

6. `actions_in_vocab` — every action cell is one of ACTIONS. value = tuple of offending raw cells.

7. `no_banned_verbs` — no action cell contains a BANNED key. value = tuple of (row_label, banned_verb).
   Kept SEPARATE from `actions_in_vocab` deliberately: a banned verb is a known-synonym error with a
   prescribed fix, an unknown action is something else. The teeth-prover needs to fire them apart.

8. `buckets_in_vocab` — every non-empty bucket cell is in BUCKETS. value = tuple of offending cells.

9. `priorities_in_vocab` — every non-empty priority cell normalises to a member of PRIOS.
   Normalise by stripping every char outside `P012`. value = tuple of offending raw cells.

10. `video_ids_resolve` — **THE FABRICATION GUARD.** Every 11-char YouTube id appearing anywhere in the
    text must be a member of `id_registry`. value = tuple of the unresolved ids, sorted.
    When `id_registry` is None the property is NOT evaluated: return ok=True, value=None,
    detail="not evaluated (no registry supplied)". Skipping must be visible in the detail string, never
    silent — an unevaluated check that looks identical to a passing check is how a guard goes vacuous.
    Match ids from these contexts only, so ordinary prose cannot false-positive:
      - after `youtu.be/`
      - after `watch?v=`
      - after `youtube.com/embed/`
      - a trailing `_<11 chars>` at the end of a slug-like token (this repo's RESULT_/FINAL_ filenames)
    An id is "11 chars from [A-Za-z0-9_-]". Note ids legitimately contain `_` and `-`; do NOT split a
    slug on underscore to find them (that bug truncated `NgAglRc_ccs` to `ccs`).

11. `word_count` — whitespace-split token count of the whole text. value = int, ok always True.
    Reports, does not judge.

12. `has_source_link` — the text contains at least one `youtu.be/` or `youtube.com/watch` URL.

## Parsing notes (learned the hard way — follow these)

- Table parsing must tolerate `**bold**`, backticks and stray spaces in cells: strip `*`, backtick and
  whitespace from every cell before comparing.
- Locate the table by finding a header row (a line starting with `|`) that has BOTH an "item"-ish column
  and an "action"/"suggested"-ish column at DIFFERENT indices. Data rows start two lines below the
  header (header, then the `|:---|` separator). Stop at the first line that does not start with `|`.
- A row whose item cell is empty or consists only of `:`, `-` and spaces is a separator, not data.
- Use only the FIRST table that qualifies.
- Never assume a column exists; a missing optional column means that property has nothing to judge for
  that row, not a violation.

## Hard rules

- Pure. No I/O except in `load_id_registry` and `extract_file`.
- No property may raise. Wrap each property's own computation so one bad property cannot take out the
  other eleven; on an internal error return ok=False with the exception text in detail.
- Deterministic ordering: every tuple value is sorted before being returned, so two runs over the same
  input produce byte-identical output.

## Verification

The gate is `../guard/tests/test_properties.py`, which you must NOT edit.
Run it with:

    cd .. && python3 -m pytest guard/tests/test_properties.py -q

It must be fully green. Do not finish on red.
