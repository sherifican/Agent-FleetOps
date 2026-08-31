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
