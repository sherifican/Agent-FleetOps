"""Regression test for the embedded terminal widget.

The bug it guards: TerminalPane had BOTH compose() (an empty Static child) AND render();
Textual shows children and never calls render() when a widget has any → the pyte screen
(the shell output) was never displayed = an empty terminal window. This test mounts the app,
shows the terminal, and asserts a shell actually spawned + rendered. (asyncio_mode=auto in pyproject.)
"""
import asyncio
from unittest.mock import patch

from fleet_tui.app import FleetTUI
from fleet_tui.widgets.terminal import TerminalPane


def test_cleanup_never_waits_blockingly_for_a_shell():
    term = TerminalPane()
    term._pid = 123
    with patch("fleet_tui.widgets.terminal.os.kill") as kill, patch("fleet_tui.widgets.terminal.os.waitpid", return_value=(0, 0)) as wait:
        term._cleanup()
    wait.assert_called_once_with(123, 1)  # os.WNOHANG
    assert kill.call_count == 2


async def test_embedded_terminal_spawns_and_renders():
    app = FleetTUI()
    async with app.run_test() as pilot:
        term = app.query_one("#terminal", TerminalPane)
        term.display = True
        await pilot.pause()
        # give the forked shell time to spawn + write its prompt; the async fd-reader feeds pyte
        content = ""
        for _ in range(30):
            await asyncio.sleep(0.15)
            await pilot.pause()
            content = "".join(term._screen.display).strip() if term._screen else ""
            if content:
                break
        assert content, "embedded terminal is EMPTY — shell did not spawn/render (compose() masking render()?)"
        # render() must return the live pyte screen, not a placeholder / empty child
        rendered = str(getattr(term.render(), "plain", term.render()))
        assert "not initialized" not in rendered.lower()
