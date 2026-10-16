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
    assert _gate().TREE_BATCH == 1000


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
    # The vendor spelling is x.ai with a literal dot: look-alikes are people, not the vendor.
    "Co-Authored-By: Max Byrne <max@xXai.example.invalid>",
    "Co-Authored-By: Rex Byrne <rex@x-ai.example.invalid>",
    # A mark continues the word on every alternative and on both sides: decomposed and composed
    # spellings of the same person give the same verdict.
    "Co-Authored-By: A\u00cdda Ruiz <a2@example.invalid>",
    "Co-Authored-By: AI\u0301da Ruiz <a3@example.invalid>",
    "Co-Authored-By: M\u00e1kimi Ortiz <m1@example.invalid>",
    "Co-Authored-By: Ma\u0301kimi Ortiz <m2@example.invalid>",
    "Co-Authored-By: Rex Byrne <rex@x.a\u00edme.example.invalid>",
    "Co-Authored-By: Rex Byrne <rex@x.ai\u0301me.example.invalid>",
    "Co-Authored-By: Gemma\u00eblle Martin <g2@example.invalid>",
    "Co-Authored-By: Kimi\u0107 Novak <k2@example.invalid>",
    "Co-Authored-By: Llama\u00f1o Ruiz <l2@example.invalid>",
    # The same names composed and decomposed: a combining mark continues the word too.
    "Co-Authored-By: Kim\u00ee Jones <k3@example.invalid>",
    "Co-Authored-By: Kimi\u0302 Jones <k4@example.invalid>",
    "Co-Authored-By: Gemm\u00e1lia Jones <g3@example.invalid>",
    "Co-Authored-By: Gemma\u0301lia Jones <g4@example.invalid>",
    # Marks with no precomposed form: normalizing to NFC and then using plain word boundaries
    # would refuse these people, so the rule must be the mark itself, not normalization.
    "Co-Authored-By: Grok\u1ab0 Ivanova <g5@example.invalid>",
    "Co-Authored-By: Ma\u1ab0kimi Ortiz <m3@example.invalid>",
    "Co-Authored-By: Gemma\u20d7 Ruiz <g6@example.invalid>",
    "Co-Authored-By: Qwen\u1ab0a Lee <q2@example.invalid>",
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


def _run_strict(repo):
    path = Path(__file__).resolve().parents[2] / "_tools" / "ref_gate.py"
    env = dict(os.environ, PYTHONIOENCODING="utf-8:strict", PYTHONUTF8="0")
    return subprocess.run([sys.executable, str(path), str(repo)], capture_output=True,
                          text=True, errors="replace", env=env)


def _configure_publish_ref(repo, ref):
    subprocess.run(["git", "config", "fleetops.publishRef", ref], cwd=repo, check=True,
                   capture_output=True)


CONFIGURED_REF_CASES = {
    # A configured publishing ref that is absent, with no tags to judge.
    "absent": (None, "refs/heads/caf\\udcff", 1),
    # One that names a tree, not a commit.
    "not-a-commit": (b"refs/tags/caf\xff", "refs/tags/caf\\udcff", 1),
    # One that is absent locally but present as a remote-tracking ref.
    "remote-anchor": (b"refs/remotes/origin/caf\xff", "refs/remotes/origin/caf\\udcff", 1),
}


@pytest.mark.parametrize("case", CONFIGURED_REF_CASES.values(), ids=CONFIGURED_REF_CASES.keys())
def test_a_configured_publishing_ref_that_is_not_utf8_is_reported_escaped(tmp_path, case):
    plant, shown, expected = case
    repo, git = _repo(tmp_path)
    git("commit", "-q", "--allow-empty", "-m", "A")
    if plant is not None:
        target = git("rev-parse", "HEAD^{tree}") if plant.startswith(b"refs/tags/") else git("rev-parse", "HEAD")
        subprocess.run(["git", "update-ref", plant, target], cwd=repo, check=True, capture_output=True)
    _configure_publish_ref(repo, b"refs/heads/caf\xff" if plant is None or b"remotes" in plant else plant)
    result = _run_strict(repo)
    assert "Traceback" not in result.stdout + result.stderr, result.stderr
    assert result.returncode == expected, result.stdout + result.stderr
    assert f"'{shown}'" in result.stdout, result.stdout
    if plant is None:
        assert f"publishing ref '{shown}' absent; no tags to judge" in result.stdout, result.stdout


# `git config --get` ends its value with one LF. Stripping more than that also removes a trailing
# U+0085, U+2028 or U+2029 (str.strip treats them as whitespace), and reading git's output in
# universal-newline text mode turns a quoted trailing CR plus that LF into one LF, so a configured
# name that is NOT refs/heads/main was read as refs/heads/main and main was allowed.
# U+0085, U+2028 and U+2029 are legal in a ref name: the configured ref stays that name, and
# refs/heads/main is reported as a stray. LF and CR are not: the value is refused.
CONFIGURED_TAILS = {"nel": "\u0085", "ls": "\u2028", "ps": "\u2029", "newline": "\n", "cr": "\r"}
REFUSED_TAILS = {"\n", "\r"}


