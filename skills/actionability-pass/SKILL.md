---
name: actionability-pass
description: Run when incoming external info (a dependency-watch digest, research/eval findings, a dependency update, a flagged alert) might warrant a system action — it converts DATA into a grounded DECISION. Must be run by the orchestrator (the agent with deep, comprehensive system-stack understanding); a cheap model with a static context file can PRE-FILTER candidates but cannot make the call. Grounds in SYSTEM STATE (primary) + SESSION STATE (co-equal during groundwork). Produces specific, operator-gated recommended actions — proposes, never auto-acts.
---

# Actionability Pass — turn incoming info into system decisions

Incoming external info — a dependency-watch digest, research/eval findings, a dep update, a flagged alert — is **DATA, not a decision**. This pass converts it into **what the system should DO**. It is worth nothing unless run by an agent with a **deep, comprehensive grasp of your system stack and current state** — i.e. the orchestrating agent. A cheaper model can do the COARSE pre-filter ("does this touch an area we run?") to narrow the candidate set; it CANNOT make the actionability call — it lacks the understanding. **Do not delegate the judgment itself.**

## When to run
- A watch/alert monitor drains its queue (a pre-filtered list of relevance candidates).
- After **research / eval / dependency findings** land that could imply a system change.
- Any time incoming info could warrant an action and nobody has triaged it.

## The grounding — what "deep understanding" means here
- **SYSTEM STATE (primary).** What you actually run + how it's wired + what's open/broken. Consult the LIVE sources, not memory-of-memory: your persistent notes/memory index, the system map or architecture docs, the relevant configs, and whatever standing context file the watcher pre-filter uses. Most actionability calls are decided here.
- **SESSION STATE (secondary — EXCEPT during groundwork).** The live conversation/work. Normally low-weight. **BUT when this session is building or changing the system (groundwork/setup work), session state == system state** — the thing just built isn't in the persistent docs yet, so factor it at FULL weight.

## The method
1. **Refresh the grounding.** Pull current system state from the live sources above; note what THIS session changed (the groundwork delta) that isn't yet codified.
2. **Per item, judge REAL actionability** — not keyword-matching, but: given how the stack actually works + what's open, does this require/suggest an action? Map to → new RELEASE of a dep you run = vet+update; SECURITY/credential fix in a dep you use = assess exposure+patch; a fix for an issue YOU have = review whether it informs your fix; BREAKING change = assess impact before updating; major FEATURE you'd use = consider adopting. **BATCH release-bound items:** when several candidates are each "track this PR / patch when the fix lands" and those fixes merge into a dep RELEASE, do NOT re-assess each PR against the installed version — VET THE RELEASE ONCE (does `<dep> <next-ver>` actually bundle them?). **Containment check (learned from a real vet):** a release cut BEFORE the fixes merged will NOT contain them — verify each fix merge-date vs the tag date (a freshly-tagged version often lags very-recent merges, so 0-of-N can be in it); one gated update closes much of the queue ONLY if the fixes are actually in that tag. **Decouple CVE bumps from unsafe code-tree adoption (learned from a real case):** when a release carries both security dependency bumps AND upstream code changes, and adopting the code tree is unsafe for your environment — e.g. you have a divergent local build with no upstream merge-base so a tag checkout would replace the running codebase, or there's a breaking schema migration that won't apply cleanly — do NOT take the tag as a whole; instead pull ONLY the isolated CVE-fix dependency bumps into the service's virtualenv (or patch them in place), and do NOT run an editable reinstall if it would downgrade an already-newer security pin.
3. **Make each action SPECIFIC + grounded** — name the actual file/config/component/operator-step, not "look into it." The deep understanding is what lets you write "upstream PR #NNNNN fixes `final_response` in the error path → compare against our local patch at <X>" instead of "a final_response PR appeared."
4. **Set urgency** (now / soon / fyi) and **mark operator-gated** items (installs, updates, patches, anything outward = the human operator decides + acts).
5. **PROPOSE to the operator.** The pass yields a recommendation, not an action. Surface a concise alert report; the operator gates.

## Output
`⚡ [now|soon|fyi] <item> → <specific action> (operator-gated: y/n)` per actionable item, plus a one-line "nothing else actionable in this batch" sweep so silent omissions are visible. Then propose; do not act.

## Codify, don't improvise
Run this the SAME way each time so actionability isn't ad-hoc. The coarse pre-filter only narrows the candidates; the judgment here is the orchestrator's and is **not delegable** — a judge only judges what it's grounded in, and the grounding here is the whole system stack.
