"""Source gate for the installed-models inventory (MODELS panel → click idle → ModelsListModal).

`build_installed()` is the PURE composer: raw ollama /api/tags records → InstalledModel list,
sorted by size desc, with size_gb + family/quant/param_size/modified. `installed_models()` is the
convenience that reads /api/tags; it must SAFE-DEFAULT to [] when ollama is unreachable (never raise).
"""
from fleet_tui.models import InstalledModel
from fleet_tui.sources import modelstate


_TAGS = [
    {
        "name": "gemma4:12b",
        "modified_at": "2026-06-30T11:22:33.000000000-07:00",
        "size": 8_100_000_000,
        "details": {"family": "gemma3", "parameter_size": "12.2B", "quantization_level": "Q4_K_M"},
    },
    {
        "name": "qwen3-coder:30b",
        "modified_at": "2026-07-01T09:00:00.000000000-07:00",
        "size": 18_600_000_000,
        "details": {"family": "qwen3", "parameter_size": "30.5B", "quantization_level": "Q4_K_M"},
    },
    {
        # minimal/degraded record: no details block, no size
        "name": "tiny:latest",
        "modified_at": "",
    },
]


def test_build_installed_maps_and_sorts_desc():
    out = modelstate.build_installed(_TAGS)
    assert [m.name for m in out] == ["qwen3-coder:30b", "gemma4:12b", "tiny:latest"]  # size desc
    coder = out[0]
    assert isinstance(coder, InstalledModel)
    assert coder.size_gb == 18.6
    assert coder.family == "qwen3"
    assert coder.quant == "Q4_K_M"
    assert coder.param_size == "30.5B"
    assert coder.modified == "2026-07-01"          # date portion only


def test_build_installed_degrades_on_missing_fields():
    tiny = modelstate.build_installed(_TAGS)[-1]
    assert tiny.name == "tiny:latest"
    assert tiny.size_gb == 0.0
    assert tiny.family == "" and tiny.quant == "" and tiny.param_size == "" and tiny.modified == ""


def test_build_installed_empty_safe():
    assert modelstate.build_installed([]) == []
    assert modelstate.build_installed(None) == []


def test_installed_models_safe_default_when_ollama_down(monkeypatch):
    def _boom(*a, **k):
        raise OSError("connection refused")
    monkeypatch.setattr(modelstate.urllib.request, "urlopen", _boom)
    assert modelstate.installed_models() == []      # never raises → [] inventory


def test_installed_models_reads_tags(monkeypatch):
    monkeypatch.setattr(modelstate, "read_tags_full", lambda: _TAGS)
    out = modelstate.installed_models()
    assert len(out) == 3
    assert out[0].name == "qwen3-coder:30b"         # largest first
