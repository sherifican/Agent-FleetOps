"""Rules 2 and 3 judged on what a push carries, not on how git happens to name it.

Rule 2: `git rev-list --objects` prints each object once, under ONE of its names, so a banned
path whose bytes also sit at an allowed path (or a banned directory whose tree is identical to
an allowed one) was never seen. Every entry name of every reachable tree is judged instead.

Rule 3: the trailer matcher missed co-author trailers naming Grok, Qwen, DeepSeek or Gemma and
matched people whose names merely start like a model's ("Claudette"). Model, vendor and
assistant names now match as whole words. Written BEFORE the fix: the bypass and
human-name cases fail by assertion on the old gate.
"""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys

import pytest

IDENTITY = ["-c", "user.name=fixture", "-c", "user.email=fixture" + "@" + "example.invalid"]


@pytest.fixture(autouse=True)
def _hermetic_git(monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")


def _gate():
    path = Path(__file__).resolve().parents[2] / "_tools" / "ref_gate.py"
    spec = importlib.util.spec_from_file_location("ref_gate", path)
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)
    return gate


def _repo(tmp_path, object_format=None):
    repo = tmp_path / "rules"
    repo.mkdir()

    def git(*args, **kwargs):
        return subprocess.run(["git", *IDENTITY, *args], cwd=repo, check=True,
                              capture_output=True, text=True, **kwargs).stdout.strip()

    init = ["init", "-q", "-b", "main"]
    if object_format:
        init.append(f"--object-format={object_format}")
    git(*init)
    return repo, git


def _commit_files(repo, git, files, message="files"):
    for name, data in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    git("add", "-f", "--", *files)
    git("commit", "-q", "-m", message)


# Each case commits these files on main; the banned name shares its object with an allowed
# name that git lists first.
SHARED_OBJECT_CASES = {
    "same-blob-pyc": {"a.txt": b"same\n", "z.pyc": b"same\n"},
    "same-blob-pyo": {"a.txt": b"same\n", "z.pyo": b"same\n"},
    "same-blob-under-pycache-dir": {"a.txt": b"same\n", "pkg/__pycache__/m.bin": b"same\n"},
    "same-tree-as-pycache-dir": {"A/x.txt": b"same\n", "__pycache__/x.txt": b"same\n"},
    "same-blob-names-with-spaces": {"a b.txt": b"same\n", "c d.pyc": b"same\n"},
    "same-blob-name-with-newline": {"a.txt": b"same\n", "c\nd.pyc": b"same\n"},
}


@pytest.mark.parametrize("files", SHARED_OBJECT_CASES.values(), ids=SHARED_OBJECT_CASES.keys())
def test_a_banned_name_sharing_an_object_with_an_allowed_name_fails(tmp_path, files):
    gate = _gate()
    repo, git = _repo(tmp_path)
    _commit_files(repo, git, files)
    assert gate.check(str(repo), quiet=True) == 1


def test_a_banned_name_only_in_older_history_still_fails(tmp_path):
    gate = _gate()
    repo, git = _repo(tmp_path)
    _commit_files(repo, git, {"a.txt": b"same\n", "z.pyc": b"same\n"})
    git("rm", "-q", "--cached", "z.pyc")
    git("commit", "-q", "-m", "drop")
    assert gate.check(str(repo), quiet=True) == 1


def test_a_shared_banned_name_fails_in_a_sha256_repository(tmp_path):
    gate = _gate()
    try:
        repo, git = _repo(tmp_path, object_format="sha256")
    except subprocess.CalledProcessError:
        pytest.skip("this git cannot create sha256 repositories")
    _commit_files(repo, git, {"a.txt": b"same\n", "z.pyc": b"same\n"})
    assert gate.check(str(repo), quiet=True) == 1


