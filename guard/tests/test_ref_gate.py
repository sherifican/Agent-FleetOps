"""Publishing-ref regression: exercise the shipped gate against real Git refs."""
import importlib.util
from pathlib import Path
import subprocess

import pytest


@pytest.mark.parametrize("configured,branch,expected", [
    (None, "main", 0), ("refs/heads/release", "release", 0),
    ("refs/heads/release", "main", 1), ("refs/heads/release", "other", 1),
])
def test_publish_ref_configuration(tmp_path, configured, branch, expected):
    path = Path(__file__).resolve().parents[2] / "_tools" / "ref_gate.py"
    spec = importlib.util.spec_from_file_location("ref_gate", path)
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)
    repo = tmp_path / "refs"
    def git(*args):
        return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
    repo.mkdir()
    git("init", "-q", "-b", branch)
    git("-c", "user.name=fixture", "-c", "user.email=fixture" + "@" + "example.invalid",
        "commit", "-q", "--allow-empty", "-m", "fixture")
    if configured is not None:
        git("config", "fleetops.publishRef", configured)
    assert gate.check(str(repo), quiet=True) == expected


@pytest.mark.parametrize("configured,branch,expected", [
    ("", "main", 0), ("  \t ", "main", 0),
    ("main", "main", 1),
    ("  refs/heads/release \t", "release", 0),
    ("refs/heads/release", "release", 0),
    ("refs/heads/bad name", "main", 1),
    ("refs/original/refs/heads/main", "main", 1),
], ids=["empty", "whitespace-only", "short-ref", "trimmed-ref", "full-ref",
        "invalid-format", "rewrite-leftover"])
def test_publish_ref_edge_cases(tmp_path, configured, branch, expected):
    """Drive the actual CLI: malformed policy must refuse without a traceback."""
    import sys
    repo = tmp_path / "refs"
    repo.mkdir()
    def git(*args):
        return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
    git("init", "-q", "-b", branch)
    git("-c", "user.name=fixture", "-c", "user.email=fixture" + "@" + "example.invalid",
        "commit", "-q", "--allow-empty", "-m", "fixture")
    git("config", "fleetops.publishRef", configured)
    path = Path(__file__).resolve().parents[2] / "_tools" / "ref_gate.py"
    result = subprocess.run([sys.executable, str(path), str(repo)],
                            capture_output=True, text=True)
    assert result.returncode == expected
    assert "Traceback" not in result.stdout + result.stderr
    if expected:
        assert len((result.stdout + result.stderr).splitlines()) == 1
        assert "fleetops.publishRef" in result.stdout + result.stderr
