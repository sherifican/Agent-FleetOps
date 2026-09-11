"""Tests for guard/doc_count_drift.py — _claims_guard_suite banner branch."""

import importlib.util
import pathlib

# Resolve the module from this file's location; conftest chdirs for every test.
_MODULE_PATH = pathlib.Path(__file__).resolve().parent.parent / "doc_count_drift.py"
_spec = importlib.util.spec_from_file_location("doc_count_drift", _MODULE_PATH)
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

_claims_guard_suite = mod._claims_guard_suite


def test_banner_svg_positive():
    """The paired banner line starting with '<n> guard' is claimed on an .svg path."""
    line = "309 guard + 364 TUI · no live fleet 67"
    rel = "docs/banner.svg"
    assert _claims_guard_suite(line, rel) == [309]


def test_prose_md_negative():
    """The same phrasing on a .md line is NOT claimed by the new branch."""
    line = "309 guard + 364 TUI · no live fleet"
    rel = "README.md"
    assert _claims_guard_suite(line, rel) == []


def test_guards_stat_not_claimed():
    """The '<n> guards' banner stat belongs to a different check, not this finder."""
    line = "27 guards"
    rel = "docs/banner.svg"
    assert _claims_guard_suite(line, rel) == []


def test_existing_hermetic_unit_gates():
    """The original phrasing '<n> hermetic unit gates' still works."""
    line = "309 hermetic unit gates"
    rel = "README.md"
    assert _claims_guard_suite(line, rel) == [309]


def test_existing_guard_tests_phrasing():
    """A line mentioning 'guard/tests' with '<n> tests' still works."""
    line = "pytest guard/tests/ -q — 309 tests"
    rel = "README.md"
    assert _claims_guard_suite(line, rel) == [309]

_claims_tui_suite = mod._claims_tui_suite


def test_the_tui_acceptance_claim_in_the_adoption_guide_is_seen():
    """CONTROL: the phrasing the finder was written for is still recognised."""
    line = "**VERIFY — expected output:** pytest exits `0`; this export's acceptance run reports `386 passed`."
    assert _claims_tui_suite(line, "adopt/10_tui.md") == [386]


def test_the_same_claim_in_the_verify_all_guide_is_also_seen():
    """The top-level verify-all guide publishes the SAME measurement in different words.

    The finder was scoped to one filename so that historical results would not be read as current
    claims. adopt/90_verify_all.md is not a historical result: it is the guide a reader runs, and its
    number IS a claim about the current suite. Scoped out, it drifted to a count the suite has not
    reported for some time while the checker said every documented count matched its instrument — a
    guard reporting clean about a surface it cannot see.
    """
    line = ("**VERIFY — expected output:** inventory checks exit `0`; "
            "TUI pytest exits `0` and reports `386 passed` in this export")
    assert _claims_tui_suite(line, "adopt/90_verify_all.md") == [386]


def test_a_tui_count_in_an_unrelated_document_is_still_not_claimed():
    """The scoping still has to mean something: an arbitrary file is not a current claim.

    The first version of this arm used prose WITHOUT the VERIFY marker, so it returned [] whether or
    not the filename scoping existed — it passed for the wrong reason and could not have failed if the
    scoping were deleted. Adversarial review caught that. The text below is now byte-identical to the
    allowed-path arm; only `rel` differs, so the filename restriction is the only thing under test.
    """
    line = ("**VERIFY — expected output:** inventory checks exit `0`; "
            "TUI pytest exits `0` and reports `386 passed` in this export")
    assert _claims_tui_suite(line, "adopt/90_verify_all.md") == [386], \
        "CONTROL: the identical line IS claimed on an allowed path"
    assert _claims_tui_suite(line, "docs/history/old_notes.md") == [], \
        "the same text on an unrelated path must not be read as a current claim"
