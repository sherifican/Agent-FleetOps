# The Silent-Clear Problem

*Engineering record · multi-agent fleet*

Two AI agents on separate machines spent a day building a shared service and kept tripping over the same defect — not in the product, but in how they knew things. Every miss had one shape. This is what happened and what came out of it.

> **Three machines · two peer agents · Seven coordination failures · Generalized — identifiers removed**

---

## Setup — the arrangement

Three machines, each with a distinct role, and two AI agents that work as peers rather than as a controller and a worker.

- **Box A** — A desktop workstation. Its resident agent, **Agent A**, runs no models locally; it dispatches work to cloud model legs through per-leg wrappers.
- **Box B** — A GPU workstation running local models. Its resident agent, **Agent B**, has both local inference and cloud legs, plus a terminal monitor for fleet activity.
- **Box C** — A headless GPU box. No resident agent. It hosts the shared service both agents were building, and serves inference to whoever asks.

The two agents coordinate by dropping files into a shared folder over SSH — notes, patches, review reports. No message bus, no shared database. **Every exchange is a file, so every exchange is diffable and revertible.**

Each agent also dispatches cloud legs for independent review: a search-capable leg, a reasoning leg, and a metered analysis pair. Legs are one-shot — they receive a brief and return an artifact. A standing rule governs them: **judge the artifact on disk, never the exit code**, because a leg can report success while writing an error message as its deliverable.

## Object — the service under construction

A cross-machine GPU scheduler: an authority that decides which job runs on which accelerator. Three slots — a fast discrete card, an integrated GPU with a large shared pool, and one slot representing a workstation that the authority can never observe directly.

Allocation weighs task priority, caller-declared urgency, model size against slot capacity, warm-model affinity, a grace period before a slot flips to a different model, explicit pinning, and a starvation boost for jobs that have waited too long. Jobs arrive over HTTP; the authority assigns or queues. A reconciler periodically observes what is actually loaded and compares that to what it believes.

**The governing principle:** an observation that failed must present as `CANNOT_CHECK` — never as "nothing is loaded." A box that cannot be seen is not an idle box. Most of the early defects were variations on breaking this rule.

## Defect — seven failures, one shape

Over a single working day the two agents produced seven coordination failures. They looked unrelated. They were not.

**The shape:** someone acted on a belief about state without re-measuring it at the moment of use. Every instance below is that sentence wearing different clothes.

| # | The belief held | What was actually true | Cost |
|---|---|---|---|
| 01 | "My inbox has nothing new for me." | Four files had been delivered and ingested, unread. | Rebuilt a fix the peer had already written and sent. |
| 02 | "I have no independent path to Box C." | It had one and used it daily. | Accepted the peer's verification instead of confirming. |
| 03 | "The peer's review came back clear." | Ten minutes stale; the review said hold. | A decision escalated to the human on bad input. |
| 04 | "This defect is reachable in production." | Reachable only through a test-only code path. | A high-severity finding that overstated the risk. |
| 05 | "This counter proves every caller is compliant." | It counted one bad value and missed two others. | Read zero while the exact failure it watched for occurred. |
| 06 | "What's in the tree is what's running." | Six revisions behind; a fail-open build served for 12.5 hours. | The worst known defect stayed live all day. |
| 07 | "My file watcher is watching." | Its baseline was set at arm time, classifying anything that arrived during a gap as already-seen. | Directly caused failure 01. |

Four of the seven belonged to one agent, three to the other. Neither was the careless one — **the pattern was structural, not a matter of diligence.**

## Method — the review loop

The scheduler went through eight revisions in a day. The loop that produced them is worth describing, because it worked — and because it developed a pathology of its own.

1. **Build a revision.** One agent implements against the agreed design.
2. **Fan out to independent legs.** Two or more cloud legs review it separately, with no knowledge of each other's findings.
3. **The peer agent reviews too** — independently, on its own copy, running its own probes rather than reading the diff.
4. **Adjudicate, don't average.** When reviewers disagree, resolve it with evidence. The tiebreaker was consistently which reviewer actually executed the case.
5. **Fix, and prove the fix can fail.** Every fix ships with a test demonstrated red before the fix and green after.

**What made findings trustworthy:** reviewers had to mark each finding `CONFIRMED` (executed, with observed values) or `SUSPECTED` (reasoned only). This mattered more than any other convention. One leg could not execute at all, honestly labelled everything `SUSPECTED`, and its report was *more* valuable than a confident one — because the labels told the reader exactly which claims to go test. Two of its three suspicions turned out real.

## Pathology — the loop ate itself

The review loop became self-sustaining. Findings produced a revision; the revision produced a review round; the round produced findings. It was genuinely good work and it had not touched production once.

> **8 revisions built · 0 deployed · 12.5 h fail-open build live · 4 review rounds on the tree**

Nobody noticed because nobody looked. Every participant reasoned about the source tree; the running process was a separate object that no check compared against. It surfaced only when one agent went to measure the live box directly and found a build predating every fix the reviews had produced.

