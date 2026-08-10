"""Source reader for the curation loop — recent pass log (CURATION_LEDGER.md) + the trigger flag.
Pure/headless (no textual), never raises. `queue_pass()` is the one CONTROL action: it flips the
existing gated `.trigger` to pending=true so the NEXT orchestrator turn runs a full curation pass —
the TUI never runs the pass itself (that's Claude's job), it just queues it (monitor, not orchestrator).
"""
import json
import os
import re
import time

# F2 (Sol audit 2026-07-11): the one CONTROL write (queue_pass -> .trigger) goes through the integrity layer
# (atomic + flock) so it can't clobber a concurrent watcher/session write. Defensive: fall back to raw if
# fleet_lib isn't on the venv path.
try:
    import fleet_state
    def _locked_write_json(path, obj): fleet_state.locked_write(path, json.dumps(obj, indent=2))
except Exception:
    def _locked_write_json(path, obj):
        with open(path, "w", encoding="utf-8") as f: json.dump(obj, f, indent=2)

LEDGER  = os.path.expanduser("~/.claude/curation/CURATION_LEDGER.md")
TRIGGER = os.path.expanduser("~/.claude/curation/.trigger")


def _read(path) -> str:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""


def _read_json(path) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            obj = json.load(f)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _parse_block(header: str, body: str) -> dict:
    """Parse one '## PASS ...' block header + body into a display record. Never raises."""
    # header e.g. "## PASS 65 — 2026-07-06 — CHANGE (1 memory-update) — headline text"
    rest = header[len("## PASS"):].strip() if header.startswith("## PASS") else header.strip()
    parts = [p.strip() for p in rest.split(" — ")]
    pass_n = parts[0] if parts else "?"
    date = parts[1] if len(parts) > 1 else ""
    kindraw = parts[2] if len(parts) > 2 else ""
    headline = " — ".join(parts[3:]) if len(parts) > 3 else ""
    ku = kindraw.upper()
    kind = "CHANGE" if "CHANGE" in ku else ("NO-OP" if "NO-OP" in ku else (kindraw or "?"))
    # one-line summary: the APPLIED line for a CHANGE, else the no-activity line
    summary = ""
    for ln in body.splitlines():
        s = ln.strip()
        if s.startswith("- APPLIED") or s.startswith("- no activity"):
            summary = s.lstrip("- ").strip()
            break
    return {"pass_n": pass_n, "date": date, "kind": kind, "kindraw": kindraw or kind,
            "headline": headline, "summary": summary}


def _date_key(date: str) -> str:
    """Sortable key from a pass date. The ledger mixes full ISO stamps ('2026-07-07T18:07:12', on the
    automatic NO-OP cron passes) with date-only strings ('2026-07-06', on the CHANGE passes). Normalize
    date-only to END-of-day so a CHANGE sorts at the top of its day (above same-day NO-OP stamps), and so
    cross-day ordering is always correct. Empty/garbage → '' (sinks to the bottom under reverse sort)."""
    d = (date or "").strip()
    if re.fullmatch(r'\d{4}-\d\d-\d\d', d):
        return d + "T23:59:59"
    return d


def recent_passes(limit: int = 20) -> list:
    """The most-recent curation passes from the ledger, NEWEST FIRST by DATE (not file order — the ledger
    isn't strictly chronological because NO-OP and CHANGE passes are appended at different times). [] on error."""
    text = _read(LEDGER)
    if not text.strip():
        return []
    # split into blocks that each start with a '## PASS' header line
    blocks = re.split(r'(?m)^(?=## PASS\b)', text)
    recs = []
    for b in blocks:
        b = b.strip()
        if not b.startswith("## PASS"):
            continue
        lines = b.splitlines()
        rec = _parse_block(lines[0], "\n".join(lines[1:]))
        # keep only REAL passes: a YYYY-MM-DD date. Drops the 'PASS 0 — system-init' marker and the
        # '## PASS <n> — <ISO timestamp>' format-template example that otherwise sort to the top.
        if re.match(r'\d{4}-\d\d-\d\d', rec.get("date", "")):
            recs.append(rec)
    recs.sort(key=lambda r: _date_key(r.get("date", "")), reverse=True)   # newest date first
    return recs[:limit]


def trigger_status() -> dict:
    """Current curation-trigger state. Always a complete dict (safe defaults)."""
    d = _read_json(TRIGGER)
    return {
        "pending": bool(d.get("pending")),
        "pass_n": d.get("pass_n", "?"),
        "reasons": d.get("reasons", []) if isinstance(d.get("reasons"), list) else [],
        "iso": d.get("iso", ""),
    }


def queue_pass() -> bool:
    """CONTROL action — flip the gated `.trigger` to pending=true so the next orchestrator turn runs a
    full curation pass. Preserves the rest of the trigger JSON; records a manual reason. Returns True on
    success. (The TUI queues the pass; Claude runs it — never runs a pass itself.)"""
    try:
        d = _read_json(TRIGGER)
        d["pending"] = True
        d["reasons"] = ["manual trigger from TUI"]
        d["iso"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        _locked_write_json(TRIGGER, d)
        return True
    except Exception:
        return False
