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