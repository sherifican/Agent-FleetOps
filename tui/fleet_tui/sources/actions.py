"""Owner 'hand off' control — route a pending alert to whoever's responsible for it.

The TUI can't ACTION an alert itself (monitor, not orchestrator), so hand-off writes the alert to a
Claude action-queue (`~/.claude/curation/.action_requests`, JSON-lines). The curation_reminder hook
surfaces that queue on the next orchestrator turn, so Claude picks it up and either actions it or routes
it to the responsible agent/script. Owner-initiated only (a button press), never the refresh loop.
"""
import json
import os
import time

ACTION_QUEUE = os.path.expanduser("~/.claude/curation/.action_requests")


def request_action(source: str, title: str, detail: str = "") -> bool:
    """Queue a pending alert for Claude to action/route. Appends one JSON line; returns True on success.
    Deduped by (source,title): re-handing-off the same alert won't pile up duplicates."""
    try:
        rec = {"ts": time.strftime("%Y-%m-%d %H:%M"), "source": str(source),
               "title": str(title)[:200], "detail": str(detail or "")[:500], "status": "requested"}
        existing = []
        if os.path.exists(ACTION_QUEUE):
            for ln in open(ACTION_QUEUE, encoding="utf-8"):
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    existing.append(json.loads(ln))
                except Exception:
                    pass
        # drop any prior open request for the same (source,title) — this one supersedes it
        existing = [e for e in existing
                    if not (e.get("source") == rec["source"] and e.get("title") == rec["title"])]
        existing.append(rec)
        os.makedirs(os.path.dirname(ACTION_QUEUE), exist_ok=True)
        with open(ACTION_QUEUE, "w", encoding="utf-8") as f:
            for e in existing:
                f.write(json.dumps(e) + "\n")
        return True
    except Exception:
        return False


def pending_count() -> int:
    """How many alerts are currently handed-off / awaiting Claude action. 0 on any error."""
    try:
        if not os.path.exists(ACTION_QUEUE):
            return 0
        return sum(1 for ln in open(ACTION_QUEUE, encoding="utf-8") if ln.strip())
    except Exception:
        return 0
