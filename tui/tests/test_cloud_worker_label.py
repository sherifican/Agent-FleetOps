"""Cloud worker rows must not repeat 'Claude' or 'worker'.

Owner-reported 2026-08-08: the MODELS panel rendered
    Claude (claude-fable-5) · worker · worker
which says 'Claude' twice (once as the prefix, once inside the model id) and
'worker' twice (once in the name, once from the activity field).

Wanted: the model title alone as the name, and a single worker marker from
activity — rendering as "Claude-Fable-5 · worker".

These assertions are about the RENDERED SHAPE, not about internals, so an
implementation is free to change how it gets there.
"""
import re
from fleet_tui.sources import cloud_legs


def _rows(monkeypatch, cmdline):
    """Drive external_claude_workers() with one fake process cmdline.

    NOTE: patches _claude_cmdlines with raising=True (the default). If that helper is
    ever renamed, this test FAILS loudly rather than silently patching nothing and
    falling through to the real process table — which would make every assertion
    below depend on whatever happens to be running.  _claude_cmdlines yields
    cmdline STRINGS; the source splits them itself.
    """
    monkeypatch.setattr(cloud_legs, "_claude_cmdlines", lambda: [cmdline])
    return cloud_legs.external_claude_workers()


def _render(row):
    """Mirror how format.py joins name + activity, so the test sees what the user sees."""
    name = str(row.get("name", "") or "")
    act = str(row.get("activity", "") or "")
    return f"{name} · {act}" if act else name


def test_no_duplicate_claude_token(monkeypatch):
    rows = _rows(monkeypatch, "claude -p --model claude-fable-5")
    assert rows, "a -p worker with --model should produce a row"
    text = _render(rows[0]).lower()
    assert text.count("claude") == 1, f"'claude' appears more than once: {_render(rows[0])!r}"


def test_no_duplicate_worker_token(monkeypatch):
    rows = _rows(monkeypatch, "claude -p --model claude-fable-5")
    text = _render(rows[0]).lower()
    assert text.count("worker") == 1, f"'worker' appears more than once: {_render(rows[0])!r}"


def test_name_is_the_model_title_only(monkeypatch):
    """The name should be the model, not a sentence about it."""
    rows = _rows(monkeypatch, "claude -p --model claude-fable-5")
    name = rows[0]["name"]
    assert "·" not in name, f"name should not carry its own separator: {name!r}"
    assert "worker" not in name.lower(), f"name should not carry the worker marker: {name!r}"
    # Matches the convention already used for every other Claude row in the panel.
    assert re.fullmatch(r"Claude \(Fable 5\)", name), f"expected 'Claude (Fable 5)', got {name!r}"


def test_worker_marker_lives_in_activity(monkeypatch):
    rows = _rows(monkeypatch, "claude -p --model claude-fable-5")
    assert "worker" in str(rows[0].get("activity", "")).lower()


def test_unknown_model_still_titled_and_singular(monkeypatch):
    rows = _rows(monkeypatch, "claude -p --model claude-zzz-9")
    text = _render(rows[0]).lower()
    assert text.count("claude") == 1
    assert text.count("worker") == 1


def test_worker_without_model_flag(monkeypatch):
    """No --model: still exactly one 'claude' and one 'worker'."""
    rows = _rows(monkeypatch, "claude -p")
    assert rows
    text = _render(rows[0]).lower()
    assert text.count("claude") == 1, _render(rows[0])
    assert text.count("worker") == 1, _render(rows[0])


def test_non_worker_is_not_listed(monkeypatch):
    """The interactive orchestrator has no -p/--print token and must not appear."""
    rows = _rows(monkeypatch, "claude --json-path /tmp/x --spawned-by z")
    assert rows == []


def test_never_raises(monkeypatch):
    """Source purity contract: returns [] rather than raising."""
    def boom():
        raise OSError("proc table unreadable")
    monkeypatch.setattr(cloud_legs, "_claude_cmdlines", boom)
    assert cloud_legs.external_claude_workers() == []
