---
name: generate-review-fix-loop
description: Improve a non-trivial code change through separate generation, review, and repair roles.
license: MIT
---
# Generate → Review → Fix Loop

## Overview
For non-trivial implementation work, use three distinct stages: a coding model drafts, an independent audit model compares the draft with the contract, and the coding model revises from that review. This reduces the chance that a single model overlooks its own edge cases; it does not replace a real test.

## Procedure
1. Give the generator the full task contract, relevant code, constraints, and expected verification.
2. Give a different reviewer the task contract and generated diff. Ask for concrete defects, missing cases, and unsupported assumptions.
3. Give the generator the review and require a revised final result. Preserve the review and final output.
4. Functionally test the final result in the repository before reporting it as verified.

## Role selection
- Use a coding-oriented local model for draft and repair.
- Use a distinct audit-oriented model for review; do not have the generator self-review as the only gate.
- Prefer a more thorough reviewer for high-risk or edge-case-dense changes.

## Pitfalls
- Shipping the draft rather than the repaired output.
- Treating review as proof of runtime behavior.
- Spending the multi-stage process on a trivial one-line edit when direct verification is cheaper.

## Verification checklist
- [ ] Generator and reviewer were separate roles.
- [ ] The repair addressed the review rather than merely restating it.
- [ ] The final revision, not an earlier draft, was tested.
