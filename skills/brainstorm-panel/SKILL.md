---
name: brainstorm-panel
description: Read BEFORE running a multi-model R&D brainstorm / open-ended design panel (human opts in). A reusable "combo team" dynamic — N models with distinct lenses independently ideate, adversarially cross-critique, reconcile via a rotation, and hand the orchestrator+operator a gated, falsifiable experiment set. This is the TEMPLATE for standing up new combo teams; swap the roster per task.
---

# brainstorm-panel — the reusable multi-model R&D panel

A **combo team** for open-ended problems where **diverse hypotheses + adversarial rigor + reconciled synthesis** beat any single model: research brainstorms, design explorations, "find the next breakthrough," build-vs-buy landscapes. **The human operator opts in** — this fans out to several models across cloud APIs + local inference and burns real tokens; don't launch it for a task one good dispatch handles.

## When to use / not
- **Use:** the solution space is wide + unknown; you want coverage (many angles) AND confidence (models argue, catch each other's re-treads/fabrications) before committing; the deliverable is a *ranked, falsifiable* plan, not code.
- **Don't:** a single-answer lookup, a bounded code edit, or anything one model dispatch resolves. Don't use it to launder consensus mush — the value is the *argument*, so keep the adversarial phase real.

## The 4-phase protocol
1. **Independent ideation (divergent, NO cross-talk).** Every model gets the SAME brief + inputs and returns its OWN ranked lead list. Independence FIRST = diverse coverage, no anchoring on the loudest model. Give the web-research seat an explicit "dig fresh, bring citations" mandate.
2. **Adversarial cross-critique.** Pool the leads; each model *attacks* them — flag re-treads of closed ground, fabrications, un-falsifiable claims, recall grabs that tank precision. This is where fakes die. Prime the panel with the KNOWN closed results (so they don't re-derive dead ends — e.g. a previously tested negative result in the problem domain).
3. **Reconcile via a multi-model rotation.** Synthesize survivors into ONE prioritized document, passing it between 2–3 different models in turn. The reconciler SURFACES disagreement, never rubber-stamps.
4. **Orchestrator gate → operator.** The orchestrating agent sanity-checks against the source material + the honesty bar, then hands the operator a **ship/no-ship recommendation**. The panel produces the sharpest experiment set; the OPERATOR decides what ships.

## The roster template (roles, not fixed models — swap per task)
Pick one model per lens from whatever you can access; a standard R&D panel:
| Lens | Example seat | Why |
|---|---|---|
| **Convergent anchor** | a strong frontier model | precise design + honesty enforcement |
| **Divergent net + web** | a frontier model with live web access | lateral ideas + fresh literature you didn't search |
| **Independent reasoner** | a third, differently-trained cloud model | another cloud angle, independent failure modes |
| **Implementability lens** | a local coding model | "can this be a clean, cheap, shippable pass? code shape?" |
| **Audit lens** | a local audit model | falsifiability, held-out rigor, catches leakage/inflation |
| **Deep-reasoning lens** | a domain-specialized local model | physics/domain hypotheses, structural priors |
- **Local models bring their LENS, not raw ideation volume** — expect the cloud models to out-ideate on open-endedness; the locals sharpen implementability/rigor/domain knowledge. That division is the point.
- **GUARDRAILS still apply:** if your roster includes models with known content restrictions, route sensitive topics to seats without them; honesty-enforce every leg; run every non-orchestrator model through a wrapper that prepends your honesty bar, never raw.

## Dispatch wiring (how each leg actually runs)
- **One brief, many legs:** write the brief ONCE (a self-contained file), dispatch the same brief to every leg with a per-model output path, collect, then reconcile. Cap concurrency to what your hardware/API rate limits sustain.
- Each leg runs through whatever CLI/API harness you have for that model, with the honesty bar prepended by the wrapper.
- **If a panel gets reused, register it as a single dispatch target** in your tooling so it's one command next time.

## The quality bar (inherit for any R&D panel)
- **Audit-to-consensus: ≥3 INDEPENDENT models must reproduce a claimed gain** (to the decimal for numeric results); the reconciler surfaces disagreement.
- **Held-out / no leakage; falsifiable up front** (metric + protocol + success threshold + KILL criteria stated before running); **small-but-real-and-clean beats big-but-fabricated.**
- **Execute the experiment set as CHEAP PHASED KILLS — cheapest-disqualifier first** (base-rate/economic feasibility → signal existence → deployment-valid non-oracle threshold), retiring an idea BEFORE building its full pipeline; watch the base-rate and oracle-selected-threshold traps. A rigorous cheap kill that relocates the ceiling is a deliverable, not a failure.
- **Label every claim evidenced-with-citation vs speculative.** Report the cost side (precision/insertion, removed-vs-lost), not just the win.
- **Prime with closed results** so the panel doesn't re-tread already-settled ground.

## Making a NEW combo team from this template
1. Write a self-contained brief (intent, inputs and where they live, constraints, the honesty bar, the deliverable shape).
2. Pick the roster (roles above; swap models to fit the domain).
3. Run the 4 phases; reconcile via the rotation; the orchestrator gates to the operator.
4. **Codify the outcome:** record what worked/failed about the panel dynamic so the next team inherits it. Reusable teams → a single dispatch-target entry in your tooling.

## Lessons (measured on the reference setup; append as they land)
- Prime-on-closed-results was essential: the operator's headline hypothesis was already a *tested dead end* — priming stops a whole team re-deriving it. Design+validate (a harness) beats design-only when the data can be moved to where the panel runs.
- **ROSTER REALITY (full 6-model run):** the **3 cloud models carried Phase-1 ideation**; **all 3 LOCAL seats failed open-ended ideation** (one hit a chat-template serving bug, one went off-task, one context-overflowed on the long inlined brief). **→ For a brainstorm panel, use LOCALS in bounded roles only** (implement a specified experiment, audit ONE claim); do NOT hand them the open ideation or a giant inlined prompt. The cloud trio IS a sufficient ideation panel.
- **HONESTY BAR HELD under autonomy:** both top leads, executed held-out, returned NULLS — zero fabricated wins. That is the deliverable working: a rigorous null + a settled diagnosis is worth more than a hyped maybe. Gate to nulls without flinching.
- **DISPATCH the execution to the ideators, not just the ideation:** strong models will implement + run the experiment held-out (real code, real numbers) when given a precise spec + the honesty bar — that's how you get *tested* leads, not just proposed ones. Then reproduce/audit for consensus.
- **OPERATIONAL:** monitoring detached dispatches is fragile — a background waiter that itself double-forks is untracked, and sleep-waiters get reaped on turn boundaries. **Wait on the dispatch process's PID** (`while kill -0 $PID; do sleep; done`) and keep the waiter-launch turn minimal. A long model run can EXIT without writing its output file — if your harness logs the model's full stream to a sidecar log, **recover the findings from that log**.
