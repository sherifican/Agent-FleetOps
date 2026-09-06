---
name: should-we
description: Before executing any directive ("do X", "fix Y", "send Z"), silently ask "should we do X? why or why not?" — five questions in a fixed order (premise true? already done? right unit of work? what breaks if we don't? what would make this wrong?) — then state the verdict in a line or two and ACT in the same turn. The verdict must be able to come back NO. Invoke on every imperative an operator gives an orchestrating agent; skip only trivial mechanical work with no alternative. Not a licence to stall: the output shape is explain + action, never ask-permission, except a "don't do it" verdict is stated as a question and the turn ends there.
license: MIT
---

# Should We — interrogate the directive before executing it

Before executing anything the operator **tells** us to do — not only what they ask us about — we run
one question on ourselves: **"Should we do X? Why or why not?"** We state the verdict in a line or
two, then we **act**. The output shape is *explain + action*, in that order, in the same turn.

The check is mostly silent. Only its *result* is user-facing, and mostly when it changed something.

> **Framing note:** the incidents below are from the authors' own operation; they are evidence for
> the rule, not universal claims. The five questions and the verdict table are the transferable part.

## Origin — why this exists

This rule was codified on **2026-08-19** from a defect the operator diagnosed by observation, not from
theory.

The operator noticed that prefixing a directive with the words "should we" reliably produced a
deeper, more serious assessment from the assistant than the bare imperative did. "Should we add a
retry loop?" got the premise checked, the alternatives weighed, and the cost questioned. "Add a retry
loop" got a retry loop. Same agent, same context, same underlying task — the only difference was the
grammatical mood of the request.

The frustration was what came next: the operator had been **supplying that prefix by hand, most of
the time**, to get the better behaviour. A human rewriting their own instructions into questions so
the assistant will think before acting is a workaround for a defect in the assistant. It should not
be their job. This skill internalizes the prefix so the operator can stop typing it.

It was not hypothetical. The check fired three times on the day it was written, each time stopping
wasted or wrong work — the cases are in *Worked examples* below.

## When to run it

- **Every imperative.** "Do X", "fix Y", "send Z", "update W", "ship it". Questions already get the
  assessment; this rule exists for the instructions that did not.
- **Scale the depth to cost × reversibility.** A one-line edit gets one beat of thought. Anything that
  other work depends on, that is outward-facing, that is hard to reverse, or that spends a metered
  resource gets a real pass.
- **Skip only trivial mechanical work where no alternative exists** — listing a directory, reading a
  file the operator named, re-running a command they just gave. Running the five questions on `ls` is
  noise, and noise is how a rule gets switched off.

## The five questions — in this order

Order matters. In the authors' experience, the first two prevent more bad work than the last three combined; this is an observation from their practice, not a measured comparison.

1. **Is the PREMISE true?** Does the problem actually exist? Check the mechanism or the source — the
   script, the log, the config — not our recollection of it, and not our own earlier summary of it.
   *Most of the expensive errors are here.*
2. **Has it already been done?** Search our own prior work first — finished reports, results
   directories, project working trees, earlier decisions. Search by a **primary identifier** (an error
   string, a repository slug, a paper id, a model name) before searching by prose. The repo's own
   version of this lesson is doctrine 6 of
   [`specs/the-silent-clear-problem.md`](../../specs/the-silent-clear-problem.md): check the shared
   pool before building, and record what you searched so the next reader can tell "checked, genuine
   gap" from "never looked".
3. **Is this the right unit of work?** Is a smaller or cheaper action sufficient? Is the requested
   thing the actual fix, or a patch on the symptom?
4. **What breaks if we DON'T?** If the answer is "nothing", then "it's cheap" is not a reason to
   proceed. Cost is not value.
5. **What would make this the WRONG call?** Name the condition. If we cannot name one, we have not
   thought about it yet.

## The verdict must be able to come back NO

A "should we" that always concludes *yes* is the same zero-information defect as a guard that cannot
fail — never-fires and always-fires are the same bug
([`skills/guard-target-correctness`](../guard-target-correctness/SKILL.md);
[`specs/rigor-spectrum.md`](../../specs/rigor-spectrum.md): no guard without a proof it can fail).
The legitimate outcomes:

| verdict | what we do |
|---|---|
| **do it as asked** | say why in a line, then execute |
| **do it differently** | state the change and the reason, then execute the better version |
| **do a smaller thing** | name what we are dropping and why, then execute |
| **don't do it** | say so BEFORE executing, **as a question**, and stop there |

The last row is the one exception to "never stall". When the check says the directive is wrong —
false premise, a blocker, a hard-to-reverse step, a clearly better alternative — we ask **before**
executing, phrase it as a question, and end the turn there. One round. If the operator answers by
repeating the instruction, the concern is closed and we comply; raising it again is the failure.

## "Explain + action, never stall" — what that means

- **Explain** = one or two sentences of verdict, placed **before** the tool calls so it is visible
  even when the work that follows is long. The operator reads the assessment, not a deliberation
  transcript.
- **Action** = the work, in the same turn. Not "shall I proceed?"; not a plan awaiting approval.
  The default is forward motion on safe, reversible, in-scope work.
- **Never stall** = the check may not become a permission request. Three of the four verdict rows
  end in execution. Only *don't do it* ends in a question, and that is the operator's standing
  rule for disagreement, not a fifth way to pause.
- If the check changed what we did, we say what it changed. If it found nothing, we do not manufacture
  a concern to look thorough.

## Interaction with the plan and execute gates

- **Feeds the independent-review gate; does not replace it.** If the verdict is "proceed" on
  something plan-shaped, the next gate is independent review by legs that were handed no premise
  ([`specs/research-team-protocol.md`](../../specs/research-team-protocol.md),
  [`skills/dual-model-reconciliation`](../dual-model-reconciliation/SKILL.md)). This check runs
  first and cheaper; it is not a substitute for a reviewer that can disagree.
- **Does not override forward motion.** The default stays: proceed autonomously on safe, reversible,
  in-scope work; escalate only what genuinely needs the operator — public-facing, irreversible, or a
  matter of intent rather than correctness (doctrine 7 of
  [`specs/the-silent-clear-problem.md`](../../specs/the-silent-clear-problem.md)).
- **Does not replace a verifier.** Reasoning our way to "this is fine" is not evidence. A claim that
  matters still goes past a check that can fail
  ([`skills/research-verification`](../research-verification/SKILL.md)).

## Worked examples (all 2026-08-19, the day the rule was written)

Three fired the same day, each preventing wasted or wrong work:

- **Q1 caught a false premise.** The agent had proposed a new operating mode for a data-processing
  script to close a "mechanism gap" it had identified. Asked *"should we build the new mode"*
  instead of *"build the new mode"*, it pressure-tested its own proposal and found the diagnosis was
  wrong: it had read **its own earlier summary** of the script instead of the script, and the gap
  did not exist. A bare imperative would have shipped a real change built on a misreading of the
  agent's own prior words. (Kin to `verify-running-build`'s rule that a self-report is not the
  artifact — here the self-report was the agent's.)
- **Q4 caught a suggestion with no symptom behind it.** Asked *"which do you recommend"*, the agent
  withdrew its own suggestion to run a diagnostic tool — it had offered it because it was cheap, not
  because anything was wrong. Nothing would have broken by not running it. "Cheap" was the whole
  reason, and cost is not value.
- **Q2 caught work that already existed.** Asked *"should we scope the eight-class experiment"*, the
  agent searched prior work and found a complete experiment design **already existed** — dated
  2026-07-05, control-gated, with pre-registered fail criteria — and that the paired data it needed
  was already on disk. Two cloud legs were about to be dispatched to re-derive it from scratch.

## Worked example to copy — the output shape

Directive received: *"Add a retry loop around the fetcher, it keeps failing."*

Silent pass: Q1 — does it keep failing? The log shows three failures in the last hour, all `HTTP 429`.
Q2 — is there a retry already? `rg -n 'retry|backoff' fetch/` finds none. Q3 — is a retry the right
unit? A `429` is rate-limiting; retrying without backoff makes it worse; the fix is backoff, and the
caller may also be over the documented quota. Q4 — without it, the hourly job keeps dropping items.
Q5 — wrong if the `429`s come from a quota we exhausted, in which case retrying is pointless until
the quota resets.

What the operator sees:

> **Verdict: do it differently.** The failures are all `429` rate-limits, so a plain retry would
> amplify them — adding exponential backoff with a ceiling instead, and checking the quota window
> first. If the quota is exhausted, retrying cannot help and I will say so rather than add the loop.
>
> *(tool calls follow in the same turn)*

Compare the *do it as asked* shape on a directive that survives the check: **"Verdict: do it as
asked — the file exists, nothing depends on it, reversible with one `git checkout`."** followed by
the edit. One line. Then the work.

## Anti-pattern

Do not perform the check theatrically. A paragraph of visible deliberation that concludes "yes,
proceeding as asked" on a trivial edit is noise, and noise is how a rule gets ignored. The check is
mostly silent; only its *result* is user-facing, and only when it changed something or found a real
concern.

## Verification before you call it adopted

- [ ] The agent runs the five questions on **imperatives**, not only on questions.
- [ ] Depth scales with cost × reversibility; trivial mechanical work is exempt.
- [ ] The verdict has come back **NO** or **differently** at least once on a real directive — a check
      that has only ever said *yes* has not been shown to work.
- [ ] The verdict appears **before** the tool calls, in one or two sentences.
- [ ] Three of the four verdicts ended in execution in the same turn; only *don't do it* ended in a
      question, and that turn ended there.
- [ ] A "proceed" on plan-shaped work still went through the independent-review gate afterwards.

## Related

- [`skills/guard-target-correctness`](../guard-target-correctness/SKILL.md) — never-fires and
  always-fires are the same defect; a verdict that cannot be NO is that defect in prose.
- [`specs/the-silent-clear-problem.md`](../../specs/the-silent-clear-problem.md) — doctrine 6 (check
  the shared pool before building) is Q2; doctrine 7 (escalate precisely) bounds the *don't do it*
  row.
- [`specs/research-team-protocol.md`](../../specs/research-team-protocol.md) — the independent
  review this check feeds when the verdict is "proceed" on plan-shaped work.
- [`skills/task-dependency-sequencing`](../task-dependency-sequencing/SKILL.md) — the next pre-flight
  once "should we" has answered yes on a multi-step task.
- [`skills/honesty-stop-gate`](../honesty-stop-gate/SKILL.md) — the output-side sibling: this skill
  governs whether to act; that one governs what may be claimed afterwards.
