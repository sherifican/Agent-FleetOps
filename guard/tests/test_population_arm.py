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


# ---------------------------------------------------------------------------------------------
# Contract: reject invalid population ceilings. Written BEFORE the fix. review()
# must validate a finite numeric ceiling in [0, 1] at its own boundary and return 2 — CANNOT
# CHECK, naming the ceiling — before invoking the checker even once; the CLI must agree.
# ---------------------------------------------------------------------------------------------
import math  # noqa: E402
import subprocess  # noqa: E402

ARM = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "population_arm.py")
INVALID_CEILINGS = [float("nan"), float("inf"), float("-inf"), -1.0, 1.1]
INVALID_CEILING_ARGS = ["nan", "inf", "-inf", "-1", "1.1"]


def _counting_checker(d):
    """A flag-everything checker that also appends each path it was invoked on to a log, so
    'zero checker calls' is a measured fact rather than an inference from the exit code."""
    log = os.path.join(d, "calls.log")
    checker = (sys.executable + " -c \"import sys; open(sys.argv[2], 'a').write(sys.argv[1] + "
               "chr(10)); sys.exit(1)\" {} " + log)
    return checker, log


def _calls(log):
    if not os.path.exists(log):
        return []
    with open(log, encoding="utf-8") as fh:
        return [ln for ln in fh.read().splitlines() if ln]


def _assert_cannot_check(rc, lines, label):
    assert rc == 2, "%s: expected 2 (CANNOT CHECK), got rc=%r %r" % (label, rc, lines)
    assert any("CANNOT CHECK" in ln and "ceiling" in ln for ln in lines), (
        "%s: the refusal must say CANNOT CHECK and name the ceiling: %r" % (label, lines))


# --- RED --------------------------------------------------------------------------------------

def test_invalid_ceilings_are_cannot_check_with_zero_checker_calls():
    """NaN, +inf, -inf, -1, 1.1: every failure collected so one run names them all."""
    failures = []
    for ceiling in INVALID_CEILINGS:
        with tempfile.TemporaryDirectory() as d:
            corpus = _corpus(d, n=2)
            checker, log = _counting_checker(d)
            rc, lines = review(corpus, checker, ceiling, [corpus[0]])
            calls = _calls(log)
        label = "ceiling=%r" % ceiling
        try:
            _assert_cannot_check(rc, lines, label)
            assert calls == [], "%s: the checker ran %d time(s) before the ceiling was validated" % (
                label, len(calls))
        except AssertionError as exc:
            failures.append(str(exc))
    assert not failures, "%d invalid ceiling(s) accepted:\n  " % len(failures) + "\n  ".join(failures)


def test_nan_ceiling_is_cannot_check():
    with tempfile.TemporaryDirectory() as d:
        corpus = _corpus(d, n=2)
        rc, lines = review(corpus, FLAG_ALL, float("nan"), [corpus[0]])
    _assert_cannot_check(rc, lines, "ceiling=nan (E4: currently passes)")


def test_negative_ceiling_is_cannot_check_not_a_measured_violation():
    with tempfile.TemporaryDirectory() as d:
        corpus = _corpus(d, n=2)
        rc, lines = review(corpus, FLAG_ALL, -1.0, [corpus[0]])
    _assert_cannot_check(rc, lines, "ceiling=-1 (E4: currently misread as an over-broad guard)")


def test_ceiling_above_one_is_cannot_check():
    with tempfile.TemporaryDirectory() as d:
        corpus = _corpus(d, n=2)
        rc, lines = review(corpus, FLAG_ALL, 1.1, [corpus[0]])
    _assert_cannot_check(rc, lines, "ceiling=1.1 (E4: currently passes)")


def test_non_numeric_ceiling_is_cannot_check_not_an_exception():
    """Direct callers must get the same 2 the CLI gets, not a TypeError from the comparison."""
    for bad in ("0.5", None, [0.5]):
        with tempfile.TemporaryDirectory() as d:
            corpus = _corpus(d, n=2)
            checker, log = _counting_checker(d)
            try:
                rc, lines = review(corpus, checker, bad, [corpus[0]])
            except Exception as exc:  # noqa: BLE001 — the RED is 'it raised', made explicit
                raise AssertionError("ceiling=%r: review() raised %s instead of returning 2: %s"
                                     % (bad, type(exc).__name__, exc))
            calls = _calls(log)
        _assert_cannot_check(rc, lines, "ceiling=%r" % (bad,))
        assert calls == [], "ceiling=%r: checker invoked %d time(s)" % (bad, len(calls))


def test_cli_rejects_invalid_ceilings_with_exit_2():
    with tempfile.TemporaryDirectory() as d:
        corpus = _corpus(d, n=2)
        manifest = os.path.join(d, "corpus.txt")
        with open(manifest, "w", encoding="utf-8") as fh:
            fh.write("\n".join(corpus) + "\n")
        for arg in INVALID_CEILING_ARGS:
            p = subprocess.run([sys.executable, ARM, "--corpus", manifest, "--checker", FLAG_ALL,
                                "--max-share", arg], capture_output=True, text=True)
            out = p.stdout + p.stderr
            assert p.returncode == 2, "--max-share %s: expected exit 2, got %d:\n%s" % (
                arg, p.returncode, out)
            assert "CANNOT CHECK" in out and "ceiling" in out, (
                "--max-share %s: the CLI must say CANNOT CHECK and name the ceiling:\n%s" % (arg, out))


# --- CONTROL ----------------------------------------------------------------------------------

def test_control_two_of_two_flagged_at_half_fails():
    with tempfile.TemporaryDirectory() as d:
        corpus = _corpus(d, n=2)
        rc, lines = review(corpus, FLAG_ALL, 0.5, [corpus[0]])
    assert rc == 1 and any("over-broad" in ln for ln in lines), (rc, lines)


def test_control_one_of_two_flagged_at_half_passes_on_equality():
    with tempfile.TemporaryDirectory() as d:
        corpus = _corpus(d, n=2)
        rc, lines = review(corpus, EXACT, 0.5, [corpus[0]])
    assert rc == 0, "share 0.50 at ceiling 0.50 is not over the ceiling: %r" % lines


def test_control_ceiling_of_one_is_valid():
    with tempfile.TemporaryDirectory() as d:
        corpus = _corpus(d, n=2)
        rc, lines = review(corpus, FLAG_ALL, 1.0, [corpus[0]])
    assert rc == 0, "1.0 is a valid ceiling and 2/2 does not exceed it: %r" % lines


def test_control_ceiling_of_zero_is_valid():
    with tempfile.TemporaryDirectory() as d:
        corpus = _corpus(d, n=2)
        rc, lines = review(corpus, FLAG_NONE, 0.0, [])
        assert rc == 0, "0 with no flags must pass: %r" % lines
        rc, lines = review(corpus, EXACT, 0.0, [corpus[0]])
        assert rc == 1 and any("over-broad" in ln for ln in lines), (
            "0 must fail on a single flag: %r" % lines)


def test_control_distinct_outcomes_are_retained():
    with tempfile.TemporaryDirectory() as d:
        corpus = _corpus(d, n=2)
        assert review([], EXACT, 0.5, [])[0] == 2, "empty corpus is CANNOT CHECK"
        rc, lines = review(corpus, FLAG_NONE, 0.5, [corpus[0]])
        assert rc == 1 and any("MISSED" in ln for ln in lines), "missed positive is a FAIL"
        rc, lines = review(corpus, USAGE_ERROR, 0.5, [])
        assert rc == 2 and any("CANNOT CHECK" in ln for ln in lines), "checker error is CANNOT CHECK"


def test_control_valid_ceilings_still_invoke_the_checker():
    """Positive control for the zero-calls instrument: on a valid ceiling the log fills."""
    with tempfile.TemporaryDirectory() as d:
        corpus = _corpus(d, n=2)
        checker, log = _counting_checker(d)
        rc, _ = review(corpus, checker, 0.5, [corpus[0]])
        calls = _calls(log)
    assert len(calls) == 2 and set(calls) == set(corpus), calls
    assert math.isfinite(0.5)  # the instrument the RED relies on is the stdlib's, not a guess


# =============================================================================================
# Earlier integration coverage. Written BEFORE the fix.
#
# R3: an integer too large for a float (10**400, -10**400) is not a ceiling. Today
#     invalid_ceiling() lets math.isfinite() raise OverflowError, so review() raises instead of
#     returning 2 — and the caller sees a traceback where the contract promises CANNOT CHECK.
# A3_bool: gate gap — a version that accepts True/False as ceilings passed the regression suite.
# =============================================================================================
from guard.population_arm import invalid_ceiling  # noqa: E402

HUGE_CEILINGS = [10 ** 400, -(10 ** 400)]


def _label(c):
    return "ceiling=%s10**400" % ("-" if c < 0 else "")


# --- R3 RED -----------------------------------------------------------------------------------

def test_invalid_ceiling_returns_a_diagnostic_for_a_huge_integer_instead_of_raising():
    failures = []
    for c in HUGE_CEILINGS:
        try:
            why = invalid_ceiling(c)
        except Exception as exc:  # noqa: BLE001 — the RED is 'it raised', made explicit
            failures.append("%s: invalid_ceiling() raised %s: %s" % (_label(c), type(exc).__name__, exc))
            continue
        if not isinstance(why, str) or not why:
            failures.append("%s: invalid_ceiling() returned %r, not a diagnostic string" % (_label(c), why))
    assert not failures, "\n  ".join(["%d huge ceiling(s) mishandled:" % len(failures)] + failures)


def test_review_with_a_huge_ceiling_is_cannot_check_with_zero_checker_calls():
    failures = []
    for c in HUGE_CEILINGS:
        with tempfile.TemporaryDirectory() as d:
            corpus = _corpus(d, n=2)
            checker, log = _counting_checker(d)
            try:
                rc, lines = review(corpus, checker, c, [corpus[0]])
            except Exception as exc:  # noqa: BLE001
                failures.append("%s: review() raised %s instead of returning 2: %s"
                                % (_label(c), type(exc).__name__, exc))
                continue
            calls = _calls(log)
        try:
            _assert_cannot_check(rc, lines, _label(c))
            assert calls == [], "%s: the checker ran %d time(s) before the ceiling was validated" % (
                _label(c), len(calls))
        except AssertionError as exc:
            failures.append(str(exc))
    assert not failures, "\n  ".join(["%d huge ceiling(s) accepted:" % len(failures)] + failures)


# --- A3_bool gate gap (passes on the tip; fails on a version that lets bool through) ----------

def test_bool_true_and_false_are_not_ceilings():
    """True == 1 and False == 0 satisfy every numeric test a careless validator applies; both
    must be refused BEFORE the checker runs, with the same CANNOT CHECK the other invalid
    ceilings get."""
    failures = []
    for b in (True, False):
        with tempfile.TemporaryDirectory() as d:
            corpus = _corpus(d, n=2)
            checker, log = _counting_checker(d)
            try:
                rc, lines = review(corpus, checker, b, [corpus[0]])
            except Exception as exc:  # noqa: BLE001
                failures.append("ceiling=%r: review() raised %s: %s" % (b, type(exc).__name__, exc))
                continue
            calls = _calls(log)
        try:
            _assert_cannot_check(rc, lines, "ceiling=%r" % b)
            assert calls == [], "ceiling=%r: the checker ran %d time(s)" % (b, len(calls))
        except AssertionError as exc:
            failures.append(str(exc))
    assert not failures, "\n  ".join(["bool accepted as a ceiling:"] + failures)


def test_bool_invalid_ceiling_names_the_bool():
    for b in (True, False):
        why = invalid_ceiling(b)
        assert isinstance(why, str) and why, "invalid_ceiling(%r) returned %r" % (b, why)


# --- R3 / A3 CONTROL --------------------------------------------------------------------------

def test_control_ordinary_int_and_float_ceilings_are_still_valid():
    for c in (0, 1, 0.5, 1.0, 0.0):
        assert invalid_ceiling(c) is None, "a valid ceiling %r was refused: %r" % (c, invalid_ceiling(c))


def test_control_large_but_representable_ints_are_refused_as_out_of_range_not_raised():
    """10**300 converts to a float; it is refused for being outside [0, 1], never by raising."""
    for c in (10 ** 300, -(10 ** 300)):
        why = invalid_ceiling(c)
        assert isinstance(why, str) and "[0, 1]" in why, (c, why)


# =============================================================================================
# The R3 fix compares huge ints without converting to float, but
# its diagnostic formats the value with %d — and an int of 5000 digits exceeds the interpreter's
# default integer-string limit, so invalid_ceiling(10**5000) raises ValueError instead of
# returning a string. Run under the DEFAULT limit; never raise it here.
# =============================================================================================
G2_HUGE_CEILINGS = [10 ** 5000, -(10 ** 5000)]


