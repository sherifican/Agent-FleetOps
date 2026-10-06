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
    CLAUSE_BREAK, CLEAN_BRIEF, COUNTER_FIXTURES, LEAKING_LINES, PATTERNS,
    _is_clause_break, scan_text, selftest)

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


# ---------------------------------------------------------------------------------------------
# Contract: localize brief suppressors. Written BEFORE the fix. The scanner must
# inspect EVERY occurrence of a pattern on a line and suppress only the quoted or prohibited
# occurrence; a negation reaches only its own sentence/clause. The RED lines are E2's two
# mixed-content misses plus the two shapes the plan adds (two occurrences with only the first
# quoted; a prior prohibition split off by a semicolon).
# ---------------------------------------------------------------------------------------------

def _names(hits):
    return [h.split("[", 1)[1].split("]", 1)[0] for h in hits]


# --- RED --------------------------------------------------------------------------------------

def test_a_quoted_occurrence_does_not_shield_a_later_bare_assertion():
    """E2 line 1: two occurrences on one physical line, only the first quoted."""
    line = 'Example: "We believe X". We believe the cache is broken.'
    hits = scan_text(line)
    assert "dispatcher-belief" in _names(hits), (
        "the later, unquoted assertion was not flagged — the first (quoted) occurrence "
        "shielded the whole line: %r" % hits)


def test_a_prior_sentence_prohibition_does_not_shield_a_later_assertion():
    """E2 line 2: the negation belongs to the previous sentence."""
    line = "Do not edit files. We believe the cache is broken."
    hits = scan_text(line)
    assert "dispatcher-belief" in _names(hits), (
        "a negation in an earlier sentence suppressed a later assertion: %r" % hits)


def test_a_semicolon_separated_prohibition_does_not_shield_a_later_assertion():
    line = "Do not edit files; we believe the cache is broken."
    hits = scan_text(line)
    assert "dispatcher-belief" in _names(hits), (
        "a negation in an earlier clause (split by ';') suppressed a later assertion: %r" % hits)


def test_a_backtick_quoted_occurrence_does_not_shield_a_later_bare_assertion():
    """Same shape as the E2 miss, second pattern and the inline-code quotation form."""
    line = "Reject `the answer is X` in briefs. The answer is that the cache layer regressed."
    hits = scan_text(line)
    assert "expected-answer" in _names(hits), (
        "the later, unquoted 'the answer is' was not flagged: %r" % hits)


def test_diagnostics_are_bounded_to_one_per_pattern_per_line_without_skipping_inspection():
    """One diagnostic per (line, pattern) — but the bound must not be implemented by stopping at
    the first occurrence, which is exactly the shielding bug. The quoted-first line carries TWO
    bare occurrences after the quotation, so it must yield ONE dispatcher-belief hit: zero means
    inspection stopped at the quoted occurrence, two means the bound is gone.

    (Fixture hygiene: the earlier fixture had only one bare occurrence, so "two" was unreachable
    and the second half of this docstring described nothing the test could measure.)"""
    line = 'Example: "We believe X". We believe the cache is broken. We believe the disk is full.'
    n = _names(scan_text(line)).count("dispatcher-belief")
    assert n == 1, "expected exactly one dispatcher-belief diagnostic, got %d" % n


# --- CONTROL ----------------------------------------------------------------------------------

def test_control_standalone_quotations_stay_quiet():
    for line in ['Example: "We believe X".',
                 "Reject any line matching `the hypothesis is` before dispatch."]:
        assert not scan_text(line), "a standalone quotation was flagged: %r" % line


def test_control_actual_prohibitions_stay_quiet():
    """CONTROL: quote forbidden wording or put no joining mark after its negation."""
    for line in ["Do not confirm our prior work.",
                 "Do not tell the leg what we believe; ask it to refute the claim.",
                 "Rather than validate our conclusion, ask the leg to break it."]:
        assert not scan_text(line), "a prohibition of a leak was flagged as the leak: %r" % line


def test_control_bare_belief_still_flags():
    assert "dispatcher-belief" in _names(scan_text("We believe the cache is broken."))


def test_control_following_negation_still_flags():
    assert scan_text("We believe the parser is not at fault."), (
        "a negation AFTER the match must not suppress it")


def test_control_neutral_refutation_instructions_stay_quiet():
    """Includes the V19 authority-appeal example, which is out of scope for A2 and must not be
    picked up as a side effect of widening inspection."""
    for line in ["The owner already signed off, proceed.",
                 "Prove or disprove the claim; either outcome is the deliverable.",
                 "Never hand a leg the hypothesis — ask it to REFUTE."]:
        assert not scan_text(line), "a neutral instruction was flagged: %r" % line


def test_control_every_existing_plant_still_flags():
    missed = [line for line in LEAKING_LINES if not scan_text(line)]
    assert not missed, "existing planted leak(s) stopped firing: %r" % missed


def test_control_two_bare_occurrences_yield_one_bounded_diagnostic():
    line = "We believe the cache is broken. We believe the disk is full."
    n = _names(scan_text(line)).count("dispatcher-belief")
    assert n == 1, "duplicate diagnostics must be bounded to one per pattern per line, got %d" % n


# =============================================================================================
# Earlier integration coverage. Written BEFORE the fix; every RED below fails on
# an earlier internal build by assertion on scan_text()'s hit list, never on a missing interface.
#
# R1: a negation word INSIDE a quoted span must not negate text OUTSIDE that span.
# R2: a period that belongs to an abbreviation (e.g., i.e.) or a number (v2.5) is not a
#     sentence boundary; '.', '!', '?', ';' still are, and a period with no following space
#     still is.
# A2_first2 / A2_nobang: gate gaps — wrong implementations that passed the regression suite.
# =============================================================================================

def _belief_hits(line):
    return _names(scan_text(line)).count("dispatcher-belief")


# --- R1 RED -----------------------------------------------------------------------------------

def test_a_negation_inside_a_quoted_span_does_not_reach_outside_it():
    """The first 'we believe' is a quotation (suppressed as such); the 'Do not' that precedes
    the second one sits INSIDE the quotation, so it is quoted text, not a prohibition of the
    bare assertion that follows the closing quote."""
    line = 'Example: "Do not say we believe X" or we believe the cache is broken.'
    n = _belief_hits(line)
    assert n == 1, ("a negation inside a quoted span negated the bare assertion outside it: "
                    "expected exactly 1 dispatcher-belief hit, got %d: %r" % (n, scan_text(line)))


