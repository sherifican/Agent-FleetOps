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
