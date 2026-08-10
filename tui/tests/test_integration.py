"""Live integration test — runs the real Textual app via the pilot, against REAL fleet state.

Formalizes the Phase-D live smoke. Uses Textual's run_test() (asyncio_mode=auto in pyproject).
The focus lock is pointed at a tmp path so pressing 'f' never touches the real watchers.lock.
"""
import pytest
from textual.widgets import Static


async def test_app_mounts_and_toggles_focus(tmp_path, monkeypatch):
    # never touch the real ~/.claude/curation/watchers.lock
    monkeypatch.setenv("FLEET_WATCHERS_LOCK", str(tmp_path / "watchers.lock"))
    from fleet_tui.app import FleetTUI
    from fleet_tui.sources import focus

    app = FleetTUI()
    async with app.run_test() as pilot:
        await pilot.pause()
        # all four panels are mounted
        for pid in ("research_playlists", "health", "jobs", "inbox"):
            assert app.query_one(f"#{pid}", Static) is not None

        # 'f' toggles focus (writes/removes the tmp lock) and flips back
        before = focus.is_on()
        await pilot.press("f")
        await pilot.pause()
        assert focus.is_on() is (not before)
        await pilot.press("f")
        await pilot.pause()
        assert focus.is_on() is before


async def test_panels_populate_from_live_sources(tmp_path, monkeypatch):
    """Let the thread-worker refresh run and assert the panels render non-empty content."""
    monkeypatch.setenv("FLEET_WATCHERS_LOCK", str(tmp_path / "watchers.lock"))
    import asyncio
    from fleet_tui.app import FleetTUI

    app = FleetTUI()
    async with app.run_test() as pilot:
        await pilot.pause()
        await asyncio.sleep(2.5)  # thread worker gathers (health shells out)
        await pilot.pause()
        # research-playlists panel renders (its hint line always contains "playlist") — proves the refresh reached the widget
        assert "playlist" in str(app.query_one("#research_playlists", Static).render()).lower()


async def test_inbox_click_opens_detail_modal(tmp_path, monkeypatch):
    """Clicking the inbox panel opens the pending-items detail modal (owner ask)."""
    monkeypatch.setenv("FLEET_WATCHERS_LOCK", str(tmp_path / "watchers.lock"))
    from fleet_tui.app import FleetTUI, DetailModal
    app = FleetTUI()
    # large enough that all 4 panels (incl. the tall JOBS list) fit + INBOX is on-screen to click
    async with app.run_test(size=(120, 50)) as pilot:
        await pilot.pause()
        # click the BODY (offset y>0) — clicking the title row (y==0) now toggles collapse instead
        await pilot.click("#inbox", offset=(5, 3))
        await pilot.pause()
        assert isinstance(app.screen_stack[-1], DetailModal)
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen_stack[-1], DetailModal)


async def test_section_collapse_toggle(tmp_path, monkeypatch):
    """Clicking a panel's TITLE ROW (y==0) collapses it (and re-clicking restores) — the fill/share layout."""
    monkeypatch.setenv("FLEET_WATCHERS_LOCK", str(tmp_path / "watchers.lock"))
    from fleet_tui.app import FleetTUI
    app = FleetTUI()
    async with app.run_test(size=(120, 50)) as pilot:
        await pilot.pause()
        assert not app._is_collapsed("jobs")
        await pilot.click("#jobs", offset=(2, 0))          # title row → collapse
        await pilot.pause()
        assert app._is_collapsed("jobs") and app.query_one("#jobs").has_class("collapsed")
        await pilot.click("#jobs", offset=(2, 0))          # title row again → restore
        await pilot.pause()
        assert not app._is_collapsed("jobs") and not app.query_one("#jobs").has_class("collapsed")


async def test_focus_help_and_palette(tmp_path, monkeypatch):
    """The focus-help modal opens, and the command palette exposes the focus commands with help."""
    monkeypatch.setenv("FLEET_WATCHERS_LOCK", str(tmp_path / "watchers.lock"))
    from fleet_tui.app import FleetTUI, FocusHelpModal, FleetCommands
    app = FleetTUI()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.action_focus_help()
        await pilot.pause()
        assert isinstance(app.screen_stack[-1], FocusHelpModal)
        # palette provider yields the focus commands (broadened to the full fleet-action set in v3.16)
        prov = FleetCommands(app.screen)
        hits = [h async for h in prov.search("focus")]
        assert len(hits) == 2
        assert all(h.help for h in hits)  # each has a description
        # the broadened palette also surfaces dispatch/passback/models actions, each with help
        disc = [h async for h in prov.discover()]
        names = " | ".join(str(h.display) if hasattr(h, "display") else "" for h in disc)
        assert len(disc) >= 12
        for kw in ("Dispatch", "Passback", "Inbox", "Models"):
            assert kw in names
        assert all(h.help for h in disc)


async def test_theme_persists_across_reopen(tmp_path, monkeypatch):
    """Changing the theme saves it; a fresh app instance restores it (Textual doesn't persist by default)."""
    monkeypatch.setenv("FLEET_WATCHERS_LOCK", str(tmp_path / "watchers.lock"))
    import fleet_tui.app as A
    monkeypatch.setattr(A, "THEME_FILE", str(tmp_path / "theme"))
    app1 = A.FleetTUI()
    async with app1.run_test() as pilot:
        await pilot.pause()
        target = next(t for t in app1.available_themes if t != app1.theme)
        app1.theme = target
        await pilot.pause()
    app2 = A.FleetTUI()
    async with app2.run_test() as pilot:
        await pilot.pause()
        assert app2.theme == target
