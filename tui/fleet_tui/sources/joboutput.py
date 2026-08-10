"""Recent per-job output for the JOBS drill-in — the tail of each Hermes cron job's latest output file.

Hermes writes cron output to ~/.hermes/cron/output/<job_id>/<timestamp> ; this surfaces the tail so you
can see WHAT a job did, not just that it ran. Pure/safe: returns [] / "" on any error.
"""
import glob
import os

OUTPUT_DIR = os.path.expanduser("~/.hermes/cron/output")


def job_output_tail(job_id, n=12) -> str:
    """Tail (last n lines) of a Hermes job's most-recent output file; '' if none/unreadable."""
    if not job_id:
        return ""
    try:
        files = sorted(glob.glob(os.path.join(OUTPUT_DIR, job_id, "*")), key=os.path.getmtime)
        if not files:
            return ""
        lines = open(files[-1], errors="replace").read().splitlines()
        return "\n".join(ln.rstrip() for ln in lines[-n:]).strip()
    except Exception:
        return ""


def recent_outputs(jobs_list) -> list:
    """[{name, tail}] for the jobs that actually have output on disk."""
    out = []
    for j in jobs_list:
        tail = job_output_tail(getattr(j, "id", "") or "")
        if tail:
            out.append({"name": j.name, "tail": tail})
    return out
