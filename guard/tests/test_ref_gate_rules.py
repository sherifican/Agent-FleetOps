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
}


@pytest.mark.parametrize("files", CLEAN_NAME_CASES.values(), ids=CLEAN_NAME_CASES.keys())
def test_allowed_names_stay_clean(tmp_path, files):
    gate = _gate()
    repo, git = _repo(tmp_path)
    _commit_files(repo, git, files)
    assert gate.check(str(repo), quiet=True) == 0


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
