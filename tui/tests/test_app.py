import pytest
from fleet_tui.models import HealthSnapshot, FocusState
from fleet_tui.app import gather_data


def test_gather_data(monkeypatch):
    """Test the gather_data function with mocked sources."""
    # Mock all the source functions
    monkeypatch.setattr("fleet_tui.app.jobs.list_jobs", lambda: [])
    monkeypatch.setattr("fleet_tui.app.inbox.list_inbox", lambda: [])
    monkeypatch.setattr("fleet_tui.app.health.snapshot", lambda: HealthSnapshot())
    monkeypatch.setattr("fleet_tui.app.modelstate.list_models", lambda: [])
    monkeypatch.setattr("fleet_tui.app.modelstate.read_gpu_util", lambda: 0)
    monkeypatch.setattr("fleet_tui.app.dispatch.recent", lambda: [])
    monkeypatch.setattr("fleet_tui.app.cloud_legs.external_cloud_procs", lambda: [])
    monkeypatch.setattr("fleet_tui.app.cloud_legs._claude_cmdlines", lambda: [])   # hermetic: no real claude workers   # hermetic: no pgrep
    monkeypatch.setattr("fleet_tui.app.cloud_legs.codex_status", lambda: [])       # hermetic: no live codex procs
    monkeypatch.setattr("fleet_tui.app.cloud_legs.kimi_status", lambda: [])        # hermetic: no live kimi procs (leak found when a real kimi-cli leg polluted the assert)
    monkeypatch.setattr("fleet_tui.app.focus.read_state", lambda: FocusState(on=False))
    monkeypatch.setattr("fleet_tui.app.network.status", lambda: {"pc": {}, "telegram": {}})
    monkeypatch.setattr("fleet_tui.app._net_cache", {"t": 0.0, "v": None})   # bypass the 20s cache

    # Call the function
    result = gather_data()

    # Assert the structure — gather_data now returns RAW objects (formatting happens in _paint so the
    # cosmetic animation timer can re-render without re-gathering)
    assert isinstance(result, dict)
    assert set(result.keys()) == {"jobs", "inbox", "health", "models", "focus", "alerts", "dispatches", "util", "network", "ops", "cloud", "posture", "passback", "research_playlists"}

    # Assert the content — raw lists/objects, not formatted strings
    assert result["jobs"] == []
    assert result["inbox"] == []
    assert result["models"] == []
    assert result["dispatches"] == []
    assert result["ops"] == []                   # no jobs + no dispatches → empty unified ops list
    assert result["cloud"] == []                 # no dispatches → no active cloud legs
    assert result["util"] == 0
    assert result["focus"].on is False           # a FocusState object now, not "focus: off"
    assert isinstance(result["health"], HealthSnapshot)
    assert result["alerts"] == []                # empty health/jobs → no alerts


def test_app_instantiation():
    """Test that FleetTUI can be instantiated and has correct bindings."""
    from fleet_tui.app import FleetTUI
    
    # Test instantiation
    app = FleetTUI()
    
    # Test bindings
    binding_keys = {(b.key if hasattr(b, "key") else b[0]) for b in FleetTUI.BINDINGS}
    # ops-tab keys (F, j/k/up/down/enter ops-nav, slash filter) removed with the Ops tab in the
    # public Fleet-tab-only build
    assert binding_keys == {"q", "question_mark", "f", "r", "i", "o", "x", "d", "s", "c", "a", "p",
                            "ctrl+grave_accent", "ctrl+q", "u", "w", "m", "C"}


def test_app_methods():
    """Test that FleetTUI has the expected callable attributes."""
    from fleet_tui.app import FleetTUI
    from textual.app import App
    
    # Test that methods exist
    assert hasattr(FleetTUI, 'action_toggle_focus')
    assert hasattr(FleetTUI, 'action_refresh_now')
    assert hasattr(FleetTUI, 'refresh_panels')
    
    # Test that refresh is not overridden (should be the inherited one)
    assert FleetTUI.refresh is App.refresh
