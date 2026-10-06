"""Tests for guard/doc_count_drift.py — _claims_guard_suite banner branch."""

import ast
import json
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


# =============================================================================================
# Contract: make release count coverage mandatory. Written BEFORE the fix.
#
# Interface under test: check(root, measured=None, release=False) and the CLI flag --release.
# Every fixture derives its baseline numbers from the REAL docs/banner.svg through the
# registry's own claim finders — never a remembered total — and plants a correct claim for every
# other check so the only thing that varies between cases is the combined-suite instrument.
# =============================================================================================
import re
import shutil
import subprocess
import sys

import pytest

REPO = _MODULE_PATH.parent.parent
BANNER = REPO / "docs" / "banner.svg"
COUNT_SEAM = REPO / "guard" / "tests" / "fixtures" / "count_measurement_seam.py"
COMBINED = "both hermetic suites"
PLANT = 7  # the planted value for checks the banner does not claim; any positive int works


def _fixture_tree(tmp_path, with_banner=True):
    """(root, measured, claimed_total). All claims correct; combined instrument left to the caller."""
    root = tmp_path / "tree"
    (root / "docs").mkdir(parents=True)
    banner_text = BANNER.read_text(encoding="utf-8")
    if with_banner:
        shutil.copy(BANNER, root / "docs" / "banner.svg")
    svg_lines = mod._svg_lines(banner_text)
    measured, prose = {}, []
    for name, _instrument, claimer, plant in mod.CHECKS:
        sites = [c for line in svg_lines for c in claimer(line, "docs/banner.svg")]
        assert len(set(sites)) <= 1, f"the banner disagrees with itself on {name}: {sites}"
        if sites and with_banner:
            measured[name] = (sites[0], None)
        else:
            measured[name] = (PLANT, None)
            if name != COMBINED:            # its claim is banner-only; nowhere to plant in prose
                prose.append(plant(PLANT))
    (root / "DOC.md").write_text("\n".join(prose) + "\n", encoding="utf-8")
    claimed = [c for line in svg_lines for c in mod._claims_repo_tests(line, "docs/banner.svg")]
    assert len(claimed) == 1, f"expected exactly one combined-total claim on the banner: {claimed}"
    return root, measured, claimed[0]


def _check(root, measured, release):
    """check() with the release keyword; a missing keyword is reported as the RED, not a crash."""
    try:
        return mod.check(root, measured=measured, release=release)
    except TypeError as exc:
        pytest.fail(f"check() does not accept release={release!r} — the release-mode interface "
                    f"is missing: {exc}")


# --- RED ---------------------------------------------------------------------------------------

def test_release_mode_turns_a_skipped_combined_instrument_into_unmeasured(tmp_path):
    root, measured, _ = _fixture_tree(tmp_path)
    measured[COMBINED] = (None, mod.SKIP_DEPS)
    rc, lines = _check(root, measured, release=True)
    assert rc == 2, f"release mode with an unavailable instrument behind a live claim returned {rc}: {lines}"
    assert any("UNMEASURED" in ln and COMBINED in ln for ln in lines), lines
    assert any("docs/banner.svg" in ln for ln in lines), (
        "the affected claim site must be named: %r" % lines)
    assert not any("DRIFT" in ln for ln in lines), "no other claim may be dirty: %r" % lines


def test_ordinary_mode_summary_discloses_partial_coverage(tmp_path):
    root, measured, _ = _fixture_tree(tmp_path)
    measured[COMBINED] = (None, mod.SKIP_DEPS)
    rc, lines = mod.check(root, measured=measured)
    assert rc == 0, lines
    head = lines[0]
    assert "partial" in head.lower() and re.search(r"\b1 skipped\b", head), (
        "the ordinary-mode summary must say coverage is partial and how many checks skipped: %r" % head)


def test_release_mode_correct_combined_count_is_clean(tmp_path):
    root, measured, claimed = _fixture_tree(tmp_path)
    measured[COMBINED] = (claimed, None)
    rc, lines = _check(root, measured, release=True)
    assert rc == 0, lines
    assert not any("skipped" in ln or "UNMEASURED" in ln for ln in lines), lines


def test_release_mode_stale_combined_count_is_a_violation(tmp_path):
    root, measured, claimed = _fixture_tree(tmp_path)
    measured[COMBINED] = (claimed + 1, None)
    rc, lines = _check(root, measured, release=True)
    assert rc == 1, lines
    assert any("docs/banner.svg" in ln and f"says {claimed}" in ln for ln in lines), lines


def test_release_mode_keeps_a_zero_site_check_unmeasured(tmp_path):
    root, measured, claimed = _fixture_tree(tmp_path, with_banner=False)
    measured[COMBINED] = (claimed, None)
    rc, lines = _check(root, measured, release=True)
    assert rc == 2 and any("UNMEASURED" in ln and COMBINED in ln for ln in lines), lines


def test_cli_release_flag_makes_a_skipped_suite_unmeasured():
    """The REAL entry point, through the seam wrapper: only the pytest collection is faked."""
    p = subprocess.run([sys.executable, str(COUNT_SEAM), "--release"], cwd=REPO,
                       capture_output=True, text=True)
    out = p.stdout + p.stderr
    assert re.search(r"UNMEASURED.*both hermetic suites", out) and "docs/banner.svg" in out, out
    assert p.returncode == 2, f"--release with an uncollectable suite exited {p.returncode}:\n{out}"


# --- CONTROL -----------------------------------------------------------------------------------

def test_control_ordinary_mode_skips_an_unavailable_combined_instrument(tmp_path):
    root, measured, _ = _fixture_tree(tmp_path)
    measured[COMBINED] = (None, mod.SKIP_DEPS)
    rc, lines = mod.check(root, measured=measured)
    assert rc == 0, lines
    assert any("skipped" in ln and COMBINED in ln for ln in lines), lines
    assert not any("DRIFT" in ln or "UNMEASURED" in ln for ln in lines), lines


def test_control_correct_combined_count_is_clean(tmp_path):
    root, measured, claimed = _fixture_tree(tmp_path)
    measured[COMBINED] = (claimed, None)
    rc, lines = mod.check(root, measured=measured)
    assert rc == 0 and not any("skipped" in ln for ln in lines), lines


def test_control_stale_combined_count_is_a_violation(tmp_path):
    root, measured, claimed = _fixture_tree(tmp_path)
    measured[COMBINED] = (claimed + 1, None)
    rc, lines = mod.check(root, measured=measured)
    assert rc == 1 and any(f"says {claimed}" in ln for ln in lines), lines


def test_control_no_sites_is_unmeasured(tmp_path):
    root, measured, claimed = _fixture_tree(tmp_path, with_banner=False)
    measured[COMBINED] = (claimed, None)
    rc, lines = mod.check(root, measured=measured)
    assert rc == 2 and any("UNMEASURED" in ln and COMBINED in ln for ln in lines), lines