@pytest.mark.parametrize("tail", CONFIGURED_TAILS.values(), ids=CONFIGURED_TAILS.keys())
def test_a_configured_publishing_ref_with_a_trailing_character_is_not_read_as_main(tmp_path, tail):
    repo, git = _repo(tmp_path)
    git("commit", "-q", "--allow-empty", "-m", "A")
    _configure_publish_ref(repo, "refs/heads/main" + tail)
    result = _run_strict(repo)
    assert "Traceback" not in result.stdout + result.stderr, result.stderr
    assert result.returncode == 1, result.stdout + result.stderr
    if tail in REFUSED_TAILS:
        assert "ref_gate: REFUSED fleetops.publishRef must name a valid full ref" in result.stdout, result.stdout
    else:
        shown = "refs/heads/main" + tail.encode("unicode_escape").decode("ascii")
        assert f"publishable allow-list: ['{shown}']" in result.stdout, result.stdout
        assert "         refs/heads/main @ " in result.stdout, result.stdout


# ASCII space and tab can never be part of a ref name, so padding at either end is trimmed
# (the legacy edge cases in test_ref_gate.py trim leading and trailing padding together).
PADDED = {"trailing": "refs/heads/main \t", "leading": " \trefs/heads/main", "both": "  refs/heads/main \t"}


@pytest.mark.parametrize("value", PADDED.values(), ids=PADDED.keys())
def test_ascii_padding_at_either_end_of_the_configured_ref_is_trimmed(tmp_path, value):
    repo, git = _repo(tmp_path)
    git("commit", "-q", "--allow-empty", "-m", "A")
    _configure_publish_ref(repo, value)
    result = _run_strict(repo)
    assert result.returncode == 0, result.stdout + result.stderr


def test_a_plain_configured_publishing_ref_still_passes(tmp_path):
    repo, git = _repo(tmp_path)
    git("commit", "-q", "--allow-empty", "-m", "A")
    _configure_publish_ref(repo, "refs/heads/main")
    result = _run_strict(repo)
    assert result.returncode == 0, result.stdout + result.stderr


def test_a_configured_publishing_ref_from_a_crlf_config_file_still_passes(tmp_path):
    # git drops the CR of a CRLF config line itself; the gate must not refuse such a file.
    repo, git = _repo(tmp_path)
    git("commit", "-q", "--allow-empty", "-m", "A")
    with open(Path(repo) / ".git" / "config", "ab") as config:
        config.write(b"[fleetops]\r\n\tpublishRef = refs/heads/main\r\n")
    result = _run_strict(repo)
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_self_test_catches_a_prose_scanner_whose_hit_matches_a_real_one(monkeypatch, capsys):
    # A scanner that reports the name once whenever the history mentions it anywhere returns the
    # same hit before and after the prose commit, because a real __pycache__ is already planted.
    gate = _gate()

    def mentions(repo):
        log = subprocess.run(["git", "log", "--all", "--name-only", "--format=%B"], cwd=repo,
                             capture_output=True, text=True, check=True).stdout
        return [("log", "__pycache__")] if "__pycache__" in log else []

    monkeypatch.setattr(gate, "banned_objects", mentions)
    assert gate.self_test() == 1
    assert "DISCRIMINATION" in capsys.readouterr().out


def test_a_repository_path_that_is_not_utf8_gets_a_verdict(tmp_path):
    parent = tmp_path / "paths"
    parent.mkdir()
    repo = os.path.join(os.fsencode(parent), b"caf\xff")
    os.mkdir(repo)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", *IDENTITY, "commit", "-q", "--allow-empty", "-m", "A"], cwd=repo, check=True)
    path = Path(__file__).resolve().parents[2] / "_tools" / "ref_gate.py"
    env = dict(os.environ, PYTHONIOENCODING="utf-8:strict", PYTHONUTF8="0")
    result = subprocess.run([sys.executable, str(path), repo], capture_output=True, env=env)
    out = (result.stdout + result.stderr).decode("utf-8", "replace")
    assert b"Traceback" not in result.stdout + result.stderr, out
    assert result.returncode == 0, out


def test_a_report_on_an_ascii_stream_gets_a_verdict(tmp_path):
    repo, git = _repo(tmp_path)
    git("commit", "-q", "--allow-empty", "-m", "A")
    git("branch", "caf\u00e9")
    path = Path(__file__).resolve().parents[2] / "_tools" / "ref_gate.py"
    env = dict(os.environ, PYTHONIOENCODING="ascii:strict", PYTHONUTF8="0")
    result = subprocess.run([sys.executable, str(path), str(repo)], capture_output=True, env=env)
    out = (result.stdout + result.stderr).decode("ascii", "replace")
    assert b"Traceback" not in result.stdout + result.stderr, out
    assert result.returncode == 1, out
    # The report itself reaches the stream, with the characters ASCII cannot hold escaped.
    assert b"refs/heads/caf\\xe9 @ " in result.stdout, out
    assert b"=> VIOLATIONS" in result.stdout, out
    # A stream that can hold the character gets it as it is, not escaped.
    env = dict(os.environ, PYTHONIOENCODING="utf-8:strict", PYTHONUTF8="0")
    result = subprocess.run([sys.executable, str(path), str(repo)], capture_output=True, env=env)
    out = (result.stdout + result.stderr).decode("utf-8", "replace")
    assert result.returncode == 1, out
    assert "refs/heads/caf\u00e9 @ ".encode() in result.stdout, out
    assert b"caf\\xe9" not in result.stdout, out


