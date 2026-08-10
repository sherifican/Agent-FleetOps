"""Tests for the jobs source."""

import os
import json
import pytest
from fleet_tui.sources.jobs import (
    read_hermes_jobs,
    parse_crontab,
    build_jobs,
    _hermes_schedule,
    _cron_name
)
from fleet_tui.models import Job

FIXTURES_PATH = os.path.join(os.path.dirname(__file__), "fixtures")

def test_build_hermes_jobs():
    jobs_path = os.path.join(FIXTURES_PATH, "jobs.json")
    with open(jobs_path, "r") as f:
        data = json.load(f)
    
    result = build_jobs(data["jobs"], "")
    
    assert len(result) == len(data["jobs"])
    for job in result:
        assert job.kind == "cron"
        assert job.name != ""
        
def test_hermes_schedule_populated():
    jobs_path = os.path.join(FIXTURES_PATH, "jobs.json")
    with open(jobs_path, "r") as f:
        data = json.load(f)
    
    result = build_jobs(data["jobs"], "")
    
    # At least one job should have a non-empty schedule
    assert any(job.schedule != "" for job in result)

def test_build_system_crons():
    crontab_path = os.path.join(FIXTURES_PATH, "crontab.txt")
    with open(crontab_path, "r") as f:
        text = f.read()
    
    result = build_jobs([], text)
    
    # Should have one job per non-comment, non-blank line
    lines = [line for line in text.splitlines() if line.strip() and not line.startswith("#")]
    assert len(result) == len(lines)
    
    for job in result:
        assert job.kind == "systemcron"
        assert job.schedule != ""
        assert job.name != ""

def test_parse_crontab_skips_comments():
    text = "# comment\n\n0 0 * * * /bin/x\n"
    result = parse_crontab(text)
    assert len(result) == 1
    assert result[0]["schedule"] == "0 0 * * *"
    assert result[0]["command"] == "/bin/x"

def test_build_jobs_empty():
    result = build_jobs([], "")
    assert result == []

def test_malformed_hermes_job():
    result = build_jobs([{"name":"x"}], "")
    assert len(result) == 1
    job = result[0]
    assert job.name == "x"
    assert job.kind == "cron"
    assert job.schedule == ""

def test_read_hermes_jobs_bad_path():
    result = read_hermes_jobs("/nonexistent/path.json")
    assert result == []


def test_cron_name_real_patterns():
    """The screenshot bug: interpreter/PATH= prefixes must not become the job name."""
    assert _cron_name("/usr/bin/python3 ~/fleet_monitor.py 1 > /dev/null") == "fleet_monitor.py"
    assert _cron_name("/usr/bin/python3 ~/ollama_watchdog.py") == "ollama_watchdog.py"
    assert _cron_name("PATH=~/.local/bin:/usr/bin ~/.local/bin/o2b brain dream >> /x.log") == "o2b"
    assert _cron_name("PATH=~/.local/bin:/bin ~/fleet_optests/hf_watch_pass.sh >> /x.log") == "hf_watch_pass.sh"
    assert _cron_name(os.path.expanduser("~/.local/bin/localllm-monthly-sweep.sh")) == "localllm-monthly-sweep.sh"
    assert _cron_name("/usr/bin/env python3 ~/x.py") == "x.py"
    assert _cron_name("") == "cron"


def test_hermes_running_state():
    """A Hermes job with fire_claim set OR state=running → Job.running True (drives the ▶ indicator)."""
    running = build_jobs([{"id": "1", "name": "x", "fire_claim": "abc"}], "")
    assert running[0].running is True
    running2 = build_jobs([{"id": "2", "name": "y", "state": "running"}], "")
    assert running2[0].running is True
    idle = build_jobs([{"id": "3", "name": "z", "state": "scheduled"}], "")
    assert idle[0].running is False


def test_human_interval():
    from fleet_tui.sources.jobs import _human_interval
    assert _human_interval(20) == "every 20m"        # <60m stays minutes
    assert _human_interval(60) == "every 1hr"        # singular
    assert _human_interval(180) == "every 3hrs"
    assert _human_interval(1440) == "every 24hrs (1 Day)"   # owner's example
    assert _human_interval(10080) == "every 168hrs (7 Days)"
