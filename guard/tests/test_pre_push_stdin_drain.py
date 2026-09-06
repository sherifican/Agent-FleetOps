"""The push protocol arrives on stdin; a helper that reads stdin steals it.

Raised by grok on 2026-09-05 in a cold review with no premise, and NOT covered by
test_pre_push_fail_open.py. Both hooks run `scan_gate.py --self-test` before the loop that
reads Git's ref list, and neither redirected that child's stdin. A scanner that reads stdin
(one taking a file list, or anything wrapping a pager or transport) consumes the ref list, the
loop sees nothing, and the hook concludes the push was empty -- exit 0, nothing scanned.

Measured before the fix: a real two-ref push reported
    "pre-push gate: CLEAN - 0 commits scanned: empty push input; no ref updates supplied."
The message is honest about scanning zero. The exit code still admits the push.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
HOOK = (HERE / os.environ.get("PRE_PUSH_HOOK", "../hooks/pre-push")).resolve()

# Rejects every tree, so exit 0 is only reachable if the scanner was never handed a tree.
SCANNER = '''import sys
if "--self-test" in sys.argv:
    if __import__("os").environ.get("DRAIN_STDIN") == "1":
        sys.stdin.read()
    sys.exit(0)
sys.exit(1)
'''


def _git(cwd, *args):
    return subprocess.run(("git",) + args, cwd=cwd, check=True,
                          capture_output=True, text=True)


@pytest.fixture()
def repo(tmp_path):
    work, remote = tmp_path / "gatework", tmp_path / "public.git"
    _git(tmp_path, "init", "-q", "--bare", str(remote))
    work.mkdir()
    _git(work, "init", "-q", "-b", "main")
    _git(work, "config", "user.name", "Fixture")
    _git(work, "config", "user.email", "fixture@example.invalid")
    _git(work, "config", "fleetops.approvedIdentity", "fixture@example.invalid")
    tools = work / "_tools"
    tools.mkdir()
    (tools / "scan_gate.py").write_text(SCANNER)
    (work / "seed.txt").write_text("seed\n")
    _git(work, "add", "-A")
    _git(work, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "base")
    base = _git(work, "rev-parse", "HEAD").stdout.strip()
    _git(work, "push", "-q", str(remote), "HEAD:refs/heads/main")
    (work / "added.txt").write_text("added\n")
    _git(work, "add", "-A")
    _git(work, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "next")
    new = _git(work, "rev-parse", "HEAD").stdout.strip()
    return work, remote, base, new


def _run(repo, drain):
    work, remote, base, new = repo
    env = dict(os.environ, DRAIN_STDIN="1" if drain else "0")
    payload = (f"refs/heads/main {new} refs/heads/main {base}\n"
               f"refs/heads/main {new} refs/heads/next {'0' * 40}\n")
    return subprocess.run([str(HOOK), "public", str(remote)], cwd=work, input=payload,
                          capture_output=True, text=True, env=env)


def test_a_stdin_reading_self_test_cannot_turn_a_real_push_into_an_empty_one(repo):
    done = _run(repo, drain=True)
    assert done.returncode != 0, (
        "the self-test drained Git's ref list and the hook admitted an unscanned push:\n"
        + done.stdout + done.stderr)
    assert "empty push input" not in done.stdout, (
        "a two-ref push was described as empty: " + done.stdout)
    assert "scanner rejected commit" in done.stdout, (
        "expected the reject-all scanner to evaluate a tree: " + done.stdout + done.stderr)


def test_control_the_same_push_is_still_evaluated_when_nothing_drains_stdin(repo):
    """Without this the test above passes on any hook that blocks unconditionally."""
    done = _run(repo, drain=False)
    assert done.returncode != 0, "reject-all scanner must block: " + done.stdout
    assert "empty push input" not in done.stdout
    assert "BLOCKED" in done.stdout, (
        "expected a scanner//content refusal, got: " + done.stdout)