def test_control_collector_error_or_partial_import_never_supplies_a_total(tmp_path, monkeypatch):
    (tmp_path / 'tests').mkdir()
    (tmp_path / 'pyproject.toml').write_text(
        '[project]\ndependencies = ["fixture_external_dep"]\n')
    def fake(cmd, **kw):
        import json
        pathlib.Path(cmd[3]).write_text(json.dumps({
            'collected': 12, 'deselected': 0, 'collection_finished': True,
            'failures': fake.causes}))
        return subprocess.CompletedProcess(cmd, fake.status, stdout=fake.out, stderr="")
    monkeypatch.setattr(mod.subprocess, "run", fake)
    fake.status = 2
    fake.causes = [{"name": "fixture_external_dep", "absent": True}]
    fake.out = "ImportError: No module named 'x'\n"
    assert mod._collect(tmp_path, 'tests') == (None, mod.SKIP_DEPS)
    fake.out = "ModuleNotFoundError: No module named 'x'\n3 tests collected\n"
    assert mod._collect(tmp_path, 'tests') == (None, mod.SKIP_DEPS)
    fake.causes = [None]
    fake.out = "3 tests collected\n1 error\n"
    n, note = mod._collect(tmp_path, 'tests')
    assert n is None and "floor" in note, (n, note)
    fake.status = 0
    fake.causes = []
    fake.out = "12 tests collected in 0.01s\n"
    assert mod._collect(tmp_path, 'tests') == (12, None), "positive control: a clean total is read"
    fake.status = 2
    fake.causes = [{"name": "fixture_external_dep", "absent": True}]
    fake.out = "ImportError: No module named 'x'\n"
    n, note = mod.measure_guard_suite(REPO)
    assert n is None and note != mod.SKIP_DEPS, "guard declares no dependencies"


def test_control_cli_ordinary_mode_reports_the_skip_and_exits_0():
    p = subprocess.run([sys.executable, str(COUNT_SEAM)], cwd=REPO, capture_output=True, text=True)
    out = p.stdout + p.stderr
    assert re.search(r"skipped\s+both hermetic suites", out), out
    assert p.returncode == 0, f"ordinary mode with a skipped suite exited {p.returncode}:\n{out}"


def test_control_svg_claim_extraction_and_historical_exclusions_intact():
    svg_lines = mod._svg_lines(BANNER.read_text(encoding="utf-8"))
    sites = [c for line in svg_lines for c in mod._claims_repo_tests(line, "docs/banner.svg")]
    assert len(sites) == 1 and isinstance(sites[0], int) and sites[0] > 0, sites
    assert mod._claims_repo_tests(f"{sites[0]} tests", "README.md") == [], "banner-only claim"
    assert mod._claims_guard_suite("this change adds 6 hermetic tests", "CHANGELOG.md") == []
    assert mod._claims_tui_suite("A historical run reported `380 passed`.", "adopt/10_tui.md") == []


def test_control_inventory_follows_the_git_index(tmp_path):
    """An untracked document with a wrong recognized count is invisible; staging it makes the
    checker return 1. Release mode must therefore run against the staged publication snapshot."""
    root, measured, claimed = _fixture_tree(tmp_path)
    measured[COMBINED] = (claimed, None)
    git = ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run(git + ["init", "-q"], check=True, capture_output=True)
    subprocess.run(git + ["add", "-A"], check=True, capture_output=True)
    subprocess.run(git + ["commit", "-q", "-m", "fixture"], check=True, capture_output=True)
    assert mod.check(root, measured=measured)[0] == 0, "fixture baseline must be clean"
    stray = root / "STRAY.md"
    stray.write_text(f"{PLANT + 1} skills\n", encoding="utf-8")
    rc, lines = mod.check(root, measured=measured)
    assert rc == 0, "an untracked document is outside the inventory: %r" % lines
    subprocess.run(git + ["add", "STRAY.md"], check=True, capture_output=True)
    rc, lines = mod.check(root, measured=measured)
    assert rc == 1 and any("STRAY.md" in ln for ln in lines), lines


# =============================================================================================
# Earlier integration coverage. Written BEFORE the fix.
#
# R4: check()'s docstring promises "a check with zero claim sites stays UNMEASURED in both
#     modes". Measured false on the tip: ordinary mode + an instrument skipped with SKIP_DEPS +
#     zero claim sites for that check exits 0 with a `skipped` line. Both modes must be
#     UNMEASURED (2) with the zero-sites wording the zero-sites path already prints.
# R5: the README's release row tells the reviewer to "read the emitted claim inventory", but a
#     verified check prints only `N claim(s) in M file(s)`. In release mode every claim site of
#     every verified check must be listed as `<rel>:<line> says <n>`, and for an SVG `<line>`
#     is the SOURCE line of the <text> node carrying the number, not its pair index.
#
# Fixture: fully hermetic — no live document is read. Every check gets a prose plant from the
# registry's own canonical phrasing; the banner-only combined claim gets an SVG the test writes.
# =============================================================================================

COUNT_PLANT = 7
COUNT_SVG = "docs/banner.svg"
COUNT_ZERO_SITES_WORDING = "no documented count matched"


def _count_fixture_prose_lines(omit=()):
    """One canonical claim per check, in registry order — except the banner-only combined claim
    and anything in `omit`. Returns [(check name, line text)]."""
    return [(name, plant(COUNT_PLANT)) for name, _i, _c, plant in mod.CHECKS
            if name != COMBINED and name not in omit]


def _count_fixture_svg(number, subject, filler=0):
    """An SVG whose count node sits on a KNOWN source line that differs from its pair index.
    Returns (text, source line of the number node, pair index _svg_lines would give it)."""
    nodes = [f"<text>filler-{i}</text>" for i in range(filler)]
    lines = ["<svg>"] + nodes + [f"<text>{number}</text>", f"<text>{subject}</text>", "</svg>"]
    number_line = 1 + filler + 1                      # <svg> is line 1
    pair_index = filler + 1                            # pairs are (node k, node k+1), 1-based
    return "\n".join(lines) + "\n", number_line, pair_index


def _count_fixture_tree(tmp_path, svg=None, omit=()):
    """(root, measured). All instruments answer COUNT_PLANT; all prose claims are correct."""
    root = tmp_path / "test_fixture"
    (root / "docs").mkdir(parents=True)
    prose = _count_fixture_prose_lines(omit)
    (root / "DOC.md").write_text("".join(text + "\n" for _n, text in prose), encoding="utf-8")
    if svg is not None:
        (root / COUNT_SVG).write_text(svg, encoding="utf-8")
    measured = {name: (COUNT_PLANT, None) for name, _i, _c, _p in mod.CHECKS}
    return root, measured


def _count_fixture_tree_with_banner(tmp_path, filler=3):
    svg, number_line, pair_index = _count_fixture_svg(COUNT_PLANT, "tests", filler=filler)
    root, measured = _count_fixture_tree(tmp_path, svg=svg)
    return root, measured, number_line, pair_index


def _count_fixture_fixture_is_clean(root, measured):
    rc, lines = mod.check(root, measured=measured)
    assert rc == 0, "fixture baseline must be clean in ordinary mode: %r" % lines


# --- R4 RED -----------------------------------------------------------------------------------

