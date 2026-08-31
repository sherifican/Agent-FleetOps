"""Gate for guard/org_lint.py — the root linter must go red on a stray root file.

Exception classes stay narrow only while something can fail them: an anchor passes, a
README-named entry point passes, everything else at a root is RED. Red demo: mutation OL1
in guard/mutation_harness.py waives the README-mention requirement and this gate must go
red.

Runs two ways: under pytest and standalone —
`python3 guard/tests/test_org_lint.py` — printing the all-pass marker the mutation
harness anchors on.
"""
import contextlib
import io
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from guard.org_lint import lint, selftest  # noqa: E402

MARKER = "ORG LINT HAS TEETH - ALL CHECKS PASSED"


def _tree(d, readme="Run `entry.py` to start.\n"):
    with open(os.path.join(d, "README.md"), "w", encoding="utf-8") as fh:
        fh.write(readme)
    with open(os.path.join(d, "LICENSE"), "w", encoding="utf-8") as fh:
        fh.write("MIT\n")
    os.mkdir(os.path.join(d, "src"))


def test_stray_root_file_goes_red_and_is_named():
    with tempfile.TemporaryDirectory() as d:
        _tree(d)
        with open(os.path.join(d, "scratch_dump.log"), "w", encoding="utf-8") as fh:
            fh.write("x\n")
        rc, strays, _ = lint(d)
    assert rc == 1, "a stray root file passed the root linter"
    assert any("scratch_dump.log" in s for s in strays), strays


def test_readme_named_entry_point_is_allowed():
    with tempfile.TemporaryDirectory() as d:
        _tree(d)
        with open(os.path.join(d, "entry.py"), "w", encoding="utf-8") as fh:
            fh.write("print('hi')\n")
        rc, strays, _ = lint(d)
    assert rc == 0, "a README-named entry point is exception class 2: %r" % strays


def test_unnamed_root_script_is_a_stray():
    with tempfile.TemporaryDirectory() as d:
        _tree(d)
        with open(os.path.join(d, "orphan_tool.py"), "w", encoding="utf-8") as fh:
            fh.write("print('hi')\n")
        rc, strays, _ = lint(d)
    assert rc == 1, ("a root script the README never mentions must be a stray — "
                     "discoverability is the licence")
    assert any("orphan_tool.py" in s for s in strays), strays


def test_anchors_and_directories_are_allowed():
    with tempfile.TemporaryDirectory() as d:
        _tree(d)
        with open(os.path.join(d, ".gitignore"), "w", encoding="utf-8") as fh:
            fh.write("*.pyc\n")
        rc, strays, _ = lint(d)
    assert rc == 0, repr(strays)


def test_ecosystem_anchors_are_not_this_repositorys_root_listing():
    """D2 red plant. The published class-1 list is the SKILL's: pyproject.toml,
    package.json, LICENSE, README.md, VCS dotfiles. Shipping one repository's own root
    listing as the exception set makes every adopter's build manifest a stray."""
    with tempfile.TemporaryDirectory() as d:
        _tree(d)
        for name in ("pyproject.toml", "package.json", ".gitattributes"):
            with open(os.path.join(d, name), "w", encoding="utf-8") as fh:
                fh.write("{}\n")
        rc, strays, _ = lint(d)
    assert rc == 0, ("a build manifest a tool addresses by fixed root path is class 1, "
                     "not a stray: %r" % strays)


def test_a_foreign_repos_contract_file_is_not_silently_anchored():
    """D2 red plant, the other direction. A filename that is THIS repository's own
    contract must not be a free pass in a tree that never declared it."""
    with tempfile.TemporaryDirectory() as d:
        _tree(d)
        with open(os.path.join(d, "STAGING_README.md"), "w", encoding="utf-8") as fh:
            fh.write("staging notes\n")
        rc, strays, _ = lint(d)
    assert rc == 1, ("an undeclared, README-unmentioned root file passed because another "
                     "repository's filename was hardcoded into the anchor set")
    assert any("STAGING_README.md" in s for s in strays), strays