# --- R1 CONTROL -------------------------------------------------------------------------------

def test_control_a_negated_quotation_stays_quiet():
    line = 'Do not write "we believe X" in a brief.'
    assert not scan_text(line), "a prohibition of a quoted leak was flagged: %r" % scan_text(line)


def test_control_a_bare_prohibition_stays_quiet():
    line = "Do not say we believe the cache is broken."
    assert not scan_text(line), "an unquoted prohibition was flagged: %r" % scan_text(line)


# --- R2 RED -----------------------------------------------------------------------------------

R2_ABBREVIATION_PROHIBITIONS = [
    ("e.g.", "For a cause (do not state e.g. we believe the cache is broken)."),
    ("i.e.", "For the leg, do not state i.e. we believe the cache is broken."),
    ("digit.digit", "Do not write that since v2.5 we believe the cache is broken."),
]


def test_an_abbreviation_or_number_period_is_not_a_sentence_boundary():
    """Each line is ONE prohibition; the period inside 'e.g.', 'i.e.' or '2.5' must not cut the
    negation off from the occurrence it governs. Every failure collected so one run names them."""
    flagged = []
    for label, line in R2_ABBREVIATION_PROHIBITIONS:
        hits = scan_text(line)
        if hits:
            flagged.append("%s: %r" % (label, hits))
    assert not flagged, ("%d prohibition(s) read as a leak because a non-sentence period opened "
                         "a new negation window:\n  " % len(flagged) + "\n  ".join(flagged))


def test_eg_period_is_not_a_boundary():
    line = R2_ABBREVIATION_PROHIBITIONS[0][1]
    assert not scan_text(line), "'e.g.' cut the negation off: %r" % scan_text(line)


def test_ie_period_is_not_a_boundary():
    line = R2_ABBREVIATION_PROHIBITIONS[1][1]
    assert not scan_text(line), "'i.e.' cut the negation off: %r" % scan_text(line)


def test_number_period_is_not_a_boundary():
    line = R2_ABBREVIATION_PROHIBITIONS[2][1]
    assert not scan_text(line), "'2.5' cut the negation off: %r" % scan_text(line)


# --- R2 CONTROL (these also catch the A2_nobang mutant) ---------------------------------------

R2_BOUNDARY_CONTROLS = [
    (".", "Do not edit files. We believe the cache is broken."),
    ("!", "Do not edit files! We believe the cache is broken."),
    ("?", "Do not edit files? We believe the cache is broken."),
    (";", "Do not edit files; we believe the cache is broken."),
    (". with no following space", "Do not edit files.We believe the cache is broken."),
]


def test_control_real_sentence_and_clause_breaks_still_end_a_negation():
    missed = []
    for label, line in R2_BOUNDARY_CONTROLS:
        n = _belief_hits(line)
        if n != 1:
            missed.append("%s: expected 1 dispatcher-belief hit, got %d (%r)" % (label, n, scan_text(line)))
    assert not missed, "a real boundary stopped ending the negation:\n  " + "\n  ".join(missed)


def test_nobang_exclamation_and_question_marks_end_a_clause():
    """Gate gap: a scanner whose clause breaks are only '.' and ';' passed the regression suite.
    A negation before '!' or '?' must not reach the assertion after it."""
    for mark in ("!", "?"):
        line = "Do not edit files%s We believe the cache is broken." % mark
        n = _belief_hits(line)
        assert n == 1, ("%r no longer ends a clause — the earlier negation suppressed the later "
                        "assertion: expected 1 hit, got %d" % (mark, n))


def test_control_period_with_no_following_space_is_still_a_boundary():
    line = "Do not edit files.We believe the cache is broken."
    assert _belief_hits(line) == 1, scan_text(line)


R2B_SINGLE_LETTER_SENTENCE_ENDS = [
    "Do not pick option B. We believe the cache is broken.",
    "Do not use step a. We believe the cache is broken.",
    "Never touch box A. We believe the cache is broken.",
]


def test_a_sentence_ending_in_a_single_letter_is_still_a_boundary():
    """Earlier integration finding: the first R2 fix read ANY single letter before a period as an
    abbreviation, so a real sentence ending in one ("option B.") no longer ended the negation and
    the next sentence's assertion went unreported — a false green, the leak direction. Only a
    dotted-letter run ("e.g.", "i.e.") is an abbreviation; a lone letter before a period is not."""
    for line in R2B_SINGLE_LETTER_SENTENCE_ENDS:
        assert _belief_hits(line) == 1, "a lone letter swallowed the sentence end: %r -> %r" % (
            line, scan_text(line))


# --- A2_first2 gate gap -----------------------------------------------------------------------

def test_first2_a_third_occurrence_is_inspected_when_the_first_two_are_quoted():
    """Gate gap: a scanner that inspects only the first two occurrences per pattern passed the
    regression suite (its fixtures never had more than two). Here the first two are quotations and
    the third is bare, past a ';' that closes the 'Never' window."""
    line = 'Never write "we believe X" or "we believe Y"; we believe the cache is broken.'
    n = _belief_hits(line)
    assert n == 1, ("the third (bare) occurrence was not inspected — expected exactly 1 "
                    "dispatcher-belief hit, got %d: %r" % (n, scan_text(line)))


def test_first2_control_three_bare_occurrences_yield_one_bounded_diagnostic():
    line = "We believe X. We believe Y. We believe the cache is broken."
    n = _belief_hits(line)
    assert n == 1, "bound broken: got %d" % n


# =============================================================================================
# Sentence-boundary coverage: earlier tests
# pinned single cases and the next review found the neighbour. This pins the RULE as one table,
# so a wrong rule fails many cells at once. Every row is the dispatcher-belief hit count for the
# whole line.
#
# The rule (covered by ONE continuation test, C):
#   C(j) — "the sentence continues": skip every whitespace character and every closing bracket
#   or quotation mark ( ')' ']' '}' '"' "'" '”' '’' and backtick ) from position j; the sentence
#   continues iff the next character is a lower-case letter, a comma or a colon.
#   - The LAST period of a dotted-letter run — two or more single letters each followed by a
#     period ("e.g.", "U.S.", "a.m.", "p.q.r.", "E.u."), any case — ends the sentence unless C
#     holds after it.
#   - '.', '!' and '?' just before a closing quotation mark end the sentence unless C holds after
#     the mark.
#   - Unchanged: an ordinary word's period ends the sentence (also before lower case: "files. we");
#     unquoted '!' and '?' always end it; ';' always ends a clause; digit.digit and a dotted run's
#     internal periods never do; a break in the MIDDLE of a quotation ("a; b") is quoted material.
#   Boundaries are found in the ORIGINAL line; only negation WORDS inside a quoted span are
#   ignored (R1).
# =============================================================================================
import functools  # noqa: E402

