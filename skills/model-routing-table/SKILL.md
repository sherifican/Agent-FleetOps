---
name: model-routing-table
description: Use BEFORE dispatching any task to a model in a multi-model fleet (local + cloud). Picks the cheapest model that can actually do the task from a LIVING, evidence-cited routing table, applies guardrails that OVERRIDE the table, respects measured step-budgets (decompose or route up), and evolves as new evals land. For an orchestrator routing work down to a fleet of models.
---

# Model Routing Table — the fleet router

The orchestrator's job is to dispatch each task to the **cheapest model that can actually do it**, after guardrails. This skill consolidates scattered routing evidence into one decision procedure + a LIVING routing table. Consult it before any non-trivial dispatch.

## Decision procedure
0a. **PROJECT GATE** — if a project is pinned to a lane set by operator policy (e.g. "this project's margins are thin; only the cloud legs, no local first pass"), that gate OVERRIDES everything below. Encode such gates here, as step 0, not as a footnote in a table row — a subtly-wrong harness on a thin-margin project yields plausible numbers that gate real decisions.
0. **DELEGATE-FIRST DEFAULT** — even MINOR/quick task-steps go to a local model FIRST, then evaluate + revise/correct. "Fast to do myself" is NOT a license to skip delegation. Orchestrator-direct is reserved for true orchestration/governance (sequencing, writing the dispatch brief itself, verifying reports, integration/wiring, memory/skill writes, operator comms); everything else — code, audit, tests, boilerplate, grep-heavy digging, drafts, condensing, routine analysis — dispatch, don't hand-do. The lane backstops to the orchestrator only when it truncates/fails an integration-heavy step.
1. **Classify the task's dimensions** — what is it really? (code-gen / code-audit / agentic-coding / tool-call-chain / web-research / convergent-analysis / reasoning-heavy / long-session / visual / simple-cheap). A task can be multi-dimension — split it.
   - *Optional ADVISORY assist:* a small classifier behind a deterministic policy pre-pass can emit a route LABEL cheaply. **Contract:** if the deterministic pre-pass fires, treat it as a TRUSTED classification (on the reference setup it verified 100% precision + 100% gated-op ESCALATE recall on a fresh retest — protected-op / destructive / outward-bound all hard-caught); otherwise the small model's output is a SUGGESTION only and **you own the final route**. Use it as a fast sanity-check / gated-op tripwire, never as a binding decision.
2. **Apply the GUARDRAILS** (below) — they OVERRIDE the table (e.g. a content-policy constraint forces away from a whole model family regardless of capability rank).
3. **Look up the routing table** → pick the model + note the **measured step-budget**; if the task exceeds the budget, DECOMPOSE or route up.
4. **PRE-FLIGHT — check the target capability is UP** before dispatching, ESPECIALLY to an on-demand / network endpoint (a sidecar server, a vision endpoint, a web gateway) or a cloud leg. Keep a small READ-ONLY health-check command that reports each capability `available:true/false`; if the target reads false → route to the **Alt** in the table or surface the gap. An on-demand/cold entry (a sidecar asleep) is **NOT** a blocker — it wakes on use. Don't run the check before every trivial LOCAL dispatch; use it at **session start**, before **network / on-demand / cloud** routing, and as the **FIRST diagnostic when a dispatch ERRORS** ("is the capability even up?" before deeper debugging).
5. **Cheapest-capable wins** — don't send to a 30B-class model what a small instruct model handles; reserve premium legs for one-shot polished artifacts.
6. **Cloud legs DELEGATE DOWN** — a cloud orchestrator/worker decomposes and hands local-sized sub-tasks to local models; the cloud keeps only the hard/long parts. Paid quota is for escalation, not routine legwork.

## ROUTING TABLE (living — each row cites its evidence + date)

These are GENERIC EXAMPLE ROWS showing the shape — replace with your own roster, measured on your setup. Note the conventions: each row cites its evidence + a date; a row can carry a ⚠ warning; a negative result stays in the table (marked NOT adopted) so the failure isn't re-tried blindly; superseded rows get marked, not silently deleted.

