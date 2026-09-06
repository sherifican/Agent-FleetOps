"""Disposable-repository tests; no network, real credentials, or production scanner.

Run: python3 -m pytest -q -p no:cacheprovider test_pre_push_fail_open.py
Compare the SAME contract with the supplied baseline:
  PRE_PUSH_HOOK=pre-push.shipped.sh python3 -m pytest -q -p no:cacheprovider \
      test_pre_push_fail_open.py -k required

Reject-all is the default scanner double. The clean incremental control explicitly
uses a content-sensitive double: reject-all cannot establish a clean-tree success.
No-op and clean controls may already pass the baseline; do not invent RED results.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


HERE = Path(__file__).resolve().parent
HOOK = (HERE / os.environ.get("PRE_PUSH_HOOK", "../hooks/pre-push")).resolve()
ZERO = "0" * 40
FAKE = "0" * 39 + "1"
SCANNER = r'''import json, os, pathlib, sys
arg = sys.argv[1]
event = {"event": "self-test" if arg == "--self-test" else "tree"}
if arg != "--self-test":
    root = pathlib.Path(arg)
    event["files"] = {str(p.relative_to(root)): p.read_text(errors="replace")
                      for p in root.rglob("*") if p.is_file() and not p.is_symlink()}
with open(os.environ["SCANNER_EVENTS"], "a") as stream:
    stream.write(json.dumps(event) + "\n")
policy = os.environ.get("SCANNER_POLICY", "reject-all")
if arg == "--self-test":
    sys.exit(3 if policy == "self-test-fail" else 0)
if policy == "content":
    sys.exit(int(any("TEST_FORBIDDEN_PAYLOAD" in s for s in event["files"].values())))
sys.exit(1)
'''


class Repo:
    def __init__(self, tmp_path, object_format="sha1"):
        self.base = tmp_path
        self.path = tmp_path / "gatework"
        self.remote = tmp_path / "public.git"
        self.events = tmp_path / "scanner.jsonl"
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        self.env.update(HOME=str(tmp_path), GIT_CONFIG_NOSYSTEM="1",
                        GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT="0",
                        SCANNER_EVENTS=str(self.events), SCANNER_POLICY="reject-all",
                        LC_ALL="C", PYTHONDONTWRITEBYTECODE="1")
        self.path.mkdir()
        self.git("init", "-q", "--template=", "-b", "main", "--object-format=" + object_format)
        (self.path / ".git" / "info").mkdir(exist_ok=True)
        (self.path / ".git" / "hooks").mkdir(exist_ok=True)
        self.git("config", "user.name", "Gate Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("config", "fleetops.approvedIdentity", "fixture@example.invalid")
        self.git("config", "commit.gpgSign", "false")
        self.git("init", "-q", "--bare", "--template=", "--object-format=" + object_format,
                 str(self.remote))
        self.git("remote", "add", "public", str(self.remote))
        self.old = self.commit("README", "safe baseline\n")
        self.git("push", "-q", "public", "main")
        (self.path / "_tools").mkdir()
        (self.path / "_tools" / "scan_gate.py").write_text(SCANNER)
        self.zero = "0" * len(self.old)

    def git(self, *args, check=True):
        result = subprocess.run(["git", *args], cwd=self.path, env=self.env,
                                text=True, capture_output=True, timeout=20)
        if check:
            assert result.returncode == 0, (args, result.stdout, result.stderr)
        return result

    def commit(self, name, content):
        target = self.path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        self.git("add", "--", name)
        self.git("commit", "-qm", "fixture commit")
        return self.git("rev-parse", "HEAD").stdout.strip()

    def input(self, new, old=None, ref="refs/heads/main"):
        return f"{ref} {new} {ref} {self.old if old is None else old}\n"

    def run(self, data, policy="reject-all", destination=None, hook=HOOK):
        self.events.unlink(missing_ok=True)
        env = dict(self.env, SCANNER_POLICY=policy)
        result = subprocess.run(["bash", str(hook), "public", str(destination or self.remote)],
                                cwd=self.path, env=env, input=data, text=True,
                                capture_output=True, timeout=30)
        events = [json.loads(s) for s in self.events.read_text().splitlines()] if self.events.exists() else []
        assert events and events[0]["event"] == "self-test", (result, events)
        scans = [e for e in events if e["event"] == "tree"]
        # Since 2026-09-05 the hook scans TWO surfaces per commit: the archived tree, and the
        # commit message (which `git archive` does not carry -- grok's A5). Both reach the scanner
        # as a directory, so both arrive here as "tree" events. A message scan is the one whose
        # only file is commit-message.txt. Tests that count "how many commits were scanned" mean
        # tree scans, so the two surfaces are kept apart rather than summed -- otherwise adding a
        # surface silently doubles every count and reads as a regression.
        self.message_scans = [e for e in scans
                              if set(e.get("files", {})) == {"commit-message.txt"}]
        return result, [e for e in scans
                        if set(e.get("files", {})) != {"commit-message.txt"}]


@pytest.fixture
def repo(tmp_path):
    return Repo(tmp_path)


def assert_blocked(result):
    assert result.returncode != 0, result.stdout + result.stderr
    assert "CLEAN" not in result.stdout.replace("no CLEAN verdict", ""), result.stdout


def test_required_unavailable_destination(repo):
    new = repo.commit("payload", "TEST_FORBIDDEN_PAYLOAD\n")
    control = repo.git("rev-list", f"{repo.old}..{new}")
    assert control.stdout.splitlines() == [new]
    broken = repo.git("rev-list", f"{FAKE}..{new}", check=False)
    assert broken.returncode != 0 and broken.stdout == ""
    result, trees = repo.run(repo.input(new, FAKE))
    assert_blocked(result)
    assert trees == []  # Refused before relying on an invalid range.


def test_required_different_remote(repo):
    new = repo.commit("payload", "TEST_FORBIDDEN_PAYLOAD\n")
    repo.git("update-ref", "refs/remotes/private/main", new)
    # CP1: reproduce the empty query, then prove that the same instrument can fire.
    assert repo.git("rev-list", new, "--not", "--remotes").stdout == ""
    repo.git("update-ref", "-d", "refs/remotes/private/main")
    assert repo.git("rev-list", new, "--not", "--remotes").stdout.splitlines() == [new]
    repo.git("update-ref", "refs/remotes/private/main", new)
    result, trees = repo.run(repo.input(new, repo.zero, "refs/heads/release"))
    assert trees, result.stdout + result.stderr
    assert trees[0]["files"]["payload"] == "TEST_FORBIDDEN_PAYLOAD\n"
    assert_blocked(result)


def test_required_clean_incremental(repo):
    new = repo.commit("safe", "safe incremental content\n")
    # CP1 positive control: the content double must reject an actual bad tree.
    probe = repo.base / "bad-tree"
    probe.mkdir()
    (probe / "payload").write_text("TEST_FORBIDDEN_PAYLOAD")
    control = subprocess.run(["python3", "_tools/scan_gate.py", str(probe)],
                             cwd=repo.path, env=dict(repo.env, SCANNER_POLICY="content"),
                             capture_output=True, timeout=10)
    assert control.returncode == 1
    result, trees = repo.run(repo.input(new), policy="content")
    assert result.returncode == 0, result.stdout + result.stderr
    assert len(trees) == 1 and trees[0]["files"]["safe"] == "safe incremental content\n"


@pytest.mark.parametrize("kind", ["empty", "identical", "deletion"])
def test_required_noop(repo, kind):
    data = {"empty": "", "identical": repo.input(repo.old),
            "deletion": repo.input(repo.zero)}[kind]
    result, trees = repo.run(data)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not trees
    assert "BLOCKED" not in result.stdout


@pytest.mark.parametrize("data_kind", ["empty", "identical", "deletion"])
def test_zero_scan_reason(repo, data_kind):
    data = {"empty": "", "identical": repo.input(repo.old),
            "deletion": repo.input(repo.zero)}[data_kind]
    result, trees = repo.run(data)
    assert result.returncode == 0 and not trees
    assert "0 commits scanned:" in result.stdout
    assert ("empty push input" if data_kind == "empty" else "only deletions") in result.stdout


def test_destination_query_failure(repo):
    new = repo.commit("safe", "safe")
    result, trees = repo.run(repo.input(new, repo.zero), destination=repo.base / "absent.git")
    assert_blocked(result)
    assert "destination refs unknown" in result.stdout and not trees


def test_empty_destination_is_known(repo):
    empty = repo.base / "empty.git"
    repo.git("init", "-q", "--bare", "--template=", str(empty))
    result, trees = repo.run(repo.input(repo.old, repo.zero), destination=empty)
    assert_blocked(result)
    assert len(trees) == 1


def test_new_ref_missing_advertised_object(repo):
    elsewhere = repo.base / "elsewhere"
    repo.git("clone", "-q", "-b", "main", str(repo.remote), str(elsewhere))
    repo.git("-C", str(elsewhere), "-c", "user.name=Fixture", "-c",
             "user.email=fixture@example.invalid", "commit", "--allow-empty", "-qm", "remote only")
    repo.git("-C", str(elsewhere), "push", "-q", "origin", "HEAD:refs/heads/other")
    result, trees = repo.run(repo.input(repo.old, repo.zero, "refs/heads/alias"))
    assert_blocked(result)
    assert "destination-tip object unavailable" in result.stdout and not trees


def test_destination_alias_cannot_silently_scan_zero(repo):
    result, trees = repo.run(repo.input(repo.old, repo.zero, "refs/heads/alias"))
    assert_blocked(result)
    assert len(trees) == 1
    assert "scanning its reachable history" in result.stdout


def test_self_test_first(repo):
    result, trees = repo.run("", policy="self-test-fail")
    assert_blocked(result)
    assert not trees


def test_missing_identity_list(repo):
    repo.git("config", "--unset-all", "fleetops.approvedIdentity")
    result, trees = repo.run("")
    assert_blocked(result)
    assert "MISSING INPUT" in result.stdout and not trees


def test_identity_file_and_exact_comparison(repo):
    repo.git("config", "--unset-all", "fleetops.approvedIdentity")
    identities = repo.base / "allowlist"
    identities.write_text("# synthetic fixture\nfixture@example.invalid\n")
    repo.env["FLEETOPS_APPROVED_IDENTITIES"] = str(identities)
    new = repo.commit("safe", "safe")
    result, trees = repo.run(repo.input(new), policy="content")
    assert result.returncode == 0 and len(trees) == 1, result.stdout
    identities.write_text("fixture@example.invalid.evil\n")
    result, _ = repo.run(repo.input(new), policy="content")
    assert_blocked(result)


@pytest.mark.parametrize("field", ["author", "committer"])
def test_unapproved_identity(repo, field):
    repo.env["GIT_" + field.upper() + "_EMAIL"] = "outsider@example.invalid"
    new = repo.commit("safe", "safe")
    result, trees = repo.run(repo.input(new), policy="content")
    assert_blocked(result)
    assert len(trees) == 1 and "non-approved" in result.stdout
    assert "outsider@example.invalid" not in result.stdout


def test_committed_tree_not_clean_worktree(repo):
    new = repo.commit("payload", "TEST_FORBIDDEN_PAYLOAD\n")
    (repo.path / "payload").write_text("safe uncommitted replacement")
    result, trees = repo.run(repo.input(new), policy="content")
    assert_blocked(result)
    assert trees[0]["files"]["payload"] == "TEST_FORBIDDEN_PAYLOAD\n"


def test_archive_cannot_hide_or_rewrite_content(repo):
    repo.commit(".gitattributes", "hidden export-ignore\nnested export-ignore\nsubst export-subst\n")
    repo.commit("hidden", "TEST_FORBIDDEN_PAYLOAD\n")
    repo.commit("nested/data", "TEST_FORBIDDEN_PAYLOAD\n")
    new = repo.commit("subst", "$Format:%H$\n")
    # Source info/attributes must also be unable to suppress a file, and stay unchanged.
    attrs = repo.path / ".git" / "info" / "attributes"
    attrs.write_text("* export-ignore\n")
    result, trees = repo.run(repo.input(new), policy="content")
    assert_blocked(result)
    tip = trees[0]["files"]
    assert tip["hidden"] == tip["nested/data"] == "TEST_FORBIDDEN_PAYLOAD\n"
    assert tip["subst"] == "$Format:%H$\n"
    assert attrs.read_text() == "* export-ignore\n"


@pytest.mark.parametrize("command", ["rev-list", "archive", "show"])
def test_git_command_failure(repo, command):
    new = repo.commit("safe", "safe")
    shim = repo.base / "bin"
    shim.mkdir()
    real_git = shutil.which("git")
    wrapper = shim / "git"
    wrapper.write_text("#!/usr/bin/env python3\nimport os, sys\n"
                       f"if {command!r} in sys.argv[1:]: sys.exit(71)\n"
                       f"os.execv({real_git!r}, [{real_git!r}] + sys.argv[1:])\n")
    wrapper.chmod(0o755)
    repo.env["PATH"] = str(shim) + os.pathsep + repo.env["PATH"]
    result, trees = repo.run(repo.input(new), policy="content")
    assert_blocked(result)
    assert not trees


def test_shallow_history_refused(repo):
    (repo.path / ".git" / "shallow").write_text(repo.old + "\n")
    new = repo.commit("safe", "safe")
    result, trees = repo.run(repo.input(new), policy="content")
    assert_blocked(result)
    assert "shallow history" in result.stdout and not trees


def test_replacements_do_not_hide_tree(repo):
    dirty = repo.commit("payload", "TEST_FORBIDDEN_PAYLOAD\n")
    clean = repo.commit("payload", "safe replacement\n")
    repo.git("replace", dirty, clean)
    result, trees = repo.run(repo.input(dirty), policy="content")
    assert_blocked(result)
    assert any(e["files"].get("payload") == "TEST_FORBIDDEN_PAYLOAD\n" for e in trees)


def test_legacy_graft_refused(repo):
    (repo.path / ".git" / "info" / "grafts").write_text(repo.old + "\n")
    result, trees = repo.run(repo.input(repo.old, repo.zero))
    assert_blocked(result)
    assert "legacy grafts" in result.stdout and not trees


def test_gitlink_refused(repo):
    repo.git("update-index", "--add", "--cacheinfo", "160000," + repo.old + ",submodule")
    repo.git("commit", "-qm", "gitlink fixture")
    new = repo.git("rev-parse", "HEAD").stdout.strip()
    result, trees = repo.run(repo.input(new), policy="content")
    assert_blocked(result)
    assert "gitlink" in result.stdout and not trees


@pytest.mark.parametrize("data", ["\n", "refs/heads/main\n", "bad garbage bad garbage\n",
                                  f"refs/heads/main {ZERO} refs/heads/main {ZERO}\n"])
def test_malformed_input(repo, data):
    result, trees = repo.run(data)
    assert_blocked(result)
    assert not trees


def test_mixed_refs_and_deduplication(repo):
    new = repo.commit("safe", "safe")
    data = repo.input(new) + repo.input(new, repo.zero, "refs/heads/other") + repo.input(repo.zero)
    result, trees = repo.run(data, policy="content")
    assert result.returncode == 0, result.stdout + result.stderr
    assert len(trees) == 1
    assert "scanned 1 distinct selected commit tree(s)" in result.stdout


def test_sha256(repo, tmp_path):
    other = tmp_path / "sha256"
    other.mkdir()
    r = Repo(other, "sha256")
    new = r.commit("safe", "safe")
    result, trees = r.run(r.input(new), policy="content")
    assert result.returncode == 0 and len(trees) == 1, result.stdout + result.stderr


def test_real_git_push_invokes_replacement(repo):
    # This installs only into a disposable repo and pushes only to a local bare repo.
    shutil.copyfile(HOOK, repo.path / ".git" / "hooks" / "pre-push")
    (repo.path / ".git" / "hooks" / "pre-push").chmod(0o755)
    dirty = repo.commit("payload", "TEST_FORBIDDEN_PAYLOAD\n")
    result = repo.git("push", "public", "HEAD:refs/heads/release", check=False)
    assert result.returncode != 0, result.stdout + result.stderr
    assert repo.git("ls-remote", "--refs", str(repo.remote), "refs/heads/release").stdout == ""
    # Positive control for the absence assertion: main still exists at the baseline.
    assert repo.git("ls-remote", "--refs", str(repo.remote), "refs/heads/main").stdout.split()[0] == repo.old
    events = [json.loads(s) for s in repo.events.read_text().splitlines()]
    assert events[0]["event"] == "self-test" and any(e["event"] == "tree" for e in events)
    assert dirty != repo.old


@pytest.mark.parametrize("git_env", ["GIT_COMMON_DIR", "GIT_DIR"])
def test_archive_environment_isolation(repo, git_env):
    repo.commit(".gitattributes", "payload export-ignore\n")
    tip = repo.commit("payload", "TEST_FORBIDDEN_PAYLOAD\n")
    before = (repo.path / ".git" / "config").read_bytes()
    repo.env[git_env] = str(repo.path / ".git")
    result, trees = repo.run(repo.input(tip), policy="content")
    assert (repo.path / ".git" / "config").read_bytes() == before
    assert_blocked(result)
    assert trees[0]["files"]["payload"] == "TEST_FORBIDDEN_PAYLOAD\n"


@pytest.mark.parametrize("case", [
    "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE", "all",
])
def test_archive_other_inherited_environment(repo, case):
    repo.commit(".gitattributes", "payload export-ignore\n")
    clean = repo.commit("payload", "ordinary\n")
    dirty = repo.commit("payload", "TEST_FORBIDDEN_PAYLOAD\n")
    gitdir = repo.path / ".git"
    before = (gitdir / "config").read_bytes()
    alternate = repo.base / "alternate-objects"
    alternate.mkdir()
    values = {
        "GIT_DIR": str(gitdir),
        "GIT_COMMON_DIR": str(gitdir),
        "GIT_WORK_TREE": str(repo.path),
        "GIT_INDEX_FILE": str(gitdir / "index"),
        "GIT_OBJECT_DIRECTORY": str(gitdir / "objects"),
        "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(alternate),
        "GIT_NAMESPACE": "fixture-isolation",
    }
    repo.env.update(values if case == "all" else {case: values[case]})
    log = repo.base / "git-boundaries.jsonl"
    shimdir = repo.base / "instrumented-bin"
    shimdir.mkdir()
    real_git = shutil.which("git")
    assert real_git is not None
    wrapper = shimdir / "git"
    wrapper.write_text(
        "#!/usr/bin/env python3\nimport json, os, sys\n"
        "a = sys.argv[1:]\n"
        "phase = ('init' if 'init' in a and '--bare' in a else\n"
        "         'archive' if 'archive' in a else None)\n"
        "if phase:\n"
        f"    keys = {tuple(values)!r}\n"
        f"    with open({str(log)!r}, 'a') as f:\n"
        "        f.write(json.dumps({'phase': phase, 'env':\n"
        "            {k: os.environ[k] for k in keys if k in os.environ}}) + '\\n')\n"
        f"os.execv({real_git!r}, [{real_git!r}] + a)\n"
    )
    wrapper.chmod(0o755)
    repo.env["PATH"] = str(shimdir) + os.pathsep + repo.env["PATH"]
    for tip, forbidden in [(clean, False), (dirty, True)]:
        log.unlink(missing_ok=True)
        result, trees = repo.run(repo.input(tip), policy="content")
        if forbidden:
            assert_blocked(result)
            assert trees[0]["files"]["payload"] == "TEST_FORBIDDEN_PAYLOAD\n"
        else:
            assert result.returncode == 0, result.stdout + result.stderr
            assert trees[0]["files"]["payload"] == "ordinary\n"
        assert (gitdir / "config").read_bytes() == before
        assert log.exists(), result.stdout + result.stderr
        events = [json.loads(line) for line in log.read_text().splitlines()]
        assert {e["phase"] for e in events} == {"init", "archive"}
        for e in events:
            expected = ({} if e["phase"] == "init" else
                        {"GIT_OBJECT_DIRECTORY": str(gitdir / "objects")})
            assert e["env"] == expected, e