import pytest  # noqa: E402

G1_BOUNDARY_TABLE = [
    # --- expect 1: the second sentence's assertion is bare and must be reported ---------------
    ("period-after-ordinary-word",        "Do not pick option B. We believe the cache is broken.", 1),
    ("US-last-period-then-capital",       "Do not write about the U.S. We believe the cache is broken.", 1),
    ("eg-last-period-then-capital",       "Do not cite e.g. We believe the cache is broken.", 1),
    ("digit-period-then-capital",         "Do not touch v2. We believe the cache is broken.", 1),
    ("period-then-lowercase-after-word",  "Do not edit files. we believe the cache is broken.", 1),
    ("period-no-space",                   "Do not edit files.We believe the cache is broken.", 1),
    ("period-after-closing-bracket",      "Do not edit files (see below). We believe the cache is broken.", 1),
    ("period-inside-straight-quote",      'Do not repeat "the answer is X." We believe the cache is broken.', 1),
    ("bang-inside-curly-quote",           "Never write “stop!” We believe the cache is broken.", 1),
    ("question-inside-straight-quote",    'Do not answer "yes?" We believe the cache is broken.', 1),
    ("negation-word-inside-quote-R1",     'Example: "Do not say we believe X" or we believe the cache is broken.', 1),
    ("period-inside-curly-quote",         "Do not write “Fix it.” We believe the cache is broken.", 1),
    ("AB-last-period-then-capital",       "Do not cite A.B. We believe the cache is broken.", 1),
    # --- expect 0: one prohibition; the assertion is inside its reach or quoted ---------------
    ("eg-internal-and-last-then-lower",   "For a cause (do not state e.g. we believe the cache is broken).", 0),
    ("ie-internal-and-last-then-lower",   "For the leg, do not state i.e. we believe the cache is broken.", 0),
    ("US-last-period-then-lower",         "Do not say U.S. we believe the cache is broken.", 0),
    ("US-last-period-then-lower-word",    "Do not assert U.S. policy means we believe the cache is broken.", 0),
    ("USA-three-letter-run-then-lower",   "Do not cite the U.S.A. we believe the cache is broken.", 0),
    ("digit-period-digit",                "Do not write that since v2.5 we believe the cache is broken.", 0),
    ("digit-period-digit-twice",          "Do not write that since v12.50.9 we believe the cache is broken.", 0),
    ("quoted-occurrence-after-negation",  'Do not write "we believe X" in a brief.', 0),
    ("bare-prohibition",                  "Do not say we believe the cache is broken.", 0),
    ("semicolon-in-the-middle-of-quote",  'Do not write "a; b" or we believe the cache is broken.', 0),
    # Sentence continuation: a sentence never goes on with a comma or colon after its
    # final period, so the last period of a dotted run followed by `,` or `:` is not a boundary
    # either. "e.g.," is the common written form; the first rule text said "lower-case" only and
    # the fix built to it flagged every one of these prohibitions.
    ("eg-last-period-then-comma",         "Do not state a cause (e.g., \"we believe the cache is broken\").", 0),
    ("ie-last-period-then-comma",         "Do not frame it for the leg, i.e., \"we believe the cache is broken\".", 0),
    ("US-last-period-then-comma",         "Do not write about the U.S., \"we believe the cache is broken\".", 0),
    ("eg-last-period-then-colon",         "Do not give an example (e.g.: \"we believe the cache is broken\").", 0),
    # The continuation test C. What follows a dotted run's last period, or a mark just
    # inside a closing quote, decides — through whitespace of any kind and closing brackets/quotes.
    # --- expect 0: the sentence visibly continues, so the prohibition still reaches ------------
    ("quoted-eg-then-lower",              'Do not write "e.g." when telling the leg what we believe; ask it to refute.', 0),
    ("quoted-etc-then-lower",             'Do not say "etc." or claim we believe the cache is broken.', 0),
    ("quoted-bang-then-lower",            'Do not write "stop!" when we believe the cache is broken.', 0),
    ("quoted-question-then-lower",        'Do not answer "why?" if we believe the cache is broken.', 0),
    ("US-then-tab-then-lower",            "Do not say U.S.\twe believe the cache is broken.", 0),
    ("eg-then-tab-then-comma",            "Do not state a cause (e.g.\t, \"we believe the cache is broken\").", 0),
    ("US-then-close-bracket-then-lower",  "Do not name a place (the U.S.) where \"we believe the cache is broken\".", 0),
    ("US-then-nbsp-then-lower",           "Do not say U.S.\xa0we believe the cache is broken.", 0),
    ("am-lowercase-run-then-lower",       "Do not say a.m. we believe the cache is broken.", 0),
    ("pqr-lowercase-run-then-lower",      "Do not cite the p.q.r. policy because we believe the cache is broken.", 0),
    ("Eu-mixed-case-run-then-lower",      "Do not cite the E.u. rule when we believe the cache is broken.", 0),
    # --- expect 1: nothing continues the sentence, so the assertion after it is bare -----------
    ("US-then-close-bracket-then-capital", "Do not name a place (the U.S.) We believe the cache is broken.", 1),
    ("quoted-period-then-tab-then-capital", 'Do not write "Fix it."\tWe believe the cache is broken.', 1),
    ("unquoted-bang-then-lower",          "Never assume anything! we believe the cache is broken.", 1),
    ("am-lowercase-run-then-capital",     "Do not meet at 9 a.m. We believe the cache is broken.", 1),
]


def test_control_the_tab_and_nbsp_rows_really_carry_the_characters_they_name():
    """The literal-TAB rows must hold a real tab (and the nbsp row a real U+00A0), or the cells
    measure a space and pass for the wrong reason."""
    by_id = {_id: line for _id, line, _w in G1_BOUNDARY_TABLE}
    assert "\t" in by_id["US-then-tab-then-lower"] and " " not in by_id["US-then-tab-then-lower"].split("U.S.")[1][:1]
    assert "\t" in by_id["eg-then-tab-then-comma"]
    assert "\t" in by_id["quoted-period-then-tab-then-capital"]
    assert "\xa0" in by_id["US-then-nbsp-then-lower"] and "\xa0".isspace()


