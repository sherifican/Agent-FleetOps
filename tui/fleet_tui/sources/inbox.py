"""Source reader for inbox items."""
from fleet_tui.models import InboxItem
import json
import os
import re

DEP_TRIGGER   = os.path.expanduser("~/.claude/curation/.dep_update_trigger")
CURATION_TRIGGER = os.path.expanduser("~/.claude/curation/.trigger")
GITHUB_ALERT  = os.path.expanduser("~/.claude/curation/.github_action_alert")
HF_DIGEST     = os.path.expanduser("~/.claude/curation/HF_WATCH_DIGEST.md")
REJECTS       = os.path.expanduser("~/.claude/curation/CURATION_REJECTS_REVIEW.md")
AUTOMATION_ALERT = os.path.expanduser("~/.claude/curation/.automation_alert")
BACKUP_ALERT     = os.path.expanduser("~/.claude/curation/.backup_alert")
SUPPLY_ALERT     = os.path.expanduser("~/.claude/curation/.supply_chain_alert")
HIVE_ALERT       = os.path.expanduser("~/.claude/hive/.hive_drift_alert")
TELEGRAM_TRIGGER = os.path.expanduser("~/.claude/curation/.telegram_trigger")


def read_json(path) -> dict:
    """Read a JSON file, return empty dict on any error."""
    try:
        with open(path, 'r') as f:
            return json.load(f)
    except:
        return {}


def read_text(path) -> str:
    """Read a text file, return empty string on any error."""
    try:
        with open(path, 'r') as f:
            return f.read()
    except:
        return ""


def dep_item(d: dict) -> InboxItem | None:
    """Build an inbox item for dependency updates."""
    if d.get("pending"):
        updates = d.get("updates", [])
        return InboxItem(
            source="dep",
            title=f"{len(updates)} dependency update(s)",
            age=d.get("iso",""),
            priority="normal",
            pending=True,
            detail=", ".join(u.get("name","?") for u in updates),
            body="\n".join(f"• {u.get('name','?')}: {u.get('local','?')} → {u.get('latest','?')}"
                           f"  ({u.get('crit','')})" for u in updates)
        )
    return None


def curation_item(d: dict) -> InboxItem | None:
    """Build an inbox item for curation passes."""
    if d.get("pending"):
        reasons = d.get("reasons", [])
        return InboxItem(
            source="curation",
            title=f"Curation pass {d.get('pass_n','?')} due",
            age=d.get("iso",""),
            priority="normal",
            pending=True,
            detail="; ".join(reasons),
            body="Reasons:\n" + "\n".join(f"• {r}" for r in reasons) + f"\n\n(pass {d.get('pass_n','?')})"
        )
    return None


def github_item(text: str) -> InboxItem | None:
    """Build an inbox item for GitHub alerts."""
    if text.strip():
        return InboxItem(
            source="github",
            title="GitHub action alert",
            priority="crit",
            pending=True,
            detail=text.strip().splitlines()[0][:120],
            body=text.strip()
        )
    return None


def rejects_item(text: str) -> InboxItem | None:
    """Build an inbox item for curation rejects."""
    # A REAL pending reject = a "### R<digits>" block still marked STATUS: UNREVIEWED.
    # Excludes the file's disclaimer prose AND the "### R<id>" format-template example — both of which
    # contain the word "unreviewed" but are NOT actionable (the old code counted those → false "2 pending").
    entries = re.split(r'(?m)(?=^### R\d+)', text)
    pending = []
    for e in entries:
        if re.match(r'^### R\d+', e) and re.search(r'STATUS:\s*UNREVIEWED', e, re.I) and '✓ reviewed' not in e:
            pending.append(e.strip().splitlines()[0])
    if not pending:
        return None
    return InboxItem(
        source="rejects",
        title="Curation rejects to review",
        priority="normal",
        pending=True,
        detail=f"{len(pending)} unreviewed",
        body="\n".join(pending)
    )


def hf_item(text: str) -> InboxItem | None:
    """Build an inbox item for the HF-watch digest — a CLEAN summary (latest scan header + the model
    names flagged for eval), NOT a raw dump of the markdown/JSON. The digest is an append-only log of
    `## HF-WATCH <date> — N new models` blocks with `### 🔔 SIGNAL — <model>` entries + JSON bodies;
    dumping its first 25 lines flooded the inbox with stray JSON (fixed 2026-07-07)."""
    if not text.strip():
        return None
    lines = text.strip().splitlines()
    headers = [ln.strip().lstrip("# ").strip() for ln in lines if ln.strip().startswith("## HF-WATCH")]
    latest = headers[-1] if headers else "HF-watch digest"
    # SIGNAL model names (the actionable, needs-eval entries), deduped in order; show the most recent few
    sigs = []
    for ln in lines:
        m = re.search(r'SIGNAL\s*[—:\-]\s*(.+?)\s*$', ln)
        if m:
            name = m.group(1).strip()
            if name and name not in sigs:
                sigs.append(name)
    if sigs:
        recent = sigs[-8:]
        body = "Models flagged for eval:\n" + "\n".join(f"• {s}" for s in recent)
        detail = f"{len(sigs)} model(s) flagged · {latest}"
    else:
        body = latest
        detail = latest
    return InboxItem(source="hf", title="HF-watch digest", priority="fyi", pending=True,
                     detail=detail[:120], body=body)


