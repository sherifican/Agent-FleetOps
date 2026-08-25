---
name: eval-integrity
description: Audit a test / benchmark / eval BEFORE trusting its result — the anti-skim, anti-laziness discipline. Invoke before running ANY test you built AND before presenting its results. Verify against RENDERED/EXECUTED reality not your intent; the CONTROL is sacred (eyeball it hardest); when N subjects contradict your ground truth, suspect the test first; the scorer must not be gameable; judge wording/completeness, not just binary catch/miss.
---

# eval-integrity — audit a test BEFORE you trust it (anti-skim discipline)

A test/benchmark/eval is the instrument you make decisions with. A miscalibrated instrument doesn't just
give a wrong number — it gives a **confident** wrong number, and you ship the decision on it. The audit is
cheap; the wrong decision is not. **Invoke this before running ANY test you build, and before presenting its
results.** Skimming this step is the single highest-leverage way to be confidently wrong.

> **Framing note:** every dated measurement below was made on the authors' reference setup (their
> hardware, models, and corpus). The numbers are evidence for the rule they sit under, not universal
> constants — your setup will differ; the failure MODE is what transfers.

## The cardinal rule — verify against RENDERED/EXECUTED reality, not your intent
Your spec is NOT the artifact. Tools transform inputs silently: **Vega/vl_convert sorts nominal categories
ALPHABETICALLY**; a template fills defaults; a quantization changes behavior; an endpoint sleeps. Before
running, **render + EYEBALL every test input** (every image), **print every ground-truth label**, and
**read every generated prompt** — then confirm they are what you think. Never trust the spec over the
produced artifact.
> Real miss: I eyeballed 4 defect charts but SKIPPED the "clean control." Vega reordered its
> months Jan→Feb/Jan/Mar, so the control had a real defect; 4 models correctly flagged it and I called them
> wrong. Eyeballing the control would have caught it in 5 seconds, before wasting the run + misleading the
> operator.

## The CONTROL is sacred — audit it hardest of all
The control is the **most important single data point**: it calibrates true-negative vs false-positive. If the
"clean" case isn't actually clean, you cannot interpret ANY result — a wrong control silently inverts your
conclusions. Verify the control by direct inspection **every time**, before anything else.

## Run the ANCHOR before the population — it catches DESIGN flaws, not just implementation bugs
Before a new instrument touches the real population, make it reproduce a **known-answer anchor** and STOP if it
does not. The usual framing is "this catches coding bugs"; the higher-value catch is that **it invalidates
whole METHODS whose logic looked sound on paper.** Two independent instances (drum-transcription research,
reference setup):
- A residual-energy test (proposed by two independent audits) was gated on the one section
  where the answer was known — snare confirmed absent from the *whole mix*, so the residual had to be empty.
  It read **0.914** (0.914 raw / 1.112 with harmonic-percussive separation) against **0.154** for the working
  metric, and sat at the **median** of that song's own window distribution. The metric had **no discriminative
  power at all** — the band was occupied by continuously-playing bass/guitar/vocals regardless of snare. The
  decision rule would have classified the known-poisoned anchor as a *separation failure*. Nothing in the
  method's description revealed this; only the anchor did.
- A sparsity-sweep harness was gated on reproducing a prior 232-song run. 19/20 songs matched **exactly**; the
  20th exposed a **corpus defect** (duplicate packs carrying independently-separated stems → different detector
  output), not a harness bug.
**When the anchor fails, DIAGNOSE — never loosen the gate.** My first hypothesis for that 20th song (run-to-run
non-determinism) was wrong, and testing it directly (same input separated twice → **0.000 ms** difference)
refuted it and pointed at the real cause. Loosening the gate to "19/20 is close enough" would have buried a real
finding. A gate you relax on failure is not a gate.

## An AGGREGATE anchor is only valid on the population it was measured on — never gate a SUBSET against it
A per-item anchor is valid at any sample size **only if the per-item quantity is REPRODUCIBLE** (a deterministic
tuple: does song X yield the same 20 onsets?). For a per-item quantity that is intrinsically NOISY, a single
item is not an anchor at all — see the next section. An **aggregate** anchor (does the corpus-wide rate
reproduce 0.0677?) is **not** — run it on a 20-song subset of a 216-song reference and a perfectly healthy
harness fails, because the expectation was never about that population. Measured on the reference setup:
`--limit 20` returned 0.0423 vs an expected 0.0677 and looked like a regression; the control (same 20 songs,
*old* code) returned **0.0423 identically**, proving the gap was sample size, not behaviour.
**Two rules follow:**
1. **When a gate cannot be evaluated, say NOT EVALUATED — never PASSED, never a widened tolerance.** Widening
   the band to make subsets "pass" converts a false alarm into a false all-clear, which is strictly worse: it
   also lets a real regression through. Not-evaluable is the correct verdict, not approximately-evaluable.
