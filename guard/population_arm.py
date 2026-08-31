#!/usr/bin/env python3
"""population_arm.py — measure a candidate guard's breadth BEFORE it lands.

guard-target-correctness covers the predicate that is too NARROW. This arm covers the
mirror direction: a guard that flags everything is never turned off in code — it is
switched off socially. Its alerts get acknowledged on reflex, then batched, then ignored,
and the end state is no guard at all. A guard that never fires and a guard that always
fires are the same defect: zero information.

So the population is measured before the invariant ships:

  1. PIN the corpus — a manifest of the files the guard will police, fixed at review time
     so the denominator cannot drift under the measurement.
  2. RUN the candidate checker over every corpus file; record flagged/scanned.
  3. FAIL the guard's own review when the flagged share exceeds a configured ceiling
     (default: half the corpus).
  4. The ratio measures BREADTH only, so the review also names the labelled positives it
     checked — a guard can be narrow and still wrong. A labelled positive the checker
     does not flag fails the review too.

CHECKER CONTRACT: the candidate guard is invoked once per file with `{}` replaced by the
path. ONE exit code is the semantic flag — exit 1, FLAG_EXIT below. Exit 0 is clean.
EVERY other exit code, and a timeout, is an ERROR, not a flag.

    Counting every nonzero as a successful flag was measured wrong in the direction that
    looks like success: a checker returning 70 only on the labelled positive — a crash on
    exactly the file it was supposed to detect — produced flagged=1 of 7, cleared the
    breadth ceiling, and cleared positive-recall. The measurement was of a checker that
    never worked. A crash, a usage error, and a timeout say nothing about the population,
    so any of them makes the whole review CANNOT CHECK; the stderr is retained per path so
    the cause is visible rather than inferred.

Exit codes: 0 review pass · 1 review FAIL (over-broad, or a labelled positive missed) ·
2 CANNOT CHECK (empty/absent corpus — a ratio over nothing proves nothing — or a checker
that errored on any corpus file).
Gate: guard/tests/test_population_arm.py. Red demos: PA1 disables the breadth ceiling;
PA2 counts every nonzero exit as a flag again.
"""
import argparse
import os
import shlex
import subprocess
import sys
import tempfile


FLAG_EXIT = 1        # the ONE exit code that means "this file is flagged"
TIMEOUT_S = 120


def read_list(path):
    """Manifest lines, or None when the manifest could not be read.

    An unreadable manifest is the same fact as an absent corpus: nothing was measured. It
    used to be an uncaught FileNotFoundError, so a missing --positives path exited 1 with a
    traceback while a missing --corpus correctly returned 2."""
    try:
        with open(path, encoding="utf-8") as fh:
            return [ln.strip() for ln in fh if ln.strip() and not ln.strip().startswith("#")]
    except OSError:
        return None


def run_checker(checker, path):
    """Returns (outcome, rc, stderr). outcome is "clean", "flagged", or "error"."""
    cmd = [path if a == "{}" else a for a in shlex.split(checker)]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return "error", None, "timed out after %ds" % TIMEOUT_S
    except OSError as exc:
        return "error", None, str(exc)
    if p.returncode == 0:
        return "clean", 0, p.stderr
    return ("flagged" if p.returncode == FLAG_EXIT else "error"), p.returncode, p.stderr


def review(corpus, checker, max_share, positives):
    """Returns (rc, lines). rc: 0 pass · 1 fail · 2 cannot check."""
    lines = []
    if not corpus:
        return 2, ["CANNOT CHECK — the pinned corpus is empty; a ratio over nothing proves nothing"]
    outcomes = {p: run_checker(checker, p) for p in corpus}
    errored = [(p, rc, err) for p, (o, rc, err) in outcomes.items() if o == "error"]
    if errored:
        lines.append("CANNOT CHECK — the candidate did not answer the question on %d of %d "
                     "corpus file(s). Exit %d is the flag; a crash, a usage error, or a "
                     "timeout is not a detection, and a breadth ratio built out of them "
                     "measures nothing." % (len(errored), len(corpus), FLAG_EXIT))
        for path, rc, err in errored:
            lines.append("  %s -> exit %s  %s" % (path, rc, (err or "").strip().replace("\n", " ")[:200]))
        return 2, lines
    flagged = [p for p in corpus if outcomes[p][0] == "flagged"]
    share = len(flagged) / len(corpus)
    lines.append("flagged %d / scanned %d (share %.2f, ceiling %.2f)"
                 % (len(flagged), len(corpus), share, max_share))
    rc = 0
    if share > max_share:
        rc = 1
        lines.append("REVIEW FAIL — over-broad: the candidate flags more than the configured "
                     "share of the pinned corpus. A guard that fires on everything is switched "
                     "off socially; the end state is no guard.")
    flagged_set = set(flagged)
    missed = [p for p in positives if p not in flagged_set]
    for p in positives:
        lines.append("labelled positive checked: %s -> %s"
                     % (p, "flagged" if p in flagged_set else "MISSED"))
    if missed:
        rc = 1
        lines.append("REVIEW FAIL — %d labelled positive(s) not flagged; a breadth pass "
                     "without its positives caught is not a pass." % len(missed))
    if rc == 0:
        lines.append("review pass — breadth under the ceiling and every labelled positive caught")
    return rc, lines


