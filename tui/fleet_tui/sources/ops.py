"""Pure readers for the ops source."""

import os
from datetime import datetime
import time

from fleet_tui.models import OpsItem
from fleet_tui.sources import jobs
from fleet_tui.sources import dispatch


def _job_to_opsitem(job) -> OpsItem | None:
    """Convert a Job to an OpsItem. Returns None for invalid jobs."""
    try:
        if job is None:
            return None

        # Determine id
        if getattr(job, "id", ""):
            job_id = f"loop:{job.id}"
        else:
            # For systemcron jobs, create deterministic id from name
            job_name = getattr(job, "name", "") or ""
            job_id = f"loop:sys:{job_name}"

        # Determine title
        title = getattr(job, "name", "") or "?"

        # Determine status
        if getattr(job, "running", False):
            status = "running"
        elif ((getattr(job, "last_status", "") or "").lower() == "ok"):
            status = "ok"
        elif ((getattr(job, "last_status", "") or "").lower() == "fail"):
            status = "fail"
        elif getattr(job, "next_run", "") or getattr(job, "kind", "") == "systemcron":
            status = "scheduled"
        else:
            status = "idle"

        # Parse last_run
        last_run_str = getattr(job, "last_run", "")
        last_run = None
        if last_run_str:
            try:
                last_run = datetime.fromisoformat(last_run_str).timestamp()
            except Exception:
                pass

        # Build detail
        schedule = getattr(job, "schedule", "") or ""
        last_line = getattr(job, "last_line", "") or ""
        if schedule and last_line:
            detail = f"{schedule} · {last_line}"
        elif schedule:
            detail = schedule
        elif last_line:
            detail = last_line
        else:
            detail = getattr(job, "last_status", "") or ""

        return OpsItem(
            id=job_id,
            kind="loop",
            title=title,
            status=status,
            last_run=last_run,
            detail=detail,
            source_ref=job
        )
    except Exception:
        return None


def _dispatch_to_opsitem(d) -> OpsItem | None:
    """Convert a dispatch dict to an OpsItem. Returns None for invalid dispatches."""
    try:
        if not isinstance(d, dict):
            return None

        # Determine id
        base = d.get("base", "") or ""
        if base:
            job_id = f"dispatch:{base}"
        else:
            when = d.get("when", "") or ""
            leg = d.get("leg", "?") or "?"
            job_id = f"dispatch:{when}-{leg}"

        # Determine title
        title = d.get("leg", "?") or "?"

        # Determine status
        running = d.get("running", False)
        status = "running" if running else "idle"

        # Parse last_run
        when = d.get("when", "")
        last_run = None
        if when:
            try:
                last_run = time.mktime(time.strptime(when, "%Y%m%d-%H%M%S"))
            except Exception:
                pass

        # Build detail
        tail = d.get("tail", "") or ""
        brief = d.get("brief", "") or ""
        detail = tail or brief
        
        # Check for feedback debt
        if base:  # Only check if a valid base name is present
            try:
                feedback_file = os.path.join(dispatch.DISPATCH_DIR, f"{base}.feedback_required")
                done_file = os.path.join(dispatch.DISPATCH_DIR, f"{base}.done")
                # If there's an open feedback debt AND output is ready, prepend the flag
                if os.path.exists(feedback_file) and os.path.exists(done_file):
                    detail = f"FEEDBACK DUE · {detail}"
            except Exception:
                # Defensive: if feedback debt can't be checked, just return the original detail
                pass

        # Add stale marker if needed
        if d.get("stale"):
            age = d.get("age")
            mins = int(age // 60) if isinstance(age, (int, float)) else None
            tag = ("STALE " + str(mins) + "m") if mins is not None else "STALE"
            detail = "⚠ " + tag + " · " + detail

        return OpsItem(
            id=job_id,
            kind="dispatch",
            title=title,
            status=status,
            last_run=last_run,
            detail=detail,
            source_ref=d
        )
    except Exception:
        return None


def build_ops(jobs=None, dispatches=None, research=None) -> list:
    """Pure (no I/O) — aggregates already-fetched fleet_tui.models.Job records and dispatch dicts into one sorted list.
    Sort: running items first, then by last_run descending (most recent first); unknown last_run (None)
    sorts to the end of its status group. Never raises."""
    if jobs is None:
        jobs = []
    if dispatches is None:
        dispatches = []
    if research is None:
        research = []

    items = []

    # Process jobs
    for job in jobs:
        opsitem = _job_to_opsitem(job)
        if opsitem is not None:
            items.append(opsitem)

    # Process dispatches
    for d in dispatches:
        opsitem = _dispatch_to_opsitem(d)
        if opsitem is not None:
            items.append(opsitem)

    # Process research (extension seam)
    for r in research:
        if isinstance(r, OpsItem):
            items.append(r)

    # Sort: running items first, then by last_run descending
    items.sort(key=lambda i: (0 if i.status == "running" else 1, -(i.last_run if i.last_run is not None else float("-inf"))))

    return items


def ops_snapshot() -> list:
    """Convenience wrapper matching the status()/snapshot() pattern in health.py/network.py: calls
    jobs.list_jobs() and dispatch.recent() (the real I/O readers), each wrapped in its own try/except
    returning [] on failure, then returns build_ops(jobs_list, dispatches_list). Must return [] and
    never raise even if jobs.list_jobs() or dispatch.recent() themselves raise an exception."""
    try:
        jobs_list = jobs.list_jobs()
    except Exception:
        jobs_list = []

    try:
        dispatches_list = dispatch.recent()
    except Exception:
        dispatches_list = []

    return build_ops(jobs_list, dispatches_list)


def filter_ops(items, filt, text=""):
    """Return the OpsItems that match the active Ops filter AND (if given) a case-insensitive substring
    `text` matched against the item's title/detail/id (filter-as-you-type). Pure; never raises; drops None."""
    _CLOUD_NAMES = ("codex", "grok", "kimi", "gpt", "claude", "gemini", "o1", "o3")
    needle = (text or "").strip().lower()
    
    def _is_cloud_title(title):
        t = (title or "").lower()
        return any(name in t for name in _CLOUD_NAMES)
    
    out = []
    for it in items or []:
        if it is None:
            continue
        try:
            match filt:
                case "all":
                    keep = True
                case "running":
                    keep = it.status == "running"
                case "fail":
                    keep = it.status == "fail"
                case "feedback-due":
                    keep = "FEEDBACK DUE" in (it.detail or "")
                case "stale":
                    # Check if stale by source_ref dict or detail
                    is_stale_by_ref = (isinstance(it.source_ref, dict) and 
                                       it.source_ref.get("stale"))
                    is_stale_by_detail = "STALE" in (it.detail or "")
                    keep = is_stale_by_ref or is_stale_by_detail
                case "cloud":
                    keep = it.kind == "dispatch" and _is_cloud_title(it.title)
                case "local":
                    keep = it.kind in ("dispatch", "job") and not _is_cloud_title(it.title)
                case "cron":
                    keep = it.kind == "loop"
                case _:
                    keep = True  # Safe default for unknown filters
            
            if keep and needle:
                hay = f"{getattr(it,'title','')} {getattr(it,'detail','')} {getattr(it,'id','')}".lower()
                keep = needle in hay
            if keep:
                out.append(it)
        except Exception:
            continue
    return out
