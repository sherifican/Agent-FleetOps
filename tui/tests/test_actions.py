"""Gate for sources/actions.py — the owner 'hand off' queue. No textual; no real Telegram/subprocess."""
import json
from fleet_tui.sources import actions


def test_request_action_appends_json_line(tmp_path, monkeypatch):
    q = tmp_path / ".action_requests"
    monkeypatch.setattr(actions, "ACTION_QUEUE", str(q))
    assert actions.request_action("automation", "supply-chain-scan FAILED", "rc=1 scanner crash") is True
    lines = [json.loads(l) for l in q.read_text().splitlines() if l.strip()]
    assert len(lines) == 1
    assert lines[0]["source"] == "automation"
    assert "supply-chain-scan FAILED" in lines[0]["title"]
    assert lines[0]["status"] == "requested"
    assert actions.pending_count() == 1


def test_dedup_same_source_title(tmp_path, monkeypatch):
    q = tmp_path / ".action_requests"
    monkeypatch.setattr(actions, "ACTION_QUEUE", str(q))
    actions.request_action("backup", "off-box backup ALERT", "detail v1")
    actions.request_action("backup", "off-box backup ALERT", "detail v2")   # supersedes, not duplicates
    actions.request_action("hive", "HIVE drift", "x")                        # distinct → separate
    assert actions.pending_count() == 2
    recs = [json.loads(l) for l in q.read_text().splitlines() if l.strip()]
    backup = [r for r in recs if r["source"] == "backup"]
    assert len(backup) == 1 and backup[0]["detail"] == "detail v2"           # kept the latest


def test_missing_queue_and_errors_are_safe(tmp_path, monkeypatch):
    monkeypatch.setattr(actions, "ACTION_QUEUE", str(tmp_path / "nope" / ".action_requests"))
    assert actions.pending_count() == 0
    # request into a fresh (creatable) dir still works
    monkeypatch.setattr(actions, "ACTION_QUEUE", str(tmp_path / "sub" / ".action_requests"))
    assert actions.request_action("github", "action item", "") is True
    assert actions.pending_count() == 1
