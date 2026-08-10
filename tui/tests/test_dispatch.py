"""Tests for the dispatch box source (allow-list + file creation + recent parsing; mock leg, no cloud fire)."""
import os
import time
from fleet_tui.sources import dispatch


def test_submit_guards_and_files(tmp_path, monkeypatch):
    monkeypatch.setattr(dispatch, "DISPATCH_DIR", str(tmp_path / "d"))
    allowed = {"true"}                                        # `true` = harmless no-op command
    assert dispatch.submit("rm -rf /tmp/x", "hi", allowed=allowed) is None   # NOT in allow-list → refused
    assert dispatch.submit("true", "   ", allowed=allowed) is None           # empty brief
    base = dispatch.submit("true", "hello brief", label="noop", allowed=allowed)
    assert base and os.path.exists(base + ".brief")
    assert open(base + ".brief").read() == "hello brief"      # brief written to a FILE (no shell injection)


def test_recent_reads_status(tmp_path, monkeypatch):
    monkeypatch.setattr(dispatch, "DISPATCH_DIR", str(tmp_path / "d"))
    base = dispatch.submit("true", "task X", label="noop", allowed={"true"})
    for _ in range(60):                                       # wait for the bg proc to touch .done
        if os.path.exists(base + ".done"):
            break
        time.sleep(0.05)
    r = dispatch.recent()
    assert len(r) == 1 and r[0]["leg"] == "noop" and r[0]["brief"] == "task X"
    assert r[0]["running"] is False


def test_recent_empty_safe(tmp_path, monkeypatch):
    monkeypatch.setattr(dispatch, "DISPATCH_DIR", str(tmp_path / "empty"))
    assert dispatch.recent() == []


def test_full_output(tmp_path, monkeypatch):
    monkeypatch.setattr(dispatch, "DISPATCH_DIR", str(tmp_path / "d"))
    base = dispatch.submit("true", "task Y", label="noop", allowed={"true"})
    name = os.path.basename(base)
    for _ in range(60):
        if os.path.exists(base + ".done"):
            break
        time.sleep(0.05)
    o = dispatch.full_output(name)
    assert o["running"] is False and "task Y" in o["brief"]
    assert "text" in o
    # unknown dispatch → safe default, never raises
    bad = dispatch.full_output("nope-does-not-exist")
    assert bad["running"] is False and "text" in bad


def test_revise_brief(monkeypatch):
    import subprocess as _sp

    # empty brief → None, never shells out
    assert dispatch.revise_brief("") is None
    assert dispatch.revise_brief("   ") is None

    captured = {}

    class _R:
        stdout = "**Task** — do the thing\n**Output** — a file"

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        # the prompt + brief go via a temp FILE (kimi-cli -f), never argv → no injection/quoting risk
        assert cmd[0] == "kimi-cli" and cmd[1] == "-f"
        assert os.path.exists(cmd[2])
        body = open(cmd[2]).read()
        assert "ROUGH BRIEF" in body and "make me a chart" in body   # brief embedded in the meta-prompt
        return _R()

    monkeypatch.setattr(_sp, "run", fake_run)
    out = dispatch.revise_brief("make me a chart")
    assert out and out.startswith("**Task**")
    # temp file is cleaned up afterwards
    assert not os.path.exists(captured["cmd"][2])

    # CLI error / timeout → None (degrade, never crash)
    def boom(*a, **k):
        raise _sp.TimeoutExpired("kimi-cli", 1)

    monkeypatch.setattr(_sp, "run", boom)
    assert dispatch.revise_brief("anything") is None
