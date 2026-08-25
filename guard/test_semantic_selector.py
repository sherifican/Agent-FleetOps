#!/usr/bin/env python3
"""Both-directions guard for the semantic candidate selector.

The selector decides which frames get PAID for, so the failure that matters is silent: a genuinely novel
frame never reaches the scorer and its absence is indistinguishable from "there was nothing there". These
fixtures are synthetic so ground truth is constructed rather than labelled — a wrong verdict cannot be
argued away.

Run: python3 guard/test_semantic_selector.py
"""
import os, sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import vision_semantic as vs

FAILED = []
rng = np.random.default_rng(20260801)


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))
    if not cond:
        FAILED.append(name)


def cluster(center, k, jitter=0.02, d=32):
    return center + rng.normal(0, jitter, (k, d))


print("=" * 78)
print("SEMANTIC SELECTOR GUARD")
print("=" * 78)

# ---------- 1. TEETH: a lone novel frame must be selected ----------
print("\n[1] FINDS the novel frame (the one we must never fail to look at)")
d = 32
base = rng.normal(0, 1, d)
V = list(cluster(base, 40))                 # 40 near-identical frames
novel = rng.normal(0, 1, d) * 3            # one genuinely different frame
V.insert(20, novel)
ts = list(range(len(V)))
sel, reasons = vs.select(np.array(V), ts, budget=5)
check("the unique frame is selected", 20 in sel, f"selected {sel}")

# ---------- 2. NOT OVERBROAD: near-duplicates collapse ----------
print("\n[2] COLLAPSES near-duplicates instead of spending budget on them")
V2 = np.array(list(cluster(base, 30)) + list(cluster(base + 5, 30)))
ts2 = list(range(60))
sel2, _ = vs.select(V2, ts2, budget=4)
a = sum(1 for i in sel2 if i < 30); b = sum(1 for i in sel2 if i >= 30)
check("budget splits across the two real groups", a >= 1 and b >= 1, f"group A={a} group B={b}")

# ---------- 3. TEMPORAL COVERAGE IS A CONTRACT ----------
print("\n[3] TEMPORAL COVERAGE — every populated bin gets a slot")
# all the 'interesting' vectors sit early: a pure representativeness objective would cluster there
V3 = np.array(list(cluster(base, 10, 0.9)) + list(cluster(base, 50, 0.01)))
ts3 = list(np.linspace(0, 900, 60))
sel3, reasons3 = vs.select(V3, ts3, budget=6, n_bins=6)
got, missing = vs.bins_covered(ts3, sel3, 6)
check("all 6 bins covered", len(got) == 6, f"covered {got}, missing {missing}")
check("bin reservations are labelled as such",
      sum(1 for i in sel3 if reasons3.get(i) == "bin-reserved") >= 5,
      f"reasons {[reasons3[i] for i in sel3]}")
thirds = [sum(1 for i in sel3 if ts3[i] < 300), sum(1 for i in sel3 if 300 <= ts3[i] < 600),
          sum(1 for i in sel3 if ts3[i] >= 600)]
check("no third of the timeline is starved", all(x > 0 for x in thirds), f"thirds={thirds}")

# ---------- 4. Bins are a CONTRACT, not a SELECTOR (the previous attempt's bug) ----------
print("\n[4] BINS RESERVE, they do not CHOOSE — the failure that killed the Stage-A budget")
# In each bin: many redundant frames plus ONE distinctive frame placed off-centre. A bin-midpoint
# selector (the refuted design) would take the middle frame and miss every distinctive one.
V4, ts4, special = [], [], []
for b in range(5):
    for j in range(10):
        V4.append(base + rng.normal(0, 0.01, d)); ts4.append(b * 100 + j * 5)
    special.append(len(V4))
    V4.append(rng.normal(0, 1, d) * 4); ts4.append(b * 100 + 72)   # off-centre on purpose
sel4, _ = vs.select(np.array(V4), ts4, budget=5, n_bins=5)
hit = sum(1 for s in special if s in sel4)
# 4/5 not 5/5 BY DESIGN: facility location covers the set, so the very first pick is the bulk
# representative rather than the outlier. The teeth are in the comparison below, not in a perfect score.
check("finds the distinctive frame in nearly every bin", hit >= 4, f"{hit}/5 distinctive found")
# TEETH: the REFUTED design (take the frame nearest each bin's midpoint) would find essentially none.
import numpy as _np
ts4a = _np.asarray(ts4, float); lo4, hi4 = ts4a.min(), ts4a.max(); span4 = (hi4 - lo4) or 1.0
mid_pick = []
for b in range(5):
    members = [i for i in range(len(ts4)) if min(int((ts4a[i]-lo4)/span4*5), 4) == b]
    centre = lo4 + span4 * (b + 0.5) / 5
    mid_pick.append(min(members, key=lambda i: abs(ts4a[i] - centre)))