def test_ordinary_mode_a_skipped_instrument_with_zero_claim_sites_is_unmeasured(tmp_path):
    root, measured = _count_fixture_tree(tmp_path)                 # no SVG -> combined has zero sites
    measured[COMBINED] = (None, mod.SKIP_DEPS)
    rc, lines = mod.check(root, measured=measured)
    assert rc == 2, ("a check with zero claim sites must be UNMEASURED (2) in ordinary mode even "
                     "when its instrument skipped; got %d: %r" % (rc, lines))
    assert any("UNMEASURED" in ln and COMBINED in ln and COUNT_ZERO_SITES_WORDING in ln
               for ln in lines), (
        "the zero-sites path's own wording (%r) must name the check: %r" % (COUNT_ZERO_SITES_WORDING, lines))
    assert not any("skipped" in ln and COMBINED in ln for ln in lines), (
        "a check that verified nothing must not be reported as a disclosed skip: %r" % lines)


def test_release_mode_a_skipped_instrument_with_zero_claim_sites_is_unmeasured(tmp_path):
    root, measured = _count_fixture_tree(tmp_path)
    measured[COMBINED] = (None, mod.SKIP_DEPS)
    rc, lines = _check(root, measured, release=True)
    assert rc == 2, lines
    assert any("UNMEASURED" in ln and COMBINED in ln and COUNT_ZERO_SITES_WORDING in ln
               for ln in lines), (
        "release mode must use the zero-sites wording (%r) for a check with no claim sites: %r"
        % (COUNT_ZERO_SITES_WORDING, lines))


# --- R4 CONTROL -------------------------------------------------------------------------------

def test_control_a_skipped_instrument_with_a_claim_site_stays_a_disclosed_skip(tmp_path):
    root, measured, _nl, _pi = _count_fixture_tree_with_banner(tmp_path)
    _count_fixture_fixture_is_clean(root, measured)
    measured[COMBINED] = (None, mod.SKIP_DEPS)
    rc, lines = mod.check(root, measured=measured)
    assert rc == 0, lines
    assert any("skipped" in ln and COMBINED in ln for ln in lines), lines
    assert not any("UNMEASURED" in ln for ln in lines), lines


def test_control_zero_sites_with_an_available_instrument_is_unmeasured(tmp_path):
    root, measured = _count_fixture_tree(tmp_path)                 # combined: instrument answers, no site
    for release in (False, True):
        rc, lines = _check(root, measured, release=release)
        assert rc == 2 and any("UNMEASURED" in ln and COMBINED in ln and COUNT_ZERO_SITES_WORDING in ln
                               for ln in lines), (release, lines)


def test_control_the_fixture_is_clean_when_every_check_has_a_site(tmp_path):
    root, measured, _nl, _pi = _count_fixture_tree_with_banner(tmp_path)
    for release in (False, True):
        rc, lines = _check(root, measured, release=release)
        assert rc == 0 and not any("UNMEASURED" in ln or "skipped" in ln for ln in lines), (release, lines)


# --- R5 RED -----------------------------------------------------------------------------------

def test_release_mode_lists_every_verified_prose_claim_site(tmp_path):
    root, measured, _nl, _pi = _count_fixture_tree_with_banner(tmp_path)
    rc, lines = _check(root, measured, release=True)
    assert rc == 0, lines
    missing = []
    for lineno, (name, _text) in enumerate(_count_fixture_prose_lines(), 1):
        want = f"DOC.md:{lineno} says {COUNT_PLANT}"
        if not any(want in ln for ln in lines):
            missing.append(f"{name}: {want}")
    assert not missing, ("release mode must list every verified claim site as <rel>:<line> says <n>; "
                         "%d of %d site(s) unlisted:\n  " % (len(missing), len(_count_fixture_prose_lines()))
                         + "\n  ".join(missing) + "\nreport was: %r" % lines)


def test_release_mode_lists_a_verified_svg_site_at_its_source_line(tmp_path):
    root, measured, number_line, pair_index = _count_fixture_tree_with_banner(tmp_path, filler=3)
    assert number_line != pair_index, "fixture must separate source line from pair index"
    rc, lines = _check(root, measured, release=True)
    assert rc == 0, lines
    want = f"{COUNT_SVG}:{number_line} says {COUNT_PLANT}"
    assert any(want in ln for ln in lines), (
        "the verified banner claim must be listed at the SOURCE line of its number node (%r): %r"
        % (want, lines))
    wrong = f"{COUNT_SVG}:{pair_index} says"
    assert not any(wrong in ln for ln in lines), (
        "the banner claim was listed at its pair index, not its source line: %r" % lines)


@pytest.mark.parametrize("release", [False, True])
def test_a_drifted_svg_site_is_reported_at_its_source_line(tmp_path, release):
    """The line the reviewer is sent to must exist: today `docs/banner.svg:19 says 1544` points at
    a pair index where the number sits on source line 56."""
    root, measured, number_line, pair_index = _count_fixture_tree_with_banner(tmp_path, filler=3)
    measured[COMBINED] = (COUNT_PLANT + 1, None)
    rc, lines = _check(root, measured, release=release)
    assert rc == 1, lines
    want = f"{COUNT_SVG}:{number_line} says {COUNT_PLANT}"
    assert any(want in ln for ln in lines), (
        "release=%r: the drifted banner claim must be reported at the source line of its number "
        "node (%r): %r" % (release, want, lines))
    assert not any(f"{COUNT_SVG}:{pair_index} says" in ln for ln in lines), (
        "release=%r: reported at the pair index instead of the source line: %r" % (release, lines))


# --- R5 CONTROL -------------------------------------------------------------------------------

def test_control_a_drifted_prose_site_is_still_reported_at_its_line(tmp_path):
    root, measured, _nl, _pi = _count_fixture_tree_with_banner(tmp_path)
    prose = _count_fixture_prose_lines()
    name, _text = prose[-1]
    measured[name] = (COUNT_PLANT + 1, None)
    for release in (False, True):
        rc, lines = _check(root, measured, release=release)
        assert rc == 1 and any(f"DOC.md:{len(prose)} says {COUNT_PLANT}" in ln for ln in lines), (
            release, lines)


def test_control_svg_pairing_still_reads_the_banner_claim(tmp_path):
    """The pairing itself is intact: a count node followed by its subject node is one claim."""
    svg, _nl, _pi = _count_fixture_svg(COUNT_PLANT, "tests", filler=3)
    pairs = mod._svg_lines(svg)
    sites = [c for p in pairs for c in mod._claims_repo_tests(p, COUNT_SVG)]
    assert sites == [COUNT_PLANT], (pairs, sites)


# =============================================================================================
# Additional boundary coverage.
#
# G3 (RED): an inventoried document that cannot be read — chmod 000, or deleted from the working
#     tree while still listed by `git ls-files` — is silently dropped from the sweep, so its claim
#     sites vanish and the run prints "every documented count matches". Both modes must exit 2
#     with a line containing UNMEASURED and the document's path.
# G4 (gate gap): the R5 SVG fixture had one node per line after one opening line, so "source
#     line" always equalled node index + 2. This fixture breaks that: blank lines, a comment and
#     two nodes on one line sit between the nodes.
# =============================================================================================
import os  # noqa: E402