2. **Keep the per-item gate running at every size.** It still catches a genuine break, so a limited run stays
   protected while declining to assert the thing it cannot measure.

## Across runs whose INPUTS differ, compare DELTAS — never LEVELS
Two runs of the same arms over the same corpus are comparable only if they saw the same inputs. Change the
detection input and you change the matched-item SET, hence the denominator, hence every accuracy LEVEL —
while the metric keeps its name. Measured on the reference setup (cymbal-detection arms): a quantitative
forward prediction of **+6 pp**, formed by differencing accuracy levels across two runs, came in at
**+0.27 pp**. The runs held **11,546 vs 8,013** matched onsets with baselines of **61% vs 78%** — two
different populations wearing one metric name. Only the **WITHIN-run arm-vs-arm** comparison was ever
controlled.
**Rules:** (1) before subtracting two numbers, confirm they were computed over the SAME item set — quote
**n** next to every level, always; (2) carry **arm DELTAS** across runs, never levels; (3) a forward
prediction assembled from cross-run levels is not a prediction, it is an uncontrolled subtraction — label
it as such or do not publish the number.

## Compute the metric WITHIN each group before you pool — a pooled score above every within-group score is BETWEEN-GROUP separation, not skill
A discriminator scored over a pooled set of groups (videos, songs, packs, patients, sessions) can earn most
of its score from *which group an item came from*, not from the property you meant to measure. The tell is
arithmetic and cheap: **pooled > every within-group value.** Measured on the reference setup (a video-vision
per-frame structure filter): within-video AUROC **0.44-0.70** — worse than a coin on one of three videos —
while the **pooled AUROC was 0.789**, higher than all of them. The groups' qualified-rates were
**1.4% / 31% / 39%**, so a feature that merely tracks "this looks like the dense video" scores well pooled
and predicts nothing within a video, which is where the decision is actually taken.
**Rules:** (1) always report the per-group values beside the pooled one — a pooled number alone is
uninterpretable on a grouped corpus; (2) if pooled exceeds every within-group value, the metric is partly
answering *which group is this*; (3) that is not automatically bad news — here it correctly relocated the
signal from the frame level to the VIDEO level — but it must be NAMED before it is acted on. This is a
sibling of the train/held-out group leak: same grouped-corpus hazard, a different stage.

## A predictor is judged on its OUTPUT, not on its correlation — check that the output VARIES
A calibration that asks only "does the feature correlate with the outcome?" can green-light a predictor
whose recommendation never changes. Measured on the reference setup (video-level sampling triage): the gate
printed **USABLE** off Spearman **+0.800**, while the module's actual recommendation was **DENSE for all four
videos** — including the 728-call / 1.4%-yield case the whole idea existed to catch. A classifier emitting
one class has zero discriminating power at any correlation. And the 0.800 was a **tie artifact** — averaged
ranks absorbing a tied pair — at **n=4**, where rho=0.8 carries **p~0.17**.
**Three checks before any calibration gate prints USABLE:**
1. **OUTPUT VARIANCE** — does the recommendation actually differ across the calibration set? If not, stop;
   the correlation is irrelevant.
2. **TIE INSPECTION** — find the items that TIE on the feature and read their outcomes. Two videos tied at
   `screen_frac=0.667` with yields of **1.4%** and **40.5%**: no monotone threshold separates them, so the
   FEATURE is refuted, not the knob. Re-tuning thresholds on those same 4 points would be
   threshold-selection leakage.
3. **n-AWARENESS** — quote the p-value/CI beside any rank correlation at small n; rho alone never carries a
   ship decision.
**A gate that checks correlation but not output variance has the same zero-information defect it was written
to detect.** Re-adjudicating the same four points through the fixed gate returned NOT USABLE on two
independent counts.

