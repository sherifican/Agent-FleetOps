"""App-level gate for the installed-models inventory feature.

Owner ask: click the MODELS panel's IDLE section (or press `m`) → a modal listing ALL on-disk models
with their size + detail, largest first. modelstate.installed_models is monkeypatched so no ollama I/O.
"""
from textual.widgets import Static

from fleet_tui.app import FleetTUI, ModelsListModal, ModelsStatic
from fleet_tui.models import InstalledModel
from fleet_tui.sources import modelstate, inflight


_INSTALLED = [
    InstalledModel(name="qwen3-coder:30b", size_gb=18.6, family="qwen3", quant="Q4_K_M", param_size="30.5B", modified="2026-07-01"),
    InstalledModel(name="gemma4:12b", size_gb=8.1, family="gemma3", quant="Q4_K_M", param_size="12.2B", modified="2026-06-30"),
    InstalledModel(name="tiny:latest", size_gb=0.0),
]


def _modal_text(modal) -> str:
    return "\n".join(str(s.render()) for s in modal.query(Static))


class _FakeClick:
    """A body click (y>0) on a panel — Panel.on_click routes y>0 to on_body_click."""
    def __init__(self, y=3):
        self.y = y


async def test_modal_lists_all_installed_sorted_desc(monkeypatch):
    monkeypatch.setattr(modelstate, "installed_models", lambda: list(_INSTALLED))
    app = FleetTUI()
    async with app.run_test() as pilot:
        await app.push_screen(ModelsListModal())
        await pilot.pause()
        modal = app.screen
        assert isinstance(modal, ModelsListModal)
        text = _modal_text(modal)
        # every installed model + its size shows
        assert "qwen3-coder:30b" in text and "18.6GB" in text
        assert "gemma4:12b" in text and "8.1GB" in text
        assert "tiny:latest" in text
        # detail (params/quant/family/date) surfaced for a rich record
        assert "30.5B" in text and "Q4_K_M" in text
        # header shows the on-disk count + total
        assert "3 on disk" in text
        # largest-first order preserved in the rendered list
        assert text.index("qwen3-coder:30b") < text.index("gemma4:12b") < text.index("tiny:latest")


async def test_modal_empty_safe(monkeypatch):
    monkeypatch.setattr(modelstate, "installed_models", lambda: [])
    app = FleetTUI()
    async with app.run_test() as pilot:
        await app.push_screen(ModelsListModal())
        await pilot.pause()
        assert "no models found" in _modal_text(app.screen)


async def test_action_opens_modal(monkeypatch):
    monkeypatch.setattr(modelstate, "installed_models", lambda: list(_INSTALLED))
    app = FleetTUI()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.action_models_list()
        await pilot.pause()
        assert isinstance(app.screen, ModelsListModal)


async def test_models_panel_body_click_opens_modal(monkeypatch):
    """The MODELS panel is a ModelsStatic — a body click opens the inventory WHEN nothing is in-flight."""
    monkeypatch.setattr(modelstate, "installed_models", lambda: list(_INSTALLED))
    monkeypatch.setattr(inflight, "build_inflight", lambda *a, **k: [])   # nothing in-flight → inventory path
    app = FleetTUI()
    async with app.run_test() as pilot:
        await pilot.pause()
        panel = app.query_one("#models", ModelsStatic)
        panel.on_click(_FakeClick(y=3))          # y>0 → body → on_body_click → action_models_list
        await pilot.pause()
        assert isinstance(app.screen, ModelsListModal)


async def test_models_panel_title_click_still_collapses(monkeypatch):
    """A TITLE-row click (y==0) must still collapse the panel, NOT open the modal (no regression)."""
    monkeypatch.setattr(modelstate, "installed_models", lambda: list(_INSTALLED))
    app = FleetTUI()
    async with app.run_test() as pilot:
        await pilot.pause()
        panel = app.query_one("#models", ModelsStatic)
        assert not app._is_collapsed("models")
        panel.on_click(_FakeClick(y=0))          # y==0 → title row → toggle collapse
        await pilot.pause()
        assert app._is_collapsed("models")
        assert not isinstance(app.screen, ModelsListModal)
