---
name: research-workflow
description: Produce an evidence-aware briefing by gathering claims, independently verifying them, and synthesizing only supported conclusions.
license: MIT
---
# Research Workflow

## Overview
This workflow produces a research briefing through three gates: gather notes, independently verify their claims, and synthesize a briefing that incorporates the verification outcome. For live or discovery-heavy research, first use the research-dispatch procedure to collect current sources.

## Procedure
1. Gather notes and enumerate the claims they make.
2. Send those claims and their evidence to an independent verification role using the research-verification procedure.
3. Synthesize the briefing from the notes and verdicts. Exclude, qualify, or visibly flag claims marked contradicted, unsupported, or overstated.
4. Preserve the verification verdicts with the briefing so readers can inspect the reasoning boundary.

## Constraints
- For sensitive topics, do not describe a same-worker consistency pass as independent verification. Seek an independent suitable reviewer when one is required.
- For current facts, use retrieved evidence rather than a model's parametric knowledge.
- Escalate reliability-critical deliverables to a multi-source reconciliation process.

## Pitfalls
- Publishing raw gather notes as a verified briefing.
- Silently retaining claims rejected by the verification stage.
- Assuming a model can discover or update current sources without retrieval.

## Verification checklist
- [ ] A verification stage occurred before synthesis.
- [ ] Rejected claims were removed or visibly qualified.
- [ ] The final briefing states evidence limitations and unresolved questions.
