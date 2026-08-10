"""Morning fleet digest — gather → condense via local LLM → optional Telegram push.

One scannable message instead of opening the TUI: 24h job/dispatch/feedback/alert/health
state, condensed by gemma4:12b, optionally pushed to Telegram. Every function is defensive.
"""

from __future__ import annotations

import json
import subprocess
import urllib.request

from fleet_tui.sources import dispatch, health, inbox, jobs

LOCAL_LLM_URL = "http://localhost:11434/api/chat"


def build_digest() -> dict:
    """Gather last-24h fleet state. Never raises — each source is wrapped.

    Returns ``{"raw": str, "counts": dict}``.
    """
    job_ok = job_fail = 0
    job_running: list[str] = []
    failed_jobs: list[str] = []
    try:
        for j in jobs.list_jobs():
            if getattr(j, "running", False):
                job_running.append(getattr(j, "name", "unnamed"))
            status = getattr(j, "last_status", "unknown") or "unknown"
            if status == "ok":
                job_ok += 1
            elif status == "fail":
                job_fail += 1
                failed_jobs.append(getattr(j, "name", "unnamed"))
    except Exception:
        pass

    recent_count = running_dispatches = 0
    try:
        for d in dispatch.recent(limit=12):
            recent_count += 1
            if isinstance(d, dict) and d.get("running"):
                running_dispatches += 1
    except Exception:
        pass

    open_feedback = 0
    try:
        open_feedback = len(dispatch.open_feedback_debts())
    except Exception:
        pass

    pending_alerts: list[tuple] = []
    try:
        for it in inbox.list_inbox():
            if getattr(it, "pending", False):
                pending_alerts.append((getattr(it, "source", "?"), getattr(it, "title", "")))
    except Exception:
        pass

    down_services: list[str] = []
    disk_free_gb = disk_total_gb = None
    uptime = None
    try:
        snap = health.snapshot()  # HealthSnapshot dataclass — attribute access, NOT .get()
        for name, up in (getattr(snap, "services", {}) or {}).items():
            if not up:
                down_services.append(name)
        disk_free_gb = getattr(snap, "disk_free_gb", None)
        disk_total_gb = getattr(snap, "disk_total_gb", None)
        uptime = getattr(snap, "uptime", None)
    except Exception:
        pass

    counts = {
        "jobs_ok": job_ok,
        "jobs_fail": job_fail,
        "jobs_running": len(job_running),
        "dispatches_recent": recent_count,
        "dispatches_running": running_dispatches,
        "open_feedback_debts": open_feedback,
        "pending_alerts": len(pending_alerts),
        "services_down": len(down_services),
    }

    lines = [
        "=== Fleet fleet state (24h) ===",
        f"jobs: ok={job_ok} fail={job_fail} running={len(job_running)}",
    ]
    if failed_jobs:
        lines.append("failed jobs: " + ", ".join(sorted(set(failed_jobs))))
    lines.append(f"dispatches: recent={recent_count} running={running_dispatches}")
    lines.append(f"feedback due: {open_feedback}")
    if pending_alerts:
        lines.append(f"alerts ({len(pending_alerts)}):")
        for src, title in pending_alerts:
            lines.append(f"  * [{src}] {title}")
    lines.append("services DOWN: " + ", ".join(down_services) if down_services else "services: all up")
    if disk_free_gb is not None and disk_total_gb is not None:
        lines.append(f"disk: {disk_free_gb}/{disk_total_gb} GB free")
    if uptime:
        lines.append(f"uptime: {uptime}")

    return {"raw": "\n".join(lines), "counts": counts}


def condense(raw: str) -> str:
    """Ask gemma4:12b to compress ``raw`` into a scannable digest. Returns ``raw`` on any error."""
    system = (
        "You are Fleet's ops assistant. Compress this fleet-state dump into a short, "
        "scannable digest — <=15 lines, emoji ok. LEAD with anything urgent needing owner action."
    )
    body = json.dumps({
        "model": "gemma4:12b",
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": raw},
        ],
        "stream": False,
    }).encode("utf-8")
    req = urllib.request.Request(LOCAL_LLM_URL, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=150) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        content = (payload.get("message") or {}).get("content")
        return content if isinstance(content, str) and content.strip() else raw
    except Exception:
        return raw


def send_telegram(text: str) -> bool:
    """Push ``text`` to Telegram via the hermes CLI. True iff rc 0; never raises."""
    cmd = ["hermes", "send", "--to", "telegram", "--subject", "☀️ Fleet morning digest", text]
    try:
        return subprocess.run(cmd, capture_output=True, timeout=60, check=False).returncode == 0
    except Exception:
        return False


def run_digest(send: bool = False) -> dict:
    """build → condense → (optional) send. Never raises."""
    try:
        gathered = build_digest()
        condensed = condense(gathered["raw"])
        sent = bool(send and send_telegram(condensed))
        return {"ok": True, "text": condensed, "sent": sent, "counts": gathered.get("counts", {})}
    except Exception as exc:
        return {"ok": False, "reason": str(exc)}