def _g2_label(c):
    return "ceiling=%s10**5000" % ("-" if c < 0 else "")


def test_control_the_default_int_string_limit_is_in_force():
    """Positive control for the arms below: the case can only bite while the limit is on and
    smaller than 5000 digits. 0 means unlimited, which would make the RED pass vacuously."""
    limit = sys.get_int_max_str_digits()
    assert 0 < limit < 5000, "the interpreter's int-string limit is %r; the G2 arms need the default" % limit


def test_invalid_ceiling_returns_a_diagnostic_for_a_5000_digit_integer():
    failures = []
    for c in G2_HUGE_CEILINGS:
        try:
            why = invalid_ceiling(c)
        except Exception as exc:  # noqa: BLE001 — the RED is 'it raised', made explicit
            failures.append("%s: invalid_ceiling() raised %s: %s" % (_g2_label(c), type(exc).__name__, str(exc)[:80]))
            continue
        if not isinstance(why, str) or not why:
            failures.append("%s: returned %r, not a diagnostic string" % (_g2_label(c), why))
    assert not failures, "\n  ".join(["%d huge ceiling(s) mishandled:" % len(failures)] + failures)


def test_review_with_a_5000_digit_ceiling_is_cannot_check_with_zero_checker_calls():
    failures = []
    for c in G2_HUGE_CEILINGS:
        with tempfile.TemporaryDirectory() as d:
            corpus = _corpus(d, n=2)
            checker, log = _counting_checker(d)
            try:
                rc, lines = review(corpus, checker, c, [corpus[0]])
            except Exception as exc:  # noqa: BLE001
                failures.append("%s: review() raised %s instead of returning 2: %s"
                                % (_g2_label(c), type(exc).__name__, str(exc)[:80]))
                continue
            calls = _calls(log)
        try:
            _assert_cannot_check(rc, lines, _g2_label(c))
            assert calls == [], "%s: the checker ran %d time(s)" % (_g2_label(c), len(calls))
        except AssertionError as exc:
            failures.append(str(exc))
    assert not failures, "\n  ".join(["%d huge ceiling(s) accepted:" % len(failures)] + failures)


def test_control_10_to_the_400_stays_covered():
    for c in (10 ** 400, -(10 ** 400)):
        why = invalid_ceiling(c)
        assert isinstance(why, str) and why, (c, why)
        with tempfile.TemporaryDirectory() as d:
            corpus = _corpus(d, n=2)
            checker, log = _counting_checker(d)
            rc, lines = review(corpus, checker, c, [corpus[0]])
            assert rc == 2 and _calls(log) == [], (rc, lines)


# =============================================================================================
# The G2 fix bounded the INTEGER diagnostic; the type-error
# diagnostic still formats an arbitrary invalid value with %r, so a container holding a
# 5000-digit int raises the same ValueError. A diagnostic must never format an arbitrary
# invalid value: say what kind of thing arrived, bounded, and stop.
# =============================================================================================
G2B_CONTAINER_CEILINGS = [("list", [10 ** 5000]), ("dict", {"k": 10 ** 5000})]


def test_invalid_ceiling_returns_a_bounded_diagnostic_for_a_container_holding_a_huge_int():
    failures = []
    for kind, c in G2B_CONTAINER_CEILINGS:
        try:
            why = invalid_ceiling(c)
        except Exception as exc:  # noqa: BLE001 — the RED is 'it raised', made explicit
            failures.append("%s: invalid_ceiling() raised %s: %s" % (kind, type(exc).__name__, str(exc)[:80]))
            continue
        if not isinstance(why, str) or not why:
            failures.append("%s: returned %r, not a diagnostic string" % (kind, why))
        elif len(why) > 500:
            failures.append("%s: the diagnostic is %d characters — it formatted the value" % (kind, len(why)))
        elif kind not in why:
            failures.append("%s: the diagnostic does not say what kind of thing arrived: %r" % (kind, why))
    assert not failures, "\n  ".join(["%d container ceiling(s) mishandled:" % len(failures)] + failures)


def test_review_with_a_container_ceiling_is_cannot_check_with_zero_checker_calls():
    failures = []
    for kind, c in G2B_CONTAINER_CEILINGS:
        with tempfile.TemporaryDirectory() as d:
            corpus = _corpus(d, n=2)
            checker, log = _counting_checker(d)
            try:
                rc, lines = review(corpus, checker, c, [corpus[0]])
            except Exception as exc:  # noqa: BLE001
                failures.append("%s: review() raised %s instead of returning 2: %s"
                                % (kind, type(exc).__name__, str(exc)[:80]))
                continue
            calls = _calls(log)
        try:
            _assert_cannot_check(rc, lines, "ceiling=%s" % kind)
            assert calls == [], "%s: the checker ran %d time(s)" % (kind, len(calls))
        except AssertionError as exc:
            failures.append(str(exc))
    assert not failures, "\n  ".join(["%d container ceiling(s) accepted:" % len(failures)] + failures)


def test_control_small_non_numeric_ceilings_keep_a_diagnostic_naming_their_kind():
    for kind, c in (("str", "0.5"), ("NoneType", None), ("list", [0.5])):
        why = invalid_ceiling(c)
        assert isinstance(why, str) and kind in why, (c, why)


# =============================================================================================
# The G2b diagnostic stopped formatting the value but still calls
# len() on it: range(10**5000) raises OverflowError from len(). The adversarial extreme: a value
# whose every dunder raises. A diagnostic built from type(x).__name__ alone survives it; one that
# calls anything on the value does not.
# =============================================================================================
class _HostileCeiling:
    """Every operation a diagnostic might be tempted to perform on the value raises."""

    def _boom(self, *_a, **_k):
        raise RuntimeError("the diagnostic touched the value")

    __len__ = __repr__ = __str__ = __format__ = __iter__ = __index__ = _boom
    __int__ = __float__ = __bool__ = __eq__ = __lt__ = __hash__ = _boom


C1_CEILINGS = [("range", lambda: range(10 ** 5000)), ("hostile", _HostileCeiling)]


def test_invalid_ceiling_never_touches_an_arbitrary_value():
    failures = []
    for kind, make in C1_CEILINGS:
        try:
            why = invalid_ceiling(make())
        except Exception as exc:  # noqa: BLE001 — the RED is 'it raised', made explicit
            failures.append("%s: invalid_ceiling() raised %s: %s" % (kind, type(exc).__name__, str(exc)[:80]))
            continue
        if not isinstance(why, str) or not why:
            failures.append("%s: returned %r, not a diagnostic string" % (kind, why))
        elif len(why) > 500:
            failures.append("%s: the diagnostic is %d characters" % (kind, len(why)))
    assert not failures, "\n  ".join(["%d hostile ceiling(s) mishandled:" % len(failures)] + failures)


def test_review_with_a_hostile_ceiling_is_cannot_check_with_zero_checker_calls():
    failures = []
    for kind, make in C1_CEILINGS:
        with tempfile.TemporaryDirectory() as d:
            corpus = _corpus(d, n=2)
            checker, log = _counting_checker(d)
            try:
                rc, lines = review(corpus, checker, make(), [corpus[0]])
            except Exception as exc:  # noqa: BLE001
                failures.append("%s: review() raised %s instead of returning 2: %s"
                                % (kind, type(exc).__name__, str(exc)[:80]))
                continue
            calls = _calls(log)
        try:
            _assert_cannot_check(rc, lines, "ceiling=%s" % kind)
            assert calls == [], "%s: the checker ran %d time(s)" % (kind, len(calls))
        except AssertionError as exc:
            failures.append(str(exc))
    assert not failures, "\n  ".join(["%d hostile ceiling(s) accepted:" % len(failures)] + failures)


def test_control_the_hostile_object_really_raises_on_every_dunder():
    h = _HostileCeiling()
    for op in (len, repr, str, format, iter, int, float, bool, hash, lambda x: x == 1, lambda x: x < 1):
        try:
            op(h)
        except RuntimeError:
            continue
        raise AssertionError("the hostile object let %r through" % op)


# =============================================================================================
# Coverage (orchestrator gate, written before the fix). C1 pinned "a NON-number is
# described by its type alone". The implementer then reported the rest of the class: an int or
# float SUBCLASS whose comparisons raise still makes invalid_ceiling() raise (it takes the numeric
# path), and so does a type whose metaclass makes `__name__` raise. Sampling one more subclass
# per round never ends; the whole class is "any value whatsoever", and the contract that covers it
# is: invalid_ceiling() NEVER raises — anything that goes wrong while validating is a CANNOT CHECK
# diagnostic. A plain numeric subclass (a computed numpy-style float) must still be accepted.
# =============================================================================================
class _HostileInt(int):
    def _boom(self, *_a, **_k):
        raise RuntimeError("the validator touched the value")

    __repr__ = __str__ = __format__ = __float__ = __index__ = __int__ = __bool__ = _boom
    __eq__ = __ne__ = __lt__ = __le__ = __gt__ = __ge__ = __hash__ = _boom


class _HostileFloat(float):
    def _boom(self, *_a, **_k):
        raise RuntimeError("the validator touched the value")

    __repr__ = __str__ = __format__ = __float__ = __index__ = __int__ = __bool__ = _boom
    __eq__ = __ne__ = __lt__ = __le__ = __gt__ = __ge__ = __hash__ = _boom


class _NamelessMeta(type):
    @property
    def __name__(cls):
        raise RuntimeError("the diagnostic read the type name")


class _Nameless(metaclass=_NamelessMeta):
    pass


# The two numeric subclasses moved to C3 below. C3 found a stronger contract than "refuse
# what raises": a numeric subclass is judged as the plain number it holds — refused only when that
# number is — so C2 now pins only the non-number whose type name raises.
C2_CEILINGS = [("type-whose-name-raises", _Nameless)]


def test_invalid_ceiling_never_raises_for_any_value():
    failures = []
    for kind, make in C2_CEILINGS:
        try:
            why = invalid_ceiling(make())
        except Exception as exc:  # noqa: BLE001 — the RED is 'it raised', made explicit
            failures.append("%s: invalid_ceiling() raised %s: %s" % (kind, type(exc).__name__, str(exc)[:80]))
            continue
        if not isinstance(why, str) or not why:
            failures.append("%s: returned %r — a value that cannot be validated is not a valid ceiling"
                            % (kind, why))
        elif len(why) > 500:
            failures.append("%s: the diagnostic is %d characters" % (kind, len(why)))
    assert not failures, "\n  ".join(["%d ceiling(s) mishandled:" % len(failures)] + failures)


def test_review_with_an_unvalidatable_ceiling_is_cannot_check_with_zero_checker_calls():
    failures = []
    for kind, make in C2_CEILINGS:
        with tempfile.TemporaryDirectory() as d:
            corpus = _corpus(d, n=2)
            checker, log = _counting_checker(d)
            try:
                rc, lines = review(corpus, checker, make(), [corpus[0]])
            except Exception as exc:  # noqa: BLE001
                failures.append("%s: review() raised %s instead of returning 2: %s"
                                % (kind, type(exc).__name__, str(exc)[:80]))
                continue
            calls = _calls(log)
        try:
            _assert_cannot_check(rc, lines, "ceiling=%s" % kind)
            assert calls == [], "%s: the checker ran %d time(s)" % (kind, len(calls))
        except AssertionError as exc:
            failures.append(str(exc))
    assert not failures, "\n  ".join(["%d ceiling(s) accepted:" % len(failures)] + failures)


def test_control_plain_numeric_subclasses_are_still_valid_and_hostile_ones_really_raise():
    class _PlainFloat(float):
        pass

    class _PlainInt(int):
        pass

    assert invalid_ceiling(_PlainFloat(0.5)) is None, "a plain float subclass in range was rejected"
    assert invalid_ceiling(_PlainInt(1)) is None, "a plain int subclass in range was rejected"
    assert invalid_ceiling(_PlainFloat(1.5)), "a plain float subclass out of range was accepted"
    for value in (_HostileInt(1), _HostileFloat(0.5)):
        for op in (repr, float, lambda x: x <= 1, lambda x: x >= 0):
            try:
                op(value)
            except RuntimeError:
                continue
            raise AssertionError("the hostile %s let %r through" % (type(value).__mro__[1].__name__, op))
    try:
        _ = type(_Nameless()).__name__
    except RuntimeError:
        pass
    else:
        raise AssertionError("the nameless type's __name__ did not raise")


