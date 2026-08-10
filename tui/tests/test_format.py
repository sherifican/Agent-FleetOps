"""Tests for the display formatters in fleet_tui/widgets/format.py."""
import re
import pytest


def _strip(s):
    """Remove Rich color markup so content/layout assertions ignore the coloring."""
    return re.sub(r"\[/?[^\]]*\]", "", s)
from fleet_tui.models import Job, InboxItem, HealthSnapshot, LoadedModel, FocusState
from fleet_tui.widgets.format import format_jobs, format_inbox, format_health, format_focus, format_models


def test_format_jobs_empty():
    """Test formatting an empty list of jobs."""
    assert format_jobs([]) == "(no jobs)"


def test_format_jobs_with_data():
    """Test formatting a list of jobs with data."""
    jobs = [
        Job(id="1", name="hf-watch", kind="cron", schedule="every 240m", last_status="ok"),
        Job(id="2", name="x", kind="systemcron", schedule="0 0 * * *", last_status="unknown")
    ]
    result = format_jobs(jobs)
    assert "hf-watch" in result
    assert "OK" in result
    assert "x" in result
    assert "~" not in result  # unknown status is now blank, not a noisy "~"


def test_format_inbox_empty():
    """Test formatting an empty list of inbox items."""
    assert format_inbox([]) == "inbox clear"


def test_format_inbox_with_data():
    """Test formatting a list of inbox items with data."""
    items = [
        InboxItem(source="github", title="alert", priority="crit", detail="d1"),
        InboxItem(source="hf", title="digest", priority="fyi", detail="d2")
    ]
    result = format_inbox(items)
    assert "[!]" in result
    assert "alert" in result
    assert "[.]" in result
    assert "digest" in result


def test_format_health_with_data():
    """Test formatting a health snapshot with data."""
    snap = HealthSnapshot(
        services={"hermes-gateway": True, "openrgb-server": False},
        loaded=[LoadedModel(name="qwen3-coder:30b", gb=18.6)],
        vram_note="1 model(s) span both cards",
        critical_caps=[{"cap": "ollama", "ok": True}, {"cap": "harness", "ok": False}]
    )
    result = _strip(format_health(snap))
    assert "hermes-gateway up" in result
    assert "openrgb-server DOWN" in result
    assert "qwen3-coder:30b 18.6GB" in result
    assert "vram:" in result
    assert "ollama ok" in result
    assert "harness FAIL" in result


def test_format_health_empty():
    """Test formatting an empty health snapshot."""
    snap = HealthSnapshot()
    result = format_health(snap)
    assert "services: (none)" in result
    assert "loaded: none" in result
    assert "vram:" not in result  # Empty note should be skipped


def test_format_focus_on():
    """Test formatting a focus state that is on."""
    state = FocusState(on=True, scope="noisy", since="2026-07-02T00:00:00")
    result = format_focus(state)
    assert "FOCUS ON" in result
    assert "noisy" in result
    assert "since" in result


def test_format_focus_off():
    """Test formatting a focus state that is off."""
    state = FocusState(on=False)
    result = format_focus(state)
    assert result == "focus: off"


def test_format_jobs_running_marker():
    from fleet_tui.models import Job
    out = format_jobs([Job(id="1", name="hive-lint", kind="cron", schedule="daily", running=True)])
    assert "▶" in out and "hive-lint" in out


def test_format_models_kanban():
    from fleet_tui.models import ModelState
    raw = format_models([
        ModelState(name="qwen3-coder:30b", loaded=True, gb=18.6, idle_in="4m", busy=False),
        ModelState(name="gemma4:12b", loaded=False),
    ])
    out = _strip(raw)
    # 3 kanban columns
    assert "IN-FLIGHT" in out and "LOADED" in out and "IDLE" in out
    # loaded-not-busy → LOADED column with VRAM + idle countdown; name is family-colored
    assert "qwen3-coder:30b" in out and "18.6GB" in out and "(4m)" in out
    assert "[cyan]qwen3-coder:30b[/]" in raw   # qwen family color
    assert "1 on disk" in out
    assert format_models([]) == "(no models)"
    # busy/in-flight model shows in IN-FLIGHT
    busy = _strip(format_models([ModelState(name="m", loaded=True, gb=1.0, busy=True)]))
    assert "IN-FLIGHT (1)" in busy and "● m" in busy


