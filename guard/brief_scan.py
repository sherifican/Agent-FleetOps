#!/usr/bin/env python3
"""brief_scan.py — flag a leaked hypothesis in an outgoing brief.

A leg handed the dispatcher's hypothesis returns the hypothesis. Independence is worth
paying for only while the brief carries the QUESTION, not the expected answer — ask a leg
to REFUTE; never hand it what you already believe.

This scanner is a TRIPWIRE, not proof of independence. It catches the explicit leak — a
conclusion stated as the expected answer, an appeal to what "we" already believe or
found. A clean scan is exactly as wide as the pattern list below and no wider; the
structural isolation (separate briefs, no first-leg output in the second brief, inlined
material, own directory) stays mandatory either way.

Exit codes: 0 clean (within the pattern list's width) · 1 leak flagged (file:line and
pattern named) · 2 CANNOT CHECK (unreadable input).
Gate: guard/tests/test_brief_scan.py. Red demo: mutation BS1 neuters a pattern and the
gate must go red.
"""
import argparse
import re
import sys

# (name, regex) — case-insensitive, applied per line. Each names the leak shape it trips on.
PATTERNS = [
    ("expected-answer", r"\bthe answer (?:is|should be)\b"),
    ("confirm-our", r"\bconfirm (?:that|our|this finding|the finding)\b"),
    ("dispatcher-belief", r"\bwe (?:believe|expect|suspect|are confident|already know)\b"),
    ("prove-that", r"\bprove that\b"),
    ("hypothesis-block", r"\bhypothesis\b"),
    ("prior-conclusion", r"\bas (?:we|I) (?:found|established|concluded|showed)\b"),
    ("steered-outcome", r"\bshould (?:show|find|conclude|come back with)\b"),
    ("validate-our", r"\bvalidate (?:our|the) (?:conclusion|finding|approach|design)\b"),
]


def scan_text(text, source="<brief>"):
    compiled = [(name, re.compile(rx, re.IGNORECASE)) for name, rx in PATTERNS]
    hits = []
    for i, line in enumerate(text.splitlines(), 1):
        for name, rx in compiled:
            if rx.search(line):
                hits.append("%s:%d: [%s] %s" % (source, i, name, line.strip()))
    return hits


LEAKING_LINES = [
    "The answer is that the library regressed in v2.",
    "Please confirm that the timeout caused the failures.",
    "We believe the cache layer is at fault.",
    "Prove that the second endpoint is unreachable.",
    "HYPOTHESIS: the retry loop masks the error.",
    "As we found earlier, the parser drops the last field.",
    "Your report should show the same spike.",
    "Validate our conclusion about the scheduler.",
]

CLEAN_BRIEF = (
    "Task: report what the attached artifact actually shows.\n"
    "If the numbers contradict the attached table, say so plainly.\n"
    "List every source you retrieved, with its URL, and every source you could not reach.\n"
    "State the limits of the query: window covered, axes not swept.\n"
)


def selftest():
    """Teeth: every pattern must fire on its planted leaking line, and the refute-framed
    clean brief must not fire at all (breadth control)."""
    failures = []
    fired = set()
    for line in LEAKING_LINES:
        for hit in scan_text(line):
            fired.add(hit.split("[", 1)[1].split("]", 1)[0])
    for name, _ in PATTERNS:
        if name not in fired:
            failures.append("pattern %r missed its planted leak — a rule that cannot fire "
                            "guards nothing" % name)
    clean_hits = scan_text(CLEAN_BRIEF, "clean-brief")
    if clean_hits:
        failures.append("the refute-framed clean brief was flagged (over-breadth): %r"
                        % clean_hits)
    for f in failures:
        print("  FAIL  %s" % f)
    if failures:
        print("brief scan selftest: %d check(s) RED" % len(failures))
        return 1
    print("brief scan selftest: every pattern fires on its plant; the clean refute-framed "
          "brief stays clean")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("briefs", nargs="*", help="brief file(s) to scan before dispatch")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    if not args.briefs:
        print("CANNOT CHECK — no brief given (or run --selftest)")
        return 2
    hits = []
    for path in args.briefs:
        try:
            with open(path, encoding="utf-8") as fh:
                hits.extend(scan_text(fh.read(), path))
        except OSError as exc:
            print("CANNOT CHECK — unreadable brief: %s" % exc)
            return 2
    for h in hits:
        print(h)
    if hits:
        print("LEAK FLAGGED — %d line(s) hand the leg a conclusion. Rewrite the brief to "
              "ask for refutation, not confirmation." % len(hits))
        return 1
    print("clean within the pattern list's width — structural isolation still applies")
    return 0


if __name__ == "__main__":
    sys.exit(main())
