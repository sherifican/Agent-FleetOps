---
name: dual-model-reconciliation
description: >
  Merge INDEPENDENT model research reports (2 or 3 backbones) run on the SAME prompt
  into ONE finalized report — with per-finding SOURCE + MODEL attribution and a
  dedicated reconciliation section that surfaces where the models AGREE, where they
  SPLIT, and resolves every split to ground truth. The reconciliation IS the reliability
  layer: it catches per-model errors (wrong-ID citations, over-hedged verdicts,
  skipped/missed sources) that no single model catches alone. Use after all backbone
  models return their individual reports for the same task. Supports dual-model (2)
  and tri-model (3) runs — see references/tri-model-dispatch.md for the 3-way pattern.
---

# Dual-Model Research Reconciliation

## Why this exists

Two (or three) models on the same prompt are **complementary, not redundant** — each
catches what the other misses (observed in dual runs: one model found a launch-date
GitHub timestamp + an independent reviewer the other missed; the other fetched the
official source + wrote a sharper relevance analysis; and reconciliation caught a
wrong-item-ID citation that *neither* model self-flagged). In tri-model runs the
signal is even stronger: the third backbone introduces a tiebreaker on splits and
a fresh coverage angle. Running all backbones and reconciling produces a more
reliable finalized report than any single model alone.

The governing principle: **the human coordinator must never act on a claim that is
actually a single-model artifact, a vendor self-report dressed as fact, or an
unresolved disagreement.** Every finding in the finalized report carries its source,
which model(s) produced it, and a clear act/provisional/hold status.

## When to use

After ALL backbone models return their individual reports for the same task.
- **Dual runs** (2 backbones — research leg A and a second research model): Report A + Report B → FINAL.
- **Tri runs** (3 backbones — legs A, B, plus a third research model): Report A + B + C → FINAL.
  Per-leg suffixes (`_A` / `_B` / `_C`) keep legs distinct.
  Weighting per §"Inputs": unvalidated backbones get down-weighted until they prove out.

## Inputs

- **Dual:** Report A (model 1) and Report B (model 2), each in the standard format
  expected by the dispatch, each stating its own backbone model on the first line.
- **Tri:** Reports A, B, and C — same format. When one backbone is unvalidated for
  research quality (common when a new model is being auditioned), explicitly
  **weight the other two higher** in the reconciliation; the tri-run then doubles
  as that model's research-quality audit. Document the weighting decision at the top
  of the FINAL.

## Finalized report format (the merge output)

- **§1 — Transcript / Source** (merged; normally identical across models — note any divergence).
- **§2 — Verification** (merged per-claim table). Columns:
  `| Claim | Final verdict | Evidence (source URL) | Source confidence | Found by |`
  where **Found by** names any leg subset — `A` / `B` / `C` / `A+B` / `A+C` / `B+C` / `all`
  (the column must be able to attribute EVERY combination; a two-value vocabulary cannot
  attribute three legs). Every row shows where the info came from AND which model(s) produced it.
- **§3 — Reconciliation** (the reliability layer — procedure below). *(Recommended order: put
  reconciliation here as §3, relevance as §4 — verification → reconciliation → "so what for this stack"
  reads cleanest, with the value payoff last.)*
- **§4 — Relevance & Value to This Stack** (merged).

## §3 Reconciliation procedure (dual and tri)

1. **ALIGN** — match each claim across all reports.
2. **CLASSIFY each claim:**
   - ✅ **AGREE / UNANIMOUS** — all backbones, same verdict. If all cite *independent*
     sources → 🟢 highest confidence. **Agreement is still not verification** — it is exactly
     what a shared unchecked premise produces: correlated error. ACT additionally requires the
     shared premise to pass a verifier that CAN fail, wherever one can exist
     (`guard/reconcile_gate.py` refuses an ACT whose shared premises are unverified).
   - ⚠️ **SPLIT** — different verdicts or conflicting evidence. **RESOLVE to ground
     truth:** do an independent lookup, pick the correct verdict, record the trail
     AND which model(s) were right. **Never** let a split silently default to one
     model's answer without this check.
   - 🔵 **SINGLE** — only one model found/verified it → 🟡 medium (corroborated by one;
     name which).
   - **Tri-only: 🔶 MAJORITY** — 2 of 3 agree, 1 dissents. Resolve the dissent with an
     independent lookup; name the dissenter. Often stronger than SINGLE but weaker than
     UNANIMOUS; treat as 🟢 once resolved, 🟡 if resolution is ambiguous.

