# SPEC — Arm S: make STRICT add-precision the default measure

Target file: `~/jarredou_run/jarredou-kick-test/arm_s_sparsity.py`

## Why (do not skip — it determines the correct implementation)

Arm S measures whether the kick-stem ADD step is worth keeping, by thinning the seed and watching
add-precision. The current measure is TOO LOOSE: it counts an added note as correct whenever it lands
within the scoring window of ANY human kick — **including a human kick the seed ALREADY covered.**
Those are duplicates, not recoveries; under 1:1 scoring they are false positives.

That flaw is not merely imprecise, it is **biased in a direction correlated with the independent
variable**: a dense seed covers most kicks, so most "correct" adds there are duplicates, while a thin
seed has genuine gaps. Measured inflation: 13.6% vs 6.8% at p=0 (2x) but 79.4% vs 76.8% at p=40
(2.6 points). So the loose measure FLATTENS the very curve the sweep exists to measure.

The strict measure also cross-validates: strict p=0 = 0.0677 reproduces the independently-derived
6.7% in `REPORT_merge_validation_2026-07-24`. The loose measure reproduces nothing.

## What to change

1. **Compute BOTH measures per (song, sparsity), keep both in the row.**
   - `added_tp_loose` — the current value: `len(C.align_labeled(added_times, human).["tp"])`.
   - `added_tp_strict` — NEW, and the default headline.
2. **Strict rule.** `common.align_labeled(detected, truth)` returns `tp` as a list of
   **`(det_t, truth_t)` PAIRS** (not scalars — calling `float()` on one raises TypeError).
   - Compute the seed's own alignment: `seed_lab = C.align_labeled(kick_before, human)`.
   - Build the set of human kicks the seed ALREADY covers, from the TRUTH side:
         covered = { round(truth_t, 6) for (det_t, truth_t) in seed_lab["tp"] }
   - Compute `add_lab = C.align_labeled(added_times, human)`.
   - An add is STRICT-correct when the human kick it matched is NOT already covered:
         strict = count of (det_t, truth_t) in add_lab["tp"] where round(truth_t, 6) not in covered
   - **Do NOT** implement this as a distance threshold against the seed's note times. The merge matches
     at `MATCH_WINDOW = 0.03` while scoring uses `0.050`, so a proximity test is the wrong instrument
     and silently mis-classifies. Compare human-kick IDENTITY via the truth side, as above.
3. **Row fields** become: `added_count`, `added_tp_strict`, `added_tp_loose`, `added_fp` (unchanged
   meaning), plus everything already present. **Keep `added_tp` as an alias of `added_tp_strict`** so
   existing readers of `results.json` do not silently switch meaning without noticing — anything that
   reads `added_tp` now gets the correct number.
4. **Per-song print line**: the `addP` column shows the STRICT precision.
5. **Summary table**: the `add-precision` column is STRICT. Add one new column **`dup-rate`** =
   `1 - (strict_tp / loose_tp)` over that sparsity level (0.0 when `loose_tp` is 0) — the share of
   otherwise-"correct" adds that were duplicates. This gap is a real diagnostic (it is what exposed
   the mechanism) so it must be reported, not discarded.
6. Apply the change to **BOTH** code paths — the p=0 anchor pass and the main sweep loop (the file
   currently computes `add_lab` twice, around lines 230 and 306).

## ★ MANDATORY ANCHOR GATE — print it, and STOP if it fails

Before any sweep numbers are reported, the run must print an anchor line comparing the STRICT p=0
aggregate — restricted to songs whose `name` appears in
`~/pc-passback/CLAUDE-COMMS/fleet-to-win/reports/merge_validation_results.json` —
against the known value:

      expected STRICT p=0 add-precision on the reference subset = 0.0677  (report states 6.7%)

Accept within +/- 0.005. If it falls outside, print `ANCHOR FAILED` and `sys.exit(1)`. A harness that
has not reproduced a cross-validated anchor produces deltas that mean nothing.

The EXISTING anchor gate (p=0 AUTO tuples reproducing the reference `au` values song-for-song) must
keep passing untouched — it compares tp/fp/fn and is unaffected by the precision definition.

## Hard contracts
- **Never raise.** Wrap per-song work so one bad song is skipped, not fatal. **Do NOT use a bare
  `except Exception: continue` that swallows the reason** — print `!! <song>: <ExcType>: <msg>` so a
  systematic failure cannot masquerade as a clean zero. (A silent TypeError of exactly this kind
  produced a plausible-looking 0.0000 during this investigation.)
- Do not change: the thinning logic, the `_detected` hook usage, guard recomputation from the thinned
  seed, `off = 0.0` scoring, the corpus dedup, or the skipped-song accounting.
- Deterministic: same RNG seeding per (song, sparsity).

## Output
Rewrite `arm_s_sparsity.py` completely. Reply with ONE fenced python block and nothing else.