## A file with the RIGHT NAME can hold the WRONG CONTENT — hash it, don't trust the path
On the reference setup, a vendored `.npz` model artifact hashed to the value of a *historical backup*: the
old backup was sitting under the current filename, plus two stale `.json` sidecars. Nothing in the code or
the tree looked wrong. Because that model drove the cleanup pass that generated an experiment's seed, running
it would have produced a plausible seed built from a superseded model — an error no code review or import
check can see.
**Before an experiment whose inputs come from a vendored artifact, verify the ARTIFACT by hash against the
source of truth, not its filename or its directory.** Ask the other side for hashes of weights/sidecars too,
not just source — they are the part that silently changes behaviour. (Related: a partial match is the
dangerous case — one local tree had correct `.npz` files but stale sidecars, which a weights-only check would
have passed.)

## Score the OUTCOME, not the mechanism — the mechanism is usually the part that already works
Measured on the reference setup (a tool-calling bake-off): all four local models used the **exact minimum
call count with zero malformed calls** at up to 12 chained calls. On a "did it call the tools correctly?"
metric it is a 4-way tie and the cheapest model wins. But scoring the **result** — the submitted total
against known ground truth — showed one subject model reading all 10 chunks correctly and then submitting
the **wrong sum**, deterministically, 3/3 identical runs. **The differentiator was
arithmetic/state-tracking, not tool-calling**, and a mechanism-only metric would have promoted the model
that fails.
**Rules:** (1) score the end artifact against known ground truth, not the steps taken to produce it;
(2) make the answer **unguessable** (values hash-derived, not summable by inspection) so a subject cannot
skip the work and still score; (3) when every subject aces the mechanism, that is a signal the mechanism
is no longer the bottleneck — go find what is, do not declare a tie.

## A changelog fix does not mean YOUR path used the broken component — verify the SERVING PATH first
A serving-stack release fixed "tool calls silently dropped at end of generation" for one model family. I
treated every prior tool-call number for that family as depressed by that bug and designed a whole bake-off
phase on it. Both halves were wrong: the production path for that family was a **separate serving sidecar
that never used the patched component** (the setup had engineered around the bug months earlier), and the
"buggy" runtime scored **4/4** on the affected path anyway. **Before attributing any result to a known
upstream defect, establish which binary / port / process actually served it.** A routing table naming a port
is not evidence of which server answers on it.