def test_a_configuration_refusal_holding_undecodable_bytes_is_reported(tmp_path, monkeypatch):
    import io
    repo, git = _repo(tmp_path)
    git("commit", "-q", "--allow-empty", "-m", "A")
    gate = _gate()

    def refuse(repo):
        raise RuntimeError("cannot read fleetops.publishRef: caf\udcff")

    monkeypatch.setattr(gate, "publishable_refs", refuse)
    sink = io.TextIOWrapper(io.BytesIO(), encoding="utf-8", errors="strict")
    monkeypatch.setattr(sys, "stdout", sink)
    assert gate.check(str(repo)) == 1


def test_the_self_test_catches_a_scanner_that_reads_commit_prose_as_a_path(monkeypatch, capsys):
    # A `git log | grep` scanner reports the name a commit message mentions. The self-test says
    # it proves prose/path discrimination, so it must go red on exactly that scanner.
    gate = _gate()
    real = gate.banned_objects

    def reads_prose(repo):
        hits = real(repo)
        log = subprocess.run(["git", "log", "--all", "--format=%B"], cwd=repo,
                             capture_output=True, text=True, check=True).stdout
        if "untrack root __pycache__" in log:
            hits = hits + [("0" * 40, "__pycache__")]
        return hits

    monkeypatch.setattr(gate, "banned_objects", reads_prose)
    assert gate.self_test() == 1
    assert "DISCRIMINATION" in capsys.readouterr().out


DECOMPOSED_HUMAN_LINES = [
    "Co-Authored-By: Kimî Jones <k4@example.invalid>",
    "Co-Authored-By: Gemmália Jones <g4@example.invalid>",
]


@pytest.mark.parametrize("line", DECOMPOSED_HUMAN_LINES)
def test_a_decomposed_human_name_in_a_tag_annotation_stays_clean(tmp_path, line):
    # Tag annotations are scanned by their own loop; the combining-mark rule must hold there too.
    gate = _gate()
    repo, git = _repo(tmp_path)
    git("commit", "-q", "--allow-empty", "-m", "A")
    git("tag", "-a", "v1", "-m", f"v1\n\n{line}")
    assert gate.check(str(repo), quiet=True) == 0


def test_the_self_test_catches_a_prose_scanner_that_reads_only_the_checked_out_branch(monkeypatch, capsys):
    # `git log` without --all reads the branch that is checked out when the check runs, so the
    # prose commit has to be reachable from it.
    gate = _gate()
    real = gate.banned_objects

    def reads_branch_prose(repo):
        log = subprocess.run(["git", "log", "--format=%B"], cwd=repo,
                             capture_output=True, text=True, check=True).stdout
        return real(repo) + ([("0" * 40, "__pycache__")] if "__pycache__" in log else [])

    monkeypatch.setattr(gate, "banned_objects", reads_branch_prose)
    assert gate.self_test() == 1
    assert "DISCRIMINATION" in capsys.readouterr().out


# Category-M characters that are also Default_Ignorable_Code_Point (Unicode 18.0.0
# DerivedCoreProperties.txt): the combining grapheme joiner, Khmer inherent vowels, Mongolian free
# variation selectors and variation selectors 1-256. They are default-ignorable, so unlike a
# diacritic they must not continue a model name on either side.
IGNORABLE_MARK_RANGES = [(0x034F, 0x034F), (0x17B4, 0x17B5), (0x180B, 0x180D), (0x180F, 0x180F),
                         (0xFE00, 0xFE0F), (0xE0100, 0xE01EF)]
IGNORABLE_MARKS = [chr(c) for low, high in IGNORABLE_MARK_RANGES for c in range(low, high + 1)]
IGNORABLE_TOKENS = ["Claude", "Grok", "Gemma", "Ornith", "AI", "x.ai"]


def test_an_ignorable_mark_beside_a_model_name_does_not_hide_it():
    gate = _gate()
    assert len(IGNORABLE_MARKS) == 263  # assert-control: the full set, not a sample
    missed = []
    for mark in IGNORABLE_MARKS:
        for token in IGNORABLE_TOKENS:
            for who in (token + mark, mark + token):
                body = f"change\n\nCo-Authored-By: {who} <dev@example.invalid>\n"
                if not list(gate.trailer_matches(body)):
                    missed.append(f"U+{ord(mark):04X} {who!r}")
    assert not missed, f"{len(missed)} missed, first: {missed[:8]}"


IGNORABLE_END_TO_END = {
    "commit": "commit",
    "tag": "tag",
    "beside-a-diacritic-name": "mixed",
}


