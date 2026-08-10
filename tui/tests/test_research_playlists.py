"""Hermetic tests for sources/research_playlists.py — pure readers + the thin request-writer.
No live fleet, no network; all paths redirected to tmp via env vars."""
import json
import os

from fleet_tui.sources import research_playlists as rp
from fleet_tui.models import Playlist


def _cfg(tmp_path, playlists):
    p = tmp_path / "cfg.json"
    p.write_text(json.dumps({"playlists": playlists}))
    return str(p)


def test_read_playlists_valid(tmp_path, monkeypatch):
    monkeypatch.setenv("FLEET_RESEARCH_PLAYLISTS",
                       _cfg(tmp_path, [{"name": "AI Stuff", "url": "http://x", "kind": "youtube"}]))
    monkeypatch.setenv("FLEET_RESEARCH_STATE", str(tmp_path / "state.json"))
    pls = rp.read_playlists()
    assert len(pls) == 1 and isinstance(pls[0], Playlist)
    assert pls[0].name == "AI Stuff" and pls[0].url == "http://x"
    assert pls[0].last_checked == "" and pls[0].kind == "youtube"


def test_read_playlists_missing_config_safe(tmp_path, monkeypatch):
    monkeypatch.setenv("FLEET_RESEARCH_PLAYLISTS", str(tmp_path / "nope.json"))
    assert rp.read_playlists() == []


def test_read_playlists_malformed_safe(tmp_path, monkeypatch):
    bad = tmp_path / "bad.json"
    bad.write_text("{ not json")
    monkeypatch.setenv("FLEET_RESEARCH_PLAYLISTS", str(bad))
    assert rp.read_playlists() == []


def test_read_playlists_skips_nameless(tmp_path, monkeypatch):
    monkeypatch.setenv("FLEET_RESEARCH_PLAYLISTS",
                       _cfg(tmp_path, [{"url": "http://x"}, {"name": "Good", "url": "y"}]))
    monkeypatch.setenv("FLEET_RESEARCH_STATE", str(tmp_path / "s.json"))
    pls = rp.read_playlists()
    assert len(pls) == 1 and pls[0].name == "Good"


def test_request_check_writes_intent_and_stamps(tmp_path, monkeypatch):
    monkeypatch.setenv("FLEET_RESEARCH_PLAYLISTS",
                       _cfg(tmp_path, [{"name": "AI Stuff", "url": "http://x"}]))
    monkeypatch.setenv("FLEET_RESEARCH_STATE", str(tmp_path / "state.json"))
    monkeypatch.setenv("FLEET_RESEARCH_REQUEST_DIR", str(tmp_path / "req"))

    path = rp.request_check("AI Stuff", "http://x")
    assert path and os.path.exists(path)
    d = json.load(open(path))
    assert d["playlist"] == "AI Stuff"
    assert d["pending"] is True
    assert d["action"] == "check_playlist_and_stage"

    # last_checked is stamped and reflected on the next read
    pls = rp.read_playlists()
    assert pls[0].last_checked != ""

    # pending_requests surfaces it (for Claude to process)
    pend = rp.pending_requests()
    assert len(pend) == 1 and pend[0]["playlist"] == "AI Stuff"


def test_request_check_bad_dir_returns_empty(tmp_path, monkeypatch):
    # request dir under a path that can't be created (a file in the way) -> safe "" , never raises
    blocker = tmp_path / "blocker"
    blocker.write_text("x")
    monkeypatch.setenv("FLEET_RESEARCH_REQUEST_DIR", str(blocker / "sub"))
    monkeypatch.setenv("FLEET_RESEARCH_STATE", str(tmp_path / "state.json"))
    assert rp.request_check("AI Stuff") == ""
