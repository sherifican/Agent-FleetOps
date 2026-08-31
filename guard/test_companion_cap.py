#!/usr/bin/env python3
"""Guard for the CONTENT-ADAPTIVE companion cap (`vision_ingest._companion_cap`, shipped 2026-08-01).

A cap decides what gets DELETED, so this guard has to prove the change in both directions: that it
actually fires (content raises the cap), that it cannot fire the wrong way (never below the old rule),
and that the old rule really was the flat thing we replaced — otherwise "we fixed it" is unfalsifiable.

Run: python3 guard/test_companion_cap.py
"""
import os, sys, math, random

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import vision_ingest as vi

FAILED = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


def cands(n, tens=0):
    """n qualified-distinct candidates, `tens` of which score a true 10."""
    return [{"companion_score": 10 if i < tens else 8, "ts": float(i)} for i in range(n)]


def old_cap(c, dur_min):
    """The PRE-2026-08-01 formula, frozen here verbatim as the comparison baseline."""
    target = min(vi.COMPANION_TARGET, max(2, round(dur_min / 10.0)))
    must_see = [r for r in c if r.get("companion_score", 0) >= 10]
    return min(vi.COMPANION_MAX, max(target, min(len(must_see), target + 3)))


print("=" * 78)
print("COMPANION CAP GUARD")
print("=" * 78)

# --- 1. TEETH: the old rule really was flat, so there is something to fix -------------------
print("\n[1] TEETH — the old rule gave the SAME cap regardless of content")
olds = {old_cap(cands(q), 16.0) for q in (5, 28, 47, 59, 62)}
check("old formula collapses 12x content variation to one value", olds == {2}, f"got {olds}")
news = [vi._companion_cap(cands(q), 16.0)[0] for q in (5, 28, 47, 59, 62)]
check("new formula separates them", len(set(news)) > 1, f"got {news}")

# --- 2. Reproduces the measured replay numbers --------------------------------------------
print("\n[2] MEASURED — matches the offline replay over the five real videos")
# (Q, dur_min) -> expected cap, from vision_measure/replay_cap.py
EXPECT = {("boris-cherny", 28, 16.0): 7, ("lm-studio", 47, 16.0): 8,
          ("5-biggest-lies", 5, 16.0): 4, ("higgsfield", 59, 16.0): 9,
          ("ollm-vs-llamacpp", 62, 12.0): 9}
for (slug, q, dur), want in EXPECT.items():
    got = vi._companion_cap(cands(q), dur)[0]
    check(f"{slug}: Q={q} dur={dur:.0f}m -> cap {want}", got == want, f"got {got}")

# --- 3. THE INVARIANT: never lower than the old rule ---------------------------------------
print("\n[3] INVARIANT — the new cap is NEVER lower than the old one (randomised sweep)")
random.seed(1729)  # fixed: this suite must be deterministic
worst = None
for _ in range(4000):
    q = random.randint(0, 120)
    tens = random.randint(0, q)
    dur = random.uniform(0.0, 180.0)
    c = cands(q, tens)
    new = vi._companion_cap(c, dur)[0]
    old = old_cap(c, dur)
    if new < old:
        worst = (q, tens, round(dur, 1), old, new)
        break
check("no (Q, tens, duration) makes the new cap smaller", worst is None,
      f"counterexample Q={worst}" if worst else "")

# --- 4. Bounds ----------------------------------------------------------------------------
print("\n[4] BOUNDS — floor, ceiling, and the empty case")
check("ceiling holds at COMPANION_MAX even at Q=10000",
      vi._companion_cap(cands(10000), 16.0)[0] <= vi.COMPANION_MAX)
check("duration floor still lifts a long, sparse video",
      vi._companion_cap(cands(1), 90.0)[0] >= min(vi.COMPANION_TARGET, 9))
check("Q=0 does not crash and stays at the floor", vi._companion_cap([], 16.0)[0] == 2)
check("a 0-length video still yields the minimum 2", vi._companion_cap(cands(30), 0.0)[1] >= 2)

# --- 5. Monotonicity: more content never means fewer frames --------------------------------
print("\n[5] MONOTONIC — cap is non-decreasing in Q")
seq = [vi._companion_cap(cands(q), 16.0)[0] for q in range(0, 120)]
check("cap never decreases as Q grows", all(b >= a for a, b in zip(seq, seq[1:])),
      f"first drop at Q={next((i for i,(a,b) in enumerate(zip(seq,seq[1:])) if b<a), None)}")

# --- 6. The overage still belongs to TRUE 10s only -----------------------------------------
print("\n[6] OVERAGE — only true 10s can push past target")
c9 = [{"companion_score": 9, "ts": float(i)} for i in range(40)]
c10 = [{"companion_score": 10, "ts": float(i)} for i in range(40)]
cap9, t9, *_ = vi._companion_cap(c9, 16.0)
cap10, t10, *_ = vi._companion_cap(c10, 16.0)
check("a wall of 9s does not trigger the overage", cap9 == t9, f"cap {cap9} vs target {t9}")
# `cap10 >= t10` was vacuous: it holds trivially if cap is misreported AS target (mutation CC7 survived
# on exactly that). The overage genuinely fires here, so the relation must be STRICT.
check("a wall of 10s DOES push strictly past target", cap10 > t10, f"cap {cap10} vs target {t10}")

# --- 7. The two bounds on the overage, which nothing pinned (mutations CC2 + CC3 survived) ---------
# Both are asserted as PROPERTIES over a sweep rather than as one magic number, so they keep binding
# if the formula is retuned. Hand-derived anchors for the record (dur=16min):
#   Q=40 tens -> target 8, overage min(40, 8+3)=11, clamped to COMPANION_MAX=10  -> cap 10
#   Q=20 tens -> target 6, overage min(20, 6+3)= 9, under the ceiling            -> cap  9
print("\n[7] OVERAGE BOUNDS — the ceiling holds, and the overage width is fixed at +3")
viol_ceiling, viol_width = [], []
for q in range(1, 121):
    tens = [{"companion_score": 10, "ts": float(i)} for i in range(q)]
    cap, target, *_ = vi._companion_cap(tens, 16.0)
    if cap > vi.COMPANION_MAX:
        viol_ceiling.append((q, cap))
    if cap > min(vi.COMPANION_MAX, target + 3):
        viol_width.append((q, cap, target))
check("the hard ceiling holds for every Q, even where the overage would exceed it",
      not viol_ceiling, f"COMPANION_MAX={vi.COMPANION_MAX}; first breach {viol_ceiling[:1]}")
check("the overage never runs wider than target+3",
      not viol_width, f"first breach (Q, cap, target) = {viol_width[:1]}")
# The ceiling check above is only meaningful if some Q actually REACHES the ceiling — otherwise it is a
# check that cannot fail. Prove the overage path is live at the ceiling.
at_max = [q for q in range(1, 121)
          if vi._companion_cap([{"companion_score": 10, "ts": float(i)} for i in range(q)], 16.0)[0]
          == vi.COMPANION_MAX]
check("the ceiling check is REACHABLE (some Q actually hits COMPANION_MAX)",
      bool(at_max), f"first Q reaching the ceiling: {at_max[:1]}")

print("\n" + "=" * 78)
if FAILED:
    print(f"FAILED ({len(FAILED)}): " + ", ".join(FAILED))
    sys.exit(1)
print("ALL CHECKS PASSED")
