# SPEC — new source: fleet_tui/sources/posture.py (Wave 3: backup + supply-chain + upstream posture)

Create ~/fleet_tui/fleet_tui/sources/posture.py — a PURE HEADLESS reader (NO textual import,
never raises: every file-read wrapped so a missing/corrupt file degrades to a safe default). It parses three
fleet ledgers + two alert files and returns one composed snapshot dict. It must pass the Claude-authored gate
tests/test_posture.py EXACTLY.

MODULE-LEVEL path constants (tests monkeypatch these, so they MUST be module attributes with these names):
    BACKUP_LOG   = "~/.claude/curation/BACKUP_LOG.md"
    SUPPLY_LOG   = "~/.claude/curation/SUPPLY_CHAIN_LOG.md"
    UPSTREAM     = "~/.claude/curation/UPSTREAM_UPDATES.md"
    BACKUP_ALERT = "~/.claude/curation/.backup_alert"
    SUPPLY_ALERT = "~/.claude/curation/.supply_chain_alert"

Provide a helper `_read(path)` returning the file text or "" on any error, and `_read_json(path)` returning a
dict or {} on any error. Use these everywhere so nothing raises.

REAL LINE FORMATS (parse these exactly):

  BACKUP_LOG lines look like (dash, space, timestamp "YYYY-MM-DD HH:MM", space, glyph ✓ or ⚠, space, message):
      - 2026-07-07 00:54 ✓ repos pushed (skills/memory/curation/hive/fleet_tui)
      - 2026-07-07 00:54 ✓ system-scripts mirror pushed
      - 2026-07-05 04:24 ⚠ off-box backup ABORTED (repos): secret in a TRACKED file: ... — scrub before next backup
    A line is a SUCCESS if its glyph is ✓, an ABORT/failure if its glyph is ⚠ (or the message contains "ABORT").
    "repos push" line = message contains "repos pushed"; "mirror push" line = message contains "mirror pushed".

  SUPPLY_LOG lines look like:
      - 2026-07-07 00:45 · alerts:0 · install-hooks:3 · new-since-last:0
    Fields are "alerts:N", "install-hooks:N", "new-since-last:N" separated by " · ".

  UPSTREAM_UPDATES is grouped into blocks, each starting with a header line:
      ## check 2026-07-06T15:47:03
    followed by item lines:
      - open-second-brain: local `1.22.0` / latest `1.24.0` — ⬆ BEHIND **[NEW since last check]** · CRITICAL — ...
      - ollama: local `0.31.1` / latest `0.31.1` — current
    An item is BEHIND if the line contains "BEHIND". It is CRITICAL if it contains "CRITICAL".
    name = the text between "- " and the first ": local"; local = the first `backtick-quoted` value after "local";
    latest = the `backtick-quoted` value after "latest".
    ONLY parse the LAST (most recent) "## check" block — earlier blocks are historical and must be ignored.

FUNCTIONS:

  _parse_backup(text) -> dict with keys:
      last          -> {ts, ok, msg} for the LAST (bottom-most) parseable line, or None if none
      last_repos_ok -> ts (str) of the most recent ✓ line whose message contains "repos pushed", else None
      last_mirror_ok-> ts (str) of the most recent ✓ line whose message contains "mirror pushed", else None
      last_abort    -> {ts, reason} for the most recent ⚠/ABORT line (reason = message after the glyph), or None
    ("most recent" = last matching line in file order, since the log is append-only chronological.)

  _parse_supply(text) -> dict: ts, alerts (int), install_hooks (int), new_since_last (int) from the LAST line;
    or {ts:None, alerts:0, install_hooks:0, new_since_last:0} if no parseable line. Parse ints defensively.

  _parse_upstream(text) -> dict:
      checked  -> the timestamp string from the LAST "## check <ts>" header, or None
      behind   -> count of item lines containing "BEHIND" in that last block (int)
      critical -> list of {name, local, latest} for lines in that last block that contain BOTH "BEHIND" and
                  "CRITICAL", in file order

  snapshot() -> dict {"backup": {...}, "supply": {...}, "upstream": {...}} where:
      backup = _parse_backup(_read(BACKUP_LOG)) PLUS an "alert_pending" bool = bool(_read_json(BACKUP_ALERT).get("pending"))
      supply = _parse_supply(_read(SUPPLY_LOG)) PLUS "alert_pending" = bool(_read_json(SUPPLY_ALERT).get("pending"))
      upstream = _parse_upstream(_read(UPSTREAM))
    snapshot() must NEVER raise; on total failure return the fully-formed empty shape (all safe defaults) so
    every key the tests check is always present.

Regex hints (indented, not fenced):
    backup line:  ^- (\d{4}-\d\d-\d\d \d\d:\d\d) ([✓⚠]) (.*)$
    supply line:  alerts:(\d+).*install-hooks:(\d+).*new-since-last:(\d+)   (with the ts captured up front)
    upstream item: ^- (.+?): local `([^`]*)` / latest `([^`]*)`
    check header:  ^## check (.+?)\s*$

Do NOT import textual. Do NOT edit models.py (return plain dicts, like sources/dispatch.py does). The gate is
tests/test_posture.py — match it exactly.
