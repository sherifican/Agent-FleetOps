"""Gate: sidecars report their REAL VRAM (from the llama-server process) instead of a 0.0 placeholder."""
from fleet_tui.sources import modelstate


def test_read_sidecars_attaches_gb_from_process(monkeypatch):
    # sidecar endpoint responds; a matching llama-server proc uses 144 MiB on GPU
    class _R:
        def __init__(s, b): s._b = b.encode()
        def read(s): return s._b
        def __enter__(s): return s
        def __exit__(s, *a): return False
    monkeypatch.setattr(modelstate.urllib.request, "urlopen",
                        lambda url, timeout=0: _R('{"data":[{"id":"gemma4-e4b-q4-k-m"}]}') if "8336" in str(url) else (_ for _ in ()).throw(OSError()))
    # stub the port->VRAM resolver to return 144 MiB for :8336
    monkeypatch.setattr(modelstate, "_sidecar_vram_mb", lambda port: 144 if port == 8336 else 0)
    recs = modelstate.read_sidecars()
    r = next(x for x in recs if x["port"] == 8336)
    assert r.get("gb", 0) > 0, "sidecar should carry real VRAM, not 0"
    assert round(r["gb"], 1) == 0.1   # 144 MiB ≈ 0.1 GB


def test_build_uses_sidecar_gb():
    states = modelstate.build_model_states([], [], 1.0, gpu_busy=True,
                                           sidecars=[{"name": "gemma4-e4b-q4-k-m", "port": 8336, "gb": 0.1}])
    s = [x for x in states if "8336" in x.name][0]
    assert s.gb == 0.1 and s.loaded is True


def test_sidecar_vram_safe_on_error(monkeypatch):
    modelstate._sidecar_vram_cache.clear()   # bypass any live value cached by an app-integration test
    monkeypatch.setattr(modelstate.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(OSError()))
    assert modelstate._sidecar_vram_mb(8336) == 0   # never raises → 0


def test_sidecar_vram_SUMS_across_gpus_and_pids(monkeypatch):
    """A sidecar that spans BOTH cards shows the SAME pid on two nvidia-smi rows (live :8336 did:
    148 + 146 MiB). It must SUM them, not take the first — and it must ignore unrelated pids and
    tolerate pgrep returning multiple pids (sum all matching)."""
    import types
    def fake_run(cmd, capture_output=False, text=False, timeout=None):
        if cmd and cmd[0] == "pgrep":
            return types.SimpleNamespace(returncode=0, stdout="3235632\n3235633\n")   # two pids
        if cmd and cmd[0] == "nvidia-smi":
            # the pids on two GPUs + one unrelated process that must NOT be counted
            return types.SimpleNamespace(returncode=0,
                stdout="3235632, 148\n999999, 5000\n3235632, 146\n3235633, 60\n")
        return types.SimpleNamespace(returncode=1, stdout="")
    monkeypatch.setattr(modelstate.subprocess, "run", fake_run)
    modelstate._sidecar_vram_cache.clear()
    assert modelstate._sidecar_vram_mb(8336) == 354   # 148 + 146 + 60 ; the 5000 (pid 999999) excluded


def test_sidecar_vram_zero_when_no_matching_proc(monkeypatch):
    import types
    def fake_run(cmd, capture_output=False, text=False, timeout=None):
        if cmd and cmd[0] == "pgrep":
            return types.SimpleNamespace(returncode=1, stdout="")     # no llama-server on that port
        return types.SimpleNamespace(returncode=0, stdout="111, 900\n")
    monkeypatch.setattr(modelstate.subprocess, "run", fake_run)
    modelstate._sidecar_vram_cache.clear()
    assert modelstate._sidecar_vram_mb(8090) == 0
