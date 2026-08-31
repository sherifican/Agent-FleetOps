Hand this file to your AI agent with: “adapt this to my project.”

# Research-team protocol

Research acceptance is an inspectable artifact chain, not a confident answer. This protocol names the
roles, artifacts, and boundaries an adopter must adapt to its own project.

## Roles and contracts

| Role | Input → output | Non-negotiable contract |
|---|---|---|
| Orchestrator | question and constraints → two separately named briefs | The second brief contains no first-leg report, premise, or derived answer. |
| Research leg | assigned brief and sources → retained RESULT | Cite retrieved evidence; state limits; do not treat agreement as verification. |
| Reconciler | independent RESULTs → one FINAL with dissent retained | Reconcile only supported overlap; route splits to ground truth or HOLD. |
| Independent verifier | FINAL and evidence pack → claim verdicts | The verifier is different from the producer and may block publication. |
| Actionability judge | verified claims → closed-vocabulary actions | Judgment remains accountable to the project owner. |

Suggested receipt fields are dispatch id, leg id, model/lane, timestamps, premise hash or explicit
absence, source URLs or evidence-pack hash, artifact path/hash, verifier verdict, and failure class.

## Independence and artifacts

Run at least two evidence legs from separate briefs. Do not show the second leg the first RESULT, its
notes, or the orchestrator’s preferred conclusion. Retain each RESULT even when the worker exits
nonzero, inspect the artifact, then classify it as absent, partial, invalid, or acceptable. One FINAL
is a reconciled artifact with provenance and dissent—not proof that the conclusion is true. Lint
each outgoing brief with `guard/brief_scan.py` before dispatch and rewrite a flagged brief; a
clean scan is only as wide as the scanner's pattern list, so the structural independence above
remains mandatory either way.

For video research, keep the six bucket names—`Flagship-App`, `Local-Models`, `Fleet-Ops`,
`Tooling-Infra`, `Research-Pipeline`, and `Memory`—lockstep in `ACTIONABLE_ADDENDUM.md`,
`check_leg_contract.py`, `actionable_rollup.py`, and `stage_video_research.py` whenever an adopter
renames them.

## Failure handling

Classify a failed leg from its retained artifact rather than its exit code alone. A missing artifact may
be retried under a bounded policy; a partial artifact needs verification; an invalid artifact stops the
lane; a capacity or premise failure requires a changed brief or route, not blind repetition. If the
verifier cannot establish a claim, record HOLD or an equivalent project status rather than publishing
consensus as evidence.

## Security and delivery boundaries

| Defense | Component | Adopter proof it can fail | Invocation boundary |
|---|---|---|---|
| Prompt honesty discipline | `templates/honesty-prepend.md.template` | an agent reports a limitation in a fixture | Prompt discipline, not a guarantee. |
| Untrusted-content handling | `guard/fetch_gate.py` | blocked input is withheld and clean input is enveloped | Before model consumption of fetched text; requires a working detector. |
| Sandbox pattern | `templates/sandboxed-dispatch.sh.template` | a prohibited write is denied in an adopter test | Template only; not automatic for arbitrary dispatches. |
| Output retention | `templates/dispatch-wrapper.sh.template` | empty output or nonzero exit with an artifact | Wrapper records and marks artifacts only. |
| Result contract | `check_leg_contract.py` | relevance or action vocabulary violation | Run after RESULT production; not all-artifact correctness. |
| Atomic replacement | `guard/artifact_txn.py` | staged validator or commit failure | Separate transaction component; wrapper does not invoke it. |
| Vocabulary drift | `guard/contract_agreement.py` | one contract surface diverges | Pre-dispatch/release guard; not source verification. |
| Guard teeth | `guard/teeth_prover.py` | planted defect makes a covered guard red | Guard-suite proof, not research-source verification. |
| Own-leg-as-adversary | retained artifact plus independent verification | report contradicts artifact or another leg | Operating pattern, not an enforced guard. |
| Correlated-error gate | `guard/reconcile_gate.py` | an all-agree record with an unverified shared premise is refused ACT | Runs on the reconcile record before action; not source verification. |
| Hypothesis-leak tripwire | `guard/brief_scan.py` | a brief stating the expected answer is flagged | Pre-dispatch lint; catches the explicit leak only, never proves independence. |

The associated skills are failure-mode procedures: `research-dispatch` addresses premise leakage and
weak retrieval; `dual-model-reconciliation` addresses consensus laundering; `research-verification`
addresses unverified claims; `research-workflow` addresses skipped stages; `decomposed-local-research`
addresses bounded-context risk; `actionability-pass` addresses non-actionable synthesis; and
`eval-integrity` addresses invalid evaluation signals. Copying a skill does not invoke it.
