---
name: decomposed-local-research
description: Run bounded local-model research by separating deterministic retrieval from short language passes.
license: MIT
---
# Decomposed Local Research

## Why decompose
A long agentic loop can exceed a local model's tool-call or context budget even when it can summarize and compare evidence well. Separate deterministic retrieval from bounded language work so each model call has one focused purpose.

## Architecture
1. A deterministic retriever reads the supplied source and retrieves the selected evidence URLs into an evidence pack.
2. A bounded model pass summarizes the source and extracts checkable claims.
3. A bounded verification pass labels each claim supported, contradicted, unsupported, or overstated against the evidence pack.
4. A bounded synthesis pass writes the verdict using only the prior stages; it introduces no new facts.

## Procedure
- The orchestrator chooses the source and only load-bearing evidence URLs.
- Keep evidence packs small enough for the target context window; split or summarize source material when needed.
- If needed evidence was not retrieved, add it and rerun retrieval rather than asking the model to guess.
- Use discovery-capable research or a human review for open-ended source discovery and reliability-critical work.

## Pitfalls
- Handing a local model a monolithic fetch-and-write agent loop.
- Calling a claim verified when the evidence pack does not cover it.
- Treating a supplied-evidence workflow as web discovery.

## Verification checklist
- [ ] Evidence was selected and retrieved before claim verification.
- [ ] Unsupported claims are labeled rather than filled in.
- [ ] The final synthesis is traceable to the evidence pack.
