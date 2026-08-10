"""Shared pytest fixtures for the Fleet TUI suite."""
import copy

import pytest

from fleet_tui.widgets import anim


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