# =============================================================================================
# Coverage (orchestrator gate, written before the fix). C2 made invalid_ceiling()
# refuse any value whose validation RAISES. The implementer then showed that is not the class: a
# numeric subclass can pass validation and still raise inside review() (its `<` raises when review
# compares it, its __float__ when review formats it) — and it need not raise at all. A float
# subclass of 0.5 whose `__lt__` answers as if it were 2.0 makes `share > max_share` False for every
# share, so an over-broad candidate PASSES its review: the guard fails open, silently. Refusing
# "hostile" values cannot see a liar.
#
# The contract that covers the whole class: a ceiling is judged by what it IS — type() and the
# number the base type stores — never by what its own methods answer. An int or float subclass is
# judged exactly as the plain int or float it holds, by invalid_ceiling() and review() alike (same
# diagnostic, same rc, same lines, same checker calls); a value whose `__class__` CLAIMS a type is
# not that type. Pinned over EVERY attribute int and float define — each overridden alone, raising
# and answering as a different number — every attribute at once, and a __class__ claiming each
# other type. A diagnostic is an exact str of at most 500 characters for any value at all.
# The review matrix swaps run_checker for an in-process stub (thousands of reviews); the anchors
# at the end use real checker processes and outcomes derived by hand, not from the plain path.
# =============================================================================================
import guard.population_arm as _pa  # noqa: E402

C3_EXCLUDED = {
    "__new__": "overriding it changes what gets constructed, not how the constructed value answers",
    "__init__": "it runs during construction, the same way",
}
C3_VALUES = {float: [("0.25", 0.25), ("0.5", 0.5), ("0.75", 0.75), ("-0.0", -0.0), ("1.5", 1.5),
                     ("nan", float("nan")), ("inf", float("inf"))],
             int: [("0", 0), ("1", 1), ("2", 2), ("-1", -1), ("10**5000", 10 ** 5000)]}
# A liar answers every attribute it overrides as if it were this other number instead.
C3_POSING_AS = {float: [2.0, 0.25, float("nan")], int: [2, 0]}
C3_CLAIMS = [bool, int, float, str, type(None), list]
C3_CORPUS = ["/c3/f%d" % i for i in range(4)]
C3_FLAGGED = C3_CORPUS[:2]  # share 0.50: ceiling 0.25 fails, 0.5 passes on equality, 1.5 is refused
C3_POSITIVES = [C3_CORPUS[0]]


def _c3_names(base):
    return sorted(n for n in dir(base) if n not in C3_EXCLUDED)


def _c3_is_property(base, name):
    return name == "__class__" or not callable(getattr(base, name))


def _c3_raises(name):
    def attr(self, *_a, **_k):
        raise RuntimeError("the ceiling's %s was consulted" % name)
    return attr


def _c3_answers_as(base, name, other):
    def attr(self, *a, **k):
        return getattr(base(other), name)(*a, **k)
    return attr


def _c3_subclass(base, overrides, label):
    """A subclass of `base` with each name -> (behaviour, other) installed: as a method, or as a
    property for a data attribute (real, imag, numerator, __doc__, __class__ ...)."""
    ns = {}
    for name, (behaviour, other) in overrides.items():
        prop = _c3_is_property(base, name)
        if behaviour == "raises":
            ns[name] = property(_c3_raises(name)) if prop else _c3_raises(name)
        elif behaviour == "answers-as":
            if prop:
                ns[name] = property(lambda self, _n=name, _o=other: getattr(base(_o), _n))
            else:
                ns[name] = _c3_answers_as(base, name, other)
        else:  # "claims": __class__ reports another type
            ns[name] = property(lambda self, _c=other: _c)
    return type("C3_%s_%s" % (base.__name__, label), (base,), ns)


def _c3_cases():
    """(label, base, subclass) for every attribute of int and float overridden alone and all at
    once — raising, and answering as each other number — plus __class__ claiming each type."""
    for base in (float, int):
        names = _c3_names(base)
        behaviours = [("raises", None)] + [("answers-as", o) for o in C3_POSING_AS[base]]
        for behaviour, other in behaviours:
            how = behaviour if other is None else "%s-%r" % (behaviour, other)
            for name in names:
                yield ("%s.%s %s" % (base.__name__, name, how), base,
                       _c3_subclass(base, {name: (behaviour, other)}, "one"))
            yield ("%s.* %s" % (base.__name__, how), base,
                   _c3_subclass(base, {n: (behaviour, other) for n in names}, "all"))
        for claim in C3_CLAIMS:
            yield ("%s.__class__ claims %s" % (base.__name__, claim.__name__), base,
                   _c3_subclass(base, {"__class__": ("claims", claim)}, "claims"))


def _c3_exact(result):
    """True when `result` is built only from exact None/int/str/list/tuple, so comparing it can
    run nobody's override."""
    t = type(result)
    if result is None or t is int or t is str:
        return True
    if t is tuple or t is list:
        return all(_c3_exact(r) for r in result)
    return False


def _c3_call(fn, *args):
    try:
        return "returned", fn(*args)
    except Exception as exc:  # noqa: BLE001 — a raise is one of the outcomes being compared
        try:
            why = "%s: %s" % (type(exc).__name__, str(exc)[:80])
        except Exception:  # noqa: BLE001
            why = type(exc).__name__
        return "raised", why


def _c3_show(outcome):
    kind, value = outcome
    if kind == "raised":
        return "raised " + value
    if not _c3_exact(value):
        return "returned a non-plain %s" % type(value).__name__
    text = repr(value)
    return "returned " + (text if len(text) <= 140 else text[:137] + "...")


class _C3Stub:
    """Stands in for run_checker: flags C3_FLAGGED, records every path it is asked about."""

    def __init__(self):
        self.calls = []

    def __call__(self, checker, path):
        self.calls.append(path)
        return ("flagged", _pa.FLAG_EXIT, "") if path in C3_FLAGGED else ("clean", 0, "")


def _c3_review(ceiling):
    """review() over the stub corpus: ((kind, value), checker calls)."""
    stub, real = _C3Stub(), _pa.run_checker
    _pa.run_checker = stub
    try:
        outcome = _c3_call(review, C3_CORPUS, "c3-stub-checker", ceiling, C3_POSITIVES)
    finally:
        _pa.run_checker = real
    return outcome, stub.calls


def _c3_report(failures, total, what):
    head = "%d of %d numeric-subclass ceiling(s) %s:" % (len(failures), total, what)
    shown = failures[:30] + (["... and %d more" % (len(failures) - 30)] if len(failures) > 30 else [])
    return "\n  ".join([head] + shown)


# --- RED --------------------------------------------------------------------------------------

def test_invalid_ceiling_judges_every_numeric_subclass_as_the_plain_number_it_holds():
    failures, total = [], 0
    for label, base, cls in _c3_cases():
        for shown, v in C3_VALUES[base]:
            total += 1
            want = _c3_call(invalid_ceiling, base(v))
            try:
                ceiling = cls(v)
            except Exception as exc:  # noqa: BLE001
                failures.append("%s @%s: could not be constructed: %s" % (label, shown, exc))
                continue
            got = _c3_call(invalid_ceiling, ceiling)
            if not (got[0] == "returned" and _c3_exact(got[1]) and got == want):
                failures.append("%s @%s: %s (plain %s: %s)"
                                % (label, shown, _c3_show(got), shown, _c3_show(want)))
    assert not failures, _c3_report(failures, total, "judged differently from their plain value")


def test_review_judges_every_numeric_subclass_as_the_plain_number_it_holds():
    failures, total, plain_rcs = [], 0, set()
    plain = {}
    for base in (float, int):
        for shown, v in C3_VALUES[base]:
            outcome, calls = _c3_review(base(v))
            plain[base, shown] = (outcome, calls)
            if outcome[0] == "returned":
                plain_rcs.add(outcome[1][0])
                if outcome[1][0] in (0, 1):
                    assert calls == C3_CORPUS, (
                        "the stub was not the path review() takes for plain %s: calls %r" % (shown, calls))
    # The matrix below compares against these plain outcomes; it must span every verdict or a
    # match proves nothing (two identical refusals agree).
    assert plain_rcs == {0, 1, 2}, "the plain outcomes cover only rc %r" % sorted(plain_rcs)
    for label, base, cls in _c3_cases():
        for shown, v in C3_VALUES[base]:
            total += 1
            want, want_calls = plain[base, shown]
            got, calls = _c3_review(cls(v))
            if not (got[0] == "returned" and _c3_exact(got[1]) and got == want):
                failures.append("%s @%s: review %s (plain: %s)"
                                % (label, shown, _c3_show(got), _c3_show(want)))
            elif calls != want_calls:
                failures.append("%s @%s: the checker ran on %d path(s), plain %d"
                                % (label, shown, len(calls), len(want_calls)))
    assert not failures, _c3_report(failures, total, "reviewed differently from their plain value")


class _C3Impostor:
    """Not a number. Answers like 0.5 to every conversion and yes to every comparison."""

    def __float__(self):
        return 0.5

    def __index__(self):
        return 1

    __int__ = __index__

    def _yes(self, _other):
        return True

    __lt__ = __le__ = __gt__ = __ge__ = __eq__ = __ne__ = _yes
    __hash__ = object.__hash__

    def __repr__(self):
        return "0.5"


def _c3_impostor(claim):
    return type("C3Impostor_%s" % claim.__name__, (_C3Impostor,),
                {"__class__": property(lambda self: claim)})()


def test_a_value_that_only_claims_to_be_a_number_is_not_a_ceiling():
    failures = []
    for claim in (float, int, bool):
        imp = _c3_impostor(claim)
        why = _c3_call(invalid_ceiling, imp)
        if not (why[0] == "returned" and type(why[1]) is str and why[1] and len(why[1]) <= 500):
            failures.append("claims %s: invalid_ceiling %s" % (claim.__name__, _c3_show(why)))
        outcome, calls = _c3_review(imp)
        if outcome[0] != "returned" or not _c3_exact(outcome[1]):
            failures.append("claims %s: review %s" % (claim.__name__, _c3_show(outcome)))
            continue
        try:
            _assert_cannot_check(outcome[1][0], outcome[1][1], "claims %s" % claim.__name__)
            assert calls == [], "claims %s: the checker ran %d time(s)" % (claim.__name__, len(calls))
        except AssertionError as exc:
            failures.append(str(exc))
    assert not failures, "\n  ".join(["%d impostor(s) accepted as a ceiling:" % len(failures)] + failures)


class _C3HostileStr(str):
    def _boom(self, *_a, **_k):
        raise RuntimeError("the diagnostic used the type name's own methods")

    __len__ = __getitem__ = __str__ = __repr__ = __format__ = __add__ = __mod__ = _boom
    __iter__ = __eq__ = __hash__ = __contains__ = _boom


def _c3_type_named(name):
    t = type("C3Named", (), {})
    t.__name__ = name
    return t


def _c3_meta_named(get):
    meta = type("C3Meta", (type,), {"__name__": property(lambda cls: get())})
    return meta("C3Meta_named", (), {})


class _C3LongStr:
    def __str__(self):
        return "N" * 10000

    __format__ = lambda self, _spec: "N" * 10000  # noqa: E731
    __repr__ = __str__


def _c3_long_repr(base):
    long = "9" * 10000
    return type("C3LongRepr_%s" % base.__name__, (base,), {
        "__repr__": lambda self: long, "__str__": lambda self: long,
        "__format__": lambda self, _spec: long})


C3_BOUNDED = [
    ("a non-number whose type name is 10000 characters", lambda: type("N" * 10000, (), {})(), None),
    ("a non-number whose metaclass names it with 10000 characters",
     lambda: _c3_meta_named(lambda: "N" * 10000)(), None),
    ("a non-number whose metaclass names it with an object whose str() is 10000 characters",
     lambda: _c3_meta_named(_C3LongStr)(), None),
    ("a non-number whose type name is a str subclass whose every method raises",
     lambda: _c3_type_named(_C3HostileStr("C3Named"))(), None),
    ("a float subclass 1.5 whose repr, str and format are 10000 characters",
     lambda: _c3_long_repr(float)(1.5), 1.5),
    ("a float subclass nan whose repr, str and format are 10000 characters",
     lambda: _c3_long_repr(float)(float("nan")), float("nan")),
    ("an int subclass 2 whose repr, str and format are 10000 characters",
     lambda: _c3_long_repr(int)(2), 2),
    ("a float subclass 1.5 whose type name is 10000 characters",
     lambda: type("N" * 10000, (float,), {})(1.5), 1.5),
]


def test_the_diagnostic_is_an_exact_str_of_at_most_500_characters_for_any_value():
    failures = []
    for label, make, plain in C3_BOUNDED:
        got = _c3_call(invalid_ceiling, make())
        if got[0] != "returned" or type(got[1]) is not str or not got[1]:
            failures.append("%s: invalid_ceiling %s" % (label, _c3_show(got)))
        elif len(got[1]) > 500:
            failures.append("%s: the diagnostic is %d characters" % (label, len(got[1])))
        elif plain is not None and got != _c3_call(invalid_ceiling, plain):
            failures.append("%s: %s, but the plain value gives %s"
                            % (label, _c3_show(got), _c3_show(_c3_call(invalid_ceiling, plain))))
    assert not failures, "\n  ".join(["%d diagnostic(s) unbounded or wrong:" % len(failures)] + failures)


