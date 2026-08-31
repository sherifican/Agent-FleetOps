"""Dispatch win-tracking — a durable log of how each target/pairing performed, so the fleet learns which
combos actually work over time. Pure append-only JSONL; summary() aggregates per target. Never raises.

A rating = {target, up (👍/👎), note, speed_s (wall-clock, MEASURED), ts}. Speed is real (from the dispatch's
file mtimes); STEPS are deliberately NOT invented here — a trustworthy step count needs the runner to emit
one, which is not built yet (honest gap, tracked for later).
"""
import json
import os
import time

RATINGS_PATH = os.path.expanduser("~/.fleet_tui/dispatch_ratings.jsonl")


def rate(target: str, up: bool, note: str = "", speed_s=None, path: str = RATINGS_PATH) -> bool:
    """Append one 👍/👎 rating for a target. Returns True on success (never raises)."""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        rec = {"target": target, "up": bool(up), "note": (note or "")[:200],
               "speed_s": speed_s, "ts": time.time()}
        with open(path, "a") as f:
            f.write(json.dumps(rec) + "\n")
        return True
    except Exception:
        return False


def _read(path: str = RATINGS_PATH) -> list:
    out = []
    try:
        with open(path) as f:
            for ln in f:
                ln = ln.strip()
                if ln:
                    try:
                        out.append(json.loads(ln))
                    except Exception:
                        pass
    except Exception:
        pass
    return out


def summary(path: str = RATINGS_PATH) -> dict:
    """Per-target aggregate: {target: {up, down, n, win_rate (%), avg_speed_s, last_note}}."""
    agg = {}
    for r in _read(path):
        t = r.get("target", "?")
        a = agg.setdefault(t, {"up": 0, "down": 0, "_speeds": [], "last_note": ""})
        if r.get("up"):
            a["up"] += 1
        else:
            a["down"] += 1
        if isinstance(r.get("speed_s"), (int, float)):
            a["_speeds"].append(r["speed_s"])
        if r.get("note"):
            a["last_note"] = r["note"]
    for a in agg.values():
        n = a["up"] + a["down"]
        a["n"] = n
        a["win_rate"] = round(100 * a["up"] / n) if n else 0
        a["avg_speed_s"] = round(sum(a["_speeds"]) / len(a["_speeds"])) if a["_speeds"] else None
        del a["_speeds"]
    return agg
