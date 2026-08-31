"""Gate for guard/reader_record.py — valid-empty must stay distinguishable from every
cannot-read state in the RETURNED RECORD.

The defect this holds shut: a reader whose safe default collapses missing / permission /
not-a-file / decode failure / parse failure into looks-empty makes every consumer report
an observation it never made. Red demo: mutation RR1 in guard/mutation_harness.py
collapses the permission state into empty and this gate must go red.

THREE DEFECTS IN THIS GATE ITSELF, ALSO FIXED
  * The permission case skipped with a bare `return` under root, because `chmod 000`
    cannot bar root — and a skip that counted as a pass left RR1 able to survive while the
    standalone script still printed its all-pass marker. The permission branch is now
    produced through the reader's `opener` seam, so it is exercised on every host.
  * `test_the_states_are_pairwise_distinct` omitted the permission state entirely, so the
    one state RR1 attacks was outside the distinctness assertion.
  * The standalone runner had no notion of a skip. It does now, and a skip WITHHOLDS the
    marker and exits 2 (UNMEASURED) instead of counting as ok. Nothing currently skips;
    the mechanism exists so that a future skip cannot pass silently.

Runs two ways: under pytest and standalone —
`python3 guard/tests/test_reader_record.py` — printing the all-pass marker the mutation
harness anchors on.
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from guard.reader_record import (  # noqa: E402
    UNVERIFIABLE, read_state, refusing_opener, selftest)

MARKER = "READER STATES DISTINGUISHED - ALL CHECKS PASSED"


class Skip(Exception):
    """A case that could not run. It withholds the marker; it is never an ok."""


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
    """Produced through the reader's opener seam rather than `chmod 000`, so it runs as
    root too. This is the branch mutation RR1 attacks."""
    rec = read_state("<a source this process may not open>", opener=refusing_opener)
    assert rec["status"] == "permission", (
        "an unreadable source collapsed into %r — cannot-read reported as looks-empty"
        % rec["status"])


def test_a_directory_is_not_a_permission_problem():
    """IsADirectoryError is an OSError and landed on the permission branch, pointing the
    operator at file modes when the fault is a wrong path."""
    with tempfile.TemporaryDirectory() as d:
        rec = read_state(d)
    assert rec["status"] == "not-a-file", (
        "a directory reported %r — a status that sends the reader looking at modes "
        "instead of at the path" % rec["status"])


def test_undecodable_bytes_do_not_raise():
    """The reader is documented as never raising; UnicodeDecodeError is a ValueError, not
    an OSError, and it comes out of fh.read() — measured on a one-byte 0xff file."""
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "raw.bin")
        with open(p, "wb") as fh:
            fh.write(b"\xff")
        rec = read_state(p)          # must not raise
    assert rec["status"] == "decode-error", repr(rec)
    assert rec["value"] is None


def test_parse_failure_is_not_empty_and_does_not_raise():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "s.json")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        rec = read_state(p, parse=json.loads)
    assert rec["status"] == "parse-error", repr(rec)


def test_the_states_are_pairwise_distinct():
    """Every state, INCLUDING permission — which the first version of this assertion left
    out, so the one state RR1 attacks was never in the distinctness check."""
    seen = {}
    with tempfile.TemporaryDirectory() as d:
        ok = os.path.join(d, "ok.txt")
        with open(ok, "w", encoding="utf-8") as fh:
            fh.write("x")
        empty = os.path.join(d, "empty.txt")
        open(empty, "w", encoding="utf-8").close()
        bad = os.path.join(d, "bad.json")
        with open(bad, "w", encoding="utf-8") as fh:
            fh.write("{")
        raw = os.path.join(d, "raw.bin")
        with open(raw, "wb") as fh:
            fh.write(b"\xff")
        seen["ok"] = read_state(ok)["status"]
        seen["empty"] = read_state(empty)["status"]
        seen["missing"] = read_state(os.path.join(d, "absent"))["status"]
        seen["permission"] = read_state(ok, opener=refusing_opener)["status"]
        seen["not-a-file"] = read_state(d)["status"]
        seen["decode-error"] = read_state(raw)["status"]
        seen["parse-error"] = read_state(bad, parse=json.loads)["status"]
    assert len(set(seen.values())) == len(seen), (
        "%d different realities produced only %d distinct statuses: %r"
        % (len(seen), len(set(seen.values())), seen))
    assert seen["permission"] == "permission", seen
    for state in UNVERIFIABLE:
        assert state in seen.values(), "%r is documented but unreachable" % state


def test_selftest_is_green():
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = selftest()
    assert rc == 0, "selftest red:\n" + buf.getvalue()


ALL = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    failures = skipped = 0
    for fn in ALL:
        try:
            fn()
            print("  ok    %s" % fn.__name__)
        except Skip as exc:
            skipped += 1
            print("  SKIP  %s: %s" % (fn.__name__, exc))
        except AssertionError as exc:
            failures += 1
            print("  FAIL  %s: %s" % (fn.__name__, exc))
    if failures:
        print("RESULT: %d check(s) RED" % failures)
        sys.exit(1)
    if skipped:
        # A skipped check is UNMEASURED, and the marker is what the mutation harness reads
        # as proof this guard has teeth. Printing it over a skip is a green light wired to
        # nothing.
        print("RESULT: %d check(s) SKIPPED — marker withheld, nothing proved" % skipped)
        sys.exit(2)
    print(MARKER)
