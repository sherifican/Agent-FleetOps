"""Gate for guard/population_arm.py — the breadth review must be able to go red.

Red cases this gate holds open: an over-broad candidate (flags more than the ceiling's
share of the pinned corpus) must FAIL its review; a candidate that misses a labelled
positive must FAIL; an empty corpus must be CANNOT CHECK, never a pass; and a checker
that CRASHES rather than answering must not be counted as having flagged anything — a
checker returning 70 only on the labelled positive cleared both the breadth ceiling and
positive-recall for the wrong reason. Red demos: PA1 disables the breadth ceiling; PA2
counts every nonzero exit as a flag again.

Runs two ways: under pytest and standalone —
`python3 guard/tests/test_population_arm.py` — printing the all-pass marker the mutation
harness anchors on.
"""
import io
import contextlib
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from guard.population_arm import read_list, review, selftest  # noqa: E402

MARKER = "POPULATION ARM HAS TEETH - ALL CHECKS PASSED"

EXACT = (sys.executable + " -c \"import sys;"
         "sys.exit(1 if 'PLANTED-DEFECT' in open(sys.argv[1]).read() else 0)\" {}")
FLAG_ALL = sys.executable + " -c \"import sys; sys.exit(1)\" {}"
FLAG_NONE = sys.executable + " -c \"import sys; sys.exit(0)\" {}"


def _corpus(d, n=6, planted=0):
    paths = []
    for i in range(n):
        p = os.path.join(d, "f%d.txt" % i)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("PLANTED-DEFECT\n" if i == planted else "clean line %d\n" % i)
        paths.append(p)
    return paths


CRASH_ON_POSITIVE = (sys.executable + " -c \"import sys;"
                     "sys.exit(70 if 'PLANTED-DEFECT' in open(sys.argv[1]).read() else 0)\" {}")
USAGE_ERROR = sys.executable + " -c \"import sys; sys.exit(2)\" {}"


def test_a_checker_that_crashes_on_the_positive_does_not_pass():
    """The measured false green: exit 70 on the labelled positive, exit 0 everywhere else,
    reported flagged 1 of 7 and passed both the ceiling and positive-recall. One exit code
    is the flag; every other nonzero is the checker failing to answer."""
    with tempfile.TemporaryDirectory() as d:
        corpus = _corpus(d, n=7)
        rc, lines = review(corpus, CRASH_ON_POSITIVE, 0.5, [corpus[0]])
    assert rc != 0, ("a crash on the labelled positive was counted as a detection: %r"
                     % lines)
    assert rc == 2, "a checker that did not answer means nothing was measured: %r" % lines
    assert any("exit 70" in ln for ln in lines), (
        "the review must retain what the checker actually did: %r" % lines)


def test_a_usage_error_is_not_a_flag():
    with tempfile.TemporaryDirectory() as d:
        corpus = _corpus(d, n=6)
        rc, lines = review(corpus, USAGE_ERROR, 0.5, [])
    assert rc == 2, ("a checker exiting 2 on every file reported an over-broad guard "
                     "instead of a broken one: %r" % lines)


def test_a_missing_manifest_reports_nothing_measured_rather_than_raising():
    """A missing --positives path was an uncaught FileNotFoundError (exit 1 plus a
    traceback) where a missing corpus correctly returned 2."""
    assert read_list(os.path.join(tempfile.gettempdir(), "no-such-manifest-4a91.txt")) is None


def test_over_broad_candidate_fails_review():
    with tempfile.TemporaryDirectory() as d:
        corpus = _corpus(d)
        rc, lines = review(corpus, FLAG_ALL, 0.5, [corpus[0]])
    assert rc == 1, "flags 6/6 against a 0.5 ceiling and the review passed — the arm is vacuous"
    assert any("over-broad" in ln for ln in lines)


def test_exact_candidate_passes_and_names_its_positives():
    with tempfile.TemporaryDirectory() as d:
        corpus = _corpus(d)
        rc, lines = review(corpus, EXACT, 0.5, [corpus[0]])
    assert rc == 0, "an exact candidate must pass: %r" % lines
    assert any("labelled positive checked" in ln and "flagged" in ln for ln in lines), \
        "the review must NAME the positives it checked, not just the ratio"


def test_missed_labelled_positive_fails_review():
    with tempfile.TemporaryDirectory() as d:
        corpus = _corpus(d)
        rc, lines = review(corpus, FLAG_NONE, 0.5, [corpus[0]])
    assert rc == 1, "a breadth pass with its labelled positive missed is not a pass"
    assert any("MISSED" in ln for ln in lines)


def test_empty_corpus_is_cannot_check():
    rc, lines = review([], EXACT, 0.5, [])
    assert rc == 2, "a ratio over nothing must be CANNOT CHECK, never a pass: %r" % lines


def test_selftest_is_green():
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = selftest()
    assert rc == 0, "selftest red:\n" + buf.getvalue()


ALL = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    failures = 0
    for fn in ALL:
        try:
            fn()
            print("  ok    %s" % fn.__name__)
        except AssertionError as exc:
            failures += 1
            print("  FAIL  %s: %s" % (fn.__name__, exc))
    if failures:
        print("RESULT: %d check(s) RED" % failures)
        sys.exit(1)
    print(MARKER)