@pytest.mark.parametrize("line, want", [(line, want) for _id, line, want in G1_BOUNDARY_TABLE],
                         ids=[_id for _id, _line, _want in G1_BOUNDARY_TABLE])
def test_sentence_boundary_rule_table(line, want):
    # Quoting cures the new fail-closed report, but must not hide a boundary regression.
    # Check the boundary itself as well as retaining the original diagnostic assertion.
    if '"we believe the cache is broken"' in line and want == 0:
        for mark in CLAUSE_BREAK.finditer(line, 0, line.index("we believe")):
            assert not _is_clause_break(line, mark.start()), line
    hits = scan_text(line)
    n = _names(hits).count("dispatcher-belief")
    assert n == want, ("expected %d dispatcher-belief hit(s), got %d for %r -> %r"
                       % (want, n, line, hits))


def test_control_table_ids_are_unique_and_rows_are_well_formed():
    ids = [_id for _id, _l, _w in G1_BOUNDARY_TABLE]
    assert len(ids) == len(set(ids)), "duplicate table ids: %r" % ids
    assert all(w in (0, 1) for _i, _l, w in G1_BOUNDARY_TABLE)
    assert all("we believe" in l.lower() for _i, l, _w in G1_BOUNDARY_TABLE), (
        "every row must carry the dispatcher-belief pattern, or a 0 is vacuous")


def _expand(fn):
    """[(label, zero-arg callable)] for the standalone runner: a parametrised test becomes one
    callable per row (label = name[id]); a plain test is itself. Only the first parametrize
    mark is expanded, which is all this file uses."""
    marks = [m for m in getattr(fn, "pytestmark", []) if m.name == "parametrize"]
    if not marks:
        return [(fn.__name__, fn)]
    argnames, argvalues = marks[0].args[0], marks[0].args[1]
    names = [a.strip() for a in argnames.split(",")] if isinstance(argnames, str) else list(argnames)
    ids = marks[0].kwargs.get("ids") or [str(k) for k in range(len(argvalues))]
    out = []
    for k, vals in enumerate(argvalues):
        vals = tuple(vals) if isinstance(vals, (tuple, list)) else (vals,)
        out.append(("%s[%s]" % (fn.__name__, ids[k]), functools.partial(fn, **dict(zip(names, vals)))))
    return out


# =============================================================================================
# The table SAMPLED each class the continuation rule C names; a wrong
# rule that handles the sampled members passes. Here each class is enumerated in full where it
# is finite (every isspace() code point; every closer the rule names) and pinned at the boundary
# where it is not (what must NOT continue; a quoted ';').
# =============================================================================================
from guard.brief_scan import QUOTED  # noqa: E402

_WS_LOWER = "Do not say U.S.%swe believe the cache is broken."
_WS_UPPER = "Do not write about the U.S.%sWe believe the cache is broken."


def _splits_a_line(c):
    return len((_WS_LOWER % c).splitlines()) != 1 or len((_WS_UPPER % c).splitlines()) != 1


ALL_WHITESPACE = [chr(c) for c in range(0x110000) if chr(c).isspace()]
W1_PINNABLE = [c for c in ALL_WHITESPACE if not _splits_a_line(c)]
W1_SKIPPED_LINE_BREAKS = [c for c in ALL_WHITESPACE if _splits_a_line(c)]


def test_control_the_whitespace_class_was_enumerated_not_sampled():
    """The class is every code point str.isspace() accepts. Members that split a physical line
    cannot be placed inside one line and are skipped — listed here so the skip is visible."""
    assert len(ALL_WHITESPACE) >= 25, len(ALL_WHITESPACE)
    assert " " in W1_PINNABLE and "\t" in W1_PINNABLE and "\xa0" in W1_PINNABLE
    for c in W1_SKIPPED_LINE_BREAKS:
        assert ("x%sy" % c).splitlines() != ["x%sy" % c], "skipped %r without cause" % c
    assert set(W1_PINNABLE) | set(W1_SKIPPED_LINE_BREAKS) == set(ALL_WHITESPACE)
    assert len(W1_PINNABLE) >= 15, [hex(ord(c)) for c in W1_PINNABLE]


@pytest.mark.parametrize("c", W1_PINNABLE, ids=["U+%04X" % ord(c) for c in W1_PINNABLE])
def test_every_whitespace_code_point_is_skipped_by_the_continuation_test(c):
    lo, up = _WS_LOWER % c, _WS_UPPER % c
    assert _belief_hits(lo) == 0, ("U+%04X between a dotted run and a lower-case continuation "
                                   "was not skipped: %r -> %r" % (ord(c), lo, scan_text(lo)))
    assert _belief_hits(up) == 1, ("U+%04X: the capital after it must still end the sentence: "
                                   "%r -> %r" % (ord(c), up, scan_text(up)))


# --- K1: every closer the rule names, in a line where it opens no quoted span of its own ----
K1_CLOSERS = [(")", "round bracket"), ("]", "square bracket"), ("}", "curly bracket"),
              ('"', "straight double quote"), ("'", "straight single quote"),
              ("”", "curly closing double quote"), ("’", "curly closing single quote"),
              ("`", "backtick")]
_K1_LOWER = "Do not name the U.S.%s where we believe the cache is broken."
_K1_UPPER = "Do not name the U.S.%s We believe the cache is broken."


def test_control_the_closer_rows_open_no_quoted_span():
    """An UNPAIRED closer: with no opening mark the quotation regex finds no span, so the closer
    is reached by the continuation test itself, not by the closing-quote rule (rule 3)."""
    for ch, _label in K1_CLOSERS:
        for line in (_K1_LOWER % ch, _K1_UPPER % ch):
            assert not list(QUOTED.finditer(line)), "%r opened a quoted span in %r" % (ch, line)