def _count_fixture_git_tree(tmp_path):
    """A committed tree where EVERY check has a claim site in TWO documents (DOC.md and
    README.md carry the same prose plants; the SVG carries the banner-only combined claim), so
    losing README.md drops sites without zeroing any check. Returns (root, measured)."""
    svg, _nl, _pi = _count_fixture_svg(COUNT_PLANT, "tests", filler=1)
    root, measured = _count_fixture_tree(tmp_path, svg=svg)
    (root / "README.md").write_text((root / "DOC.md").read_text(encoding="utf-8"), encoding="utf-8")
    git = ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run(git + ["init", "-q"], check=True, capture_output=True)
    subprocess.run(git + ["add", "-A"], check=True, capture_output=True)
    subprocess.run(git + ["commit", "-q", "-m", "fixture"], check=True, capture_output=True)
    tracked = subprocess.run(git + ["ls-files"], check=True, capture_output=True, text=True).stdout.split()
    assert "README.md" in tracked and "DOC.md" in tracked, tracked
    for release in (False, True):
        rc, lines = _check(root, measured, release=release)
        assert rc == 0, "fixture baseline must be clean (release=%r): %r" % (release, lines)
    return root, measured


def _count_fixture_unreadable(path):
    path.chmod(0)
    if os.geteuid() == 0:
        pytest.skip("running as root: permission bits do not make the document unreadable")
    if os.access(path, os.R_OK):
        pytest.skip("chmod 000 did not make the document unreadable on this filesystem")


def _assert_unmeasured_naming(rc, lines, rel, release):
    assert rc == 2, ("release=%r: an inventoried document that could not be read must make the "
                     "run UNMEASURED (2), got %d: %r" % (release, rc, lines))
    assert any("UNMEASURED" in ln and rel in ln for ln in lines), (
        "release=%r: a line must say UNMEASURED and name %s: %r" % (release, rel, lines))


# --- G3 RED -----------------------------------------------------------------------------------

@pytest.mark.parametrize("release", [False, True])
def test_an_unreadable_inventoried_document_is_unmeasured(tmp_path, release):
    root, measured = _count_fixture_git_tree(tmp_path)
    readme = root / "README.md"
    _count_fixture_unreadable(readme)
    try:
        rc, lines = _check(root, measured, release=release)
    finally:
        readme.chmod(0o644)
    _assert_unmeasured_naming(rc, lines, "README.md", release)


@pytest.mark.parametrize("release", [False, True])
def test_a_tracked_document_missing_from_the_working_tree_is_unmeasured(tmp_path, release):
    root, measured = _count_fixture_git_tree(tmp_path)
    (root / "README.md").unlink()
    rc, lines = _check(root, measured, release=release)
    _assert_unmeasured_naming(rc, lines, "README.md", release)


# --- G3 CONTROL -------------------------------------------------------------------------------

def test_control_the_readable_fixture_is_clean_in_both_modes(tmp_path):
    root, measured = _count_fixture_git_tree(tmp_path)      # asserts rc 0 in both modes itself
    assert (root / "README.md").is_file() and os.access(root / "README.md", os.R_OK)


def test_control_a_drift_in_the_readable_document_is_still_reported(tmp_path):
    root, measured = _count_fixture_git_tree(tmp_path)
    prose = _count_fixture_prose_lines()
    name, _text = prose[0]
    measured[name] = (COUNT_PLANT + 1, None)
    for release in (False, True):
        rc, lines = _check(root, measured, release=release)
        assert rc == 1 and any(f"README.md:1 says {COUNT_PLANT}" in ln for ln in lines), (release, lines)


# --- G4 gate gap: SVG source line is the real line, not node index + 2 ----------------------

G4_SVG = (
    "<svg>\n"                                                  # 1
    "\n"                                                       # 2  blank
    "<!-- stats block: one count node, then its subject -->\n"  # 3  comment
    "<text>filler-a</text><text>filler-b</text>\n"             # 4  two nodes on one line
    "\n"                                                       # 5  blank
    f"<text>{COUNT_PLANT}</text>\n"                            # 6  the number node
    "<text>tests</text>\n"                                     # 7
    "</svg>\n"
)
G4_NUMBER_LINE = 6
G4_NODE_INDEX_PLUS_2 = 4      # the number node is the third <text> node (index 2)
G4_PAIR_INDEX = 3


def _assert_svg_site_line(lines, want_line, label):
    want = f"{COUNT_SVG}:{want_line} says {COUNT_PLANT}"
    assert any(want in ln for ln in lines), "%s: %r not listed: %r" % (label, want, lines)
    for wrong in (G4_NODE_INDEX_PLUS_2, G4_PAIR_INDEX):
        assert not any(f"{COUNT_SVG}:{wrong} says" in ln for ln in lines), (
            "%s: listed at %d (an offset, not the source line): %r" % (label, wrong, lines))


def test_control_fixture_separates_source_line_from_every_offset():
    assert G4_SVG.splitlines()[G4_NUMBER_LINE - 1] == f"<text>{COUNT_PLANT}</text>"
    assert G4_NUMBER_LINE not in (G4_NODE_INDEX_PLUS_2, G4_PAIR_INDEX)
    sites = [c for p in mod._svg_lines(G4_SVG) for c in mod._claims_repo_tests(p, COUNT_SVG)]
    assert sites == [COUNT_PLANT], sites


def test_release_listing_reports_the_svg_number_nodes_real_source_line(tmp_path):
    root, measured = _count_fixture_tree(tmp_path, svg=G4_SVG)
    rc, lines = _check(root, measured, release=True)
    assert rc == 0, lines
    _assert_svg_site_line(lines, G4_NUMBER_LINE, "release listing")


@pytest.mark.parametrize("release", [False, True])
def test_drift_line_reports_the_svg_number_nodes_real_source_line(tmp_path, release):
    root, measured = _count_fixture_tree(tmp_path, svg=G4_SVG)
    measured[COMBINED] = (COUNT_PLANT + 1, None)
    rc, lines = _check(root, measured, release=release)
    assert rc == 1, lines
    _assert_svg_site_line(lines, G4_NUMBER_LINE, "drift line (release=%r)" % release)


# Synthetic transport controls; real-pytest regressions below exercise the producer.
def _receipt_run(monkeypatch, *, count=2, deselected=0, failures=None,
                 finished=True, status=0, stdout='', stderr=''):
    def run(cmd, **kwargs):
        pathlib.Path(cmd[3]).write_text(json.dumps({
            'collected': count, 'deselected': deselected,
            'collection_finished': finished,
            'failures': [] if failures is None else failures}))
        return subprocess.CompletedProcess(cmd, status, stdout, stderr)
    monkeypatch.setattr(mod.subprocess, 'run', run)


# Literal newlines under pytest's opt-out option are physical stdout lines.
@pytest.mark.parametrize('noise', [
    '999 tests collected in 0.01s',
    '1/9 tests collected (8 deselected) in 0.01s',
    '1 error', 'ModuleNotFoundError',
])
def test_collection_ids_do_not_decide_outcome(tmp_path, noise):
    tests = tmp_path / 'tests'
    tests.mkdir()
    (tmp_path / 'pytest.ini').write_text(
        '[pytest]\ndisable_test_id_escaping_and_forfeit_all_rights_to_community_support = true\n')
    (tests / 'test_fixture.py').write_text(
        'import pytest\n@pytest.mark.parametrize("x", [1], ids=[' + repr('\n' + noise + '\n') + '])\n'
        'def test_value(x): pass\n')
    assert mod._collect(tmp_path, 'tests') == (1, None)


