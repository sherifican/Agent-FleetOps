"""Exercise the shipped process-only example, including its absence control."""
from pathlib import Path
import os
import shutil
import subprocess
import sys


def test_minimal_example_check_config(tmp_path):
    root = Path(__file__).resolve().parents[2]
    settings = tmp_path / "settings" / "honesty_gate.config.json"
    settings.parent.mkdir()
    shutil.copyfile(root / "guard/honesty_gate.config.minimal.example.json", settings)
    # Deterministic command availability: process probes resolve; optional
    # service/container commands do not. No probe is executed by --check-config.
    binaries = tmp_path / "bin"
    binaries.mkdir()
    for command in ("ps", "pgrep"):
        executable = binaries / command
        executable.write_text("#!/bin/sh\nexit 0\n")
        executable.chmod(0o755)
    env = dict(os.environ, PATH=str(binaries), HONESTY_GATE_CONFIG=str(settings))
    def check():
        return subprocess.run(
            [sys.executable, str(root / "guard/honesty_stop_gate.py"), "--check-config"],
            capture_output=True, text=True, env=env)
    present = check()
    assert present.returncode == 0, present.stdout + present.stderr
    assert "check-config: OK" in present.stdout
    assert "3 verification command(s) resolve" in present.stdout
    settings.unlink()
    absent = check()
    assert absent.returncode == 1, absent.stdout + absent.stderr
    assert "check-config: PROBLEMS" in absent.stdout
    assert "does not resolve" in absent.stdout