def test_anchor_a_lying_ceiling_cannot_pass_an_over_broad_candidate():
    """Real checker processes, outcomes derived by hand: every file flagged is share 1.00, which is
    over a 0.5 ceiling however the ceiling's `<` answers."""
    failures = []
    liars = [("__lt__ answers as 2.0", _c3_subclass(float, {"__lt__": ("answers-as", 2.0)}, "a1")),
             ("every attribute answers as 2.0",
              _c3_subclass(float, {n: ("answers-as", 2.0) for n in _c3_names(float)}, "a2"))]
    for label, cls in liars:
        with tempfile.TemporaryDirectory() as d:
            corpus = _corpus(d, n=2)
            got = _c3_call(review, corpus, FLAG_ALL, cls(0.5), [corpus[0]])
        if got[0] != "returned" or not _c3_exact(got[1]):
            failures.append("float 0.5, %s: review %s" % (label, _c3_show(got)))
        elif got[1][0] != 1 or not any("over-broad" in ln for ln in got[1][1]):
            failures.append("float 0.5, %s: flagging every file PASSED (rc %r) %r"
                            % (label, got[1][0], got[1][1]))
    assert not failures, "\n  ".join(["a lying ceiling let an over-broad candidate through:"] + failures)


def test_anchor_an_out_of_range_ceiling_that_answers_in_range_is_refused_unrun():
    """1.5 is not in [0, 1] whatever its `<=` and `>=` say; the candidate must not run."""
    cls = _c3_subclass(float, {"__le__": ("answers-as", 0.5), "__ge__": ("answers-as", 0.5)}, "a3")
    with tempfile.TemporaryDirectory() as d:
        corpus = _corpus(d, n=2)
        checker, log = _counting_checker(d)
        got = _c3_call(review, corpus, checker, cls(1.5), [corpus[0]])
        calls = _calls(log)
    assert got[0] == "returned" and _c3_exact(got[1]), "review %s" % _c3_show(got)
    _assert_cannot_check(got[1][0], got[1][1], "float 1.5 answering in range")
    assert calls == [], "the checker ran %d time(s) against a ceiling of 1.5" % len(calls)


def test_anchor_the_report_prints_the_ceiling_it_holds_not_the_one_it_claims():
    """An int subclass 1 whose __float__ answers 0: the report line says ceiling 1.00."""
    cls = _c3_subclass(int, {"__float__": ("answers-as", 0)}, "a4")
    with tempfile.TemporaryDirectory() as d:
        corpus = _corpus(d, n=2)
        got = _c3_call(review, corpus, EXACT, cls(1), [corpus[0]])
    assert got[0] == "returned" and _c3_exact(got[1]), "review %s" % _c3_show(got)
    assert got[1][0] == 0, "one of two flagged under a ceiling of 1 must pass: %r" % (got[1],)
    assert any("ceiling 1.00" in ln for ln in got[1][1]), "the report misstates the ceiling: %r" % (got[1][1],)


# --- controls ---------------------------------------------------------------------------------

def test_control_the_matrix_is_whole_and_its_overrides_really_act():
    for base in (int, float):
        names = _c3_names(base)
        assert set(C3_EXCLUDED) <= set(dir(base)), "an exclusion names nothing: %r" % sorted(C3_EXCLUDED)
        assert set(names) | set(C3_EXCLUDED) == set(dir(base)) and len(names) >= 50, (base, len(names))
        probe = C3_VALUES[base][1][1]  # 0.5 / 1
        for name in names:
            cls = _c3_subclass(base, {name: ("raises", None)}, "ctl")
            assert name in cls.__dict__, "%s.%s: the override was not installed" % (base.__name__, name)
            member = cls.__dict__[name]
            try:
                if isinstance(member, property):
                    member.fget(cls(probe))
                elif isinstance(member, (classmethod, staticmethod)):
                    # type() wraps an __init_subclass__ override in classmethod automatically.
                    member.__func__(cls(probe))
                else:
                    member(cls(probe))
            except RuntimeError:
                continue
            raise AssertionError("%s.%s: the raising override did not raise" % (base.__name__, name))
    liar = _c3_subclass(float, {"__lt__": ("answers-as", 2.0)}, "ctl")(0.5)
    assert (liar < 1.0) is False and (0.5 < 1.0) is True, "the __lt__ liar does not lie"
    assert (1.0 > liar) is False, "`share > ceiling` does not reach the liar's reflected __lt__"
    assert math.isnan(float(_c3_subclass(float, {"__float__": ("answers-as", float("nan"))}, "ctl")(0.5)))
    assert float(_c3_subclass(int, {"__float__": ("answers-as", 0)}, "ctl")(1)) == 0.0
    assert _c3_subclass(int, {"bit_length": ("answers-as", 0)}, "ctl")(10 ** 5000).bit_length() == 0
    claimer = _c3_subclass(float, {"__class__": ("claims", bool)}, "ctl")(0.5)
    assert isinstance(claimer, bool) and type(claimer) is not bool, "the __class__ claim fools nothing"
    for claim in (float, int, bool):
        assert isinstance(_c3_impostor(claim), claim), "the impostor does not fool isinstance(%s)" % claim
    assert type(_c3_type_named(_C3HostileStr("C3Named")).__name__) is _C3HostileStr


def test_control_the_stub_is_the_path_review_takes():
    for ceiling, rc, n in ((0.25, 1, 4), (0.5, 0, 4), (1.5, 2, 0)):
        outcome, calls = _c3_review(ceiling)
        assert outcome[0] == "returned" and outcome[1][0] == rc, (ceiling, outcome)
        assert len(calls) == n, (ceiling, calls)


# =============================================================================================
# Coverage (orchestrator gate, written before the fix). Found by the C3 implementer:
# text that comes from OUTSIDE the guard reaches the report unescaped, so it can forge report
# lines. Three doors: the ceiling's type name (in the diagnostic), a corpus or positive PATH, and
# the candidate checker's own STDERR — which is only stripped of "\n", so the very program under
# review can print "\r" or "\u2028" followed by "review pass — ..." and forge a passing line (rc is
# still right; the human reading the report is not). A lone surrogate in any of them also makes
# the CLI's print() raise UnicodeEncodeError. And a manifest that is not UTF-8 crashes read_list
# with a traceback (it catches OSError only), where an unreadable manifest must be CANNOT CHECK.
#
# Contract: every line review() returns, and every diagnostic, is ONE printable line
# (str.isprintable()) whatever the outside text holds; a non-printable character is SHOWN, not
# dropped (the text on either side stays, with something visible between them); printable text,
# non-ASCII included, passes through unchanged; the verdict (rc) is what the printable equivalent
# gets. Pinned over every non-printable code point in the BMP (enumerated at run time, so both
# Unicode tables CI and the box carry) plus astral samples, through each of the three doors.
# =============================================================================================
import unicodedata  # noqa: E402

C4_CHARS = [c for c in range(0x10000) if not chr(c).isprintable()] + [0xE0001, 0xF0000, 0x10FFFF, 0x1D173]
C4_BEFORE, C4_AFTER = "BEFORE", "AFTER"


class _C4Stub:
    """run_checker stand-in: 'error' with a given stderr for ERR_PATH, flags FLAG_PATHS, else clean."""

    def __init__(self, err_path=None, err=""):
        self.err_path, self.err, self.flag_paths, self.calls = err_path, err, set(), []

    def __call__(self, checker, path):
        self.calls.append(path)
        if path == self.err_path:
            return "error", 3, self.err
        return ("flagged", _pa.FLAG_EXIT, "") if path in self.flag_paths else ("clean", 0, "")


def _c4_review(stub, corpus, ceiling, positives):
    real = _pa.run_checker
    _pa.run_checker = stub
    try:
        return _c3_call(review, corpus, "c4-stub-checker", ceiling, positives)
    finally:
        _pa.run_checker = real


def _c4_line_problem(lines, ch):
    """Why these report lines fail the contract for the outside text BEFORE<ch>AFTER, or None."""
    if not (type(lines) is list and all(type(ln) is str for ln in lines)):
        return "not a list of exact str: %r" % type(lines).__name__
    for ln in lines:
        if not ln.isprintable():
            bad = next(c for c in ln if not c.isprintable())
            return "a line carries U+%04X raw: %r" % (ord(bad), ln[:100])
    text = "\n".join(lines)
    if C4_BEFORE not in text or C4_AFTER not in text:
        return "the text around the character was lost: %r" % text[:160]
    if C4_BEFORE + C4_AFTER in text:
        return "the character was dropped without a trace (BEFOREAFTER)"
    return None


def _c4_door_type_name(ch):
    try:
        kind = type(C4_BEFORE + ch + C4_AFTER, (), {})
    except (ValueError, UnicodeError):
        return "skip"
    why = _c3_call(invalid_ceiling, kind())
    if why[0] != "returned" or type(why[1]) is not str:
        return "invalid_ceiling %s" % _c3_show(why)
    out = _c4_review(_C4Stub(), C3_CORPUS, kind(), C3_POSITIVES)
    if out[0] != "returned" or not _c3_exact(out[1]) or out[1][0] != 2:
        return "review %s" % _c3_show(out)
    return _c4_line_problem([why[1]], ch) or _c4_line_problem(out[1][1], ch)


def _c4_door_path(ch):
    odd = "/c4/" + C4_BEFORE + ch + C4_AFTER
    stub = _C4Stub()
    stub.flag_paths = {odd}
    out = _c4_review(stub, ["/c4/a", odd], 0.5, [odd])
    if out[0] != "returned" or not _c3_exact(out[1]) or out[1][0] != 0:
        return "review %s (a printable path gets rc 0 here)" % _c3_show(out)
    return _c4_line_problem(out[1][1], ch)


def _c4_door_stderr(ch):
    out = _c4_review(_C4Stub("/c4/b", C4_BEFORE + ch + C4_AFTER), ["/c4/a", "/c4/b"], 0.5, [])
    if out[0] != "returned" or not _c3_exact(out[1]) or out[1][0] != 2:
        return "review %s" % _c3_show(out)
    return _c4_line_problem(out[1][1], ch)


C4_DOORS = [("type name", _c4_door_type_name), ("path", _c4_door_path), ("checker stderr", _c4_door_stderr)]


# --- RED --------------------------------------------------------------------------------------

def test_outside_text_cannot_forge_or_break_a_report_line():
    failures, total, skipped = [], 0, {}
    for door, check in C4_DOORS:
        for c in C4_CHARS:
            total += 1
            why = check(chr(c))
            if why == "skip":
                skipped.setdefault(door, []).append(c)
            elif why:
                failures.append("%s U+%04X (%s): %s" % (door, c, unicodedata.category(chr(c)), why))
    # type() itself refuses a surrogate or NUL in a class name; nothing else may be skipped.
    assert set(skipped) <= {"type name"} and all(
        c == 0 or 0xD800 <= c <= 0xDFFF for c in skipped.get("type name", [])), (
        "skipped outside the expected set: %r" % {k: v[:5] for k, v in skipped.items()})
    assert not failures, _c3_report(failures, total, "let outside text break a report line") \
        .replace("numeric-subclass ceiling(s)", "door/character case(s)")


def test_a_manifest_that_is_not_utf8_is_cannot_check_not_a_traceback():
    with tempfile.TemporaryDirectory() as d:
        bad = os.path.join(d, "corpus.txt")
        with open(bad, "wb") as fh:
            fh.write(b"good/path\n\xff\xfebad\n")
        good = os.path.join(d, "ok.txt")
        with open(good, "w", encoding="utf-8") as fh:
            fh.write("x\n")
        runs = [("--corpus", ["--corpus", bad, "--checker", "true {}", "--max-share", "0.5"]),
                ("--positives", ["--corpus", good, "--positives", bad, "--checker", "true {}", "--max-share", "0.5"])]
        failures = []
        for which, argv in runs:
            p = subprocess.run([sys.executable, os.path.join(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__))), "population_arm.py")] + argv,
                capture_output=True, text=True, timeout=60)
            if p.returncode != 2 or "Traceback" in p.stderr or "CANNOT CHECK" not in p.stdout:
                failures.append("%s: rc=%s stdout=%r stderr tail=%r"
                                % (which, p.returncode, p.stdout[-160:], p.stderr[-160:]))
    assert not failures, "\n  ".join(["a non-UTF-8 manifest was not CANNOT CHECK:"] + failures)


# --- controls ---------------------------------------------------------------------------------