@pytest.mark.parametrize("status,output", [
    (2, "2 tests collected in 0.01s\n"),
    (0, "tests/test_fixture.py::test_value[999 tests collected]\n"),
])
def test_collection_requires_receipt_and_success(tmp_path, monkeypatch, status, output):
    (tmp_path / "tests").mkdir()
    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k:
                        subprocess.CompletedProcess(a, status, output, ""))
    count, note = mod._collect(tmp_path, "tests")
    assert count is None and note


def test_real_collection_ids_and_error(tmp_path):
    """Real pytest on a planted suite: ID noise is clean; a broken import is UNMEASURED."""
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_fixture.py").write_text(
        "import pytest\n"
        "@pytest.mark.parametrize('value', ['1 error', '2 errors', 'ImportError', "
        "'ModuleNotFoundError', '999 tests collected'])\n"
        "def test_value(value): pass\n", encoding="utf-8")
    assert mod._collect(tmp_path, "tests") == (5, None)
    (tests / "test_broken.py").write_text("raise RuntimeError('planted collection failure')\n",
                                        encoding="utf-8")
    count, note = mod._collect(tmp_path, "tests")
    assert count is None and "floor" in note, (count, note)
    # Route the real failed reading through check(), independent of live prose.
    (tmp_path / "DOC.md").write_text("5 hermetic unit gates\n", encoding="utf-8")
    measured = {name: (count, note) for name, *_ in mod.CHECKS}
    rc, lines = mod.check(tmp_path, measured=measured)
    assert rc == 2
    assert any("UNMEASURED  guard unit suite:" in line and "floor" in line for line in lines)


@pytest.mark.parametrize("colour", ["", "\x1b[32m", "\x1b[1;32m"])
def test_collection_summary_colour(tmp_path, monkeypatch, colour):
    (tmp_path / "tests").mkdir()
    reset = "\x1b[0m" if colour else ""
    output = colour + "2 tests collected" + reset + " in 0.01s\n"
    _receipt_run(monkeypatch, stdout=output)
    assert mod._collect(tmp_path, "tests") == (2, None)


# Planted suites and pytest's own formatter, independent of live counts.
@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_collector_teardown_output(tmp_path, stream):
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_ok.py").write_text("def test_ok(): pass\n")
    (tests / "conftest.py").write_text(
        "import sys\ndef pytest_unconfigure(config):\n"
        f"    print('TEARDOWN', file=sys.{stream})\n")
    assert mod._collect(tmp_path, "tests") == (1, None)


@pytest.mark.parametrize("seconds", [-0.94, 0, 59.99, 72.34, 400000])
@pytest.mark.parametrize("selected,total", [(2, 2), (3, 5), (0, 5), (0, 0)])
def test_collector_stock_summary(tmp_path, monkeypatch, seconds, selected, total):
    from _pytest.terminal import TerminalReporter, format_session_duration
    reporter = object.__new__(TerminalReporter)
    reporter._numcollected = total
    reporter._get_reports_to_display = lambda key: [None] * (total - selected) if key == 'deselected' else []
    parts, _ = reporter._build_collect_only_summary_stats_line()
    summary = ', '.join(text for text, _ in parts) + ' in ' + format_session_duration(seconds)
    (tmp_path / 'tests').mkdir()
    _receipt_run(monkeypatch, count=selected, deselected=total-selected,
                 status=0 if selected else 5,
                 stdout='BEFORE\n=== ' + summary + ' ===\nAFTER\n')
    count, note = mod._collect(tmp_path, 'tests')
    if selected != total:
        assert count is None and 'deselection' in note
    elif not total:
        assert count is None and 'no tests' in note and 'errors' not in note
    else:
        assert (count, note) == (total, None)


@pytest.mark.parametrize('options', ['-q', '-qqq', '-v', '--no-summary', '-k absent', '--bad-option'])
def test_collector_neutralizes_addopts(tmp_path, monkeypatch, options):
    tests = tmp_path / 'tests'
    tests.mkdir()
    (tests / 'test_ok.py').write_text('def test_ok(): pass\n')
    monkeypatch.setenv('PYTEST_ADDOPTS', options)
    assert mod._collect(tmp_path, 'tests') == (1, None)


@pytest.mark.parametrize('body,external', [
    ("print('ImportError: red herring')\nraise RuntimeError('broken')\n", False),
    ("print(\"E   ModuleNotFoundError: No module named 'fake_dep'\")\nraise RuntimeError('broken')\n", False),
    ("from local_package import absent_helper\n", False),
    ("import local_package.absent_module\n", False),
    ("import fixture_external_missing_package\n", True),
    ("raise ImportError('not a missing module')\n", False),
])
def test_collection_dependency_cause(tmp_path, body, external):
    (tmp_path / 'pyproject.toml').write_text(
        '[project]\ndependencies = ["fixture_external_missing_package"]\n')
    tests = tmp_path / 'tests'
    tests.mkdir()
    package = tmp_path / 'local_package'
    package.mkdir()
    (package / '__init__.py').write_text('')
    (tests / 'test_bad.py').write_text(body)
    count, note = mod._collect(tmp_path, 'tests')
    assert count is None
    assert (note == mod.SKIP_DEPS) == external, note
    if not external:
        # All other readings/claims are planted valid: this failure alone must
        # determine check()'s ordinary exit, not an unrelated missing claim.
        (tmp_path / 'DOC.md').write_text('\n'.join(plant(7) for *_, plant in mod.CHECKS))
        (tmp_path / 'DOC.svg').write_text('<text>7</text><text>tests</text>')
        readings = {name: (7, None) for name, *_ in mod.CHECKS}
        readings['guard unit suite'] = (count, note)
        rc, lines = mod.check(tmp_path, measured=readings)
        assert rc == 2 and any('UNMEASURED  guard unit suite' in line for line in lines)
        assert sum(line.startswith('   UNMEASURED') for line in lines) == 1, lines


def test_mixed_collection_errors_are_not_dependency_skip(tmp_path):
    tests = tmp_path / 'tests'
    tests.mkdir()
    (tests / 'test_dep.py').write_text('import fixture_external_missing_package\n')
    (tests / 'test_bug.py').write_text("raise RuntimeError('bug')\n")
    count, note = mod._collect(tmp_path, 'tests')
    assert count is None and note != mod.SKIP_DEPS


@pytest.mark.parametrize('stdout,stderr,want', [
    ('2 tests collected in 0.01s\n', '99 tests collected in 0.01s\n', (2, None)),
    ('', '99 tests collected in 0.01s\n', (2, None)),
])
def test_collector_ignores_both_output_streams(tmp_path, monkeypatch, stdout, stderr, want):
    (tmp_path / 'tests').mkdir()
    _receipt_run(monkeypatch, stdout=stdout, stderr=stderr)
    assert mod._collect(tmp_path, 'tests') == want


@pytest.mark.parametrize('layout', ['src', 'nested/project'])
def test_nested_repository_module_is_not_external(tmp_path, layout):
    tests = tmp_path / 'tests'
    tests.mkdir()
    package = tmp_path / layout / 'local_package'
    package.mkdir(parents=True)
    (package / '__init__.py').write_text('')
    (tests / 'test_bad.py').write_text(
        'import sys\nsys.path.insert(0, %r)\nimport local_package.missing\n' % str(package.parent))
    count, note = mod._collect(tmp_path, 'tests')
    assert count is None and note != mod.SKIP_DEPS