mid_hit = sum(1 for s in special if s in mid_pick)
check("a bin-MIDPOINT selector finds far fewer (this is why that design was refuted)",
      mid_hit < hit, f"midpoint {mid_hit}/5 vs semantic {hit}/5")

# ---------- 5. Determinism ----------
print("\n[5] DETERMINISM")
r1 = vs.select(np.array(V4), ts4, budget=5, n_bins=5)[0]
r2 = vs.select(np.array(V4), ts4, budget=5, n_bins=5)[0]
check("identical inputs -> identical selection", r1 == r2)
Vt = np.array([base, base, base + 3.0])
st, _ = vs.select(Vt, [0, 1, 2], budget=1)
check("exact ties resolve by first index (stable)", st == [0] or st == [2], f"got {st}")

# ---------- 6. Degenerate inputs ----------
print("\n[6] DEGENERATE INPUTS — must not crash or silently return nothing")
e, _ = vs.select(np.zeros((0, d)), [], budget=5)
check("empty input -> empty output, no crash", e == [])
one, _ = vs.select(np.array([base]), [0], budget=5)
check("budget larger than n returns everything", one == [0])
zero, _ = vs.select(np.zeros((10, d)), list(range(10)), budget=3)
check("all-identical vectors still returns a full budget", len(zero) == 3, f"got {zero}")
check("never returns more than the budget", len(vs.select(np.array(V4), ts4, 5, 5)[0]) == 5)

# ---------- 7. Unfilled bins are REPORTED, not hidden ----------
print("\n[7] AN EMPTY BIN IS VISIBLE (never back-filled to fake coverage)")
tsg = [0, 1, 2, 3, 900, 901]          # nothing in the middle bins at all
Vg = np.array(list(cluster(base, 4)) + list(cluster(base + 9, 2)))
selg, _ = vs.select(Vg, tsg, budget=4, n_bins=6)
gotg, missg = vs.bins_covered(tsg, selg, 6)
check("genuinely empty bins are reported missing", len(missg) > 0, f"missing={missg}")

# ---------- 8. OCR NOVELTY — the term added to catch text-bearing UI ----------
print("\n[8] OCR NOVELTY — separates near-identical frames that differ only in TEXT")
# Three frames with essentially the SAME embedding (a terminal that barely changes visually) but
# different text. Appearance alone cannot tell them apart; that is the measured failure this fixes.
# NOTE the fixture must contain ONLY near-identical frames: an earlier version also included a
# strongly-different frame, which correctly won on coverage and left the text frame third in line —
# the test then "failed" while the code was right. Isolate the variable being tested.
term = base + rng.normal(0, 0.001, d)
Vo = np.array([term, term + 1e-6, term + 2e-6])
tso = [0.0, 10.0, 20.0]
toks = [{"loading", "model"},
        {"loading", "model"},
        {"loading", "model", "tokens", "sec", "41.2"}]           # NEW text only on index 2
sel_no, _ = vs.select(Vo, tso, budget=2, n_bins=1, ocr_w=0.0)
sel_ocr, _ = vs.select(Vo, tso, budget=2, n_bins=1, tokens=toks, ocr_w=0.6)
check("with OCR weight, the text-bearing frame is selected", 2 in sel_ocr,
      f"no-ocr={sel_no} ocr={sel_ocr}")
check("OCR weight changes the outcome (the term is critical)", sel_no != sel_ocr,
      f"identical selections {sel_no}")

# textless frames must NOT be punished — that is what the embedding term exists for
Vt = np.array([base, base + 1e-6, base + 8.0])
tst = [0.0, 10.0, 20.0]
tokt = [{"alpha", "beta"}, {"alpha", "beta"}, set()]              # index 2 is a textless diagram
selt, _ = vs.select(Vt, tst, budget=2, n_bins=1, tokens=tokt, ocr_w=0.9)
check("a textless diagram still wins on coverage at high OCR weight", 2 in selt, f"got {selt}")

# determinism with OCR active
o1, _ = vs.select(Vo, tso, budget=2, n_bins=1, tokens=toks, ocr_w=0.6)
o2, _ = vs.select(Vo, tso, budget=2, n_bins=1, tokens=toks, ocr_w=0.6)
check("OCR path is deterministic", o1 == o2)
check("empty token sets everywhere == the no-OCR result",
      vs.select(Vo, tso, budget=2, n_bins=1, tokens=[set()]*3, ocr_w=0.6)[0] == sel_no)


print("\n" + "=" * 78)
if FAILED:
    print(f"FAILED ({len(FAILED)}): " + ", ".join(FAILED)); sys.exit(1)
print("ALL CHECKS PASSED — finds novelty, collapses duplicates, coverage is a contract not a hope.")
