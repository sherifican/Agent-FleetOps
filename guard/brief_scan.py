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

IT WAS WRONG IN BOTH DIRECTIONS, AND BOTH WERE MEASURED
    Too wide: a bare `\\bhypothesis\\b` flagged the paired skill's own prescribed wording
    ("Never hand a leg the hypothesis — ask it to REFUTE"), and 4 of 8 refute-framed
    briefs were false positives on mandated language — "do not confirm our prior work" hit
    `confirm-our` while being a prohibition of exactly that. A scanner that fires on the
    instruction telling you not to leak is the always-fires defect, and an always-firing
    check gets switched off socially.
    Too narrow: genuine leaks passed clean — "The expected answer is that the library
    regressed in v2.", "I believe the cache layer is at fault.", "We're confident the cache
    layer is at fault.", "Prove the second endpoint is unreachable."

    So: the hypothesis rule matches a leak SHAPE ("the hypothesis is", "our hypothesis:"),
    not the bare word; a match is suppressed when a NEGATION precedes it on the line, so a
    prohibition of a leak is not read as the leak; and a match inside an inline-code span
    or a quoted span is read as a QUOTATION of the pattern, which is how briefs cite
    forbidden wording. That last one is an evasion route and is documented as such — this
    is a tripwire, not a proof.

Exit codes: 0 clean (within the pattern list's width) · 1 leak flagged (file:line and
pattern named) · 2 CANNOT CHECK (unreadable input).
Gate: guard/tests/test_brief_scan.py. Red demos: BS1 neuters a pattern; BS2 disables the
negation suppression so the mandated wording is flagged again; BS3 narrows the
expected-answer pattern back so its plant stops firing.
"""
import argparse
import re
import sys

# (name, regex) — case-insensitive, applied per line. Each names the leak shape it trips on.
PATTERNS = [
    ("expected-answer", r"\bthe (?:expected |likely |correct |real )?answer (?:is|was|should be|will be)\b"),
    ("confirm-our", r"\bconfirm (?:that|our|this finding|the finding)\b"),
    ("dispatcher-belief",
     r"\b(?:we|i)(?:\s*(?:'re|’re|'m|’m)|\s+(?:are|am))?\s+"
     r"(?:believe|expect|suspect|confident|already know)\b"),
    # "prove X" steers; "prove or disprove", "prove whether" do not.
    ("steered-proof", r"\bprove\s+(?!(?:or|whether|either|nothing|anything)\b)(?:that\s+)?\w"),
    # The leak SHAPE, not the bare word: the word alone appears in every instruction that
    # tells you not to leak one.
    ("hypothesis-leak",
     r"(?:\b(?:the|our|my|this|working)\s+hypothesis\b\s*(?:is|was|:)|\bhypothesis\s*:)"),
    ("prior-conclusion", r"\bas (?:we|i) (?:found|established|concluded|showed)\b"),
    ("steered-outcome", r"\b(?:should|must|will) (?:show|find|conclude|come back with)\b"),
    ("validate-our", r"\bvalidate (?:our|the) (?:conclusion|finding|approach|design)\b"),
]

# A prohibition of a leak is not a leak. Only text BEFORE the match counts, so
# "we believe the parser is not at fault" still fires.
NEGATION = re.compile(
    r"\b(?:never|not|no|non|don't|dont|doesn't|does not|do not|avoid|without|"
    r"instead of|rather than|refrain from|must not|should not|cannot|can't|"
    r"forbidden|prohibited)\b", re.IGNORECASE)

# A quotation of the pattern is not an instruction. Documented evasion route; see above.
QUOTED = re.compile(r"`[^`]*`|\"[^\"]*\"|“[^”]*”")


def _negated(line, start):
    return bool(NEGATION.search(line[:start]))


def _quoted(line, start, end):
    return any(m.start() <= start and end <= m.end() for m in QUOTED.finditer(line))


def scan_text(text, source="<brief>"):
    compiled = [(name, re.compile(rx, re.IGNORECASE)) for name, rx in PATTERNS]
    hits = []
    for i, line in enumerate(text.splitlines(), 1):
        for name, rx in compiled:
            m = rx.search(line)
            if not m:
                continue
            if _negated(line, m.start()):
                continue
            if _quoted(line, m.start(), m.end()):
                continue
            hits.append("%s:%d: [%s] %s" % (source, i, name, line.strip()))
    return hits


LEAKING_LINES = [
    "The answer is that the library regressed in v2.",
    "The expected answer is that the library regressed in v2.",
    "Please confirm that the timeout caused the failures.",
    "We believe the cache layer is at fault.",
    "I believe the cache layer is at fault.",
    "We're confident the cache layer is at fault.",
    "Prove that the second endpoint is unreachable.",
    "Prove the second endpoint is unreachable.",
    "HYPOTHESIS: the retry loop masks the error.",
    "The hypothesis is that the retry loop masks the error.",
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

# The clean brief above contains none of the ambiguity classes, so on its own it cannot
# measure over-breadth. These three do, and each is real wording from the surfaces this
# scanner is pointed at.
NEUTRAL_LINES = [
    "Prove or disprove the claim; either outcome is the deliverable.",
    "Prove whether the second endpoint is reachable.",
    "State your confidence and say what would change it.",
    "Never hand a leg the hypothesis — ask it to REFUTE.",
    "Report the mechanism you actually observed, not the one you would expect.",
]
NEGATED_LINES = [
    "do not confirm our prior work",
    "Do not tell the leg what we believe; ask it to refute the claim.",
    "The brief must never state the answer is X before the leg has looked.",
    "Rather than validate our conclusion, ask the leg to break it.",
    "This brief carries no hypothesis and no expected answer.",
    "Avoid any hypothesis-shaped framing in the brief.",
]
QUOTED_LINES = [
    "Forbidden phrasings include `we believe the cache layer is at fault`.",
    'A brief that says "the answer is X" has already spent the leg\'s independence.',
    "Reject any line matching `the hypothesis is` before dispatch.",
]
COUNTER_FIXTURES = [("neutral", NEUTRAL_LINES), ("negated", NEGATED_LINES),
                    ("quoted", QUOTED_LINES)]


def selftest():
    """Teeth: every pattern must fire on its planted leaking line, and the breadth
    controls — the refute-framed clean brief plus neutral, negated and quoted
    counter-fixtures — must not fire at all."""
    failures = []
    fired = set()
    for line in LEAKING_LINES:
        hits = scan_text(line)
        if not hits:
            failures.append("planted leak was not flagged at all: %r" % line)
        for hit in hits:
            fired.add(hit.split("[", 1)[1].split("]", 1)[0])
    for name, _ in PATTERNS:
        if name not in fired:
            failures.append("pattern %r missed its planted leak — a rule that cannot fire "
                            "guards nothing" % name)
    clean_hits = scan_text(CLEAN_BRIEF, "clean-brief")
    if clean_hits:
        failures.append("the refute-framed clean brief was flagged (over-breadth): %r"
                        % clean_hits)
    for label, lines in COUNTER_FIXTURES:
        for line in lines:
            hits = scan_text(line, "%s-control" % label)
            if hits:
                failures.append("%s counter-fixture was flagged (over-breadth): %r"
                                % (label, hits))
    for f in failures:
        print("  FAIL  %s" % f)
    if failures:
        print("brief scan selftest: %d check(s) RED" % len(failures))
        return 1
    print("brief scan selftest: every pattern fires on its plant; the clean refute-framed "
          "brief and the neutral, negated and quoted counter-fixtures all stay clean")
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