# Each fixture isolates a refusal condition from declared/absent controls.
def _dependency_project(tmp_path, bodies, declaration='[project]\ndependencies = ["r33_missing_dep"]\n'):
    tests = tmp_path / 'tests'
    tests.mkdir()
    if declaration is not None:
        (tmp_path / 'pyproject.toml').write_text(declaration)
    for name, body in bodies.items():
        (tests / name).write_text(body)
    return tests


def _dependency_check(root, reading, release=False):
    (root / 'DOC.md').write_text('\n'.join(plant(7) for *_, plant in mod.CHECKS))
    (root / 'DOC.svg').write_text('<text>7</text><text>tests</text>')
    readings = {name: (7, None) for name, *_ in mod.CHECKS}
    readings['guard unit suite'] = reading
    return mod.check(root, measured=readings, release=release)


@pytest.mark.parametrize('body,declaration', [
    ("raise ModuleNotFoundError('disabled', name='fictional_external_dep')\n", None),
    ("raise ModuleNotFoundError('disabled', name='json')\n", '[project]\ndependencies = ["json"]\n'),
    ('import helpers\n', None),
    ('import undeclared_absent_dep\n', None),
    ('import json.r33_absent_child\n', '[project]\ndependencies = ["json"]\n'),
])
def test_undeclared_or_findable_is_unmeasured(tmp_path, body, declaration):
    tests = _dependency_project(tmp_path, {'test_bad.py': body}, declaration)
    (tests / 'helpers_v2.py').write_text('X = 1\n')  # stale helpers import after rename
    reading = mod._collect(tmp_path, 'tests')
    assert reading[0] is None and reading[1] != mod.SKIP_DEPS, reading
    rc, lines = _dependency_check(tmp_path, reading)
    assert rc == 2 and sum('UNMEASURED  guard unit suite:' in line for line in lines) == 1, lines


@pytest.mark.parametrize('source', ['runtime', 'dev', 'requirements'])
def test_declared_missing_skip_names_and_release(tmp_path, source):
    declaration = {
        'runtime': '[project]\ndependencies = ["r33-missing-dep>=1"]\n',
        'dev': '[project.optional-dependencies]\ndev = ["r33-missing-dep>=1"]\n',
        'requirements': None,
    }[source]
    _dependency_project(tmp_path, {'test_bad.py': 'import r33_missing_dep\n'}, declaration)
    if source == 'requirements':
        (tmp_path / 'requirements.txt').write_text('# dependencies\nr33-missing-dep>=1 # comment\n')
    reading = mod._collect(tmp_path, 'tests')
    assert reading == (None, mod.SKIP_DEPS), reading
    for release, expected in [(False, 0), (True, 2)]:
        rc, lines = _dependency_check(tmp_path, reading, release)
        assert rc == expected, lines
        assert any('guard unit suite:' in line and 'missing: r33_missing_dep' in line for line in lines), lines


@pytest.mark.parametrize('other', ['import r33_undeclared_dep\n', "raise RuntimeError('broken')\n"])
def test_every_failed_report_must_qualify(tmp_path, other):
    _dependency_project(tmp_path, {'test_dep.py': 'import r33_missing_dep\n', 'test_other.py': other})
    reading = mod._collect(tmp_path, 'tests')
    assert reading[0] is None and reading[1] != mod.SKIP_DEPS, reading
    assert _dependency_check(tmp_path, reading)[0] == 2


def test_multiple_declared_missing_names(tmp_path):
    _dependency_project(tmp_path, {'test_a.py': 'import r33_missing_dep\n',
                               'test_b.py': 'import r33_other_dep\n'},
                     '[project]\ndependencies = ["r33_missing_dep", "r33_other_dep"]\n')
    reading = mod._collect(tmp_path, 'tests')
    assert reading == (None, mod.SKIP_DEPS)
    rc, lines = _dependency_check(tmp_path, reading)
    assert rc == 0 and any('missing: r33_missing_dep, r33_other_dep' in line for line in lines), lines


@pytest.mark.parametrize('layout', ['', 'src', 'nested/project'])
@pytest.mark.parametrize('kind', ['file', 'directory'])
def test_repository_ownership_refuses_declared_absence(tmp_path, layout, kind):
    # Keep source outside sys.path: a findable control would mask a broken ownership check.
    _dependency_project(tmp_path, {'test_bad.py': 'import r33_missing_dep\n'})
    assert mod._collect(tmp_path, 'tests') == (None, mod.SKIP_DEPS)
    parent = tmp_path / layout
    parent.mkdir(parents=True, exist_ok=True)
    if kind == 'file':
        (parent / 'r33_missing_dep.py').write_text('X = 1\n')
    else:
        (parent / 'r33_missing_dep').mkdir()
    # Direct synthetic receipt isolates ownership even for a flat importable source.
    assert not mod._external_missing_modules(
        tmp_path, [{'name': 'r33_missing_dep', 'absent': True}], {'r33_missing_dep'})
    if layout:
        reading = mod._collect(tmp_path, 'tests')
        assert reading[0] is None and reading[1] != mod.SKIP_DEPS, reading


@pytest.mark.parametrize('environment', ['.venv', 'venv', 'env', '.env', 'custom_venv', 'custom_conda'])
def test_environment_is_not_repository_source(tmp_path, environment):
    _dependency_project(tmp_path, {'test_bad.py': 'import r33_missing_dep\n'})
    base = tmp_path / environment
    package = base / 'lib/python3.14/site-packages/r33_missing_dep'
    package.mkdir(parents=True)
    (package / '__init__.py').write_text('')
    # Also place a package directly under marked environments to isolate root pruning.
    (base / 'r33_missing_dep').mkdir()
    if environment == 'custom_venv':
        (base / 'pyvenv.cfg').write_text('')
    if environment == 'custom_conda':
        (base / 'conda-meta').mkdir()
    assert mod._collect(tmp_path, 'tests') == (None, mod.SKIP_DEPS)


@pytest.mark.parametrize('output,want', [
    ('2 tests collected in 0.01s\n9 tests collected in 0.02s\n', 2),
    ('9 tests collected in 0.01s\n2 tests collected in 0.02s\n', 2),
    ('2 tests collected in 0.01s\n2 tests collected in 0.02s\n', 2),
])
def test_stdout_totals_cannot_contradict_receipt(tmp_path, monkeypatch, output, want):
    (tmp_path / 'tests').mkdir()
    _receipt_run(monkeypatch, stdout=output)
    count, note = mod._collect(tmp_path, 'tests')
    assert count == want
    assert note is None


@pytest.mark.parametrize('summary', [
    '2 tests collected', 'no tests collected', '2 tests collected, 1 error',
    '2 tests collected, 1 errors in 0.01s', '2 tests collected, 2 error in 0.01s',
    '2 tests collected, 0 errors in 0.01s', '1 tests collected in 0.01s',
    '2 test collected in 0.01s',
])
def test_nonstock_collection_summary_refused(tmp_path, monkeypatch, summary):
    (tmp_path / 'tests').mkdir()
    monkeypatch.setattr(mod.subprocess, 'run', lambda *a, **k:
                        subprocess.CompletedProcess(a, 0, summary + '\n', ''))
    assert mod._collect(tmp_path, 'tests') == (None, 'the collector supplied no valid collection receipt')


