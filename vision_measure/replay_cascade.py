#!/usr/bin/env python3
"""DESCRIBER->AUDITOR CASCADE — offline replay. ZERO model calls, ZERO file writes.

THE CASCADE: today every extracted frame gets TWO model calls — a describer (primary) and an auditor.
The cascade proposal runs the auditor ONLY on frames the describer scored >= GATE, on the theory that the
auditor almost never overturns a low describer score, so those calls buy nothing.

This script exists because the cascade was the ONLY surviving candidate of the 2026-08-01 session and the
ONLY one whose numbers lived in prose rather than in a runnable script. Every refuted arm had a script in
this directory; the survivor did not. That asymmetry is exactly backwards, and re-deriving the numbers here
immediately falsified one of them (see THE 191 DEFECT below).

WHAT IT REPRODUCES (all four figures cited in FINDINGS_2026-08-01.md), plus a gate sweep.

──────────────────────────────────────────────────────────────────────────────────────────────────────────
★ THE 191 DEFECT — why this script exists, found by writing it
The findings note claimed the cascade shrank **published companions** "191 -> 183 (-4.2%)" across 34
videos. Those two numbers are real and they are a legitimate baseline-vs-cascade comparison — but of
`sum(cap)`, the cap CEILING, which is not what the pipeline publishes. `gc()` publishes `cands[:cap]`, so
a video with fewer qualified candidates than its cap publishes fewer than its cap. Measured here:

    arm            sum(cap)     published = sum(min(cap, |cands|))
    baseline          191                  183
    cascade @5        183                  174

The published movement is **183 -> 174**, not 191 -> 183. The error was invisible because the cascade's
CAP-sum lands on exactly the same number as the baseline's PUBLISHED count — two different quantities
colliding on 183 — so the figure looked like a coherent published-count comparison and was labelled as one.
Every arm here is measured with the same `min(cap, |cands|)`, and the cap-sum is reported separately and
labelled as the ceiling it is.
──────────────────────────────────────────────────────────────────────────────────────────────────────────

TWO DENOMINATORS, NEVER MIXED (the lesson that killed the filter work and revived this one):
  ON-DISK    109 companions in `companions.json` across 34 videos. HISTORICAL: written by runs, most of
             them under the PRE-2026-08-01 duration-only cap. This is the denominator for "how often did
             the auditor actually rescue something published".
  PROSPECTIVE the selection the SHIPPED adaptive cap would make from the same records today. This is the
             denominator for "what would the cascade cost going forward".
A rate quoted over one and a cost quoted over the other are not comparable, so both are printed side by
side with their own totals and never summed together.

BOTH DIRECTIONS ARE MEASURED. Skipping the auditor does not only lose its RESCUES (keep_p False -> keep
True); it equally loses its VETOES (keep_p True -> keep False). A skipped veto leaves a frame in the pool
that the auditor had removed, so the cascade can in principle ADD candidates as well as drop them. A replay
that only counted rescues would report a one-sided cost and would be wrong in the flattering direction.

★ BUT A "GAINED" FRAME IS NOT A BENEFIT, and this is the single most important thing to understand before
reading the table. The script separates the two ways a frame can enter the published set that was not there
before, and MEASURES which one is happening:
  PROMOTION  the frame was already a candidate and merely moved UP the ranking because better candidates
             above it were dropped. It is a SUBSTITUTE for something worse — a downgrade wearing a plus sign.
  VETO-ADD   the frame was not a candidate at all and enters because a skipped auditor veto no longer
             removes it. Requires keep_p True AND COMPANION_MIN <= score_p < gate, so it is arithmetically
             impossible at any gate <= COMPANION_MIN (7).
Measured on this corpus: **every gain at every gate from 3 to 9 is a promotion; zero are veto-adds.** The
empirical reason is sharper than the arithmetic one — all 33 vetoes in the corpus sit at score_p >= 7, so
no veto is ever SKIPPED below gate 8, and the one that is skipped at gate 8 never reaches the published set.
Veto-adds first reach publication at gate 10.

So netting gains against losses does not offset a loss with a benefit — it offsets a loss with a worse
replacement for that same loss, and books the substitution as a win. The verdict section therefore scores
THREE readings (net / strict / symmetric) rather than picking one, because which one you take changes the
answer at gates 3-4 and concealing that would be the whole game.

Usage: replay_cascade.py [gate ...]      (default sweep: 0 3 4 5 6 7 8)
"""
import glob, json, math, os, sys
from collections import Counter

