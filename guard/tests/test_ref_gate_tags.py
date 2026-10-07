"""Release tags: a tag that only names already-published history must not fail the gate.

The project's first release created refs/tags/v0.1.1 on the tip of main, and rule 1 then
failed on it, because the allow-list holds one branch name. A tag is exempt only when it
peels to a commit reachable from the publishing ref; every tag message on the way is held
to rule 3. Tags on unpublished history, on trees or blobs, and every other stray ref keep
failing. Written BEFORE the fix: the four release cases fail by assertion on the old gate.
"""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys

import pytest

TRAILER = "Co-Authored-By: Claude <noreply@anthropic.com>"
IDENTITY = ["-c", "user.name=fixture", "-c", "user.email=fixture" + "@" + "example.invalid"]


@pytest.fixture(autouse=True)
def _hermetic_git(monkeypatch):
    """A global fleetops.publishRef, tag signing or advice setting must not change a fixture."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")


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
    "nested-clean-inner-ref-deleted": lambda git, c: (
        _annotated(git, "inner", c["B"], "inner"),
        _annotated(git, "outer", "refs/tags/inner", "outer"),
        git("tag", "-d", "inner")),
    "multiline-clean-message": lambda git, c: _annotated(
        git, "v0.1.1", c["B"], "v0.1.1\n\nReviewed-by: a person\nNotes: none"),
    "clean-annotation-outside-tags": lambda git, c: (
        _annotated(git, "x", c["B"], "clean\n\nbody"),
        git("update-ref", "refs/remotes/origin/x", "refs/tags/x"),
        git("tag", "-d", "x")),
    "deep-clean-chain": lambda git, c: _chain(git, c["B"], 40, inner_message="t1"),
}


def _chain(git, target, depth, inner_message):
    """depth nested annotated tags; only the outermost keeps a ref."""
    prev = target
    for i in range(1, depth + 1):
        _annotated(git, "t%d" % i, prev, inner_message if i == 1 else "t%d" % i)
        prev = "refs/tags/t%d" % i
    for i in range(1, depth):
        git("tag", "-d", "t%d" % i)

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
    "trailer-in-annotation-outside-tags": lambda git, c: (
        _annotated(git, "x", c["B"], "release\n\n" + TRAILER),
        git("update-ref", "refs/remotes/origin/x", "refs/tags/x"),
        git("tag", "-d", "x")),
    "trailer-deep-in-chain": lambda git, c: _chain(git, c["B"], 40, inner_message="t1\n\n" + TRAILER),
    "other-trailer-spelling-in-tag": lambda git, c: _annotated(
        git, "v0.1.1", c["B"], "release\n\nco-authored-by: Codex <bot" + "@" + "example.invalid>"),
    "annotated-tag-of-a-blob": lambda git, c: _annotated(git, "blobtag", c["blob"], "blob"),
    "orphan-laundered-by-a-replace-ref": lambda git, c: (
        git("replace", "--graft", c["B"], c["U"]),
        git("tag", "laundered", c["U"])),
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


def test_the_anchor_is_the_configured_ref_tip_not_its_root(tmp_path):
    gate = _gate()
    repo, git, commits = _repo(tmp_path)
    git("branch", "release", commits["S"])
    git("config", "fleetops.publishRef", "refs/heads/release")
    git("update-ref", "-d", "refs/heads/main")
    git("tag", "rel", commits["S"])  # the tip of release itself
    assert gate.check(str(repo), quiet=True) == 0


def test_a_publish_ref_that_is_not_a_commit_is_refused(tmp_path, capsys):
    gate = _gate()
    repo, git, commits = _repo(tmp_path)
    git("tag", "rel", commits["blob"])
    git("config", "fleetops.publishRef", "refs/tags/rel")
    assert gate.check(str(repo)) == 1
    assert "REFUSED" in capsys.readouterr().out
    assert gate.publishable_refs(str(repo)) == {"refs/tags/rel"}


def _pr_checkout(git, commits):
    """What a pull-request checkout holds: no local branch, origin/main, tags, detached HEAD."""
    git("update-ref", "refs/remotes/origin/main", commits["B"])
    git("checkout", "-q", "--detach", commits["B"])
    git("update-ref", "-d", "refs/heads/main")


def test_a_pull_request_checkout_judges_tags_against_origin_main(tmp_path):
    gate = _gate()
    repo, git, commits = _repo(tmp_path)
    _pr_checkout(git, commits)
    git("tag", "v0.1.1", commits["B"])
    assert gate.check(str(repo), quiet=True) == 0
    git("tag", "v9.9.9", commits["U"])
    assert gate.check(str(repo), quiet=True) == 1


def test_a_pull_request_head_off_main_does_not_anchor_tags(tmp_path):
    gate = _gate()
    repo, git, commits = _repo(tmp_path)
    _pr_checkout(git, commits)
    tree = git("rev-parse", "HEAD^{tree}")
    pr_head = git("commit-tree", tree, "-p", commits["B"], "-m", "P")
    git("checkout", "-q", "--detach", pr_head)
    git("tag", "v0.1.1", commits["B"])
    assert gate.check(str(repo), quiet=True) == 0
    git("tag", "on-pr-head", pr_head)  # reachable from HEAD, not from origin/main
    assert gate.check(str(repo), quiet=True) == 1


def test_a_tag_named_like_the_publishing_ref_cannot_become_the_anchor(tmp_path):
    gate = _gate()
    repo, git, commits = _repo(tmp_path)
    _pr_checkout(git, commits)
    git("tag", "refs/heads/main", commits["U"])  # refs/tags/refs/heads/main
    assert gate.check(str(repo), quiet=True) == 1


def test_an_impostor_tag_on_published_history_does_not_shift_the_anchor(tmp_path):
    gate = _gate()
    repo, git, commits = _repo(tmp_path)
    _pr_checkout(git, commits)
    git("tag", "refs/heads/main", commits["A"])
    git("tag", "v0.1.1", commits["B"])  # on origin/main; must not be judged against A
    assert gate.check(str(repo), quiet=True) == 0


def test_an_impostor_tag_cannot_stand_in_for_a_missing_anchor(tmp_path):
    gate = _gate()
    repo, git, commits = _repo(tmp_path)
    git("checkout", "-q", "--detach", commits["B"])
    git("update-ref", "-d", "refs/heads/main")
    git("tag", "refs/heads/main", commits["B"])
    assert gate.check(str(repo), quiet=True) == 2


def test_tags_without_any_publishing_anchor_refuse(tmp_path):
    gate = _gate()
    repo, git, commits = _repo(tmp_path)
    git("checkout", "-q", "--detach", commits["B"])
    git("update-ref", "-d", "refs/heads/main")
    git("tag", "v0.1.1", commits["B"])
    assert gate.check(str(repo), quiet=True) == 2


IMPOSTOR_SPELLINGS = [
    "refs/tags/refs/heads/main",
    "refs/remotes/refs/heads/main",
    "refs/heads/refs/heads/main",
    "refs/refs/heads/main",
    "refs/heads/main/extra",
]


@pytest.mark.parametrize("impostor", IMPOSTOR_SPELLINGS)
def test_no_spelling_of_the_publishing_ref_stands_in_for_it(tmp_path, impostor):
    """rev-parse would resolve each of these for refs/heads/main; the gate must not."""
    gate = _gate()
    repo, git, commits = _repo(tmp_path)
    git("checkout", "-q", "--detach", commits["B"])
    git("update-ref", "-d", "refs/heads/main")
    git("update-ref", impostor, commits["U"])
    git("tag", "on-orphan", commits["U"])
    assert gate.check(str(repo), quiet=True) == 2


@pytest.mark.parametrize("shape", ["push", "pull-request"])
def test_a_git_failure_at_any_call_refuses(tmp_path, capsys, shape):
    """Every git call the gate makes is failed in turn; none may turn into a verdict."""
    gate = _gate()
    repo, git, commits = _repo(tmp_path)
    _annotated(git, "v0.1.1", commits["B"], "v0.1.1")
    git("tag", "v0.1.0", commits["A"])
    if shape == "pull-request":
        _pr_checkout(git, commits)
    real_run = subprocess.run
    calls = []

    def record(argv, *args, **kwargs):
        if argv[:1] == ["git"]:
            calls.append(list(argv))
        return real_run(argv, *args, **kwargs)

    gate.subprocess.run = record
    try:
        assert gate.check(str(repo), quiet=True) == 0
    finally:
        gate.subprocess.run = real_run
    seen = {" ".join(argv) for argv in calls}
    assert any("cat-file tag" in argv for argv in seen)
    assert any("merge-base --is-ancestor" in argv for argv in seen)
    capsys.readouterr()

    for target in range(len(calls)):
        count = [0]

        def run(argv, *args, **kwargs):
            if argv[:1] == ["git"]:
                count[0] += 1
                if count[0] == target + 1:
                    return subprocess.CompletedProcess(argv, 128, "", "fatal: injected failure")
            return real_run(argv, *args, **kwargs)

        gate.subprocess.run = run
        try:
            rc = gate.check(str(repo), quiet=False)
        finally:
            gate.subprocess.run = real_run
        out = capsys.readouterr()
        argv = " ".join(calls[target])
        assert count[0] > target, argv
        if "config" in calls[target]:
            assert rc == 1 and "REFUSED" in out.out, argv  # documented config refusal
        elif "--is-shallow-repository" in calls[target]:
            assert rc == 2, argv
        else:
            assert rc == 2 and "injected failure" in out.err, argv


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
    assert "shallow" in result.stderr
    full = tmp_path / "full"
    subprocess.run(["git", "clone", "-q", repo.as_uri(), str(full)], check=True, capture_output=True)
    control = subprocess.run([sys.executable, str(path), str(full)], capture_output=True, text=True)
    assert control.returncode == 0, control.stdout + control.stderr
