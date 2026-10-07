"""Release tags: a tag that only names already-published history must not fail the gate.

The project's first release created refs/tags/v0.1.1 on the tip of main, and rule 1 then
failed on it, because the allow-list holds one branch name. A tag is exempt only when it
peels to a commit reachable from the publishing ref; every tag message on the way is held
to rule 3. Tags on unpublished history, on trees or blobs, and every other stray ref keep
failing. Written BEFORE the fix: the four release cases fail by assertion on the old gate.
"""
import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest

TRAILER = "Co-Authored-By: Claude <noreply@anthropic.com>"
IDENTITY = ["-c", "user.name=fixture", "-c", "user.email=fixture" + "@" + "example.invalid"]


def _gate():
    path = Path(__file__).resolve().parents[2] / "_tools" / "ref_gate.py"
    spec = importlib.util.spec_from_file_location("ref_gate", path)
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)
    return gate


def _repo(tmp_path):
    """main = A -> B; a sibling S off A and an orphan U exist only as commit ids."""
    repo = tmp_path / "tags"
    repo.mkdir()

    def git(*args):
        return subprocess.run(["git", *IDENTITY, *args], cwd=repo, check=True,
                              capture_output=True, text=True).stdout.strip()

    git("init", "-q", "-b", "main")
    git("commit", "-q", "--allow-empty", "-m", "A")
    a = git("rev-parse", "HEAD")
    git("commit", "-q", "--allow-empty", "-m", "B")
    b = git("rev-parse", "HEAD")
    tree = git("rev-parse", "HEAD^{tree}")
    s = git("commit-tree", tree, "-p", a, "-m", "S")
    u = git("commit-tree", tree, "-m", "U")
    blob = subprocess.run(["git", "hash-object", "-w", "--stdin"], cwd=repo, input="blob\n",
                          check=True, capture_output=True, text=True).stdout.strip()
    return repo, git, {"A": a, "B": b, "S": s, "U": u, "blob": blob}


def _annotated(git, name, target, message):
    git("tag", "-a", name, target, "-m", message)


RELEASE_CASES = {
    "lightweight-at-tip": lambda git, c: git("tag", "v0.1.1", c["B"]),
    "annotated-at-tip": lambda git, c: _annotated(git, "v0.1.1", c["B"], "v0.1.1"),
    "lightweight-at-ancestor": lambda git, c: git("tag", "v0.1.0", c["A"]),
    "nested-annotated-clean": lambda git, c: (
        _annotated(git, "inner", c["B"], "inner"),
        _annotated(git, "outer", "refs/tags/inner", "outer")),
}

STAY_RED = {
    "tag-on-unpublished-sibling": lambda git, c: git("tag", "pre-package-backup", c["S"]),
    "release-name-on-orphan": lambda git, c: git("tag", "v9.9.9", c["U"]),
    "annotated-orphan": lambda git, c: _annotated(git, "v9.9.8", c["U"], "orphan"),
    "trailer-in-tag-message": lambda git, c: _annotated(git, "v0.1.1", c["B"], "release\n\n" + TRAILER),
    "trailer-in-inner-tag": lambda git, c: (
        _annotated(git, "inner", c["B"], "inner\n\n" + TRAILER),
        _annotated(git, "outer", "refs/tags/inner", "outer"),
        git("tag", "-d", "inner")),
    "tag-of-a-blob": lambda git, c: git("tag", "blobtag", c["blob"]),
    "tag-of-a-tree": lambda git, c: git("tag", "treetag", "main^{tree}"),
    "second-branch-at-tip": lambda git, c: git("branch", "alias", c["B"]),
    "rewrite-leftover-at-tip": lambda git, c: git("update-ref", "refs/original/refs/heads/main", c["B"]),
}


@pytest.mark.parametrize("case", sorted(RELEASE_CASES))
def test_a_tag_on_published_history_passes(tmp_path, case):
    gate = _gate()
    repo, git, commits = _repo(tmp_path)
    RELEASE_CASES[case](git, commits)
    assert gate.check(str(repo), quiet=True) == 0


@pytest.mark.parametrize("case", sorted(STAY_RED))
def test_a_ref_outside_published_history_still_fails(tmp_path, case):
    gate = _gate()
    repo, git, commits = _repo(tmp_path)
    STAY_RED[case](git, commits)
    assert gate.check(str(repo), quiet=True) == 1


def test_the_tag_exemption_follows_a_configured_publish_ref(tmp_path):
    gate = _gate()
    repo, git, commits = _repo(tmp_path)
    git("branch", "release", commits["A"])
    git("config", "fleetops.publishRef", "refs/heads/release")
    git("update-ref", "-d", "refs/heads/main")
    git("tag", "v0.1.0", commits["A"])
    assert gate.check(str(repo), quiet=True) == 0
    git("tag", "v0.1.1", commits["B"])  # B is not reachable from release
    assert gate.check(str(repo), quiet=True) == 1


def test_a_shallow_clone_with_tags_refuses_instead_of_judging(tmp_path):
    repo, git, commits = _repo(tmp_path)
    git("tag", "v0.1.0", commits["A"])
    shallow = tmp_path / "shallow"
    subprocess.run(["git", "clone", "-q", "--depth", "1", "--no-single-branch",
                    repo.as_uri(), str(shallow)], check=True, capture_output=True)
    subprocess.run(["git", "fetch", "-q", "--depth", "1", "origin", "refs/tags/v0.1.0:refs/tags/v0.1.0"],
                   cwd=shallow, check=True, capture_output=True)
    path = Path(__file__).resolve().parents[2] / "_tools" / "ref_gate.py"
    result = subprocess.run([sys.executable, str(path), str(shallow)], capture_output=True, text=True)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "Traceback" not in result.stdout + result.stderr
