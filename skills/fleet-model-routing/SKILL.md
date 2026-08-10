---
name: fleet-model-routing
description: Route work to the lowest-cost model role that meets capability, safety, and step-budget needs.
license: MIT
---
# Fleet Model Routing

## Decision recipe
1. Estimate task complexity, context size, external-research need, sensitivity, and sequential tool-call depth.
2. Select the least expensive available role that clears both the capability and reliability requirements.
3. For a task beyond a worker's tool or context budget, decompose it into bounded pieces or escalate one rung; do not retry-loop blindly.
4. Reassess after a failure using the actual failure output.

## Generic routing ladder
| Task shape | Suggested role |
|---|---|
| Routine, tightly scoped, few tools | fast local worker |
| Ambiguous local reasoning or debugging | reasoning-enabled local worker |
| Deep dependent tool chain | coherent agentic local worker with context management |
| Large context, source discovery, or contested research | capable frontier research worker, then reconciliation |
| Work no available role can reliably perform | authorized human or premium escalation |

## Safeguards
- Use an independent model or evidence gate for consequential claims.
- Route sensitive topics only to workers suitable for them under the deployment's documented policy and capability limits.
- Measure actual step budgets on the reference setup; treat such measurements as local observations, not universal model guarantees.
- Keep validation separate from evaluation answer keys. A validator transfers to new work only when it checks a real task property.

## Pitfalls
- Choosing a cheap worker that cannot meet the required depth.
- Confusing a benchmark-specific validator with real-world verification.
- Escalating cost before trying a safe decomposition.

## Verification checklist
- [ ] Capability and step budget were assessed.
- [ ] Sensitive-content restrictions were applied.
- [ ] Failure handling chose decomposition or measured escalation.
