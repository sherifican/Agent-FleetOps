# SPEC — contract_agreement.py (N surfaces must agree on one contract)

Write the file COMPLETELY at `../guard/contract_agreement.py`.

## Why this exists

Our output contract is stated in FOUR places. Twice now they have silently disagreed:

  - the dispatch preamble prescribed the verbs `TRY` and `MONITOR` while the validator BANNED them.
    The legs obeyed the preamble and we blamed the legs.
  - §3b asked legs to rate a `Memory` project that the §5 bucket list did not contain, so a leg using
    `Memory` consistently in both places got flagged for being consistent.

A vocabulary that lives in more than one place drifts. The fix is not vigilance, it is a mechanical
pre-dispatch gate: **extract each vocabulary independently from every surface, then assert they are
identical.** ~20 lines of real logic that permanently kills an incident class.

## The four surfaces

    SURFACES = [
      ("validator", "./check_leg_contract.py"),      # the declared OWNER
      ("addendum",  "./ACTIONABLE_ADDENDUM.md"),     # what the legs read
      ("rollup",    "./actionable_rollup.py"),       # the harvester
      ("preamble",  "./stage_video_research.py"),    # the live prescription
    ]

Make this list a module constant so it can be overridden in tests.

## Public interface

    @dataclass(frozen=True)
    class Reading:
        surface: str                    # e.g. "validator"
        vocab: str                      # "ACTIONS" | "BUCKETS" | "PRIOS" | "RATINGS" | "PROJECTS"
        values: frozenset[str] | None   # None = this surface does not declare this vocabulary
        error: str                      # "" unless the surface could not be read/parsed

    def read_surface(name: str, path: str) -> list[Reading]
    def compare(readings: list[Reading]) -> list[str]      # returns disagreement messages
    def main(argv=None) -> int                             # exit code

## Extraction rules

**Python surfaces** — parse with `ast.parse`, walk module-level `Assign` nodes, and take any target
named ACTIONS / BUCKETS / PRIOS / RATINGS / PROJECTS whose value is a list/set/tuple of string
literals. Use `ast.literal_eval` on the value node.
**Never import the module** — importing runs code and can have side effects; the gate must be able to
inspect a file that is currently broken.

**Markdown surfaces** — the addendum states its vocabularies as backticked runs, e.g. a line
containing `` `GET` · `ADOPT` · `ADAPT` `` and so on. Extract every backticked token from the region
that follows a heading matching each vocabulary, and keep the tokens that look like vocabulary members
(uppercase words, or the hyphenated bucket names). Concretely:

  - ACTIONS: the region after a heading containing "Action" and "CLOSED vocabulary"; keep backticked
    tokens matching `^[A-Z]{3,8}$`.
  - BUCKETS: the region after a heading containing "Bucket"; keep backticked tokens matching
    `^[A-Za-z][A-Za-z-]+$` that contain a capital letter.
  - PRIOS: the region after a heading containing "Priority"; keep backticked tokens matching `^P[0-9]$`.
  - RATINGS + PROJECTS: from the §3b fenced example lines `RELEVANCE: <Project> = <...>`; the project
    names are the left-hand sides, and the ratings are the pipe-separated alternatives on the right.

  A "region" runs from its heading to the next heading of the same or higher level, or EOF.

**Struck-through / rejected tokens must be excluded.** The addendum lists banned synonyms inside `~~`
strikethrough markers (`~~TRY~~`). Strip every `~~...~~` span from the markdown BEFORE extracting, or
the gate will read the banned list as part of the vocabulary and report a false disagreement. This is
the single most likely way to get this module wrong.

## Declared sentinels (a documented superset is NOT drift)

`actionable_rollup.py` legitimately extends two vocabularies with its own bookkeeping sentinels:
`Unsorted` in BUCKETS and an em-dash in PRIOS. These are real and correct — the rollup needs a home for
unclassified rows.

    SENTINELS = {("rollup", "BUCKETS"): {"Unsorted"}, ("rollup", "PRIOS"): {"—", "-", "--"}}

Subtract the allowed sentinels from a surface's values before comparing. A surface carrying an extra
value that is NOT a declared sentinel is a disagreement. Adding a new sentinel must require editing
this constant — that is the point: the extension becomes a deliberate, reviewable act.

## Comparisons to assert

1. For each vocabulary, every surface that declares it must have an IDENTICAL value set (after sentinel
   subtraction). Report each difference as `<vocab>: <surfaceA> has X not in <surfaceB>` naming both
   sides and the exact symmetric-difference members.
2. `PROJECTS` and `BUCKETS` must be equal to each other. The addendum states this identity
   deliberately ("The six buckets MATCH the six RELEVANCE projects"); when they drifted apart we
   flagged legs for being correct.
3. **No surface may PRESCRIBE a banned verb.** Read `BANNED` from the validator. A banned verb appearing
   in the preamble or addendum as a *prescription* is the original incident. Distinguish prescription
   from documentation: a banned verb is ACCEPTABLE where it appears inside a `~~strikethrough~~`, or on a
   line that also contains "NOT valid", "not valid", "do NOT", "Rejected", "banned", or "instead of".
   Anywhere else it is a violation. Report as `preamble prescribes banned verb 'TRY'`.

## Surfacing — the exit codes matter

    0 = every surface agrees
    1 = a real disagreement was found
    2 = UNMEASURED: a surface could not be read or parsed

**Exit 2 must never be silently folded into 0.** A surface that did not get checked is absent, and
absent must be loud — a gate that quietly skips a file it cannot parse is worse than no gate, because it
reports success. Print an explicit `UNMEASURED: <surface> — <reason>` line for each, and let exit 2
dominate exit 1 only when there are no real disagreements (report both, exit 2 if any unmeasured).

Print a one-line summary per vocabulary when clean, e.g. `ACTIONS: 4 surfaces agree (7 values)`, so a
clean run still produces evidence it actually looked at something. A silent green light is not evidence.

## Verification

Gate: `../guard/tests/test_contract_agreement.py` — do NOT edit it.

    cd .. && python3 -m pytest guard/tests/test_contract_agreement.py -q

Must be fully green. Do not finish on red.
