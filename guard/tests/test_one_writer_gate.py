"""Gate for guard/one_writer_gate.py — exactly one writer may proceed, and every path a
refusal names must be REAL.

Red cases held open here: two contenders race and EXACTLY ONE acquires (a dirty-tree
snapshot let both proceed against one clean tree, and both then wrote); a foreign dirty
file must refuse; the refusal must name only paths that are actually there, checked with
`os.path.exists` and `git ls-files -z --deleted` rather than by re-running the production
parser — the old version of this test re-parsed porcelain with the SAME blind " -> "
split, so it stayed green on the fabricated path it was supposed to catch; a non-repo must
be CANNOT CHECK, never a pass. Red demos: OW1 blinds the foreign-file detection, OW2 drops
O_EXCL so both contenders acquire, OW3 restores the blind split. Each must take this gate
red.

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
from guard.one_writer_gate import (  # noqa: E402
    _race, check, describe, held_by, open_transaction, release, selftest)

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


def _deleted_paths(d):
    """Paths git reports as deleted from the working tree. A DIFFERENT command with a
    trivial NUL split — deliberately not the module's porcelain parser, because a test
    that shares the parser under test cannot catch the parser's own bug."""
    out = subprocess.run(["git", "-C", d, "ls-files", "-z", "--deleted"],
                         capture_output=True, text=True, env=_ENV).stdout
    return {p for p in out.split("\0") if p}


def test_refusal_names_only_real_paths():
    """The plant is a file whose NAME contains the rename arrow. Blindly splitting every
    porcelain line on " -> " reported `bar.txt`, which does not exist. Verified here with
    os.path.exists, not by re-parsing porcelain the same way the module does."""
    with tempfile.TemporaryDirectory() as d:
        _repo(d)
        with open(os.path.join(d, "their_edit.txt"), "w", encoding="utf-8") as fh:
            fh.write("x\n")
        with open(os.path.join(d, "foo -> bar.txt"), "w", encoding="utf-8") as fh:
            fh.write("a filename containing the rename arrow\n")
        rc, foreign, _ = check(d, [])
        deleted = _deleted_paths(d)
        on_disk = {f: os.path.exists(os.path.join(d, f)) for f in foreign}
    assert rc == 1
    assert foreign, "refusal with an empty name list is unactionable"
    for f in foreign:
        assert on_disk[f] or f in deleted, (
            "the refusal named %r, which is neither on disk nor reported deleted — a "
            "fabricated path teaches the operator to ignore refusals (named: %r)"
            % (f, foreign))
    assert "foo -> bar.txt" in foreign, (
        "the file whose NAME contains the arrow must be named verbatim: %r" % foreign)
    assert "bar.txt" not in foreign, "a path was assembled out of a filename: %r" % foreign


def test_a_deleted_file_is_named_as_a_deletion_not_as_a_file():
    """A dirty-because-absent path is real in the ledger and absent on disk. The refusal
    must say so rather than pointing the operator at a file that is not there."""
    with tempfile.TemporaryDirectory() as d:
        _repo(d)
        os.remove(os.path.join(d, "mine.txt"))
        rc, foreign, _ = check(d, [])
        lines = describe(d, foreign)
        deleted = _deleted_paths(d)
    assert rc == 1 and "mine.txt" in foreign
    assert "mine.txt" in deleted
    assert any("mine.txt" in ln and "deleted" in ln for ln in lines), (
        "a deleted path was named as though it were a file on disk: %r" % lines)


def test_an_absolute_claim_is_this_jobs_own_file():
    """An absolute --claim never matched git's root-relative porcelain, so a job's own
    file was refused as foreign."""
    with tempfile.TemporaryDirectory() as d:
        _repo(d)
        with open(os.path.join(d, "mine.txt"), "a", encoding="utf-8") as fh:
            fh.write("my own pass\n")
        rc, foreign, _ = check(d, [os.path.join(d, "mine.txt")])
    assert rc == 0, ("an absolute claim must name the same file as the ledger's relative "
                     "path (foreign=%r)" % foreign)


def test_two_contenders_race_and_exactly_one_acquires():
    """The defect this holds shut: two jobs checking the same CLEAN tree both returned
    proceed, then both wrote. Proceeding now means winning an atomic create."""
    with tempfile.TemporaryDirectory() as d:
        _repo(d)
        winners = _race(d, n=8)
    assert len(winners) == 1, (
        "%d of 8 contenders acquired the one-writer lock (%r) — a gate that lets more "
        "than one writer through is a snapshot, not a gate" % (len(winners), winners))


def test_two_contending_processes_and_exactly_one_acquires():
    """The same race across real processes, through the shipped CLI — threads share one
    interpreter, and the lock has to hold across process boundaries to mean anything."""
    with tempfile.TemporaryDirectory() as d:
        _repo(d)
        cli = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))), "guard", "one_writer_gate.py")
        procs = [subprocess.Popen(
            [sys.executable, cli, "--repo", d, "--acquire", "--owner", "job-%d" % i],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True) for i in range(2)]
        codes = [p.wait() for p in procs]
        for p in procs:
            p.stdout.close()
    assert sorted(codes) == [0, 1], (
        "exactly one process must proceed and the other must be refused; got %r" % codes)


def test_the_second_job_is_refused_while_the_lock_is_held():
    with tempfile.TemporaryDirectory() as d:
        _repo(d)
        rc_first, _, acquired = open_transaction(d, "job-a", [])
        rc_second, lines, _ = open_transaction(d, "job-b", [])
        released, _ = release(d, "job-a")
        rc_third, _, _ = open_transaction(d, "job-c", [])
    assert rc_first == 0 and acquired
    assert rc_second == 1, "a second job must be refused while the lock is held"
    assert any("job-a" in ln for ln in lines), ("the refusal must name the holder: %r" % lines)
    assert released and rc_third == 0, "a released lock must let the next job in"


def test_another_owner_cannot_release_the_lock():
    with tempfile.TemporaryDirectory() as d:
        _repo(d)
        open_transaction(d, "job-a", [])
        ok, why = release(d, "job-b")
        still = held_by(d)
        release(d, "job-a")
    assert not ok and "job-a" in why
    assert still and still.get("owner") == "job-a", "the lock was released by a non-owner"


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
