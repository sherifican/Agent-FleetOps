#!/usr/bin/env python3
"""reader_record.py — a safe default that stays DISTINGUISHABLE.

local-lane-build-loop's reader pattern is `try/except` returning a safe default, never
raise. The trap in the naive form: a bare empty default collapses CANNOT-READ into
LOOKS-EMPTY, and every consumer downstream then reports an empty source it never actually
read — state asserted without a measurement.

This reference reader never raises AND never collapses the states. The returned record
carries a status:

    ok           — the source was read and parsed; `value` is the observation
    empty        — the source was read and holds nothing (a VALID observation)
    missing      — no source to read
    permission   — a source exists and the OS refused to open it
    not-a-file   — the path names a directory, not a source
    decode-error — bytes were read and are not text in the expected encoding
    parse-error  — text was read and did not parse

`empty` is data. Every other non-ok state is UNVERIFIABLE-HERE: the source was NOT
observed, and a consumer that treats any of them as empty is reporting state it never
measured.

WHY not-a-file AND decode-error ARE THEIR OWN STATES
    `IsADirectoryError` is an `OSError`, so it landed on the `permission` branch. That is
    a lie in the direction that costs an operator time: it points them at file modes when
    the real fault is a wrong path. It gets its own status.
    `UnicodeDecodeError` is a `ValueError`, NOT an `OSError`, and it is raised inside
    `fh.read()` — so a reader documented as never raising raised on a one-byte 0xff file.
    Measured. Undecodable bytes are also a different fact from unparseable text: the file
    was reachable and readable, and it is not in the encoding the caller assumed.

Gate: guard/tests/test_reader_record.py — the non-ok states must stay distinguishable
from each other and from a valid empty read. Red demo: mutation RR1 collapses the
permission state into empty and the gate must go red.
"""
import sys

ENCODING = "utf-8"


def read_state(path, parse=None, encoding=ENCODING, opener=open):
    """Read `path` into a record; never raises. `parse` (optional) maps the text to a
    value; a parse exception becomes status parse-error, never a crash.

    `opener` is a seam, not a knob: it lets a gate produce the OS refusals this reader
    routes without depending on filesystem modes. `chmod 000` cannot bar root, so a
    mode-based permission case SKIPS under root — and a skip counted as a pass leaves
    mutation RR1 able to survive while the gate still prints its all-pass marker."""
    try:
        with opener(path, encoding=encoding) as fh:
            text = fh.read()
    except FileNotFoundError as exc:
        return {"status": "missing", "value": None, "detail": str(exc)}
    except IsADirectoryError as exc:
        return {"status": "not-a-file", "value": None, "detail": str(exc)}
    except PermissionError as exc:
        return {"status": "permission", "value": None, "detail": str(exc)}
    except UnicodeDecodeError as exc:
        # Raised by fh.read(), and it is a ValueError — an `except OSError` never sees it.
        return {"status": "decode-error", "value": None,
                "detail": "not %s: %s" % (encoding, exc)}
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


OBSERVED = ("ok", "empty")          # the source WAS observed
UNVERIFIABLE = ("missing", "permission", "not-a-file", "decode-error", "parse-error")


def selftest():
    """Teeth: every state is reachable and no two collapse into one another."""
    import json
    import tempfile
    failures = []
    seen = {}
    with tempfile.TemporaryDirectory() as d:
        import os
        ok = os.path.join(d, "ok.json")
        with open(ok, "w", encoding="utf-8") as fh:
            fh.write('{"n": 3}')
        empty = os.path.join(d, "empty.txt")
        open(empty, "w", encoding="utf-8").close()
        bad = os.path.join(d, "bad.json")
        with open(bad, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        raw = os.path.join(d, "raw.bin")
        with open(raw, "wb") as fh:
            fh.write(b"\xff")
        seen["ok"] = read_state(ok, parse=json.loads)["status"]
        seen["empty"] = read_state(empty)["status"]
        seen["missing"] = read_state(os.path.join(d, "absent"))["status"]
        seen["not-a-file"] = read_state(d)["status"]
        seen["decode-error"] = read_state(raw)["status"]
        seen["parse-error"] = read_state(bad, parse=json.loads)["status"]
        seen["permission"] = _permission_record()["status"]
    for want, got in sorted(seen.items()):
        if got != want:
            failures.append("a %s source reported %r" % (want, got))
    if len(set(seen.values())) != len(seen):
        failures.append("%d different realities produced %d distinct statuses: %r"
                        % (len(seen), len(set(seen.values())), sorted(set(seen.values()))))
    for f in failures:
        print("  FAIL  %s" % f)
    if failures:
        print("reader record selftest: %d check(s) RED" % len(failures))
        return 1
    print("reader record selftest: %d realities, %d distinct statuses, none collapsed"
          % (len(seen), len(set(seen.values()))))
    return 0


def refusing_opener(*_a, **_kw):
    """An opener the OS-refusal branch routes, on every host including root."""
    raise PermissionError(13, "Permission denied")


def _permission_record():
    return read_state("<a source this process may not open>", opener=refusing_opener)


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "--selftest":
        sys.exit(selftest())
    if len(sys.argv) != 2:
        print("usage: reader_record.py <path> | --selftest")
        sys.exit(2)
    rec = read_state(sys.argv[1])
    print("%s: %s" % (rec["status"], rec["detail"] or "%d char(s)" % len(rec["value"] or "")))
    sys.exit(0 if rec["status"] in OBSERVED else 1)