@pytest.mark.parametrize("where", IGNORABLE_END_TO_END.values(), ids=IGNORABLE_END_TO_END.keys())
def test_an_ignorable_mark_after_a_model_name_still_fails_the_gate(tmp_path, where, capsys):
    gate = _gate()
    repo, git = _repo(tmp_path)
    line = "Co-Authored-By: Grok\ufe00 <x@example.invalid>"
    if where == "commit":
        git("commit", "-q", "--allow-empty", "-m", f"change\n\n{line}")
    elif where == "tag":
        git("commit", "-q", "--allow-empty", "-m", "A")
        git("tag", "-a", "v1", "-m", f"v1\n\n{line}")
    else:
        # A real diacritic elsewhere in the message must not switch the ignorable mark on.
        git("commit", "-q", "--allow-empty", "-m",
            f"change\n\nCo-Authored-By: Kim\u0131\u0302 Jones <k5@example.invalid>\n{line}")
    assert gate.check(str(repo)) == 1
    # The refusal must be for the model line, not for the person beside it.
    flagged = [l for l in capsys.readouterr().out.splitlines() if "Co-Authored-By" in l]
    assert any("Grok" in l for l in flagged), flagged
    if where == "mixed":
        assert not any("Jones" in l for l in flagged), flagged


B_CLEAN = "BASELINE: a clean single-main repo was not green \u2014 gate is over-firing"
B_TAG = "BASELINE: a release tag on main's tip was not green \u2014 tag exemption is over-firing"
ALWAYS_RED_FAILURES = [
    B_CLEAN, B_TAG,
    "FIXTURE RULE 1 (stray ref): fresh repo was not green",
    "FIXTURE RULE 2 (never-publish name): fresh repo was not green",
    "FIXTURE RULE 3 (AI-attribution trailer): fresh repo was not green",
]
PASSED_LINE = ("ref_gate --self-test: PASSED \u2014 all 3 rules provably go red; "
               "tags exempt only on published history; prose/path discrimination holds")


def test_the_self_test_reports_on_an_ascii_stream():
    path = Path(__file__).resolve().parents[2] / "_tools" / "ref_gate.py"
    env = dict(os.environ, PYTHONIOENCODING="ascii:strict", PYTHONUTF8="0")
    result = subprocess.run([sys.executable, str(path), "--self-test"], capture_output=True, env=env)
    out = (result.stdout + result.stderr).decode("ascii", "replace")
    assert b"Traceback" not in result.stdout + result.stderr, out
    assert result.returncode == 0, out
    assert result.stdout == PASSED_LINE.encode("ascii", "backslashreplace") + b"\n", out
    assert result.stderr == b"", out
    # On a stream that can hold it, the line carries the em dash itself, not an escape.
    env = dict(os.environ, PYTHONIOENCODING="utf-8:strict", PYTHONUTF8="0")
    result = subprocess.run([sys.executable, str(path), "--self-test"], capture_output=True, env=env)
    out = (result.stdout + result.stderr).decode("utf-8", "replace")
    assert b"Traceback" not in result.stdout + result.stderr, out
    assert result.returncode == 0, out
    assert result.stdout == PASSED_LINE.encode("utf-8") + b"\n", out
    assert result.stderr == b"", out


def test_a_self_test_failure_is_reported_on_an_ascii_stream(monkeypatch):
    import io
    gate = _gate()
    # A gate that never goes green makes the baseline checks fail; their messages hold an em dash.
    monkeypatch.setattr(gate, "check", lambda *a, **k: 1)
    sink = io.TextIOWrapper(io.BytesIO(), encoding="ascii", errors="strict")
    monkeypatch.setattr(sys, "stdout", sink)
    assert gate.self_test() == 1
    sink.flush()
    out = sink.buffer.getvalue().decode("ascii")
    expected = "ref_gate --self-test: FAILED\n" + "".join(
        "  - " + message + "\n" for message in ALWAYS_RED_FAILURES)
    assert out == expected.encode("ascii", "backslashreplace").decode("ascii"), out


def test_a_self_test_failure_is_reported_on_a_utf8_stream(monkeypatch):
    import io
    gate = _gate()
    monkeypatch.setattr(gate, "check", lambda *a, **k: 1)
    sink = io.TextIOWrapper(io.BytesIO(), encoding="utf-8", errors="strict")
    monkeypatch.setattr(sys, "stdout", sink)
    assert gate.self_test() == 1
    sink.flush()
    out = sink.buffer.getvalue().decode("utf-8")
    assert out == "ref_gate --self-test: FAILED\n" + "".join(
        "  - " + message + "\n" for message in ALWAYS_RED_FAILURES), out


@pytest.mark.parametrize("probe", ["clean", "tag"])
def test_a_baseline_failure_names_only_its_own_probe(monkeypatch, probe):
    import io
    gate = _gate()
    real = gate.check

    def fake(repo, quiet=False):
        if os.path.basename(repo) == "r":
            tags = subprocess.run(["git", "tag", "--list"], cwd=repo, capture_output=True,
                                  text=True, check=True).stdout.split()
            if probe == "clean" and not tags:
                return 1
            if probe == "tag" and tags == ["v0.0.1"]:
                return 1
        return real(repo, quiet=quiet)

    monkeypatch.setattr(gate, "check", fake)
    sink = io.TextIOWrapper(io.BytesIO(), encoding="utf-8", errors="strict")
    monkeypatch.setattr(sys, "stdout", sink)
    assert gate.self_test() == 1
    sink.flush()
    out = sink.buffer.getvalue().decode("utf-8")
    chosen = B_CLEAN if probe == "clean" else B_TAG
    assert out == "ref_gate --self-test: FAILED\n  - " + chosen + "\n", out


