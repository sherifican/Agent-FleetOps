---
name: guard-target-correctness
description: Check that a guard watches the RIGHT predicate, not merely that it can fail. Invoke when writing or reviewing any guard, check, gate, metric, or assertion — especially one written in response to an incident. teeth_prover proves a guard is REACHABLE and mutation_harness kills DECLARED mutations; both are bounded by the input shapes you already thought of. This procedure enumerates the equivalence class the predicate claims to cover and finds the members it silently excludes, which is where the next incident lives. Covers BOTH directions: a predicate too NARROW, and a guard so WIDE it is switched off socially — never-fires and always-fires are the same defect.
license: MIT
---

# Guard Target-Correctness — reachable is not correct

This repo already proves guards can fail. `guard/teeth_prover.py` mutates a clean fixture and asserts the
guard flips, and states its own boundary explicitly:

> "This module proves guards are REACHABLE, not that they are CORRECT."

`guard/mutation_harness.py` goes further — it mutates the code under test and reports SURVIVED when a guard
stays green while its protected property is broken. **Both are bounded by the same thing: the mutations you
declared.** A guard passes both while being wrong, whenever reality produces an input shape nobody
enumerated. That untested region is not random — it is precisely the region you did not think of, which is
exactly where the next incident comes from.

This skill is the discipline for that gap. It is cheap, it is manual, and it is not replaceable by running
the harness again.

> **Framing note:** the instances below are from the authors' own systems and are evidence for the rule,
> not universal claims.

## The shape of the failure

A guard is almost always written **after** an incident, so it encodes the shape *that* incident had. The
concept it is meant to protect is wider than the shape. The predicate silently becomes the narrow one.

Then a value in the same conceptual class but a different literal shape arrives, the predicate says fine,
and the guard reports green while the thing it exists to prevent happens.

### Instance 1 — a validity counter that watched one falsy value

A counter tracked how many callers sent a request without a valid identity fence. The predicate was
`value is None`. Production also produced `""`.

`"" is not None` evaluates True, so every empty-string caller was counted as *properly fenced*. The guard
was reachable — a `None` would have tripped it — and it was wrong.

**The concealed defect was worse than the metric.** Because `""` was accepted as an identity, two distinct
callers both sending `""` compared **equal to each other** and were handed the same registration. A
uniqueness invariant was broken, and the guard that existed to observe exactly that reported clean.

**And then the obvious fix was wrong too.** The first correction normalized `""` to `None` so the counter
would tally it. That fixed the *measurement* and left the *behavior* intact — the invalid fence was still
accepted. It was fail-open. The correct fix rejects the value. Repairing the instrument instead of the
fault is its own failure mode; when you find a guard watching the wrong predicate, ask whether the right
predicate should also *refuse* something, not just count it.

### Instance 2 — exact equality standing in for sameness

A scheduler compared model identity with `observed != expected`, exact string equality. The environment
tags certain models with a device-variant suffix, so `name` and `name-<device>` are the same model wearing
different labels.

Every reconciliation logged a divergence for two identical models. Worse, the same exact-equality test
gated the "this job is genuinely running" confirmation — so a variant-tagged job could never be confirmed,
its lease would not be protected, and it became eligible for eviction mid-task. The predicate (byte
equality) was narrower than the concept (same model), and a correctness guarantee rested on the gap.

## The other direction — a guard that is too WIDE

Everything above is the predicate that is too narrow. The mirror failure is the guard that flags
everything: nobody deletes it — it is switched off *socially*. Its alerts get acknowledged on
reflex, then batched, then ignored, and the end state is no guard at all. A guard that never fires
and a guard that always fires are the same defect: zero information.

So: **measure the population before the invariant lands.** Run the candidate predicate over the
live corpus it will police and read flagged/scanned before the guard ships. Pin the corpus in a
manifest first, so the denominator cannot drift under the measurement; a predicate flagging more
than a configured share of that pinned corpus (default: half) fails its own review. And breadth
alone is not a pass — the review also names the labelled positives it checked, because a guard can
be narrow and still wrong.

