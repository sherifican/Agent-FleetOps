"""Claude-authored gate for Wave 2 — dispatch PARTIAL/degraded surfacing.
A cloud-leg run that produced output but exited rc!=0 leaves a `<out>.PARTIAL` marker
(codex-fleet / grok-dispatch, H3 audit fix). The TUI must treat that as a distinct
'finished-but-degraded' state — NOT a clean done. Pure-source tests; no textual."""
import os
import time
import json
from fleet_tui.sources import dispatch


def _finish(base):
    for _ in range(60):
        if os.path.exists(base + ".done"):
            return
        time.sleep(0.05)


def test_partial_marker_on_out_path_detected(tmp_path, monkeypatch):
    # TUI passes `<base>.out` as the wrapper's OUT arg, so the marker is `<base>.out.PARTIAL`
    monkeypatch.setattr(dispatch, "DISPATCH_DIR", str(tmp_path / "d"))
    base = dispatch.submit("true", "degraded task", label="codex", allowed={"true"})
    _finish(base)
    with open(base + ".out.PARTIAL", "w") as f:
        f.write(json.dumps({"partial": True, "worker_rc": 2, "ts": "2026-07-07T00:00:00Z"}))
    r = dispatch.recent()
    assert len(r) == 1
    assert r[0]["partial"] is True
    assert r[0]["running"] is False        # partial is a finished state, not still-running


def test_bare_partial_marker_also_detected(tmp_path, monkeypatch):
    # robustness: a `<base>.PARTIAL` adjacent marker must also count
    monkeypatch.setattr(dispatch, "DISPATCH_DIR", str(tmp_path / "d"))
    base = dispatch.submit("true", "t", label="grok", allowed={"true"})
    _finish(base)
    open(base + ".PARTIAL", "w").close()   # empty marker — presence is the signal, must not raise
    r = dispatch.recent()
    assert r[0]["partial"] is True


def test_clean_dispatch_is_not_partial(tmp_path, monkeypatch):
    monkeypatch.setattr(dispatch, "DISPATCH_DIR", str(tmp_path / "d"))
    base = dispatch.submit("true", "clean task", label="noop", allowed={"true"})
    _finish(base)
    r = dispatch.recent()
    assert r[0]["partial"] is False
    assert r[0]["running"] is False


def test_full_output_exposes_partial(tmp_path, monkeypatch):
    monkeypatch.setattr(dispatch, "DISPATCH_DIR", str(tmp_path / "d"))
    base = dispatch.submit("true", "z", label="codex", allowed={"true"})
    _finish(base)
    open(base + ".out.PARTIAL", "w").close()
    o = dispatch.full_output(os.path.basename(base))
    assert o.get("partial") is True
    # unknown dispatch still safe
    assert dispatch.full_output("nope")["partial"] is False


def test_partial_field_always_present_and_safe(tmp_path, monkeypatch):
    # every recent() row must carry the key (bool), even with no marker, and never raise
    monkeypatch.setattr(dispatch, "DISPATCH_DIR", str(tmp_path / "empty"))
    assert dispatch.recent() == []
    monkeypatch.setattr(dispatch, "DISPATCH_DIR", str(tmp_path / "d"))
    base = dispatch.submit("true", "q", label="noop", allowed={"true"})
    _finish(base)
    assert isinstance(dispatch.recent()[0]["partial"], bool)
