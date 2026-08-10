"""Claude-authored gate for Wave 3 — backup + supply-chain + upstream POSTURE source.
Pure headless reader; no textual. Fixtures mirror the REAL log formats on the box (2026-07-07)."""
import json
from fleet_tui.sources import posture


BACKUP = """\
- 2026-07-04 00:26 ✓ system-scripts mirror pushed
- 2026-07-05 04:24 ⚠ off-box backup ABORTED (repos): secret in a TRACKED file: | memory:inline — scrub before next backup
- 2026-07-05 04:24 ✓ system-scripts mirror pushed
- 2026-07-06 21:26 ✓ repos pushed (skills/memory/curation/hive/fleet_tui)
- 2026-07-07 00:54 ✓ repos pushed (skills/memory/curation/hive/fleet_tui)
- 2026-07-07 00:54 ✓ system-scripts mirror pushed
"""

SUPPLY = """\
- 2026-07-05 17:43 · alerts:0 · install-hooks:3 · new-since-last:0
- 2026-07-06 17:44 · alerts:0 · install-hooks:3 · new-since-last:0
- 2026-07-07 00:45 · alerts:2 · install-hooks:3 · new-since-last:1
"""

UPSTREAM = """\
## check 2026-07-05T10:00:00
- ollama: local `0.31.1` / latest `0.31.1` — current

## check 2026-07-06T15:47:03
- open-second-brain: local `1.22.0` / latest `1.24.0` — ⬆ BEHIND **[NEW since last check]** · CRITICAL — our Tier-2 brain (MCP)
- ollama: local `0.31.1` / latest `0.31.1` — current
- Hermes harness: local `2026.6.19` / latest `2026.7.1` — ⬆ BEHIND · CRITICAL — the agent framework
- syncthing: local `1.29.5` / latest `2.1.1` — ⬆ BEHIND · MED — Tier-2 brain sync
"""


def _setup(tmp_path, monkeypatch, backup=BACKUP, supply=SUPPLY, upstream=UPSTREAM,
           backup_alert=None, supply_alert=None):
    b = tmp_path / "BACKUP_LOG.md"; b.write_text(backup)
    s = tmp_path / "SUPPLY_CHAIN_LOG.md"; s.write_text(supply)
    u = tmp_path / "UPSTREAM_UPDATES.md"; u.write_text(upstream)
    ba = tmp_path / ".backup_alert"; ba.write_text(json.dumps(backup_alert or {}))
    sa = tmp_path / ".supply_chain_alert"; sa.write_text(json.dumps(supply_alert or {}))
    monkeypatch.setattr(posture, "BACKUP_LOG", str(b))
    monkeypatch.setattr(posture, "SUPPLY_LOG", str(s))
    monkeypatch.setattr(posture, "UPSTREAM", str(u))
    monkeypatch.setattr(posture, "BACKUP_ALERT", str(ba))
    monkeypatch.setattr(posture, "SUPPLY_ALERT", str(sa))


def test_backup_parses_last_good_and_abort(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    p = posture.snapshot()["backup"]
    assert p["last"]["ok"] is True and p["last"]["ts"] == "2026-07-07 00:54"
    assert p["last_repos_ok"] == "2026-07-07 00:54"
    assert p["last_mirror_ok"] == "2026-07-07 00:54"
    assert p["last_abort"] is not None
    assert p["last_abort"]["ts"] == "2026-07-05 04:24"
    assert "secret in a TRACKED file" in p["last_abort"]["reason"]
    assert p["alert_pending"] is False


def test_backup_alert_pending(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, backup_alert={"pending": True, "detail": "x"})
    assert posture.snapshot()["backup"]["alert_pending"] is True


def test_supply_parses_latest_row(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    p = posture.snapshot()["supply"]
    assert p["ts"] == "2026-07-07 00:45"
    assert p["alerts"] == 2 and p["install_hooks"] == 3 and p["new_since_last"] == 1
    assert p["alert_pending"] is False


def test_upstream_latest_block_only_and_criticals(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    p = posture.snapshot()["upstream"]
    assert p["checked"] == "2026-07-06T15:47:03"
    assert p["behind"] == 3               # osb + hermes + syncthing in the LATEST block only
    names = [c["name"] for c in p["critical"]]
    assert "open-second-brain" in names and "Hermes harness" in names
    assert "syncthing" not in names       # MED, not CRITICAL
    osb = next(c for c in p["critical"] if c["name"] == "open-second-brain")
    assert osb["local"] == "1.22.0" and osb["latest"] == "1.24.0"


def test_missing_files_degrade_safely(tmp_path, monkeypatch):
    # point at nonexistent paths — must return a well-formed snapshot, never raise
    monkeypatch.setattr(posture, "BACKUP_LOG", str(tmp_path / "no1"))
    monkeypatch.setattr(posture, "SUPPLY_LOG", str(tmp_path / "no2"))
    monkeypatch.setattr(posture, "UPSTREAM", str(tmp_path / "no3"))
    monkeypatch.setattr(posture, "BACKUP_ALERT", str(tmp_path / "no4"))
    monkeypatch.setattr(posture, "SUPPLY_ALERT", str(tmp_path / "no5"))
    snap = posture.snapshot()
    assert snap["backup"]["last"] is None and snap["backup"]["last_abort"] is None
    assert snap["backup"]["alert_pending"] is False
    assert snap["supply"]["ts"] is None and snap["supply"]["alerts"] == 0
    assert snap["upstream"]["behind"] == 0 and snap["upstream"]["critical"] == []


def test_snapshot_shape_always_complete(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    snap = posture.snapshot()
    assert set(snap.keys()) == {"backup", "supply", "upstream"}
    for k in ("last", "last_repos_ok", "last_mirror_ok", "last_abort", "alert_pending"):
        assert k in snap["backup"]
    for k in ("ts", "alerts", "install_hooks", "new_since_last", "alert_pending"):
        assert k in snap["supply"]
    for k in ("behind", "critical", "checked"):
        assert k in snap["upstream"]
