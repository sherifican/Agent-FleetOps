"""Tests for the fleet-model-state source (loaded/running vs cold/idle)."""
from unittest.mock import patch

from fleet_tui.sources.modelstate import build_model_states, read_ps, read_tags
from fleet_tui.models import ModelState


def test_build_loaded_and_cold():
    now = 1000.0
    ps = [{"name": "qwen3-coder:30b", "size": 18_600_000_000, "expires_at": ""}]
    tags = ["qwen3-coder:30b", "gemma4:12b", "qwen3.6:35b-a3b-q4_K_M"]
    states = build_model_states(ps, tags, now)
    by_name = {s.name: s for s in states}
    assert by_name["qwen3-coder:30b"].loaded is True
    assert abs(by_name["qwen3-coder:30b"].gb - 18.6) < 0.1
    # the two not in ps are cold (loaded False), and not duplicated
    assert by_name["gemma4:12b"].loaded is False
    assert by_name["qwen3.6:35b-a3b-q4_K_M"].loaded is False
    assert len(states) == 3


def test_idle_countdown():
    now = 1000.0
    # expires 5 minutes from now (epoch 1300 = now+300)
    import datetime
    exp = datetime.datetime.fromtimestamp(now + 300, datetime.timezone.utc).isoformat()
    ps = [{"name": "m", "size": 1_000_000_000, "expires_at": exp}]
    states = build_model_states(ps, ["m"], now)
    assert states[0].loaded is True
    assert states[0].idle_in == "5m"


def test_build_empty():
    assert build_model_states([], [], 1000.0) == []


def test_all_cold_when_none_loaded():
    states = build_model_states([], ["a", "b"], 1000.0)
    assert all(not s.loaded for s in states)
    assert len(states) == 2


def test_reader_safety():
    with patch("urllib.request.urlopen", side_effect=Exception("boom")):
        assert read_ps() == []
        assert read_tags() == []


def test_busy_when_gpu_active():
    ps = [{"name": "m", "size": 1_000_000_000, "expires_at": ""}]
    busy = build_model_states(ps, ["m"], 1000.0, gpu_busy=True)
    assert busy[0].loaded is True and busy[0].busy is True
    idle = build_model_states(ps, ["m"], 1000.0, gpu_busy=False)
    assert idle[0].busy is False


def test_sidecar_busy_from_own_request_not_global_gpu():
    # THE FIX: an idle resident sidecar must NOT show in-flight just because the GPU is busy
    # (this is the gemma4-vision false-'in-flight' the owner spotted during the eval).
    sc_idle = [{"name": "gemma4-vision", "port": 8336, "gb": 0.3, "busy": False}]
    st = build_model_states([], [], 1000.0, gpu_busy=True, sidecars=sc_idle)
    scs = [s for s in st if "8336" in s.name][0]
    assert scs.loaded is True and scs.busy is False   # gpu_busy=True but sidecar idle → NOT busy
    # a sidecar WITH a live request on its own port shows in-flight even when the GPU is idle
    sc_busy = [{"name": "gemma4-vision", "port": 8336, "gb": 0.3, "busy": True}]
    st2 = build_model_states([], [], 1000.0, gpu_busy=False, sidecars=sc_busy)
    assert [s for s in st2 if "8336" in s.name][0].busy is True


def test_bonsai_sidecar_registered_and_loads():
    """Bonsai (on-demand llama-server :8100) is a known sidecar and shows as a LOADED model with
    in-flight when its sidecar record is present (mirrors the gemma4/glm sidecar handling)."""
    from fleet_tui.sources.modelstate import SIDECARS, build_model_states
    assert (8100, "bonsai-ternary") in SIDECARS
    sc = [{"name": "Bonsai-Ternary-27B", "port": 8100, "gb": 9.0, "busy": True}]
    states = build_model_states([], [], 1000.0, sidecars=sc)
    b = [s for s in states if "Bonsai-Ternary-27B" in s.name]
    assert len(b) == 1
    assert b[0].loaded is True and b[0].busy is True and b[0].gb == 9.0
    assert ":8100" in b[0].name          # port in the name → distinguishable from an ollama model
