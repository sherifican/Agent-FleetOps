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
    not the bare word; an occurrence is suppressed when a NEGATION precedes it within the
    same sentence or clause with no intervening clause-joining mark; and an
    occurrence inside an inline-code span or a quoted span is read as a QUOTATION of the
    pattern, which is how briefs cite forbidden wording. That last one is an evasion route
    and is documented as such — this is a tripwire, not a proof.

    Localized, not line-wide. The suppressors used to act on the
    whole physical line and only the FIRST occurrence was ever inspected, so
    'Example: "We believe X". We believe the cache is broken.' and "Do not edit files. We
    believe the cache is broken." both passed clean — a quotation or an unrelated
    prohibition shielded a later bare assertion. Now EVERY occurrence of every pattern is
    inspected. A negation's window ends at '.', ';', '!' or '?' (subject to the
    dotted-run continuation rule below). Within that window a negation suppresses an
    occurrence ONLY when no clause-joining mark lies between the negation and occurrence.
    The mark class is colon, comma, opening/closing round parenthesis, em dash, en dash,
    whitespace-surrounded '-' or '--', and the whole word 'and' (case-insensitive).
    These marks do not change CLAUSE_BREAK or the dotted-run continuation rule.
    Fail closed: "Do not edit files: we believe the cache is broken." is flagged, as is
    the real prohibition "Never write: we believe X." Quote the forbidden phrasing:
    'Never write: "we believe X".' stays quiet. A direct prohibition with no intervening
    mark, "Do not say we believe the cache is broken.", also stays quiet.
    Known residual: an unmarked leak such as "Rather than edit files we believe the cache
    is broken." is still suppressed; this is a limitation, not desired behaviour.
    "Rather than validate our conclusion, ask the leg to break it." stays a prohibition.
    A quotation covers only the occurrence inside it. Diagnostics stay
    bounded to one per (line, pattern), and that bound is applied AFTER inspection, never
    by stopping at the first occurrence.

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
# "we believe the parser is not at fault" still fires — and only text in the SAME
# sentence or clause, with no intervening clause-joining mark. Thus both
# "Do not edit files. We believe ..." and "Do not edit files: we believe ..." fire.
NEGATION = re.compile(
    r"\b(?:never|not|no|non|don't|dont|doesn't|does not|do not|avoid|without|"
    r"instead of|rather than|refrain from|must not|should not|cannot|can't|"
    r"forbidden|prohibited)\b", re.IGNORECASE)

# Sentence/clause boundaries stay distinct from marks that block negation suppression:
# "Rather than validate our conclusion, ask the leg to break it." is one prohibition.
CLAUSE_BREAK = re.compile(r"[.;!?]")

# Inspect the original span between a negation and the occurrence, not the occurrence
# itself (which may contain a colon). A later unblocked negation can still suppress.
CLAUSE_JOIN = re.compile(r"[:,()—–]|(?<=\s)--?(?=\s)|\band\b", re.IGNORECASE)

# A quotation of the pattern is not an instruction. Documented evasion route; see above.
QUOTED = re.compile(r"`[^`]*`|\"[^\"]*\"|“[^”]*”")


# A dotted-letter run: two or more single letters each followed by a period (e.g., i.e.,
# U.S., U.S.A.). Its internal periods never end a clause; its LAST period ends one unless the
# next non-space character is a lower-case letter (the sentence is continuing).
DOTTED_RUN = re.compile(r"(?<![A-Za-z])(?:[A-Za-z]\.){2,}")


# Closing brackets and quotation marks that C(j) skips before deciding whether the sentence
# continues: round/square/curly brackets, straight double and single quotes, the curly closing
# double and single quotes, and the backtick.
CLOSERS = ")]}\"'\u201d\u2019`"


def _continues(line, j):
    """C(j): does the sentence CONTINUE after position j?

    Skip every whitespace character (str.isspace — tab and the no-break space included) and
    every closing bracket or quotation mark from j; the sentence continues iff the next
    character is a lower-case letter, a comma or a colon. Used in exactly two places: after
    the last period of a dotted-letter run, and after the closing quotation mark when '.',
    '!' or '?' sits immediately before that mark.
    """
    k = j
    while k < len(line) and (line[k].isspace() or line[k] in CLOSERS):
        k += 1
    if k >= len(line):
        return False
    ch = line[k]
    return (ch.isalpha() and ch.islower()) or ch in (",", ":")


