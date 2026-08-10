#!/usr/bin/env python3
"""Regression test: companion selection must SPREAD across the timeline within a score tier.

Found 2026-08-01 on the first dense-sampling validation run. The old sort key was
(companion_score, -ts) with reverse=True, so among EQUALLY-scored frames the earliest always won.
At 30s sampling ties were rare and this never showed. At 2s sampling frames tie at 10 constantly,
and a 16-minute video selected all five of its companions from the first 5m44s — 28% of the
runtime — while two more score-10 frames sat at 12:12 and 13:56 and were only visible because the
new reserve caught them instead of deleting them.

The bug was invisible for as long as it existed because the losing frames were DELETED: there was
no artifact left to notice. This test exists so that silence is not the only signal again.

★ FIXED 2026-08-02 — until then this file tested a hand-written MIRROR of the ordering and never
imported `vision_ingest`, so reverting gc() to the naive key left it GREEN: it validated its own copy
of the logic, not the code that deletes frames. The mutation harness caught it (SP1/SP2 SURVIVED). It
now imports `vi._order_companions`, the single implementation gc() itself calls. A guard that
re-implements what it protects is decoration — see feedback-control-must-be-verified ADDENDUM 2b.

Run:  python3 guard/test_companion_spread.py
Exit: 0 pass · 1 regression detected
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import vision_ingest as vi                     # noqa: E402 - path set above

FAILS = []

# THE ordering the pipeline actually uses. Imported, never re-implemented: that is the entire point of
# the 2026-08-02 fix. If this import breaks, the guard must FAIL loudly rather than fall back to a copy.
order = vi._order_companions


def check(name, cond, detail=""):
    print(f"  {'OK  ' if cond else 'FAIL'} {name}{(' — ' + detail) if detail else ''}")
    if not cond:
        FAILS.append(name)


def main():
    print("companion temporal-spread regression test\n")

    # A 16-minute video where TWELVE frames all tie at the top score, clustered 8 early and 4 late.
    # This is the exact shape that produced the real defect.
    cands = ([{"ts": t, "companion_score": 10} for t in (74, 120, 196, 264, 300, 340, 344, 380)]
             + [{"ts": t, "companion_score": 10} for t in (732, 800, 836, 900)])
    sel = order(cands)[:5]
    span = max(r["ts"] for r in sel) - min(r["ts"] for r in sel)
    total = max(r["ts"] for r in cands)

    check("selection is not confined to the first half",
          max(r["ts"] for r in sel) > total * 0.6,
          f"latest selected t={max(r['ts'] for r in sel):.0f}s of {total:.0f}s")
    check("selection spans a majority of the runtime",
          span / total >= 0.6, f"span {span:.0f}s / {total:.0f}s = {100*span/total:.0f}%")
    # (A `check(..., True, ...)` lived here until 2026-08-02 — a literal constant asserted as a condition,
    #  which is decoration by definition. The real version of that claim is the TEETH check below.)

    # Teeth: prove the naive ordering really does cluster, so this test could FAIL if the fix regressed.
    naive = sorted(cands, key=lambda r: (r["companion_score"], -r["ts"]), reverse=True)[:5]
    naive_span = max(r["ts"] for r in naive) - min(r["ts"] for r in naive)
    check("TEETH: the naive key clusters (so a regression WOULD be caught)",
          naive_span / total < 0.5, f"naive span {100*naive_span/total:.0f}% vs fixed {100*span/total:.0f}%")

    # Score must still dominate: a lone 10 outranks any number of 9s.
    mixed = [{"ts": 500, "companion_score": 10}] + [{"ts": t, "companion_score": 9} for t in (10, 900)]
    check("score still dominates spread (a 10 outranks 9s)",
          order(mixed)[0]["companion_score"] == 10)

    print(f"\n{'PASS' if not FAILS else 'FAIL: ' + ', '.join(FAILS)}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