| Task dimension | Primary | Alt / notes | Evidence |
|---|---|---|---|
| **Code GENERATION** (write new) | a mid-tier local coding model | best generator in a head-to-head on real project tasks. **Gotcha:** agentic file-editing failed until the model was dispatched under a clean local ALIAS — the raw model-ID string defeated the agent framework's tool-capability detection (0/2 with the raw name, autonomous read+edit with the alias). ALWAYS dispatch via the alias | your coding-lane eval + date |
| **Code AUDIT / review leg** | the best model on the FASTEST capable GPU — re-derive the pick when hardware or roster changes; never pin a model name as standing policy | thinking stays ON for audit passes; disabling it is the measured FALLBACK, not a preference — only for a run that fails to terminate (rerun, tagged as the fallback) or for a stated security reason, and a termination guard (a token budget or a bounded prompt) is what keeps that fallback rare. Fallback leg = the next-fastest competent model available (on a slower or smaller device where the fleet is tiered); a larger model that trades throughput for capacity only for a stated reason. Example profile (an example WITH its command, never the default): a dense 27B within ~6% on throughput of a 25.2B-parameter MoE (tagged `26b`) on the same device — 58.8 vs 62.2 tok/s, the dense model the slower of the two and chosen for audit quality, not speed, same audit prompt, timed with the serving runtime's per-run stats (e.g. `ollama run <tag> --verbose`). Staleness arm: `templates/roster-check.sh.template` fails when the table names a model the live roster no longer serves | your audit-lane head-to-head + date |
| **Tool-call-heavy chains** | a mid-tier local MoE model | perfect at every chain length tested and **6–9× faster than the sidecar-served alternative at equal accuracy** (measured on the reference setup). ⚠ **a small local instruct model is NOT a safe unassisted chain-driver** — deterministically wrong total at 12 calls (3/3 runs); its "beats every larger model" result was WITH a harness retry/validation layer | your head-to-head + date |
| **Reasoning-heavy / hard delegated work** | a cloud frontier model | **escalation leg, NOT default** — local first to conserve paid quota; decompose and escalate only the HARDEST parts. Complex+long+GPU-free has no single local answer → escalate the hard parts | your eval + date |
| **Decomposed-local research synthesis** | ⚠ a small local model — **NOT adopted** | transcript-only trial clean (5/5) but the LIVE-evidence trial FAILED (confabulated fake sources when asked to verify raw fetched JSON). Row stays to record the negative result + the retry precondition (diagnose the evidence-format failure before re-trying) | your trial notes + date |

## Serving path is a routing fact — measured example

**Why this has its own section:** a table cell once read `(:8090, best tool-caller)` and was misread as "the main model runtime serves this model on port 8090". It did not — a separate sidecar server did. That misreading justified an entire bake-off phase built on a false premise. The serving path is a routing fact, not a footnote.

Tool-call reliability for one model, by serving path (4 cases, temp 0, measured on the reference setup):

| arm | tool-call rate |
|---|---|
| sidecar server with the correct chat-template flag — the PRODUCTION path | 4/4 |
| main runtime, current version | 3/4 → 4/5 on seed-repeat |
| main runtime, older version | 4/4 |

**Three conclusions, all measured:**
1. **The sidecar remained the right default** — perfect score, and it gives explicit GPU-memory control that the main runtime's auto-eviction does not.
2. **A runtime changelog entry ("tool calls silently dropped at end of generation", fixed) did NOT bind this deployment.** The production path never used that parser, and the *old* runtime scored perfectly anyway. Do not cite a changelog entry as a reason to re-open a model's ranking — test it on YOUR path.
3. **Re-check whether a workaround is still mandatory.** The main runtime later learned to parse the calls (4/5), so the sidecar was no longer strictly *mandatory* — just still the better-measured path.

**Known quirk (both runtimes, not a bug to chase):** on the *simplest* possible tool prompt this model sometimes narrates instead of calling — *"I'll get the current weather for you using the available tools"* — 1 of 5 seeds. Prompt around it (name the tool and demand the call) rather than treating it as a regression.

## Tool-call chain head-to-head — measured example

**Task:** sum a vault — `get_manifest()` → `get_chunk(id)`×N → `submit_total(total)`. Values are derived so the total **cannot be guessed** without reading every chunk. Ground truth known ⇒ fully deterministic scoring, no judge. Raw API per each model's production serving path, **no harness optimizations** (a retry/validation harness layer takes the small model "from worst-case to every task full" — routing through it measures the HARNESS, not the model).

Results (measured on the reference setup; your numbers will differ):