@pytest.mark.parametrize("ch", [c for c, _l in K1_CLOSERS], ids=[l for _c, l in K1_CLOSERS])
def test_every_named_closer_is_skipped_by_the_continuation_test(ch):
    lo, up = _K1_LOWER % ch, _K1_UPPER % ch
    if ch == ")":
        # The unpaired closer still reaches C(j); quote only the forbidden phrase.
        assert not _is_clause_break(lo, lo.index("U.S.") + 3), lo
        lo = lo.replace("we believe the cache is broken", '"we believe the cache is broken"')
    assert _belief_hits(lo) == 0, ("closer %r after a dotted run was not skipped before the "
                                   "lower-case continuation: %r -> %r" % (ch, lo, scan_text(lo)))
    assert _belief_hits(up) == 1, ("closer %r: the capital after it must still end the sentence: "
                                   "%r -> %r" % (ch, up, scan_text(up)))


# --- N1: what does NOT continue a sentence (leak direction) ---------------------------------
N1_NON_CONTINUERS = [
    ("digit-after-space",        "Do not cite the U.S. 9 we believe the cache is broken."),
    ("digit-adjacent",           "Do not cite the U.S.9 we believe the cache is broken."),
    ("open-round-bracket",       "Do not cite the U.S. (we believe the cache is broken)."),
    ("hyphen",                   "Do not cite the U.S. - we believe the cache is broken."),
    ("curly-opening-quote",      "Do not cite the U.S. “x” we believe the cache is broken."),
    # A straight '"' is in the rule's own closer set (opening and closing are the same
    # character), so it is skipped and what FOLLOWS it decides; here that is a capital.
    ("straight-open-quote-then-capital", 'Do not cite the U.S. "Note" we believe the cache is broken.'),
]


@pytest.mark.parametrize("line", [l for _i, l in N1_NON_CONTINUERS], ids=[i for i, _l in N1_NON_CONTINUERS])
def test_only_a_lower_case_letter_comma_or_colon_continues_a_sentence(line):
    n = _belief_hits(line)
    assert n == 1, ("something other than a lower-case letter, comma or colon was read as a "
                    "continuation, so the prohibition reached the assertion: %r -> %r" % (line, scan_text(line)))


# --- Q1: ';' just before a closing quotation mark always ends the clause (leak direction) ---
Q1_QUOTED_SEMICOLONS = [
    ("straight-quote-then-lower", 'Do not write "stop;" we believe the cache is broken.'),
    ("straight-quote-then-capital", 'Do not write "stop;" We believe the cache is broken.'),
    ("curly-quote-then-lower", "Do not write “stop;” we believe the cache is broken."),
]


@pytest.mark.parametrize("line", [l for _i, l in Q1_QUOTED_SEMICOLONS], ids=[i for i, _l in Q1_QUOTED_SEMICOLONS])
def test_a_semicolon_just_inside_a_closing_quote_always_ends_the_clause(line):
    n = _belief_hits(line)
    assert n == 1, ("the continuation test was applied to a quoted ';' — it must end the clause "
                    "unconditionally: %r -> %r" % (line, scan_text(line)))


# =============================================================================================
# Coverage (orchestrator gate). From an earlier review (B2, S1), reproduced by the
# orchestrator: N1 claims "only a lower-case letter, comma or colon continues a sentence" but
# sampled 6 rows. A rule that lets a caseless or titlecase letter continue, or one that skips "[",
# "{" and a curly opening quote as closers, passed all 116 tests while letting the prohibition
# reach the assertion (a leak). W1 pinned each whitespace code point alone, so a rule that skips
# at most ONE whitespace character passed too (a false red on "U.S.  we").
# Contract (the _continues docstring): skip every whitespace character and every named closer;
# the sentence continues iff the next character is a lower-case letter, a comma or a colon.
# Pinned over every BMP code point plus astral samples, in both positions (right after the dotted
# run, and after a space, which makes any whitespace character the second of a run). The closer
# set is the test's own K1 list, not the code's, so a wrong rule cannot redefine the oracle.
# Line-splitting characters are excluded, as in W1.
# =============================================================================================
import unicodedata  # noqa: E402

C7_FORMS = ("Do not cite the U.S. %s we believe the cache is broken.",
            "Do not cite the U.S.%s we believe the cache is broken.")
C7_CHARS = [chr(n) for n in range(0x10000)] + [chr(n) for n in (0x10400, 0x10428, 0x1D4B6, 0x1F600,
                                                                  0x20000, 0xE0001)]


def _c7_documented_hits(c):
    """1 when the prohibition must end before ' we believe', 0 when the sentence continues."""
    closers = {ch for ch, _label in K1_CLOSERS}
    continues = (c.isspace() or c in closers or (c.isalpha() and c.islower()) or c in (",", ":"))
    return 0 if continues else 1


def test_checker_invocation_the_continuation_rule_holds_for_every_character_after_a_dotted_run():
    failures, total = [], 0
    for c in C7_CHARS:
        if _splits_a_line(c):
            continue
        want = _c7_documented_hits(c)
        for form in C7_FORMS:
            total += 1
            line = form % c
            boundary = _is_clause_break(line, line.index("U.S.") + 3)
            if boundary != bool(want):
                failures.append("U+%04X boundary: %r, want %r" % (ord(c), boundary, bool(want)))
            # Continuation and negation reach are separate contracts now. The boundary
            # oracle above stays independent; quote forbidden wording in quiet rows.
            if want == 0:
                line = line.replace("we believe the cache is broken", "“we believe the cache is broken”")
            got = _belief_hits(line)
            if got != want:
                failures.append("U+%04X (%s) in %r: %d belief hit(s), want %d"
                                % (ord(c), unicodedata.category(c), form % c, got, want))
    assert not failures, ("%d of %d character/position case(s) break the continuation rule:\n  "
                          % (len(failures), total)) + "\n  ".join(failures[:30])


def test_checker_invocation_control_the_continuation_oracle_is_independent_and_can_fail():
    assert len(K1_CLOSERS) == 8
    assert not [n for n in range(0x10000, 0x110000) if chr(n).isspace()]
    # each wrong rule the review built is a disagreement with this oracle on a named character
    assert _c7_documented_hits("\u65e5") == 1 and _c7_documented_hits("\u01c5") == 1   # caseless, titlecase
    assert _c7_documented_hits("[") == 1 and _c7_documented_hits("{") == 1             # openers
    assert _c7_documented_hits("\u2018") == 1
    assert _c7_documented_hits(" ") == 0 and _c7_documented_hits("\t") == 0            # a run of two
    assert _c7_documented_hits("a") == 0 and _c7_documented_hits(",") == 0