Arm: `guard/population_arm.py` runs a candidate checker over a pinned corpus manifest, records
flagged/scanned, and fails the review when the share exceeds the ceiling or a labelled positive is
missed. Gate: `guard/tests/test_population_arm.py`.

**Define ONE exit code as the flag.** "Nonzero means flagged" quietly counts crashes, usage errors
and the timeout code as detections, and it fails in the direction that looks like success: a
checker crashing on exactly the file it was meant to detect reads as a narrow, accurate guard that
caught its labelled positive. Every other nonzero, and any timeout, means the checker did not
answer the question — so the breadth ratio measures nothing and the review is CANNOT CHECK, with
the checker's own stderr retained per path.

## The procedure

For each guard, in writing:

1. **State the concept in one sentence.** Not the code — the thing you actually mean. "No caller may act
   without a unique identity." "A job that is really running must be confirmed."
2. **Write down the predicate as implemented.** The literal comparison. `x is None`. `a != b`. `count > 0`.
3. **Enumerate the equivalence class the concept covers.** Adversarially, and specifically include:
   - **falsy siblings** — `""`, `0`, `[]`, `{}`, `false`, whitespace-only
   - **aliases and normalizations** — case, suffixes, prefixes, tags, trailing separators, unicode forms
   - **absent vs empty vs default** — three distinct states that collapse into each other constantly
   - **the value that is equal to itself but should not be** — two callers legitimately sending the same
     placeholder, and whether your identity check can tell them apart
   - **the type you did not expect** — a string where a number was assumed, and vice versa
4. **For each member, decide: should the guard trip? Does it?** Any row where those two disagree is a
   finding. This is a table, not a feeling.
5. **Check what the guard does on a trip.** Counting an invalid value is not rejecting it. If the concept
   says the input is invalid, something must refuse it, not merely tally it.

6. **Measure the population before the invariant lands.** Run the candidate predicate over the
   pinned corpus it will police (`guard/population_arm.py`): record flagged/scanned, fail the
   review over the ceiling, and name the labelled positives checked.

## When the field name lies

A guard reading a field with a stable name is not reading a stable meaning. A back-compat alias can keep
a field's name identical while its semantics invert underneath — and every suite reading that field stays
green through the inversion.

**Assert on observable behavior, not on field presence or field name.** If a guard's evidence is "the field
is there" or "the field is non-empty", it is watching a label, not a property.

Related: a checker that searches for prose describing a rule can be satisfied by the prose *about* the rule
rather than by the rule holding — check structure, not substrings.

## Verification before you call it done

- [ ] The concept is written in one sentence, separate from the code.
- [ ] The equivalence-class table exists, with a should-trip / does-trip column per row.
- [ ] Falsy siblings, aliases, and absent-vs-empty are each represented in the table.
- [ ] Every disagreement is either fixed or recorded as a known, accepted gap — not left implicit.
- [ ] Where the concept implies invalidity, something **rejects**; a counter alone is fail-open.
- [ ] The guard asserts on behavior, not on the presence of a named field.
- [ ] The candidate ran over the PINNED live corpus; flagged/scanned is recorded and under the
      configured ceiling (default: half the corpus).
- [ ] The review names the labelled positives it checked — breadth alone is not a pass.

## Related

- `guard/teeth_prover.py` — reachability; the necessary companion, and explicitly not this.
- `guard/mutation_harness.py` — kills declared mutations; this skill is how you decide which mutations are
  worth declaring.
- `skills/verify-running-build` — the same failure in deploy gates: a marker present in both builds is a
  predicate that cannot discriminate.
- `guard/population_arm.py` (+ its gate `guard/tests/test_population_arm.py`) — the too-WIDE arm:
  breadth over a pinned corpus, with labelled positives.