## When subjects disagree with you, suspect YOURSELF first
**Consensus disagreement = a test bug until proven otherwise.** If N independent subjects (models / runs /
people) all "fail" a case, or all flag your "clean" control, that is a loud signal that **YOUR ground truth
is wrong**, not that they all failed simultaneously. STOP and re-verify the test before concluding anything
about the subjects. (4/4 models flagging the control was the alarm; I ignored it. Don't.)

## Be EQUALLY skeptical of a result you WANT
Motivated reasoning cuts both ways: you scrutinize a disappointing result but wave a flattering one through. A
positive surprise you were hoping for is a yellow flag exactly like a suspicious negative — audit it just as
hard. In one re-gating arc this discipline caught SIX wrong turns before any became a conclusion: a
crash-gate reimplementation bug (fake catastrophe), a "the GT-fix reveals the benefit" hypothesis, an n=3
"pipeline-eats-it" FALSE-NEGATIVE preview, an ineffective threshold re-gate that "looked" like it ran, a
candidate-only operating-point-confounded sweep, and an over-strong "significant" CI claim — several of them
results the orchestrator WANTED to be true. Skepticism you apply only to bad news is not skepticism.
(n=small previews flip conclusions — verify on the FULL set before adopting.)

## Re-test on the FULL population before globalizing a change measured on a SELECTED sample
A favorable result on material SELECTED because the change helps there is a **selection artifact**, not
evidence for a global change — re-measure on the full, unselected population before shipping a blanket
change. Worked case (reference setup, a sub-60 ms onset gate): relaxing the gate looked great on the 51
double-bass targets (+194 real/+27 phantom, ~7:1) — but those songs were chosen BECAUSE the relaxation
helps them; the full-232 re-test was a **WASH** (+38 real/+16 phantom, F1 +0.0002) with worst-case HARM
(one song +1 real/+10 phantoms), VALIDATING the original tightening → no blanket change. This is the
selection-bias twin of "verify on the FULL set" above and the sample-side twin of threshold-selection
leakage; endorse your own scope-caveat OVER a flattering subset number.

## Ground-truth by hand
Every expected answer / pass-criterion is verified against the ACTUAL artifact by a careful check — never
assumed from the intended value. Generating the data does not exempt you from checking the render.

## The scorer must not be gameable
Adversarially test your auto-scorer against a KNOWN-WRONG output before trusting it. A GT-substring check
passes verbose rambling that merely mentions the answer; a lenient MATCH parse passes noise; a "did it fail"
check that reads live state is non-deterministic. If a wrong answer can score right, **fix the scorer, not
the conclusion**.

**And a FIX's success metric must be able to FALL when the fix gets worse.** A peer agent's re-keying fix
reported a RISING "matched" count as it got *worse* — because "matched" counted matches without ever asking
whether a match was CORRECT, so an estimated offset that re-keyed flags onto material 90 seconds away scored
itself higher for doing it. **The fix was worse than the bug**: the bug flagged nothing, the fix confidently
flagged the wrong things, and its own metric was monotone in the fix's own action. Rule (theirs, adopted):
**when a fix's own success metric can rise while the fix gets worse, that metric is the bug.** Before
trusting any fix-validation number, ask what it would read if the fix were wrong in the most likely
direction — if the answer is "the same or higher", replace the metric before you replace the code. Sibling
of "verify a gain is a gain": a +1 that is really "the wrong item took the right one's place" is a
regression wearing a plus sign.

## A CLEAN / "nothing found" verdict is unproven until the detector has gone RED on a known positive
A scan, gate or regression test that has **only ever returned clean** proves nothing: a working detector and a
broken one produce byte-identical output. Before you trust any negative, feed it a **known positive** and confirm
it fails. Three instances from one day on the reference setup:
- **A secret-scan regex that silently could not match.** A post-staging scan of an eval repo used
  `sk-[a-zA-Z0-9]{16,}` and returned clean — but the planted fixture `sk-prod-9f8a7b6c…` never matches, because
  the character class **breaks at the hyphen** in `sk-prod-`. It was caught only because the fixture was already
  known to be there. Otherwise "clean" would have been the same output a correct scan gives.
- **A tool whose default silently narrows the population.** `git check-ignore` **skips tracked files by default**,
  so an audit for "listed in `.gitignore` yet still tracked" found nothing — including the file being audited
  *because it was already known to be broken*. The correct invocation is `git check-ignore --no-index`. A default
  flag is part of your instrument; read what it excludes before reading its result.
- **A negative control that taught something new.** A patch's proof harness was green 35/35 on the patched tree
  and **RED with 18 failures** on an unpatched tree rebuilt from `git show HEAD:<patched-file>` — that pairing
  is what makes it a gate rather than a ritual. The RED run also surfaced an unclaimed property: on the
  *upstream* pattern a legitimate `--dry-run` invocation was **wrongly blocked**, so the token-boundary fix
  repaired a false POSITIVE too.
**How to apply:** for any detector, ship the known-positive with it and run both directions — the negative case is
what establishes the instrument exists at all. Never report "clean" / "no leaks" / "no regressions" from a pattern,
query or gate whose failure path you have not observed **this session**. Prefer the fixture live IN the repo so the
validation re-runs. **Mechanised form:** pair every guard with a MUTATION that must turn it red, promoted to a
build step where the guard count justifies it — and treat an empty *or ambiguous* search needle in any checker as
a hard failure, never a vacuous pass. First full run of the mechanised form (reference setup): 46 mutations vs 6
guards → **12 SURVIVED**, including a guard that never IMPORTS the module it protects (it tested a local MIRROR of
the logic) and a check comparing two outputs of the same call.

## Count matches with 1:1 assignment, not nearest-neighbor
When scoring detections/predictions against a reference by proximity ("within ±T of a ground-truth item = a hit"),
use **1:1 greedy/bipartite matching** (each reference item matched at most once), NOT independent
nearest-neighbor. A many-to-one match **double-credits DUPLICATES**: two detections on ONE real event both land
near the same reference, so both score "real" — the hit count inflates and the false-positive (phantom) count is
UNDERCOUNTED. Worked case (reference setup, a drum-onset gate): a naive "within ±50 ms of a human = real" count
read 212 real / 9 phantom; proper 1:1 matching read **194 real / 27 phantom** — the 18-gap was exactly the
duplicate triggers (a known duplicate-trigger failure mode the naive count hid). A 2nd detection on an
already-matched note MUST count as a false positive.

## Report the single-feature baseline alongside any FUSED number — fusion often DILUTES
When combining signals (an AND/OR rule, an ensemble, a "fused discriminator"), the fused score is frequently
**WORSE than the strongest single feature alone** — weak evidence dilutes strong. Observed 3× in one arc on the
reference setup: fast-roll fused AUROC **0.783 < 0.813** for the strongest single feature alone; a two-feature
AND rule at the same precision but **2.5× less recall** than its better feature alone; a fused crash detector
**0.725 ≤ 0.735** stem-energy alone. **Rule: always report the best single-feature baseline next to the fused
number.** If you show only the fused score the dilution is invisible and you may adopt a combination worse than
one of its parts. Default assumption on any "combine these signals" proposal: it dilutes until proven otherwise.

## Before you name the CAUSE of an aggregate loss, disaggregate by the dimension that distinguishes causes
An aggregate ("22.8% resolution-loss") is a symptom, not a diagnosis. Before attributing it to a cause, break it
down along the dimension whose SIGNATURE would confirm/refute that cause — and check the signature actually holds.
Worked miss (reference setup): a 22.8% onset loss was labelled "close-hit resolution," but disaggregating by
inter-onset-interval band showed **91% sat at >80 ms (normal spacing)** and the miss-rate was **non-monotonic**
(a close-hit cause predicts the miss-rate FALLING as spacing grows — it didn't). A fix aimed at "close-hit" would
have optimized ~9% of the loss. The band breakdown was already in the report table — the disaggregation existed;
the wrong conclusion was drawn from the LABEL, not the breakdown. Name a cause only after its signature survives
the disaggregation.

## Fair, equal conditions for every subject
The eval condition must be identical and fair across subjects: a `think=True` setting leaks a reasoner's chain
into a format check (unfair to reasoners); a slept endpoint errors one subject (missing data); a stale/warm load
skews another. Control for these BEFORE the run (pre-warm, fresh loads, matched settings).

**★ And a probe must replicate the SUBJECT'S OWN resilience, or it is measuring a different code path.** A
throughput probe died on its first cold call until it reproduced the production caller's **4x retry** — the
sidecar being measured sleeps at 600s idle, so the real path absorbs a cold start a naive probe cannot. A harness
that omits the retries, warm-up, timeouts or fallbacks the real caller has is not a faster/slower version of
production; it is a different program. **IMPORT the call path — do NOT copy it.** ⚠ This rule originally said
*copy*, and the very probe it was distilled from then DRIFTED: within days the probe carried a timeout of
**300 vs production's 150** (a 200 s call SUCCEEDED in the probe that production would have abandoned and
retried, so the probe could credit an arm with a latency the pipeline never accepts) and a bare
`except Exception` vs production's narrow exception tuple (a JSON/key error that propagates in production was
silently swallowed as a retry). Fixed by single-sourcing the request SHAPE and transport POLICY into one shared
module + constants, which the probe now imports. **A copy of a call path is a fork with no merge** — and the
sentence advising the copy is why the fork happened.

