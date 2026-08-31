"""Gate for guard/population_arm.py — the breadth review must be able to go red.

Red cases this gate holds open: an over-broad candidate (flags more than the ceiling's
share of the pinned corpus) must FAIL its review; a candidate that misses a labelled
positive must FAIL; an empty corpus must be CANNOT CHECK, never a pass. Red demo:
mutation PA1 in guard/mutation_harness.py disables the breadth ceiling and this gate must
go red.

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
from guard.population_arm import review, selftest  # noqa: E402

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
