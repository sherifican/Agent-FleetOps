"""Test the inbox source module."""
import os
import pytest
from fleet_tui.sources.inbox import (
    read_json,
    read_text,
    dep_item,
    curation_item,
    github_item,
    rejects_item,
    hf_item,
    build_inbox,
    list_inbox
)


def test_dep_item_pending():
    """Test that dep_item returns an InboxItem when pending is true."""
    d = {
        "pending": True,
        "iso": "2026-07-02T13:39:16",
        "updates": [
            {"name": "browser-use", "local": "?", "latest": "0.13.3", "crit": "browser stack"},
            {"name": "Hermes harness", "local": "2026.6.19", "latest": "2026.7.1", "crit": "agent harness"}
        ]
    }
    
    result = dep_item(d)
    assert result is not None
    assert result.source == "dep"
    assert result.priority == "normal"
    assert "browser-use" in result.detail
    assert "Hermes harness" in result.detail


def test_dep_item_not_pending():
    """Test that dep_item returns None when pending is false."""
    d = {
        "pending": False,
        "iso": "2026-07-02T13:39:16",
        "updates": [
            {"name": "browser-use", "local": "?", "latest": "0.13.3", "crit": "browser stack"}
        ]
    }
    
    result = dep_item(d)
    assert result is None


def test_curation_item_pending():
    """Test that curation_item returns an InboxItem when pending is true."""
    d = {
        "pending": True,
        "pass_n": 31,
        "iso": "2026-07-02T00:00:00",
        "reasons": ["x"]
    }
    
    result = curation_item(d)
    assert result is not None
    assert result.source == "curation"
    assert "31" in result.title
    assert "x" in result.detail


def test_curation_item_not_pending():
    """Test that curation_item returns None when pending is false."""
    d = {
        "pending": False,
        "pass_n": 30,
        "iso": "2026-07-02T00:00:00",
        "reasons": ["x"]
    }
    
    result = curation_item(d)
    assert result is None


def test_github_item_non_empty():
    """Test that github_item returns an InboxItem when text is non-empty."""
    text = "actionable\nmore details"
    
    result = github_item(text)
    assert result is not None
    assert result.source == "github"
    assert result.priority == "crit"
    assert "actionable" in result.detail


def test_github_item_empty():
    """Test that github_item returns None when text is empty."""
    result = github_item("")
    assert result is None
    
    result = github_item("   ")
    assert result is None


def test_rejects_item_real_entry():
    """A real '### R<n>' UNREVIEWED entry yields an inbox item with the right count."""
    text = (
        "# Curation rejects\n\nThe gate stays UNREVIEWED until reviewed.\n\n"   # disclaimer (word only)
        "### R1 — PASS 12 — 2026-07-02T00:00:00\n- what: sample\n- STATUS: UNREVIEWED\n"
    )
    result = rejects_item(text)
    assert result is not None
    assert result.source == "rejects"
    assert result.detail == "1 unreviewed"
    assert "R1" in result.body


def test_rejects_item_ignores_disclaimer_and_template():
    """REGRESSION (2026-07-02): the disclaimer prose AND the '### R<id>' format-template example both
    contain the word 'unreviewed' but are NOT real entries -> must return None (was a false '2 pending')."""
    text = (
        "# QUEUE\n\nRejects stay **UNREVIEWED** until you confirm.\n\n"           # disclaimer
        "## Entry format\n```\n### R<id> — PASS <n>\n- STATUS: UNREVIEWED\n```\n"  # template (R<id>, not R\\d+)
    )
    assert rejects_item(text) is None


def test_rejects_item_no_unreviewed():
    """No entries at all -> None."""
    assert rejects_item("all clean") is None


def test_build_inbox_empty():
    """Test that build_inbox returns empty list when all inputs are empty.
    (v3.12 signature: automation_text, backup, supply, dep, curation, github_text,
     hive_text, rejects_text, hf_text, telegram)"""
    result = build_inbox("", {}, {}, {}, {}, "", "", "", "", {})
    assert result == []


def test_build_inbox_order():
    """Test that build_inbox returns items in correct order (v3.12 signature)."""
    dep = {"pending": True, "updates": [{"name": "test"}]}
    github_text = "github alert"

    result = build_inbox("", {}, {}, dep, {}, github_text, "", "", "", {})

    assert len(result) == 2
    assert result[0].source == "github"
    assert result[1].source == "dep"


def test_read_json_nonexistent():
    """Test that read_json returns empty dict for nonexistent file."""
    result = read_json("/nonexistent")
    assert result == {}


def test_read_text_nonexistent():
    """Test that read_text returns empty string for nonexistent file."""
    result = read_text("/nonexistent")
    assert result == ""


def test_list_inbox_integration():
    """Test the full list_inbox function with fixture files."""
    # Get fixture paths
    dep_path = os.path.join(os.path.dirname(__file__), "fixtures", "dep_update_trigger_PENDING.json")
    curation_path = os.path.join(os.path.dirname(__file__), "fixtures", "curation_trigger.json")
    github_path = os.path.join(os.path.dirname(__file__), "fixtures", "github_action_alert_PENDING.txt")
    rejects_path = os.path.join(os.path.dirname(__file__), "fixtures", "fixture_rejects_review.md")
    hf_path = os.path.join(os.path.dirname(__file__), "fixtures", "HF_WATCH_DIGEST.md")
    
    # Mock the file paths to use fixtures
    from unittest.mock import patch
    
    with patch('fleet_tui.sources.inbox.DEP_TRIGGER', dep_path), \
         patch('fleet_tui.sources.inbox.CURATION_TRIGGER', curation_path), \
         patch('fleet_tui.sources.inbox.GITHUB_ALERT', github_path), \
         patch('fleet_tui.sources.inbox.REJECTS', rejects_path), \
         patch('fleet_tui.sources.inbox.HF_DIGEST', hf_path):
        
        result = list_inbox()
        # Should have at least one item (the github alert)
        assert len(result) >= 1
        assert result[0].source == "github"


def test_ack_clears_triggers(tmp_path, monkeypatch):
    import json as _json
    from fleet_tui.sources import inbox as I
    gh = tmp_path / "gh"; dep = tmp_path / "dep.json"; cur = tmp_path / "cur.json"
    gh.write_text("actionable alert")
    dep.write_text(_json.dumps({"pending": True, "updates": []}))
    cur.write_text(_json.dumps({"pending": True, "pass_n": 40}))
    monkeypatch.setattr(I, "GITHUB_ALERT", str(gh))
    monkeypatch.setattr(I, "DEP_TRIGGER", str(dep))
    monkeypatch.setattr(I, "CURATION_TRIGGER", str(cur))
    assert I.ack("github") is True and gh.read_text() == ""            # truncated
    assert I.ack("dep") is True and _json.loads(dep.read_text())["pending"] is False
    assert I.ack("curation") is True and _json.loads(cur.read_text())["pending"] is False
    assert I.ack("hf") is False                                        # not a clearable trigger
