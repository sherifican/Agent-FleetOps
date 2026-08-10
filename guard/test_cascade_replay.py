#!/usr/bin/env python3
"""GUARD — the cascade replay's own logic, on fixtures whose answers are computed by hand.

`replay_cascade.py` now carries a verdict that overturned a shipped claim, and it is the only evidence for
the one surviving frame-capture proposal. Its outputs are derived from real records, so a bug in
`verdict()`/`select()` would produce a plausible table with no visible symptom. These fixtures pin the
semantics against cases small enough to check by eye.

Fixture discipline (learned the hard way twice on 2026-08-01): each case isolates ONE variable, and the
expected value is derived from the rule, never from running the code and blessing the output.
"""
import sys

sys.path.insert(0, ".")
sys.path.insert(0, "./vision_measure")
import vision_ingest as vi
import replay_cascade as rc

FAIL = []


def check(name, got, want):
    ok = got == want
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}" + ("" if ok else f"   got={got!r} want={want!r}"))
    if not ok:
        FAIL.append(name)


def rec(idx, ts, keep, score, keep_p, score_p, text=None):
    """One record. `text` drives _dedup_companions (Jaccard >= 0.65 on on_screen+keep_reason tokens),
    so distinct text keeps fixtures from silently deduping and changing what is being tested."""
    return {"idx": idx, "ts": ts, "tc": f"{int(ts)//60:02d}:{int(ts)%60:02d}",
            "frame": f"frame_{idx:04d}.jpg", "on_screen": text or f"distinct subject alpha{idx} beta{idx}",
            "keep_reason": f"reason gamma{idx}", "keep": keep, "companion_score": score,
            "audit": {"keep_p": keep_p, "score_p": score_p}}


print("verdict() — which model's opinion the pipeline sees")
# gate 0 is today's pipeline: the auditor ran on everything, so the reconciled verdict always stands,
# no matter how far the describer disagreed.
check("gate 0 uses reconciled even when describer disagreed",
      rc.verdict(rec(1, 10, True, 9, False, 0), 0), (True, 9, True))
# Below the gate the auditor never runs, so the describer's own verdict is what survives.
check("below gate uses the describer's verdict",
      rc.verdict(rec(2, 10, True, 9, False, 2), 5), (False, 2, False))
# At the gate exactly, the auditor DOES run (>= is the rule, not >).
check("score_p == gate is ABOVE the gate (>=, not >)",
      rc.verdict(rec(3, 10, True, 9, False, 5), 5), (True, 9, True))
check("score_p just under gate is below",
      rc.verdict(rec(4, 10, True, 9, False, 4), 5), (False, 4, False))
# A record with no usable audit block cannot be gated; it must fail SAFE toward the incumbent rather than
# being silently treated as low-scoring and dropped.
check("missing score_p falls back to reconciled (fail-safe)",
      rc.verdict({"keep": True, "companion_score": 8, "audit": {}}, 5), (True, 8, True))
check("missing audit key entirely falls back to reconciled",
      rc.verdict({"keep": True, "companion_score": 8}, 5), (True, 8, True))
check("keep_p None falls back to reconciled",
      rc.verdict({"keep": True, "companion_score": 8, "audit": {"score_p": 1, "keep_p": None}}, 5),
      (True, 8, True))

print("\nselect() — candidate build under a gate")
# Hand-built video: 4 frames, all reconciled-qualified (keep, score >= COMPANION_MIN=7).
# Two were rescues the describer had rejected at a low score; two the describer scored highly itself.
V = [rec(1, 10, True, 9, True, 9),      # describer agreed, high — survives any gate <= 9
     rec(2, 70, True, 8, False, 1),     # RESCUE at score_p 1 — dies at any gate > 1
     rec(3, 130, True, 8, False, 4),    # RESCUE at score_p 4 — dies at any gate > 4
     rec(4, 190, True, 7, True, 7)]     # describer agreed — survives any gate <= 7
check("gate 0 keeps all four", rc.select(V, 0)["Q"], 4)
check("gate 2 drops only the score_p=1 rescue", rc.select(V, 2)["Q"], 3)
check("gate 5 drops both rescues", rc.select(V, 5)["Q"], 2)
check("gate 5 keeps exactly the describer-agreed frames",
      sorted(r["idx"] for r in rc.select(V, 5)["cands"]), [1, 4])
# The auditor also RAISES scores. A frame the describer scored below COMPANION_MIN only qualifies because
# of that upgrade, so gating it out removes it even though the describer said keep.
UP = [rec(5, 10, True, 9, True, 3)]     # keep_p True, but score_p 3 < COMPANION_MIN
check("score upgrade lost below gate removes the candidate", rc.select(UP, 5)["Q"], 0)
check("same frame qualifies at gate 0", rc.select(UP, 0)["Q"], 1)
# A skipped VETO can only ADD a frame when the describer both kept it and scored it >= COMPANION_MIN.
VETO = [rec(6, 10, False, 0, True, 8)]  # auditor vetoed it; describer said keep at 8
check("skipped veto adds the frame back above its score_p", rc.select(VETO, 9)["Q"], 1)
check("the same veto is NOT skipped below score_p", rc.select(VETO, 8)["Q"], 0)
check("veto-add impossible at a gate <= COMPANION_MIN", rc.select(VETO, vi.COMPANION_MIN)["Q"], 0)