# =============================================================================================
# Coverage (orchestrator gate). From an earlier review (B3), reproduced by the
# orchestrator: a C(j) that skips at most ONE closing bracket or quotation mark passed all 118
# tests. On "U.S.)) we believe" the sentence continues, but that rule stops at the second ")"
# and reads a sentence end (a false red). C7 pinned each character ALONE; W1 closed the same
# defect for runs of whitespace only.
# Contract (the _continues docstring): skip EVERY whitespace character and EVERY named closer,
# in any number and any mix. Pinned over every run of two and of three drawn from the K1 closers
# and four whitespace characters, at both places C(j) is used (after a dotted-letter run, and
# after a closing quotation mark with '.' before it), in both directions: a lower-case "we"
# continues the sentence, an upper-case "We" does not.
# =============================================================================================
C8_RUN_PARTS = [ch for ch, _label in K1_CLOSERS] + [" ", "\t", chr(0xA0), chr(0x3000)]
C8_RUNS = ([a + b for a in C8_RUN_PARTS for b in C8_RUN_PARTS]
           + [a + b + c for a in C8_RUN_PARTS for b in C8_RUN_PARTS for c in C8_RUN_PARTS])
C8_SITES = (("after a dotted-letter run", "Do not cite the U.S.%s %s believe the cache is broken."),
            ("after a closing quotation mark", 'Do not say "stop."%s %s believe the cache is broken.'))


def test_stored_value_a_run_of_closers_and_whitespace_is_skipped_whatever_its_length_and_mix():
    failures, total = [], 0
    for site, form in C8_SITES:
        for run in C8_RUNS:
            for word, want in (("we", 0), ("We", 1)):
                total += 1
                line = form % (run, word)
                # Use the last dotted-run period or the period just inside the quote.
                period = line.index("U.S.") + 3 if "U.S." in line else line.index(".")
                boundary = _is_clause_break(line, period)
                if boundary != bool(want):
                    failures.append("%s, run %r: boundary %r, want %r"
                                    % (site, run, boundary, bool(want)))
                if want == 0:
                    # A curly opener cannot pair with the run's unpaired straight quotes.
                    line = line.replace("we believe the cache is broken", "“we believe the cache is broken”")
                got = _belief_hits(line)
                if got != want:
                    failures.append("%s, run %r: %r has %d belief hit(s), want %d"
                                    % (site, run, line, got, want))
    assert not failures, ("%d of %d run/site case(s) break the continuation rule:\n  "
                          % (len(failures), total)) + "\n  ".join(failures[:30])


def test_stored_value_control_the_run_forms_reach_the_continuation_rule_and_can_fail():
    assert len(C8_RUN_PARTS) == 12 and len(C8_RUNS) == 12 ** 2 + 12 ** 3
    assert not [c for c in C8_RUN_PARTS if _splits_a_line(c)]
    for _site, form in C8_SITES:
        # with no run, each form reads as the rule says in both directions
        assert _belief_hits(form % ("", "we")) == 0 and _belief_hits(form % ("", "We")) == 1
    # the lower-case form CAN report a hit: an ordinary word's period always ends the sentence
    assert _belief_hits("Do not cite the files.)) we believe the cache is broken.") == 1


# Follow-up coverage: joining marks block negation, not sentence continuation.
JOINING_MARKS = [
    ("colon", ":"), ("comma", ","), ("open-parenthesis", "("),
    ("close-parenthesis", ")"), ("em-dash", "—"), ("en-dash", "–"),
    ("spaced-hyphen", "-"), ("spaced-double-hyphen", "--"), ("and", "and"),
]
JOINING_CASES = [
    (mark_id + "-" + form_id, "Do not edit files %s %s" % (mark, phrase), want)
    for mark_id, mark in JOINING_MARKS
    for form_id, phrase, want in [
        ("leak", "we believe the cache is broken.", 1),
        ("quoted", '"we believe the cache is broken".', 0),
        ("backticks", "`we believe the cache is broken`.", 0),
        ("curly-quotes", "“we believe the cache is broken”.", 0),
        ("direct-negation", "do not say we believe the cache is broken.", 0),
    ]
]


@pytest.mark.parametrize("line, want", [(line, want) for _id, line, want in JOINING_CASES],
                         ids=[_id for _id, _line, _want in JOINING_CASES])
def test_joining_marks_block_only_intervening_negation(line, want):
    """Class: each named joining mark × bare leak / three quotation forms / later direct
    negation. The old _negated fails every leak row. This is not a semantic intent test;
    unmarked leaks remain a documented residual, not a desired-behaviour control.
    """
    assert _belief_hits(line) == want, (line, scan_text(line))


JOINING_CONTROLS = [
    ("colon-prohibition", "Never write: we believe X.", 1),
    ("comma-prohibition", "Do not, under any circumstances, tell them we believe X.", 1),
    ("quoted-colon-prohibition", 'Never write: "we believe X".', 0),
    ("quoted-comma-prohibition", 'Do not, under any circumstances, tell them "we believe X".', 0),
    ("direct-prohibition", "Do not say we believe the cache is broken.", 0),
    ("and-upper", "Do not edit files AND we believe the cache is broken.", 1),
    ("and-word-boundary", "Do not say candy means we believe the cache is broken.", 0),
    ("unspaced-hyphen", "Do not say cache-related we believe the cache is broken.", 0),
    ("tab-spaced-hyphen", "Do not edit files\t-\twe believe the cache is broken.", 1),
    ("nbsp-spaced-double-hyphen", "Do not edit files\xa0--\xa0we believe the cache is broken.", 1),
    ("occurrence-before-mark", "Do not say we believe X, then investigate.", 0),
]


@pytest.mark.parametrize("line, want", [(line, want) for _id, line, want in JOINING_CONTROLS],
                         ids=[_id for _id, _line, _want in JOINING_CONTROLS])
def test_joining_mark_controls(line, want):
    assert _belief_hits(line) == want, (line, scan_text(line))


@pytest.mark.parametrize("line, want", [
    ('Never write "note: safe" we believe the cache is broken.', 1),
    ('Do not state our hypothesis: the cache is broken.', 0),
    ('Do not say 10:30 we believe the cache is broken.', 1),
    ('Do not state: our hypothesis: the cache is broken.', 1),
    ('Never write "note safe" we believe the cache is broken.', 0),
    ('Rule: do not say we believe the cache is broken.', 0),
    ('Do not edit files --- we believe the cache is broken.', 0),
], ids=['original-quoted-mark', 'occurrence-end-excluded', 'mark-without-space',
        'intervening-mark-control', 'no-mark-control', 'mark-before-negation', 'triple-hyphen'])