| model (role) | n=2 | n=6 | n=10 (12 calls) | wall-clock n=10 | size / path |
|---|---|---|---|---|---|
| mid-tier local MoE | ✓ | ✓ | ✓ (3/3 repeats) | 4.3 s | ~23 GB · main runtime |
| strong local dense | ✓ | ✓ | ✓ | 6.0 s | ~21 GB · main runtime |
| sidecar-served tool-caller | ✓ | ✓ | ✓ | 39.8 s | ~18 GB · sidecar |
| small local instruct | ✓ | ✓ | **✗ wrong total (3/3)** | 10.0 s | ~8 GB · main runtime |

**Findings:**
1. **Tool-call MECHANICS were solved across the board** — all four used the EXACT minimum call count with **zero malformed calls** at up to 12 calls. The step-budget worry did not materialise for any subject at this length.
2. **The real differentiator was arithmetic/state-tracking, not tool-calling.** The small model read all 10 chunks correctly and then submitted the wrong sum — a failure a pass/fail "did it call the tools right" metric would have scored as SUCCESS. Score the OUTCOME, not the call sequence (cf. [[eval-integrity]]).
3. **Accurate but expensive is a routing fact:** the sidecar model cost ~2.7 s/call vs the MoE's ~0.36 s/call, warm — plus a second process and manual memory juggling.

**Scope limits — do not over-read a narrow benchmark:** a 3-tool deterministic domain, chains ≤12 calls, N=1 per cell for two arms. The sidecar model's standing on a broad public tool-calling benchmark came from a far broader test, so the honest claim is **"its accuracy advantage does not appear on OUR chain shape and it costs 6–9× the latency"** — NOT "it is worse at tool-calling."