def test_control_printable_outside_text_passes_through_unchanged():
    for ch in ("z", " ", "é", "日", "—"):
        text = C4_BEFORE + ch + C4_AFTER
        stub = _C4Stub()
        odd = "/c4/" + text
        stub.flag_paths = {odd}
        out = _c4_review(stub, ["/c4/a", odd], 0.5, [odd])
        assert out[0] == "returned" and out[1][0] == 0 and any(text in ln for ln in out[1][1]), (ch, out)
        out = _c4_review(_C4Stub("/c4/b", text), ["/c4/a", "/c4/b"], 0.5, [])
        assert out[0] == "returned" and out[1][0] == 2 and any(text in ln for ln in out[1][1]), (ch, out)
        why = invalid_ceiling(type(text, (), {})())
        assert text in why, (ch, why)
    assert len(C4_CHARS) > 8000 and 0x2028 in C4_CHARS and 0x1B in C4_CHARS and 0x202E in C4_CHARS
    # the problem detector can fail: a raw separator, a dropped character and lost text are each caught
    assert _c4_line_problem(["x" + C4_BEFORE + "\u2028" + C4_AFTER], "\u2028")
    assert _c4_line_problem([C4_BEFORE + C4_AFTER], "\x1b")
    assert _c4_line_problem([C4_BEFORE + "\\x1b"], "\x1b")
    assert _c4_line_problem([C4_BEFORE + "\\x1b" + C4_AFTER], "\x1b") is None

# =============================================================================================
# Coverage (orchestrator gate, written before the fix). Found by the C4 implementer:
# a REAL checker process whose stdout or stderr is not UTF-8 crashes the review. run_checker
# captures with text=True, so the UnicodeDecodeError escapes subprocess.run and the CLI exits 1
# with a traceback — and exit 1 is "review FAIL", so a crash reads as a verdict. Text mode also
# rewrites every "\r" and "\r\n" a checker writes as "\n", so C4's "shown, not dropped" held for
# the in-process stand-in only: the report showed a character the checker never wrote. And the
# corpus path on the errored line, like both manifest paths, never went through C4's doors (a
# version that printed the errored path raw passed the whole module).
#
# Contract: a checker's output is display only. Whatever BYTES a real checker process writes to
# stdout or stderr, the review ends as it does for the same checker writing nothing — same exit
# code, same report, no traceback — except that an errored file's line shows its stderr: a byte
# that is not part of valid UTF-8 is shown as \xNN (its value; never dropped, replaced, or read as
# some other character), valid UTF-8 is decoded and shown as C4 shows text, and "\r" stays "\r".
# Pinned over every byte value alone (0x00-0xFF) and each malformed multi-byte form (overlong,
# encoded surrogate, beyond U+10FFFF, truncated mid-line and at the very end) through a real
# process; and over every C4 code point through the errored-line path and both manifest paths.
# =============================================================================================
import shlex  # noqa: E402

C5_CHECKER = r'''
import sys
mode, path = sys.argv[1], sys.argv[2]
data = open(path, "rb").read()
if mode == "stdout":
    sys.stdout.buffer.write(data)
elif mode in ("stderr", "error"):
    sys.stderr.buffer.write(data)
sys.stdout.flush()
sys.stderr.flush()
sys.exit(3 if mode == "error" else 1 if path.endswith(".flag") else 0)
'''
C5_MODES = ("silent", "stdout", "stderr")


def _c5_byte_shown(b):
    """How an errored line must show the lone byte b. Computed here, not by the guard."""
    if b >= 0x80:
        return "\\x%02x" % b                      # not valid UTF-8 on its own
    named = {0x09: "\\t", 0x0A: "\\n", 0x0D: "\\r"}
    if b in named:
        return named[b]
    return chr(b) if 0x20 <= b < 0x7F else "\\x%02x" % b


C5_BYTES = [(b"<" + bytes([b]) + b">", "<" + _c5_byte_shown(b) + ">") for b in range(256)]
C5_FORMS = [
    (b"<\xc0\xaf>", "<\\xc0\\xaf>"),                                  # overlong "/"
    (b"<\xe0\x80\xaf>", "<\\xe0\\x80\\xaf>"),                         # overlong, three bytes
    (b"<\xed\xa0\x80>", "<\\xed\\xa0\\x80>"),                         # an encoded surrogate
    (b"<\xf4\x90\x80\x80>", "<\\xf4\\x90\\x80\\x80>"),                # beyond U+10FFFF
    (b"<\xe2\x82>", "<\\xe2\\x82>"),                                  # truncated mid-line
    (b"<\xe2\x82", "<\\xe2\\x82"),                                    # truncated at the very end
    (b"<\xff\xc3\xa9\xff>", "<\\xffé\\xff>"),                    # valid between invalid
    (b"<a\rb\r\nc>", "<a\\rb\\r\\nc>"),                               # "\r" is not rewritten "\n"
    (b"<\xc3\xa9\xe6\x97\xa5\xf0\x9f\x98\x80>", "<é日\U0001F600>"),  # valid, printable
    (b"<\xe2\x80\xa8\xf3\xa0\x80\x81>", "<\\u2028\\U000e0001>"),      # valid, not printable
]
C5_ALL_BYTES = b"".join(p for p, _ in C5_BYTES + C5_FORMS)


def _c5_checker(d, mode):
    script = os.path.join(d, "c5_checker.py")
    if not os.path.exists(script):
        with open(script, "w", encoding="utf-8") as fh:
            fh.write(C5_CHECKER)
    return "%s -S %s %s {}" % (shlex.quote(sys.executable), shlex.quote(script), mode)


def _c5_file(d, name, data):
    path = os.path.join(d, name)
    with open(path, "wb") as fh:
        fh.write(data)
    return path


def _c5_cli(argv):
    p = subprocess.run([sys.executable, os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "population_arm.py")] + argv,
        capture_output=True, timeout=120)
    return p.returncode, p.stdout, p.stderr


def _c5_errored_line_problem(payload, want, d, i):
    path = _c5_file(d, "e%03d" % i, payload)
    out = _c3_call(review, [path], _c5_checker(d, "error"), 0.5, [])
    if out[0] != "returned" or not _c3_exact(out[1]) or out[1][0] != 2:
        return "review %s (want rc 2, the checker errored)" % _c3_show(out)
    lines = out[1][1]
    bad = [ln for ln in lines if not ln.isprintable()]
    if bad:
        return "a line is not printable: %r" % bad[0][:120]
    mine = [ln for ln in lines if path in ln]
    if len(mine) != 1:
        return "want exactly one line naming the errored file, got %d: %r" % (len(mine), lines)
    if want not in mine[0]:
        return "the line shows %r, want %r in it" % (mine[0][len(path) + 2:][:120], want)
    return None


def _c5_main_output(argv):
    buf = io.StringIO()
    real = sys.argv
    sys.argv = ["population_arm.py"] + argv
    try:
        with contextlib.redirect_stdout(buf):
            rc = _c3_call(_pa.main)
    finally:
        sys.argv = real
    return rc, buf.getvalue()


def _c5_printed_line_problem(rc, text, ch):
    if rc != ("returned", 2):
        return "main %s (want 2: the manifest cannot be read)" % _c3_show(rc)
    if text.count("\n") != 1 or not text.endswith("\n"):
        return "want ONE printed line, got %r" % text[:160]
    return _c4_line_problem([text[:-1]], ch)


def _c5_door_errored_path(ch):
    odd = "/c5/" + C4_BEFORE + ch + C4_AFTER
    out = _c4_review(_C4Stub(odd, "boom"), ["/c5/a", odd], 0.5, [])
    if out[0] != "returned" or not _c3_exact(out[1]) or out[1][0] != 2:
        return "review %s" % _c3_show(out)
    return _c4_line_problem(out[1][1], ch)


def _c5_door_manifest(which, ch, good):
    odd = "/c5-no-such-dir/" + C4_BEFORE + ch + C4_AFTER
    argv = (["--corpus", odd, "--checker", "true {}"] if which == "corpus" else
            ["--corpus", good, "--positives", odd, "--checker", "true {}"])
    rc, text = _c5_main_output(argv)
    return _c5_printed_line_problem(rc, text, ch)


# --- RED --------------------------------------------------------------------------------------

def test_checker_output_checker_output_bytes_cannot_change_the_verdict_or_crash_the_review():
    failures = []
    with tempfile.TemporaryDirectory() as d:
        flag = _c5_file(d, "a.flag", C5_ALL_BYTES)
        clean = _c5_file(d, "b", C5_ALL_BYTES)
        extra = [_c5_file(d, n, C5_ALL_BYTES) for n in ("c", "d")]
        corpus = _c5_file(d, "corpus.txt", "\n".join([flag, clean] + extra).encode("utf-8"))
        configs = {"pass": _c5_file(d, "pos_pass.txt", flag.encode("utf-8")),
                   "fail": _c5_file(d, "pos_fail.txt", clean.encode("utf-8"))}
        for config, positives in sorted(configs.items()):
            runs = {m: _c5_cli(["--corpus", corpus, "--positives", positives,
                                "--checker", _c5_checker(d, m), "--max-share", "0.5"])
                    for m in C5_MODES}
            want = runs["silent"]
            for mode in C5_MODES[1:]:
                rc, out, err = runs[mode]
                if b"Traceback" in err or (rc, out) != want[:2]:
                    failures.append("%s config, checker writes every byte to %s: rc %s, want %s "
                                    "(the same checker writing nothing); stdout tail %r; stderr tail %r"
                                    % (config, mode, rc, want[0], out[-120:], err[-160:]))
    assert not failures, "\n  ".join(["a checker's output bytes changed the review:"] + failures)


def test_checker_output_an_errored_line_shows_every_byte_its_checker_wrote():
    failures, total = [], 0
    with tempfile.TemporaryDirectory() as d:
        for i, (payload, want) in enumerate(C5_BYTES + C5_FORMS):
            total += 1
            why = _c5_errored_line_problem(payload, want, d, i)
            if why:
                failures.append("%r: %s" % (payload, why))
    assert not failures, _c3_report(failures, total, "showed a checker's stderr wrongly") \
        .replace("numeric-subclass ceiling(s)", "stderr payload(s)")


def test_checker_output_every_path_on_a_report_line_is_shown_escaped():
    failures, total, skipped = [], 0, []
    with tempfile.TemporaryDirectory() as d:
        good = _c5_file(d, "corpus.txt", b"/c5/a\n")
        doors = [("errored-line path", _c5_door_errored_path),
                 ("corpus manifest path", lambda ch: _c5_door_manifest("corpus", ch, good)),
                 ("positives manifest path", lambda ch: _c5_door_manifest("positives", ch, good))]
        for door, check in doors:
            for c in C4_CHARS:
                total += 1
                why = check(chr(c))
                if why:
                    failures.append("%s U+%04X (%s): %s" % (door, c, unicodedata.category(chr(c)), why))
    assert not failures, _c3_report(failures, total, "printed a path that breaks its line") \
        .replace("numeric-subclass ceiling(s)", "door/character case(s)")


# --- controls ---------------------------------------------------------------------------------

def test_checker_output_control_the_probes_reach_their_doors_and_the_detectors_can_fail():
    with tempfile.TemporaryDirectory() as d:
        src = _c5_file(d, "probe", C5_ALL_BYTES)
        for mode, stream in (("stdout", 1), ("stderr", 2), ("error", 2)):
            argv = shlex.split(_c5_checker(d, mode).replace("{}", shlex.quote(src)))
            p = subprocess.run(argv, capture_output=True, timeout=60)
            assert (p.stdout if stream == 1 else p.stderr) == C5_ALL_BYTES, mode
            assert p.returncode == (3 if mode == "error" else 0), (mode, p.returncode)
        # the two configurations are a real pass and a real fail when the checker is silent
        flag = _c5_file(d, "a.flag", b"")
        clean = _c5_file(d, "b", b"")
        corpus = _c5_file(d, "corpus.txt", ("%s\n%s\n" % (flag, clean)).encode("utf-8"))
        for positive, want in ((flag, 0), (clean, 1)):
            pos = _c5_file(d, "pos.txt", positive.encode("utf-8"))
            rc = _c5_cli(["--corpus", corpus, "--positives", pos,
                          "--checker", _c5_checker(d, "silent"), "--max-share", "0.5"])[0]
            assert rc == want, (positive, rc, want)
        # a printable stderr reaches the errored line unchanged
        assert _c5_errored_line_problem(b"<plain text>", "<plain text>", d, 900) is None
        # and the detector fails on a wrong display
        assert _c5_errored_line_problem(b"<plain text>", "<other>", d, 901)
    # every byte value is enumerated, and each expectation is a printable string
    assert len(C5_BYTES) == 256 and all(w.isprintable() for _, w in C5_BYTES + C5_FORMS)
    assert _c5_byte_shown(0xFF) == "\\xff" and _c5_byte_shown(0x41) == "A" and _c5_byte_shown(0x0D) == "\\r"
    # the printed-line detector catches a raw newline split, a raw character and a wrong rc
    assert _c5_printed_line_problem(("returned", 2), C4_BEFORE + "\n" + C4_AFTER + "\n", "\n")
    assert _c5_printed_line_problem(("returned", 2), C4_BEFORE + "\x1b" + C4_AFTER + "\n", "\x1b")
    assert _c5_printed_line_problem(("returned", 1), C4_BEFORE + "\\x1b" + C4_AFTER + "\n", "\x1b")
    assert _c5_printed_line_problem(("returned", 2), C4_BEFORE + "\\x1b" + C4_AFTER + "\n", "\x1b") is None


