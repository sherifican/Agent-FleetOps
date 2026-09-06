"""The commit MESSAGE is a publication surface, and `git archive` does not carry it.

Raised by grok on 2026-09-05 (its A5) in a cold review, then reproduced against the hook:

    clean commit        -> exit 0    control: the gate is not always red
    secret in the TREE  -> exit 1    control: the scanner does fire
    secret in the MESSAGE -> exit 0  <- admitted

The refusal text was never dishonest -- it says "tree(s)" -- but a secret in a commit message
still reached the destination. The same run admitted `Co-authored-by: <unapproved address>`,
because the identity check reads only %ae and %ce.

The marker is assembled at runtime. A first version of this test failed for the wrong reason:
the scanner double carried the literal marker in its own source, that file was committed into
the fixture's tree, and the scanner detected itself -- so BOTH arms went red and the gap looked
closed. The controls below exist to catch exactly that.
"""
import os
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
HOOK = (HERE / os.environ.get("PRE_PUSH_HOOK", "../hooks/pre-push")).resolve()
APPROVED = "fixture@example.invalid"
OUTSIDER = "outsider@example.invalid"
MARKER = "SEC" + "RET_PAY" + "LOAD"

SCANNER = (
    "import sys, pathlib\n"
    "if '--self-test' in sys.argv: sys.exit(0)\n"
    "M = 'SEC' + 'RET_PAY' + 'LOAD'\n"
    "root = pathlib.Path(sys.argv[-1])\n"
    "hit = any(M in p.read_text(errors='replace')\n"
    "          for p in root.rglob('*') if p.is_file() and not p.is_symlink())\n"
    "sys.exit(1 if hit else 0)\n"
)


class Fixture:
    def __init__(self, tmp_path):
        self.tmp = tmp_path
        self.work = tmp_path / "gatework"
        self.remote = tmp_path / "publication.git"
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        self.env.update(HOME=str(tmp_path), GIT_CONFIG_NOSYSTEM="1",
                        GIT_CONFIG_GLOBAL=os.devnull, LC_ALL="C")
        self.work.mkdir()
        subprocess.run(["git", "init", "-q", "--bare", str(self.remote)],
                       env=self.env, check=True, capture_output=True)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "fixture")
        self.git("config", "user.email", APPROVED)
        self.git("config", "fleetops.approvedIdentity", APPROVED)
        tools = self.work / "_tools"
        tools.mkdir()
        (tools / "scan_gate.py").write_text(SCANNER)
        (self.work / "ordinary.txt").write_text("nothing notable\n")
        self.base = self.commit("base")
        self.git("push", "-q", str(self.remote), "HEAD:refs/heads/main")

    def git(self, *args, check=True):
        return subprocess.run(("git",) + args, cwd=self.work, env=self.env,
                              check=check, capture_output=True, text=True)

    def commit(self, message, path=None, body="ordinary\n"):
        if path:
            (self.work / path).write_text(body)
        self.git("add", "-A")
        self.git("-c", "commit.gpgsign=false", "commit", "-q", "--allow-empty", "-m", message)
        return self.git("rev-parse", "HEAD").stdout.strip()

    def push(self, tip):
        line = f"refs/heads/main {tip} refs/heads/main {self.base}\n"
        return subprocess.run(["bash", str(HOOK), "publication", str(self.remote)],
                              cwd=self.work, env=self.env, input=line,
                              capture_output=True, text=True)


@pytest.fixture()
def repo(tmp_path):
    return Fixture(tmp_path)


def test_control_a_clean_commit_is_accepted(repo):
    """Without this, a hook that refused everything would satisfy every arm below."""
    done = repo.push(repo.commit("routine change", "added.txt"))
    assert done.returncode == 0, "an ordinary push must still pass: " + done.stdout + done.stderr


def test_control_a_secret_in_the_tree_is_refused(repo):
    """Proves the scanner is wired and fires -- the arms below mean nothing otherwise."""
    done = repo.push(repo.commit("routine change", "leak.txt", "key = " + MARKER + "\n"))
    assert done.returncode != 0, "a secret in the tree must be refused: " + done.stdout


def test_a_secret_in_the_commit_message_is_refused(repo):
    tip = repo.commit("routine change\n\nkey = " + MARKER + "\n", "added.txt")
    done = repo.push(tip)
    assert done.returncode != 0, (
        "a secret in the commit message reached the destination unscanned:\n"
        + done.stdout + done.stderr)


def test_an_unapproved_co_author_trailer_is_refused(repo):
    tip = repo.commit(f"routine change\n\nCo-authored-by: Someone <{OUTSIDER}>", "added.txt")
    done = repo.push(tip)
    assert done.returncode != 0, (
        "an unapproved address in a Co-authored-by trailer was admitted:\n" + done.stdout)


def test_a_non_identity_trailer_is_not_treated_as_an_identity(repo):
    """Anti-overcorrection. This repo's own convention requires `Delegated-to: <name>`,
    which carries no address; refusing it would block every commit made here."""
    tip = repo.commit("routine change\n\nDelegated-to: astra+grok", "added.txt")
    done = repo.push(tip)
    assert done.returncode == 0, (
        "a non-identity trailer must not be read as an identity: " + done.stdout + done.stderr)


def test_an_approved_co_author_trailer_is_accepted(repo):
    """Anti-overcorrection: the check must discriminate, not blanket-refuse trailers."""
    tip = repo.commit(f"routine change\n\nCo-authored-by: Fixture <{APPROVED}>", "added.txt")
    done = repo.push(tip)
    assert done.returncode == 0, (
        "an approved co-author must be accepted: " + done.stdout + done.stderr)


@pytest.mark.parametrize("coauthor", [False, True])
def test_signed_off_by_identity_is_checked(repo, coauthor):
    prefix = (f"Co-authored-by: Fixture <{APPROVED}>\n" if coauthor else "")
    good = repo.commit(
        "routine change\n\n" + prefix + f"Signed-off-by: Fixture <{APPROVED}>"
    )
    accepted = repo.push(good)
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    bad = repo.commit(
        "routine change\n\n" + prefix + f"Signed-off-by: Someone <{OUTSIDER}>"
    )
    refused = repo.push(bad)
    assert refused.returncode != 0, refused.stdout + refused.stderr
    assert "trailer identity" in refused.stdout
    assert OUTSIDER not in refused.stdout