def test_format_coding():
    from fleet_tui.models import ModelState
    from fleet_tui.widgets.format import format_coding
    models = [
        ModelState(name="qwen3-coder:30b", loaded=True, gb=18.6, busy=True),
        ModelState(name="gemma4:12b", loaded=True, gb=8.1, idle_in="3m", busy=False),
    ]
    dispatches = [
        {"leg": "codex-fleet", "brief": "refactor the chain runner", "when": "10:02", "running": True},
        {"leg": "grok-research", "brief": "survey new coders", "when": "09:40", "running": False},
    ]
    raw = format_coding(dispatches, models, 47)
    out = _strip(raw)
    # GPU util shown + computing flag (>=20)
    assert "47%" in out and "computing" in out
    # active dispatch (running) surfaced; completed one under recent
    assert "codex-fleet" in out and "refactor the chain runner" in out
    assert "grok-research" in out and "RECENT DISPATCHES" in out
    # in-VRAM models, busy vs warm, family-colored
    assert "qwen3-coder:30b" in out and "in-flight" in out
    assert "gemma4:12b" in out and "warm" in out
    assert "[cyan]qwen3-coder:30b[/]" in raw
    # idle / empty degrade cleanly (no crash, helpful text)
    idle = _strip(format_coding([], [], 3))
    assert "idle" in idle and "none running" in idle and "none loaded" in idle


def test_sparkline():
    from fleet_tui.widgets.format import sparkline, _SPARK
    assert sparkline([], 0, 100) == ""
    s = sparkline([0, 50, 100], 0, 100)          # low → mid → high
    assert s[0] == _SPARK[0] and s[-1] == _SPARK[-1] and _SPARK.index(s[1]) in (3, 4)
    # out-of-range values clamp, never index-error
    assert sparkline([-10, 200], 0, 100) == _SPARK[0] + _SPARK[-1]
    assert len(sparkline([1, 2, 3, 4], 0, 10)) == 4


def test_format_health_disk_and_trends():
    from fleet_tui.models import HealthSnapshot
    from fleet_tui.widgets.format import format_health
    # disk: low free % → red
    snap = HealthSnapshot(disk_free_gb=10, disk_total_gb=500)
    out = _strip(format_health(snap))
    assert "disk: 10/500GB free" in out
    assert "[red]10[/]" in format_health(snap)              # <8% free → red
    # healthy disk → lighter green
    snap2 = HealthSnapshot(disk_free_gb=300, disk_total_gb=500)
    assert "[palegreen]300[/]" in format_health(snap2)
    # sparkline TREND graphs removed — GPU util % now shows inline on the gpu line, colored by load band
    g = HealthSnapshot(gpu=[{"used": 2000, "total": 16000, "temp": 39, "util": 23},
                            {"used": 15000, "total": 16000, "temp": 80, "util": 95}])
    raw = format_health(g)
    out2 = _strip(raw)
    assert "trends:" not in out2
    assert "gpu0:" in out2 and "23%" in out2 and "gpu1:" in out2 and "95%" in out2
    assert "[palegreen]23%[/]" in raw    # low util → lighter green
    assert "[red]95%[/]" in raw          # near-full util → red


def test_format_health_bridges():
    from fleet_tui.models import HealthSnapshot
    from fleet_tui.widgets.format import format_health
    # PC reachable + telegram up → both green
    net = {"pc": {"link_up": True, "reachable": True, "ip": "192.0.2.1"},
           "telegram": {"gateway_up": True, "poller": True, "last_seen_mtime": 1.0}}
    out = format_health(HealthSnapshot(), net=net)
    assert "bridges: PC" in _strip(out) and "[green]up[/]" in out
    # link up but PC unreachable → link-only (yellow); telegram down → red
    net2 = {"pc": {"link_up": True, "reachable": False}, "telegram": {"gateway_up": False}}
    o2 = format_health(HealthSnapshot(), net=net2)
    assert "link-only" in _strip(o2) and "[red]down[/]" in o2
    # no net → no bridges line (unchanged)
    assert "bridges" not in _strip(format_health(HealthSnapshot()))
    # codex bridge UP → segment shown
    net3 = {"pc": {"reachable": True}, "telegram": {"gateway_up": True},
            "codex": {"state": "up", "host_label": "WinPC"}}
    assert "codex↔WinPC" in _strip(format_health(HealthSnapshot(), net=net3))
    # codex bridge DISABLED (owner-parked) → segment omitted, not shown as "off"
    net4 = {"pc": {"reachable": True}, "telegram": {"gateway_up": True},
            "codex": {"state": "disabled", "host_label": "WinPC"}}
    o4 = _strip(format_health(HealthSnapshot(), net=net4))
    assert "bridges: PC" in o4 and "codex" not in o4


