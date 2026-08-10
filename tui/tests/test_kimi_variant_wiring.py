"""Gate: cloud_snapshot must surface the MODEL-SPECIFIC kimi rows, not the generic 'kimi (session)'.

The whole point of the variant detection is that the MODELS panel tells the owner whether K3 or
K2.7 Code is running. If cloud_snapshot keeps emitting the flat 'kimi (session)' row, the feature
is invisible no matter how good the source is.
"""
from fleet_tui.sources import cloud_legs


def _isolate(monkeypatch, kimi_rows, ext_rows=None):
    monkeypatch.setattr(cloud_legs, "active_cloud_legs", lambda d: [], raising=False)
    monkeypatch.setattr(cloud_legs, "external_claude_workers", lambda: [], raising=False)
    monkeypatch.setattr(cloud_legs, "external_cloud_procs",
                        lambda: (ext_rows if ext_rows is not None else
                                 [{"name": "kimi (session)", "activity": "interactive session", "started": None}]),
                        raising=False)
    monkeypatch.setattr(cloud_legs, "kimi_status", lambda: kimi_rows, raising=False)
    # codex variant rows also supersede the generic 'codex (session)' row (v3.36). Without this the
    # helper is no longer isolating: codex_status() would read the LIVE process table and drop the
    # generic codex row these tests assert on — a hermeticity gap, not a kimi regression.
    monkeypatch.setattr(cloud_legs, "codex_status", lambda: [], raising=False)


def test_model_specific_row_replaces_the_generic_kimi_row(monkeypatch):
    _isolate(monkeypatch, [{"name": "kimi K3", "activity": "fleet dispatch", "started": None}])
    names = [l["name"] for l in cloud_snapshot_names(monkeypatch)]
    assert "kimi K3" in names, f"model-specific row must reach the panel, got {names}"
    assert "kimi (session)" not in names, "the generic row must NOT survive alongside the specific one"


def test_k2_7_is_named_too_not_just_k3(monkeypatch):
    _isolate(monkeypatch, [{"name": "kimi K2.7 Code", "activity": "interactive session", "started": None}])
    names = [l["name"] for l in cloud_snapshot_names(monkeypatch)]
    assert "kimi K2.7 Code" in names
    assert "kimi (session)" not in names


def test_two_concurrent_kimi_models_both_shown(monkeypatch):
    _isolate(monkeypatch, [
        {"name": "kimi K3", "activity": "fleet dispatch", "started": None},
        {"name": "kimi K2.7 Code", "activity": "interactive session", "started": None},
    ])
    names = [l["name"] for l in cloud_snapshot_names(monkeypatch)]
    assert "kimi K3" in names and "kimi K2.7 Code" in names


def test_no_kimi_running_falls_back_to_existing_behaviour(monkeypatch):
    """kimi_status() empty -> whatever external_cloud_procs said still flows through unchanged."""
    _isolate(monkeypatch, [])
    names = [l["name"] for l in cloud_snapshot_names(monkeypatch)]
    assert "kimi (session)" in names, "must not silently drop kimi when variant detection finds nothing"


def test_other_legs_are_untouched(monkeypatch):
    _isolate(monkeypatch,
             [{"name": "kimi K3", "activity": "fleet dispatch", "started": None}],
             ext_rows=[{"name": "codex (session)", "activity": "interactive session", "started": None},
                       {"name": "kimi (session)", "activity": "interactive session", "started": None}])
    names = [l["name"] for l in cloud_snapshot_names(monkeypatch)]
    assert "codex (session)" in names, "codex/grok rows must be unaffected by the kimi substitution"
    assert "kimi K3" in names
    assert "kimi (session)" not in names


def test_kimi_status_failure_does_not_break_the_panel(monkeypatch):
    """A raising kimi_status must degrade to the old row, never take cloud_snapshot down."""
    def boom():
        raise RuntimeError("detection exploded")
    _isolate(monkeypatch, [])
    monkeypatch.setattr(cloud_legs, "kimi_status", boom, raising=False)
    legs = cloud_legs.cloud_snapshot([])          # must not raise
    assert "kimi (session)" in [l["name"] for l in legs]


def cloud_snapshot_names(monkeypatch):
    return cloud_legs.cloud_snapshot([])
