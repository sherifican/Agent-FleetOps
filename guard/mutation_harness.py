#!/usr/bin/env python3
"""MUTATION HARNESS — prove every guard can actually go RED.

For each declared mutation this harness:
  1. copies the code under test into a throwaway SANDBOX (the real tree is never edited),
  2. applies one small, surgical, semantic mutation to the sandbox copy,
  3. runs the paired guard from the sandbox and asserts it now FAILS,
  4. restores the pristine sandbox copy and asserts the guard passes again.

KILLED   = the guard caught the reintroduced bug (good).
SURVIVED = the guard stayed green while the property it claims to protect was broken —
           that guard is vacuous for that property, and that is a finding, not a nuisance.

Integrity rules baked in:
  * The working tree is NEVER modified: guards run from a temp-dir copy whose two hardcoded
    .. sys.path inserts are retargeted to the sandbox. `git status
    --porcelain` is captured at start and asserted byte-identical at exit.
  * A SURVIVED verdict requires exit 0 AND the guard's own all-pass marker in stdout; exit 0
    without the marker is an ANOMALY (harness/environment bug), never a survivor.
  * test_perceptual_prefilter exits 2 for "UNMEASURED (no corpus)" — that counts as ABSTAINED,
    not KILLED: a guard that declined to assert anything did not catch the bug.
  * test_cascade_replay wraps its live-corpus regression block in a bare try/except that prints
    "skip  corpus unavailable" — a mutant that crashes there is being SWALLOWED, so that string
    in a mutant run is flagged on the result line.
  * A mutation whose target string does not match exactly once is a hard ERROR, never a verdict.
  * Every SURVIVED mutation is additionally swept against the other five guards, so the report
    can distinguish "vacuous here, caught elsewhere" from "no guard would have noticed".

Run:  python3 guard/mutation_harness.py [-v] [--only ID [ID ...]]
Exit: 0 all killed · 1 at least one SURVIVED (or ABSTAINED/ANOMALY) · 2 harness integrity
      failure · 3 working tree modified (should be impossible)
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GUARDS = {
    "cascade":   ("guard/test_cascade_replay.py",       "all cascade-replay guards pass"),
    "cap":       ("guard/test_companion_cap.py",        "ALL CHECKS PASSED"),
    "motion":    ("guard/test_motion_gate.py",          "ALL CHECKS PASSED"),
    "semantic":  ("guard/test_semantic_selector.py",    "ALL CHECKS PASSED"),
    "spread":    ("guard/test_companion_spread.py",     "\nPASS"),
    "prefilter": ("guard/test_perceptual_prefilter.py", "PREFILTER HAS TEETH"),
    "txn_mode":  ("guard/tests/test_artifact_txn_mode.py", "TXN MODE PRESERVED - ALL CHECKS PASSED"),
    "scrub":     ("guard/tests/test_scrub_arm.py",     "SCRUB ARM HAS TEETH - ALL CHECKS PASSED"),
    "pid_bind":  ("guard/tests/test_verify_running_build_pid.py", "PID BIND HAS TEETH - ALL CHECKS PASSED"),
    "population": ("guard/tests/test_population_arm.py", "POPULATION ARM HAS TEETH - ALL CHECKS PASSED"),
    "one_writer": ("guard/tests/test_one_writer_gate.py", "ONE WRITER GATE HAS TEETH - ALL CHECKS PASSED"),
    "reader":    ("guard/tests/test_reader_record.py",   "READER STATES DISTINGUISHED - ALL CHECKS PASSED"),
    "reconcile": ("guard/tests/test_reconcile_gate.py",  "RECONCILE GATE HAS TEETH - ALL CHECKS PASSED"),
    "brief":     ("guard/tests/test_brief_scan.py",      "BRIEF SCAN HAS TEETH - ALL CHECKS PASSED"),
    "curation":  ("guard/tests/test_curation_gate.py",   "CURATION GATE HAS TEETH - ALL CHECKS PASSED"),
    "org_lint":  ("guard/tests/test_org_lint.py",        "ORG LINT HAS TEETH - ALL CHECKS PASSED"),
    "roster":    ("guard/tests/test_roster_check.py",    "ROSTER CHECK HAS TEETH - ALL CHECKS PASSED"),
}
# files copied into the sandbox (guards + code under test), relative to REPO
FILES = [
    "vision_ingest.py", "vision_semantic.py", "vision_motion.py",
    "vision_measure/replay_cascade.py",
    "guard/artifact_txn.py",
    "guard/scrub_arm.py",
    "guard/tests/fixtures/scrub_plant.txt",
    "guard/verify_running_build_pid.py",
    "guard/population_arm.py",
    "guard/one_writer_gate.py",
    "guard/reader_record.py",
    "guard/reconcile_gate.py",
    "guard/brief_scan.py",
    "guard/curation_gate.py",
    "guard/org_lint.py",
    "templates/roster-check.sh.template",
] + [path for path, _ in GUARDS.values()]


def verify_anchors():
    """--verify-anchors: does every mutation's needle still bind EXACTLY ONCE in its file?

    a peer agent's finding (2026-08-05), and it applies here because we anchor on literals too:
    **a STALE NEEDLE DOES NOT FAIL — it silently stops proving the guard has teeth, and the guard stays
    green either way.** Their prover had a needle pinned to a changelog line carrying a version number;
    the line moved at a release and the mutation had been binding zero times ever since. No symptom.
    Cheap enough to run on every edit, unlike the full prove-teeth pass. (I proposed exactly this split
    to them weeks ago, they built it, it caught a real one — and I never built it here until now.)

    Exit: 0 all anchors bind once · 1 one or more stale/ambiguous · 2 a file could not be read.
    """
    import collections
    bad, unreadable = [], []
    cache = {}
    for m in MUTATIONS:
        f = m["file"]
        if f not in cache:
            try:
                cache[f] = open(os.path.join(REPO, f), encoding="utf-8").read()
            except OSError as e:
                cache[f] = None; unreadable.append((f, str(e)))
        src = cache[f]
        if src is None:
            continue
        n = src.count(m["old"])
        if n != 1:
            bad.append((m["id"], f, n))
    print(f"verify-anchors — {len(MUTATIONS)} mutation(s) across {len(cache)} file(s)")
    for mid, f, n in bad:
        print(f"  ⛔ {mid:6} needle binds {n}x (need exactly 1) in {f}")
    for f, e in unreadable:
        print(f"  ? {f}: UNREADABLE — {e}")
    if unreadable:
        print("2 UNMEASURED — a file could not be read, so some anchors were never checked")
        return 2
    if bad:
        print(f"STALE: {len(bad)} anchor(s) no longer bind once. Those mutations prove NOTHING.")
        return 1
    print("  all anchors bind exactly once — every mutation still points at live code")
    return 0


def M(mid, guard, path, desc, claims, old, new):
    return {"id": mid, "guard": guard, "file": path, "desc": desc, "claims": claims,
            "old": old, "new": new}


# ────────────────────────────────────────────────────────────────────────────────────────────
# THE MUTATIONS — each reintroduces the bug a specific guard check claims to make impossible.
# `claims` names the guard property under test, so a SURVIVED line reads as a finding.
# ────────────────────────────────────────────────────────────────────────────────────────────
RC = "vision_measure/replay_cascade.py"
VI = "vision_ingest.py"
VM = "vision_motion.py"
VS = "vision_semantic.py"

MUTATIONS = [
    # ── cascade replay: verdict() ───────────────────────────────────────────────────────────
    M("CR1", "cascade", RC, "gate boundary >= becomes >: score_p == gate now skips the auditor",
      "'score_p == gate is ABOVE the gate (>=, not >)'",
      "    if gate > 0 and isinstance(sp, (int, float)) and kp is not None and sp < gate:",
      "    if gate > 0 and isinstance(sp, (int, float)) and kp is not None and sp <= gate:"),
    M("CR2", "cascade", RC, "gated branch returns the RECONCILED verdict — the cascade changes nothing",
      "'below gate uses the describer's verdict'",
      "        return bool(kp), sp, False",
      "        return bool(rec.get(\"keep\")), rec.get(\"companion_score\", 0) or 0, False"),
    M("CR3", "cascade", RC, "missing audit fields default to (0, False) instead of failing safe",
      "'missing score_p / missing audit key falls back to reconciled (fail-safe)'",
      "\n    sp, kp = a.get(\"score_p\"), a.get(\"keep_p\")\n",
      "\n    sp, kp = a.get(\"score_p\", 0), a.get(\"keep_p\", False)\n"),
    M("CR4", "cascade", RC, "keep_p None no longer forces the reconciled fallback",
      "'keep_p None falls back to reconciled'",
      "    if gate > 0 and isinstance(sp, (int, float)) and kp is not None and sp < gate:",
      "    if gate > 0 and isinstance(sp, (int, float)) and sp < gate:"),
    M("CR5", "cascade", RC, "auditor_ran flag inverted — call-savings bookkeeping lies",
      "'auditor calls skipped is non-decreasing in gate' (monotonicity)",
      "        return bool(kp), sp, False\n"
      "    return bool(rec.get(\"keep\")), rec.get(\"companion_score\", 0) or 0, True",
      "        return bool(kp), sp, True\n"
      "    return bool(rec.get(\"keep\")), rec.get(\"companion_score\", 0) or 0, False"),
    # ── cascade replay: select() ────────────────────────────────────────────────────────────
    M("CR6", "cascade", RC, "candidate qualification >= COMPANION_MIN becomes >",
      "'gate 0 keeps all four' (a score-7 frame qualifies)",
      "        if (keep and score >= vi.COMPANION_MIN",
      "        if (keep and score > vi.COMPANION_MIN"),
    M("CR7", "cascade", RC, "qualification reads the RECONCILED keep, not the gated verdict",
      "'skipped veto adds the frame back above its score_p'",
      "        if (keep and score >= vi.COMPANION_MIN",
      "        if (r.get(\"keep\") and score >= vi.COMPANION_MIN"),
    M("CR8", "cascade", RC, "publication slice off-by-one: [:cap] -> [:cap+1]",
      "'published never exceeds the cap' — NOTE the fixtures never have Q > cap, "
      "so only the live-corpus regression block can catch this",
      "    sel = ordered[:cap]                      # exactly what gc() publishes",
      "    sel = ordered[:cap + 1]                  # exactly what gc() publishes"),
    # CR9/CR10 targeted replay_cascade's own ORDERING COPY until 2026-08-02. That copy is gone — the
    # replay now imports vision_ingest._order_companions — so these re-anchor onto the single
    # implementation. Mutating it must be caught by BOTH the spread guard and the cascade corpus pin.
    M("CR9", "cascade", VI, "score tiers ordered ASCENDING — worst candidates publish first",
      "candidate ordering; fixtures never have cap < Q, so only the corpus block can see it",
      "    for score in sorted(by_score, reverse=True):",
      "    for score in sorted(by_score):"),
    M("CR10", "cascade", VI, "temporal-spread tie-break dropped (earliest-first) in the shared ordering",
      "within-tier temporal spread; observable only when cap < Q (corpus block)",
      "            nxt = max(tier, key=lambda r: min(abs(r[\"ts\"] - p[\"ts\"]) for p in picked))",
      "            nxt = tier[0]"),
    # ── companion cap: vision_ingest._companion_cap ─────────────────────────────────────────
    M("CC1", "cap", VI, "overage tier loosened: score >= 9 counts as a must-see 10",
      "'a wall of 9s does not trigger the overage'",
      "    must_see = [r for r in cands if r.get(\"companion_score\", 0) >= 10]",
      "    must_see = [r for r in cands if r.get(\"companion_score\", 0) >= 9]"),
    M("CC2", "cap", VI, "hard ceiling removed from the cap formula — overage can exceed COMPANION_MAX",
      "'ceiling holds at COMPANION_MAX' — but that check feeds zero 10s, so the overage path is dead there",
      "    cap = min(COMPANION_MAX, max(target, min(len(must_see), target + 3)))",
      "    cap = max(target, min(len(must_see), target + 3))"),
    M("CC3", "cap", VI, "overage widened: target+3 -> target+4",
      "the overage AMOUNT (a cap can now run one frame hotter)",
      "    cap = min(COMPANION_MAX, max(target, min(len(must_see), target + 3)))",
      "    cap = min(COMPANION_MAX, max(target, min(len(must_see), target + 4)))"),
    M("CC4", "cap", VI, "content term halved: 2 + sqrt(Q)/2",
      "'matches the offline replay over the five real videos'",
      "    target = max(dur_floor, min(COMPANION_MAX, 2 + int(math.sqrt(Q))))",
      "    target = max(dur_floor, min(COMPANION_MAX, 2 + int(math.sqrt(Q) / 2)))"),
    M("CC5", "cap", VI, "duration floor dropped — a long sparse video loses its minimum",
      "'the new cap is NEVER lower than the old one' + 'duration floor still lifts'",
      "    target = max(dur_floor, min(COMPANION_MAX, 2 + int(math.sqrt(Q))))",
      "    target = min(COMPANION_MAX, 2 + int(math.sqrt(Q)))"),
    M("CC6", "cap", VI, "Q wraps at 64 (stand-in for any non-monotone Q accounting bug)",
      "'cap never decreases as Q grows'",
      "    Q = len(cands)",
      "    Q = len(cands) % 64"),
    M("CC7", "cap", VI, "target misreported as cap in the return tuple",
      "'wall of 9s' check compares cap9 == t9 — two outputs of the same call",
      "    return cap, target, Q, dur_floor",
      "    return cap, cap, Q, dur_floor"),
    # ── motion gate: vision_motion ──────────────────────────────────────────────────────────
    M("MG1", "motion", VM, "min-shift boundary < becomes <=: an exactly-2px pan is no longer motion",
      "'FIRES on translation' — is the smallest real pan in the fixtures?",
      "    if abs(dx) + abs(dy) < MIN_SHIFT:",
      "    if abs(dx) + abs(dy) <= MIN_SHIFT:"),
    M("MG2", "motion", VM, "absolute-residual aliasing bound disabled",
      "'periodic content flipped -> STATIC' (the bug this guard itself found on 2026-08-01)",
      "    if resid > MAX_RESID_ABS:",
      "    if resid > float(\"inf\"):"),
    M("MG3", "motion", VM, "MAX_RESID_ABS default loosened 8.0 -> 80.0",
      "the aliasing fixture leaves resid 14.8 — does the guard pin the VALUE?",
      "os.environ.get(\"VI_MOTION_RESID_ABS\", \"8.0\")",
      "os.environ.get(\"VI_MOTION_RESID_ABS\", \"80.0\")"),
    M("MG4", "motion", VM, "noise floor inverted: below MIN_RAW now reads as MOTION",
      "'identical frames -> STATIC' / 'tiny codec noise -> STATIC'",
      "    if raw < MIN_RAW:\n        return False                                # nothing meaningfully changed",
      "    if raw < MIN_RAW:\n        return True"),
    M("MG5", "motion", VM, "residual-ratio test inverted: pans read STATIC, in-place changes read MOTION",
      "'FIRES on translation' (every true-positive check)",
      "    return (resid / raw) <= RESID_MAX               # translation accounts for the change",
      "    return (resid / raw) >= RESID_MAX"),
    M("MG6", "motion", VM, "RESID_MAX default loosened 0.45 -> 0.9",
      "do the fail-open fixtures (zoom/rotation/new-content) sit between 0.45 and 0.9?",
      "os.environ.get(\"VI_MOTION_RESID\", \"0.45\")",
      "os.environ.get(\"VI_MOTION_RESID\", \"0.9\")"),
    M("MG7", "motion", VM, "run-collapse keeps the FIRST frame of a run, not the settle frame",
      "'a motion run collapses to its LAST frame'",
      "            keep.add(run[-1])                       # settle frame of the run that just ended",
      "            keep.add(run[0])"),
    M("MG8", "motion", VM, "a run ending at the sequence TAIL keeps its first frame, not the settle frame",
      "tail-run settle semantics — the fixture's tail run is 1 frame long",
      "        keep.add(run[-1])                           # a run ending at the sequence tail still settles",
      "        keep.add(run[0])"),
    M("MG9", "motion", VM, "unreadable-frame special case dropped from classify_sequence",
      "'unreadable frame -> STATIC, never dropped'",
      "        if prev is None or g is None:",
      "        if prev is None:"),
    M("MG10", "motion", VM, "first-frame special case dropped from classify_sequence",
      "'first frame is never MOTION (no predecessor)'",
      "        if prev is None or g is None:",
      "        if g is None:"),
    # ── semantic selector: vision_semantic ──────────────────────────────────────────────────
    M("SS1", "semantic", VS, "facility-location gain replaced by raw popularity (no diminishing returns)",
      "'the unique frame is selected' — novelty via coverage",
      "    return np.maximum(S, cur[:, None]).sum(axis=0) - cur.sum()",
      "    return S.sum(axis=0)"),
    M("SS2", "semantic", VS, "bin reservation takes the bin's FIRST member instead of the argmax",
      "'bins RESERVE, they do not CHOOSE' — merit choice inside a bin",
      "        pick = int(members[int(np.argmax(g[members]))])",
      "        pick = int(members[0])"),
    M("SS3", "semantic", VS, "pass-2 pick takes the first eligible index instead of the argmax",
      "global representativeness spending — which check exercises pass 2 at all?",
      "        pick = int(np.argmax(g))",
      "        pick = int(np.flatnonzero(np.isfinite(g))[0])"),
    M("SS4", "semantic", VS, "bin-reservation pass skipped entirely",
      "'every populated bin gets a slot' (temporal coverage as a contract)",
      "    for b in range(n_bins):\n        if len(sel) >= budget:",
      "    for b in range(0):\n        if len(sel) >= budget:"),
    M("SS5", "semantic", VS, "n_bins-must-fit-budget clamp removed",
      "with SEM_BINS=12 > budget, late bins are never reserved (documented invariant)",
      "    n_bins = max(1, min(n_bins, budget))   # can never exceed the budget; reservations must fit",
      "    n_bins = max(1, n_bins)"),
    M("SS6", "semantic", VS, "OCR novelty inverted into familiarity (rewards already-seen text)",
      "'with OCR weight, the text-bearing frame is selected'",
      "        vals[i] = len(tk - seen) / len(tk)",
      "        vals[i] = len(tk & seen) / len(tk)"),
    M("SS7", "semantic", VS, "textless-frame protection removed — no-text frames get OCR-blended to near zero",
      "'a textless diagram still wins on coverage at high OCR weight'",
      "        out = np.where(has, blended, out)             # textless frames keep their coverage score untouched",
      "        out = blended"),
    M("SS8", "semantic", VS, "tie-break flipped to LAST maximal index in bin reservation",
      "'exact ties resolve by first index (stable)'",
      "        pick = int(members[int(np.argmax(g[members]))])",
      "        pick = int(members[len(members) - 1 - int(np.argmax(g[members][::-1]))])"),
    M("SS9", "semantic", VS, "select_union drops the OCR arm — the SHIPPING configuration halves",
      "select_union is the shipping entry point; which check covers it?",
      "    idx = sorted(set(a) | set(b), key=lambda i: timestamps[i])",
      "    idx = sorted(set(a), key=lambda i: timestamps[i])"),
    M("SS10", "semantic", VS, "budget >= n early return yields EMPTY instead of everything",
      "'budget larger than n returns everything'",
      "        return list(range(n)), {i: \"all\" for i in range(n)}",
      "        return [], {}"),
    # ── companion spread: vision_ingest.gc ordering ─────────────────────────────────────────
    M("SP1", "spread", VI, "gc() ordering reverted to the naive (score, -ts) earliest-wins key — "
      "the EXACT historical bug this guard exists for",
      "'selection is not confined to the first half' — but does the guard import the real code?",
      "    by_score = {}\n"
      "    for r in cands:\n"
      "        by_score.setdefault(r.get(\"companion_score\", 0), []).append(r)\n"
      "    ordered = []\n"
      "    for score in sorted(by_score, reverse=True):\n"
      "        tier = sorted(by_score[score], key=lambda r: r[\"ts\"])\n"
      "        if len(tier) <= 2:\n"
      "            ordered.extend(tier); continue\n"
      "        picked = [tier.pop(0)]                      # anchor on the tier's earliest\n"
      "        while tier:\n"
      "            nxt = max(tier, key=lambda r: min(abs(r[\"ts\"] - p[\"ts\"]) for p in picked))\n"
      "            tier.remove(nxt); picked.append(nxt)\n"
      "        ordered.extend(picked)\n"
      "    return ordered",
      "    return sorted(cands, key=lambda r: (r.get(\"companion_score\", 0), -r[\"ts\"]), reverse=True)"),
    M("SP2", "spread", VI, "gc() spread tie-break degraded to earliest-first within a tier",
      "within-tier temporal spread in the code that actually deletes frames",
      "            nxt = max(tier, key=lambda r: min(abs(r[\"ts\"] - p[\"ts\"]) for p in picked))",
      "            nxt = tier[0]"),
    # ── perceptual prefilter: vision_ingest ─────────────────────────────────────────────────
    M("PF1", "prefilter", VI, "hamming distance computed over OR instead of XOR — identity distance nonzero",
      "'identity: distance(f, f) == 0'",
      "    return bin(a ^ b).count(\"1\")",
      "    return bin(a | b).count(\"1\")"),
    M("PF2", "prefilter", VI, "anchor drifts: compare against the PREVIOUS frame, not the last KEPT frame",
      "the docstring's anti-drift property ('a slow drift still eventually registers')",
      "            dropped += 1\n            continue",
      "            dropped += 1; last_h = h\n            continue"),
    M("PF3", "prefilter", VI, "drop threshold boundary <= becomes <: distance exactly PHASH_DIST now survives",
      "the threshold BOUNDARY — no fixture sits at exactly PHASH_DIST",
      "        if last_h is not None and _hamming(h, last_h) <= dist:",
      "        if last_h is not None and _hamming(h, last_h) < dist:"),
    M("PF4", "prefilter", VI, "unreadable frame silently DROPPED instead of kept",
      "'unreadable frame is KEPT, not silently dropped'",
      "            kept.append(fr); last_h = None            # cannot compare -> keep, and reset the anchor",
      "            dropped += 1"),
    M("PF5", "prefilter", VI, "filter always matches — drops every comparable frame",
      "'NOT OVERBROAD: distinct frames all survive'",
      "        if last_h is not None and _hamming(h, last_h) <= dist:",
      "        if last_h is not None and _hamming(h, last_h) >= 0:"),
    M("PF6", "prefilter", VI, "filter never matches — completely inert",
      "'TEETH: 8 identical frames collapse to 1'",
      "        if last_h is not None and _hamming(h, last_h) <= dist:",
      "        if last_h is not None and _hamming(h, last_h) < 0:"),
    M("PF7", "prefilter", VI, "PHASH_DIST default loosened 6 -> 30",
      "'discrimination: distinct frames exceed the drop threshold' — is the VALUE pinned?",
      "os.environ.get(\"VI_PHASH_DIST\", \"6\")",
      "os.environ.get(\"VI_PHASH_DIST\", \"30\")"),
    # ── artifact transaction: mode preservation ─────────────────────────────────────────────
    M("TX1", "txn_mode", "guard/artifact_txn.py",
      "mode-preservation chmod dropped — a 0755 target rewritten through the transaction returns 0644",
      "'a rewrite preserves the target's permission bits' (0644 and 0700 likewise)",
      "                    os.chmod(tmp_path, stat.S_IMODE(os.stat(path).st_mode))\n",
      ""),
    # ── scrub arm: private material in public-bound bytes ───────────────────────────────────
    M("SA1", "scrub", "guard/scrub_arm.py",
      "maintainer profile with an ABSENT overlay returns a PASS instead of CANNOT_CHECK",
      "'an absent overlay is CANNOT_CHECK (2), never a pass' — else every fresh clone reads green",
      "            print(\"reads clean — the silent-clear problem inside its own fix. exit 2.\")\n"
      "            return 2",
      "            print(\"reads clean — the silent-clear problem inside its own fix. exit 2.\")\n"
      "            return 0"),
    M("SA2", "scrub", "guard/scrub_arm.py",
      "private-address baseline branch disabled — a planted address is no longer caught",
      "'every baseline rule flags its plant' — the private-material class, address rule",
      "r\"\\b(?:10\\.\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}\"",
      "r\"\\b(?:10X\\.\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}\""),
    M("SA3", "scrub", "guard/scrub_arm.py",
      "unix home-path branch disabled — a planted home directory is no longer caught",
      "'every baseline rule flags its plant' — the private-material class, home-path rule",
      "r\"(?:/home/|/Users/|C:\\\\Users\\\\)\"",
      "r\"(?:/homeX/|/Users/|C:\\\\Users\\\\)\""),
    M("SA5", "scrub", "guard/scrub_arm.py",
      "tilde home-path branch disabled — the shorthand for an account home is no longer caught",
      "'every baseline rule flags its plant' — the private-material class, tilde home-path rule",
      "r\"(?<![\\w.~-])~\"",
      "r\"(?<![\\w.~-])~X\""),
    M("SA4", "scrub", "guard/scrub_arm.py",
      "quoted-speech person-attribution subjects disabled — a quoted person ships silently",
      "'every baseline rule flags its plant' — the quoted-speech class",
      "(?:the owner|he|she)",
      "(?:the ownerX|heX|sheX)"),
    # -- batch-3 arms: each gate above must be reachable --------------------------------------
    M("PB1", "pid_bind", "guard/verify_running_build_pid.py",
      "content-hash comparison disabled - a matching path alone certifies the deploy",
      "'a path is identity, not bytes' (right path, wrong bytes must go red)",
      "    if resolved_sha != expected_sha256:",
      "    if resolved_sha != expected_sha256 and expected_sha256 is None:"),
    M("PA1", "population", "guard/population_arm.py",
      "breadth ceiling inflated 9x - an always-firing candidate passes its own review",
      "'over-broad candidate FAILS the review' (the social off-switch direction)",
      "    if share > max_share:",
      "    if share > max_share * 9:"),
    M("PA2", "population", "guard/population_arm.py",
      "every nonzero exit counted as a flag again - a crash on the labelled positive reads "
      "as a detection",
      "'one exit code is the flag; a crash, a usage error or a timeout measures nothing'",
      '    return ("flagged" if p.returncode == FLAG_EXIT else "error"), p.returncode, p.stderr',
      '    return "flagged", p.returncode, p.stderr'),
    M("OW1", "one_writer", "guard/one_writer_gate.py",
      "foreign-file detection blinded - another job's dirty tree proceeds",
      "'a foreign dirty file refuses the transaction and is named'",
      '    foreign = [f for f in dirty if f.replace(os.sep, "/") not in claimed_set]',
      "    foreign = []  # every dirty file treated as this job's own"),
    M("OW2", "one_writer", "guard/one_writer_gate.py",
      "O_EXCL dropped - the create is no longer exclusive and every contender acquires",
      "'exactly one of several contenders may acquire the lock'",
      "    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY",
      "    flags = os.O_CREAT | os.O_WRONLY"),
    M("OW3", "one_writer", "guard/one_writer_gate.py",
      "blind ' -> ' split restored - the refusal fabricates a path out of a filename",
      "'the refusal names only paths that are actually there'",
      "        xy, path = f[:2], f[3:]",
      '        xy, path = f[:2], f[3:]\n        path = path.split(" -> ", 1)[-1].strip(\'"\')'),
    M("RR1", "reader", "guard/reader_record.py",
      "permission failure collapses into looks-empty - cannot-read reported as a valid observation",
      "'permission is not empty' (the distinguishable-safe-default rule)",
      'except PermissionError as exc:\n        return {"status": "permission", "value": None, "detail": str(exc)}\n    except UnicodeDecodeError',
      'except PermissionError as exc:\n        return {"status": "empty", "value": None, "detail": str(exc)}\n    except UnicodeDecodeError'),
    M("RR2", "reader", "guard/reader_record.py",
      "decode failure no longer caught - the reader documented as never raising raises",
      "'undecodable bytes are reported, not raised' (UnicodeDecodeError is not an OSError)",
      "    except UnicodeDecodeError as exc:",
      "    except ZeroDivisionError as exc:"),
    M("RR3", "reader", "guard/reader_record.py",
      "a directory collapses into permission - the reader is sent to check file modes when "
      "the fault is a wrong path",
      "'not-a-file is its own status' (the distinguishable-safe-default rule)",
      'except IsADirectoryError as exc:\n        return {"status": "not-a-file", "value": None, "detail": str(exc)}',
      'except IsADirectoryError as exc:\n        return {"status": "permission", "value": None, "detail": str(exc)}'),
    M("RG1", "reconcile", "guard/reconcile_gate.py",
      "unanimity substitutes for verification - enough agreeing legs waives the premise check",
      "'all-agree ACT on an unverified shared premise is refused' (correlated error)",
      '            ok_premise = p.get("verified") is True and bool(str(p.get("verifier") or "").strip())',
      '            ok_premise = len(c.get("legs", [])) >= 2'),
    M("RG2", "reconcile", "guard/reconcile_gate.py",
      "required-verdict-field check waived - an ACT missing conclusion_verdict entirely ships",
      "'an ACT records BOTH verdicts, so accidentally right is distinguishable from diagnosed'",
      '        for field in VERDICT_FIELDS:\n            if field not in c:',
      '        for field in VERDICT_FIELDS:\n            if False:'),
    M("RG3", "reconcile", "guard/reconcile_gate.py",
      "stringly booleans coerced - a quoted \"false\" reads as TRUE and the premise passes",
      "'a JSON boolean field must be a JSON boolean, never a truthy string'",
      "    if not isinstance(value, bool):",
      "    if False:"),
    M("BS1", "brief", "guard/brief_scan.py",
      "one leak pattern neutered - its planted leak ships silently",
      "'every pattern fires on its plant' (a rule that cannot fire guards nothing)",
      '    ("confirm-our", r"\\bconfirm (?:that|our|this finding|the finding)\\b"),',
      '    ("confirm-our", r"\\bconfirmX (?:that|our|this finding|the finding)\\b"),'),
    M("BS2", "brief", "guard/brief_scan.py",
      "negation suppression disabled - a prohibition of a leak is flagged as the leak, and "
      "the scanner fires on the wording that forbids leaking",
      "'a prohibition is not a leak' (the over-breadth direction)",
      "            if _negated(line, m.start()):",
      "            if False:"),
    M("BS3", "brief", "guard/brief_scan.py",
      "expected-answer pattern narrowed back - 'the EXPECTED answer is' passes clean again",
      "'every planted leak spelling is flagged' (the under-breadth direction)",
      '    ("expected-answer", r"\\bthe (?:expected |likely |correct |real )?answer (?:is|was|should be|will be)\\b"),',
      '    ("expected-answer", r"\\bthe answer (?:is|should be)\\b"),'),
    M("CG1", "curation", "guard/curation_gate.py",
      "independent-reviewer floor lowered to one - single-model approval accepted",
      "'one-model approval is rejected' (the panel requirement)",
      "        if len(independent) < 2:",
      "        if len(independent) < 1:"),
    M("CG2", "curation", "guard/curation_gate.py",
      "reviewer identities no longer deduplicated - one model echoing itself is a panel",
      "'the independence floor counts DISTINCT reviewer identities'",
      '        independent = sorted({v["reviewer"].strip() for v in votes\n'
      '                              if v.get("independent") is True})',
      '        independent = [v["reviewer"] for v in votes if v.get("independent") is True]'),
    M("CG3", "curation", "guard/curation_gate.py",
      "stringly booleans coerced - a quoted \"false\" reads as TRUE and the vote counts",
      "'a JSON boolean field must be a JSON boolean, never a truthy string'",
      "    if not isinstance(value, bool):",
      "    if False:"),
    M("OL1", "org_lint", "guard/org_lint.py",
      "README-mention requirement waived - any root file reads as an entry point",
      "'a root script the README never mentions is a stray'",
      "        named = _mentions(name, readme_text)",
      "        named = True"),
    M("OL2", "org_lint", "guard/org_lint.py",
      "whole-token match reverted to the raw substring test - a suffix-named stray rides "
      "on a longer name the README does mention",
      "'a stray whose name is a suffix of a README-named script is still a stray'",
      '    token = re.compile(r"(?<![\\w.\\-])%s(?![\\w.\\-])" % re.escape(name))',
      "    token = re.compile(re.escape(name))"),
    M("OL3", "org_lint", "guard/org_lint.py",
      "prohibition context ignored - the README inverts into an allow-list and 'never "
      "commit X' licenses X",
      "'a README prohibition is not a licence'",
      "        if PROHIBITION.search(line):",
      "        if False and PROHIBITION.search(line):"),
    M("RK1", "roster", "templates/roster-check.sh.template",
      "missing-tag flag never set - a model absent from the roster reads as served",
      "'a routed model absent from the roster fails the arm and is named'",
      "    missing=1",
      "    missing=0"),
]


# ────────────────────────────────────────────────────────────────────────────────────────────
def sh(cmd, cwd=None):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)


def run_guard(sandbox, guard_key, timeout=180):
    """Run one guard from the sandbox. Returns (rc, combined_output)."""
    rel, _marker = GUARDS[guard_key]
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    try:
        p = subprocess.run([sys.executable, os.path.join(sandbox, rel)], cwd=sandbox,
                           capture_output=True, text=True, timeout=timeout, env=env)
        return p.returncode, p.stdout + p.stderr
    except subprocess.TimeoutExpired:
        return -1, "<TIMEOUT>"


def guard_green(guard_key, rc, out):
    _rel, marker = GUARDS[guard_key]
    return rc == 0 and marker in out


def classify(guard_key, rc, out):
    """Verdict for a MUTANT run of guard_key."""
    if rc == -1:
        return "ERROR", "guard timed out"
    if guard_key == "prefilter" and rc == 2:
        return "ABSTAINED", "guard returned UNMEASURED (exit 2) — it asserted nothing"
    if rc != 0:
        mode = "crash" if "Traceback (most recent call last)" in out else "assert"
        return "KILLED", mode
    if guard_green(guard_key, rc, out):
        note = ""
        if guard_key == "cascade" and "corpus unavailable" in out:
            note = "⚠ corpus regression block SWALLOWED an exception (bare try/except)"
        return "SURVIVED", note
    return "ANOMALY", "exit 0 but the guard's all-pass marker is missing"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--verify-anchors", action="store_true",
                    help="check every needle still binds exactly once (fast; no mutating)")
    ap.add_argument("--only", nargs="+", help="run only these mutation IDs")
    args = ap.parse_args()

    if args.verify_anchors:
        return verify_anchors()

    muts = [m for m in MUTATIONS if not args.only or m["id"] in args.only]
    ids = {m["id"] for m in MUTATIONS}
    if args.only and set(args.only) - ids:
        print(f"unknown mutation IDs: {sorted(set(args.only) - ids)}"); return 2

    tree_before = sh(["git", "status", "--porcelain"], cwd=REPO).stdout

    sandbox = tempfile.mkdtemp(prefix="mutation_harness_")
    results, integrity_errors = [], []
    try:
        # ── build the sandbox: copy + retarget the hardcoded repo paths ─────────────────────
        pristine = {}
        for rel in FILES:
            src = os.path.join(REPO, rel)
            content = open(src, encoding="utf-8").read().replace(REPO, sandbox)
            dst = os.path.join(sandbox, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            open(dst, "w", encoding="utf-8").write(content)
            pristine[rel] = content

        # ── control: every guard must be green in the sandbox before any mutation ──────────
        print(f"sandbox: {sandbox}")
        print("baseline (control) — every guard must pass from the sandbox before any mutation:")
        for key in GUARDS:
            rc, out = run_guard(sandbox, key)
            ok = guard_green(key, rc, out)
            print(f"  {'ok  ' if ok else 'FAIL'}  {key} (rc={rc})")
            if not ok:
                print(out[-2000:])
                print("BASELINE FAILED — the sandbox does not reproduce the clean tree. Aborting.")
                return 2

        # ── the mutation loop ───────────────────────────────────────────────────────────────
        print(f"\nrunning {len(muts)} mutations:")
        for m in muts:
            content = pristine[m["file"]]
            n = content.count(m["old"])
            if n != 1:
                integrity_errors.append(f"{m['id']}: target string matched {n}x in {m['file']} (need exactly 1)")
                results.append({**m, "verdict": "ERROR", "note": f"target matched {n}x", "out": ""})
                print(f"  ERROR      {m['id']:5s} target string matched {n}x")
                continue
            path = os.path.join(sandbox, m["file"])
            try:
                open(path, "w", encoding="utf-8").write(content.replace(m["old"], m["new"]))
                rc, out = run_guard(sandbox, m["guard"])
                verdict, note = classify(m["guard"], rc, out)
            finally:
                open(path, "w", encoding="utf-8").write(content)      # restore pristine copy
            rc2, out2 = run_guard(sandbox, m["guard"])                 # step 4: clean re-verify
            if not guard_green(m["guard"], rc2, out2):
                integrity_errors.append(f"{m['id']}: guard NOT green after restore (rc={rc2})")
                verdict, note = "ERROR", f"clean re-verify failed (rc={rc2})"
            results.append({**m, "verdict": verdict, "note": note, "out": out})
            flag = {"KILLED": "killed", "SURVIVED": "SURVIVED ⚠", "ABSTAINED": "ABSTAINED ⚠",
                    "ANOMALY": "ANOMALY ⚠", "ERROR": "ERROR"}[verdict]
            print(f"  {flag:11s}{m['id']:6s}[{m['guard']}] {m['desc']}"
                  + (f"  ({note})" if note and args.verbose or note.startswith("⚠") else ""))
            if args.verbose and verdict != "KILLED":
                print("    · claims: " + m["claims"])

        # ── survivors: would ANY other guard have caught it? ────────────────────────────────
        survivors = [r for r in results if r["verdict"] == "SURVIVED"]
        if survivors:
            print("\ncross-sweep — running the other guards against each survivor:")
        for r in survivors:
            path = os.path.join(sandbox, r["file"])
            caught = []
            try:
                open(path, "w", encoding="utf-8").write(pristine[r["file"]].replace(r["old"], r["new"]))
                for key in GUARDS:
                    if key == r["guard"]:
                        continue
                    rc, out = run_guard(sandbox, key)
                    v, _ = classify(key, rc, out)
                    if v == "KILLED":
                        caught.append(key)
            finally:
                open(path, "w", encoding="utf-8").write(pristine[r["file"]])
            r["caught_elsewhere"] = caught
            print(f"  {r['id']:6s}" + (f"caught by other guard(s): {', '.join(caught)}"
                                       if caught else "caught by NO guard at all"))

        # final integrity: whole sandbox pristine → every guard green once more
        for key in GUARDS:
            rc, out = run_guard(sandbox, key)
            if not guard_green(key, rc, out):
                integrity_errors.append(f"final sweep: {key} not green on pristine sandbox (rc={rc})")
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)

    # ── the working tree must be untouched ──────────────────────────────────────────────────
    tree_after = sh(["git", "status", "--porcelain"], cwd=REPO).stdout
    if tree_after != tree_before:
        print("\n✗✗ WORKING TREE CHANGED during the run — this must never happen:")
        print(tree_after)
        return 3

    # ── scoreboard ──────────────────────────────────────────────────────────────────────────
    by = lambda v: [r for r in results if r["verdict"] == v]
    killed, survived = by("KILLED"), by("SURVIVED")
    other = by("ABSTAINED") + by("ANOMALY") + by("ERROR")
    print("\n" + "=" * 79)
    print(f"SCOREBOARD  {len(results)} mutations · {len(killed)} KILLED · {len(survived)} SURVIVED"
          + (f" · {len(other)} abstained/anomaly/error" if other else ""))
    print("=" * 79)
    for r in survived:
        where = (", ".join(r.get("caught_elsewhere") or []) or "NO guard")
        print(f"\n  SURVIVED  {r['id']}  [{r['guard']}]  {r['desc']}")
        print(f"            guard property it exposes as vacuous: {r['claims']}")
        print(f"            caught elsewhere: {where}")
    for r in other:
        print(f"\n  {r['verdict']}  {r['id']}  [{r['guard']}]  {r['desc']}  — {r['note']}")
    if integrity_errors:
        print("\nINTEGRITY ERRORS:")
        for e in integrity_errors:
            print("  ✗ " + e)
        return 2
    print(f"\nworking tree clean: yes (git status unchanged)")
    return 1 if survived or other else 0


if __name__ == "__main__":
    raise SystemExit(main())
