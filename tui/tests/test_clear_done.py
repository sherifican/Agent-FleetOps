"""Gate for the dispatch 'Clear done' feature (owner ask: the finished-dispatch list crowds the modal
and pushes the target buttons off-screen).

- dispatch.clear_done() ARCHIVES finished dispatches (.done exists) out of recent()'s view, keeps running ones.
- The DispatchModal shows a 🗑 Clear done (N) button when finished dispatches exist; clicking it clears the
  recents list + hides the button.
"""
import os

from textual.widgets import Button

from fleet_tui.app import FleetTUI, DispatchModal
from fleet_tui.sources import dispatch


def _mk(d, base, done):
    for ext in (".meta", ".brief", ".out"):
        with open(os.path.join(d, base + ext), "w") as f:
            f.write("{}")
    if done:
        open(os.path.join(d, base + ".done"), "w").close()


def test_clear_done_archives_finished_keeps_running(tmp_path, monkeypatch):
    d = str(tmp_path / "d")
    os.makedirs(d)
    monkeypatch.setattr(dispatch, "DISPATCH_DIR", d)
    _mk(d, "20260704-100000-done", done=True)
    _mk(d, "20260704-100001-run", done=False)
    assert len(dispatch.recent()) == 2

    n = dispatch.clear_done()
    assert n == 1                                     # only the finished one
    rec = dispatch.recent()
    assert len(rec) == 1 and rec[0]["running"] is True
    # archived (moved), not deleted
    arc = os.path.join(d, ".archive")
    assert os.path.isdir(arc)
    assert os.path.exists(os.path.join(arc, "20260704-100000-done.meta"))
    assert os.path.exists(os.path.join(arc, "20260704-100000-done.done"))
    # the running dispatch's files stayed put
    assert os.path.exists(os.path.join(d, "20260704-100001-run.meta"))


def test_clear_done_empty_safe(tmp_path, monkeypatch):
    monkeypatch.setattr(dispatch, "DISPATCH_DIR", str(tmp_path / "empty"))
    assert dispatch.clear_done() == 0                 # never raises on a missing/empty dir


async def test_modal_shows_clear_button_and_clears(tmp_path, monkeypatch):
    d = str(tmp_path / "d")
    os.makedirs(d)
    monkeypatch.setattr(dispatch, "DISPATCH_DIR", d)
    _mk(d, "20260704-090000-a", done=True)
    _mk(d, "20260704-090001-b", done=True)

    app = FleetTUI()
    async with app.run_test() as pilot:
        await app.push_screen(DispatchModal())
        await pilot.pause()
        modal = app.screen
        clear = [b for b in modal.query(Button) if (b.id or "") == "clear-done"]
        assert clear, "Clear-done button missing when finished dispatches exist"
        assert "2" in str(clear[0].label)             # count of finished dispatches

        # click it → archives both, empties the recents, hides the button
        modal.on_button_pressed(type("E", (), {"button": clear[0]})())
        await pilot.pause()
        assert dispatch.clear_done() == 0             # nothing left to clear (already archived)
        assert clear[0].display is False


async def test_modal_no_clear_button_when_nothing_done(tmp_path, monkeypatch):
    monkeypatch.setattr(dispatch, "DISPATCH_DIR", str(tmp_path / "empty"))
    app = FleetTUI()
    async with app.run_test() as pilot:
        await app.push_screen(DispatchModal())
        await pilot.pause()
        modal = app.screen
        assert not [b for b in modal.query(Button) if (b.id or "") == "clear-done"]