@pytest.mark.parametrize('hook_line', ['999 tests collected', '2 tests collected',
                                      '2 tests collected, 1 error'])
def test_real_collectionfinish_hook_is_not_summary(tmp_path, hook_line):
    _dependency_project(tmp_path, {'test_ok.py': 'def test_one(): pass\ndef test_two(): pass\n'}, None)
    (tmp_path / 'conftest.py').write_text(
        'def pytest_report_collectionfinish(config, start_path, items):\n'
        f'    return {hook_line!r}\n')
    assert mod._collect(tmp_path, 'tests') == (2, None)


@pytest.mark.parametrize('errors', [0, 1, 2, 11])
@pytest.mark.parametrize('total,selected', [(0, 0), (1, 1), (2, 2), (3, 1), (3, 0)])
def test_stock_collection_error_clause(tmp_path, monkeypatch, errors, total, selected):
    from _pytest.terminal import TerminalReporter, format_session_duration
    reporter = object.__new__(TerminalReporter)
    reporter._numcollected = total
    reporter._get_reports_to_display = lambda key: [None] * (
        errors if key == 'error' else total - selected if key == 'deselected' else 0)
    parts, _ = reporter._build_collect_only_summary_stats_line()
    summary = ', '.join(text for text, _ in parts) + ' in ' + format_session_duration(72.34)
    (tmp_path / 'tests').mkdir()
    _receipt_run(monkeypatch, count=selected, deselected=total-selected,
                 failures=[None] * errors, status=2 if errors else 0 if selected else 5,
                 stdout=summary)
    count, note = mod._collect(tmp_path, 'tests')
    if errors:
        assert count is None and 'floor' in note
    elif selected != total:
        assert count is None and 'deselection' in note
    elif not selected:
        assert count is None and 'no tests' in note
    else:
        assert (count, note) == (total, None)


# Stdout producers cannot alter a successful pytest session count.
@pytest.mark.parametrize('line', [
    '999 tests collected in 0.01s',
    '2 tests collected in 0.01s',
    '1/9 tests collected (8 deselected) in 0.01s',
])
def test_collectionfinish_output_cannot_decide_count(tmp_path, line):
    _dependency_project(tmp_path, {'test_ok.py': 'def test_one(): pass\ndef test_two(): pass\n'}, None)
    (tmp_path / 'conftest.py').write_text(
        'def pytest_report_collectionfinish(config, start_path, items):\n'
        f'    return {line!r}\n')
    assert mod._collect(tmp_path, 'tests') == (2, None)


@pytest.mark.parametrize('emitter', ['unconfigure', 'atexit'])
@pytest.mark.parametrize('line', [
    '999 tests collected in 0.01s',
    '1/9 tests collected (8 deselected) in 0.01s',
])
def test_after_summary_output_cannot_decide_count(tmp_path, emitter, line):
    _dependency_project(tmp_path, {'test_ok.py': 'def test_one(): pass\ndef test_two(): pass\n'}, None)
    source = (f'def pytest_unconfigure(config):\n    print({line!r})\n'
              if emitter == 'unconfigure' else
              f'import atexit\natexit.register(print, {line!r})\n')
    (tmp_path / 'conftest.py').write_text(source)
    assert mod._collect(tmp_path, 'tests') == (2, None)


@pytest.mark.parametrize('remove', [1, 2])
def test_real_deselection_is_unmeasured(tmp_path, remove):
    _dependency_project(tmp_path, {'test_ok.py': 'def test_one(): pass\ndef test_two(): pass\n'}, None)
    (tmp_path / 'conftest.py').write_text(
        'def pytest_collection_modifyitems(config, items):\n'
        f'    removed = items[:{remove}]\n    del items[:{remove}]\n'
        '    config.hook.pytest_deselected(items=removed)\n')
    count, note = mod._collect(tmp_path, 'tests')
    assert count is None and 'deselection' in note


@pytest.mark.parametrize('field,value', [
    ('collected', None), ('collected', -1), ('collected', True),
    ('deselected', -1), ('deselected', True), ('failures', None),
    ('collection_finished', 'yes'),
])
def test_malformed_receipt_never_uses_stdout(tmp_path, monkeypatch, field, value):
    (tmp_path / 'tests').mkdir()
    evidence = dict(collected=2, deselected=0, failures=[], collection_finished=True)
    evidence[field] = value
    def run(cmd, **kwargs):
        pathlib.Path(cmd[3]).write_text(json.dumps(evidence))
        return subprocess.CompletedProcess(cmd, 0, '2 tests collected in 0.01s\n', '')
    monkeypatch.setattr(mod.subprocess, 'run', run)
    assert mod._collect(tmp_path, 'tests')[0] is None


@pytest.mark.parametrize('options', [dict(status=2), dict(failures=[None]), dict(finished=False)])
def test_receipt_requires_success_without_errors(tmp_path, monkeypatch, options):
    (tmp_path / 'tests').mkdir()
    _receipt_run(monkeypatch, **options)
    assert mod._collect(tmp_path, 'tests')[0] is None