def test_a_quiet_check_prints_nothing_on_a_violating_repo(tmp_path, capsys):
    repo, git = _repo(tmp_path)
    git("commit", "-q", "--allow-empty", "-m", "A")
    git("branch", "side")            # a stray ref: the gate must go red
    gate = _gate()
    capsys.readouterr()
    assert gate.check(str(repo), quiet=True) == 1
    captured = capsys.readouterr()
    assert captured.out == "", captured.out
    assert captured.err == "", captured.err
    # Control: the same repo without quiet prints its report.
    assert gate.check(str(repo)) == 1
    assert capsys.readouterr().out.strip() != ""

    # Quiet must hold for every rule's report, not only the stray-ref one.
    for kind in ("object", "trailer"):
        base = tmp_path / kind
        base.mkdir()
        other, ogit = _repo(base)
        ogit("commit", "-q", "--allow-empty", "-m", "A")
        if kind == "object":
            _commit_files(other, ogit, {"pkg/__pycache__/mod.cpython-312.pyc": b"\x00compiled\x00"})
        else:
            ogit("commit", "-q", "--allow-empty", "-m",
                 "chore: thing\n\nCo-Authored-By: Claude <noreply@anthropic.com>")
        capsys.readouterr()
        assert gate.check(str(other), quiet=True) == 1, kind
        captured = capsys.readouterr()
        assert captured.out == "", (kind, captured.out)
        assert captured.err == "", (kind, captured.err)


def test_a_carriage_return_alone_as_the_configured_ref_is_refused(tmp_path, capsys):
    repo, git = _repo(tmp_path)
    git("commit", "-q", "--allow-empty", "-m", "A")
    with open(repo / ".git" / "config", "ab") as config:
        config.write(b'[fleetops]\n\tpublishRef = "\r"\n')
    raw = subprocess.run(["git", "config", "--get", "fleetops.publishRef"], cwd=repo,
                         capture_output=True, check=True).stdout
    assert raw == b"\r\n", raw          # the CR itself reaches the gate's reader
    gate = _gate()
    capsys.readouterr()
    assert gate.check(str(repo)) == 1
    lines = capsys.readouterr().out.split("\n")
    assert "ref_gate: REFUSED fleetops.publishRef must name a full ref beginning with refs/" in lines, lines


RULE_ALONE = {
    "stray": "ref(s) outside the allow-list",
    "object": "never-publish name(s) reachable from --all:",
    "trailer": "AI-attribution trailer(s) in reachable history:",
}
OK_LINES = {
    "stray": "  [OK]   no stray publishable refs",
    "object": "  [OK]   no never-publish names reachable",
    "trailer": "  [OK]   no AI-attribution trailers",
}
RULE_EVIDENCE = {
    "stray": "refs/heads/side",
    "object": "mod.cpython-312.pyc",
    "trailer": "Co-Authored-By: Claude",
}


@pytest.mark.parametrize("rule", sorted(RULE_ALONE))
def test_each_rule_alone_reports_only_its_own_failure(tmp_path, capsys, rule):
    repo, git = _repo(tmp_path)
    git("commit", "-q", "--allow-empty", "-m", "A")
    if rule == "stray":
        git("branch", "side")
    elif rule == "object":
        _commit_files(repo, git, {"pkg/__pycache__/mod.cpython-312.pyc": b"\x00compiled\x00"})
    else:
        git("commit", "-q", "--allow-empty", "-m",
            "chore: thing\n\nCo-Authored-By: Claude <noreply@anthropic.com>")
    gate = _gate()
    capsys.readouterr()
    assert gate.check(str(repo)) == 1
    lines = capsys.readouterr().out.split("\n")
    fails = [line for line in lines if line.startswith("  [FAIL] ")]
    assert len(fails) == 1, lines
    assert fails[0].endswith(RULE_ALONE[rule]) or RULE_ALONE[rule] in fails[0], lines
    for other_rule, text in RULE_ALONE.items():
        if other_rule != rule:
            assert text not in fails[0], lines
    for other, ok in OK_LINES.items():
        if other != rule:
            assert ok in lines, lines
        else:
            assert ok not in lines, lines

    # Assert the whole report: another rule cannot hide on a non-FAIL line,
    # or escape a substring exclusion by paraphrasing its diagnostic.
    trailer = "Co-Authored-By: Claude <noreply@anthropic.com>"
    reports = {
        "stray": [
            "  [FAIL] 1 ref(s) outside the allow-list — pushing them would publish them (push --all sends branches, push --tags sends tags, push --mirror sends every ref):",
            f"         refs/heads/side @ {git('rev-parse', 'HEAD')[:9]}",
        ],
        "object": [],
        "trailer": [
            "  [FAIL] 1 AI-attribution trailer(s) in reachable history:",
            f"         {git('rev-parse', 'HEAD')[:9]}  {trailer}",
        ],
    }
    if rule == "object":
        reports["object"] = [
            "  [FAIL] 2 never-publish name(s) reachable from --all:",
            f"         {git('rev-parse', 'HEAD:pkg')[:9]}  __pycache__",
            f"         {git('rev-parse', 'HEAD:pkg/__pycache__')[:9]}  mod.cpython-312.pyc",
        ]
    expected = [f"ref_gate: {repo}", "  publishable allow-list: ['refs/heads/main']"]
    for name in ("stray", "object", "trailer"):
        expected.extend(reports[name] if name == rule else [OK_LINES[name]])
    expected.extend(["  => VIOLATIONS", ""])
    assert lines == expected, lines