CLEAN_NAME_CASES = {
    "same-blob-two-allowed-names": {"a.txt": b"same\n", "b.txt": b"same\n"},
    "pyc-not-the-suffix": {"x.pyc.txt": b"x\n", "pyc": b"y\n"},
    "pycache-as-a-prefix-only": {"__pycache__x/m.txt": b"x\n", "my__pycache__/m.txt": b"y\n"},
    "final-newline-after-pyc": {"ok.pyc\n": b"x\n"},
    "final-newline-after-pyo": {"ok.pyo\n": b"x\n"},
    "final-newline-after-pycache": {"__pycache__\n/m.txt": b"x\n"},
}


@pytest.mark.parametrize("files", CLEAN_NAME_CASES.values(), ids=CLEAN_NAME_CASES.keys())
def test_allowed_names_stay_clean(tmp_path, files):
    gate = _gate()
    repo, git = _repo(tmp_path)
    _commit_files(repo, git, files)
    assert gate.check(str(repo), quiet=True) == 0


def test_a_banned_name_with_a_control_character_is_reported_escaped(tmp_path, capsys):
    gate = _gate()
    repo, git = _repo(tmp_path)
    _commit_files(repo, git, {"c\nd.pyc": b"x\n"})
    assert gate.check(str(repo)) == 1
    assert "'c\\nd.pyc'" in capsys.readouterr().out


def test_trees_are_read_in_bounded_batches(tmp_path, monkeypatch):
    gate = _gate()
    monkeypatch.setattr(gate, "TREE_BATCH", 2)
    repo, git = _repo(tmp_path)
    files = {f"d{i}/f.txt": f"{i}\n".encode() for i in range(5)}
    files["d9/z.pyc"] = b"x\n"
    _commit_files(repo, git, files)
    batches = []
    real = gate.git_bytes

    def spy(args, cwd, input=None):
        if args[:2] == ["cat-file", "--batch"]:
            batches.append(input.count(b"\n"))
        return real(args, cwd, input=input)

    monkeypatch.setattr(gate, "git_bytes", spy)
    assert gate.check(str(repo), quiet=True) == 1
    assert sum(batches) == 7 and max(batches) <= 2, batches


def test_the_default_tree_batch_is_bounded():
    assert 1 <= _gate().TREE_BATCH <= 1000


MODEL_COAUTHORS = [
    "Claude <noreply" + "@" + "anthropic.com>",
    "Bot <noreply" + "@" + "anthropic.com>",
    "ChatGPT <bot@example.invalid>",
    "GPT-6 Astra <bot@example.invalid>",
    "OpenAI Codex <bot@example.invalid>",
    "Gemini <bot@example.invalid>",
    "Gemma <bot@example.invalid>",
    "Grok <bot@example.invalid>",
    "Qwen <bot@example.invalid>",
    "DeepSeek <bot@example.invalid>",
    "Llama <bot@example.invalid>",
    "Mistral <bot@example.invalid>",
    "Kimi <bot@example.invalid>",
    "GLM <bot@example.invalid>",
    "GitHub Copilot <bot@example.invalid>",
    "An Assistant <bot@example.invalid>",
    "Some AI <bot@example.invalid>",
    "Qwen3-Coder <bot@example.invalid>",
    "Gemma4 <bot@example.invalid>",
    "GPT6 <bot@example.invalid>",
    "GPT-4o <bot@example.invalid>",
    "ClaudeCode <bot@example.invalid>",
    "Claude Sonnet 5 <bot@example.invalid>",
    "Llama3.3 <bot@example.invalid>",
    "DeepSeek-V4 <bot@example.invalid>",
    "Kimi-K2 <bot@example.invalid>",
    "glm4 <bot@example.invalid>",
    "codex_cli <bot@example.invalid>",
    "Bot <noreply" + "@" + "x.ai>",
    "x.ai <bot@example.invalid>",
    "Cursor <bot@example.invalid>",
    "Cursor Agent <bot@example.invalid>",
    "Ornith-9B <bot@example.invalid>",
]


