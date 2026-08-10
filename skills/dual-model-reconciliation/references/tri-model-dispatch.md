# Tri-Backbone Dispatch Convention

The 3-way research dispatch + reconciliation convention for a multi-model research setup.
Each backbone gets the IDENTICAL prompt — that's what makes reconciliation meaningful.

## Dispatch shape

| Backbone                | Interface                                                                 | Leg suffix |
|-------------------------|--------------------------------------------------------------------------|------------|
| Research leg A          | your agent-runner CLI, one profile per model (e.g. `agent -p legA -z "<prompt>"`) | `_A`       |
| A second research model (paid API) | same runner, different profile — verify-winner                   | `_B`       |
| A third research model  | an interactive chat session you drive manually — write `DISPATCH_<topic>_<date>.md` with a HARD DIRECT-FETCH GATE (NOT via the runner CLI) | `_C` |

A weaker local model can add a 4th leg but is the weakest researcher — optional.

Per-backbone legs write to:
`research/RESULT_<topic>_<leg>.md`
(one file per backbone). The coordinator then produces:
`research/RECONCILED_FINAL_<topic>_<date>.md`

## Weighting of unvalidated backbones
A run may include a backbone not yet stress-tested as a researcher. Rule: weight the proven
backbones higher until the new one proves out; the run doubles as its audit.
- Proven backbones AGREE + the new one dissents → majority is default-ACT (log the dissent).
- New backbone's unique finding → SINGLE / 🟡 PROVISIONAL until independently corroborated.
- All agree → 🟢 UNANIMOUS. Document the weighting at the top of the FINAL.

## Per-backbone reliability log
Running scorecard at `research/_backbone_reliability_log.md`. Log:
- **Tiebreaker:** the 3rd backbone broke a split that would've been HOLD in a dual run.
- **Outlier on majority:** one dissented from a 2/3 consensus — after resolution, was it right (rare, high signal) or wrong (typical).
- **Audit verdict:** the unvalidated backbone's overall showing vs the proven ones.

## Reconciliation decision tree (per claim across the 3 legs)
1. All three agree → UNANIMOUS 🟢.
2. Two say X, one says Y → MAJORITY 🔶 — resolve Y by independent lookup; X wins → 🟢, Y wins → promote the dissenter + log, neither → 🔴 HOLD.
3. All three differ → SPLIT ⚠️ — independent lookup required; do NOT average/synthesize.
4. One filled, two empty → SINGLE 🔵 / 🟡 — ask why the other two missed it (confab risk on the filler).
5. Two same, one empty → majority without conflict → 🟢 after verifying the two sources are independent.

## Pitfalls
- A tri-run is NOT "dual + a tiebreaker" — final pass: did the 3rd backbone surface anything the other two missed?
- Don't skip the unvalidated-backbone audit deliverable (appendix of the FINAL).
- **The manually-driven interactive leg needs the HARD DIRECT-FETCH GATE or its specifics are confabulated** (measured on the reference setup).

## When tri over dual
Auditioning a new backbone; high-stakes decisions wanting max coverage; large-surface topics
(landscape surveys, 10+ candidates). Skip tri when the topic is narrow — dual suffices and tri
adds coordination overhead (3 legs to reconcile, not 2).
