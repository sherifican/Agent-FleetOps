# SPEC — leg_canary.py (an unexercised component must be asserted alive)

Write the file COMPLETELY at `../guard/leg_canary.py`.

## Why this exists

The grok leg was **dead for 15 days**. A sandbox change made its prompt file unreachable, so a 3-leg
dispatch quietly became a 2-leg dispatch. Nothing noticed, because in that window nobody dispatched to
grok — and a component that is never exercised can hide its own death indefinitely.

The generalised rule:

> **A component that is not exercised must be independently asserted to exist and be reachable, on a
> schedule, regardless of whether work is flowing through it.**

A daily synthetic dispatch per leg converts "silently dead" into "loudly dead within 24h."

## The rule that shapes the whole module

> **Judge the artifact, never the exit code.**

A leg can exit 0 having written nothing, and can exit non-zero having produced a perfectly good answer.
Both have happened here: kimi's quota cap presents as rc=1 with empty stdout AND empty stderr, and a
queue reported rc=0 for all 25 items while processing 13. So `probe()` decides ALIVE **only** by finding
the expected token in the response text. The exit code may be recorded as evidence; it must never be the
verdict.

## Public interface

    @dataclass(frozen=True)
    class Leg:
        name: str
        argv: list          # the command; the prompt is appended as the final argument
        timeout: int = 120

    @dataclass(frozen=True)
    class Probe:
        leg: str
        outcome: str        # "ALIVE" | "DEAD" | "UNMEASURED"
        evidence: str       # trimmed response excerpt or the failure reason
        rc: object          # the exit code, recorded but NOT the verdict (None if never ran)

    CANARY_TOKEN = "CANARY-OK"
    CANARY_PROMPT = (
        "Reply with exactly this one word and nothing else: CANARY-OK"
    )

    LEGS = [ Leg("kimi", ["kimi-cli"]), Leg("grok", [...]), Leg("codex", [...]),
             Leg("gemini36", [...]) ]

    def probe(leg: Leg, *, runner=None) -> Probe
    def probe_all(legs=None, *, runner=None) -> list
    def load_state(path) -> dict
    def save_state(path, state) -> None
    def stale(state, legs, *, now, max_age_hours=26) -> list
    def main(argv=None) -> int

`runner` is `Callable[[list, str, int], tuple[int, str]]` returning `(rc, stdout_text)`. Defaulting it to
None means "use the real subprocess runner". **Every test injects a fake runner**, so the module must be
fully exercisable with no network and no cloud spend. A guard you cannot test offline will not be tested.

## probe() semantics

    ALIVE       CANARY_TOKEN appears in the response text (case-insensitive, whitespace-tolerant)
    DEAD        the runner returned, but the token is absent — INCLUDING when rc == 0
    UNMEASURED  the runner raised, timed out, or the command does not exist

`DEAD` and `UNMEASURED` are different: DEAD means we asked and got a wrong answer (the leg is broken);
UNMEASURED means we never got to ask (the probe is broken). Conflating them sends you debugging the
wrong system — a sibling team burned a day on three false reds that were all broken measurements.

`evidence` for a DEAD leg must include the first ~200 chars of what came back instead, so a human can
tell a quota message from a crash from an empty string without re-running anything.

## Staleness

State file is JSON: `{ "<leg>": {"last_alive_seq": <int>} }`.

Use an INJECTED integer clock, not a wall clock: `stale(state, legs, *, now, max_age_hours=26)` where
`now` is an integer hour counter supplied by the caller. `main` derives it from the real clock exactly
once and passes it down. This keeps every other function deterministic and testable — the same reason
`stage_ledger` forbids a clock in its core.

`stale()` returns the sorted list of leg names whose `last_alive_seq` is missing or older than
`max_age_hours`. A leg that has NEVER been alive is stale — absence of evidence is not evidence of
health, and a fresh state file must not read as a clean bill of health.

Default 26 hours (not 24) so a daily cron does not false-alarm on ordinary jitter.

## main / CLI

    leg_canary.py [--state PATH] [--legs a,b] [--max-age-hours N] [--dry-run]

`--dry-run` uses a built-in fake runner that always returns the token — it exercises the wiring without
spending a cloud call, and must be clearly labelled as such in the output so a dry run can never be
mistaken for a real probe.

Print one line per leg: `<outcome padded> <leg>  rc=<rc>  <evidence excerpt>`.
Then a `STALE: <leg> (last alive <n>h ago | never)` line per stale leg.

Exit codes:

    0 = every probed leg ALIVE and nothing stale
    1 = any DEAD or any stale leg
    2 = any UNMEASURED

Exit 2 dominates: if we could not measure a leg we do not know whether the others' results are
meaningful.

Update state only for legs that probed ALIVE. Never write a "last alive" for a DEAD or UNMEASURED
probe — that would launder a failure into the health record, which is the same laundering problem a
stored baseline has.

## Hard rules

- No network in any function except the default real runner.
- `probe()` must never raise; every failure becomes UNMEASURED.
- The canary prompt must stay trivially cheap — one word, no tools, no context.

## Verification

Gate: `../guard/tests/test_leg_canary.py` — do NOT edit it.

    cd .. && python3 -m pytest guard/tests/test_leg_canary.py -q

Must be fully green. Do not finish on red.