@pytest.mark.parametrize("missing", ["objs", "strays"])
def test_the_self_test_catches_each_trailer_pair_verdict_gap(monkeypatch, capsys, missing):
    gate = _gate()
    real = gate.check

    def misses_pair(repo, quiet=False):
        found = {"strays": bool(gate.stray_refs(repo)),
                 "objs": bool(gate.banned_objects(repo)),
                 "trailers": bool(gate.banned_trailers(repo))}
        if not found[missing] and sum(found.values()) == 2:
            return 0
        return real(repo, quiet=quiet)

    monkeypatch.setattr(gate, "check", misses_pair)
    pair = "stray-trailer" if missing == "objs" else "object-trailer"
    _assert_self_test_failures(gate, capsys, [f"PAIR {pair}: gate did not go red when these two rules were violated"])


def test_reach_reports_both_a_silent_object_detector_and_a_premature_trailer(monkeypatch, capsys):
    gate = _gate()
    real_objects, real_trailers = gate.banned_objects, gate.banned_trailers

    def silent_objects(repo):
        return [] if os.path.basename(repo) == "reach" else real_objects(repo)

    def premature_trailer(repo):
        if os.path.basename(repo) == "reach":
            return [("fixture", "premature trailer")]
        return real_trailers(repo)

    monkeypatch.setattr(gate, "banned_objects", silent_objects)
    monkeypatch.setattr(gate, "banned_trailers", premature_trailer)
    assert gate.self_test() == 1
    lines = capsys.readouterr().out.split("\n")
    assert lines == ["ref_gate --self-test: FAILED",
                     "  - REACH: a .pyc reachable only from a non-checked-out branch did NOT trip rule 2",
                     "  - FIXTURE REACH: the trailer detector fired before any trailer was planted", ""]


@pytest.mark.parametrize("rules", [
    ("stray", "object"), ("stray", "trailer"), ("object", "trailer"),
    ("stray", "object", "trailer"),
])
def test_a_quiet_check_prints_nothing_when_multiple_rules_fire(tmp_path, capsys, rules):
    repo, git = _repo(tmp_path)
    git("commit", "-q", "--allow-empty", "-m", "A")
    if "object" in rules:
        _commit_files(repo, git, {"pkg/__pycache__/mod.cpython-312.pyc": b"\x00compiled\x00"})
    if "trailer" in rules:
        git("commit", "-q", "--allow-empty", "-m",
            "chore: thing\n\nCo-Authored-By: Claude <noreply@anthropic.com>")
    if "stray" in rules:
        git("branch", "side")
    gate = _gate()
    capsys.readouterr()
    assert gate.check(str(repo), quiet=True) == 1
    captured = capsys.readouterr()
    assert captured.out == "", captured.out
    assert captured.err == "", captured.err
    # Positive control: the same fixture really exercises every chosen rule.
    assert gate.check(str(repo)) == 1
    captured = capsys.readouterr()
    assert sum(line.startswith("  [FAIL] ") for line in captured.out.split("\n")) == len(rules)
    fails = [ln for ln in captured.out.split("\n") if ln.startswith("  [FAIL] ")]
    for ln in fails:
        matched = [rule for rule, text in RULE_ALONE.items() if text in ln]
        assert len(matched) == 1, (ln, captured.out)
    # Each fired rule's header must sit above that rule's own detail lines: a check() that exchanged two fired
    # rules' header phrases would still print one phrase per [FAIL] line.
    out_lines = captured.out.split("\n")
    for rule in rules:
        start = next(i for i, ln in enumerate(out_lines)
                     if ln.startswith("  [FAIL] ") and RULE_ALONE[rule] in ln)
        block = []
        for ln in out_lines[start + 1:]:
            if ln.startswith("  [") or ln.startswith("  =>"):
                break
            block.append(ln)
        assert any(RULE_EVIDENCE[rule] in ln for ln in block), (rule, captured.out)
        for other in rules:
            if other != rule:
                assert not any(RULE_EVIDENCE[other] in ln for ln in block), (rule, other, captured.out)
    for rule, text in RULE_ALONE.items():
        fired = [ln for ln in fails if text in ln]
        if rule in rules:
            assert len(fired) == 1, (rule, captured.out)
            assert OK_LINES[rule] not in captured.out, (rule, captured.out)
        else:
            assert fired == [], (rule, captured.out)
            assert OK_LINES[rule] in captured.out, (rule, captured.out)
    # Assert the whole report, as the single-rule test does: a check() that drops a detail line or the verdict
    # only when several rules fire would still carry one evidence string per block.
    head = git("rev-parse", "HEAD")[:9]
    reports = {
        "stray": [
            "  [FAIL] 1 ref(s) outside the allow-list — pushing them would publish them (push --all sends branches, push --tags sends tags, push --mirror sends every ref):",
            f"         refs/heads/side @ {head}",
        ],
        "trailer": [
            "  [FAIL] 1 AI-attribution trailer(s) in reachable history:",
            f"         {head}  Co-Authored-By: Claude <noreply@anthropic.com>",
        ],
    }
    if "object" in rules:
        reports["object"] = [
            "  [FAIL] 2 never-publish name(s) reachable from --all:",
            f"         {git('rev-parse', 'HEAD:pkg')[:9]}  __pycache__",
            f"         {git('rev-parse', 'HEAD:pkg/__pycache__')[:9]}  mod.cpython-312.pyc",
        ]
    expected = [f"ref_gate: {repo}", "  publishable allow-list: ['refs/heads/main']"]
    for name in ("stray", "object", "trailer"):
        expected.extend(reports[name] if name in rules else [OK_LINES[name]])
    expected.extend(["  => VIOLATIONS", ""])
    assert out_lines == expected, out_lines
    assert captured.err == "", captured.err


