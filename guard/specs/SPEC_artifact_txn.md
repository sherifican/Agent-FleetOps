# SPEC — artifact_txn.py (a transaction for artifacts that mutate in place)

Write the file COMPLETELY at `../guard/artifact_txn.py`.

## Why this exists

`VIDEO_RESEARCH_HUB.html` is a 1.9 MB artifact that is spliced in place, card by card, and is not under
version control. A splice has already overwritten a prior card with no history and no way back. Every
other guard here is about DETECTING a problem; this one is about SURVIVING one.

The shape, lifted from a sibling system's update transaction:

    stage to <file>.dl.tmp  ->  verify it THERE  ->  snapshot the live file to <file>.prev
    ->  atomic replace  ->  on any failure, restore every already-committed file from .prev

Verifying the staged copy rather than the live file is the point: a bad write is never allowed to become
the live artifact, so there is nothing to recover from in the common case.

## Public interface

    class TransactionError(Exception): ...

    class Transaction:
        def __init__(self, *, tmp_suffix=".dl.tmp", prev_suffix=".prev"): ...
        def stage(self, path: str, content: str) -> None
        def verify(self, path: str, validator) -> None
        def commit(self) -> list[str]
        def rollback(self) -> list[str]
        def __enter__(self) -> "Transaction"
        def __exit__(self, exc_type, exc, tb) -> bool   # returns False (never swallows)

`validator` is `Callable[[str], bool | str]` receiving the STAGED content. Return True (or "") for ok;
return False or a non-empty string to fail, where the string is the reason. Any exception it raises is
a verification failure with that exception as the reason.

## Semantics

**stage(path, content)** — writes `content` to `path + tmp_suffix`. Multiple stages of the same path
overwrite the staged copy. Staging alone never touches the live file. Parent dir must exist; if not,
raise TransactionError immediately (fail before any live file is at risk).

**verify(path, validator)** — reads the STAGED copy and runs the validator. On failure raise
TransactionError naming the path and the reason. Verifying a path that was never staged is a
TransactionError, not a silent pass — an unverified file that looks verified is the failure mode this
whole subsystem exists to prevent.

**commit()** — for every staged path, in a deterministic (sorted) order:
  1. if the live file exists, copy it to `path + prev_suffix` (copy, not move — the live file must stay
     readable until the moment of replacement), and copy the live file's permission bits onto the
     staged tmp — `os.replace` hands the live path the TMP's mode, so an executable rewritten through
     the transaction would otherwise lose its `+x` silently. A path with no live file keeps the
     process default (umask); the transaction invents no mode.
  2. `os.replace(tmp, path)` — atomic within a filesystem, so a reader never sees a partial file
Returns the list of committed paths. If ANY step raises, immediately roll back every path already
committed in THIS commit, remove leftover tmp files, and re-raise as TransactionError.

**rollback()** — restores each committed path from its `.prev`, and removes staged tmp files. A path
that had no `.prev` (it did not exist before) is DELETED, returning the tree to its pre-transaction
state. Returns the list of restored paths. Rollback must be safe to call twice.

**Context manager** — `__exit__` rolls back if an exception is propagating OR if `commit()` was never
called. Leaving the block without committing must NOT silently leave staged files lying around.
It returns False so the original exception still propagates: a transaction that swallows the error it
rolled back for is worse than no transaction.

**After a successful commit** the `.prev` files REMAIN on disk. They are the one-step history the hub
never had. Tmp files are always removed.

## Hard rules

- Never write to the live path except via `os.replace`. No `open(live, "w")` anywhere.
- Deterministic ordering everywhere, so a failure mid-commit is reproducible.
- Text is written UTF-8 with `newline=""` so line endings survive round-trip unchanged. A tool that
  silently rewrites line endings has already cost a sibling system a false regression hunt.
- No network, no clock in the committed content.
- A rewrite preserves the target's permission bits: a 0755 target is still 0755 after commit
  (0644 and 0700 likewise). The red case is real, not hypothetical: tmp+replace hands the target
  the tmp's default mode, and executables rewritten through such a transaction lose `+x`.

## Verification

Gate: `../guard/tests/test_artifact_txn.py` and `../guard/tests/test_artifact_txn_mode.py` — do NOT
edit them. The mode gate's red demo is mutation TX1 in `../guard/mutation_harness.py` (drop the
chmod line; the gate must go red).

    cd .. && python3 -m pytest guard/tests/test_artifact_txn.py guard/tests/test_artifact_txn_mode.py -q

Must be fully green. Do not finish on red.
