"""Gate: cloud legs detected by their REAL process names — kimi's CLI runs as `kimi-code`, not `kimi`."""
from fleet_tui.sources import cloud_legs


def test_kimi_real_procname_is_registered():
    assert "kimi-code" in cloud_legs.SESSION_PROCS["kimi"]
    assert "kimi" in cloud_legs.SESSION_PROCS["kimi"]
    # orchestrator still excluded (no bare claude in session detection)
    assert "claude" not in cloud_legs.SESSION_MARKERS
    assert "claude" not in cloud_legs.SESSION_PROCS


def _fake_pgrep(match_names):
    """subprocess.run stub: returncode 0 only for `pgrep -x <name>` where name in match_names."""
    class _R:
        pass
    def run(cmd, **kw):
        r = _R()
        hit = (isinstance(cmd, list) and cmd[:2] == ["pgrep", "-x"] and len(cmd) >= 3 and cmd[2] in match_names)
        r.returncode = 0 if hit else 1
        r.stdout = "4242\n" if hit else ""
        return r
    return run


def test_external_procs_detects_kimi_code(monkeypatch):
    monkeypatch.setattr(cloud_legs.subprocess, "run", _fake_pgrep({"kimi-code"}))
    cloud_legs._ext_cache["t"] = 0.0                       # bust the 15s cache
    procs = cloud_legs.external_cloud_procs()
    assert any(p["name"].startswith("kimi") for p in procs)     # kimi shown even though comm is 'kimi-code'


def test_external_procs_no_double_add_per_label(monkeypatch):
    # both 'kimi' AND 'kimi-code' matching must yield ONE kimi entry, not two
    monkeypatch.setattr(cloud_legs.subprocess, "run", _fake_pgrep({"kimi", "kimi-code"}))
    cloud_legs._ext_cache["t"] = 0.0
    procs = cloud_legs.external_cloud_procs()
    assert sum(1 for p in procs if p["name"].startswith("kimi")) == 1


def test_external_procs_none_running(monkeypatch):
    monkeypatch.setattr(cloud_legs.subprocess, "run", _fake_pgrep(set()))
    cloud_legs._ext_cache["t"] = 0.0
    assert cloud_legs.external_cloud_procs() == []
