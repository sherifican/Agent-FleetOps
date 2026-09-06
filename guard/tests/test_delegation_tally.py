"""Caller for guard/delegation_tally.py, which is a REPORTER and not a guard.

It has no verdict to go red on, so the property under test is the one thing it could get wrong in a
way that would matter: a commit made before the trailer convention existed must be reported as
UNTAGGED and never imputed to either bucket. Counting an untagged pre-hook commit as "authored
directly" would manufacture the very datum the tool exists to collect.

The fixture is a synthetic git repository, so the assertion does not depend on this repo's history.
"""
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TALLY = os.path.join(REPO, "guard", "delegation_tally.py")

PRE_HOOK_DATE = "2026-08-01T12:00:00"       # before the hook existed
POST_HOOK_DATE = "2026-08-05T12:00:00"      # after it


def _git(repo, *args, env=None):
    e = dict(os.environ)
    e.update({"GIT_AUTHOR_NAME": "fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
              "GIT_COMMITTER_NAME": "fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid"})
    e.update(env or {})
    return subprocess.run(["git", "-C", str(repo)] + list(args),
                          capture_output=True, text=True, env=e, check=True)


def _commit(repo, name, message, when):
    (repo / name).write_text(name + "\n")
    _git(repo, "add", name)
    _git(repo, "commit", "-q", "--no-verify", "-m", message,
         env={"GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when})


def _fixture(tmp_path):
    repo = tmp_path / "synthetic"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _commit(repo, "old.txt", "a commit from before the trailer convention", PRE_HOOK_DATE)
    _commit(repo, "tagged.txt",
            "a delegated commit\n\nDelegated-to: some-model via some-cli", POST_HOOK_DATE)
    _commit(repo, "untagged.txt", "a commit with no trailer at all", POST_HOOK_DATE)
    return repo


def _run(repo, since):
    return subprocess.run([sys.executable, TALLY, "--repo", str(repo), "--since", since],
                          capture_output=True, text=True)


def test_a_pre_hook_commit_is_never_imputed(tmp_path):
    repo = _fixture(tmp_path)
    # Sweep from before the hook so the pre-hook commit is inside the window and must still be
    # reported as untagged rather than folded into either bucket.
    r = _run(repo, "2026-07-01")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "UNTAGGED: 2" in r.stdout, r.stdout
    assert "DELEGATED 1/1" in r.stdout, r.stdout
    assert "AUTHORED-DIRECTLY 0/1" in r.stdout, r.stdout


def test_the_reporter_reads_the_trailer_it_was_given(tmp_path):
    repo = _fixture(tmp_path)
    r = _run(repo, "2026-08-04")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "some-model via some-cli" in r.stdout, r.stdout
