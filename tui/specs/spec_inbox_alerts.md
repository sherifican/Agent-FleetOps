# SPEC — extend sources/inbox.py: surface ALL fleet alert channels (v3.12 wave)

Rewrite ~/fleet_tui/fleet_tui/sources/inbox.py COMPLETELY, preserving every existing function,
constant, behavior and docstring EXACTLY as-is, and ADDING the following. Do not change models.py.
Do not import textual. Every reader must never raise (missing/corrupt file -> None / skip).

NEW path constants (add below the existing ones):
    AUTOMATION_ALERT = "~/.claude/curation/.automation_alert"
    BACKUP_ALERT     = "~/.claude/curation/.backup_alert"
    SUPPLY_ALERT     = "~/.claude/curation/.supply_chain_alert"
    HIVE_ALERT       = "~/.claude/hive/.hive_drift_alert"
    TELEGRAM_TRIGGER = "~/.claude/curation/.telegram_trigger"

NEW builder functions (same style as the existing ones — take parsed input, return InboxItem|None):

automation_item(text: str) -> InboxItem | None
    Input = raw text of AUTOMATION_ALERT: zero or more JSON lines, each like
        {"ts": "2026-07-07 00:41", "job": "supply-chain-scan", "detail": "scanner FAILED rc=1 ..."}
    Parse each non-blank line with json.loads inside try/except (skip unparseable lines).
    If zero parsed entries -> None.
    Else return InboxItem(source="automation",
        title=f"{n} automation failure(s)",       # n = number of parsed entries
        age=<ts of the FIRST entry, or "">,
        priority="crit", pending=True,
        detail=", ".join(job names, deduped, in order),
        body=one line per entry: f"• [{ts}] {job}: {detail}")

backup_item(d: dict) -> InboxItem | None
    Input = parsed JSON of BACKUP_ALERT (may be {}). If d.get("pending") is truthy ->
    InboxItem(source="backup", title="Off-box backup ALERT", priority="crit", pending=True,
              detail=str(d.get("detail",""))[:120], body=str(d.get("detail","")))
    else None.

supply_item(d: dict) -> InboxItem | None
    Same shape as backup_item but source="supply", title="Supply-chain scan flagged",
    reading SUPPLY_ALERT's dict.

hive_item(text: str) -> InboxItem | None
    Input = raw text of HIVE_ALERT. If text.strip() nonempty ->
    InboxItem(source="hive", title="HIVE drift alert", priority="normal", pending=True,
              detail=first nonblank line truncated to 120 chars, body=text.strip())
    else None.

telegram_item(d: dict) -> InboxItem | None
    Input = parsed JSON of TELEGRAM_TRIGGER. If d.get("pending") ->
    InboxItem(source="telegram", title="Telegram msg awaiting Claude", priority="fyi", pending=True,
              detail=f"{d.get('count',0)} message(s), {d.get('directed',0)} directed",
              body="Owner message(s) pending in .telegram_context.md — Claude answers on next turn or headless cron.")
    else None.

CHANGE build_inbox to this exact new signature and ordering (crit-class first):
    def build_inbox(automation_text, backup, supply, dep, curation, github_text,
                    hive_text, rejects_text, hf_text, telegram) -> list[InboxItem]
    Order of appends: automation_item, github_item, backup_item, supply_item, dep_item,
                      curation_item, hive_item, rejects_item, hf_item, telegram_item.
    (Same append-if-not-None pattern as now.)

CHANGE list_inbox() to read all ten inputs with the existing read_json/read_text helpers and pass them
in the new order.

EXTEND ack(source) with these cases (same gated-path semantics as the existing ones):
    "automation" -> truncate AUTOMATION_ALERT (open(...,"w").close()); return True
    "hive"       -> truncate HIVE_ALERT; return True
    "backup"     -> set pending=False in BACKUP_ALERT json (same pattern as dep/curation); return True
    "supply"     -> set pending=False in SUPPLY_ALERT json; return True
    ("telegram" gets NO ack case — Claude owns clearing that trigger; ack("telegram") must return False.)

The Claude-authored gate is ~/fleet_tui/tests/test_inbox_alerts.py — your file must pass it
plus the existing tests/test_inbox.py unchanged.
