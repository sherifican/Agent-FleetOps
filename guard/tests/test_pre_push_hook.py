"""Gate for guard/hooks/pre-push — five arms, each one a push the hook must judge.

The hook it replaces scanned the WORKTREE, so a secret that existed only in a commit being pushed
was outside the instrument; and it enumerated a new ref's range with a form that cannot express
"every commit this push would add". Arms 1 and 5 are those two holes.

Planted values are built by concatenation at runtime, so this file's own bytes stay clean under the
repo's scan gate — a test that plants a literal secret publishes one.
"""
import os
import shutil
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HOOK = os.path.join(REPO, "guard", "hooks", "pre-push")
SCAN_GATE = os.path.join(REPO, "_tools", "scan_gate.py")

ZERO = "0" * 40
APPROVED = "fixture@example.invalid"
OUTSIDER = "someone-else@example.invalid"
IDENTITY_TERM = "fixtureperson"
# A key-shaped string the scan gate's aws-key pattern matches, assembled here so the literal never
# appears in tracked bytes.
PLANTED_SECRET = "AK" + "IA" + "ABCDEFGHIJKLMNOP"


def _git(repo, *args, env=None, check=True):
    e = dict(os.environ)
    e.update({"GIT_AUTHOR_NAME": "fixture", "GIT_AUTHOR_EMAIL": APPROVED,
              "GIT_COMMITTER_NAME": "fixture", "GIT_COMMITTER_EMAIL": APPROVED})
    e.update(env or {})
    return subprocess.run(["git", "-C", str(repo)] + list(args),
                          capture_output=True, text=True, env=e, check=check)


def _fixture(tmp_path, approved=True):
    """A throwaway repo carrying the scanner, its inputs, and the hook."""
    repo = tmp_path / "clone"
    (repo / "_tools").mkdir(parents=True)
    shutil.copy(SCAN_GATE, repo / "_tools" / "scan_gate.py")
    (repo / "_tools" / "identity_terms.txt").write_text(IDENTITY_TERM + "\n")
    if approved:
        (repo / "_tools" / "approved_identities.txt").write_text(APPROVED + "\n")
    (repo / "docs").mkdir()
    (repo / "docs" / "readme.md").write_text("nothing private here\n")
    # Mirror the real repo's .gitignore: the identity list is a private INPUT beside the scanner,
    # never a published byte. Tracked, it plants the identity term in every archived tree.
    (repo / ".gitignore").write_text("_tools/identity_terms.txt\n")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--no-verify", "-m", "base")
    return repo


def _commit(repo, rel, body, author=APPROVED):
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    _git(repo, "add", rel)
    _git(repo, "commit", "-q", "--no-verify", "-m", "add " + rel,
         env={"GIT_AUTHOR_EMAIL": author, "GIT_COMMITTER_EMAIL": author})
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


def _destination(repo):
    """A real bare repo to stand in for the push destination.

    git always calls pre-push with two arguments -- `githooks(5)`: "The hook is called with two
    parameters which provide the name and location of the destination remote". Omitting them here
    made the fixture unlike any real push, and hid that a new-ref range must be computed against
    the DESTINATION rather than against every remote this clone happens to track.
    """
    dest = repo.parent / "destination.git"
    if not dest.exists():
        subprocess.run(["git", "init", "-q", "--bare", str(dest)], check=True,
                       capture_output=True, text=True)
    return dest


def _run_hook(repo, localsha, remotesha, env=None):
    """Drive the hook the way git does: name + URL as argv, the push line on stdin."""
    e = dict(os.environ)
    e.update(env or {})
    line = f"refs/heads/main {localsha} refs/heads/main {remotesha}\n"
    return subprocess.run(["bash", HOOK, "destination", str(_destination(repo))],
                          cwd=str(repo), input=line,
                          capture_output=True, text=True, env=e)