sys.path.insert(0, ".")
import vision_ingest as vi

BASE = "~/Fleet-PC-Passback/Research-fleet/video"
DEFAULT_GATES = [0, 3, 4, 5, 6, 7, 8]


def _gates(argv=None):
    """Gates from the command line, parsed HERE and not at import time.

    This module is imported by `guard/test_cascade_replay.py`; a module-level `sys.argv` read would make
    the import inherit the IMPORTER's arguments and crash on any non-integer flag.
    """
    args = sys.argv[1:] if argv is None else argv
    return [int(a) for a in args] or DEFAULT_GATES


# ── the arms ───────────────────────────────────────────────────────────────────────────────────────────
def verdict(rec, gate):
    """Return (keep, score, auditor_ran) for one frame under a cascade with this gate.

    gate 0 = today's pipeline: the auditor runs on everything, so the RECONCILED verdict always stands.
    gate g = the auditor is skipped whenever the describer scored < g, and the describer's own verdict
    (`keep_p`/`score_p`) is what the rest of the pipeline then sees.
    """
    a = rec.get("audit") or {}
    sp, kp = a.get("score_p"), a.get("keep_p")
    if gate > 0 and isinstance(sp, (int, float)) and kp is not None and sp < gate:
        return bool(kp), sp, False
    return bool(rec.get("keep")), rec.get("companion_score", 0) or 0, True


def select(recs, gate):
    """Full candidate build + ordering + cap for one video under one gate.

    Both the ordering and the cap are IMPORTED from vision_ingest, so the replay cannot validate logic that
    differs from the code deleting frames on disk. (The ordering was a verbatim COPY until 2026-08-02, when
    the mutation harness showed a copied ordering is exactly how a guard goes green under a live bug.) What
    is NOT imported is the verdict source — that is the single variable this replay changes.
    """
    view = []
    for r in recs:
        keep, score, ran = verdict(r, gate)
        if (keep and score >= vi.COMPANION_MIN
                and not (r.get("on_screen", "").startswith("ERROR") or r.get("keep_reason") == "CALL_FAIL")):
            q = dict(r); q["keep"] = keep; q["companion_score"] = score
            view.append(q)
    cands = vi._dedup_companions(view)

    ordered = vi._order_companions(cands)      # IMPORTED, never re-implemented (see the docstring there)

    dur_min = max((r["ts"] for r in recs), default=0) / 60.0
    cap, target, Q, floor = vi._companion_cap(ordered, dur_min)
    sel = ordered[:cap]                      # exactly what gc() publishes
    return {"cands": ordered, "Q": Q, "cap": cap, "sel": sel,
            "idx": {r["idx"] for r in sel}, "pub": len(sel)}