def selftest():
    """Teeth: an over-broad candidate and a positive-missing candidate must both FAIL the
    review; an exact candidate must pass. Exercised against a throwaway corpus."""
    failures = []
    with tempfile.TemporaryDirectory() as d:
        corpus = []
        for i in range(6):
            p = os.path.join(d, "f%d.txt" % i)
            with open(p, "w", encoding="utf-8") as fh:
                fh.write("PLANTED-DEFECT\n" if i == 0 else "clean line %d\n" % i)
            corpus.append(p)
        positives = [corpus[0]]
        exact = (sys.executable + " -c \"import sys;"
                 "sys.exit(1 if 'PLANTED-DEFECT' in open(sys.argv[1]).read() else 0)\" {}")
        flag_all = sys.executable + " -c \"import sys; sys.exit(1)\" {}"
        flag_none = sys.executable + " -c \"import sys; sys.exit(0)\" {}"
        rc, _ = review(corpus, exact, 0.5, positives)
        if rc != 0:
            failures.append("exact candidate must pass the review (got rc=%d)" % rc)
        rc, _ = review(corpus, flag_all, 0.5, positives)
        if rc != 1:
            failures.append("an over-broad candidate must FAIL the review (got rc=%d)" % rc)
        rc, _ = review(corpus, flag_none, 0.5, positives)
        if rc != 1:
            failures.append("a candidate missing its labelled positive must FAIL (got rc=%d)" % rc)
        rc, _ = review([], exact, 0.5, [])
        if rc != 2:
            failures.append("an empty corpus must be CANNOT CHECK, never a pass (got rc=%d)" % rc)
        crash_on_positive = (sys.executable + " -c \"import sys;"
                             "sys.exit(70 if 'PLANTED-DEFECT' in open(sys.argv[1]).read() "
                             "else 0)\" {}")
        rc, out = review(corpus, crash_on_positive, 0.5, positives)
        if rc != 2 or not any("CANNOT CHECK" in ln for ln in out):
            failures.append("a checker that CRASHES on the labelled positive must be "
                            "CANNOT CHECK, never a pass (got rc=%d %r)" % (rc, out))
        if read_list(os.path.join(d, "no-such-manifest.txt")) is not None:
            failures.append("an unreadable manifest must report nothing measured, not raise")
    for f in failures:
        print("  FAIL  %s" % f)
    if failures:
        print("population arm selftest: %d check(s) RED" % len(failures))
        return 1
    print("population arm selftest: over-broad FAILS, missed-positive FAILS, exact passes, "
          "empty corpus and a crashing checker are CANNOT CHECK")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--corpus", help="manifest: one corpus path per line (# comments) — PINNED")
    ap.add_argument("--checker", help="candidate command; {} is replaced by each corpus path")
    ap.add_argument("--max-share", type=float, default=0.5,
                    help="ceiling on flagged/scanned (default 0.5)")
    ap.add_argument("--positives", help="manifest of labelled-positive paths the checker must flag")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    if not args.corpus or not args.checker:
        print("CANNOT CHECK — --corpus and --checker are required (or run --selftest)")
        return 2
    corpus = read_list(args.corpus)
    if corpus is None:
        print("CANNOT CHECK — corpus manifest could not be read: %s" % args.corpus)
        return 2
    positives = []
    if args.positives:
        positives = read_list(args.positives)
        if positives is None:
            print("CANNOT CHECK — positives manifest could not be read: %s" % args.positives)
            return 2
    rc, lines = review(corpus, args.checker, args.max_share, positives)
    for ln in lines:
        print(ln)
    return rc


if __name__ == "__main__":
    sys.exit(main())