3. **CONFIDENCE MAP** (at-a-glance for the coordinator):
   - 🟢 **ACT** — all agree + independent sources + every shared premise verified (where no
     verifier can exist, the record says so explicitly), OR a resolved majority under the same
     premise rule.
   - 🟡 **PROVISIONAL** — single-model, vendor self-report, or partial.
   - 🔴 **HOLD** — unresolved split or unsubstantiated → do NOT act; explicitly flagged
     for the human.

4. **PER-MODEL RELIABILITY LOG** — for each resolved split, record who was right. Append
   to a running scorecard (e.g. `_backbone_reliability_log.md` in your research-output
   directory) so over time you learn
   which model to trust on what (launch dates, independent reviews, official-source
   fetching, verdict calibration, …). In tri-runs, the third model often breaks ties
   — log when that happens.

5. **BLIND-SPOT SIGNALS** — note systematic per-model failure modes seen this run (e.g.
   over-hedged verdicts / won't say CONTRADICTED; skipped a fetchable source; over-rated
   a single anecdotal source as HIGH). These feed the skill-improvement cycle (prompt
   fixes, skill refinements). In tri-runs, compare the UNVALIDATED backbone's blind spots
   against the proven ones — that's the research-quality audit payoff.

## "More info is better" — keep both sources
When the models pulled from *different* sources for the same claim, keep ALL of
them in the evidence cell — do not drop one. Cross-confirming sources raise
confidence; a second source that disagrees is itself a finding worth surfacing.
In tri-runs, if all 3 cite different sources for the same fact, that fact is
either highly contested or poorly documented — flag it explicitly.

## Hard rules
- **Self-citation:** any 3rd-lookup source used to resolve a split must be one you actually
  retrieved this run (real content, not a 404).
- **Anti-fabrication** throughout — never invent a source, verdict, or "which model said it."
- A SPLIT is not resolved until a ground-truth source settles it OR it's explicitly marked
  🔴 HOLD (unresolved) for the human.
- **Record TWO verdicts per resolved claim — the CONCLUSION verdict and the MECHANISM verdict**
  (is the reason sound?), separately. A true conclusion protects a false reason from scrutiny;
  keeping them apart stops a right-answer-wrong-mechanism claim from banking credibility for
  its premise (`guard/reconcile_gate.py` requires both on any acted claim).
- **Never hand a leg the hypothesis — ask it to REFUTE.** A brief that leaks the expected
  answer converts N independent legs into N echoes. Lint outgoing briefs with
  `guard/brief_scan.py`: a tripwire for the explicit leak — a clean scan is only as wide as its
  pattern list, so the structural isolation (separate briefs, no first-leg output in the
  second) stays mandatory. The patterns match leak SHAPES, not keywords: the bare word
  "hypothesis" appears in every instruction telling you not to leak one — including this
  bullet — so a scanner keyed to it flags the rule as a violation of itself. A match is also
  suppressed when a negation precedes it on the line, or when it sits inside a quoted or
  inline-code span, because a brief that FORBIDS a phrasing has to be able to write it down.
  That last suppression is an evasion route, and naming it is the honest form of a tripwire.

## Who runs it
Currently the **coordinator (the orchestrating agent with full system context)** — reconciliation
is reliability-critical and requires ground-truth verification + judgment. As helper-agent
skills mature, a lighter automation agent can produce a first-pass
merge that the coordinator audits (the slow-loop audit). The VISUAL edition of the finalized
report is then generated by that downstream rendering agent.

## Related
- Your per-leg retrieval convention (how each model should retrieve from + persist on sources).
- The per-model report format (§1 transcript / §2 verification / §3 analysis) that this merges.

## References
- `references/tri-model-dispatch.md` — tri-model run convention: 3 backbone × suffix,
  weighting of unvalidated backbones, file-naming, reconciliation-specific rules for
  3-way splits and the research-quality audit side-effect. Read this BEFORE running a
  tri-model reconciliation.
