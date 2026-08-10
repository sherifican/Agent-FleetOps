"""App-level gate: clicking the MODELS panel body reveals what each IN-FLIGHT model/leg is working on —
the dispatch TITLE + brief — via InFlightTasksModal; a dispatch-linked row can open the live output.

v3.35: a persistent llama-server SIDECAR is rendered as the SERVICE it is (not "no linked TUI dispatch")
and never offers a watch button; only genuine dispatch-linked rows (base set) get the ▶ watch button."""
from textual.widgets import Static, Button

from fleet_tui.app import FleetTUI, InFlightTasksModal, ModelsStatic, DispatchOutputModal
from fleet_tui.sources import inflight


_ENTRIES = [
    {"name": "qwen3-coder:30b", "kind": "local", "title": "gen→audit",
     "brief": "fix the cross-file bug in chain_runner", "base": "20260711-x"},
    {"name": "codex-fleet", "kind": "cloud", "title": "codex-fleet",
     "brief": "audit the state-integrity layer", "base": "20260711-c"},
    {"name": "gemma4-e4b-q4-k-m (:8336)", "kind": "service", "title": "vision service (:8336)",
     "brief": "", "base": None},
    {"name": "gemma4:12b", "kind": "local", "title": "loaded · GPU active · no linked TUI dispatch",
     "brief": "", "base": None},
]


def _modal_text(modal) -> str:
    return "\n".join(str(s.render()) for s in modal.query(Static))


class _FakeClick:
    def __init__(self, y=3):
        self.y = y


async def test_inflight_modal_shows_each_task():
    app = FleetTUI()
    async with app.run_test() as pilot:
        await app.push_screen(InFlightTasksModal(list(_ENTRIES)))
        await pilot.pause()
        modal = app.screen
        assert isinstance(modal, InFlightTasksModal)
        text = _modal_text(modal)
        assert "qwen3-coder:30b" in text and "gen→audit" in text and "fix the cross-file bug" in text
        assert "codex-fleet" in text and "audit the state-integrity layer" in text
        # the sidecar reads as a SERVICE, never as a stalled dispatch
        assert "vision service (:8336)" in text
        # the untracked-but-busy local row keeps an honest fallback
        assert "no linked TUI dispatch" in text
        # only dispatch-linked rows (base set) get a ▶ watch button; service + untracked do NOT
        watch = [b for b in modal.query(Button) if (b.id or "").startswith("watch-")]
        assert len(watch) == 2


async def test_service_row_offers_no_watch_button():
    # a modal of ONLY the vision service → zero watch buttons, and it must not say "no linked TUI dispatch"
    only_service = [{"name": "gemma4-e4b-q4-k-m (:8336)", "kind": "service",
                     "title": "vision service (:8336)", "brief": "", "base": None}]
    app = FleetTUI()
    async with app.run_test() as pilot:
        await app.push_screen(InFlightTasksModal(only_service))
        await pilot.pause()
        modal = app.screen
        text = _modal_text(modal)
        assert "vision service (:8336)" in text
        assert "no linked TUI dispatch" not in text
        watch = [b for b in modal.query(Button) if (b.id or "").startswith("watch-")]
        assert len(watch) == 0


async def test_body_click_opens_inflight_modal_when_busy(monkeypatch):
    monkeypatch.setattr(inflight, "build_inflight", lambda *a, **k: list(_ENTRIES))
    app = FleetTUI()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.query_one("#models", ModelsStatic).on_click(_FakeClick(y=3))   # y>0 → body
        await pilot.pause()
        assert isinstance(app.screen, InFlightTasksModal)


async def test_watch_button_opens_the_live_output():
    app = FleetTUI()
    async with app.run_test() as pilot:
        await app.push_screen(InFlightTasksModal(list(_ENTRIES)))
        await pilot.pause()
        modal = app.screen
        btn = next(b for b in modal.query(Button) if b.id == "watch-0")
        modal.on_button_pressed(type("E", (), {"button": btn})())
        await pilot.pause()
        assert isinstance(app.screen, DispatchOutputModal)


async def test_inflight_modal_empty_is_safe():
    app = FleetTUI()
    async with app.run_test() as pilot:
        await app.push_screen(InFlightTasksModal([]))
        await pilot.pause()
        assert "Nothing in-flight" in _modal_text(app.screen)