@pytest.mark.parametrize("who", MODEL_COAUTHORS)
@pytest.mark.parametrize("key", ["Co-Authored-By", "co-authored-by"])
def test_a_co_author_trailer_naming_a_model_fails(tmp_path, who, key):
    gate = _gate()
    repo, git = _repo(tmp_path)
    git("commit", "-q", "--allow-empty", "-m", f"change\n\n{key}: {who}")
    assert gate.check(str(repo), quiet=True) == 1


def test_a_tag_annotation_naming_a_model_fails(tmp_path):
    gate = _gate()
    repo, git = _repo(tmp_path)
    git("commit", "-q", "--allow-empty", "-m", "A")
    git("tag", "-a", "v1", "-m", "v1\n\nCo-Authored-By: Qwen <bot@example.invalid>")
    assert gate.check(str(repo), quiet=True) == 1


HUMAN_LINES = [
    "Co-Authored-By: Claudette Martin <c@example.invalid>",
    "Co-Authored-By: Gemmaline Ortiz <g@example.invalid>",
    "Co-Authored-By: Kimiko Sato <k@example.invalid>",
    "Co-Authored-By: Ai Nakamura <a@example.invalid>",
    "Co-Authored-By: Lena Grokowski <l@example.invalid>",
    "Co-Authored-By: Qwendolyn Hart <q@example.invalid>",
    "Co-Authored-By: Codexa Rivers <x@example.invalid>",
    "Co-Authored-By: Max Aiken <max.aiken@example.invalid>",
    "Co-Authored-By: Rex Aiello <rex.aiello@example.invalid>",
    "Co-Authored-By: Gemma\u00eblle Martin <g2@example.invalid>",
    "Co-Authored-By: Kimi\u0107 Novak <k2@example.invalid>",
    "Co-Authored-By: Llama\u00f1o Ruiz <l2@example.invalid>",
    "Reviewed-by: Claude <r@example.invalid>",
    "Delegated-to: a local code model wrote the change from a written spec",
    "Authored-directly: the spec, the tests and the count updates",
]


@pytest.mark.parametrize("line", HUMAN_LINES)
def test_people_and_disclosure_lines_stay_clean(tmp_path, line):
    gate = _gate()
    repo, git = _repo(tmp_path)
    git("commit", "-q", "--allow-empty", "-m", f"change\n\n{line}")
    assert gate.check(str(repo), quiet=True) == 0



def test_a_disclosure_line_does_not_excuse_a_model_co_author(tmp_path):
    gate = _gate()
    repo, git = _repo(tmp_path)
    git("commit", "-q", "--allow-empty", "-m",
        "change\n\nDelegated-to: a local code model wrote the change\n"
        "Co-Authored-By: Qwen <bot@example.invalid>")
    assert gate.check(str(repo), quiet=True) == 1


def test_a_person_whose_name_is_a_model_name_is_refused_and_the_line_shown_whole(tmp_path, capsys):
    """Documented policy: names are matched as words, so a person who shares a model's name
    is refused too (fail closed). The report prints the whole line so the author can see why."""
    gate = _gate()
    repo, git = _repo(tmp_path)
    line = "Co-Authored-By: Gemma Jones <h@example.invalid>"
    git("commit", "-q", "--allow-empty", "-m", f"change\n\n{line}")
    assert gate.check(str(repo)) == 1
    assert line in capsys.readouterr().out



# Python's str.splitlines() also splits on these; git allows them inside a ref name.
UNICODE_LINE_BREAKS = ["\u0085", "\u2028", "\u2029"]


@pytest.mark.parametrize("sep", UNICODE_LINE_BREAKS, ids=["NEL", "LS", "PS"])
def test_a_branch_named_main_plus_a_unicode_line_break_is_a_stray(tmp_path, sep):
    gate = _gate()
    repo, git = _repo(tmp_path)
    git("commit", "-q", "--allow-empty", "-m", "A")
    git("update-ref", f"refs/heads/main{sep}hidden", "HEAD")
    assert gate.check(str(repo), quiet=True) == 1


