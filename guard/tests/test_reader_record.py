"""Gate for guard/reader_record.py — valid-empty must stay distinguishable from every
cannot-read state in the RETURNED RECORD.

The defect this holds shut: a reader whose safe default collapses missing / permission /
parse failure into looks-empty makes every consumer report an observation it never made.
Red demo: mutation RR1 in guard/mutation_harness.py collapses the permission state into
empty and this gate must go red.

Runs two ways: under pytest and standalone —
`python3 guard/tests/test_reader_record.py` — printing the all-pass marker the mutation
harness anchors on.
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from guard.reader_record import read_state  # noqa: E402

MARKER = "READER STATES DISTINGUISHED - ALL CHECKS PASSED"

_IS_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0

try:
    import pytest
except ImportError:
    pytest = None


def test_ok_read_carries_the_value():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "s.json")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write('{"n": 3}')
        rec = read_state(p, parse=json.loads)
    assert rec["status"] == "ok" and rec["value"] == {"n": 3}, repr(rec)


def test_empty_is_a_valid_observation_not_an_error():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "s.txt")
        open(p, "w", encoding="utf-8").close()
        rec = read_state(p)
    assert rec["status"] == "empty", "a read empty source is DATA: %r" % rec


def test_missing_is_not_empty():
    rec = read_state(os.path.join(tempfile.gettempdir(), "no-such-file-9f3a2.txt"))
    assert rec["status"] == "missing", (
        "an absent source collapsed into %r — the consumer would report an observation "
        "it never made" % rec["status"])


def test_permission_is_not_empty():
    if _IS_ROOT:
        if pytest:
            pytest.skip("chmod 000 does not bar root")
        return
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "s.txt")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("secret content\n")
        os.chmod(p, 0)
        try:
            rec = read_state(p)
        finally:
            os.chmod(p, 0o600)
    assert rec["status"] == "permission", (
        "an unreadable source collapsed into %r — cannot-read reported as looks-empty"
        % rec["status"])


def test_parse_failure_is_not_empty_and_does_not_raise():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "s.json")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        rec = read_state(p, parse=json.loads)
    assert rec["status"] == "parse-error", repr(rec)


def test_the_states_are_pairwise_distinct():
    seen = set()
    with tempfile.TemporaryDirectory() as d:
        ok = os.path.join(d, "ok.txt")
        with open(ok, "w", encoding="utf-8") as fh:
            fh.write("x")
        empty = os.path.join(d, "empty.txt")
        open(empty, "w", encoding="utf-8").close()
        bad = os.path.join(d, "bad.json")
        with open(bad, "w", encoding="utf-8") as fh:
            fh.write("{")
        seen.add(read_state(ok)["status"])
        seen.add(read_state(empty)["status"])
        seen.add(read_state(os.path.join(d, "absent"))["status"])
        seen.add(read_state(bad, parse=json.loads)["status"])
    assert len(seen) == 4, ("four different realities produced only %d distinct "
                            "statuses: %r" % (len(seen), sorted(seen)))


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
