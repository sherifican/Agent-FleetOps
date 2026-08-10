# guard/ — drift guards for a multi-stage research pipeline, mutation-proven

Adapted from a shipping desktop application's `_breaker/` verification stack. The transferable
part was the META-harness: machinery that keeps invariants honest, not the invariants themselves.

## The discipline, in one paragraph

A green check proves nothing until the check has been watched failing. So the **teeth-prover runs
first** and plants real defects to confirm every guard can go red (`HAS_TEETH` / `OVERBROAD` /
`VACUOUS` verdicts). Exit codes everywhere: `0` clean · `1` violation · `2` UNMEASURED — and
**2 dominates 1**, because a check that did not run can hide any number of violations beneath it.
Until 2026-08-03 the leg-liveness dry-run wrote fabricated ALIVE state and reported a PASS; the
staleness check could never fire. That defect is why the dry run now returns `2` and says so.

## What runs from a fresh clone (no private data needed)

| Layer | Command | Expectation |
|---|---|---|
| Teeth-prover | `python3 guard/teeth_prover.py` | 10 planted mutations; every guard proves it can fail |
| Contract agreement | `python3 guard/contract_agreement.py` | all four vocabulary surfaces agree (validator · addendum · rollup · preamble) |
| Guard unit gates | `pytest guard/tests/ -q` | 165 tests, hermetic |
| Full runner | `guard/run_guards.sh` | the above in order; leg-liveness dry-run returns `2 = UNMEASURED` by design |

## What fail-closes without private data — deliberately

`mutation_harness.py` sandboxes the code under test, applies surgical mutations, and asserts the
paired guard goes red (`KILLED` / `SURVIVED` / `ABSTAINED` — a guard that declined to assert
anything did not catch the bug). Its baseline check pins measured corpus sizes; without the private
measurement corpus it **aborts before mutating anything** — a harness that cannot reproduce the
clean baseline refuses to certify mutations against it. That refusal is the integrity rule, not a
missing feature. A synthetic public corpus is planned.

## Provenance note

During export, this directory's own gates caught the exporter twice: a sanitization pass made the
contract surfaces cwd-relative and the `isabs()` unit gate refused it; the mutation harness refused
its baseline in the corpus-less tree. Guards that police their own maintainers are the point.