# =============================================================================================
# Coverage (orchestrator gate, written before the fix). Found by the C5 implementer
# and reproduced by the orchestrator:
#   - The errored line shows the checker's stderr through .strip(), which removes EVERY kind of
#     whitespace at either edge — "\x1c" alone showed as nothing, a no-break space and a U+2028
#     around "X" showed only "X", a leading "\r" or vertical tab vanished. Rows 6/7 claim a
#     non-printable character is shown, never dropped; at the edges it was dropped.
#   - Unpinned: the stderr cut. _shown() promises the 200-character cut falls only between
#     escapes. A decode that spells a bad byte as the four characters \xff (backslashreplace)
#     passed every test while leaving "\", "\x" or "\xf" dangling at the cut.
#   - Unpinned: decoding before cutting. A version that cut the raw bytes at 200 and then decoded
#     passed every test while showing a valid character that straddled byte 200 as byte escapes.
#
# Contract: on an errored line the checker's stderr loses nothing but its final line ending — the
# only characters that may be removed are "\r" and "\n" at the END. Every other character at
# either edge leaves a visible trace (checked by comparing against the same stderr without it, so
# no escape format is assumed). The cut falls between whole escapes, for every escape kind at
# every offset near the limit. Stderr whose decoded text fits the limit is shown whole, and a
# valid character is never shown as byte escapes because of where the cut fell.
# =============================================================================================
C6_EDGE_MAY_GO = {"\r", "\n"}          # at the END only: the final line ending is not content
# EVERY character, printable ones included: the contract is "nothing else at either edge is
# removed", and a strip of plain spaces breaks it as surely as a strip of U+2028. Every BMP code
# point, plus the C4 astral samples (the control proves no astral code point is whitespace).
C6_EDGE_CHARS = list(range(0x10000)) + [c for c in C4_CHARS if c > 0xFFFF] + [0x1F600, 0x20000]
_C6_WITHOUT = {}


def _c6_errored_lines(err):
    out = _c4_review(_C4Stub("/c6/e", err), ["/c6/a", "/c6/e"], 0.5, [])
    if out[0] != "returned" or not _c3_exact(out[1]) or out[1][0] != 2:
        return None, "review %s" % _c3_show(out)
    bad = [ln for ln in out[1][1] if not ln.isprintable()]
    return out[1][1], ("a line is not printable: %r" % bad[0][:120]) if bad else None


def _c6_edge_problem(ch, where):
    text = C4_AFTER if where == "start" else C4_BEFORE
    with_ch = ch + text if where == "start" else text + ch
    lines, why = _c6_errored_lines(with_ch)
    if why:
        return why
    if text not in _C6_WITHOUT:
        _C6_WITHOUT[text] = _c6_errored_lines(text)
    without, why = _C6_WITHOUT[text]
    if why:
        return "control " + why
    if lines == without and not (where == "end" and ch in C6_EDGE_MAY_GO):
        return "dropped: the report is identical to the one for %r alone" % text
    return None


def _c6_real_errored_line(d, name, payload):
    path = _c5_file(d, name, payload)
    out = _c3_call(review, [path], _c5_checker(d, "error"), 0.5, [])
    if out[0] != "returned" or not _c3_exact(out[1]) or out[1][0] != 2:
        return None, "review %s (want rc 2)" % _c3_show(out)
    mine = [ln for ln in out[1][1] if path in ln]
    if len(mine) != 1 or not mine[0].isprintable():
        return None, "want one printable line naming the file, got %r" % out[1][1]
    return mine[0], None


# (raw bytes, the escape the errored line shows for them — documented in _shown / rows 6 and 7)
C6_ESCAPES = [(b"\xff", "\\xff"), (b"\x1b", "\\x1b"), (b"\t", "\\t"), (b"\n", "\\n"),
              (b"\r", "\\r"), (b"\xe2\x80\xa8", "\\u2028"), (b"\xf3\xa0\x80\x81", "\\U000e0001")]


# --- RED --------------------------------------------------------------------------------------

def test_nothing_at_either_edge_of_a_checkers_stderr_is_dropped():
    failures, total = [], 0
    for where in ("start", "end"):
        for c in C6_EDGE_CHARS:
            total += 1
            why = _c6_edge_problem(chr(c), where)
            if why:
                failures.append("%s U+%04X (%s): %s" % (where, c, unicodedata.category(chr(c)), why))
    assert not failures, _c3_report(failures, total, "lost a character at the edge of stderr") \
        .replace("numeric-subclass ceiling(s)", "edge/character case(s)")


def test_the_stderr_cut_falls_between_whole_escapes():
    limit = _pa.STDERR_LIMIT
    failures, total = [], 0
    with tempfile.TemporaryDirectory() as d:
        for raw, esc in C6_ESCAPES:
            for k in range(limit - 12, limit):
                total += 1
                line, why = _c6_real_errored_line(d, "k%d_%d" % (k, raw[0]), b"a" * k + raw + b"zzzzz")
                if why:
                    failures.append("%r after %d a: %s" % (raw, k, why))
                    continue
                tail = line[line.index("a" * k) + k:]
                if tail.startswith("\\") and not tail.startswith(esc):
                    failures.append("%r after %d a: the cut split its escape: ...%r"
                                    % (raw, k, line[-30:]))
    assert not failures, _c3_report(failures, total, "cut inside an escape") \
        .replace("numeric-subclass ceiling(s)", "escape/offset case(s)")


def test_stderr_that_fits_is_shown_whole_and_is_decoded_before_the_cut():
    limit = _pa.STDERR_LIMIT
    cases = [("a" + "\u00e9" * 150, True), ("\u2014" * 66 + "ab", True), ("\u65e5" * limit, True)]
    cases += [("a" * k + "\u2014" + "b" * 5, None) for k in range(limit - 12, limit + 2)]
    failures = []
    with tempfile.TemporaryDirectory() as d:
        for i, (text, fits) in enumerate(cases):
            line, why = _c6_real_errored_line(d, "f%03d" % i, text.encode("utf-8"))
            if why:
                failures.append("%r...: %s" % (text[:12], why))
            elif "\\x" in line:
                failures.append("%r... (%d characters): valid UTF-8 shown as byte escapes: ...%r"
                                % (text[:12], len(text), line[-40:]))
            elif (fits or len(text) <= limit) and text not in line:
                failures.append("%r... (%d characters, fits %d): not shown whole: ...%r"
                                % (text[:12], len(text), limit, line[-40:]))
    assert not failures, "\n  ".join(["stderr was cut before it was decoded, or not shown whole:"]
                                     + failures)



def test_only_one_final_line_ending_is_removed():
    # "msg\n\n" is a message and a blank last line: removing the final "\n" leaves one "\n" shown.
    # A strip of EVERY trailing CR/LF would hide that blank line (and a "\r" before the ending).
    endings = ("\n", "\r", "\r\n")
    bare, why = _c6_errored_lines("X")
    assert why is None, why
    failures = []
    for first in endings:
        for last in endings:
            if (first, last) == ("\r", "\n"):
                continue                     # that pair IS one line ending, "\r\n"
            text = "X" + first + last
            lines, why = _c6_errored_lines(text)
            if why or lines == bare:
                failures.append("%r: %s" % (text, why or "more than its final line ending was removed"))
    assert not failures, "\n  ".join(["an errored line removed more than ONE final line ending:"]
                                     + failures)

# --- controls ---------------------------------------------------------------------------------

def test_control_the_edge_and_cut_detectors_can_fail():
    # the edge detector: a stripped stand-in is caught, a trailing line ending is allowed to go
    real = _pa._shown
    _pa._shown = lambda text, limit=None, **kw: real(text.strip(), limit, **kw)
    try:
        assert _c6_edge_problem("\x1c", "start") and _c6_edge_problem("\u2028", "end")
    finally:
        _pa._shown = real
    assert _c6_edge_problem("\n", "end") is None and _c6_edge_problem("\r", "end") is None
    # printable text at an edge is always visible
    assert _c6_edge_problem("z", "start") is None and _c6_edge_problem("z", "end") is None
    # every escape row is a real non-printable, and its expected escape is printable
    assert all(not r.decode("utf-8", "surrogateescape")[0].isprintable() and e.isprintable()
               for r, e in C6_ESCAPES)
    # the cut detector: a dangling escape tail is recognised, a whole one and plain text are not
    for tail, esc, split in (("\\x\u2026 (9)", "\\xff", True), ("\\xff\u2026", "\\xff", False),
                             ("\u2026 (9)", "\\xff", False), ("\\U000e\u2026", "\\U000e0001", True)):
        assert (tail.startswith("\\") and not tail.startswith(esc)) == split, tail
    assert len(C4_CHARS) > 8000 and "\x1c" in map(chr, C4_CHARS) and "\u2028" in map(chr, C4_CHARS)
    # the edge enumeration covers every character a whitespace strip can touch
    assert not [c for c in range(0x10000, 0x110000) if chr(c).isspace()]
    assert " " in map(chr, C6_EDGE_CHARS) and len(C6_EDGE_CHARS) > 0x10000
    # the one-ending detector: a stand-in that strips every trailing CR/LF is caught
    real = _pa._shown
    _pa._shown = lambda text, limit=None, **kw: real(text.rstrip("\r\n"), limit, **kw)
    try:
        assert _c6_errored_lines("X\n\n")[0] == _c6_errored_lines("X")[0]
    finally:
        _pa._shown = real


# =============================================================================================
# Coverage (orchestrator gate, written before the fix). From earlier reviews,
# three independent reviewers; each finding reproduced by the orchestrator with its own wrong version:
#   - No test required over-limit stderr to be CUT: with no stderr limit at all, all 60 tests passed
#     and the errored line showed 250 characters.
#   - No test required the final line ending to be REMOVED: a helper returning its text unchanged
#     passed all 60.
#   - The C6 edge enumerations run through a stand-in for run_checker, so the earlier .strip()
#     moved INTO run_checker, or run_checker dropping bidi and C1 controls, passed all 60.
#   - A lone byte 0x80-0xFF and the valid character U+0080-U+00FF with the same number were shown
#     identically ("\x85" for both), although run_checker's docstring says a byte is never read as
#     some other character.
#   - Pre-existing at 01a321a: a --checker with an unbalanced quote, or a corpus path holding a NUL,
#     crashed with a traceback and exit 1, the class C5 closed for output bytes.
# Contract: stderr over the limit is cut to the limit and marked with its original length;
# exactly one final line ending is removed; a real checker's stderr reaches the errored line
# exactly as the same text handed over in process does (valid UTF-8 here; invalid bytes are C5's);
# a byte and a character are never shown the same; an unusable checker command or corpus path is
# CANNOT CHECK (rc 2, no traceback).
# =============================================================================================
C7_ENDINGS = (b"\n", b"\r", b"\r\n")
C7_EDGE_CLASS = sorted({chr(n) for n in range(0x10000) if chr(n).isspace()}
                       | {chr(n) for n in range(0x80, 0xA0)}
                       | {chr(n) for n in (0x00AD, 0x061C, 0x180E, 0x200B, 0x200C, 0x200D, 0x200E,
                                           0x200F, 0x202A, 0x202B, 0x202C, 0x202D, 0x202E, 0x2060,
                                           0x2066, 0x2067, 0x2068, 0x2069, 0xFEFF)})
C7_PACKED = [chr(c) for c in C4_CHARS if not 0xD800 <= c <= 0xDFFF and c <= 0xFFFF]
C7_ASTRAL = [chr(c) for c in C4_CHARS if c > 0xFFFF]


def _c7_same_as_in_process(d, text, name):
    """Why a real checker's stderr `text` shows differently from the same text in process, or None."""
    real, why = _c6_real_errored_line(d, name, text.encode("utf-8"))
    if why:
        return "real checker: " + why
    path = os.path.join(d, name)
    out = _c4_review(_C4Stub(path, text), [path], 0.5, [])
    if out[0] != "returned" or not _c3_exact(out[1]) or out[1][0] != 2:
        return "in-process review %s" % _c3_show(out)
    mine = [ln for ln in out[1][1] if path in ln]
    if mine != [real]:
        return "real checker shows %r, in process %r" % (real[len(path) + 2:][:90],
                                                       mine[0][len(path) + 2:][:90] if mine else None)
    return None


