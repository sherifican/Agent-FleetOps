---
name: local-model-onboarding
description: Orient an agent to a local-first model fleet, its safety boundaries, and its operating disciplines.
license: MIT
---
# Local Model Onboarding

## Overview
Use this at the start of work in a local-first fleet. Read the deployment's own documentation for its authoritative model roster, storage locations, access controls, and escalation policy; do not rely on remembered infrastructure details.

## Standing disciplines
1. Prefer a local model when it can meet the required capability and reliability bar; escalate deliberately when it cannot.
2. Keep durable rules and decisions separate from session observations and incidental traces.
3. Honor project-level protected-code, approval, source-ownership, and secret-handling rules.
4. Verify operational actions at their destination. Evidence is stronger than a status assertion.
5. Name workers by their actual model and role in reports so results remain attributable.
6. Bound parallel work to available compute and memory capacity.

## Operating map
Document locally: inference endpoint(s), available worker roles, durable-memory location, episodic-memory location, project roots, handoff mechanism, and permitted external services. Treat the current project documentation as the source of truth when these conflict with an old note.

## Pitfalls
- Treating a historical transcript as a current rule source.
- Sending sensitive work to a provider whose policy or capability makes the result unsuitable.
- Announcing success before inspecting the resulting file, command output, or destination.

## Verification checklist
- [ ] The current deployment documentation was read.
- [ ] Local-first and escalation policies are understood.
- [ ] Protected-code and secret-handling rules are known.
- [ ] The planned handoff and verification mechanism are identified.
