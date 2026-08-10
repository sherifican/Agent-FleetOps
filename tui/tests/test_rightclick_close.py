"""Gate for right-click-to-close: every pop-up dismisses on right-click (mouse-only operation),
while left-click leaves interactive modals open."""
import pytest
from fleet_tui.app import FleetTUI, FleetModal, HelpModal, DispatchModal, CurationModal, DetailModal


class _Btn:
    def __init__(self, b): self.button = b


@pytest.mark.asyncio
async def test_right_click_closes_every_modal_kind():
    app = FleetTUI()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        for M, args in ((HelpModal, ()), (DispatchModal, ()), (CurationModal, ()), (DetailModal, ([],))):
            app.push_screen(M(*args))
            await pilot.pause()
            depth = len(app.screen_stack)
            app.screen_stack[-1].on_mouse_down(_Btn(3))     # right-click
            await pilot.pause()
            assert len(app.screen_stack) == depth - 1, f"{M.__name__} did not close on right-click"


@pytest.mark.asyncio
async def test_left_click_does_not_close_interactive_modal():
    app = FleetTUI()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.push_screen(DispatchModal())
        await pilot.pause()
        depth = len(app.screen_stack)
        app.screen_stack[-1].on_mouse_down(_Btn(1))         # left-click must NOT close it
        await pilot.pause()
        assert len(app.screen_stack) == depth


def test_all_modals_inherit_fleetmodal():
    # structural guard: every *Modal (except the base) subclasses FleetModal so it gets right-click-close
    import fleet_tui.app as A
    import inspect
    modals = [c for n, c in inspect.getmembers(A, inspect.isclass)
              if n.endswith("Modal") and n != "FleetModal" and issubclass(c, A.ModalScreen)]
    assert modals, "no modals found"
    for c in modals:
        assert issubclass(c, FleetModal), f"{c.__name__} does not inherit FleetModal (no right-click close)"