def _write_raw_message(repo, git, kind, message):
    """Store a commit or a tag whose message is not valid UTF-8."""
    head = git("rev-parse", "HEAD")
    if kind == "tag":
        body = (f"object {head}\ntype commit\ntag v1\n"
                "tagger f <f@example.invalid> 0 +0000\n\n").encode() + message
        sha = subprocess.run(["git", "hash-object", "-t", "tag", "-w", "--stdin"], cwd=repo,
                             input=body, capture_output=True, check=True).stdout.decode().strip()
        git("update-ref", "refs/tags/v1", sha)
    else:
        tree = git("rev-parse", "HEAD^{tree}")
        body = (f"tree {tree}\nparent {head}\n"
                "author f <f@example.invalid> 0 +0000\n"
                "committer f <f@example.invalid> 0 +0000\n\n").encode() + message
        sha = subprocess.run(["git", "hash-object", "-t", "commit", "-w", "--stdin"], cwd=repo,
                             input=body, capture_output=True, check=True).stdout.decode().strip()
        git("update-ref", "refs/heads/main", sha)


NON_UTF8_CASES = {
    "tag-clean": ("tag", b"caf\xe9\n", 0),
    "tag-model-trailer": ("tag", b"caf\xe9\n\nCo-Authored-By: Qwen <bot@example.invalid>\n", 1),
    "commit-clean": ("commit", b"caf\xe9\n", 0),
    "commit-model-trailer": ("commit", b"caf\xe9\n\nCo-Authored-By: Qwen \xe9 <bot@example.invalid>\n", 1),
}


@pytest.mark.parametrize("case", NON_UTF8_CASES.values(), ids=NON_UTF8_CASES.keys())
def test_a_message_that_is_not_utf8_gets_a_verdict_not_a_traceback(tmp_path, case):
    kind, message, expected = case
    repo, git = _repo(tmp_path)
    git("commit", "-q", "--allow-empty", "-m", "A")
    _write_raw_message(repo, git, kind, message)
    path = Path(__file__).resolve().parents[2] / "_tools" / "ref_gate.py"
    env = dict(os.environ, PYTHONIOENCODING="utf-8:strict", PYTHONUTF8="0")
    result = subprocess.run([sys.executable, str(path), str(repo)], capture_output=True,
                            text=True, errors="replace", env=env)
    assert "Traceback" not in result.stdout + result.stderr, result.stderr
    assert result.returncode == expected, result.stdout + result.stderr


def test_a_ref_name_holding_a_line_break_does_not_stand_in_for_the_publishing_ref(tmp_path):
    # for-each-ref lists children of refs/heads/main; split on U+2028, this child's name
    # would end in a record reading exactly "refs/heads/main <sha>".
    gate = _gate()
    repo, git = _repo(tmp_path)
    git("commit", "-q", "--allow-empty", "-m", "A")
    git("branch", "-m", "work")
    git("update-ref", "refs/heads/main/a refs/heads/main", "HEAD")
    assert gate.publish_commit(str(repo), "refs/heads/main") == (None, "absent")


def test_a_branch_name_that_is_not_utf8_is_reported_escaped(tmp_path):
    repo, git = _repo(tmp_path)
    git("commit", "-q", "--allow-empty", "-m", "A")
    subprocess.run(["git", "update-ref", b"refs/heads/caf\xff", "HEAD"], cwd=repo, check=True,
                   capture_output=True)
    path = Path(__file__).resolve().parents[2] / "_tools" / "ref_gate.py"
    env = dict(os.environ, PYTHONIOENCODING="utf-8:strict", PYTHONUTF8="0")
    result = subprocess.run([sys.executable, str(path), str(repo)], capture_output=True,
                            text=True, errors="replace", env=env)
    assert "Traceback" not in result.stdout + result.stderr, result.stderr
    assert result.returncode == 1, result.stdout + result.stderr
    assert "'refs/heads/caf\\udcff'" in result.stdout, result.stdout
