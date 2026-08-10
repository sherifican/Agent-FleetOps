"""Gate for the cloud-leg activity source + its MODELS-panel formatter. Pure: dispatch dicts in,
cloud-leg records / display string out. No I/O, no Textual. Colors route through anim/valid CSS names."""
import time

from fleet_tui.sources.cloud_legs import is_cloud_leg, active_cloud_legs
from fleet_tui.widgets.format import format_cloud_legs


# ---------- is_cloud_leg ----------

def test_is_cloud_leg_matches_cloud():
    assert is_cloud_leg("codex-fleet")
    assert is_cloud_leg("grok-research")
    assert is_cloud_leg("kimi")
    assert is_cloud_leg("CODEX-driver")          # case-insensitive


def test_is_cloud_leg_rejects_local_and_garbage():
    assert not is_cloud_leg("qwen3-coder:30b")
    assert not is_cloud_leg("gemma4:12b")
    assert not is_cloud_leg("glm-4.7-flash")
    assert not is_cloud_leg("")
    assert not is_cloud_leg(None)
    assert not is_cloud_leg(123)


# ---------- active_cloud_legs ----------

def test_active_cloud_legs_empty():
    assert active_cloud_legs([]) == []
    assert active_cloud_legs(None) == []


def test_active_cloud_legs_filters_to_running_cloud_only():
    disp = [
        {"leg": "grok-research", "running": True, "brief": "look into X", "when": "20260703-120000"},
        {"leg": "codex-fleet", "running": False, "brief": "already done"},     # not running → excluded
        {"leg": "qwen3-coder", "running": True, "brief": "local work"},        # local → excluded
        {"leg": "grok-code", "running": True, "brief": "fix bug"},
    ]
    out = active_cloud_legs(disp)
    assert [c["name"] for c in out] == ["grok-research", "grok-code"]
    assert out[0]["activity"] == "look into X"
    assert isinstance(out[0]["started"], float)


def test_active_cloud_legs_activity_falls_back_to_tail():
    out = active_cloud_legs([{"leg": "codex-driver", "running": True, "tail": "step 3/5"}])
    assert out[0]["activity"] == "step 3/5"


def test_active_cloud_legs_never_raises_on_garbage():
    out = active_cloud_legs([None, 123, "str", {"leg": None, "running": True}, {"running": True}])
    assert isinstance(out, list)


# ---------- format_cloud_legs ----------

def test_format_cloud_legs_empty():
    assert "none active" in format_cloud_legs([])


def test_format_cloud_legs_shows_name_and_activity():
    out = format_cloud_legs([{"name": "grok-research", "activity": "look into X", "started": time.time()}])
    assert "grok-research" in out
    assert "look into X" in out


def test_format_cloud_legs_never_raises_on_garbage():
    out = format_cloud_legs([None, {"name": None, "activity": None, "started": None}])
    assert isinstance(out, str)


def test_cloud_snapshot_merges_and_dedups(monkeypatch):
    from fleet_tui.sources import cloud_legs
    monkeypatch.setattr(cloud_legs, "codex_status", lambda: [])   # hermetic
    monkeypatch.setattr(cloud_legs, "kimi_status", lambda: [])    # hermetic: a live kimi-cli leg previously leaked in as "kimi K3"
    monkeypatch.setattr(cloud_legs, "external_cloud_procs", lambda: [
        {"name": "codex (session)", "activity": "interactive session", "started": None},
        {"name": "kimi (session)", "activity": "interactive session", "started": None}])
    disp = [{"leg": "codex-fleet", "running": True, "brief": "auditing X", "when": "20260703-120000"}]
    names = [c["name"] for c in cloud_legs.cloud_snapshot(disp)]
    assert "codex-fleet" in names           # the fleet dispatch (has the 'what')
    assert "kimi (session)" in names        # external kimi session — no kimi dispatch → included
    assert "codex (session)" not in names   # deduped: codex already has a running dispatch


def test_format_cloud_legs_markup_escapes_activity():
    out = format_cloud_legs([{"name": "codex", "activity": "weird [tag]", "started": None}])
    assert "\\[tag]" in out


# ---------- Antigravity (agy CLI) cloud-leg detection ----------

def test_is_cloud_leg_matches_antigravity():
    assert is_cloud_leg("agy-pro")
    assert is_cloud_leg("agy-flash")
    assert is_cloud_leg("AGY-pro")               # case-insensitive


def test_is_cloud_leg_antigravity_no_false_positive_on_locals():
    # adding the "agy" marker must not accidentally match any local model name
    for local in ("qwen3-coder:30b", "gemma4:12b", "glm-4.7-flash", "ornith:35b", "qwen3.6:35b-a3b"):
        assert not is_cloud_leg(local)


def test_active_cloud_legs_includes_antigravity_dispatch():
    out = active_cloud_legs([{"leg": "agy-pro", "running": True, "brief": "reason about X",
                              "when": "20260714-140000"}])
    assert [c["name"] for c in out] == ["agy-pro"]
    assert out[0]["activity"] == "reason about X"


def test_external_cloud_procs_detects_agy_as_antigravity(monkeypatch):
    from fleet_tui.sources import cloud_legs

    # reset the 15s subprocess cache so our monkeypatched pgrep is actually consulted
    cloud_legs._ext_cache["t"] = 0.0
    cloud_legs._ext_cache["v"] = []

    class _R:
        def __init__(self, rc, out=""):
            self.returncode = rc
            self.stdout = out

    def fake_run(cmd, **kw):
        # cmd == ["pgrep", "-x", <name>]; only 'agy' is "running"
        name = cmd[-1] if isinstance(cmd, (list, tuple)) else ""
        return _R(0, "123456\n") if name == "agy" else _R(1, "")

    monkeypatch.setattr(cloud_legs.subprocess, "run", fake_run)
    try:
        names = [p["name"] for p in cloud_legs.external_cloud_procs()]
    finally:
        cloud_legs._ext_cache["t"] = 0.0     # don't leak the fake result into later tests
        cloud_legs._ext_cache["v"] = []
    assert "antigravity (session)" in names
    assert "codex (session)" not in names    # non-running clouds produce no phantom session
