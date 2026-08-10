"""Recent FAILED tool calls from Hermes state.db — WHAT tool, WHO (model), WHEN, and the TASK context.

Read-only query of ~/.hermes/state.db (the same source fleet_monitor.py uses). A tool call is a 'fail'
only by its JSON success/error KEY (never substring-matched). Safe: returns [] on any error.
"""
import json
import os
import sqlite3
import time

DB = os.path.expanduser("~/.hermes/state.db")


def _classify(content) -> str:
    """ok / fail / neutral — from the JSON success/error key, matching fleet_monitor.py."""
    try:
        j = json.loads(content)
    except Exception:
        return "neutral"
    if not isinstance(j, dict):
        return "neutral"
    if "success" in j:
        return "ok" if j["success"] else "fail"
    if "error" in j and j["error"]:
        return "fail"
    return "neutral"


def _task_context(c, sid, n=90) -> str:
    """The session's first user message = the task that was being worked on."""
    try:
        r = c.execute(
            "select content from messages where session_id=? and role='user' order by timestamp asc limit 1",
            (sid,)).fetchone()
        if r and r[0]:
            return " ".join(str(r[0]).split())[:n]
    except Exception:
        pass
    return ""


def recent_failures(limit=15, days=2) -> list:
    """[{tool, model, when, task, error, session}] for recent failed tool calls, newest first."""
    try:
        since = time.time() - days * 86400
        c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        scols = [r[1] for r in c.execute("PRAGMA table_info(sessions)").fetchall()]
        mcol = next((x for x in ("model", "model_name", "last_model") if x in scols), None)
        sess_model = {}
        if mcol:
            for sid, m in c.execute(f"select id,{mcol} from sessions").fetchall():
                sess_model[sid] = m or "?"
        rows = c.execute(
            "select session_id, tool_name, content, timestamp from messages "
            "where role='tool' and timestamp>=? order by timestamp desc", (since,)).fetchall()
        out = []
        for sid, tool, content, ts in rows:
            if _classify(content) != "fail":
                continue
            try:
                err = str(json.loads(content).get("error", "")) or str(content)
            except Exception:
                err = str(content)
            out.append({
                "tool": tool or "?",
                "model": sess_model.get(sid, "?"),      # raw; the widget cleans it for display
                "when": time.strftime("%m-%d %H:%M", time.localtime(ts)) if ts else "?",
                "task": _task_context(c, sid),
                "error": " ".join(err.split())[:200],
                "session": str(sid)[:12],
            })
            if len(out) >= limit:
                break
        return out
    except Exception:
        return []
