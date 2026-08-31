# SPEC — stage_ledger.py (counts close, or it did not happen)

Write the file COMPLETELY at `../guard/stage_ledger.py`.

## Why this exists

A queue processed **13 of 25** items, printed `DONE`, and reported `rc=0` for every item. A child had
eaten the loop's stdin. Nothing was wrong with any individual item; the only thing that betrayed it was
the COUNT. Separately, a 3-leg dispatch quietly became a 2-leg dispatch for 15 days.

The invariant that would have caught both:

> **items_in == items_out + dropped_with_reason**, per stage. A stage that cannot explain a missing
> item fails. And a stage that never ran at all is ABSENT — which must be loud, never silent.

## Public interface

    @dataclass(frozen=True)
    class StageRecord:
        stage: str
        items_in: int
        items_out: int
        dropped: dict          # reason -> count; reasons are free-form strings
        run_id: str

        @property
        def accounted(self) -> int      # items_out + sum(dropped.values())
        @property
        def closes(self) -> bool        # items_in == accounted

    class Ledger:
        def __init__(self, path: str, run_id: str, declared_stages: list[str]): ...
        def record(self, stage, items_in, items_out, dropped=None) -> StageRecord
        def records(self) -> list[StageRecord]        # this run only, in write order
        def check(self) -> tuple                      # (violations, unmeasured)

    def check_file(path: str, run_id: str, declared_stages: list[str]) -> tuple
    def main(argv=None) -> int

`declared_stages` is the pipeline's stage list, declared UP FRONT. It is what makes absence detectable:
without it, a stage that never ran is simply not in the file, and a missing row looks exactly like a
clean run.

## Storage

Append-only JSONL at `path`, one object per record: stage, items_in, items_out, dropped, run_id, seq.
Append-only is deliberate — a ledger you can rewrite is a ledger that can launder a bad run.

`record()` appends immediately (flush + `os.fsync` not required, but the file must be readable by
another process right after the call returns). Never rewrite or truncate an existing file. Opening an
existing ledger for a new `run_id` appends to it; records from other run_ids are ignored by
`records()` and `check()` but must be preserved on disk.

`seq` is a per-run monotonic counter starting at 0, assigned in `record()` — do NOT use a clock
(determinism, and the module must be testable without freezing time).

## check() — what it returns

Returns `(violations, unmeasured)`, both lists of strings.

**violations** — one per failure:

1. *Closure*: `items_in != items_out + sum(dropped)`. Message must name the stage, both totals, and the
   unexplained count, e.g.
   `dispatch: 25 in, 13 out + 0 dropped = 13 accounted — 12 items unexplained`

2. *Chain*: for consecutive DECLARED stages both of which recorded, `stage[n].items_out !=
   stage[n+1].items_in`. Message names both stages and both numbers. This is what catches an item
   vanishing BETWEEN stages, which per-stage closure alone cannot see.

3. *Negative or non-integer counts* — a count that is not a non-negative int is a violation, not a crash.

4. *Duplicate record* for the same (run_id, stage) — the second write is a violation naming both sets of
   numbers. Silently keeping the last one would let a re-run overwrite a bad result.

**unmeasured** — one per declared stage with NO record in this run:
`UNMEASURED: <stage> — declared but never recorded`.

An unmeasured stage is NOT a violation and NOT a pass. It is its own category with its own exit code,
because "it failed" and "we never looked" need different responses.

## Exit codes (main)

    0 = every declared stage recorded and every count closes
    1 = at least one violation
    2 = at least one unmeasured stage

If both are present, print both and **exit 2** — not knowing whether a stage ran is the more serious
condition, because it can hide any number of violations.

A clean run must still print the per-stage numbers, so a green result is evidence rather than silence.

    usage: stage_ledger.py <ledger.jsonl> --run-id ID --stages a,b,c

## Hard rules

- Never raise on a malformed ledger line: an unparseable line becomes a violation naming the line
  number, and processing continues. A guard that dies on bad input stops guarding at the first problem.
- Pure except for the append in `record()` and the read in `check_file`.
- Deterministic: no clock, no randomness, sorted output where order is not semantically meaningful.

## Verification

Gate: `../guard/tests/test_stage_ledger.py` — do NOT edit it.

    cd .. && python3 -m pytest guard/tests/test_stage_ledger.py -q

Must be fully green. Do not finish on red.