def test_format_models_animation():
    from fleet_tui.models import ModelState
    from fleet_tui.widgets.format import format_models
    from fleet_tui.widgets import anim
    busy = [ModelState(name="qwen3-coder:30b", loaded=True, gb=18.6, busy=True)]
    # static: solid ● marker
    assert "●" in _strip(format_models(busy))
    # animated: the in-flight marker becomes the spinner frame (pulsing)
    anim.set_style("braille", True)
    out = format_models(busy, frame=3)
    assert anim.spin(3) in _strip(out)


def test_colorize_log():
    from fleet_tui.widgets.format import _colorize_log
    out = _colorize_log(
        "[2026-07-03 05:40:01] start\n"
        "ERROR failed to reach [/api/ps]\n"
        "WARNING retrying\n"
        "done: wrote file"
    )
    lines = out.splitlines()
    assert "[dim]" in lines[0]                     # leading timestamp dimmed
    assert "[red]" in lines[1] and "\\[/api/ps]" in lines[1]   # error red + bracket ESCAPED (markup-safe)
    assert "[yellow]" in lines[2]                 # warning yellow
    assert "[green]" in lines[3]                   # success green
    # word-boundaried: 'broken'/'token' must NOT match 'ok'
    assert "[green]" not in _colorize_log("the build is broken")
    assert _colorize_log("") == "" and _colorize_log(None) == ""


def test_model_family_colors():
    from fleet_tui.widgets.format import _model_color, _model_family
    # same family (qwen) → same color, regardless of variant
    assert _model_color("qwen3-coder:30b") == _model_color("qwen3.6:35b-a3b-q4_K_M")
    assert _model_family("hf.co/deepreinforce-ai/Ornith-1.0-9B-GGUF:Q4_K_M") == "ornith"
    # different families → different colors (qwen vs gemma)
    assert _model_color("qwen3-coder:30b") != _model_color("gemma4:12b")


def test_format_health_temps():
    from fleet_tui.models import HealthSnapshot
    out = format_health(HealthSnapshot(cpu_temp=54, ssd_temp=38, ssd_ext_temp=30))
    assert "[palegreen]" in out  # temps are colored by threshold (lighter green when healthy)
    stripped = _strip(out)
    assert "cpu 54°C" in stripped and "ssd 38°C" in stripped and "ssd2 30°C" in stripped
    # a HOT temp goes red
    assert "[red]" in format_health(HealthSnapshot(cpu_temp=95))
    # temps omitted when unknown (0)
    assert "cpu" not in format_health(HealthSnapshot())


def test_clean_model_name():
    from fleet_tui.widgets.format import _clean_model_name
    # HF registry noise stripped, GGUF marker dropped, quant kept
    assert _clean_model_name("hf.co/deepreinforce-ai/Ornith-1.0-35B-GGUF:Q4_K_M") == "Ornith-1.0-35B:Q4_K_M"
    assert _clean_model_name("hf.co/LiquidAI/LFM2.5-8B-A1B-GGUF:Q4_K_M") == "LFM2.5-8B-A1B:Q4_K_M"
    # standard ollama names untouched
    assert _clean_model_name("qwen3-coder:30b") == "qwen3-coder:30b"
    assert _clean_model_name("gemma4:12b") == "gemma4:12b"
    assert _clean_model_name("glm-flash:latest") == "glm-flash:latest"
    # llama-server sidecars (port-tagged): strip the redundant GGUF dash-quant so the name fits the
    # kanban column (else it truncates and loses its family color → the "gray" bug). Only when a
    # `(:port)` suffix follows — ollama colon-quants above stay untouched.
    assert _clean_model_name("gemma4-e4b-q4-k-m (:8336)") == "gemma4-e4b (:8336)"
    assert _clean_model_name("glm-4.7-flash-q4_k_m (:8090)") == "glm-4.7-flash (:8090)"
    # a dash-quant WITHOUT a sidecar suffix must NOT be stripped (safety: never touch ollama names)
    assert _clean_model_name("weird-q4-k-m:latest") == "weird-q4-k-m:latest"


def test_sidecar_cell_fits_and_keeps_family_color():
    """Regression for the owner-reported 'gray + 0GB' sidecar: the shortened name must fit the kanban
    column so _pad_markup keeps the family color (not drop it on truncation) and the GB stays visible."""
    from fleet_tui.widgets.format import format_models
    from fleet_tui.models import ModelState
    s = ModelState(name="gemma4-e4b-q4-k-m (:8336)", loaded=True, gb=0.3, busy=True)
    out = format_models([s])
    plain = _strip(out)
    assert "gemma4-e4b (:8336)" in plain           # display shortened
    assert "0.3GB" in plain                          # GB not truncated away
    assert "[green]gemma4-e4b (:8336)[/]" in out     # family color PRESERVED (the anti-"gray" assert)
