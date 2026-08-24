Hand this file to your AI agent with: “adapt this to my project.”

# Rigor spectrum

Use the same falsifiability rule at every scale: a check that cannot fail provides no evidence. The
difference is the number of rungs justified by consequence, reversibility, and failure history.

## Choose a project class

Ask three questions before copying an inventory:

1. Could a bad change affect users, self-update a system, or alter a long-lived corpus?
2. Is the failure hard to reverse or expensive to detect after release?
3. Does this project have a measured history of high-cost regressions or cross-surface drift?

If every answer is no, start with the pattern-library rung. If any answer is yes, keep the same base
and add only the higher-rigor controls that answer the specific risk.

| Pattern-library / reusable workflow | User-facing application / updater / corpus |
|---|---|
| Contract agreement, a few teeth-proven checks, derive-don’t-pin, and refusal when evidence is absent. | The same base, plus release rehearsals, broader invariant coverage, rollback exercises, and cross-surface drift checks where the product needs them. |
| Add a narrowly scoped mutation case after a named incident. | Fund a larger ladder because the blast radius justifies its maintenance cost. |
| Acceptance: prove the guard can fail; do not simulate an unshipped release system. | Acceptance: rehearse the release path and demonstrate the relevant failure controls. |

## Copy these four, then stop

1. Retain the artifact and inspect it rather than trusting a worker’s report.
2. Prove at least one guard can turn red before trusting a green result.
3. Keep shared vocabulary in agreement checks when it has more than one surface.
4. Mark missing evidence `UNMEASURED` or unknown rather than laundering it into success.

Stop after these four unless a named risk, an incident, or one of the three questions above requires a
new rung. More controls without a failure model create maintenance surface, not rigor.

### The high-rigor reference inventory — INBOUND, adapted from a production app project; pending

This section is intentionally a stub. A production-app inventory will be adapted into this repository
only after its controls, invocation boundaries, and proof obligations can be described without claiming
that they already ship here.