## A FIDELITY CLAIM ("this mirrors / replicates the real X") is a critical assertion nobody tests
A comment or docstring asserting that an instrument reproduces production is a **testable proposition**, and
it is worse than no comment at all, because it actively discourages the check. Two confirmed instances in two
days on two machines: a throughput probe claiming *"replicates the REAL call body, only the named
variable changes"* (two divergences, both of which changed what it measured), and a peer agent's instrument
whose header claimed to run the real windowing logic while simulating with its own loops.
1. **A STALENESS sweep and a FIDELITY check are different questions.** A retirement pass asks *"is this
   instrument stale?"* and never asks *"does it still do what it SAYS it does?"* One of three instruments
   cleared as TRUSTWORTHY by exactly such a sweep was carrying a false fidelity claim the whole
   time. Ask both explicitly, or the second never gets asked.
2. **Grep the CLAIM vocabulary — and keep widening it.** `"mirror of" · "copy of" · "same logic as"` found the
   first instance; another said **"replicates the REAL"** and matched none of them. Current list:
   `mirror|copy of|same logic as|replicates|identical to|in sync with|same as the real`. **A vocabulary that
   missed the case you actually had is the vocabulary to extend.**
3. **Prove a refactor equivalent by LOADING THE OLD CODE, never by hand-copying it.** Load the pre-change
   module straight out of git (`git show <rev>:<path>`), intercept what the old path would have produced, and
   compare bytes — hand-writing a copy of the old body to compare against re-commits the exact defect being
   fixed. Then **mutation-check the equivalence proof itself**: an equivalence test that passes when nothing
   changed is indistinguishable from one that always passes. Worked instance (reference setup): byte-identical
   on a real 150,243-byte request, and perturbing one shared constant (max-tokens 1200→1199) turns it red on
   both comparisons — which is what shows the import is live rather than cosmetic.

