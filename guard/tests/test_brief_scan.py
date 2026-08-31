"""Gate for guard/brief_scan.py — every leak pattern must fire, and the clean refute-framed
brief must not.

Red demo: mutation BS1 in guard/mutation_harness.py neuters one pattern and this gate
must go red (its plant stops firing).

Runs two ways: under pytest and standalone —
`python3 guard/tests/test_brief_scan.py` — printing the all-pass marker the mutation
harness anchors on.
"""
import contextlib
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from guard.brief_scan import CLEAN_BRIEF, LEAKING_LINES, PATTERNS, scan_text, selftest  # noqa: E402

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
