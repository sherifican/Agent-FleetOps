# SPEC — new source: fleet_tui/sources/passback.py (Wave 5: WinClaude passback inbox)

Create ~/fleet_tui/fleet_tui/sources/passback.py — a PURE HEADLESS reader (NO textual import;
never raises). It surfaces WinClaude→Fleet passback files as a newest-first list with an unread marker.
Ack/seen-state is stored in a small JSON file OUTSIDE the shared passback dirs. Must pass the Claude-authored
gate tests/test_passback.py EXACTLY. Do NOT edit models.py (return plain dicts, like sources/dispatch.py).

MODULE-LEVEL constants (tests monkeypatch these — must be module attributes with these names):
    DOCS_GLOB = "~/Documents/PASSBACK_*.md"
    PC_GLOB   = "$PASSBACK_PC_GLOB (your inbound-message glob)"
    SEEN_FILE = "~/.fleet_tui/passback_seen.json"

Use `import glob, json, os, time`.

Helpers (all must swallow errors):
  _load_seen() -> dict     # json.load(SEEN_FILE) mapping {abspath: seen_mtime_float}; {} on any error/corrupt/missing
  _save_seen(d) -> None    # os.makedirs(dirname(SEEN_FILE), exist_ok=True); json.dump; swallow any error
  _title(path) -> str      # read the file; return the first Markdown "# heading" text (strip leading '# ');
                           # else the first non-empty stripped line; else the basename without extension.
                           # Read at most ~4KB; never raise.
  _age(mtime, now=None) -> str   # humanized: "<60s"->"just now"; minutes->"Nm ago"; hours->"Nh ago"; else "Nd ago".
                           # (now defaults to time.time())

FUNCTIONS:

  list_passback() -> list[dict]
    - glob DOCS_GLOB + PC_GLOB (both patterns); collect matched file paths (dedupe by abspath).
    - for each: st_mtime (float); skip files that error on stat.
    - sort NEWEST FIRST by mtime (descending).
    - seen = _load_seen()
    - each item dict:
        { "path": abspath,
          "name": basename,
          "title": _title(path),
          "mtime": mtime_float,
          "age": _age(mtime_float),
          "new": mtime_float > seen.get(abspath, 0) }     # unseen OR modified-since-seen → new
    - return the list. On ANY top-level error return [] (never raise).

  new_count() -> int
    - number of items in list_passback() whose "new" is True. Never raises (return 0 on error).

  mark_seen(path) -> None
    - record this ONE file's current mtime into SEEN_FILE (so it's no longer "new"). If the file is gone,
      still store using time.time() so a vanished file doesn't stay perpetually new. Swallow errors.

  mark_all_seen() -> None
    - for every current item, set seen[path] = its mtime; save. Swallow errors. Must not raise when empty.

Notes:
  - "new" means: mtime strictly greater than the stored seen-mtime (default 0 → brand-new files are new).
  - Never write into the passback files or their dirs — only SEEN_FILE under ~/.fleet_tui.
  - No textual, no models.py. The gate is tests/test_passback.py — match it exactly.
