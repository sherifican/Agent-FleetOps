"""Claude-authored gate: the models panel must also surface llama-server SIDECARS (gemma4 vision :8336,
GLM :8090) as loaded local models — not just ollama models. Root cause of the owner-reported bug: GPU is
busy from a sidecar but ollama /api/ps is empty, so nothing showed. Pure/headless; readers monkeypatched."""
from fleet_tui.sources import modelstate
from fleet_tui.models import ModelState


# ── the pure composer now takes a `sidecars` list ──────────────────────────────────────

def test_build_includes_sidecars_as_loaded_and_busy():
    ps = [{"name": "qwen3.6:35b", "size": 23e9, "expires_at": ""}]
    # a sidecar with a LIVE request on its own port → in-flight (busy from its OWN flag now,
    # NOT global gpu_busy — see the fix in _sidecar_busy).
    sidecars = [{"name": "gemma4-e4b-q4-k-m", "port": 8336, "busy": True}]
    states = modelstate.build_model_states(ps, ["qwen3.6:35b", "gemma4:12b"],
                                           now_epoch=1.0, gpu_busy=True, sidecars=sidecars)
    side = [s for s in states if "8336" in s.name]
    assert len(side) == 1, "the :8336 sidecar must appear exactly once"
    assert side[0].loaded is True
    assert side[0].busy is True            # from the sidecar's own request flag
    # an IDLE sidecar is NOT in-flight even when the GPU is busy (the owner-reported false-positive)
    idle = [{"name": "gemma4-e4b-q4-k-m", "port": 8336, "busy": False}]
    st2 = modelstate.build_model_states(ps, [], 1.0, gpu_busy=True, sidecars=idle)
    assert [s for s in st2 if "8336" in s.name][0].busy is False


def test_sidecar_name_identifies_the_port():
    # the display name must make it distinguishable from an ollama model (owner sees WHICH thing is loaded)
    sidecars = [{"name": "gemma4-e4b-q4-k-m", "port": 8336}]
    states = modelstate.build_model_states([], [], 1.0, gpu_busy=False, sidecars=sidecars)
    assert len(states) == 1
    assert "8336" in states[0].name and "gemma4-e4b-q4-k-m" in states[0].name


def test_sidecars_grouped_with_loaded_before_cold():
    # order: ollama-loaded, then sidecars (also loaded), then cold on-disk models
    ps = [{"name": "qwen3-coder:30b", "size": 19e9, "expires_at": ""}]
    sidecars = [{"name": "gemma4-e4b-q4-k-m", "port": 8336}]
    states = modelstate.build_model_states(ps, ["qwen3-coder:30b", "llama3:8b"],
                                           1.0, gpu_busy=True, sidecars=sidecars)
    loaded = [s for s in states if s.loaded]
    cold = [s for s in states if not s.loaded]
    assert all(s.loaded for s in loaded) and len(loaded) == 2      # ollama model + sidecar
    assert [s.name for s in cold] == ["llama3:8b"]                 # cold set unchanged


def test_backward_compat_no_sidecars_arg():
    # existing callers pass no sidecars → behaves EXACTLY as before (default empty)
    ps = [{"name": "qwen3.6:35b", "size": 23e9, "expires_at": ""}]
    a = modelstate.build_model_states(ps, ["qwen3.6:35b", "gemma4:12b"], 1.0, gpu_busy=False)
    assert [s.name for s in a] == ["qwen3.6:35b", "gemma4:12b"]
    assert a[0].loaded is True and a[1].loaded is False


def test_empty_sidecars_adds_nothing():
    states = modelstate.build_model_states([], ["m1"], 1.0, gpu_busy=False, sidecars=[])
    assert [s.name for s in states] == ["m1"]


# ── the sidecar reader (the only I/O) — safe + parses OpenAI /v1/models ──────────────────

def test_read_sidecars_safe_when_endpoint_down(monkeypatch):
    def boom(*a, **k):
        raise OSError("connection refused")
    monkeypatch.setattr(modelstate.urllib.request, "urlopen", boom)
    assert modelstate.read_sidecars() == []          # asleep/unbound sidecars → [], never raises


def test_read_sidecars_parses_v1_models(monkeypatch):
    # one endpoint responds with an OpenAI-style /v1/models payload → one loaded sidecar record
    class _Resp:
        def __init__(self, body): self._b = body.encode()
        def read(self): return self._b
        def __enter__(self): return self
        def __exit__(self, *a): return False
    def fake_urlopen(url, timeout=0):
        if "8336" in str(url):
            return _Resp('{"data":[{"id":"gemma4-e4b-q4-k-m"}]}')
        raise OSError("down")                         # :8090 asleep
    monkeypatch.setattr(modelstate.urllib.request, "urlopen", fake_urlopen)
    recs = modelstate.read_sidecars()
    assert any(r["name"] == "gemma4-e4b-q4-k-m" and r["port"] == 8336 for r in recs)
    assert all(r["port"] != 8090 for r in recs)      # the down endpoint contributes nothing


def test_module_stays_headless():
    import sys
    src = open(sys.modules["fleet_tui.sources.modelstate"].__file__).read()
    assert "import textual" not in src