## Compare at MATCHED operating points — sweep BOTH sides, calibrate OFF the eval set
When two models/configs sit at DIFFERENT operating points (thresholds/gates), a one-sided threshold shift is
operating-point-CONFOUNDED: re-gating ONLY the candidate over-credits it, because the baseline ALSO gains from
the same shift. Sweep BOTH and quote the fair MATCHED-shift delta (each side at its own re-tuned point), never
the candidate-only lift. And NEVER pick the "optimal" threshold on the same eval set you then report on — that
is threshold-selection leakage; calibrate gates/thresholds on an INDEPENDENT set, and treat a small-K
overlapping-subsample "CI" as EXPLORATORY, not calibrated significance.

## The BAR is part of the instrument — score every defensible READING, and "pre-registered" must be provable
Measured on the reference setup (a cascade-replay eval). The denominator picks the population; the
**reading** picks the arithmetic. Both must be fixed before the result is seen, and only one of them usually is.
1. **A bar admits multiple readings — enumerate and score ALL of them, and the reading implied by your own
   stated target is BINDING.** One gate read net **0.0% PASS** / strict **1.1% PASS** / symmetric (lost+gained)
   **2.2% FAIL**. The stated target was *"agreement with the incumbent — identical output for fewer calls"*,
   under which **any** changed artifact is a disagreement, so symmetric binds and the gate fails. I had defended
   my reading only against the more LENIENT alternative and was silent on the stricter one my own framing
   implied. **Motivated selection among readings is the same defect as motivated selection among results**, and
   it is harder to see because every reading is individually defensible. A verdict that flips with the reading
   is not a verdict yet — report it as reading-dependent.
2. **Convert a %-bar into ITEM units before trusting any verdict near it.** 2% of 183 published = **3.66
   frames**, so every verdict near the bar turned on ±1 frame — and all the churn sat inside one video. When the
   margin is under one item, the honest verdict is **"not provable at this corpus size"**, never PASS.
3. **"Pre-registered" is a property of the AUDIT TRAIL, not of intent.** A threshold whose first committed
   appearance sits in the same commit as a favourable result is not pre-registered in any checkable sense —
   however sincerely you remember fixing it first. Claim only what the trail supports (here: the bar was *not
   moved when moving it would have helped* — the denominator migrated and the verdict flipped to FAIL while the
   bar stayed). For the strong claim, commit the bar before the measurement exists.

## Two arms must be scored over a COMMON BUDGET — a budget derived from an arm's own output is a different budget per arm
`n_keep = max(1, int(len(onsets) * keep_fraction))` is unremarkable in product code. Inside a **measurement
harness** it scales each arm's keep-budget with **that arm's own output**, so the two arms were never scored over
a common budget — the composition defect (a limit keyed on a measured count of upstream output) relocated
**from the product into the instrument**. An arm that detects fewer items keeps proportionally fewer, so the arm
that looks efficient may partly be the arm that was allowed to publish less. Cross-machine grep (a peer agent,
same day): **0 instances in shipping code, 7 in eval tooling — all the same line.** The instrument is where this
concentrates, and it is where it does the most damage: in the product the coupling shifts behaviour, in the
instrument it silently changes what the comparison MEANS.
**Distinguish LIVE from THEORETICAL before you act:** the shape only bites when the count can differ *between
arms*. A holdout sized `int(len(track_names) * frac)` keys on the corpus, which is fixed before any arm runs — a
theoretical hit (verified in the gate script). A sweep over a fixed scored set (both arms thresholding the same
9,429 onsets) shares its budget by construction and is not exposed at all. Ask: *can the arm under test move this
count?* **How to apply:** every budget, cap and keep-count inside a harness is a constant of the COMPARISON,
never a function of the arm being compared — and when you report absolute counts across arms, state the shared
denominator explicitly so the question cannot be silently begged.

## Don't score the lane you FIT the registration/alignment to — fit on a HELD-OUT lane
If you align/register two signals by **maximizing agreement on lane X** (a timeline offset, a warp, a matching
tolerance), then **lane X's own precision/recall is optimistically biased** — you tuned a free parameter to make
X agree, so X's score is no longer independent evidence. Read it with that caveat or, better, **fit the
registration on a lane you are NOT scoring** and take the target lane's number clean. Worked case (reference
setup, a full-mix→ground-truth alignment): the timeline offset was fit on the **SNARE** lane (kick was too
phantom-ridden to align on), so SNARE's precision/recall read optimistically and must NOT be headlined; **kick
stayed honest because nothing was fit to it**, so the kick collapse (F1 0.830→0.418) is the trustworthy
headline. Same fit-and-report-on-the-same-data family as threshold-selection leakage — a registration offset is
just another fitted knob; fitting it on the scored lane leaks. **How to apply:** name every parameter you fit
(offset/warp/tolerance/threshold) and the lane/set you fit it on; if that lane is one you then score, hold out a
different lane for the fit, or flag that lane's number as fit-biased.