def test_negation_mark_source_and_window(line, want):
    """Original-line marks, ending at occurrence START, without a whitespace requirement.

    Finite lexical controls, including a positive control for the quiet hypothesis
    occurrence; this does not decide semantic intent or extend the joining class.
    """
    assert len(scan_text(line)) == want, (line, scan_text(line))


# Derive rows from the prose contract, never from the implementation regex.
def _documented_joiners():
    import re
    from guard import brief_scan
    contract = re.search(r"The mark class is (.*?)\.\n", brief_scan.__doc__, re.S)
    assert contract, 'missing documented joining class'
    prose = ' '.join(contract.group(1).split())
    marks, boundaries = prose.split(", whitespace-surrounded ", 1)
    vocabulary = {
        'colon': ':', 'comma': ',', 'opening/closing round parenthesis': '()',
        'em dash': '—', 'en dash': '–',
    }
    names = marks.split(', ')
    assert set(names) == set(vocabulary), ('unclassified documented marks', names)
    punctuation = ''.join(vocabulary[name] for name in names)
    hyphens, word = boundaries.split(', and the whole word ', 1)
    forms = re.findall(r"'([^']+)'", hyphens)
    assert ' or '.join(repr(form) for form in forms) == hyphens
    match = re.fullmatch(r"'([^']+)' \(case-insensitive\)", word)
    assert match, ('unclassified documented word boundary', word)
    return punctuation, forms, match.group(1)


A26_MARKS, A26_HYPHENS, A26_WORD = _documented_joiners()
A26_CONTEXTS = [('letters', 'ab', 'cd'), ('digits', '12', '34'),
                ('after-only', 'ab', ' cd'), ('before-only', 'ab ', 'cd')]
A26_ROWS = [
    ('mark-%s-%s' % (ord(mark), context), left + mark + right, 1)
    for mark in A26_MARKS for context, left, right in A26_CONTEXTS
] + [
    ('hyphen-%s-%s' % (len(mark), context), left + mark + right, want)
    for mark in A26_HYPHENS
    for context, left, right, want in [
        *[(context, left, right, 0) for context, left, right in A26_CONTEXTS],
        ('surrounded', 'ab ', ' cd', 1), ('tabs', 'ab\t', '\tcd', 1),
        ('nbsp', 'ab\xa0', '\xa0cd', 1),
    ]
] + [
    ('word-%s-%s' % (word, context), left + word + right, want)
    for word in (A26_WORD, A26_WORD.upper(), A26_WORD.title())
    for context, left, right, want in [
        ('whole', 'ab ', ' cd', 1), ('punctuation', 'ab/', '/cd', 1),
        *[(context, left, right, 0) for context, left, right in A26_CONTEXTS],
        ('underscore-before', '_', ' cd', 0), ('underscore-after', 'ab ', '_', 0),
    ]
]


@pytest.mark.parametrize('fragment,want', [(fragment, want) for _, fragment, want in A26_ROWS],
                         ids=[name for name, _, _ in A26_ROWS])
def test_documented_marks_in_boundary_contexts(fragment, want):
    """Every documented mark × four adjacency contexts; lexical boundaries separately.

    Finite letters/digits representatives; the test below enumerates whitespace.
    These rows do not infer semantic intention. Hyphens need whitespace on both sides; the word needs
    word boundaries on both sides, with case-insensitive recognition.
    """
    line = 'Do not say ' + fragment + ' we believe the cache is broken.'
    assert _belief_hits(line) == want, (fragment, want, scan_text(line))


def test_documented_joining_class_equals_implementation():
    """Bidirectional class pin in the current regex grammar; reject unknown grammar.

    Construct the canonical regex from the independently parsed prose. An added
    or removed regex member or a changed boundary fails, even if no row used it.
    Equivalent alternative regex grammars require review, not silent acceptance.
    """
    import re
    from guard.brief_scan import CLAUSE_JOIN
    assert A26_HYPHENS == ['-', '--'], ('unclassified hyphen grammar', A26_HYPHENS)
    expected = '[' + A26_MARKS + r']|(?<=\s)--?(?=\s)|\b' + A26_WORD + r'\b'
    assert CLAUSE_JOIN.pattern == expected, ('documented/implemented joining class',
                                            expected, CLAUSE_JOIN.pattern)
    assert CLAUSE_JOIN.flags == (re.IGNORECASE | re.UNICODE), CLAUSE_JOIN.flags



# Enumerate the alphabet from the regex engine, not str.isspace or a curated list.
def _consumer_whitespace():
    import re
    return tuple(chr(i) for i in range(sys.maxunicode + 1) if re.fullmatch(r'\s', chr(i)))


A27_WHITESPACE = _consumer_whitespace()


def _whitespace_consumer_rows():
    marks, hyphens, word = _documented_joiners()
    forms = [(mark, 'mark') for mark in marks] + [(mark, 'hyphen') for mark in hyphens]
    forms += [(form, 'word') for form in (word, word.upper(), word.title())]
    for mark, kind in forms:
        for context, left, right in A26_CONTEXTS:
            want = int(kind == 'mark')
            yield '%s/%s' % (repr(mark), context), 'Do not say ' + left + mark + right + ' we believe X', want
        for ws in A27_WHITESPACE:
            for count in (1, 2, 3):
                space = ws * count
                for side, left, right in (('both', space, space), ('before', space, ''), ('after', '', space)):
                    line = 'Do not say ab' + left + mark + right + 'cd we believe X'
                    # scan_text processes splitlines: a line break also ends the
                    # negation window, independently of the joining-mark rule.
                    split = len(line.splitlines()) > 1
                    want = int(split or kind == 'mark' or side == 'both')
                    yield '%s/U+%04X/%s/%s' % (repr(mark), ord(ws), count, side), line, want
            # At window start the actual previous character belongs to "not".
            # At window end the next character is the occurrence's initial "w".
            for edge, line in (
                ('start', 'Do not' + mark + ws + 'we believe X'),
                ('end', 'Do not say' + ws + mark + 'we believe X'),
            ):
                split = len(line.splitlines()) > 1
                want = int(kind == 'mark' or split)
                if kind == 'word':
                    # Concatenation removes a required word boundary: at start
                    # "notand" is no negation; at end "andwe" is no occurrence.
                    want = int(edge == 'start')
                yield '%s/U+%04X/%s' % (repr(mark), ord(ws), edge), line, want


