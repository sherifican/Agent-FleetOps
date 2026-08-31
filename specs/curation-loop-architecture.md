# The Curation Loop — a gated architecture for agents that edit their own operating rules

A self-improving agent system has one existential failure mode: **silent drift of its own rules.**
An agent that freely edits its skills and memory will, over enough cycles, optimize its rules toward
whatever its recent transcripts rewarded — including its own mistakes, restated as policy. This
architecture lets a fleet propose improvements to its own skill/memory base continuously while making
every change **attributable, reviewable, reversible, and human-gated**.

It has operated in production on a working multi-agent fleet for hundreds of passes. The design
below is the mechanism, generalized; numbers are from the reference deployment.

## The loop, end to end

```
   transcripts / activity           (what actually happened)
        │
        ▼
   WATCHER — line-count + file-change thresholds; fires a TRIGGER file, never an action
        │
        ▼
   AUDIT SUB-AGENT — reads a recent-activity digest + the durable sources;
        │            emits PROPOSALS (create/update/hygiene), each with rationale + dedup evidence
        ▼
   REVIEW GATE — verifier/dry-run runs FIRST, then a PANEL of independent models votes;
        │        disagreement goes to the operator with dissent preserved;
        │        agreement follows the adopter's policy — ACCEPT: apply · FILTER: forward
        │        to the operator (rule 11; both are valid, the adopter picks one);
        │        rejects are LOGGED for calibration, not discarded
        ▼
   DETERMINISTIC APPLY — approved edits become exact search/replace specs;
        │               a spec that does not match EXACTLY ONCE is refused, never fuzzy-applied
        ▼
   INDEPENDENT AUDIT — a second, smaller model diffs intent vs. applied change: MATCH / MISMATCH
        │
        ▼
   GIT COMMIT — the skill/memory trees are git repos; every pass is one commit, revertible
        │
        ▼
   EVOLUTION LOG + REBASELINE — the pass is journaled; the watcher's baseline moves so the
                                pass's own edits cannot re-trigger it (no echo loops)
```

## Design rules that carry the weight

1. **The watcher fires a trigger, never an action.** Detection and execution are separated so a
   runaway watcher can at worst nag, not edit. The trigger is a file with `pending: true` — visible,
   inspectable, clearable.

2. **The proposer cannot apply.** The audit sub-agent's entire output is a proposal list. It has no
   write access to the rule base. This is the same privilege separation as code review: authorship
   and merge rights never in the same hands.

3. **Proposals carry their own dedup evidence.** Every proposal must state what existing rule it
   checked against and why it isn't already covered. A curation loop without hard dedup converges on
   a rule base of restatements.

4. **An honest empty pass is a valid pass.** "Nothing new worth capturing" is an acceptable audit
   result. Padding pressure is drift pressure.

5. **The apply step is deterministic, not generative.** Approved intent → exact unique-match
   search/replace → refuse on zero or multiple matches. A CREATE proposal is applied as a
   whole-file block — the complete content of the new file, written only when the target path does
   not yet exist; an existing target is a refusal, never an overwrite. The model that *decides*
   the edit never free-hands the *application* of it. Zero-corruption by construction, at the cost of occasional
   refusals that fall back to manual application.

6. **A second model audits the diff.** Intent vs. applied change, MATCH/MISMATCH, from a model that
   didn't write either. Cheap, and it catches the apply layer's mistakes rather than trusting them.

7. **Rejects are a calibration dataset.** Rejected proposals go to a review file, not the void. If
   the proposer keeps proposing the same rejected thing, that's signal about the proposer; if a
   reject later turns out right, that's signal about the gate.

8. **Rebaseline after every pass.** The loop's own edits must not count as "activity" for the next
   trigger, or the loop feeds itself. (Found the hard way: the first deployment echo-triggered off
   its own commits.)

9. **Inferred preferences are defaults, not laws.** Rules learned from observed behavior (rather
   than stated instruction) are recorded with their evidence strength and marked overridable. A
   single observation is a weak prior; the loop strengthens or retires it as the pattern recurs —
   and says so in the rule text itself.

10. **Hygiene is a separate, mechanical lane.** Broken links, stale paths, index gaps — safe to
    auto-apply and clearly labeled as such. Substantive content changes never ride the hygiene lane.

11. **Rule-base changes are panel-reviewed, and the vote comes AFTER the verifier.** A change to
    skills, operating rules, or accepted drift passes a panel of INDEPENDENT models — one model's
    approval is not acceptance. Whatever verifier or dry-run exists runs BEFORE the vote (a vote on
    unverified material is refused), and panel disagreement goes to the operator with the dissent
    preserved. Whether unanimous panel agreement may itself ACCEPT the change, or the panel only
    FILTERS — rejecting or forwarding to an accountable human — is a deployment policy the adopter
    sets explicitly; both policies produce the same pass record, and `guard/curation_gate.py`
    rejects that record under either policy when it shows a one-model approval, a vote before
    verification, or a disagreement without an operator verdict
    (gate: `guard/tests/test_curation_gate.py`).

## Failure modes this design answers

| Failure | Answered by |
|---|---|
| Rules drift toward recent noise | human gate + dedup evidence + honest-empty-pass norm |
| A bad apply corrupts a rule file | deterministic unique-match apply + git revert |
| The loop triggers itself | rebaseline after every pass |
| The gate rubber-stamps | rejects log = a measurable gate record |
| One instance's view overwrites another's | git as ground truth; parallel sessions label non-canonical output and never bulk-write shared memory |
| The applier "improves" the edit | apply is search/replace, not generation; second-model diff audit |
| One approver's blind spot becomes policy | panel of independent models + `guard/curation_gate.py` — rejects one-model approval, vote-before-verification, and unresolved disagreement |

## Minimal adoption

The true minimum is **two independent reviewers plus one accountable operator** — not one agent
and a human. Rule 11 makes the panel part of the mechanism, so a deployment with a single reviewer
cannot pass its own gate; `guard/curation_gate.py` rejects that record. Independent means distinct
reviewer identities: the gate counts identities, because one model voting twice is an echo, not a
second opinion.

Everything else can be one agent: a git repo for the rule base, a line-count watcher, one audit
prompt that outputs structured proposals, a review step (verifier first, then the panel/operator
gate of rule 11), and a script that applies exact matches or refuses. The two components most
tempting to skip — the rejects log and the rebaseline — are the two that prevent the quiet failure
modes.
