"""Pure readers for the jobs source."""

from fleet_tui.models import Job
import json
import os
import subprocess
import time

HERMES_JOBS_PATH = os.path.expanduser("~/.hermes/cron/jobs.json")


def read_hermes_jobs(path=HERMES_JOBS_PATH) -> list:
    """Read Hermes jobs from JSON file, return safe default on any error."""
    try:
        with open(path, "r") as f:
            data = json.load(f)
        return data.get("jobs", [])
    except Exception:
        return []


def run_now(job_id) -> bool:
    """Trigger a Hermes cron job to fire on the next scheduler tick (`hermes cron run <id>`). Owner-initiated,
    thin runner over the existing command — NOT orchestration. Returns True on success; only works for Hermes
    jobs (which have an id), not raw system crons."""
    if not job_id:
        return False
    try:
        r = subprocess.run(["hermes", "cron", "run", str(job_id)],
                           capture_output=True, text=True, timeout=15)
        return r.returncode == 0
    except Exception:
        return False


def run_system_cron(command) -> bool:
    """Manually trigger a SYSTEM crontab job now — run its command line detached/non-blocking via `bash -c`
    so any `PATH=…` prefix + redirects in the line still apply (replicates how cron would run it). `command`
    is the owner's own crontab line (read from `crontab -l`), not external input. Returns True on launch.
    This is the run-now path for raw crontab jobs (no Hermes id → `hermes cron run` can't reach them)."""
    if not command:
        return False
    try:
        subprocess.Popen(["bash", "-c", command],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         stdin=subprocess.DEVNULL, start_new_session=True)
        return True
    except Exception:
        return False


_crontab_cache = {}   # tiny time-cache so the 1s refresh doesn't spawn `crontab -l` every tick


def read_crontab() -> str:
    """Read crontab, return safe default on any error. Cached ~8s (crontab rarely changes)."""
    now = time.time()
    hit = _crontab_cache.get("c")
    if hit is not None and (now - hit[0]) < 8.0:
        return hit[1]
    try:
        result = subprocess.run(
            ["crontab", "-l"],
            capture_output=True,
            text=True,
            timeout=5
        )
        val = result.stdout
    except Exception:
        val = ""
    _crontab_cache["c"] = (now, val)
    return val


def _human_interval(minutes) -> str:
    """Humanize an interval: <60m stays minutes; 60m–1439m → hours; ≥1440m → hours + (days).
    e.g. 60→'every 1hr', 180→'every 3hrs', 1440→'every 24hrs (1 Day)', 10080→'every 168hrs (7 Days)'."""
    try:
        m = int(minutes)
    except (TypeError, ValueError):
        return f"every {minutes}m"
    if m < 60:
        return f"every {m}m"
    hrs = m / 60
    hrs_s = str(int(hrs)) if hrs == int(hrs) else f"{hrs:.1f}"
    if m < 1440:
        return f"every {hrs_s}hr" + ("" if hrs == 1 else "s")
    days = m / 1440
    days_s = str(int(days)) if days == int(days) else f"{days:.1f}"
    return f"every {int(hrs)}hrs ({days_s} Day" + ("" if days == 1 else "s") + ")"


def _hermes_schedule(schedule) -> str:
    """Human schedule string from a Hermes job's schedule dict — interval crons humanized past 59m → hrs/days."""
    if not isinstance(schedule, dict):
        return ""
    if schedule.get("kind") == "interval":
        return _human_interval(schedule.get("minutes", 0))
    return schedule.get("display", "")


_INTERPRETERS = {"python", "python2", "python3", "bash", "sh", "zsh", "env", "node", "ruby", "perl"}

def _cron_name(command) -> str:
    """Extract a friendly job name from a crontab command line.

    Skips leading env-var assignments (e.g. PATH=...) and interpreters (python3/bash/env/...) so the
    name is the actual SCRIPT (fleet_monitor.py, o2b, hf_watch_pass.sh) rather than "python3" or "bin".
    """
    if not command:
        return "cron"
    tokens = command.split()
    if not tokens:
        return "cron"
    for token in tokens:
        if "=" in token:                       # env assignment like PATH=... — skip
            continue
        base = os.path.basename(token)
        if base in _INTERPRETERS:              # interpreter — skip to the real script it runs
            continue
        return base
    # everything was env/interpreter — fall back to the first non-assignment token's basename
    for token in tokens:
        if "=" not in token:
            return os.path.basename(token)
    return "cron"


def parse_crontab(text) -> list[dict]:
    """Parse crontab text into schedule/command pairs."""
    result = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 5)
        if len(parts) < 6:
            continue
        schedule = " ".join(parts[:5])
        command = parts[5]
        result.append({"schedule": schedule, "command": command})
    return result


def build_jobs(hermes_jobs: list, cron_text: str) -> list[Job]:
    """Build list of Job records from Hermes jobs and crontab."""
    jobs = []
    
    # Add Hermes jobs (jobs.json tracks real last_status / next_run / last_run / running-state)
    for j in hermes_jobs:
        running = bool(j.get("fire_claim")) or (str(j.get("state", "")).lower() in ("running", "firing", "active", "executing"))
        job = Job(
            id=j.get("id", ""),
            name=j.get("name", "?"),
            kind="cron",
            schedule=_hermes_schedule(j.get("schedule", {}) if isinstance(j.get("schedule"), dict) else {}),
            next_run=j.get("next_run_at", "") or "",
            last_run=j.get("last_run_at", "") or "",
            last_status=(j.get("last_status") or "unknown"),
            running=running,
        )
        jobs.append(job)
    
    # Add system cron jobs
    for c in parse_crontab(cron_text):
        job = Job(
            id="",
            name=_cron_name(c["command"]),
            kind="systemcron",
            schedule=c["schedule"],
            last_status="unknown",
            command=c["command"],
        )
        jobs.append(job)
    
    return jobs


def list_jobs() -> list[Job]:
    """Convenience function to get all jobs."""
    return build_jobs(read_hermes_jobs(), read_crontab())
