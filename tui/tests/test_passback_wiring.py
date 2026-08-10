"""App-level gate for Wave 5 wiring — passback modal opens + marks seen, and the header attention
counter (wave 4) reflects alerts/partial/feedback/passback. Hermetic (no live fleet)."""
import pytest
from fleet_tui.app import FleetTUI, PassbackModal
from fleet_tui.sources import passback


@pytest.mark.asyncio
async def test_subtitle_counter_reflects_signals(monkeypatch):
    app = FleetTUI()
    async with app.run_test() as pilot:
        await pilot.pause()
        # inject a data cycle with one of each signal
        class _Ops:
            def __init__(self, detail): self.detail = detail; self.status = "idle"; self.id = "x"
        app._data = {
            "alerts": ["gpu hot"],
            "dispatches": [{"partial": True}, {"partial": False}],
            "ops": [_Ops("… FEEDBACK DUE …"), _Ops("clean")],
            "passback": [{"new": True}, {"new": False}],
        }
        app._alerts = {"gpu hot"}
        app._update_subtitle()
        st = app.sub_title
        assert "⚠1" in st and "partial1" in st and "fb1" in st and "pb1" in st


@pytest.mark.asyncio
async def test_subtitle_clear_when_nothing_pending(monkeypatch):
    app = FleetTUI()
    async with app.run_test() as pilot:
        await pilot.pause()
        app._data = {"alerts": [], "dispatches": [], "ops": [], "passback": []}
        app._alerts = set()
        app._update_subtitle()
        assert "✓clear" in app.sub_title


@pytest.mark.asyncio
async def test_passback_modal_marks_all_seen(monkeypatch):
    called = {"n": 0}
    monkeypatch.setattr(passback, "mark_all_seen", lambda: called.__setitem__("n", called["n"] + 1))
    app = FleetTUI()
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.push_screen(PassbackModal([{"title": "T", "age": "1h ago", "name": "f.md", "new": True}]))
        await pilot.pause()
        assert called["n"] == 1        # opening the modal acknowledges (marks seen)
