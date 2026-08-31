"""Gate for guard/brief_scan.py — every leak pattern must fire, and none of the breadth
controls may.

The scanner was measured wrong in BOTH directions, and both directions are held shut
here. Too wide: its own paired skill's prescribed wording was flagged, and 4 of 8
refute-framed briefs were false positives on mandated language. Too narrow: four genuine
leak spellings passed clean. The clean brief contains none of the ambiguity classes and so
cannot measure over-breadth on its own, so neutral, negated and quoted counter-fixtures
are asserted alongside it.

Red demos: BS1 neuters a pattern (its plant stops firing); BS2 disables the negation
suppression (the mandated wording is flagged again); BS3 narrows the expected-answer
pattern back (its plant stops firing).

Runs two ways: under pytest and standalone —
`python3 guard/tests/test_brief_scan.py` — printing the all-pass marker the mutation
harness anchors on.
"""
import contextlib
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from guard.brief_scan import (  # noqa: E402
    CLEAN_BRIEF, COUNTER_FIXTURES, LEAKING_LINES, PATTERNS, scan_text, selftest)

MARKER = "BRIEF SCAN HAS TEETH - ALL CHECKS PASSED"


def test_every_pattern_fires_on_its_plant():
    fired = set()
    for line in LEAKING_LINES:
        for hit in scan_text(line):
            fired.add(hit.split("[", 1)[1].split("]", 1)[0])
    missing = [name for name, _ in PATTERNS if name not in fired]
    assert not missing, ("pattern(s) with no reachable plant — a rule that cannot fire "
                         "guards nothing: %r" % missing)


def test_clean_refute_framed_brief_stays_clean():
    hits = scan_text(CLEAN_BRIEF, "clean-brief")
    assert not hits, ("the clean control was flagged — an always-firing tripwire is "
                      "switched off socially: %r" % hits)


def test_every_planted_line_is_flagged():
    """Per-line, not just per-pattern: a pattern can be reachable through one plant while
    another spelling of the same leak sails past."""
    missed = [line for line in LEAKING_LINES if not scan_text(line)]
    assert not missed, ("planted leak(s) not flagged at all — these four were the measured "
                        "false negatives: %r" % missed)


def test_the_four_measured_false_negatives_are_flagged():
    for line in ["The expected answer is that the library regressed in v2.",
                 "I believe the cache layer is at fault.",
                 "We're confident the cache layer is at fault.",
                 "Prove the second endpoint is unreachable."]:
        assert scan_text(line), "a measured genuine leak still passes clean: %r" % line


def test_the_counter_fixtures_measure_over_breadth():
    """Neutral, negated and quoted wording — the ambiguity classes the clean brief does not
    contain, so without these the over-breadth direction is unmeasured."""
    for label, lines in COUNTER_FIXTURES:
        for line in lines:
            hits = scan_text(line, "%s-control" % label)
            assert not hits, ("%s counter-fixture flagged — an always-firing tripwire is "
                              "switched off socially: %r" % (label, hits))


def test_the_paired_skills_own_prescribed_wording_is_not_a_leak():
    """The measured absurdity: the sentence instructing you not to leak a hypothesis was
    itself flagged as a leaked hypothesis."""
    for line in ["Never hand a leg the hypothesis — ask it to REFUTE.",
                 "do not confirm our prior work"]:
        assert not scan_text(line), "the instruction against leaking was flagged: %r" % line


def test_a_negated_leak_still_fires_when_the_negation_follows_it():
    """Breadth control on the suppressor: only text BEFORE the match suppresses, so a leak
    whose sentence happens to contain a later "not" is still a leak."""
    assert scan_text("We believe the parser is not at fault."), (
        "the negation suppressor swallowed a real leak")


def test_hits_carry_source_line_and_pattern():
    hits = scan_text("We believe the cache layer is at fault.\n", "brief.md")
    assert hits and hits[0].startswith("brief.md:1:"), hits
    assert "[dispatcher-belief]" in hits[0], hits


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
