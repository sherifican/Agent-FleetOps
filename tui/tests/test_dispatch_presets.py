"""Gate for dispatch presets as one-click TUI buttons (item 7b).

The DispatchModal must render a '⚡ Presets' button per fleet_cli preset, and clicking one must submit
its saved cmd with (prefix + typed brief), labeled by the preset name. dispatch.submit is monkeypatched
so no real subprocess launches.
"""
from textual.widgets import Button, TextArea

from fleet_tui.app import FleetTUI, DispatchModal
from fleet_tui.sources import dispatch


class _FakeBtn:
    def __init__(self, bid):
        self.id = bid
        self.label = ""
        self.disabled = False


class _FakeEvent:
    def __init__(self, bid):
        self.button = _FakeBtn(bid)


async def test_modal_renders_preset_buttons_and_submits(monkeypatch):
    calls = []

    def fake_submit(cmd, brief, label=None, allowed=None):
        calls.append({"cmd": cmd, "brief": brief, "label": label, "allowed": allowed})
        return "/tmp/x-20260704-120000-gen-audit"

    monkeypatch.setattr(dispatch, "submit", fake_submit)

    app = FleetTUI()
    async with app.run_test() as pilot:
        await app.push_screen(DispatchModal())
        await pilot.pause()
        modal = app.screen

        # 1) a ⚡ Presets button exists for each default preset
        preset_ids = {(b.id or "") for b in modal.query(Button) if (b.id or "").startswith("preset-")}
        assert "preset-gen-audit" in preset_ids
        assert "preset-agentic-edit" in preset_ids

        # 2) empty brief → no submit (guard)
        modal.query_one("#dispatch_input", TextArea).text = ""
        modal.on_button_pressed(_FakeEvent("preset-gen-audit"))
        assert calls == []

        # 3) with a brief → submits the preset's cmd with (prefix + brief), labeled by name
        modal.query_one("#dispatch_input", TextArea).text = "refactor the parser"
        modal.on_button_pressed(_FakeEvent("preset-gen-audit"))

    assert len(calls) == 1
    c = calls[0]
    assert c["cmd"] == "combo-gencode-audit"          # gen-audit's saved cmd
    assert c["label"] == "gen-audit"
    assert c["brief"].endswith("refactor the parser")  # prefix ('' here) + typed brief
    # the trusted allow-list passed to submit includes the preset's own cmd
    assert c["allowed"] is not None and "combo-gencode-audit" in c["allowed"]


async def test_preset_with_prefix_prepends(monkeypatch):
    """A preset that carries a prefix (e.g. deep-audit) must prepend it to the brief."""
    calls = []
    monkeypatch.setattr(dispatch, "submit",
                        lambda cmd, brief, label=None, allowed=None: calls.append(brief) or "/tmp/x")
    app = FleetTUI()
    async with app.run_test() as pilot:
        await app.push_screen(DispatchModal())
        await pilot.pause()
        modal = app.screen
        modal.query_one("#dispatch_input", TextArea).text = "the diff"
        modal.on_button_pressed(_FakeEvent("preset-deep-audit"))
    assert calls and calls[0].startswith("Audit the following")   # deep-audit's prefix
    assert calls[0].endswith("the diff")
