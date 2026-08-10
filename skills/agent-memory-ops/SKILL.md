---
name: agent-memory-ops
description: Maintain durable agent rules separately from episodic context and keep a compact memory index.
license: MIT
---
# Agent Memory Operations

## Memory tiers
- **Durable tier:** standing rules, decisions, preferences, and non-obvious facts that must survive sessions. This is the authoritative tier.
- **Episodic tier:** temporary observations, session signals, and supporting evidence. It is not the place for binding rules.

## Rules
1. Put a durable rule only in the durable tier; never rely on an episodic note to carry a binding instruction.
2. Before creating a memory, search for an existing entry and update it when it covers the same subject.
3. Keep one durable fact per entry, with a stable identifier, a short retrieval-oriented description, type metadata, and application guidance where useful.
4. Keep the memory index as pointers only. Put substantive content in the individual entry.
5. Do not store facts already reliably represented by source control or the repository unless a cross-session decision needs explanation.

## Pitfalls
- Duplicating a rule with slightly different wording.
- Putting full memory content in an index file.
- Saving transient conversation detail that will create noisy retrieval.

## Verification checklist
- [ ] The information was classified as durable or episodic.
- [ ] Existing coverage was checked.
- [ ] Durable entries have required metadata and a concise index pointer.