def _is_clause_break(line, i):
    """Is the CLAUSE_BREAK character at index `i` of the ORIGINAL line a real boundary?

    The rule (the table in guard/tests/test_brief_scan.py pins it):
      1. ';' always ends a clause, also right before a closing quote or bracket; unquoted
         '!' and '?' always end a sentence.
      2. A period ends a clause UNLESS it is (a) between two digits ("v2.5", "v12.50.9");
         (b) inside a dotted-letter run (two or more single letters of any case, each followed
         by a period: e.g., i.e., U.S., a.m., p.q.r.) and not the run's last period; or (c) the
         LAST period of a dotted-letter run after which the sentence continues, C(j): skipping
         whitespace and closing brackets/quotation marks, the next character is a lower-case
         letter, a comma or a colon. A lone letter before a period ("option B.") and an
         ordinary word's period ("files. we") always end the sentence.
      3. '.', '!' or '?' immediately before a closing quotation mark end the sentence unless
         C holds after the mark; a break in the MIDDLE of a quotation ("a; b") is quoted
         material and is not a boundary.
    Earlier cuts got this wrong in both directions: "any single letter is an abbreviation"
    swallowed "option B. We believe ..." (a false green, the leak direction); an lstrip(" ")
    continuation check missed tabs, no-break spaces and closing brackets (false reds).
    """
    ch = line[i]
    # Rule 3: inside a quoted span, only the character immediately before the closing mark
    # is a candidate boundary — and for '.', '!' and '?' only if the sentence does not
    # continue after the mark.
    for q in QUOTED.finditer(line):
        if q.start() < i < q.end() - 1:
            if i != q.end() - 2:
                return False
            if ch in (".", "!", "?"):
                return not _continues(line, q.end())
            return True                                               # ';' always
    if ch != ".":
        return True                                                   # rule 1
    prev = line[i - 1] if i >= 1 else ""
    nxt = line[i + 1] if i + 1 < len(line) else ""
    if prev.isdigit() and nxt.isdigit():
        return False                                                  # rule 2a
    for run in DOTTED_RUN.finditer(line):
        if run.start() <= i < run.end():
            if i != run.end() - 1:
                return False                                          # rule 2b
            return not _continues(line, run.end())                    # rule 2c
    return True


def _unquoted(line):
    """The line with every quoted span blanked to spaces (same length, same offsets).

    A "Do not" INSIDE a quotation is quoted material; it must not suppress a
    bare occurrence after the closing quote. Only negation WORDS are looked up here —
    boundaries are found in the original line (rule 3 above).
    """
    out = list(line)
    for m in QUOTED.finditer(line):
        for k in range(m.start(), m.end()):
            out[k] = " "
    return "".join(out)


def _negated(line, start):
    """True when an unquoted negation precedes `start` with no boundary or joining mark.

    Boundaries and intervening marks come from the ORIGINAL line; negation words are
    searched in the quote-blanked copy, which has the same offsets."""
    opened = 0
    for b in CLAUSE_BREAK.finditer(line, 0, start):
        if _is_clause_break(line, b.start()):
            opened = b.end()
    return any(not CLAUSE_JOIN.search(line, neg.end(), start)
               for neg in NEGATION.finditer(_unquoted(line), opened, start))


def _quoted(line, start, end):
    return any(m.start() <= start and end <= m.end() for m in QUOTED.finditer(line))


def _occurrences(compiled, line):
    """Every (pattern-name, match) on the line — all occurrences, not just the first."""
    for name, rx in compiled:
        for m in rx.finditer(line):
            yield name, m


def scan_text(text, source="<brief>"):
    compiled = [(name, re.compile(rx, re.IGNORECASE)) for name, rx in PATTERNS]
    hits = []
    for i, line in enumerate(text.splitlines(), 1):
        reported = set()
        for name, m in _occurrences(compiled, line):
            # One diagnostic per (line, pattern). Every occurrence is still inspected above;
            # the bound only de-duplicates what is REPORTED.
            if name in reported:
                continue
            if _negated(line, m.start()):
                continue
            if _quoted(line, m.start(), m.end()):
                continue
            reported.add(name)
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