def _unguarded_imports(source):
    """Absolute import roots at any scope; only handled try bodies are optional."""
    names = set()
    def visit(node):
        if isinstance(node, ast.Import):
            names.update(alias.name.split('.')[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
            names.add(node.module.split('.')[0])
        # Imports in handlers, else and finally are required; a bare try/finally
        # does not make its body optional. This is syntax, not exception analysis.
        if isinstance(node, (ast.Try, ast.TryStar)) and node.handlers:
            for child in [*node.handlers, *node.orelse, *node.finalbody]:
                visit(child)
        else:
            for child in ast.iter_child_nodes(node):
                visit(child)
    visit(ast.parse(source))
    return names


def _tui_inventory_sources():
    tui = REPO / 'tui'
    return [path for path in tui.rglob('*.py') if not any(
        part in {'.venv', 'venv', '.env', 'env', '__pycache__', 'site-packages', 'dist-packages'}
        for part in path.relative_to(tui).parts)]


def _deepest_statement_depth(sources):
    # Same ast.stmt ancestor count as the independent mutation battery.
    deepest = 0
    for source in sources:
        pending = [(ast.parse(source), 0)]
        while pending:
            node, depth = pending.pop()
            if isinstance(node, ast.stmt):
                deepest = max(deepest, depth)
            pending.extend((child, depth + isinstance(node, ast.stmt))
                           for child in ast.iter_child_nodes(node))
    return deepest


def test_tui_unguarded_imports_are_declared():
    """Inventory Python sources, including tests, without importing the TUI."""
    tui = REPO / 'tui'
    sources = _tui_inventory_sources()
    local = {path.stem for path in tui.glob('*.py')} | {
        path.name for path in tui.iterdir() if path.is_dir() and (path / '__init__.py').is_file()}
    required = set().union(*(_unguarded_imports(path.read_bytes()) for path in sources))
    third_party = required - sys.stdlib_module_names - local
    declared = mod._declared_imports(REPO, 'tui/tests')
    assert third_party, 'inventory must contain third-party imports'
    assert not third_party - declared, sorted(third_party - declared)


# Derived from every statement in exactly the inventory's source files.
_IMPORT_DEPTH_PROBE_N = max(8, _deepest_statement_depth(
    path.read_bytes() for path in _tui_inventory_sources()))
_IMPORT_DEPTH_MARGIN = 8


def test_import_inventory_controls():
    """Probe both forms through the source-derived depth plus eight levels.

    This finite control grows with the scanned code; it does not claim to kill
    arbitrary stateful mutations at depths beyond the measured frontier.
    """
    source, expected = _compound_import_source()
    assert _unguarded_imports(source) == expected
    lines = []
    depth_expected = set()
    for depth in range(1, _IMPORT_DEPTH_PROBE_N + _IMPORT_DEPTH_MARGIN + 1):
        lines.append(' ' * (depth - 1) + 'if condition:')
        import_root = f'depth_import_{depth}'
        from_root = f'depth_from_{depth}'
        lines.extend([
            ' ' * depth + f'import {import_root}.sub',
            ' ' * depth + f'from {from_root}.sub import value',
        ])
        depth_expected.update((import_root, from_root))
    assert _unguarded_imports('\n'.join(lines) + '\n') == depth_expected
    # Fresh names prevent a rich-only allowlist from satisfying the class test.
    assert _unguarded_imports("import new_vendor.sub\nfrom second_vendor import x\n") == {
        'new_vendor', 'second_vendor'}
    assert _unguarded_imports("try:\n import optional_vendor\nexcept ImportError:\n pass\n") == set()
    assert _unguarded_imports("try:\n import required_vendor\nfinally:\n pass\n") == {'required_vendor'}
    assert _unguarded_imports("try:\n pass\nexcept Exception:\n import fallback_vendor\n"
                             "else:\n import else_vendor\nfinally:\n import final_vendor\n") == {
        'fallback_vendor', 'else_vendor', 'final_vendor'}
    assert _unguarded_imports("from . import local\ndef f():\n import deferred_vendor\n") == {'deferred_vendor'}
    assert _unguarded_imports("class InventoryProbe:\n import class_body_vendor.sub\n") == {
        'class_body_vendor'}
    # One required import crosses all four nested scopes. Skipping any method,
    # conditional (including TYPE_CHECKING), async function or with body loses it.
    assert _unguarded_imports(
        "class NestedProbe:\n"
        " def method(self):\n"
        "  if TYPE_CHECKING:\n"
        "   async def deferred():\n"
        "    with context():\n"
        "     import nested_vendor.sub\n") == {'nested_vendor'}


def _compound_import_source():
    """Exercise every ordered chain of three forms through every unguarded suite.

    Templates supply parseable syntax only. The AST's ASDL signatures, not this
    list, decide which statement-list fields must be covered. A new compound
    form/field without a template fails closed instead of losing coverage.
    At each of three levels, seed every statement-list slot and put every
    template into every unguarded slot before expanding the next level. Thus
    every ordered template/suite/template/suite/template chain occurs; handler
    and match-case wrapper nodes are included by the ASDL field walk as well.
    """
    import re

    templates = [
        "def f():\n pass",
        "async def f():\n pass",
        "class C:\n pass",
        "for item in items:\n pass\nelse:\n pass",
        "async for item in items:\n pass\nelse:\n pass",
        "while condition:\n pass\nelse:\n pass",
        "if condition:\n pass\nelse:\n pass",
        "with context():\n pass",
        "async with context():\n pass",
        "match value:\n case _:\n  pass",
        # Only a try without handlers has an unguarded body. Both variants
        # matter: handled try suites still require handler/else/finally imports.
        "try:\n pass\nfinally:\n pass",
        "try:\n pass\nexcept ImportError:\n pass\nelse:\n pass\nfinally:\n pass",
        "try:\n pass\nexcept* ImportError:\n pass\nelse:\n pass\nfinally:\n pass",
    ]
    roots = [ast.parse(source).body[0] for source in templates]
    signatures = {
        cls: cls.__doc__ or '' for cls in vars(ast).values()
        if isinstance(cls, type) and issubclass(cls, ast.AST)
        and cls not in (ast.stmt, ast.excepthandler)
        and (issubclass(cls, (ast.stmt, ast.excepthandler)) or cls is ast.match_case)
    }
    fields = {cls: re.findall(r'\bstmt\* (\w+)', signature)
              for cls, signature in signatures.items()}
    compound = {cls for cls, signature in signatures.items()
                if re.search(r'\b(?:stmt|excepthandler|match_case)\* ', signature)}
    seen_types = {type(node) for root in roots for node in ast.walk(root)}
    assert compound <= seen_types, ('missing compound templates', compound - seen_types)
    required_fields = {(cls, field) for cls, names in fields.items() for field in names}
    seen_fields = set()
    expected = set()
    serial = 0

    def seed(root):
        nonlocal serial
        slots = []
        for node in list(ast.walk(root)):
            for field in fields.get(type(node), ()):
                # A bare try/finally cannot have else; its handled sibling
                # supplies that field's required import instead.
                if isinstance(node, ast.Try) and not node.handlers and field == 'orelse':
                    continue
                seen_fields.add((type(node), field))
                serial += 1
                name = f'vendor_{serial}'
                from_name = f'from_vendor_{serial}'
                suite = getattr(node, field)
                # Distinct roots for both absolute forms at every slot. Relative
                # imports must never enter the inventory, even with a module.
                suite.extend([
                    ast.Import(names=[ast.alias(name=name)]),
                    ast.ImportFrom(module=from_name, names=[ast.alias(name='name')], level=0),
                    ast.ImportFrom(module=None, names=[ast.alias(name='local')], level=1),
                    ast.ImportFrom(module=f'relative_vendor_{serial}',
                                   names=[ast.alias(name='local')], level=1),
                ])
                guarded = (isinstance(node, (ast.Try, ast.TryStar))
                           and field == 'body' and node.handlers)
                if not guarded:
                    expected.update((name, from_name))
                    slots.append(suite)
        return slots

    # Expand only the current template's slots, then recurse into fresh children.
    # Guarded markers remain absent from the oracle at every level.
    def expand(root, levels):
        for suite in seed(root):
            if levels > 1:
                for template in templates:
                    child = ast.parse(template).body[0]
                    expand(child, levels - 1)
                    suite.append(child)

    for root in roots:
        expand(root, 3)
    assert seen_fields == required_fields, ('missing statement-list fields',
                                             required_fields - seen_fields)
    source = ast.unparse(ast.fix_missing_locations(ast.Module(body=roots, type_ignores=[])))
    return source, expected


def test_import_depth_tracks_statement_nesting():
    shallow = 'import shallow\n'
    deep = '\n'.join(' ' * d + 'if condition:' for d in range(24)) + '\n' + ' ' * 24 + 'import deep\n'
    assert _deepest_statement_depth([shallow]) == 0
    assert _deepest_statement_depth([shallow, deep]) == 24
    assert _IMPORT_DEPTH_PROBE_N >= _deepest_statement_depth(
        path.read_bytes() for path in _tui_inventory_sources())
