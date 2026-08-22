"""Read a generic background-agent ledger without assuming a model-name mapping."""
import json
import time
from pathlib import Path

DEFAULT_LEDGER = Path.home() / ".fleet_tui" / "bg_agents.jsonl"


def read_bg_agents(path=None, ttl_s=3600):
    """Return current ledger rows; the recorded model is rendered verbatim or as ``unknown``."""
    running, finished = {}, set()
    try:
        for line in Path(path or DEFAULT_LEDGER).read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except Exception:
                continue
            if not isinstance(row, dict) or not row.get("key"):
                continue
            if row.get("state") in {"done", "finished", "cancelled"}:
                finished.add(str(row["key"]))
            elif row.get("state") in {"running", "active"}:
                running[str(row["key"])] = row
    except Exception:
        return []
    now, out = time.time(), []
    for key, row in running.items():
        if key in finished:
            continue
        try:
            age = now - float(row.get("ts", 0) or 0)
        except (TypeError, ValueError):
            continue
        if age < 0 or age > ttl_s:
            continue
        out.append({"name": str(row.get("model") or "unknown"), "kind": "cloud", "agent": True,
                    "activity": str(row.get("label") or "")[:60], "age_s": int(age)})
    return sorted(out, key=lambda row: row["age_s"])
