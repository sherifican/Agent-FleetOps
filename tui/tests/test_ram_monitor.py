"""Gate: RAM + swap readout in the HEALTH panel.

Owner's box hit "Device memory is nearly full — an application was forced to stop" on 2026-08-08
with NO memory readout anywhere in the TUI. The real numbers at that moment:

    MemTotal 31733532 kB · MemAvailable 23884604 kB   -> RAM ~24% used, HEALTHY
    SwapTotal 8388604 kB · SwapFree 1195656 kB        -> swap ~86% used, NEARLY EXHAUSTED

So a RAM-only monitor would have shown green during the actual incident. That is why swap is gated
here too, with tighter thresholds. Several tests below exist specifically to stop swap being dropped
or its thresholds being "tidied" to match RAM's.

Hermetic: every test feeds fixture text; none reads the live /proc/meminfo.
"""
import pytest
from fleet_tui.sources import health
from fleet_tui.widgets import format as fmt
from fleet_tui.models import HealthSnapshot


# The exact /proc/meminfo values captured during the incident.
INCIDENT = """MemTotal:       31733532 kB
MemFree:         2118392 kB
MemAvailable:   23884604 kB
Buffers:          412000 kB
Cached:         18000000 kB
SwapTotal:       8388604 kB
SwapFree:        1195656 kB
"""

ROOMY = """MemTotal:       31733532 kB
MemFree:        28000000 kB
MemAvailable:   30000000 kB
SwapTotal:       8388604 kB
SwapFree:        8388604 kB
"""

NO_SWAP = """MemTotal:       31733532 kB
MemAvailable:   23884604 kB
SwapTotal:             0 kB
SwapFree:              0 kB
"""


def _read(monkeypatch, text):
    """Drive read_meminfo() off fixture text, bypassing the module cache."""
    import builtins
    real_open = builtins.open

    def fake_open(path, *a, **k):
        if str(path) == "/proc/meminfo":
            from io import StringIO
            return StringIO(text)
        return real_open(path, *a, **k)

    monkeypatch.setattr(builtins, "open", fake_open)
    # the reader is cached; clear whatever cache backs it so fixtures aren't ignored
    for attr in ("_cache", "_CACHE", "_cached_values"):
        c = getattr(health, attr, None)
        if isinstance(c, dict):
            c.clear()
    return health.read_meminfo()


# ---------- parsing ----------

def test_used_is_total_minus_available_not_minus_free(monkeypatch):
    """The single most important parsing decision.

    MemFree excludes page cache the kernel hands back on demand. This box held ~18GB of cache during
    the incident, so a MemFree-based 'used' would report ~93% while the machine genuinely had 22.8GB
    available. That would fire red constantly and train the owner to ignore the row.
    """
    m = _read(monkeypatch, INCIDENT)
    assert m["ram_pct"] == pytest.approx(24, abs=2), m
    assert m["ram_used_gb"] == pytest.approx(7.5, abs=0.4), m


def test_totals_in_gib(monkeypatch):
    m = _read(monkeypatch, INCIDENT)
    assert m["ram_total_gb"] == pytest.approx(30.3, abs=0.3), m
    assert m["swap_total_gb"] == pytest.approx(8.0, abs=0.2), m


def test_swap_used_is_total_minus_free(monkeypatch):
    """Catches reporting SwapFree as if it were swap used."""
    m = _read(monkeypatch, INCIDENT)
    assert m["swap_used_gb"] == pytest.approx(6.9, abs=0.3), m
    assert m["swap_pct"] == pytest.approx(86, abs=3), m


def test_no_swap_configured_does_not_divide_by_zero(monkeypatch):
    m = _read(monkeypatch, NO_SWAP)
    assert m["swap_total_gb"] == 0
    assert m["swap_pct"] == 0


def test_garbage_yields_safe_default(monkeypatch):
    m = _read(monkeypatch, "this is not meminfo\n@@@@\n")
    assert m["ram_total_gb"] == 0 and m["ram_pct"] == 0