def test_declared_contract_anchor_passes_and_must_be_checked():
    """D2 green path: the extension point. A repository declares its own contract
    filenames in guard/org_anchors.txt, with a reason, and each declaration is CHECKED —
    a declaration naming a file that is not there is itself a finding."""
    with tempfile.TemporaryDirectory() as d:
        _tree(d)
        with open(os.path.join(d, "STAGING_README.md"), "w", encoding="utf-8") as fh:
            fh.write("staging notes\n")
        os.mkdir(os.path.join(d, "guard"))
        decl = os.path.join(d, "guard", "org_anchors.txt")
        with open(decl, "w", encoding="utf-8") as fh:
            fh.write("STAGING_README.md: the staging contract other tooling reads by path\n")
        rc, strays, _ = lint(d)
        assert rc == 0, "a declared, existing contract anchor is class 1: %r" % strays
        with open(decl, "w", encoding="utf-8") as fh:
            fh.write("NOT_THERE.md: a declaration for a file nobody ships\n")
        rc_stale, strays_stale, _ = lint(d)
    assert rc_stale == 1, "a stale anchor declaration must be a finding, not a free pass"
    assert any("NOT_THERE.md" in s for s in strays_stale), strays_stale


def test_a_prohibition_in_the_readme_is_not_a_licence():
    """D3 red plant. `never commit scratch_dump.log` LICENSED scratch_dump.log: the raw
    substring test reads any mention as an endorsement, so the README inverts into an
    allow-list."""
    with tempfile.TemporaryDirectory() as d:
        _tree(d, readme="Run `entry.py` to start.\nnever commit scratch_dump.log\n")
        with open(os.path.join(d, "scratch_dump.log"), "w", encoding="utf-8") as fh:
            fh.write("x\n")
        rc, strays, _ = lint(d)
    assert rc == 1, "a README PROHIBITION was read as a licence to keep the file at the root"
    assert any("scratch_dump.log" in s for s in strays), strays


def test_a_suffix_named_stray_is_not_licensed_by_a_longer_name():
    """D3 red plant, the one named in the defect list: a stray `leg_contract.py` beside a
    README that only names `check_leg_contract.py`."""
    with tempfile.TemporaryDirectory() as d:
        _tree(d, readme="Run `check_leg_contract.py` to verify the contract.\n")
        with open(os.path.join(d, "check_leg_contract.py"), "w", encoding="utf-8") as fh:
            fh.write("print('real')\n")
        with open(os.path.join(d, "leg_contract.py"), "w", encoding="utf-8") as fh:
            fh.write("print('stray')\n")
        rc, strays, _ = lint(d)
    assert any("leg_contract.py" in s and "check_leg_contract.py" not in s for s in strays), (
        "a substring test licensed a stray whose name is a SUFFIX of a README-named "
        "script; every suffix-named file at the root passes for free: %r" % strays)
    assert rc == 1


def test_the_readme_named_script_itself_still_passes():
    """Breadth control for the two plants above: tightening the match must not start
    flagging the entry points the README really does name."""
    with tempfile.TemporaryDirectory() as d:
        _tree(d, readme="Run `check_leg_contract.py` to verify the contract.\n")
        with open(os.path.join(d, "check_leg_contract.py"), "w", encoding="utf-8") as fh:
            fh.write("print('real')\n")
        rc, strays, _ = lint(d)
    assert rc == 0, "the README names it in a code span; it is class 2: %r" % strays


def test_missing_readme_is_cannot_check():
    with tempfile.TemporaryDirectory() as d:
        rc, _, _ = lint(d)
    assert rc == 2, "no README means the entry-point class cannot be established — loud, not green"


def test_selftest_is_green():
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = selftest()
    assert rc == 0, "selftest red:\n" + buf.getvalue()


ALL = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    failures = 0
    for fn in ALL:
        try:
            fn()
            print("  ok    %s" % fn.__name__)
        except AssertionError as exc:
            failures += 1
            print("  FAIL  %s: %s" % (fn.__name__, exc))
    if failures:
        print("RESULT: %d check(s) RED" % failures)
        sys.exit(1)
    print(MARKER)
