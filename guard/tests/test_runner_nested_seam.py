"""Cheap pytest seam for the nested runner; also collected by the outer suite."""
from pathlib import Path


def test_runner_script_exists_and_is_a_bash_entry_point():
    runner = Path(__file__).resolve().parents[1] / "run_guards.sh"
    assert runner.is_file()
    assert runner.read_text(encoding="utf-8").startswith("#!/usr/bin/env bash\n")
