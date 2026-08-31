#!/usr/bin/env python3
"""reader_record.py — a safe default that stays DISTINGUISHABLE.

local-lane-build-loop's reader pattern is `try/except` returning a safe default, never
raise. The trap in the naive form: a bare empty default collapses CANNOT-READ into
LOOKS-EMPTY, and every consumer downstream then reports an empty source it never actually
read — state asserted without a measurement.

This reference reader never raises AND never collapses the states. The returned record
carries a status:

    ok          — the source was read and parsed; `value` is the observation
    empty       — the source was read and holds nothing (a VALID observation)
    missing     — no source to read
    permission  — a source exists and could not be opened
    parse-error — bytes were read and did not parse

`empty` is data. The other non-ok states are UNVERIFIABLE-HERE: the source was NOT
observed, and a consumer that treats them as empty is reporting state it never measured.

Gate: guard/tests/test_reader_record.py — the four non-ok states must stay distinguishable
from each other and from a valid empty read. Red demo: mutation RR1 collapses the
permission state into empty and the gate must go red.
"""
import sys


def read_state(path, parse=None):
    """Read `path` into a record; never raises. `parse` (optional) maps the text to a
    value; a parse exception becomes status parse-error, never a crash."""
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except FileNotFoundError as exc:
        return {"status": "missing", "value": None, "detail": str(exc)}
    except PermissionError as exc:
        return {"status": "permission", "value": None, "detail": str(exc)}
    except OSError as exc:
        return {"status": "permission", "value": None, "detail": str(exc)}
    if not text.strip():
        return {"status": "empty", "value": None, "detail": "read %d byte(s), no content"
                                                            % len(text)}
    if parse is None:
        return {"status": "ok", "value": text, "detail": ""}
    try:
        return {"status": "ok", "value": parse(text), "detail": ""}
    except Exception as exc:  # a reader never raises; it reports
        return {"status": "parse-error", "value": None, "detail": str(exc)}


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: reader_record.py <path>")
        sys.exit(2)
    rec = read_state(sys.argv[1])
    print("%s: %s" % (rec["status"], rec["detail"] or "%d char(s)" % len(rec["value"] or "")))
    sys.exit(0 if rec["status"] in ("ok", "empty") else 1)