@pytest.mark.parametrize("stream", ["no-encoding", "none", "ascii"])
def test_stdout_handles_unusual_stream_encodings(monkeypatch, stream):
    gate = _gate()

    class Sink:
        def __init__(self):
            self.parts = []

        def write(self, text):
            if stream == "ascii":
                text.encode("ascii", "strict")
            self.parts.append(text)

    sink = Sink()
    if stream != "no-encoding":
        sink.encoding = None if stream == "none" else "ascii"
    monkeypatch.setattr(sys, "stdout", sink)
    gate._stdout("report café — caf\udcff", 7)
    expected = ("report caf\\xe9 \\u2014 caf\\udcff 7\n" if stream == "ascii"
                else "report café — caf\\udcff 7\n")
    assert "".join(sink.parts) == expected


REACH_OBJECT_LOST = "REACH: the historical .pyc disappeared after the trailer plant — rule 2 is tip-only"
REACH_STRAY_LOST = "REACH: the sidecar stray ref disappeared after the trailer plant — rule 1 is incomplete"
REACH_TRAILER_LOST = "REACH: a Co-Authored-By trailer reachable only from a non-checked-out branch did NOT trip rule 3"
ALL_THREE_LOST = "ALL THREE: a repo violating all three rules at once did not turn the gate red"
POST_FAILURE = {"banned_objects": REACH_OBJECT_LOST, "stray_refs": REACH_STRAY_LOST,
                "banned_trailers": REACH_TRAILER_LOST}


def _reach_has_trailer(gate, repo):
    return (os.path.basename(repo) == "reach"
            and "Co-Authored-By:" in gate.git(["log", "--all", "--format=%B"], repo))


def _assert_self_test_failures(gate, capsys, expected):
    assert gate.self_test() == 1
    captured = capsys.readouterr()
    assert captured.out.split("\n") == ["ref_gate --self-test: FAILED",
                                       *["  - " + line for line in expected], ""]
    assert captured.err == ""


@pytest.mark.parametrize("detector", sorted(POST_FAILURE))
def test_each_post_trailer_recheck_names_exactly_its_missing_detector(monkeypatch, capsys, detector):
    gate = _gate()
    real = getattr(gate, detector)

    def disappears(repo, **kwargs):
        return [] if _reach_has_trailer(gate, repo) else real(repo, **kwargs)

    monkeypatch.setattr(gate, detector, disappears)
    _assert_self_test_failures(gate, capsys, [POST_FAILURE[detector]])


@pytest.mark.parametrize("missing", [None, *sorted(POST_FAILURE)])
def test_all_three_verdict_is_checked_only_when_all_detectors_fire(monkeypatch, capsys, missing):
    gate = _gate()
    real_check = gate.check
    if missing:
        real_detector = getattr(gate, missing)

        def disappears(repo, **kwargs):
            return [] if _reach_has_trailer(gate, repo) else real_detector(repo, **kwargs)

        monkeypatch.setattr(gate, missing, disappears)

    def false_green(repo, quiet=False):
        return 0 if _reach_has_trailer(gate, repo) else real_check(repo, quiet=quiet)

    monkeypatch.setattr(gate, "check", false_green)
    _assert_self_test_failures(gate, capsys,
                               [POST_FAILURE[missing]] if missing else [ALL_THREE_LOST])