## Catch/miss is not the whole story — judge the WORDING
For quality-sensitive evals (a QA gate, a reviewer, a writer), a binary pass/fail HIDES the real signal. A
terse "something's off" is NOT the same as a thorough, precise diagnosis. **Capture the RAW outputs and judge
completeness/precision/wording** — the auto-score is advisory; the raw output is the truth. (A model that
"caught" a defect but described it vaguely while missing the bigger problem is not equal to one that diagnosed
it fully — same binary, opposite quality.)

## A pure COST optimisation is validated on OUTPUT EQUIVALENCE — a speedup you have not equivalence-checked is not a result
When a change is meant to buy speed/cost with **no** change in quality, the metric is not the speed number —
it is **byte-level agreement with the incumbent's output**. Measured on the reference setup (a video-describer
throughput probe, 8 real frames against the live sidecar):

| arm | speedup | verdict |
|---|---:|---|
| output-token caps 1200→400→200 | 0.99x / 1.01x | **no effect at all** |
| image resolution w768 / w512 | 1.21x / 1.03x | non-monotone ⇒ noise at n=8 |
| concurrency 4 | **1.49x** | **the only real speedup — and NOT free** |

Concurrency was the "unconditional win". Only **3 of 6** outputs were byte-identical to serial, and two
differed in **companion score (9→8, 10→9) at temperature 0** — not cosmetic, because a downstream cap counts
frames scoring exactly 10, so the batching silently moves it. No arm survived the equivalence check; the
probe's own closing reminder is what caught it.
**Rules:**
1. **Every cost/speed arm reports an equivalence result beside its speedup**, and a non-identical output is a
   FAILURE until someone argues otherwise — not a footnote.
2. **Equivalence against the incumbent is NOT circular for a cost optimisation.** Validating a cheaper path
   against the expensive path's own output is exactly right when the claim is *"same answer, less spend"*. It
   becomes circular only the moment you also claim the change improves QUALITY — then the incumbent is no
   longer a valid reference. Say which claim you are making before choosing the metric.
3. **Before capping a resource, confirm it is the one being consumed.** Output-token caps did literally
   nothing (1200→200, 0.99-1.01x) because the describer's output never approaches the cap and the cost is
   **image PREFILL, not generation**. A null result on a knob is evidence about where the cost lives; read it
   that way instead of concluding "no headroom".

## If a swept parameter OUGHT to be monotone, check that it IS — non-monotonicity is an instrument bug until proven otherwise
Sweeping a budget/size/threshold gives you a free validity test most sweeps never use: **a larger budget must
be a SUPERSET of a smaller one.** If it is not, the sweep's points are not comparable and its peak is
meaningless. Two instances from one day on the reference setup, two different causes:
- **A coupled instrument.** Saved-frame recall went **5/7 → 4/7 → 4/7** as the budget rose 40→60→80, i.e.
  *more* budget lost frames. Cause: the bin count was set to the budget, so raising the budget
  **re-partitioned the timeline** — budget 80 was not budget 60 plus twenty more frames, it was a different
  question. Decoupling the partition from the budget restored monotonicity and made every later measurement
  in that arc trustworthy.
- **Insufficient n.** Image width w768 gave **1.21x** and w512 only **1.03x**. A smaller image cannot be
  slower; the encoder almost certainly resizes both into one internal bucket. Read as **noise at n=8** and
  discarded, rather than reported as "w768 is the sweet spot".
**Rules:** (1) name the monotonicity your parameter should obey BEFORE sweeping it, and assert it —
`set(result[k]) ⊆ set(result[k+1])` is usually one line; (2) a violation has exactly two explanations, a
coupled/unstable instrument or too small an n, and **both invalidate the sweep** — never report the peak of a
non-monotone curve; (3) fix the coupling and re-run, don't smooth it.

