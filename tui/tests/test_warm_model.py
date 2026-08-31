"""Gate for the model warm button (item 7c).

`w` opens a WarmModal listing COLD (on-disk, not loaded) models; clicking one runs
`ollama run <model> ok` (detached) to load it into VRAM. subprocess.Popen is monkeypatched so no
real ollama process launches.
"""
from textual.widgets import Button

from fleet_tui.app import FleetTUI, WarmModal
from fleet_tui.models import ModelState
from fleet_tui.sources import modelstate


class _FakeBtn:
    def __init__(self, bid):
        self.id = bid


class _FakeEvent:
    def __init__(self, bid):
        self.button = _FakeBtn(bid)


async def test_warm_modal_lists_cold_models_and_launches(monkeypatch):
    # two cold, one already loaded → only the cold ones are offered
    monkeypatch.setattr(modelstate, "list_models", lambda: [
        ModelState(name="qwen3-coder:30b", loaded=False, gb=18.6),
        ModelState(name="gemma4:12b", loaded=False, gb=8.1),
        ModelState(name="qwen3.6:35b-a3b", loaded=True, gb=23.0),
    ])
    launched = []
    import subprocess
    monkeypatch.setattr(subprocess, "Popen",
                        lambda argv, **kw: launched.append(argv) or type("P", (), {})())

    app = FleetTUI()
    async with app.run_test() as pilot:
        await app.push_screen(WarmModal())
        await pilot.pause()
        modal = app.screen
        warm_ids = {(b.id or "") for b in modal.query(Button) if (b.id or "").startswith("warm-")}
        assert len(warm_ids) == 2                       # only the 2 cold models
        # warm the first cold model
        modal.on_button_pressed(_FakeEvent("warm-0"))

    # the app's background refresh may also Popen (crontab -l etc.) during the test → filter for OUR call
    ollama_calls = [list(a) for a in launched if list(a[:2]) == ["ollama", "run"]]
    assert ollama_calls, f"no ollama warm launched (captured: {launched})"
    argv = ollama_calls[0]
    assert argv[2] in ("qwen3-coder:30b", "gemma4:12b")
    assert argv[3] == "ok"                              # tiny prompt → loads resident (fleet-model's path)


async def test_warm_action_opens_modal():
    app = FleetTUI()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.action_warm_model()
        await pilot.pause()
        assert isinstance(app.screen, WarmModal)