@pytest.mark.parametrize("detector,expected", [
    ("banned_objects", "REACH: a .pyc reachable only from a non-checked-out branch did NOT trip rule 2"),
    ("stray_refs", "FIXTURE PAIR: the sidecar branch was not reported as a stray ref"),
])
def test_a_detector_that_never_fired_is_not_reported_as_disappearing(monkeypatch, capsys, detector, expected):
    gate = _gate()
    real = getattr(gate, detector)

    def silent(repo, **kwargs):
        return [] if os.path.basename(repo) == "reach" else real(repo, **kwargs)

    monkeypatch.setattr(gate, detector, silent)
    _assert_self_test_failures(gate, capsys, [expected])


@pytest.mark.parametrize("pair,detector,expected", [
    ("stray-trailer", "stray_refs",
     "FIXTURE PAIR stray-trailer: expected ['strays', 'trailers'], found ['trailers']"),
    ("object-trailer", "banned_objects",
     "FIXTURE PAIR object-trailer: expected ['objs', 'trailers'], found ['trailers']"),
])
def test_a_pair_fixture_reports_exactly_its_wrong_detector_set(monkeypatch, capsys, pair, detector, expected):
    gate = _gate()
    real = getattr(gate, detector)

    def silent(repo, **kwargs):
        return [] if os.path.basename(repo) == pair else real(repo, **kwargs)

    monkeypatch.setattr(gate, detector, silent)
    _assert_self_test_failures(gate, capsys, [expected])


@pytest.mark.parametrize("scanner", ["no-walk", "ls-tree", "side-tip-trailers", "tip-trailers"])
def test_the_self_test_rejects_ref_tip_only_content_scanners(monkeypatch, capsys, scanner):
    gate = _gate()
    if scanner == "no-walk":
        real_git = gate.git

        def no_walk(args, repo):
            if args[:3] == ["rev-list", "--all", "--objects"]:
                args = args[:2] + ["--no-walk"] + args[2:]
            return real_git(args, repo)

        monkeypatch.setattr(gate, "git", no_walk)
    elif scanner == "ls-tree":
        def tip_objects(repo):
            hits = []
            for ref in gate.git(["for-each-ref", "--format=%(refname)"], repo).split():
                for entry in gate.git_bytes(["ls-tree", "-rz", ref], repo).split(b"\0"):
                    if entry:
                        header, path = entry.split(b"\t", 1)
                        name = os.fsdecode(path)
                        if gate.BANNED_PATH.search(name):
                            hits.append((header.decode().split()[-1][:9], name))
            return hits

        monkeypatch.setattr(gate, "banned_objects", tip_objects)
    elif scanner == "side-tip-trailers":
        # The global tip-message mutant already fails the round-1 object-trailer
        # pair. Keep main's history so this tests the still-uncovered side ref.
        def side_tip_trailers(repo):
            bodies = [gate.git(["log", "HEAD", "--format=%B"], repo)]
            for ref in gate.git(["for-each-ref", "--format=%(refname)"], repo).split():
                bodies.append(gate.git(["log", "-1", "--format=%B", ref], repo))
            return [("tip", m.group(0)) for body in bodies for m in gate.trailer_matches(body)]

        monkeypatch.setattr(gate, "banned_trailers", side_tip_trailers)
    else:
        # The literal R3(c) mutant: only each ref tip's own message is read.
        def tip_trailers(repo):
            bodies = [gate.git(["log", "-1", "--format=%B", ref], repo)
                      for ref in gate.git(["for-each-ref", "--format=%(refname)"], repo).split()]
            return [("tip", m.group(0)) for body in bodies for m in gate.trailer_matches(body)]

        monkeypatch.setattr(gate, "banned_trailers", tip_trailers)
    expected = {
        "no-walk": [REACH_OBJECT_LOST],
        "ls-tree": [REACH_OBJECT_LOST],
        "side-tip-trailers": [REACH_TRAILER_LOST],
        "tip-trailers": ["FIXTURE PAIR object-trailer: expected ['objs', 'trailers'], found ['objs']",
                         REACH_TRAILER_LOST],
    }[scanner]
    _assert_self_test_failures(gate, capsys, expected)


@pytest.mark.parametrize("broken", ["object", "both"])
def test_reach_preconditions_are_checked_independently_of_detectors(monkeypatch, capsys, broken):
    gate = _gate()
    real_git = gate.git

    def leave_tip_dirty(args, repo):
        if os.path.basename(repo) == "reach":
            if args[:2] == ["rm", "-q"]:
                return ""
            # Two marker commits still land, so the trailer commit sits two below the tip:
            # only a check that reads at least three commits of each ref (rev-list -n 3) reports it.
            if broken == "both" and args[-1:] == ["add: marker 1"]:
                return ""
            if args[-1:] == ["remove: compiled artifact"]:
                if broken == "both":
                    return ""
                args = args[:5] + ["--allow-empty"] + args[5:]
        return real_git(args, repo)

    monkeypatch.setattr(gate, "git", leave_tip_dirty)
    expected = ["FIXTURE REACH: a ref tip tree contains a never-publish name"]
    if broken == "both":
        expected.append("FIXTURE REACH: the trailer commit is within the first 3 commits of a ref")
    _assert_self_test_failures(gate, capsys, expected)
