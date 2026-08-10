"""Hermetic tests for sources/codex_link.py — the Codex-PC-Link bridge status reader.
No live network: the port/readyz probes are monkeypatched; config path redirected to tmp."""
import json

import pytest

from fleet_tui.sources import codex_link as cl


def _reset():
    cl._cache["v"] = None
    cl._cache["t"] = 0.0


@pytest.fixture(autouse=True)
def _enabled_config(monkeypatch, tmp_path):
    """Default the reader to an ENABLED tmp config so tests never read the real
    ~/.fleet_tui/codex_link.json (now owner-disabled). Tests that need a different
    config just set FLEET_CODEX_LINK_CONFIG themselves — their setenv wins."""
    cfg = tmp_path / "default_codex_link.json"
    cfg.write_text(json.dumps({"port": 4500, "host_label": "WinPC", "enabled": True}))
    monkeypatch.setenv("FLEET_CODEX_LINK_CONFIG", str(cfg))


def test_up(monkeypatch, tmp_path):
    _reset()
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"port": 4599, "host_label": "WinPC"}))
    monkeypatch.setenv("FLEET_CODEX_LINK_CONFIG", str(cfg))
    monkeypatch.setattr(cl, "_port_listening", lambda p: True)
    monkeypatch.setattr(cl, "_readyz", lambda p: 200)
    s = cl.read_status(force=True)
    assert s["state"] == "up"
    assert s["port"] == 4599 and s["host_label"] == "WinPC" and s["http_code"] == 200


def test_down_listening_but_not_ready(monkeypatch):
    _reset()
    monkeypatch.setattr(cl, "_port_listening", lambda p: True)
    monkeypatch.setattr(cl, "_readyz", lambda p: 503)
    assert cl.read_status(force=True)["state"] == "down"


def test_off_not_listening(monkeypatch):
    _reset()
    monkeypatch.setattr(cl, "_port_listening", lambda p: False)
    monkeypatch.setattr(cl, "_readyz", lambda p: 0)
    s = cl.read_status(force=True)
    assert s["state"] == "off" and s["http_code"] is None


def test_never_raises_on_bad_config(monkeypatch, tmp_path):
    _reset()
    bad = tmp_path / "bad.json"
    bad.write_text("{ not json")
    monkeypatch.setenv("FLEET_CODEX_LINK_CONFIG", str(bad))
    monkeypatch.setattr(cl, "_port_listening", lambda p: False)
    s = cl.read_status(force=True)
    assert s["state"] == "off" and s["port"] == 4500 and s["host_label"] == "WinPC"


def test_disabled_short_circuits(monkeypatch, tmp_path):
    # enabled:false → state "disabled", and NO ss/curl probing happens
    _reset()
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"port": 4500, "host_label": "WinPC", "enabled": False}))
    monkeypatch.setenv("FLEET_CODEX_LINK_CONFIG", str(cfg))
    probed = {"n": 0}
    monkeypatch.setattr(cl, "_port_listening", lambda p: probed.__setitem__("n", probed["n"] + 1) or True)
    monkeypatch.setattr(cl, "_readyz", lambda p: 200)
    s = cl.read_status(force=True)
    assert s["state"] == "disabled" and s["http_code"] is None
    assert probed["n"] == 0   # disabled must not touch the network


def test_cached(monkeypatch):
    _reset()
    calls = {"n": 0}

    def pl(p):
        calls["n"] += 1
        return False

    monkeypatch.setattr(cl, "_port_listening", pl)
    monkeypatch.setattr(cl, "_readyz", lambda p: 0)
    cl.read_status(force=True)   # computes (call 1)
    cl.read_status()             # cached, no new probe
    assert calls["n"] == 1


def test_probe_helpers_never_raise(monkeypatch):
    # subprocess blowing up must degrade to False/0, never raise
    def boom(*a, **k):
        raise OSError("no ss/curl")
    monkeypatch.setattr(cl.subprocess, "run", boom)
    assert cl._port_listening(4500) is False
    assert cl._readyz(4500) == 0