def automation_item(text: str) -> InboxItem | None:
    """Build an inbox item for automation alerts."""
    lines = text.strip().splitlines()
    parsed = []
    for line in lines:
        if not line.strip():
            continue
        try:
            parsed.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    if not parsed:
        return None
    n = len(parsed)
    first_ts = parsed[0].get("ts", "")
    jobs = list(dict.fromkeys([e["job"] for e in parsed]))  # deduped, preserve order
    return InboxItem(
        source="automation",
        title=f"{n} automation failure(s)",
        age=first_ts,
        priority="crit",
        pending=True,
        detail=", ".join(jobs),
        body="\n".join(f"• [{e['ts']}] {e['job']}: {e['detail']}" for e in parsed)
    )


def backup_item(d: dict) -> InboxItem | None:
    """Build an inbox item for backup alerts."""
    if d.get("pending"):
        return InboxItem(
            source="backup",
            title="Off-box backup ALERT",
            priority="crit",
            pending=True,
            detail=str(d.get("detail",""))[:120],
            body=str(d.get("detail",""))
        )
    return None


def supply_item(d: dict) -> InboxItem | None:
    """Build an inbox item for supply-chain alerts."""
    if d.get("pending"):
        return InboxItem(
            source="supply",
            title="Supply-chain scan flagged",
            priority="crit",
            pending=True,
            detail=str(d.get("detail",""))[:120],
            body=str(d.get("detail",""))
        )
    return None


def hive_item(text: str) -> InboxItem | None:
    """Build an inbox item for hive drift alerts."""
    if text.strip():
        first_line = text.strip().splitlines()[0]
        return InboxItem(
            source="hive",
            title="HIVE drift alert",
            priority="normal",
            pending=True,
            detail=first_line[:120],
            body=text.strip()
        )
    return None


def telegram_item(d: dict) -> InboxItem | None:
    """Build an inbox item for telegram triggers."""
    if d.get("pending"):
        return InboxItem(
            source="telegram",
            title="Telegram msg awaiting Claude",
            priority="fyi",
            pending=True,
            detail=f"{d.get('count',0)} message(s), {d.get('directed',0)} directed",
            body="Owner message(s) pending in .telegram_context.md — Claude answers on next turn or headless cron."
        )
    return None


def build_inbox(automation_text, backup, supply, dep, curation, github_text,
                hive_text, rejects_text, hf_text, telegram) -> list[InboxItem]:
    """Compose all inbox items in order (crit-class first)."""
    items = []
    
    # Add items in the specified order
    item = automation_item(automation_text)
    if item:
        items.append(item)
        
    item = github_item(github_text)
    if item:
        items.append(item)
        
    item = backup_item(backup)
    if item:
        items.append(item)
        
    item = supply_item(supply)
    if item:
        items.append(item)
        
    item = dep_item(dep)
    if item:
        items.append(item)
        
    item = curation_item(curation)
    if item:
        items.append(item)
        
    item = hive_item(hive_text)
    if item:
        items.append(item)
        
    item = rejects_item(rejects_text)
    if item:
        items.append(item)
        
    item = hf_item(hf_text)
    if item:
        items.append(item)
        
    item = telegram_item(telegram)
    if item:
        items.append(item)
        
    return items


def list_inbox() -> list[InboxItem]:
    """Get all pending inbox items."""
    return build_inbox(
        read_text(AUTOMATION_ALERT),
        read_json(BACKUP_ALERT),
        read_json(SUPPLY_ALERT),
        read_json(DEP_TRIGGER),
        read_json(CURATION_TRIGGER),
        read_text(GITHUB_ALERT),
        read_text(HIVE_ALERT),
        read_text(REJECTS),
        read_text(HF_DIGEST),
        read_json(TELEGRAM_TRIGGER)
    )


def ack(source: str) -> bool:
    """Owner GATE action — acknowledge/clear a pending inbox item by source (via the existing gated path:
    truncate the alert / set the trigger's pending=false). Returns True if it cleared something."""
    try:
        if source == "github":
            open(GITHUB_ALERT, "w").close()          # truncate the alert file
            return True
        if source in ("dep", "curation"):
            path = DEP_TRIGGER if source == "dep" else CURATION_TRIGGER
            if os.path.exists(path):
                d = json.load(open(path))
                d["pending"] = False
                json.dump(d, open(path, "w"), indent=2)
            return True
        if source == "automation":
            open(AUTOMATION_ALERT, "w").close()      # truncate the alert file
            return True
        if source == "hive":
            open(HIVE_ALERT, "w").close()            # truncate the alert file
            return True
        if source == "backup":
            path = BACKUP_ALERT
            if os.path.exists(path):
                d = json.load(open(path))
                d["pending"] = False
                json.dump(d, open(path, "w"), indent=2)
            return True
        if source == "supply":
            path = SUPPLY_ALERT
            if os.path.exists(path):
                d = json.load(open(path))
                d["pending"] = False
                json.dump(d, open(path, "w"), indent=2)
            return True
    except Exception:
        pass
    return False
