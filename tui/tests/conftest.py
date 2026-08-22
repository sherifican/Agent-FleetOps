"""Shared pytest fixtures for the Fleet TUI suite."""
import copy

import pytest

from fleet_tui.widgets import anim
from fleet_tui.models import FleetBox, FocusState, HealthSnapshot


@pytest.fixture(autouse=True)
def _restore_anim_globals():
    """Keep every test hermetic against cosmetic pollution.

    Mounting the real FleetTUI (app.run_test()) runs on_mount, which loads the owner's cosmetics config
    and calls anim.set_colors()/set_style() — MUTATING the module-global palette (anim._colors, e.g.
    model->magenta) and spinner (anim._active_frames). Those globals feed widgets/format.py, so an
    app-mounting test that happens to sort BEFORE test_format would leak the custom palette and break
    format assertions that expect the defaults (this actually bit us when test_dispatch_presets was added).
    Snapshot + restore around each test so order never matters.
    """
    saved_colors = copy.deepcopy(anim._colors)
    saved_frames = list(anim._active_frames)
    saved_glow = anim._glow_on
    try:
        yield
    finally:
        anim._colors = copy.deepcopy(saved_colors)
        anim._active_frames = list(saved_frames)
        anim._glow_on = saved_glow


@pytest.fixture(autouse=True)
def _isolate_app_refresh(monkeypatch, request):
    """A mounted Textual app must not start live HTTP/subprocess reads during a test.

    Source tests call their readers directly; app tests need only a stable raw snapshot.  Patching the
    module attribute leaves a test's directly imported ``gather_data`` function available for its own
    explicit source seams while making every `run_test()` mount hermetic and quick to tear down.
    """
    import fleet_tui.app as app
    snapshot = {
        "jobs": [], "health": HealthSnapshot(), "models": [], "focus": FocusState(), "inbox": [],
        "dispatches": [], "util": 0, "network": {}, "alerts": [], "ops": [], "cloud": [],
        "posture": {}, "passback": [], "research_playlists": [], "boxes": [FleetBox()],
        "models_by_box": {"local": []}, "receipts": [], "throughput": {"local": {}},
        "lanes": [], "downloads": [], "bg_agents": [],
    }
    monkeypatch.setattr(app, "gather_data", lambda: snapshot)
    # The PTY is integration-tested in test_terminal.py.  Every other app test renders the pane hidden,
    # so for those tests avoid spawning a real shell that can outlive a test runner shutdown.
    if request.node.fspath.basename != "test_terminal.py":
        from fleet_tui.widgets.terminal import TerminalPane

        async def _no_terminal_process(self):
            return None

        monkeypatch.setattr(TerminalPane, "on_mount", _no_terminal_process)