## GUARDRAILS (override the table)
- **LOCAL-FIRST, CLOUD AS SUPPORT:** the default working legs stay LOCAL — local models pull as much weight as they can (you already own the hardware; conserve paid quota). Cloud = escalation / the hard-or-long parts, never the default for routine work. Do not burn cloud budget on orchestration PLUS regular agentic legwork.
- **CONTENT/POLICY ROUTING:** some model families decline or skew certain content categories → route that lane to a family known to handle it, regardless of capability rank. *(EXCEPTION: deliberately running a model on sensitive prompts to TEST/observe its own behavior is not serving production content — the route-away rule governs production, not evaluation.)* *(NB: don't over-apply it — internal tool/dependency triage is not the sensitive content, so the route-away does not apply there.)*
- **CONFABULATION-PRONE LEG:** if a leg's documented risk is confabulating specifics / overclaiming, verify its FACTS and external-verify every deliverable — and **NEVER hand it a premise: ask "how many?" not "list all six."** Prompt construction, not an in-prompt honesty contract, is what prevents the failure.
- **STATED CONTEXT WINDOW ≠ ORCHESTRATION CEILING:** a model's advertised window is not a reliable orchestration ceiling — models degrade before their max. Keep such legs to BOUNDED slices (focused research, focused code); in any multi-leg reconcile, hand the window-limited leg a context-bounded slice, NEVER the full corpus (cf. [[dual-model-reconciliation]]).
- **VRAM / cohosting:** models too large for one GPU span both → run them SEQUENTIALLY; two SMALL models can run concurrently → one per GPU.
- **GPU AVAILABILITY (occupancy, not just fit):** before dispatching to a LOCAL model, check the GPU is actually FREE (your runtime's process list / `nvidia-smi`). If a long GPU job is running (a tune, a big eval, an image-gen batch), local dispatch is BLOCKED — QUEUE it behind the job, or route the interim to a cloud leg. The VRAM rule covers FIT; this covers OCCUPANCY.
- **CONTEXT BUDGET (doc/data-heavy coding dispatch):** a discipline/onboarding prepend plus a data/doc file plus accumulating tool output can OVERFLOW a small default context window (measured on the reference setup: peaked 16,294/16,384 tokens on a real stack test). For agentic-coding dispatch that reads a data/doc file → RAISE the context window or trim the prepend — blind front-truncation produced a template-error 400.
- **STEP-BUDGET:** curate the dispatch to the model's MEASURED step budget; when the task exceeds it, DECOMPOSE or ROUTE UP. Re-measure after head-to-heads — a budget inherited from an old eval may belong to a model that has since been reassigned.
- **TOKEN-COST = PAID LEGS ONLY:** token-spend applies to cloud legs only — LOCAL generation optimizes QUALITY + context-SPACE, never token-frugality. (Measured example: a local coder was ~2.8–4× costlier in tokens at a verbose spec format than at a code-executing charting library, and priming WIDENED the gap — but local tokens are free, so the verbose format's output-quality win still stands for local gen.)
- **EFFORT-BUMP GATE:** give each lane a STANDING effort level; a per-task bump above standing is **operator-gated — flag it (approve/reject) BEFORE applying, never self-escalate.** Escalate only on a documented trigger: security/threat-model audit · concurrency/race correctness · cross-cutting protocol or schema work · a high-stakes irreversible architecture call · an expensive human-review bar · a long unattended agentic run · or **the standing effort already failed on the merits** (not on a prompt/context gap). Do NOT escalate for routine implementation, mechanical refactors, docs, or design polish — the benefit there is UNESTABLISHED, and slower loops actively hurt agentic iteration. **Why this gate exists:** a wrapper's hardcoded flag silently overrode the config file's declared effort for every caller; a controlled head-to-head then measured the higher effort at ~2.2× cost / ~1.3× wall time with **non-monotonic test pass** (the LOWER effort passed more) — measured on the reference setup.
- **LOCAL WHOLE-FILE EDIT = VERIFY THE LINE COUNT:** a local-lane whole-file edit can **silently truncate** — on the reference setup a coding model returned a file at **428 of 683 lines, 11 functions dropped, exit 0**, no warning. The "fails safe on giant monoliths" result did NOT generalise to mid-size files. After any whole-file local edit, diff **line count + symbol inventory** against the pre-edit file and fail the dispatch on an unexplained drop; prefer read-only context + a targeted spec over whole-file rewrites past a few hundred lines.
- **SIZING = HEADROOM OVER MAXIMUM FIT:** do NOT promote a routing row for the largest model that FITS; the metric is **quality per GB with context headroom left**. Below your quantization floor is a gamble needing a measured win. Keep new-hardware routing rows UNPROMOTED until real throughput is measured on the actual box.
- **A FAILURE REPORT IS NOT A FAILURE:** a leg reporting failure / an error string / nothing is as unverified as one reporting success — **check the artifact before believing either**, and count only VERIFIED failures toward the escalation ladder. Harness denials (a headless gateway auto-denying write/exec tools) are not model failures.
- **HONESTY:** honesty-enforce dispatched work; VERIFY deliverables, never trust a self-report.
- **TEST BEFORE ADOPTING** a new backbone; match the eval instrument to the model's design.
- **CODING-DISCIPLINE PREPEND (code-edit / agentic-coding dispatch):** prepend a short standing discipline doc to local coders — read the project's doc chain → read the relevant subset (batched) → plan→act → **VERIFY-BEFORE-FINISH (never finish on red, verify once on green)** → stay lean → update the map. A/B-proven on the reference setup to cut tool calls ~20–26% + context ~40% + verify-rate → 100%. Capable cloud coding agents read project docs on their own; the prepend is for the local lane (cf. [[local-lane-build-loop]]).

## REFRESH PROTOCOL — how this skill EVOLVES (the point of it)
The routing table is LIVING; stale routing = bad dispatches. Refresh it:
- **REACTIVELY** — after ANY model eval / bakeoff / new-model trial, update the affected row(s) + bump the evidence + date.
- **PERIODICALLY** — at each **curation checkpoint** (cf. [[curation-audit]]), scan your findings ledger + recent eval notes for new model findings and reconcile the table. Piggyback on an existing checkpoint — no new hook needed.
- Keep every row's **evidence pointer current** so staleness is visible at a glance.

## Propagation
Canonical copy = this skill, held by the top orchestrator. Summarize the table + guardrails into the dispatch headers/onboarding files of the OTHER agents that delegate down, so every orchestrator routes consistently; re-propagate after each refresh. **Drift lesson:** if a wrapper prepends a different copy than the canonical one, update BOTH — the first propagation is also when you discover the headers had NO routing summary at all.

## Intra-task topology — the EDGE-ADVISOR SANDWICH (10/80/10)
The routing table picks a model PER TASK; this is the complementary INTRA-task pattern for a single hard task where intelligence changes the outcome: **put the strong model on the EDGES, the cheap one in the MIDDLE.** (1) the strong model writes the plan + success test + risks + verification rubric [first ~10%]; (2) a cheap/local worker executes the bounded middle [~80%]; (3) a strong model AUDITS the output against that plan for false confidence / missed constraints / hidden assumptions [last ~10%]. Optional **advisor consult**: mid-task the cheap executor asks the strong model ONE short bounded question instead of escalating the whole task. Pay top rates only where intelligence actually moves the needle (the plan + the review), not the mechanical middle. The last-10% audit IS the data-integrity gate the orchestrator must keep — make this the default shape for hard delegated builds.