A27_ROWS = list(_whitespace_consumer_rows())


@pytest.mark.parametrize('label,line,want', A27_ROWS, ids=[row[0] for row in A27_ROWS])
def test_whitespace_through_consumer(label, line, want):
    """Prose joiners × re whitespace alphabet × runs/sides/window edges.

    Includes line separators through scan_text's physical-line consumer. Runs
    have lengths one through three; mixtures and arbitrary lengths are not an
    exhaustive string claim. Letters/digits use the documented representatives.
    """
    assert _belief_hits(line) == want, (label, repr(line), want, scan_text(line))


# The explicit product supplements every older consumer row.
A29_PLACEMENTS = ('before', 'after', 'both')
A29_EDGES = ('start', 'end', 'interior')
A29_NEIGHBOURS = (('letters', 'ab', 'cd'), ('digits', '12', '34'))
A29_RUNS = (1, 2, 3)


def _consumer_product_rows():
    from itertools import product
    marks, hyphens, word = _documented_joiners()
    forms = [(mark, 'mark') for mark in marks] + [(mark, 'hyphen') for mark in hyphens]
    forms += [(form, 'word') for form in (word, word.upper(), word.title())]
    for (mark, kind), side, edge, (neighbour, left, right), ws, count in product(
            forms, A29_PLACEMENTS, A29_EDGES, A29_NEIGHBOURS, A27_WHITESPACE, A29_RUNS):
        before = ws * count if side in ('before', 'both') else ''
        after = ws * count if side in ('after', 'both') else ''
        prefix = 'Do not' if edge == 'start' else 'Do not say ' + left
        suffix = 'we believe X' if edge == 'end' else right + ' we believe X'
        line = prefix + before + mark + after + suffix
        # Independent lexical oracle for these finite templates. Both digits
        # and letters are word characters; window edges are real characters,
        # never implicit whitespace. A splitline starts a fresh negation window.
        split = len(line.splitlines()) > 1
        negation_exists = not (edge == 'start' and kind == 'word' and not before)
        occurrence_exists = not (edge == 'end' and kind == 'word' and not after)
        joins = kind == 'mark' or bool(before and after)
        want = int(occurrence_exists and (not negation_exists or split or joins))
        label = '%r/%s/%s/%s/U+%04X/%d' % (mark, side, edge, neighbour, ord(ws), count)
        yield label, line, want


A29_ROWS = list(_consumer_product_rows())


def _consumer_axes_sentence():
    return ('Consumer product: documented marks, hyphens and three word cases x '
            + '/'.join(A29_PLACEMENTS) + ' x ' + '/'.join(A29_EDGES)
            + ' x ' + '/'.join(n[0] for n in A29_NEIGHBOURS)
            + ' x every regex whitespace code point x runs ' + '/'.join(map(str, A29_RUNS)) + '.')


def _assert_consumer_product(rows):
    from itertools import product
    marks, hyphens, word = _documented_joiners()
    forms = list(marks) + list(hyphens) + [word, word.upper(), word.title()]
    templates = {
        'start': 'Do not{before}{mark}{after}{right} we believe X',
        'end': 'Do not say {left}{before}{mark}{after}we believe X',
        'interior': 'Do not say {left}{before}{mark}{after}{right} we believe X',
    }
    expected = {}
    for mark, side, edge, (neighbour, left, right), ws, count in product(
            forms, ('before', 'after', 'both'), ('start', 'end', 'interior'),
            (('letters', 'ab', 'cd'), ('digits', '12', '34')), A27_WHITESPACE, (1, 2, 3)):
        label = '%r/%s/%s/%s/U+%04X/%d' % (mark, side, edge, neighbour, ord(ws), count)
        expected[label] = templates[edge].format(
            left=left, right=right, mark=mark,
            before='' if side == 'after' else ws * count,
            after='' if side == 'before' else ws * count)
    actual = {label: line for label, line, want in rows}
    assert len(rows) == len(expected) and actual == expected, (
        'consumer product omitted or changed contexts',
        sorted(set(expected) - set(actual))[:5],
        [(label, repr(line), repr(actual.get(label))) for label, line in expected.items()
         if actual.get(label) != line][:5])


def test_product_and_documentation():
    from pathlib import Path
    _assert_consumer_product(A29_ROWS)
    sentence = _consumer_axes_sentence()
    readme = Path(__file__).resolve().parents[1].joinpath('README.md').read_text()
    assert sentence in readme
    assert sentence in test_consumer_product.__doc__
    # A retained label cannot conceal a missing byte-level axis. Both omission
    # and mislabelled context controls must fail the independent template product.
    label, line, want = A29_ROWS[0]
    for broken in (A29_ROWS[1:], [(label, 'Do not we believe X', want)] + A29_ROWS[1:]):
        with pytest.raises(AssertionError, match='consumer product omitted or changed contexts'):
            _assert_consumer_product(broken)


def test_consumer_product():
    """Consumer product: documented marks, hyphens and three word cases x before/after/both x start/end/interior x letters/digits x every regex whitespace code point x runs 1/2/3.

    At start/end the selected whitespace abuts the negation/occurrence; if that
    side has no whitespace the mark touches it. The other neighbour is the
    selected representative. Interior uses both representatives. Physical line
    breaks reach scan_text; mixtures, longer runs and other neighbours are outside.
    """
    # Batch the additional rows in one pytest item to avoid thousands of HOME
    # fixture directory pairs. Failures still carry the exact generated context.
    failures = []
    for label, line, want in A29_ROWS:
        got = _belief_hits(line)
        if got != want:
            failures.append((label, repr(line), want, got))
    assert not failures, ('consumer contexts', len(failures), failures[:20])
    print('CONSUMER ROWS:', len(A29_ROWS))


ALL = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    failures = 0
    for fn in ALL:
        for label, call in _expand(fn):
            try:
                call()
                print("  ok    %s" % label)
            except AssertionError as exc:
                failures += 1
                print("  FAIL  %s: %s" % (label, exc))
    if failures:
        print("RESULT: %d check(s) RED" % failures)
        sys.exit(1)
    print(MARKER)