# --- RED --------------------------------------------------------------------------------------

def test_checker_invocation_stderr_over_the_limit_is_cut_and_marked_with_its_length():
    limit = _pa.STDERR_LIMIT
    per = len("\\u2028")
    rows = ([(b"A", "A", n) for n in (limit - 1, limit, limit + 1, limit + 2, 2 * limit, 5 * limit)]
            + [(b"\xc3\xa9", "\u00e9", n) for n in (limit, limit + 1, 3 * limit)]
            + [(b"\xe2\x80\xa8", "\\u2028", n) for n in (limit // per, limit // per + 1, limit)])
    failures = []
    with tempfile.TemporaryDirectory() as d:
        for unit, shown, n in rows:
            line, why = _c6_real_errored_line(d, "cut", unit * n)
            if why:
                failures.append("%r x %d: %s" % (unit, n, why))
                continue
            fits = len(shown) * n <= limit
            keep = n if fits else limit // len(shown)
            whole, over = shown * keep, shown * (keep + 1)
            if whole not in line or over in line:
                failures.append("%r x %d (%d shown characters, limit %d): want exactly %d shown, "
                                "line ends ...%r" % (unit, n, len(shown) * n, limit, keep, line[-50:]))
            elif not fits and str(n) not in line[line.index(whole) + len(whole):]:
                failures.append("%r x %d: cut, but the original length %d is not marked after it: "
                                "...%r" % (unit, n, n, line[-50:]))
    assert not failures, "\n  ".join(["stderr over the limit was not cut to it:"] + failures)


def test_checker_invocation_a_real_checkers_final_line_ending_is_removed_and_only_one():
    failures = []
    with tempfile.TemporaryDirectory() as d:
        bare, why = _c6_real_errored_line(d, "end", b"X")
        assert why is None, why
        for e in C7_ENDINGS:
            line, why = _c6_real_errored_line(d, "end", b"X" + e)
            if why or line != bare:
                failures.append("%r: %s" % (b"X" + e, why or "its final line ending was not removed"))
        for first in C7_ENDINGS:
            for last in C7_ENDINGS:
                if (first, last) == (b"\r", b"\n"):
                    continue                     # that pair IS one line ending
                line, why = _c6_real_errored_line(d, "end", b"X" + first + last)
                if why or line == bare:
                    failures.append("%r: %s" % (b"X" + first + last,
                                                why or "more than its final line ending was removed"))
    assert not failures, "\n  ".join(["a real checker's line ending was mishandled:"] + failures)


def test_checker_invocation_a_real_checkers_stderr_reaches_the_report_as_the_same_text_in_process_does():
    texts = [("edge", c + "X" + c) for c in C7_EDGE_CLASS]
    texts += [("ending", "X" + a + b) for a in ("", "\n", "\r", "\r\n") for b in ("\n", "\r", "\r\n")]
    texts += [("leading", e + "X") for e in ("\n", "\r", "\r\n", "\n\n")]
    texts += [("packed", "<" + "".join(C7_PACKED[i:i + 30]) + ">") for i in range(0, len(C7_PACKED), 30)]
    texts += [("astral", "<" + "".join(C7_ASTRAL) + ">")]
    failures = []
    with tempfile.TemporaryDirectory() as d:
        for kind, text in texts:
            why = _c7_same_as_in_process(d, text, "same")
            if why:
                failures.append("%s %r: %s" % (kind, text[:12], why))
    assert not failures, ("%d of %d real-checker stderr text(s) reached the report changed:\n  "
                          % (len(failures), len(texts))) + "\n  ".join(failures[:30])


def test_checker_invocation_a_byte_and_a_character_are_never_shown_the_same():
    failures = []
    with tempfile.TemporaryDirectory() as d:
        for n in range(0x80, 0x100):
            byte_line, w1 = _c6_real_errored_line(d, "bc", b"<" + bytes([n]) + b">")
            char_line, w2 = _c6_real_errored_line(d, "bc", b"<" + chr(n).encode("utf-8") + b">")
            if w1 or w2:
                failures.append("0x%02X: %s" % (n, w1 or w2))
            elif byte_line == char_line:
                failures.append("the lone byte 0x%02X and the character U+%04X both show as %r"
                                % (n, n, byte_line[byte_line.index("<"):]))
    assert not failures, ("%d of 128 byte/character pair(s) are shown the same:\n  " % len(failures)
                          + "\n  ".join(failures[:20]))


def test_checker_invocation_an_unusable_checker_command_or_corpus_path_is_cannot_check_not_a_traceback():
    failures = []
    with tempfile.TemporaryDirectory() as d:
        good = _c5_file(d, "f", b"x")
        corpus = _c5_file(d, "corpus.txt", good.encode("utf-8"))
        nul_corpus = _c5_file(d, "corpus_nul.txt", (good + "\n" + good + "\x00x\n").encode("utf-8"))
        runs = [("an unbalanced quote in --checker", ["--corpus", corpus, "--checker", "grep -q 'x {}"]),
                ("a NUL in a corpus path", ["--corpus", nul_corpus, "--checker", "true {}"])]
        for label, argv in runs:
            rc, out, err = _c5_cli(argv)
            if rc != 2 or b"Traceback" in err or b"CANNOT CHECK" not in out:
                failures.append("%s: rc %s, want 2 CANNOT CHECK; stdout %r; stderr tail %r"
                                % (label, rc, out[-100:], err[-160:]))
    assert not failures, "\n  ".join(["an unusable input crashed instead of CANNOT CHECK:"] + failures)


def test_checker_invocation_a_blank_or_non_text_checker_is_cannot_check():
    # Found by the C7 implementer: a command of blanks splits into no arguments and
    # subprocess.run([]) raises IndexError (rc 1 and a traceback through the CLI, whose own
    # guard catches only the empty string); a checker that is not a str made shlex.split raise
    # for a direct caller of review(). Both are configuration errors: CANNOT CHECK, rc 2.
    failures = []
    with tempfile.TemporaryDirectory() as d:
        good = _c5_file(d, "f", b"x")
        corpus = _c5_file(d, "corpus.txt", good.encode("utf-8"))
        for blank in ("   ", "\t", " \n "):
            rc, out, err = _c5_cli(["--corpus", corpus, "--checker", blank])
            if rc != 2 or b"Traceback" in err or b"CANNOT CHECK" not in out:
                failures.append("CLI --checker %r: rc %s; stdout %r; stderr tail %r"
                                % (blank, rc, out[-80:], err[-120:]))
        for checker in ("   ", "", 5, None, b"true {}", ["true", "{}"]):
            got = _c3_call(review, [good], checker, 0.5, [])
            if got[0] != "returned" or not _c3_exact(got[1]) or got[1][0] != 2:
                failures.append("review(checker=%r) %s" % (checker, _c3_show(got)))
    assert not failures, "\n  ".join(["an unusable checker was not CANNOT CHECK:"] + failures)


# --- controls ---------------------------------------------------------------------------------

def test_checker_invocation_control_the_probes_reach_their_doors_and_the_detectors_can_fail():
    assert len(C7_PACKED) > 7500 and "\u202e" in C7_EDGE_CLASS and "\x85" in C7_EDGE_CLASS
    assert all(not c.isprintable() for c in C7_PACKED + C7_ASTRAL) and C7_ASTRAL
    with tempfile.TemporaryDirectory() as d:
        assert _c7_same_as_in_process(d, "\x1cX\u202e", "ok") is None
        # a run_checker that strips its stderr is caught by the same-text comparison
        real = _pa.run_checker
        _pa.run_checker = lambda checker, path: (lambda r: (r[0], r[1], (r[2] or "").strip()))(real(checker, path))
        try:
            assert _c7_same_as_in_process(d, "\x1cX\x1c", "strip")
        finally:
            _pa.run_checker = real
        # the cut row arithmetic: a text exactly at the limit fits, one more does not
        line, why = _c6_real_errored_line(d, "ctl", b"A" * _pa.STDERR_LIMIT)
        assert why is None and "A" * _pa.STDERR_LIMIT in line, (why, line[-40:])


# =============================================================================================
# Coverage (orchestrator gate, written before the fix). From earlier reviews,
# three independent reviewers; each finding reproduced by the orchestrator with its own wrong
# version, and each wrong version passed all 67 tests of this file:
#   - a cut marker stating ten times the length: C7 accepted any line holding the digits
#   - a retained "\r" rewritten as "\n": C7 checked only that SOMETHING was left
#   - U+0085 shown as \x86 (the byte 0x86's form), or U+0080-U+00FF shown as \U000000NN or as
#     \u85: C7 compared a byte only with the character of the SAME number, only for inequality
#   - run_checker stripping C0 controls (NUL, ESC, DEL, ...) from the edges of stderr, or only
#     trailing NULs: no C0 control that is not whitespace was ever put at an edge
#   - an unbalanced quote or a NUL path caught only in main(): review() raised for a direct
#     caller, and C7 tried both through the CLI only
#   - STDERR_LIMIT = 100000: the documented 200 was pinned nowhere
# Also reproduced, pre-existing at 01a321a: an object whose __class__ claims str passed the
# non-string check and review() raised TypeError; a corpus path that is None or an int raised
# TypeError. Found by the orchestrator while closing that class: a str subclass whose methods
# lie changes the verdict when it is a corpus or positive path (a path whose ==/hash() claim
# another path is counted as that path; a missed positive is reported as flagged).
# Contract:
#   - the errored line shows a checker's stderr as exactly: its shown text cut to 200
#     characters between escapes, then (only when cut) "… (N characters)", N = the length of the
#     stderr without its final line ending, one character per undecodable byte
#   - every byte and every character has ONE documented form: a byte 0x80-0xFF is \xNN; a
#     non-printable character U+0080-U+00FF is \u00NN; below 0x80 the byte and the character are
#     the same thing (\t \n \r, else \xNN when not printable); printable text is itself
#   - what remains after the ONE final line ending is removed is shown as written ("\r" as \r)
#   - run_checker returns a checker's stderr whole, whatever character is at either edge
#   - review() judges the checker and every corpus and positive path by the text it stores (a
#     str, or a str subclass whose methods are never run), and for any value it cannot use it
#     returns CANNOT CHECK (rc 2) and never raises: corpus and positives are an exact list or
#     tuple of such paths (a subclass is refused: its iteration can yield what it does not store)
# =============================================================================================
C8_LIMIT = 200                   # the documented limit (STDERR_LIMIT; "a limit of 200" in _shown)
C8_MARK = chr(0x2026) + " (%d characters)"
C8_ENDING_SHOWN = {b"\n": "\\n", b"\r": "\\r", b"\r\n": "\\r\\n"}
C8_C0_NOT_SPACE = [chr(n) for n in list(range(0x20)) + [0x7F] if not chr(n).isspace()]


def _c8_shown_stderr(d, name, payload):
    """(the stderr part of the errored line of a real checker that wrote `payload`, why-not)."""
    line, why = _c6_real_errored_line(d, name, payload)
    if why:
        return None, why
    head = "  %s -> exit 3  " % os.path.join(d, name)
    if not line.startswith(head):
        return None, "the errored line does not start %r: %r" % (head, line[:120])
    return line[len(head):], None


def _c8_form(n, is_byte):
    """The documented display of the byte n, or of the character U+00NN: the test's own table."""
    if n in (0x09, 0x0A, 0x0D):
        return {0x09: "\\t", 0x0A: "\\n", 0x0D: "\\r"}[n]
    if is_byte and n >= 0x80:
        return "\\x%02x" % n
    if chr(n).isprintable():
        return chr(n)
    return ("\\x%02x" if n < 0x80 else "\\u%04x") % n


class _C8Run:
    """subprocess.run stand-in: the process wrote `stderr` (bytes) and exited `rc`."""

    def __init__(self):
        self.stderr, self.rc = b"", 3

    def __call__(self, cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, self.rc, b"", self.stderr)


def _c8_whole_problems(payloads, rcs=(3,)):
    """Why run_checker does not return each payload whole (as surrogateescape text), per case."""
    fake, real = _C8Run(), _pa.subprocess.run
    _pa.subprocess.run = fake
    failures = []
    try:
        for rc in rcs:
            fake.rc = rc
            outcome = {0: "clean", 1: "flagged"}.get(rc, "error")
            for p in payloads:
                fake.stderr = p
                want = (outcome, rc, p.decode("utf-8", errors="surrogateescape"))
                got = _c3_call(_pa.run_checker, "c8-checker {}", "c8-path")
                if got[0] != "returned" or not _c3_exact(got[1]) or got[1] != want:
                    failures.append("stderr %r, exit %d: %s" % (p[:16], rc, _c3_show(got)))
    finally:
        _pa.subprocess.run = real
    return failures


class _C8ClaimsStr:
    """Not a str. Its __class__ claims str, so isinstance(x, str) is True, and it compares equal
    to everything with the hash of `claims`."""
    __class__ = property(lambda self: str)

    def __init__(self, claims="true {}"):
        self._claims = claims

    def __str__(self):
        return self._claims

    def __eq__(self, other):
        return True

    def __hash__(self):
        return hash(self._claims)


class _C8LyingStr(str):
    """A str that STORES one text and whose every method answers for `claims`."""

    def __new__(cls, stored, claims):
        self = str.__new__(cls, stored)
        self._claims = claims
        return self

    def __eq__(self, other):
        return True

    def __ne__(self, other):
        return False

    def __hash__(self):
        return hash(self._claims)

    def __str__(self):
        return self._claims

    def __repr__(self):
        return repr(self._claims)

    def __format__(self, spec):
        return self._claims

    def __len__(self):
        return len(self._claims)

    def __iter__(self):
        return iter(self._claims)

    def __getitem__(self, i):
        return self._claims[i]

    def __contains__(self, x):
        return x in self._claims

    def encode(self, *a, **k):
        return self._claims.encode(*a, **k)

    def split(self, *a, **k):
        return self._claims.split(*a, **k)

    def startswith(self, *a):
        return self._claims.startswith(*a)

    def endswith(self, *a):
        return self._claims.endswith(*a)


class _C8List(list):
    """A list whose iteration yields nothing it stores."""

    def __iter__(self):
        return iter(["/nonexistent/c8-other-path"])


class _C8Tuple(tuple):
    """A tuple whose iteration yields nothing it stores."""

    def __iter__(self):
        return iter(["/nonexistent/c8-other-path"])


def _c8_cannot_check_problem(out):
    """Why a review() outcome is not a returned, plain CANNOT CHECK (rc 2) report, or None."""
    if out[0] != "returned" or not _c3_exact(out[1]):
        return _c3_show(out)
    rc, lines = out[1]
    if rc != 2 or not lines or not lines[0].startswith("CANNOT CHECK"):
        return "want rc 2 CANNOT CHECK, " + _c3_show(out)
    if not all(ln.isprintable() for ln in lines):
        return "a line is not printable: %r" % lines
    return None


# --- RED --------------------------------------------------------------------------------------

def test_stored_value_every_byte_and_character_to_0xff_is_shown_in_its_one_documented_form():
    rows = ([("byte", bytes([n]), n, True) for n in range(0x80, 0x100)]
            + [("char", chr(n).encode("utf-8"), n, False) for n in range(0x100)])
    failures = []
    with tempfile.TemporaryDirectory() as d:
        for i in range(0, len(rows), 16):
            group = rows[i:i + 16]
            payload = b"<" + b"|".join(r[1] for r in group) + b">"
            want = "<" + "|".join(_c8_form(r[2], r[3]) for r in group) + ">"
            got, why = _c8_shown_stderr(d, "forms", payload)
            if why or got != want:
                failures.append("%s 0x%02X-0x%02X: %s" % (group[0][0], group[0][2], group[-1][2],
                                                          why or "shows %r, want %r" % (got, want)))
    assert not failures, "\n  ".join(["a byte or character was not shown in its documented form:"]
                                     + failures)


def test_stored_value_a_cut_line_is_exactly_the_first_200_shown_characters_then_the_length():
    rows = ([(b"A", "A", n, b"") for n in (199, 200, 201, 10000)]
            + [(b"A", "A", 300, e) for e in (b"\n", b"\r", b"\r\n")]       # the ending: not counted
            + [(b"\xc3\xa9", chr(0xE9), n, b"") for n in (200, 201, 1000)]
            + [(b"\xe2\x80\xa8", "\\u2028", n, b"") for n in (33, 34, 1000)]
            + [(b"\xff", "\\xff", n, b"") for n in (50, 51, 1000)]
            + [(b"\x1b", "\\x1b", n, b"\n") for n in (50, 51, 1000)])
    failures = []
    with tempfile.TemporaryDirectory() as d:
        for unit, shown, n, end in rows:
            if len(shown) * n <= C8_LIMIT:
                want = shown * n
            else:
                want = shown * (C8_LIMIT // len(shown)) + C8_MARK % n
            got, why = _c8_shown_stderr(d, "cut", unit * n + end)
            if why or got != want:
                failures.append("%r x %d + %r: %s" % (unit, n, end, why or "shows ...%r, want ...%r"
                                                      % (got[-60:], want[-60:])))
    assert not failures, "\n  ".join(["a long stderr was not cut exactly:"] + failures)


def test_stored_value_what_remains_after_the_final_line_ending_is_shown_as_written():
    endings = list(C8_ENDING_SHOWN)
    rows = [(b"X" + e, "X") for e in endings]
    rows += [(b"X" + a + b, "X" + C8_ENDING_SHOWN[a]) for a in endings for b in endings
             if (a, b) != (b"\r", b"\n")]                            # that pair IS one ending
    rows += [(e + b"X", C8_ENDING_SHOWN[e] + "X") for e in endings]
    rows += [(b"X\n\n\n", "X\\n\\n"), (b"X\r\r\r", "X\\r\\r"), (b"\r\nX\r\n\r\n", "\\r\\nX\\r\\n")]
    failures = []
    with tempfile.TemporaryDirectory() as d:
        for payload, want in rows:
            got, why = _c8_shown_stderr(d, "end", payload)
            if why or got != want:
                failures.append("%r: %s" % (payload, why or "shows %r, want %r" % (got, want)))
    assert not failures, "\n  ".join(["a line ending was changed or removed:"] + failures)


def test_stored_value_run_checker_returns_stderr_whole_whatever_is_at_either_edge():
    chars = [chr(n) for n in range(0x10000) if not 0xD800 <= n <= 0xDFFF]
    chars += [chr(n) for n in (0x10000, 0x1D4B6, 0x1F600, 0xE0001, 0x10FFFF)]
    payloads = [(c + "X" + c).encode("utf-8") for c in chars]
    payloads += [bytes([b]) + b"X" + bytes([b]) for b in range(0x100)]
    payloads += [b"\x00\x00X\x00\x00", b"\x1b[31m boom \x00", b"\x7fX\x7f\n"]
    failures = _c8_whole_problems(payloads)
    failures += _c8_whole_problems([bytes([b]) + b"X" + bytes([b]) for b in range(0x100)], rcs=(0, 1))
    assert not failures, ("%d case(s) where run_checker did not return stderr whole:\n  "
                          % len(failures)) + "\n  ".join(failures[:30])


def test_stored_value_a_real_checkers_c0_controls_at_either_edge_are_shown():
    rows = [((c + "X" + c).encode("utf-8"), _c8_form(ord(c), False) + "X" + _c8_form(ord(c), False))
            for c in C8_C0_NOT_SPACE]
    rows += [(b"\x1b[31m boom \x00", "\\x1b[31m boom \\x00"), (b"\x00\x00X\x00\x00\n", "\\x00\\x00X\\x00\\x00")]
    failures = []
    with tempfile.TemporaryDirectory() as d:
        for payload, want in rows:
            got, why = _c8_shown_stderr(d, "c0", payload)
            if why or got != want:
                failures.append("%r: %s" % (payload, why or "shows %r, want %r" % (got, want)))
    assert not failures, "\n  ".join(["a C0 control at an edge was not shown:"] + failures)


def test_stored_value_a_direct_review_call_with_an_unusable_input_is_cannot_check():
    failures = []
    with tempfile.TemporaryDirectory() as d:
        good = _c5_file(d, "f", b"x")
        rows = [
            ("an unbalanced quote in the checker", [good], "grep -q 'x {}", []),
            ("a trailing backslash in the checker", [good], "true {} \\", []),
            ("a checker whose __class__ claims str", [good], _C8ClaimsStr(), []),
            ("a checker that is bytes", [good], b"true {}", []),
            ("a NUL in a corpus path", [good, good + "\x00x"], "true {}", []),
            ("a corpus path the file system cannot encode", [good, chr(0xD800) + "x"], "true {}", []),
            ("a corpus path that is None", [good, None], "true {}", []),
            ("a corpus path that is an int", [good, 5], "true {}", []),
            ("a corpus path whose __class__ claims str", [good, _C8ClaimsStr(good)], "true {}", []),
            ("a corpus that is a str", good, "true {}", []),
            ("a corpus that is an int", 5, "true {}", []),
            ("a positive that is None", [good], "true {}", [None]),
            ("a positive that is a list", [good], "true {}", [[good]]),
            ("a positive whose __class__ claims str", [good], "true {}", [_C8ClaimsStr(good)]),
            ("positives that are None", [good], "true {}", None),
            ("positives that are a str", [good], "true {}", good),
            ("a corpus that is a list subclass", _C8List([good]), "true {}", []),
            ("positives that are a tuple subclass", [good], "true {}", _C8Tuple((good,))),
        ]
        for label, corpus, checker, positives in rows:
            why = _c8_cannot_check_problem(_c3_call(review, corpus, checker, 0.5, positives))
            if why:
                failures.append("%s: %s" % (label, why))
    assert not failures, "\n  ".join(["a direct review() call did not return CANNOT CHECK:"]
                                     + failures)


def test_stored_value_the_checker_and_every_path_are_judged_by_the_text_they_store():
    failures = []
    with tempfile.TemporaryDirectory() as d:
        clean, flag = _c5_file(d, "a.txt", b"x"), _c5_file(d, "b.flag", b"x")
        missed = os.path.join(d, "c.flag")                    # a positive NOT in the corpus
        checker = _c5_checker(d, "silent")
        rows = [
            ("a corpus path claiming to be another corpus path",
             ([clean, _C8LyingStr(flag, clean)], checker, 0.5, []), ([clean, flag], checker, 0.5, [])),
            ("a corpus path claiming to be flagged",
             ([_C8LyingStr(clean, flag), flag], checker, 0.9, []), ([clean, flag], checker, 0.9, [])),
            ("a missed positive claiming to be a flagged path",
             ([clean, flag], checker, 0.9, [_C8LyingStr(missed, flag)]),
             ([clean, flag], checker, 0.9, [missed])),
            ("a flagged positive claiming to be a missed path",
             ([clean, flag], checker, 0.9, [_C8LyingStr(flag, missed)]),
             ([clean, flag], checker, 0.9, [flag])),
            ("a checker claiming to be another command",
             ([clean, flag], _C8LyingStr(checker, "false {}"), 0.9, [flag]),
             ([clean, flag], checker, 0.9, [flag])),
        ]
        for label, lying, plain in rows:
            got, want = _c3_call(review, *lying), _c3_call(review, *plain)
            if want[0] != "returned" or not _c3_exact(want[1]):
                failures.append("%s: the plain call itself %s" % (label, _c3_show(want)))
            elif got != want:
                failures.append("%s: %s, want the plain result %s" % (label, _c3_show(got),
                                                                     _c3_show(want)))
    assert not failures, "\n  ".join(["a str subclass was judged by its methods:"] + failures)


# --- controls ---------------------------------------------------------------------------------

def test_stored_value_control_the_oracles_and_probes_can_fail():
    forms = ([_c8_form(n, True) for n in range(0x80, 0x100)]
             + [_c8_form(n, False) for n in range(0x100)])
    assert len(set(forms)) == 384                      # no two documented forms are the same
    # each wrong display the reviews built disagrees with the table
    assert _c8_form(0x85, False) not in ("\\x85", "\\x86", "\\U00000085", "\\u85")
    assert _c8_form(0x85, False) == "\\u0085" and _c8_form(0x86, True) == "\\x86"
    # the probe objects really do lie
    claims, lying = _C8ClaimsStr(), _C8LyingStr("b.flag", "a.txt")
    assert isinstance(claims, str) and type(claims) is not str
    assert lying == "anything" and hash(lying) == hash("a.txt") and str(lying) == "a.txt"
    assert str.__str__(lying) == "b.flag"
    # the subprocess.run stand-in reaches run_checker, and a stripping run_checker is caught
    assert not _c8_whole_problems([b"\x00X\x00"])
    real = _pa.run_checker
    _pa.run_checker = lambda checker, path: (lambda r: (r[0], r[1], r[2].strip("\x00")))(
        real(checker, path))
    try:
        assert _c8_whole_problems([b"\x00X\x00"])
    finally:
        _pa.run_checker = real
    # a real checker's shown stderr is found on its line; a text at the limit fits whole
    with tempfile.TemporaryDirectory() as d:
        got, why = _c8_shown_stderr(d, "ctl", b"A" * C8_LIMIT)
        assert why is None and got == "A" * C8_LIMIT, (why, got)


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
