"""Tests for the tool-failures source (state.db classify + safety)."""
from fleet_tui.sources.failures import _classify, recent_failures


def test_classify():
    assert _classify('{"success": false, "error": "boom"}') == "fail"
    assert _classify('{"error": "boom"}') == "fail"
    assert _classify('{"success": true}') == "ok"
    assert _classify('{"result": "hi"}') == "neutral"   # no success/error key
    assert _classify("not json") == "neutral"
    assert _classify('["a","b"]') == "neutral"           # not a dict


def test_recent_failures_safe_on_bad_db(monkeypatch):
    import fleet_tui.sources.failures as F
    monkeypatch.setattr(F, "DB", "/nonexistent/state.db")
    assert F.recent_failures() == []