**What caught it, and what would not have:** not a version string — those were correct and meaningless. What caught it was counting marker symbols in the deployed files and comparing to the tree. A field absent from the live health response is unfakeable; a version label in a covering note would have been believed by everyone.

The deploy that followed set the pattern for later ones: back up first so rollback is one command, copy, restart, then verify by marker-diff and fail closed — any missing marker triggers automatic rollback. The verification gate was itself tested against a known-stale tree first, to prove it could go red.

## Doctrine — the disciplines that came out of it

These are the durable outputs — more valuable than the scheduler itself, because they transfer.

1. **A guard must be reachable *and* target-correct.** "A check that cannot fail is indistinguishable from a check that passes" is true and insufficient. Mutation testing catches a guard that cannot go red. It does not catch a guard that can go red while watching the wrong predicate. Failure 05 was the second kind: the prove-it-can-fail harness passed it, and it still read zero while its target failure happened.
2. **A clear has a timestamp and an expiry.** A passing check is a measurement, not a property. Quoted twenty minutes later it becomes an assertion about the present made from a past observation — the same defect the checks exist to catch. Every pass now emits when it was measured and when it stops being valid. Corollary: a staleness window has to match how fast the subject changes — a six-hour tolerance would never have flagged the ten-minute-stale status in failure 03.
3. **Verify the running build, not the tree.** Reviewing source tells you about source. Before any deploy-or-hold decision, measure what is actually serving and diff it by markers, not labels.
4. **Unmeasured is never clean.** Verdicts are tri-state: pass, fail, or cannot-check. A dead link, a missing dependency, a leg that could not execute — each returns cannot-check, which is a finding. Silence must never render as "nothing to report."
5. **Design shared things together; build the specified thing in parallel.** Two failure modes look similar and are opposites. *Unilateral design* — one party invents a shared mechanism and the other inherits it — is drift: the peer gets decisions they never made, and by the time they see it, disagreeing costs discarding working code. *Convergent verification* — both parties independently build the same already-specified thing and compare — is a strength. It happened twice in this project and both times the implementations landed byte-identical, which was strong evidence the fix was right. The test is whether the design space was already settled.
6. **Check the shared pool before building; reconcile back into it.** Both agents rebuilt mechanisms that already existed in a shared repository neither had consulted. The rule that followed: check first, record what you searched and what you found so the next reader can tell "checked, genuine gap" from "never looked" — and push new lessons back rather than keeping a private variant.
7. **Escalate precisely.** Both agents drifted into routing engineering decisions to the human — decisions they were better placed to make, on a codebase he had the least context on. Peers should settle what is theirs and escalate only what genuinely needs the owner: things that are public-facing, irreversible, or a matter of intent rather than correctness.

## Topology — watching without routing

A question surfaced late and reframed the architecture: if Box A talks to Box C directly, does Box B's monitor see that work?

Measured answer: no — but not for the reason expected. Box B's monitor reads a receipts file. Work dispatched through a wrapper writes a receipt and appears; anything talking to Box C directly writes nothing and is invisible. **The determinant was never whether Box B sat in the path. It was whether a receipt got written.**

**The principle it settled:** no box should be a mandatory relay for another. Routing every interaction through one machine means that machine spends compute and context tracking work whether or not it is relevant. The goal instead: each box keeps a legible history, and any box can drop in on demand to read another's activity. Pull, not push.

Three options were weighed. Client-side receipts are cheapest but depend on every caller remembering — and the agent proposing it had just made a dozen calls emitting none, which settled that argument empirically. Reading the host's own service journal turned out to capture every request from every client with no caller cooperation at all. The chosen shape: the host's own log as the floor, with receipts layered on only where clean attribution is needed. Discipline is removed from the loop for basic visibility, and required only for the part that genuinely needs it.

## Open — not yet settled

- **A queue lease that reclaims the wrong resource.** A queued job's lease was given the same short expiry as a running job's. But a running job holds an accelerator — expensive, reclaim it fast — while a queued job holds only a position in line. Expiring that in seconds buys nothing and is the sole reason waiting jobs can never accrue starvation priority. Proposed fix: decouple the two, which dissolves the problem rather than managing it.
- **Concurrent callers sharing one job identity.** Two callers waiting on the same job can cancel each other, because cancellation has no concept of exclusive ownership. It only bites once multiple clients submit concurrently — which is exactly what the next integration step introduces.
- **Whether the shared pool becomes the canonical home for the collaboration conventions themselves,** rather than each machine keeping a re-pasted copy. A scope decision, and the one thing genuinely reserved for the human.

---

The scheduler is live and healthy. The two mechanical defects found in review are fixed and converged on both machines. What was actually built over the day was less a scheduler than a set of habits for two independent agents to hold a shared belief about the world without either of them quietly making it up.

*Generalized engineering record. Machine names, paths, addresses, model vendors and repository identifiers removed. Technical substance and failure detail preserved.*
