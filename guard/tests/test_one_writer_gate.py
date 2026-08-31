"""Gate for guard/one_writer_gate.py — the dirty-tree refusal must fire, and every path a
refusal names must be REAL.

Red cases held open here: a foreign dirty file must refuse; the refusal must name only
paths that exist in the actual version-control status (no fabrication — a refusal that
invents paths teaches its operator to ignore refusals); a non-repo must be CANNOT CHECK,
never a pass. Red demo: mutation OW1 in guard/mutation_harness.py blinds the foreign-file
detection and this gate must go red.

Runs two ways: under pytest and standalone —
`python3 guard/tests/test_one_writer_gate.py` — printing the all-pass marker the mutation
harness anchors on.
"""
import contextlib
import io
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from guard.one_writer_gate import check, selftest  # noqa: E402

MARKER = "ONE WRITER GATE HAS TEETH - ALL CHECKS PASSED"

_ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
            GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")


def _repo(d):
    def git(*a):
        return subprocess.run(["git", "-C", d] + list(a), capture_output=True,
                              text=True, env=_ENV)
    git("init", "-q")
    with open(os.path.join(d, "mine.txt"), "w", encoding="utf-8") as fh:
        fh.write("committed\n")
    git("add", "mine.txt")
    git("commit", "-qm", "base")
    return git


def test_foreign_dirty_file_refuses():
    with tempfile.TemporaryDirectory() as d:
        _repo(d)
        with open(os.path.join(d, "their_edit.txt"), "w", encoding="utf-8") as fh:
            fh.write("another job's in-flight work\n")
        rc, foreign, _ = check(d, ["mine.txt"])
    assert rc == 1, "a foreign dirty file must refuse the transaction (rc=%d)" % rc
    assert "their_edit.txt" in foreign, "the refusal must name the foreign file: %r" % foreign


def test_refusal_names_only_real_paths():
    with tempfile.TemporaryDirectory() as d:
        git = _repo(d)
        with open(os.path.join(d, "their_edit.txt"), "w", encoding="utf-8") as fh:
            fh.write("x\n")
        rc, foreign, _ = check(d, [])
        porcelain = git("status", "--porcelain").stdout
        actual = {ln[3:].split(" -> ", 1)[-1].strip('"') for ln in porcelain.splitlines()
                  if len(ln) > 3}
    assert rc == 1
    assert foreign, "refusal with an empty name list is unactionable"
    for f in foreign:
        assert f in actual, ("refusal named a path git does not report — fabricated "
                             "paths teach the operator to ignore refusals: %r" % f)


def test_own_claimed_dirty_file_proceeds():
    with tempfile.TemporaryDirectory() as d:
        _repo(d)
        with open(os.path.join(d, "mine.txt"), "a", encoding="utf-8") as fh:
            fh.write("my own pass\n")
        rc, foreign, _ = check(d, ["mine.txt"])
    assert rc == 0, "this job's own claimed dirty file must not refuse (foreign=%r)" % foreign


def test_clean_tree_proceeds():
    with tempfile.TemporaryDirectory() as d:
        _repo(d)
        rc, _, _ = check(d, [])
    assert rc == 0


def test_non_repo_is_cannot_check():
    with tempfile.TemporaryDirectory() as d:
        rc, _, _ = check(d, [])
    assert rc == 2, "no ledger must be loud (CANNOT CHECK), never a pass (rc=%d)" % rc


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