def test_never_raises(monkeypatch):
    def boom(*a, **k):
        raise OSError("proc unreadable")
    import builtins
    monkeypatch.setattr(builtins, "open", boom)
    for attr in ("_cache", "_CACHE", "_cached_values"):
        c = getattr(health, attr, None)
        if isinstance(c, dict):
            c.clear()
    m = health.read_meminfo()
    assert m["ram_pct"] == 0 and m["swap_pct"] == 0


# ---------- the model ----------

def test_snapshot_has_the_fields():
    s = HealthSnapshot()
    for f in ("ram_used_gb", "ram_total_gb", "ram_pct",
              "swap_used_gb", "swap_total_gb", "swap_pct"):
        assert hasattr(s, f), f
        assert getattr(s, f) == 0, f"{f} must default to 0 so existing call sites keep working"


# ---------- rendering ----------

def test_ram_row_rendered():
    out = fmt.format_health(HealthSnapshot(ram_used_gb=7.5, ram_total_gb=30.3, ram_pct=24))
    assert "ram:" in out, out
    assert "30.3" in out and "7.5" in out, out
    assert "24%" in out, out


def test_swap_row_rendered():
    out = fmt.format_health(HealthSnapshot(swap_used_gb=6.9, swap_total_gb=8.0, swap_pct=86))
    assert "swap:" in out, out
    assert "86%" in out, out


def test_no_rows_when_unknown():
    """A zeroed snapshot must not print a misleading 0.0/0.0GB row."""
    out = fmt.format_health(HealthSnapshot())
    assert "ram:" not in out and "swap:" not in out, out


def test_swap_row_hidden_when_no_swap_configured():
    out = fmt.format_health(HealthSnapshot(ram_used_gb=7.5, ram_total_gb=30.3, ram_pct=24))
    assert "swap:" not in out, out


# ---------- the thresholds that make this feature worth having ----------

def test_incident_state_is_not_all_green():
    """THE regression test for this whole feature.

    Feed the exact numbers from the near-crash. RAM was genuinely healthy; swap was nearly gone.
    If this renders entirely green, the panel would have said 'fine' while the OS was killing an
    application — which is the failure this feature exists to prevent.
    """
    out = fmt.format_health(HealthSnapshot(
        ram_used_gb=7.5, ram_total_gb=30.3, ram_pct=24,
        swap_used_gb=6.9, swap_total_gb=8.0, swap_pct=86))
    assert "red" in out, f"swap at 86% must render red, got: {out}"


def test_swap_threshold_is_tighter_than_ram():
    """Catches 'tidying' the swap thresholds to match RAM's.

    60% is comfortably green for RAM but already yellow-or-worse for swap: sustained swap use on this
    box means it is thrashing. If both rows colour identically at the same percentage, the asymmetry
    that makes swap useful has been removed.
    """
    ram60 = fmt.format_health(HealthSnapshot(ram_used_gb=18.0, ram_total_gb=30.3, ram_pct=60))
    swap60 = fmt.format_health(HealthSnapshot(swap_used_gb=4.8, swap_total_gb=8.0, swap_pct=60))
    assert "red" not in ram60, f"RAM at 60% should not be red: {ram60}"
    assert ("red" in swap60) or ("yellow" in swap60), f"swap at 60% must warn: {swap60}"


def test_ram_high_is_red():
    out = fmt.format_health(HealthSnapshot(ram_used_gb=28.5, ram_total_gb=30.3, ram_pct=94))
    assert "red" in out, out


# ---------- the honesty constraint ----------

def test_no_fabricated_ram_temperature():
    """This box has NO DIMM temperature sensor (hwmon exposes only k10temp, nvme and two NICs; the
    jc42 SPD driver is absent). The owner asked for a temperature, and the honest answer is that the
    hardware cannot provide one. A fabricated or CPU-derived stand-in would be acted on as real."""
    s = HealthSnapshot()
    assert not hasattr(s, "ram_temp"), "there is no DIMM sensor on this box; do not add a fake field"
    out = fmt.format_health(HealthSnapshot(ram_used_gb=7.5, ram_total_gb=30.3, ram_pct=24))
    ram_line = next((l for l in out.splitlines() if "ram:" in l), "")
    assert "°C" not in ram_line, f"no RAM temperature exists to report: {ram_line}"