def main(argv=None):
    gates = _gates(argv)
    # ── load ───────────────────────────────────────────────────────────────────────────────────────────────
    vids = []
    for root in sorted(glob.glob(BASE + "/*")):
        if not os.path.exists(os.path.join(root, "vision", "_temp", "records_audited.jsonl")):
            continue
        recs = vi._load_records(root)
        cj = os.path.join(root, "vision", "companions.json")
        published = json.load(open(cj)) if os.path.exists(cj) else []
        vids.append({"slug": os.path.basename(root), "recs": recs, "published": published})

    if not vids:
        print("no audited videos on disk — nothing to replay"); raise SystemExit(2)

    TOT_FRAMES = sum(len(v["recs"]) for v in vids)
    TOT_PUB_DISK = sum(len(v["published"]) for v in vids)

    # Coverage of the instrument itself: a frame with no usable audit block can never be gated, so it silently
    # behaves like gate 0 forever. If that set is large the whole measurement is over a subset and says so.
    missing = sum(1 for v in vids for r in v["recs"]
                  if not isinstance((r.get("audit") or {}).get("score_p"), (int, float))
                  or (r.get("audit") or {}).get("keep_p") is None)

    print("=" * 104)
    print("CASCADE REPLAY — describer->auditor gate")
    print("=" * 104)
    print(f"videos {len(vids)}   frames {TOT_FRAMES}   frames lacking a usable audit block {missing} "
          f"({missing/TOT_FRAMES:.1%})   companions on disk {TOT_PUB_DISK}")

    # ── CONTROLS ───────────────────────────────────────────────────────────────────────────────────────────
    # A control that cannot fail is not a control. The first version of this block called `select(recs, 0)`
    # TWICE and compared the results — but `select` is pure and does not mutate its inputs, so that assertion
    # was satisfied by construction and could only ever have caught non-determinism that does not exist. It
    # was the exact defect this project has banked twice (a check that never fires reports the same thing as
    # a check that always fires: nothing). Caught by an independent review, not by me.
    #
    # The two controls below BIND, because each one can be broken by a plausible bug in the code under test.
    base = {v["slug"]: select(v["recs"], 0) for v in vids}

    # CONTROL 1 — SATURATING GATE. Above the maximum possible describer score every frame is below the gate,
    # so the cascade degenerates to a PURE-DESCRIBER pipeline. Build that pipeline directly from
    # `keep_p`/`score_p` WITHOUT going through `verdict()`, and require frame-identical selection.
    # This is the only assertion here that exercises the gating branch itself: an off-by-one (`<=` for `<`),
    # a swapped return tuple, or reading `score_a` instead of `score_p` breaks it and passes everything else.
    SAT = 11                                   # > max companion_score (10), so no frame can be at/above it
    sat_bad = []
    for v in vids:
        pure = [dict(r, keep=bool((r.get("audit") or {}).get("keep_p")),
                     companion_score=(r.get("audit") or {}).get("score_p") or 0)
                for r in v["recs"]
                if isinstance((r.get("audit") or {}).get("score_p"), (int, float))
                and (r.get("audit") or {}).get("keep_p") is not None]
        pure += [r for r in v["recs"]           # un-gateable rows keep their reconciled verdict in both arms
                 if not isinstance((r.get("audit") or {}).get("score_p"), (int, float))
                 or (r.get("audit") or {}).get("keep_p") is None]
        if select(pure, 0)["idx"] != select(v["recs"], SAT)["idx"]:
            sat_bad.append(v["slug"])
    if sat_bad:
        print(f"\n!! CONTROL FAILED — a gate-{SAT} cascade is not equal to a pure-describer pipeline for: "
              + ", ".join(sat_bad))
        print("!! The gating branch in verdict() is wrong. All cascade numbers suppressed."); raise SystemExit(1)

    # CONTROL 2 — the baseline candidate build must match the pipeline's own, computed independently of
    # select()'s verdict indirection. Breaks if the candidate predicate or the dedup wiring drifts.
    mismatch = []
    for v in vids:
        direct = vi._dedup_companions([r for r in v["recs"]
                                       if r.get("keep") and r.get("companion_score", 0) >= vi.COMPANION_MIN
                                       and not (r.get("on_screen", "").startswith("ERROR")
                                                or r.get("keep_reason") == "CALL_FAIL")])
        if len(direct) != base[v["slug"]]["Q"]:
            mismatch.append(f"{v['slug']} direct={len(direct)} replay={base[v['slug']]['Q']}")
    if mismatch:
        print("\n!! CONTROL FAILED — gate-0 candidate set diverges from the pipeline's own build:")
        for m in mismatch:
            print("   " + m)
        raise SystemExit(1)
    BASE_PUB = sum(b["pub"] for b in base.values())
    print(f"CONTROLS clean — a saturating gate degenerates to a pure-describer pipeline exactly, and gate 0"
          f"\n                 reproduces the pipeline's own candidate build ({BASE_PUB} prospective companions).\n")

    # ── what the auditor actually did, over the WHOLE corpus ──────────────────────────────────────────────
    resc = vet = 0
    for v in vids:
        for r in v["recs"]:
            a = r.get("audit") or {}
            if a.get("keep_p") is None:
                continue
            if a["keep_p"] is False and r.get("keep"):
                resc += 1
            if a["keep_p"] is True and not r.get("keep"):
                vet += 1
    print(f"auditor overturned the describer on {resc + vet} of {TOT_FRAMES} frames "
          f"({(resc+vet)/TOT_FRAMES:.1%}) — {resc} rescues, {vet} vetoes")

    # ── A: rescues among ON-DISK published companions, by gate ────────────────────────────────────────────
    by_frame = []
    for v in vids:
        idx = {}
        for r in v["recs"]:
            for k in (r.get("frame"), r.get("tc"), r.get("idx")):
                if k is not None:
                    idx.setdefault(k, r)
        for e in v["published"]:
            rec = idx.get(e.get("frame")) or idx.get(e.get("tc")) or idx.get(e.get("idx"))
            by_frame.append((v["slug"], e.get("tc"), rec))
    unmatched = sum(1 for _, _, r in by_frame if r is None)

    print("\n" + "=" * 104)
    print("A. AUDITOR RESCUES AMONG COMPANIONS ACTUALLY PUBLISHED  (denominator = %d on disk%s)"
          % (TOT_PUB_DISK, f", {unmatched} unmatched" if unmatched else ""))
    print("=" * 104)
    dist = Counter((r.get("audit") or {}).get("score_p") for _, _, r in by_frame if r)
    print("describer score of published companions: "
          + "  ".join(f"{k}:{v}" for k, v in sorted(dist.items(), key=lambda kv: (kv[0] is None, kv[0]))))
    # AUDIT-DEPENDENT, not merely "rescued": a frame owes its publication to the auditor either because the
    # describer REJECTED it (keep_p False) or because the describer kept it but scored it BELOW
    # COMPANION_MIN and only the auditor's raise qualified it. Counting only the first undercounts, and
    # Section E counts both — an inconsistency between two sections measuring the same thing. On this corpus
    # the second kind is empty, so no number moves; the definition is fixed so it stays right when one appears.
    def _audit_dependent(r):
        a_ = r.get("audit") or {}
        sp, kp = a_.get("score_p"), a_.get("keep_p")
        if kp is False:
            return True
        return kp is True and isinstance(sp, (int, float)) and sp < vi.COMPANION_MIN

    rescued = [(s, tc, (r.get("audit") or {}).get("score_p")) for s, tc, r in by_frame
               if r and _audit_dependent(r)]
    print(f"published only because of the auditor (reject overturned, or score raised past MIN): {len(rescued)} "
          f"({len(rescued)/max(1,TOT_PUB_DISK):.1%})")
    for s, tc, sp in sorted(rescued, key=lambda x: x[2] if x[2] is not None else -1):
        print(f"    describer score {sp:>2}   {tc:>6}   {s[:56]}")

    # ── B/C/D: the sweep ───────────────────────────────────────────────────────────────────────────────────
    print("\n" + "=" * 104)
    print("B-D. GATE SWEEP")
    print("=" * 104)
    print(f"{'gate':>5}{'auditor calls run':>19}{'skipped':>10}{'% of ALL calls':>16}"
          f"{'published':>11}{'vs base':>9}{'lost':>7}{'gained':>8}{'promo':>7}{'veto+':>7}{'disk':>7}")
    print("-" * 104)
    rows = []
    for g in gates:
        arms = {v["slug"]: select(v["recs"], g) for v in vids}
        ran = sum(1 for v in vids for r in v["recs"] if verdict(r, g)[2])
        skipped = TOT_FRAMES - ran
        pub = sum(a["pub"] for a in arms.values())
        lost = sum(len(base[s]["idx"] - arms[s]["idx"]) for s in arms)
        gained = sum(len(arms[s]["idx"] - base[s]["idx"]) for s in arms)
        # Split the gains by MECHANISM rather than asserting one. A frame already in the baseline
        # candidate list only rose in rank (a substitute); one that was not a candidate at all is a
        # genuine veto-add. Asserting the second while observing the first is how a downgrade gets
        # reported as an offsetting benefit.
        promo = veto_add = 0
        for s in arms:
            bcand = {r["idx"] for r in base[s]["cands"]}
            for i in arms[s]["idx"] - base[s]["idx"]:
                if i in bcand:
                    promo += 1
                else:
                    veto_add += 1
        # on-disk loss: a published companion whose describer score is below the gate AND which the describer
        # itself rejected would never have reached the candidate pool at all.
        disk_loss = sum(1 for _, _, r in by_frame
                        if r and _audit_dependent(r)
                        and isinstance((r.get("audit") or {}).get("score_p"), (int, float))
                        and (r.get("audit") or {}).get("score_p") < g)
        # total model calls = 1 describer on every frame + 1 auditor on every non-skipped frame
        total_calls = 2 * TOT_FRAMES
        rows.append((g, ran, skipped, skipped / total_calls, pub, pub - BASE_PUB, lost, gained, disk_loss))
        print(f"{g:>5}{ran:>19}{skipped:>10}{skipped/total_calls:>15.1%}{pub:>11}{pub-BASE_PUB:>+9}"
              f"{lost:>7}{gained:>8}{promo:>7}{veto_add:>7}{disk_loss:>7}")
    print("-" * 104)
    print("published = sum of min(cap, |candidates|) — the SAME measurement in every arm, which is the whole")
    print("point: `gc()` publishes `cands[:cap]`, so an unclamped `cap` is not a publishable count.")

    # validity: skipped calls must be non-decreasing in gate, or the instrument is coupled to something else
    sk = [r[2] for r in rows]
    if sorted(gates) == gates and any(b < a for a, b in zip(sk, sk[1:])):
        print("\n!! MONOTONICITY VIOLATED — a higher gate skipped FEWER calls. The instrument is coupled; "
              "treat every number above as unreliable.")

    # ── E: WHY the loss happens — and why 2.8% was measured against a cap that no longer exists ───────────
    print("\n" + "=" * 104)
    print("E. LOSS MECHANISM — what the skipped auditor calls were actually doing")
    print("=" * 104)
    for g in gates:
        if g == 0:
            continue
        flip = up = 0
        for v in vids:
            for r in v["recs"]:
                a = r.get("audit") or {}
                sp, kp = a.get("score_p"), a.get("keep_p")
                if not isinstance(sp, (int, float)) or kp is None or sp >= g:
                    continue
                if (r.get("keep") and r.get("companion_score", 0) >= vi.COMPANION_MIN
                        and not (kp and sp >= vi.COMPANION_MIN)):
                    if kp:
                        up += 1          # describer said keep, but scored it too low to qualify
                    else:
                        flip += 1        # describer rejected it outright; only the auditor saved it
        print(f"  gate {g}: {flip + up:>3} qualified frames lost — {flip:>3} because the auditor had OVERTURNED "
              f"a describer reject, {up:>3} because it had RAISED the score past COMPANION_MIN")

    # The counts above are CANDIDATE-POOL level. What actually matters is the PUBLISHED level, and the two
    # tell different stories: a frame can be lost from publication without its own verdict having changed at
    # all, because the pool shrank, Q shrank, and the cap contracted underneath it. Splitting the published
    # losses by whether the frame's OWN verdict degraded is the difference between blaming the gate and
    # blaming the cap — and on this corpus it is an even split.
    print("\n  PUBLISHED-level losses, split by whether the lost frame's OWN verdict changed:")
    print(f"  {'gate':>5}{'lost':>7}{'direct':>9}{'collateral':>13}   (direct = this frame's own verdict "
          f"degraded; collateral = cap contraction)")
    for g in gates:
        if g == 0:
            continue
        direct = collat = 0
        for v in vids:
            byidx = {r["idx"]: r for r in v["recs"]}
            for i in base[v["slug"]]["idx"] - select(v["recs"], g)["idx"]:
                a = byidx[i].get("audit") or {}
                sp, kp = a.get("score_p"), a.get("keep_p")
                gated = isinstance(sp, (int, float)) and kp is not None and sp < g
                if gated and not (kp and sp >= vi.COMPANION_MIN):
                    direct += 1          # the gate demoted this very frame
                else:
                    collat += 1          # audited identically in BOTH arms; killed by the smaller cap
        print(f"  {g:>5}{direct+collat:>7}{direct:>9}{collat:>13}")
    print("""
    ★ HALF OF THE GATE-5 LOSS IS COLLATERAL. Five of the ten frames dropped at gate 5 have score_p 9 and
    keep_p True — the describer and auditor AGREED on them, they are audited identically under the cascade,
    and they are cut purely because Q shrank and the cap contracted beneath them. So the cascade's cost is
    NOT mostly "the auditor's rescues were skipped"; it is half that and half the cap-Q feedback documented
    in the next section. An earlier draft of this script asserted the first half as the whole explanation.

    The related claim — that the 2.8% on-disk rescue rate understates the cost because the shipped cap
    publishes deeper into where the rescues live — is directionally right and much weaker than it sounds.
    The comparable prospective figure is computed below, and the gap is about half a point, not a
    reframing. Rescue concentration is a minor term; cap contraction is the story.""")

    prosp_resc = sum(1 for v in vids for r in base[v["slug"]]["sel"]
                     if (r.get("audit") or {}).get("keep_p") is False)
    print(f"    rescue-dependent share of published companions: "
          f"{prosp_resc}/{BASE_PUB} = {prosp_resc/BASE_PUB:.1%} prospective   vs   "
          f"{len(rescued)}/{TOT_PUB_DISK} = {len(rescued)/max(1,TOT_PUB_DISK):.1%} on disk")

    # ── the cap-Q interaction, measured honestly ──────────────────────────────────────────────────────────
    print("\n" + "=" * 104)
    print("★ CAP-Q INTERACTION — does the cascade shrink the cap as well as the candidate pool?")
    print("=" * 104)
    print("The adaptive cap is keyed on Q, and Q is computed from what got SCORED. If an upstream reduction")
    print("shrinks Q it shrinks the cap, so a video can publish less even when every published frame survived.")
    print("`sum cap` is the CEILING and `published` is what is actually kept; the 191->183 figure quoted the")
    print("first while labelling it the second. Both are shown here so they can never be confused again.\n")
    print(f"{'gate':>5}{'sum Q':>8}{'sum cap':>9}{'published':>11}{'videos w/ smaller cap':>23}"
          f"{'videos w/ larger cap':>22}")
    print("-" * 104)
    for g in gates:
        arms = {v["slug"]: select(v["recs"], g) for v in vids}
        sq = sum(a["Q"] for a in arms.values())
        sc = sum(a["cap"] for a in arms.values())
        pub = sum(a["pub"] for a in arms.values())
        dn = sum(1 for s in arms if arms[s]["cap"] < base[s]["cap"])
        up = sum(1 for s in arms if arms[s]["cap"] > base[s]["cap"])
        print(f"{g:>5}{sq:>8}{sc:>9}{pub:>11}{dn:>23}{up:>22}")
    print("-" * 104)
    print("No cap moves UP anywhere in this corpus. It could in principle — skipping the auditor also")
    print("discards its VETOES, so a frame it had removed would stay in the pool — but that needs")
    print("COMPANION_MIN <= score_p < gate, which is empty below gate 8. Every gain observed here is a")
    print("rank PROMOTION into a slot a better frame vacated, not a frame the cascade rescued.")

    # ── per-video, so a single video cannot carry the aggregate ───────────────────────────────────────────
    G = 5 if 5 in gates else gates[-1]
    arms5 = {v["slug"]: select(v["recs"], G) for v in vids}
    # Trigger on the SELECTED SET changing, not merely on the count changing. A video that swaps one frame
    # for another moves zero counts and is invisible to a count-based filter — so a reader tracing the N
    # strict losses through this block would find only N-1 and have no way to know why. A block that hides
    # a loss because it was offset is the same defect as a net that hides one.
    moved = [(s, base[s]["Q"], arms5[s]["Q"], base[s]["cap"], arms5[s]["cap"], base[s]["pub"], arms5[s]["pub"],
              len(base[s]["idx"] - arms5[s]["idx"]), len(arms5[s]["idx"] - base[s]["idx"]))
             for s in arms5 if base[s]["idx"] != arms5[s]["idx"] or arms5[s]["cap"] != base[s]["cap"]]
    print(f"\nPER-VIDEO at gate {G} — every video whose SELECTED SET or cap moved ({len(moved)} of "
          f"{len(vids)}); 'lost/gained' sum to the corpus totals so every loss is traceable here:")
    if not moved:
        print("    none — the aggregate is flat because every video is flat, not because losses cancelled.")
    for s, q0, q1, c0, c1, p0, p1, lo, ga in sorted(moved, key=lambda x: x[6] - x[5]):
        flat = "   <- churn only, counts flat" if p0 == p1 and lo else ""
        print(f"    {s[:44]:<46} Q {q0:>3}->{q1:<3} cap {c0:>2}->{c1:<2} pub {p0:>2}->{p1:<2} "
              f"lost {lo} gained {ga}{flat}")
    tl, tg = sum(m[7] for m in moved), sum(m[8] for m in moved)
    print(f"    totals: lost {tl}, gained {tg}  (must equal the gate-{G} sweep row)")
    conc = sum(abs(p1 - p0) for *_, p0, p1, _lo, _ga in moved)
    if conc:
        top = max(moved, key=lambda x: abs(x[6] - x[5]))
        print(f"    concentration: the largest single video accounts for {abs(top[6]-top[5])}/{conc} "
              f"of all published-count movement.")

    # ── VERDICT against the threshold that was pre-registered BEFORE the measurement ──────────────────────
    THRESH = 0.02          # pre-registered 2026-08-01, before any cascade number existed
    print("\n" + "=" * 104)
    print(f"VERDICT vs the PRE-REGISTERED loss threshold ({THRESH:.0%} of published companions)")
    print("=" * 104)
    print("PROVENANCE, stated honestly: the 2% threshold's first COMMITTED appearance (c927856) is in the")
    print("same commit as a favourable cascade result, and no earlier artifact exists in git or memory. So")
    print("'pre-registered' rests on my word, not the audit trail. What IS checkable: the threshold was NOT")
    print("moved when moving it would have helped — the denominator migrated from the historical 109 to the")
    print("prospective 183, which flipped gate 5 from PASS to FAIL, and the bar stayed at 2%.\n")
    print("THREE readings are scored, because the answer depends on which one you take and hiding that would")
    print("be the whole game:")
    print("  NET        published(base) - published(cascade). The most lenient. Counts a promotion as an")
    print("             offsetting benefit, which it is not — see the gain-mechanism split above.")
    print("  STRICT     frames published in baseline and no longer published. Matches the threshold's own")
    print("             wording ('loses N of the published set') and how it was applied the first time.")
    print("  SYMMETRIC  lost + gained. The metric implied by LIMITS #1, which states the target as AGREEMENT")
    print("             WITH THE INCUMBENT — identical output for fewer calls. Any changed frame is a")
    print("             disagreement, in either direction. The harshest, and the most consistent with the")
    print("             stated goal of a pure cost cut.\n")
    print(f"{'gate':>5}{'calls saved':>13}{'published':>11}{'net':>7}{'strict':>9}{'symm':>7}"
          f"{'  NET':>7}{'STRICT':>9}{'SYMM':>7}")
    print("-" * 104)
    verdicts = {}
    for g, ran, skipped, frac, pub, delta, lost, gained, disk in rows:
        net = (BASE_PUB - pub) / BASE_PUB if BASE_PUB else 0.0
        strict = lost / BASE_PUB if BASE_PUB else 0.0
        symm = (lost + gained) / BASE_PUB if BASE_PUB else 0.0
        v = tuple("PASS" if x <= THRESH else "FAIL" for x in (net, strict, symm))
        verdicts[g] = (frac, net, strict, symm, v)
        print(f"{g:>5}{frac:>12.1%}{pub:>11}{net:>7.1%}{strict:>9.1%}{symm:>7.1%}"
              f"{v[0]:>7}{v[1]:>9}{v[2]:>7}")
    print("-" * 104)
    # A gate is only worth recommending if it clears EVERY reading. One that clears some is a coin-flip
    # dressed as a result, and saying which readings it fails is the point.
    robust = [(g, d[0]) for g, d in verdicts.items() if d[0] > 0 and set(d[4]) == {"PASS"}]
    split = [(g, d[4]) for g, d in verdicts.items() if d[0] > 0 and len(set(d[4])) > 1]
    dead = [g for g, d in verdicts.items() if d[0] > 0 and set(d[4]) == {"FAIL"}]
    if robust:
        g, frac = max(robust, key=lambda x: x[1])
        print(f"CLEARS EVERY READING: gate {g}, saving {frac:.1%} of all model calls.")
    else:
        print("NO gate clears all three readings at a non-zero saving.")
    for g, v in sorted(split):
        print(f"  gate {g}: READING-DEPENDENT — net {v[0]}, strict {v[1]}, symmetric {v[2]}. Not a result.")
    if dead:
        print(f"  gate(s) {sorted(dead)}: FAIL under every reading. Refuted robustly.")
    # Fragility: with a denominator this small the bar is a fraction of a frame, so a PASS that sits within
    # one or two frames of the line is not distinguishable from a FAIL by this corpus.
    bar = THRESH * BASE_PUB
    print(f"\nFRAGILITY: {THRESH:.0%} of {BASE_PUB} published is {bar:.2f} frames, so every verdict near the")
    print(f"bar turns on +/-1 frame. Any gate whose loss is within one frame of {bar:.2f} is UNPROVEN at this")
    print("corpus size, not passing. Check the per-video block: if the movement sits in one video, the")
    print("aggregate is that video's result, not the corpus's.")

    # ── limits ─────────────────────────────────────────────────────────────────────────────────────────────
    print("\n" + "=" * 104)
    print("LIMITS — read before quoting any number above")
    print("=" * 104)
    print("""1. PROXY LABEL. There is no ground truth for "should this frame have been published". The reconciled
       describer+auditor verdict is treated as correct, so this measures AGREEMENT WITH THE INCUMBENT, not
       quality. That is the right target for a pure cost cut (identical output, fewer calls) and it would be
       circular the moment anyone claims the cascade IMPROVES selection. It does not, and cannot show that.
    2. NON-RANDOM CORPUS. 34 videos chosen by what the research pipeline happened to ingest; heavily
       screencast/technical. Frame-level yield varies over an order of magnitude across them, so the aggregate
       is dominated by whichever videos are dense. Read the per-video block, not only the totals.
    3. THE ON-DISK DENOMINATOR IS HISTORICAL. Most `companions.json` files were written under the
       pre-2026-08-01 duration-only cap. The rescue RATE over them is a fair estimate of how often the auditor
       changes a published outcome; the published COUNTS in them are not comparable to the prospective arm.
    4. SKIPPED CALLS ARE ESTIMATED FROM STORED SCORES. A real cascade decides live, and a describer whose
       score distribution shifts (different content, a model update) shifts the saving with it. The saving is
       a property of THIS corpus under THIS describer.
    5. THE BASELINE MIXES TWO RECONCILIATION POLICIES. 1,790 of 3,014 records (30 of 34 videos) were
   reconciled under the pre-2026-08-01 OR/max rule, where an auditor veto could not lower a describer keep;
   vetoes were structurally impossible there. Immaterial to every gate <= 7 measured here (all 33 vetoes sit
   at score_p >= 7 and so are audited in BOTH arms regardless), but the incumbent this replay measures
   agreement WITH is not one policy — it is a mixture, one half of which is retired.
6. NOT MEASURED HERE: whether the auditor's presence changes the DESCRIBER's behaviour (it does not — the
       describer runs first and is unaware), and downstream synthesis quality, which nothing in this replay
       touches.""")


if __name__ == "__main__":
    raise SystemExit(main() or 0)
