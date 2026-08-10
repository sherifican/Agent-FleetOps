---
name: curation-audit
description: Run ONE skill/memory self-curation pass. The orchestrator invokes this when a curation trigger fires OR a new session drains a pending trigger. Reviews what was learned recently and proposes concrete skill/memory CREATE/UPDATE diffs — does NOT apply them; the orchestrator gates (approve/revise/reject), then applies and commits. Dedups against a ledger so nothing is re-proposed.
---

# Curation Audit — one pass

Goal: turn what was learned in recent work into **proposed** skill/memory changes, with a human (or orchestrator) gate before anything is written. You are the legwork layer; the orchestrator is the judgment layer.

## Context boundary (read this first)
A spawned sub-agent does **not** automatically see the live conversation. So the orchestrator passes you a **RECENT-ACTIVITY DIGEST** (what was done / learned / corrected / decided since the last pass) in your prompt. You also READ the durable sources below. Work from the digest + durable artifacts; if the digest is thin, say so rather than inventing learnings. (A fork-type sub-agent that inherits context is a future optimization; until then, the digest is the channel.)

## Sources to read
- The orchestrator's **RECENT-ACTIVITY DIGEST** (in your prompt) — primary signal.
- The **curation ledger** (e.g. `<curation-dir>/CURATION_LEDGER.md`) — **dedup**: never re-propose a settled item; never re-raise a rejected one (keep a rejects-review file and check it too).
- Current skills: `<skills-dir>/*/SKILL.md` — update an existing skill rather than duplicate.
- Current memories/notes: your agent's memory store + its index — same.

## Procedure
1. **Extract learnings** from the digest: new facts, corrected mistakes, validated/falsified approaches, new tools/decisions, discovered-stale info. Each must be durable (matters beyond this session) — skip the conversation-only.
   - **Also extract interaction / working-style preference signals** about the *operator*: patterns in how they react to the assistant's behavior, and preferences they demonstrate implicitly across turns rather than state outright — formatting, level of proactivity, how questions are posed, how much suggestion vs open-ended, verbosity, when they want delegation vs a direct answer. These map to `feedback` memories (with **Why:** + **How to apply:**). *Worked example:* the operator reacted well when decision-questions were each presented **with a concrete recommendation + a short rationale** (vs bare questions) — capture that as a durable default. Notice the subtle behavioral signal, not just hard facts.
   - **Guardrail (inferred-from-behavior):** these are OBSERVED, not stated — phrase the memory as a **default the operator can override**, and do NOT over-fit to a single instance. One occurrence = a weak signal; note the confidence (e.g. "moderate — one explicit endorsement") and strengthen it as the pattern recurs.
2. **Map each to an action**, following the existing disciplines:
   - **Memory** (one fact per file): correct type frontmatter (e.g. `user|feedback|project|reference`) + **lifecycle metadata** (`updated:<date>` + `status:` — e.g. `current`/`superseded` for reference/feedback/user, `active`/`done`/`parked`/`superseded` for project; add `superseded_by:<slug>` / `re-triage:<date>` when applicable). **Type-specific required schema:** `feedback` gets **Why:** + **How to apply:**; `project` gets **Goal / Current state / Next trigger / Why** (How-to-apply optional, for projects that ship a reusable artifact). If you have a hygiene linter for the memory store, it should flag missing schema fields. Link related entries, and add the one-line entry to the correct index — reserve any always-loaded hot index for guardrails/nav/pre-flight only. UPDATE an existing file if one covers the topic; don't duplicate. **★ ON ANY MATERIAL BODY EDIT: bump `updated` AND re-check the file's index hook + frontmatter `description` still match the body** — a corrected body under a stale hook is the #1 drift source.
   - **Single-owner rule:** current policy / routing / a reusable procedure has ONE canonical owner (a skill or the one canonical memory); every OTHER mention is a POINTER + a dated-evidence marker, never a copy — copies diverge into "which page is right?".
   - **Skill**: create/update a `SKILL.md` only for a reusable *procedure*; don't make a skill out of a one-off fact (that's a memory).
3. **Dedup hard** against the ledger + existing skills/memories. If it's already captured (e.g. this session already wrote it), DROP it. Cite what it matches.
4. **Separate two buckets:**
   - **Mechanical hygiene** (safe to auto-apply): fix a broken cross-link, a stale path (e.g. post-reorg), a dead index line, an obvious dupe. Mark `auto-ok`.
   - **Substantive** (must go through the gate): any new/changed *content* or claim.
5. **Output proposals** — do NOT edit any skill/memory file. Return a structured list:
   ```
   PROPOSAL <k>
   - kind: memory-create | memory-update | skill-create | skill-update | hygiene
   - target: <path>
   - what: <one line>
   - rationale: <why it's durable + worth it>
   - dedup: <what you checked; why it's not already covered>
   - bucket: substantive | auto-ok
   - diff: <the concrete content/change, ready to apply>
   ```
6. **Honesty:** a pass whose honest result is "nothing new worth capturing" is valid and correct — return zero proposals, don't pad. Surface uncertainty. Do not claim you read the live conversation if you only had the digest.

## After you return
The orchestrator gates each proposal (approve / revise / reject), logs rejects to the rejects-review file (calibration loop), applies approved ones, commits to version control (reversibility), records the pass in the ledger, and announces. You do none of the writing — you propose.

## ★ RELAY CHECK — a corrected claim can have already left the building
**Whenever a pass CORRECTS or RETRACTS an assertion, ask: was that assertion ever RELAYED, and to whom?**
Fixing the local record while a peer keeps acting on the old version is the sent-then-diverged
failure applied to a CLAIM instead of a FILE — and unlike a file, there is no content hash for
*"the thing I told you is still what I believe."* The orchestrator owns this step; the auditor
cannot see outbound traffic. Both of these were committed in one day on the reference setup, which
is why it is written down:
- *"exit codes 0/1/2 with 2 dominating are used machine-wide"* — relayed to a peer agent on another
  machine, then measured: it was ONE roll-up in ONE entry point. Corrected in memory the same pass.
- *"we have hit this fidelity-claim class three times"* — relayed as the EVIDENCE for naming a
  class, then found to be two (the third was a false SCOPE claim, a different defect). The peer was
  adopting on it.
**How to apply:** for each applied correction, grep the outbound channel (whatever directory or
queue carries messages to peers) for the claim; if it was sent, a short correction goes back **in
the same pass**, not "next time we write". A correction that lands only where you can see it is not
a correction.