def test_arm1_a_secret_only_in_a_pushed_commit_is_rejected(tmp_path):
    """The worktree is CLEAN; the secret lives only in a commit inside the range."""
    repo = _fixture(tmp_path)
    base = _git(repo, "rev-parse", "HEAD").stdout.strip()
    leak = _commit(repo, "docs/leak.md", "key = " + PLANTED_SECRET + "\n")
    _git(repo, "rm", "-q", "docs/leak.md")
    _git(repo, "commit", "-q", "--no-verify", "-m", "remove it again")
    head = _git(repo, "rev-parse", "HEAD").stdout.strip()
    assert not (repo / "docs" / "leak.md").exists(), "the worktree must be clean for this arm"

    r = _run_hook(repo, head, base)
    assert r.returncode != 0, f"a secret in a pushed commit must be rejected\n{r.stdout}{r.stderr}"
    assert leak in r.stdout, (
        "the refusal must name the commit that actually carries the secret -- not HEAD, whose "
        "tree is clean: " + r.stdout)


def test_arm2_a_clean_incremental_push_is_accepted(tmp_path):
    repo = _fixture(tmp_path)
    base = _git(repo, "rev-parse", "HEAD").stdout.strip()
    head = _commit(repo, "docs/ordinary.md", "an ordinary line\n")
    r = _run_hook(repo, head, base)
    assert r.returncode == 0, f"a clean push must be accepted\n{r.stdout}{r.stderr}"
    assert "CLEAN" in r.stdout, r.stdout


def test_arm3_a_non_approved_author_is_rejected(tmp_path):
    repo = _fixture(tmp_path)
    base = _git(repo, "rev-parse", "HEAD").stdout.strip()
    head = _commit(repo, "docs/other.md", "an ordinary line\n", author=OUTSIDER)
    r = _run_hook(repo, head, base)
    assert r.returncode != 0, f"an unapproved identity must be rejected\n{r.stdout}{r.stderr}"
    assert head in r.stdout, ("the refusal must name the commit carrying the identity: "
                              + r.stdout)
    assert "identit" in r.stdout.lower(), ("the refusal must say it is about identity: "
                                           + r.stdout)


def test_arm4_a_missing_identity_input_fails_closed_and_names_the_input(tmp_path):
    repo = _fixture(tmp_path, approved=False)
    base = _git(repo, "rev-parse", "HEAD").stdout.strip()
    head = _commit(repo, "docs/ordinary.md", "an ordinary line\n")
    r = _run_hook(repo, head, base, env={"FLEETOPS_APPROVED_IDENTITIES": "",
                                         "HOME": str(tmp_path / "nohome")})
    assert r.returncode != 0, f"an absent identity list must fail closed\n{r.stdout}{r.stderr}"
    assert "MISSING INPUT" in r.stdout, r.stdout
    assert "approved_identities" in r.stdout or "approvedIdentity" in r.stdout, r.stdout


def test_arm5_a_new_ref_has_its_range_enumerated(tmp_path):
    """The boundary a two-dot range cannot express: an all-zero remote sha."""
    repo = _fixture(tmp_path)
    head = _commit(repo, "docs/leak2.md", "token = " + PLANTED_SECRET + "\n")
    r = _run_hook(repo, head, ZERO)
    assert r.returncode != 0, (
        "a new ref must have every commit it would add enumerated and scanned\n"
        f"{r.stdout}{r.stderr}")
    assert head in r.stdout, ("the refusal must name the offending commit, not just refuse: "
                              + r.stdout)


@pytest.mark.parametrize("payload", [IDENTITY_TERM, PLANTED_SECRET])
def test_real_scanner_rejects_private_message_surface(tmp_path, payload):
    repo = _fixture(tmp_path)
    base = _git(repo, "rev-parse", "HEAD").stdout.strip()
    _git(repo, "-c", "commit.gpgSign=false", "commit", "--allow-empty",
         "--no-verify", "-qm", "ordinary message")
    clean = _git(repo, "rev-parse", "HEAD").stdout.strip()
    accepted = _run_hook(repo, clean, base)
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    _git(repo, "-c", "commit.gpgSign=false", "commit", "--allow-empty",
         "--no-verify", "-qm", "ordinary subject\n\nprivate value: " + payload)
    dirty = _git(repo, "rev-parse", "HEAD").stdout.strip()
    assert (_git(repo, "rev-parse", clean + "^{tree}").stdout ==
            _git(repo, "rev-parse", dirty + "^{tree}").stdout)
    refused = _run_hook(repo, dirty, clean)
    assert refused.returncode != 0, refused.stdout + refused.stderr
    assert "scanner rejected commit " + dirty + "'s message" in refused.stdout
    assert payload not in refused.stdout + refused.stderr