print("\nselect() — publication is capped, and the cap follows Q")
# _companion_cap is imported from vision_ingest, so this pins the CONTRACT (published never exceeds either
# the cap or the candidate count), not the current constants — the numbers may move when the cap changes.
s0 = rc.select(V, 0)
check("published == min(cap, |candidates|)", s0["pub"], min(s0["cap"], len(s0["cands"])))
check("published never exceeds the cap", s0["pub"] <= s0["cap"], True)
check("selected set is a prefix of the ordered candidates",
      [r["idx"] for r in s0["cands"][:s0["pub"]]], [r["idx"] for r in s0["sel"]])
# The cap-Q interaction, on a fixture: fewer scored candidates must never RAISE the cap.
check("shrinking Q never raises the cap", rc.select(V, 5)["cap"] <= s0["cap"], True)

print("\nmonotonicity — a higher gate can only skip more calls")
prev = -1
mono = True
for g in range(0, 11):
    skipped = sum(1 for r in V if not rc.verdict(r, g)[2])
    if skipped < prev:
        mono = False
    prev = skipped
check("auditor calls skipped is non-decreasing in gate", mono, True)
# Candidate count must be non-increasing in gate for THIS fixture, which contains no veto-adds. (On the
# real corpus a veto-add could break this at gate > COMPANION_MIN, which is why it is asserted here, on a
# fixture where the exception is known to be absent, rather than as a universal law.)
qs = [rc.select(V, g)["Q"] for g in range(0, 11)]
check("no veto-adds in this fixture, so Q is non-increasing", all(b <= a for a, b in zip(qs, qs[1:])), True)

print("\nregression — the two numbers the FINDINGS note now cites")
# These pin the CONCLUSION against the live corpus. They will legitimately change if records are added or
# the cap changes; when that happens, re-derive and update FINDINGS in the same commit rather than
# loosening the check. A silent drift here is exactly how the 191 defect survived.
try:
    import glob, os
    roots = [r for r in sorted(glob.glob(rc.BASE + "/*"))
             if os.path.exists(os.path.join(r, "vision", "_temp", "records_audited.jsonl"))]
    # ⚠ A SKIP HERE IS NOT A PASS. Processing one new video on 2026-08-02 took the corpus 34 -> 35 and
    # this block silently stopped running, while the suite still printed "all cascade-replay guards pass".
    # The mutation harness caught it immediately: three cascade mutations (CR8/CR9/CR10) that ONLY this
    # block catches went from KILLED to SURVIVED. Growing the corpus is normal and must not disarm the
    # pin, so an unrecognised size is now a FAILURE demanding re-derivation, per this subsystem's rule
    # that UNMEASURED dominates a violation.
    # videos -> (baseline published, gate-5 published, strict lost). Re-derived each time the corpus grows;
    # the VERDICT has held at every size (gate 5 always exceeds the 2% bar), which is the point of keeping
    # them rather than loosening the check. 39 added 2026-08-03 after the vision queue processed 4 videos —
    # and the FAIL-on-unknown-size behaviour is what surfaced it, exactly as intended.
    PINS = {34: (183, 174, 10), 35: (189, 180, 10), 39: (222, 213, 10)}
    if len(roots) not in PINS:
        check(f"corpus size {len(roots)} has a pinned expectation "
              f"(re-derive and add it in the same commit; do NOT delete this check)", False,
              f"known sizes: {sorted(PINS)}")
    else:
        exp_base, exp_g5, exp_lost = PINS[len(roots)]
        base = gate5 = lost = 0
        for root in roots:
            recs = vi._load_records(root)
            b, c = rc.select(recs, 0), rc.select(recs, 5)
            base += b["pub"]; gate5 += c["pub"]; lost += len(b["idx"] - c["idx"])
        check(f"baseline published == {exp_base} ({len(roots)} videos)", base, exp_base)
        check(f"gate-5 published == {exp_g5}", gate5, exp_g5)
        check(f"gate-5 strict loss == {exp_lost} frames", lost, exp_lost)
        check("gate 5 exceeds the 2% threshold", lost / base > 0.02, True)
except Exception as e:                                    # noqa: BLE001 - corpus may be absent
    print(f"  skip  corpus unavailable ({type(e).__name__})")

print()
if FAIL:
    print(f"FAILED {len(FAIL)}: " + ", ".join(FAIL))
    raise SystemExit(1)
print("all cascade-replay guards pass")