## When a guard fails, read the FIXTURE before you read the code
The default reaction to a red test is "what did I break?". Twice in one session (reference setup) the code was
right and the **fixture did not test what its author believed**: a motion-gate fixture used a **periodic**
texture, and periodic content **aliases** — so the detector correctly found a translation that was really a
repeat; and an OCR fixture included a strongly-different frame that legitimately won on coverage, pushing the
intended text frame to third.
**Rules:** (1) on a guard failure, state what the fixture is supposed to exercise and check the input actually
has that property and only that property; (2) **this is not permission to blame the test** — the
periodic-texture case produced a real hardening (a ratio-only similarity test needs an absolute bound beside
it, or fail-CLOSED deletes a new screen), so the honest verdict was *both*; (3) a fixture you had to reason
about to interpret is a fixture that needs simplifying, because the next reader will not repeat the reasoning.

## PRE-FLIGHT CHECKLIST — run before EVERY test
- [ ] Rendered/executed every test input and **EYEBALLED it — especially the control**.
- [ ] The control is genuinely clean/correct, verified by inspection (not assumption).
- [ ] Every ground-truth label checked against the actual artifact.
- [ ] The scorer cannot pass a KNOWN-WRONG output (adversarially checked).
- [ ] Any detector/scan/gate whose result is "clean / nothing found" has been shown to go **RED on a known positive** (fixture, unpatched tree, planted defect) — this session, not historically.
- [ ] Every guard AND every measurement PROBE IMPORTS the shipping module it protects/measures (a local re-implementation is satisfied by construction) and exercises the SHIPPING entry point, not a test-only sibling.
- [ ] Any comment/docstring claiming an instrument MIRRORS or REPLICATES production has been VERIFIED this session, or the claim deleted — and the instrument IMPORTS what it claims to reproduce rather than restating it.
- [ ] Conditions are fair + equal across subjects (no chain-of-thought leak, no slept endpoint, fresh/matched loads).
- [ ] Any number compared against another run was computed over the **same item set** (n quoted); cross-run
      comparisons carry **deltas**, not levels.
- [ ] Any metric over a grouped corpus is reported PER GROUP as well as pooled (pooled > every within-group value = between-group separation, not skill).
- [ ] Any gate/predictor cleared by a CORRELATION was also checked for OUTPUT VARIANCE (does its recommendation ever differ?), tie behaviour, and n.
- [ ] Any speed/cost arm was checked for **OUTPUT EQUIVALENCE** against the incumbent (byte-level where possible) — a speedup with changed outputs is a failed arm, not a caveat.
- [ ] Any swept parameter with an expected monotonicity was ASSERTED monotone (a bigger budget is a superset); a violation invalidates the sweep — coupled instrument or too small an n, never a finding.
- [ ] Comparisons are at MATCHED operating points (swept BOTH sides, not candidate-only); any threshold was calibrated on an INDEPENDENT set, not the eval set being reported.
- [ ] No lane/metric you REPORT was used to FIT a registration/alignment offset (fit on a HELD-OUT lane, or flag that lane's number as fit-biased).
- [ ] The pass/fail BAR: every defensible READING enumerated + scored (the one your stated target implies BINDS); the %-bar converted to item units (margin ≥ 1 item, else "not provable at this n"); the threshold's commit provably predates the result — else claim only "not moved".
- [ ] Every budget/cap/keep-count inside the HARNESS is a constant of the comparison, not derived from the arm under test; shared denominators stated explicitly when reporting absolute counts across arms.
- [ ] Any FIX-validation metric was checked for direction: it must be able to FALL if the fix is wrong.
- [ ] For quality evals: raw outputs captured for human judgment, not just a binary.
- [ ] **If any subject-consensus contradicts your GT → re-audit the test; do NOT blame the subjects.**

## Why non-negotiable
Never present a result you haven't audited the test for. "The data looked done" is not the same as "the test
was correct." When in doubt, re-render, re-read, and re-check the control — it is never wasted time.

### A date argument that silently under-counts
`git log --since=2026-08-03` uses **approxidate**, which resolves a bare date to that date **at the current
time of day** — so at 21:51 it excluded a commit made at 21:48 and the tally printed "no commits". Unfixed it
would have dropped every commit made before 21:51 on day one, permanently, and reported a clean zero for the
measurement it gates. Pin the time: `--since="2026-08-03 00:00:00"`. Same family as the `sk-` regex and
`git check-ignore --no-index` instances above: **a default you did not choose is part of your instrument.**
Caught only because the instrument prints *"nothing measured yet (this is not a result)"* instead of `0%` —
**an instrument must distinguish "measured zero" from "measured nothing".**
