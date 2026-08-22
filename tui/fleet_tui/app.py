import os
import re
import time
from collections import deque
from textual.app import App, ComposeResult
from textual.screen import ModalScreen
from textual.widgets import Static, Footer, Header, Input, Button, TabbedContent, TabPane, TextArea
from textual.containers import Vertical, VerticalScroll, Horizontal
from textual.command import Provider, Hit, Hits, DiscoveryHit
from textual import work
from rich.markup import escape
from fleet_tui.sources import jobs, inbox, health, focus, modelstate, joboutput, failures, dispatch, cosmetics, targets, ratings, network, ops, cloud_legs, posture, passback, curation, actions, inflight, research_playlists, codex_link, boxes, receipts, throughput, lanes, downloads, bg_agents
from fleet_tui.widgets import format as fmt
from fleet_tui.widgets import anim
from fleet_tui.widgets.format import _clean_model_name, _color_model
from textual.binding import Binding
from fleet_tui.widgets.terminal import TerminalPane

THEME_FILE = os.path.expanduser("~/.config/fleet_tui/theme")   # persists the chosen theme across reopen
VERSION = "4.0"  # bump per shipped feature wave; shown in the Header sub-title (next to the clock)


def _tighten_svg(svg: str) -> str:
    """Crop a Textual screenshot SVG down to the content — remove the empty gap between the panels and
    the docked footer (the window is usually much taller than the content). Keeps the window frame.
    Also hardens font fallback: Rich embeds Fira Code (via CDN), which ISN'T installed here + lacks
    block/box glyphs when the CDN can't be reached (an offline image viewer) → panel borders + plot
    marks render as tofu boxes. We inject the locally-installed 'DejaVu Sans Mono' (full box-drawing +
    block-element coverage) into the fallback chain so screenshots render cleanly in ANY viewer."""
    try:
        # font-fallback hardening (do this first; independent of the crop) — add DejaVu before generic mono
        svg = svg.replace("font-family: Fira Code, monospace;",
                          'font-family: "Fira Code", "DejaVu Sans Mono", monospace;')
        # (y, content) per text row; keep only rows with VISIBLE (non-whitespace) content, so the empty
        # gap between the panels and the docked footer shows up as a real gap in the y-sequence.
        rows = re.findall(r'<text[^>]*\by="([0-9.]+)"[^>]*>(.*?)</text>', svg, re.S)
        ys = sorted({float(y) for y, c in rows if c.replace("&#160;", " ").strip()})
        if len(ys) < 2:
            return svg
        row = 24.65  # Rich SVG row height
        content_bottom = ys[0]
        for a, b in zip(ys, ys[1:]):
            if b - a > row * 2.0:   # first big vertical gap = content ends, empty region begins
                break
            content_bottom = b
        new_h = content_bottom + row + 14.0
        m = re.search(r'viewBox="0 0 ([0-9.]+) ([0-9.]+)"', svg)
        if not m or float(m.group(2)) <= new_h:
            return svg  # already tight
        w = m.group(1)
        svg = re.sub(r'viewBox="0 0 [0-9.]+ [0-9.]+"', f'viewBox="0 0 {w} {new_h:.1f}"', svg, count=1)
        # shrink the outer window frame rect height too, if present
        svg = re.sub(r'(<rect fill="#292929"[^>]*height=")[0-9.]+(")',
                     lambda mm: f'{mm.group(1)}{new_h-2:.1f}{mm.group(2)}', svg, count=1)
        return svg
    except Exception:
        return svg


def gather_data() -> dict:
    """Gather RAW fleet data (objects, not formatted strings). Formatting happens in the app's _paint()
    so the fast cosmetic-animation timer can re-render with the current frame WITHOUT re-gathering (no
    subprocess churn). inbox stays the LIST of InboxItems (also held for the click-through detail modal)."""
    jobs_list = jobs.list_jobs()
    snap = health.snapshot()
    models = modelstate.list_models()
    dispatches = dispatch.recent()
    fleet_boxes = boxes.read_boxes()
    models_by_box = {box.name: boxes.read_models(box, models) for box in fleet_boxes}
    for box in fleet_boxes:
        ledger = getattr(box, "ledger_path", "")
        if ledger:
            cloud_rows = bg_agents.read_bg_agents(ledger)
            if cloud_rows:
                # The ledger's model label is already its source of truth; no local name mapping.
                cloud_rows = cloud_rows
                break
    else:
        cloud_rows = []
    return {
        "jobs": jobs_list,
        "health": snap,
        "models": models,
        "focus": focus.read_state(),
        "inbox": inbox.list_inbox(),
        "dispatches": dispatches,
        "util": modelstate.read_gpu_util(),
        "network": {**_cached_network(), "codex": codex_link.read_status()},   # + Codex-PC-Link bridge status
        "alerts": _compute_alerts(jobs_list, snap),
        "ops": ops.build_ops(jobs_list, dispatches),   # reuse the already-fetched jobs+dispatches (no extra I/O)
        "cloud": cloud_legs.cloud_snapshot(dispatches),   # running codex/grok/kimi legs (dispatches + ext sessions) → MODELS
        "posture": posture.snapshot(),   # backup + supply-chain + upstream ledgers → POSTURE panel
        "passback": passback.list_passback(),   # WinClaude→Fleet passback files (w) + header pb counter
        "research_playlists": research_playlists.read_playlists(),   # video-research source queues → Research Playlists panel
        "boxes": fleet_boxes,
        "models_by_box": models_by_box,
        "receipts": receipts.from_boxes(fleet_boxes),
        "throughput": throughput.read_throughput(fleet_boxes),
        "lanes": lanes.read_lanes(fleet_boxes),
        "downloads": downloads.from_boxes(fleet_boxes),
        "bg_agents": cloud_rows,
    }


_net_cache = {"t": 0.0, "v": None}


def _cached_network() -> dict:
    """network.status() does a ping + 2 subprocesses — cache it ~20s so the 3s refresh loop never
    hammers them (the 'no heavy probes in the refresh loop' crash-hardening rule)."""
    now = time.time()
    if _net_cache["v"] is None or now - _net_cache["t"] > 20:
        _net_cache["v"] = network.status()
        _net_cache["t"] = now
    return _net_cache["v"]


def _compute_alerts(jobs_list, snap) -> list:
    """Attention conditions worth a proactive notification (job failed / service down / running hot)."""
    a = []
    for j in jobs_list:
        if j.last_status == "fail":
            a.append(f"⚠ job failed: {j.name}")
    for name, up in (snap.services or {}).items():
        if not up:
            a.append(f"⚠ service DOWN: {name}")
    for i, c in enumerate(snap.gpu or []):
        if c.get("temp", 0) >= 85:
            a.append(f"🔥 gpu{i} {c['temp']}°C")
    if snap.cpu_temp and snap.cpu_temp >= 90:
        a.append(f"🔥 cpu {snap.cpu_temp}°C")
    if snap.xid and snap.xid != "none":
        a.append(f"⚠ GPU error logged: {snap.xid}")
    return a


# ---------------------------------------------------------------- modals

class FleetModal(ModalScreen):
    """Base for every pop-up: RIGHT-CLICK anywhere dismisses it, so the whole TUI is mouse-operable
    (left-click to act, right-click to close) — no reach for Esc. Subclasses keep their own on_click /
    BINDINGS; this only adds the right-button close."""
    def on_mouse_down(self, event) -> None:
        if getattr(event, "button", 1) == 3:      # 1=left 2=middle 3=right
            try:
                self.dismiss()
            except Exception:
                pass


class DetailModal(FleetModal):
    """Inbox detail + per-item actions: ▶ Hand off (route the alert to Claude/whoever's responsible — it
    queues for the next orchestrator turn) and, for clearable items, ✓ Acknowledge (clears via the existing
    gated path). Hand-off does NOT clear the item; ack does."""
    BINDINGS = [("escape", "dismiss", "Close")]
    _CLEARABLE = {"github", "dep", "curation", "automation", "hive", "backup", "supply"}

    def __init__(self, items):
        super().__init__()
        self._items = items

    def compose(self) -> ComposeResult:
        with Vertical(id="modalbox"):
            yield Static("INBOX — pending items", id="modaltitle")
            with VerticalScroll(id="modalbody"):
                if not self._items:
                    yield Static("Inbox is clear — nothing pending. ✓")
                else:
                    for i, it in enumerate(self._items):
                        mark = {"crit": "[!]", "fyi": "[.]"}.get(it.priority, "[•]")
                        yield Static(f"{mark} {it.title}", classes="itemtitle")
                        yield Static(it.body or it.detail or "(no detail)", classes="itembody")
                        with Horizontal(classes="alertrow"):
                            yield Button("▶ hand off", id=f"handoff-{i}", variant="primary")
                            if it.source in self._CLEARABLE:
                                yield Button("✓ ack", id=f"ack-{i}", variant="success")
            yield Static("▶ hand off routes it to Claude/responsible · ✓ ack clears it · Esc to close", id="modalhint")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        try:
            idx = int(bid.split("-")[1])
            it = self._items[idx]
        except (ValueError, IndexError):
            return
        if bid.startswith("handoff-"):
            ok = actions.request_action(it.source, it.title, it.detail or it.body or "")
            self.app.notify(f"▶ handed off: {it.title[:60]} — queued for Claude to action/route"
                            if ok else "hand-off failed", timeout=8)
            # do NOT dismiss — the owner may hand off / ack several items in one visit
        elif bid.startswith("ack-"):
            if inbox.ack(it.source):
                self.app.notify(f"✓ acknowledged: {it.source} (cleared)", timeout=6)
            else:
                self.app.notify(f"nothing to clear for {it.source}", severity="warning", timeout=4)
            self.dismiss()


class FocusHelpModal(FleetModal):
    """Explains what focus mode is and does."""
    BINDINGS = [("escape", "dismiss", "Close")]
    HELP = (
        "FOCUS MODE\n\n"
        "Toggle with the  f  key (or from this command palette).\n\n"
        "When ON, it writes a lock file (~/.claude/curation/watchers.lock) that PAUSES the fleet's\n"
        "high-frequency autonomous watchers so they don't interrupt you mid-task:\n"
        "   • curation-watcher      (would fire a memory/skill audit)\n"
        "   • github-activity-watch (would surface new GitHub actionables)\n\n"
        "Everything else keeps running normally — health, backups, telegram, dep/supply/hf watches.\n"
        "When OFF (the default), every loop runs as usual — zero change.\n\n"
        "Turn it ON for deep or manual work; turn it OFF when you're done."
    )

    def compose(self) -> ComposeResult:
        with Vertical(id="modalbox"):
            yield Static("FOCUS MODE — what it does", id="modaltitle")
            with VerticalScroll(id="modalbody"):
                st = focus.read_state()
                _cur = f"CURRENT: {'● ON' if st.on else '○ off'}" + (f"  (scope={st.scope})" if st.on else "")
                yield Static(f"{_cur}\n\n{self.HELP}")
            yield Static("press  f  to toggle · Esc to close", id="modalhint")

    def on_click(self) -> None:
        self.dismiss()


class HelpModal(FleetModal):
    """Full keybinding + feature reference (press ?)."""
    BINDINGS = [("escape", "dismiss", "Close"), ("question_mark", "dismiss", "Close")]
    HELP = (
        "[b]TABS[/b]   Fleet · Coding · Trends · Ops   (click the tab name up top)\n\n"
        "[b]KEYS[/b]\n"
        "  q  quit (asks to confirm)     f  focus mode toggle\n"
        "  r  refresh now                i  inbox detail + acknowledge\n"
        "  o  job output (color-coded)   x  tool failures\n"
        "  d  dispatch box (+ presets)   s  screenshot\n"
        "  c  cosmetics menu             a  alerts history\n"
        "  F  cycle Ops filter           u  unload idle models\n"
        "  w  warm a model into VRAM     m  installed-models inventory\n"
        "  p  WinClaude passback inbox   /  filter Ops tasks (type to filter)\n"
        "  j/k or ↑/↓  move Ops selection   Enter  open selected Ops item\n"
        "  C  curation log + trigger a pass   ?  this help\n"
        "  Ctrl+`  embedded terminal     Ctrl+Q  quit\n"
        "  Ctrl+P  command palette\n"
        "  phone/browser view:  ./serve.sh  → http://<fleet-LAN-ip>:8011  (home wifi only)\n\n"
        "[b]MOUSE[/b]\n"
        "  click a panel's TITLE row (▼/▶) → collapse/expand it (others grow to fill)\n"
        "  click a JOB row                 → that job's detail + ▶ Run-now\n"
        "  click an OPS row                → select it (▶ Run / 📄 Output action bar)\n"
        "  click INBOX / HEALTH body        → pending items / tool failures\n"
        "  click MODELS body → what each IN-FLIGHT model/leg is working on (dispatch title + brief);\n"
        "     nothing in-flight → installed-models inventory (all on disk + size; also the m key)\n\n"
        "[b]TERMINAL[/b]   Ctrl+` toggles an embedded shell (hidden by default; Ctrl+` again to minimize)"
    )

    def compose(self) -> ComposeResult:
        with Vertical(id="modalbox"):
            yield Static("⌨  KEYS & FEATURES", id="modaltitle")
            with VerticalScroll(id="modalbody"):
                yield Static(self.HELP)
            yield Static("click anywhere or press Esc to close", id="modalhint")

    def on_click(self) -> None:
        self.dismiss()


class QuitConfirmModal(FleetModal):
    """Confirm before quitting — q is easy to fat-finger and lose your attached session."""
    BINDINGS = [("escape", "dismiss", "Cancel"), ("q", "do_quit", "Quit")]

    def compose(self) -> ComposeResult:
        with Vertical(id="modalbox"):
            yield Static("Quit the Fleet TUI?", id="modaltitle")
            yield Static("(the tmux session keeps running — you can re-attach with ./run.sh)", classes="itembody")
            with Horizontal(id="quit_btns"):
                yield Button("Quit", id="quit-yes", variant="error")
                yield Button("Cancel", id="quit-no", variant="primary")
            yield Static("q again to quit · Esc to cancel", id="modalhint")

    def action_do_quit(self) -> None:
        self.app.exit()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "quit-yes":
            self.app.exit()
        else:
            self.dismiss()


# ---------------------------------------------------------------- clickable inbox

# color-coded target legend (press ❔) — built from the registry so it always matches the buttons.
_TGT_GROUP_COLOR = {"Cloud legs": "deepskyblue", "Local models": "cyan",
                    "Combos": "orange", "Teams": "magenta"}


SCORECARD_FILE = os.path.expanduser("~/pc-passback/Research-fleet/viz_assets/reports/fleet_pairings_scorecard.html")


def _scorecard_brief(summary: dict) -> str:
    import json
    return (
        "Build a RICH, self-contained, dark-theme HTML SCORECARD of these fleet dispatch pairings and "
        f"OVERWRITE this exact file in place (create parent dirs if needed):\n{SCORECARD_FILE}\n\n"
        "Data — per target: up/down votes, n (sample size), win_rate (%), avg_speed_s (MEASURED wall-clock "
        "seconds), last_note:\n"
        f"{json.dumps(summary, indent=2)}\n\n"
        "Requirements: one row/card per target, ranked by win_rate; show win% + n + avg speed + the note; "
        "a green→red color scale on win_rate; HONESTLY flag small samples (low n = low confidence — don't "
        "overstate); fully self-contained (inline CSS, no external refs); readable dark theme."
    )


def _build_legend(groups) -> str:
    lines = ["[b]dispatch targets[/b]",
             "[gold]✨ Revise (Kimi)[/] — reformat your brief + surface relevant tools/files/paths (K2.7)"]
    for g in groups:
        c = _TGT_GROUP_COLOR.get(g.get("name", ""), "silver")
        lines.append(f"[b]{g.get('name','')}[/b]")
        for t in g.get("targets", []):
            lines.append(f"  [{c}]{t.get('id','?')}[/] — {t.get('desc','')}")
    return "\n".join(lines)


class DispatchOutputModal(FleetModal):
    """The FULL output of one dispatch — color-coded, auto-refreshing every 2s while it's still running,
    so you can actually watch it work + read the whole result (not just the truncated tail)."""
    BINDINGS = [("escape", "dismiss", "Close")]

    def __init__(self, base_name, label=""):
        super().__init__()
        self._base = base_name
        self._label = label

    def compose(self) -> ComposeResult:
        with Vertical(id="modalbox"):
            yield Static(f"DISPATCH OUTPUT — {self._label}", id="modaltitle")
            with VerticalScroll(id="modalbody"):
                yield Static("", id="dispout", classes="itembody")
            yield Static("auto-refreshes while running · Esc to close", id="modalhint")

    def on_mount(self) -> None:
        self._refresh()
        self._timer = self.set_interval(2.0, self._refresh)

    def _refresh(self) -> None:
        o = dispatch.full_output(self._base)
        if o["running"]:
            status = "[yellow]▶ running…[/]"
        elif o.get("partial"):
            status = "[red]⚠ PARTIAL — worker rc≠0, output UNVERIFIED/truncated[/]"
        else:
            status = "[green]✓ done[/]"
        self.query_one("#modaltitle", Static).update(f"DISPATCH OUTPUT — {self._label} · {status}")
        # keep the last ~300 lines (a long agentic run can be huge), color-coded
        text = "\n".join(o["text"].splitlines()[-300:])
        # when done, surface a failure classification (reason + next action) if it looks like a failure
        if not o["running"]:
            try:
                from fleet_tui.fleet_cli import postmortem
                cls = postmortem.classify_failure(o["text"])
                if cls.get("class") and cls["class"] != "unknown":
                    text += (f"\n\n[b]— classification —[/]\n[red]{cls['class']}[/] · "
                             f"{cls.get('reason','')}\n→ {cls.get('suggested_next','')}")
            except Exception:
                pass
        self.query_one("#dispout", Static).update(fmt._colorize_log(text))
        if not o["running"] and getattr(self, "_timer", None):
            self._timer.stop()                                # done → stop polling


class InFlightTasksModal(FleetModal):
    """What each IN-FLIGHT model/leg (local OR cloud) is working on — the dispatch TITLE it was given + its
    full brief (the inline cloud line truncates at 60 chars; this shows it whole). A row tied to a fleet
    dispatch gets a ▶ watch button that opens the live output. Opened by clicking the MODELS panel body."""
    BINDINGS = [("escape", "dismiss", "Close")]

    def __init__(self, entries):
        super().__init__()
        self._entries = entries or []

    def compose(self) -> ComposeResult:
        with Vertical(id="modalbox"):
            yield Static("▶ IN-FLIGHT — what each model is working on", id="modaltitle")
            with VerticalScroll(id="modalbody"):
                if not self._entries:
                    yield Static("Nothing in-flight right now — no busy models or running legs.")
                for i, e in enumerate(self._entries):
                    kind = e.get("kind")
                    name = escape(str(e.get("name", "?") or "?"))
                    title = str(e.get("title", "") or "")
                    if kind == "service":
                        # a persistent llama-server sidecar (vision :8336 / GLM :8090) — a SERVICE, not a
                        # task: distinct glyph, no "→ dispatch" arrow, and NO watch button (nothing to watch).
                        yield Static(f"⚙ [b]{name}[/b]  ·  {escape(title)}", classes="itemtitle")
                        yield Static("[dim](persistent service — always loaded; no task to watch)[/]", classes="itembody")
                        continue
                    icon = "☁" if kind == "cloud" else "▶"
                    head = f"{icon} [b]{name}[/b]"
                    if title and title != str(e.get("name", "")):   # local: show the dispatch label; cloud name==title
                        head += f"  →  {escape(title)}"
                    yield Static(head, classes="itemtitle")
                    brief = str(e.get("brief", "") or "").strip()
                    yield Static(escape(brief[:400]) if brief else "[dim](no brief captured)[/]", classes="itembody")
                    if e.get("base"):
                        with Horizontal(classes="alertrow"):
                            yield Button("▶ watch output", id=f"watch-{i}", variant="primary")
            yield Static("▶ watch = open the live output · Esc / right-click to close", id="modalhint")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid.startswith("watch-"):
            try:
                e = self._entries[int(bid.split("-")[1])]
            except (ValueError, IndexError):
                return
            if e.get("base"):
                self.app.push_screen(DispatchOutputModal(e["base"], e.get("name", "")))


class DispatchModal(FleetModal):
    """Send a brief to an existing fleet leg (thin runner over the dispatch scripts) + show recent dispatches.
    NOT click-to-dismiss (it's interactive — you type in it); close with Esc or after a submit."""
    BINDINGS = [("escape", "dismiss", "Close")]

    _GROUP_VARIANT = {"Cloud legs": "primary", "Local models": "success", "Combos": "warning", "Teams": "default"}

    def compose(self) -> ComposeResult:
        from fleet_tui.fleet_cli.presets import load_presets
        groups = targets.list_groups()
        self._targets = [t for g in groups for t in g.get("targets", [])]   # flat, index matches tgt-<i>
        self._presets = load_presets()                                       # {name: {cmd, prefix, desc}} — one-click shortcuts
        with Vertical(id="modalbox"):
            yield Static("DISPATCH — type a brief, pick a target", id="modaltitle")
            with VerticalScroll(id="modalbody"):
                self._recents = dispatch.recent()[:4]
                summ = ratings.summary()
                if self._recents:
                    for j, d in enumerate(self._recents):
                        if d["running"]:
                            mark = "[yellow]▶ running[/]"
                        elif d.get("partial"):
                            mark = "[red]⚠ PARTIAL (rc≠0 — UNVERIFIED)[/]"
                        else:
                            mark = "[green]✓ done[/]"
                        spd = f" · {d['elapsed']}s" if d.get("elapsed") is not None else ""
                        s = summ.get(d["leg"])
                        wr = f"  [dim]win {s['win_rate']}% ({s['n']})[/]" if s and s["n"] else ""
                        yield Static(f"[{d['when']}] {d['leg']}{spd} · {mark}{wr}", classes="itemtitle")
                        if d.get("brief"):
                            yield Static(f"  {d['brief'][:100]}", classes="itembody")
                        with Horizontal(classes="raterow"):
                            yield Button("📄 output", id=f"view-{j}")     # watch/see the full result
                            if not d["running"]:      # rate finished dispatches → the win-tracking log
                                yield Button("👍", id=f"rate-up-{j}")
                                yield Button("👎", id=f"rate-dn-{j}")
                                yield Button("📊 scorecard", id=f"card-{j}")
                else:
                    yield Static("no dispatches yet.", classes="itembody")
            yield TextArea(id="dispatch_input", soft_wrap=True)
            with Horizontal(id="dispatch_tools"):
                yield Button("✨ Revise (Kimi)", id="kimi-revise", variant="warning")
                yield Button("❔ legend", id="dispatch-legend")
                _done_n = sum(1 for d in self._recents if not d.get("running"))  # finished dispatches crowding the list
                if _done_n:
                    yield Button(f"🗑 Clear done ({_done_n})", id="clear-done", variant="error")
            # grouped target buttons from the registry (cloud legs / local models / combos / teams)
            with VerticalScroll(id="dispatch_targets"):
                i = 0
                for g in groups:
                    yield Static(f"[b]{g.get('name','')}[/b]", classes="tgtgroup")
                    with Horizontal(classes="tgtrow"):
                        for t in g.get("targets", []):
                            yield Button(t.get("id", "?"), id=f"tgt-{i}",
                                         variant=self._GROUP_VARIANT.get(g.get("name", ""), "default"))
                            i += 1
                # ⚡ Presets — saved (cmd+prefix) shortcuts (fleet_cli/presets.py); applied to the typed brief
                if self._presets:
                    yield Static("[b]⚡ Presets[/b]", classes="tgtgroup")
                    with Horizontal(classes="tgtrow"):
                        for name, p in self._presets.items():
                            b = Button(f"⚡ {name}", id=f"preset-{name}", variant="warning")
                            b.tooltip = p.get("desc", "")
                            yield b
            yield Static(_build_legend(groups), id="dispatch_legend")
            yield Static("type a brief → ✨ Revise, or pick a target · ❔ legend · Esc to close", id="modalhint")

    def on_mount(self) -> None:
        self.query_one("#dispatch_input", TextArea).focus()
        self.query_one("#dispatch_legend", Static).display = False   # collapsed until ❔ is pressed

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "dispatch-legend":                         # toggle the color-coded button legend
            box = self.query_one("#dispatch_legend", Static)
            box.display = not box.display
            return
        if bid == "clear-done":                              # 🗑 archive finished dispatches → declutter the list
            n = dispatch.clear_done()
            mb = self.query_one("#modalbody", VerticalScroll)
            mb.remove_children()                             # drop the recents rows (input/targets/typed brief untouched)
            mb.mount(Static("no dispatches yet.", classes="itembody"))
            self._recents = []
            event.button.display = False                     # nothing left to clear
            self.query_one("#modalhint", Static).update(f"🗑 cleared {n} finished dispatch(es) (archived) — targets are in view")
            return
        if bid.startswith("view-"):                          # 📄 → open the full output (watch it live)
            try:
                d = self._recents[int(bid[5:])]
            except (ValueError, IndexError):
                return
            self.app.push_screen(DispatchOutputModal(d["base"], d["leg"]))
            return
        if bid.startswith("rate-"):                          # 👍/👎 a finished dispatch → win-tracking log
            up = bid.startswith("rate-up-")
            try:
                d = self._recents[int(bid.rsplit("-", 1)[1])]
            except (ValueError, IndexError):
                return
            ratings.rate(d["leg"], up, speed_s=d.get("elapsed"))
            self.app.notify(f"{'👍' if up else '👎'} recorded for {d['leg']}", timeout=4)
            event.button.disabled = True                     # one rating per button press
            return
        if bid.startswith("card-"):                          # 📊 → hand the pairings data to visual/vega
            summ = ratings.summary()
            if not summ:
                self.app.notify("no ratings yet — 👍/👎 a few dispatches first", severity="warning", timeout=5)
                return
            vt = next((t for t in self._targets if t.get("id") == "visual/vega"), None)
            if not vt:
                self.app.notify("no visual/vega team target configured", severity="error", timeout=5)
                return
            base = dispatch.submit(vt["cmd"], vt.get("prefix", "") + _scorecard_brief(summ), label="scorecard→visual/vega")
            if base:
                self.app.notify("📊 scorecard dispatched to visual/vega → will overwrite fleet_pairings_scorecard.html (press d to watch)", timeout=12)
                self.dismiss()
            else:
                self.app.notify("scorecard dispatch failed", severity="error", timeout=6)
            return
        brief = self.query_one("#dispatch_input", TextArea).text.strip()
        if bid == "kimi-revise":
            if not brief:
                self.app.notify("type a brief first", severity="warning", timeout=4)
                return
            event.button.disabled = True
            event.button.label = "✨ revising…"
            self.query_one("#modalhint", Static).update("✨ Kimi (K2.7) is revising your brief — standard-format + surfacing tools/files/paths…")
            self._revise(brief)
            return
        # a preset button (preset-<name>) → dispatch its saved cmd+prefix applied to the typed brief
        if bid.startswith("preset-"):
            name = bid[len("preset-"):]
            p = getattr(self, "_presets", {}).get(name)
            if not p:
                return
            if not brief:
                self.app.notify("type a brief first", severity="warning", timeout=4)
                return
            allowed = targets.allowed_cmds() | {pp.get("cmd", "") for pp in self._presets.values()}  # presets.json is trusted owner config
            base = dispatch.submit(p["cmd"], p.get("prefix", "") + brief, label=name, allowed=allowed)
            if base:
                self.app.notify(f"⚡ {name} → {os.path.basename(base)}.out (press d to watch)", timeout=10)
                self.dismiss()
            else:
                self.app.notify(f"preset dispatch failed ({name})", severity="error", timeout=6)
            return
        # else: a target button (tgt-<i>) → dispatch its command
        if bid.startswith("tgt-"):
            if not brief:
                self.app.notify("type a brief first", severity="warning", timeout=4)
                return
            try:
                t = self._targets[int(bid[4:])]
            except (ValueError, IndexError):
                return
            full_brief = t.get("prefix", "") + brief         # team targets prepend a framing prefix
            base = dispatch.submit(t["cmd"], full_brief, label=t.get("id"))
            if base:
                self.app.notify(f"📤 dispatched to {t.get('id')} → {os.path.basename(base)}.out (press d to watch)", timeout=10)
                self.dismiss()
            else:
                self.app.notify(f"dispatch failed ({t.get('id')})", severity="error", timeout=6)

    @work(thread=True, exclusive=True)
    def _revise(self, brief: str) -> None:
        """Background: pipe the brief through Kimi (can take a while), then apply on the UI thread."""
        revised = dispatch.revise_brief(brief)
        self.app.call_from_thread(self._apply_revision, revised)

    def _apply_revision(self, revised) -> None:
        # guard every UI touch — the owner may have hit Esc while Kimi was working (modal gone)
        try:
            btn = self.query_one("#kimi-revise", Button)
            btn.disabled = False
            btn.label = "✨ Revise (Kimi)"
            hint = self.query_one("#modalhint", Static)
            if revised:
                self.query_one("#dispatch_input", TextArea).text = revised
                hint.update("✨ revised by Kimi — review/edit above, then pick a leg · Esc to close")
                self.app.notify("✨ Kimi revised your brief — review it, then send", timeout=8)
            else:
                hint.update("Kimi revise failed (timeout / CLI error) — send as-is or retry · Esc to close")
                self.app.notify("Kimi revise failed", severity="error", timeout=6)
        except Exception:
            # modal was dismissed mid-revise; just report the outcome
            if revised:
                self.app.notify("✨ Kimi finished revising, but the dispatch box was closed", timeout=6)


class CosmeticsModal(FleetModal):
    """Customize the cosmetic animations — style, glow, speed, and WHICH panels animate. Live preview;
    each row cycles/toggles + saves + applies immediately (thin over sources/cosmetics + widgets/anim)."""
    BINDINGS = [("escape", "dismiss", "Close")]

    def compose(self) -> ComposeResult:
        with Vertical(id="modalbox"):
            yield Static("✨ COSMETICS — animation appearance + where it shows", id="modaltitle")
            yield Static("", id="cos_preview")
            with VerticalScroll(id="modalbody"):
                yield Button("", id="cos-enabled")
                with Horizontal(id="cos-spinner-row"):
                    yield Button("◀ prev", id="cos-spinner-prev")
                    yield Button("", id="cos-spinner")     # next + shows name (n/total)
                yield Button("", id="cos-glow")
                yield Button("", id="cos-speed")
                yield Static("animate which panels:", classes="itemtitle")
                for cat in cosmetics.CATS:
                    yield Button("", id=f"cos-cat-{cat}")
                yield Static("colors (animated status text):", classes="itemtitle")
                for slot in cosmetics.COLOR_SLOTS:
                    yield Button("", id=f"cos-color-{slot}")
            yield Static("click a row to cycle/toggle · saves + applies live · Esc to close", id="modalhint")

    def on_mount(self) -> None:
        self._pf = 0
        self._sync_labels()
        self.set_interval(0.12, self._tick_preview)   # animate the preview so styles are visible live

    def _sync_labels(self) -> None:
        c = self.app._cos
        self.query_one("#cos-enabled", Button).label = f"animations:  {'ON' if c['enabled'] else 'off'}"
        keys = anim.SPINNER_KEYS
        pos = (keys.index(c["spinner"]) + 1) if c["spinner"] in keys else 0
        self.query_one("#cos-spinner", Button).label = f"spinner: {c['spinner']} ({pos}/{len(keys)}) next ▶"
        self.query_one("#cos-glow", Button).label = f"glow (breathing pulse):  {'on' if c['glow'] else 'off'}"
        self.query_one("#cos-speed", Button).label = f"speed:  {c['speed']}"
        for cat in cosmetics.CATS:
            self.query_one(f"#cos-cat-{cat}", Button).label = f"  {cat}:  {'on' if c['cats'][cat] else 'off'}"
        for slot, dflt in cosmetics.COLOR_SLOTS.items():
            col = c.get("colors", {}).get(slot, dflt)
            self.query_one(f"#cos-color-{slot}", Button).label = f"  {slot}:  {col}"

    def _tick_preview(self) -> None:
        self._pf += 1
        c = self.app._cos
        f = self._pf
        if c["enabled"]:
            anim_line = (f"{anim.active('running', anim.color('running','yellow'), f)}  "
                         f"{anim.active('computing', anim.color('computing','cyan'), f)}  "
                         f"{anim.active('dispatching', anim.color('dispatching','gold'), f)}  "
                         f"{anim.active('in-flight', anim.color('in_flight','orange'), f)}")
        else:
            anim_line = "[dim](animations off)[/]"
        # static-label sample (reflects the ok/fail/schedule/model colors live)
        static_line = (f"[{anim.color('ok','green')}]OK[/] [{anim.color('fail','red')}]FAIL[/]  "
                       f"[{fmt._model_slot('cyan')}]model[/]  [{anim.color('schedule','cyan')}]every 3hrs[/]")
        self.query_one("#cos_preview", Static).update(f"anim:  {anim_line}\nlabels:  {static_line}")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        c = self.app._cos
        bid = event.button.id or ""
        if bid == "cos-enabled":
            c["enabled"] = not c["enabled"]
        elif bid in ("cos-spinner", "cos-spinner-prev"):
            keys = anim.SPINNER_KEYS
            i = keys.index(c["spinner"]) if c["spinner"] in keys else 0
            step = -1 if bid == "cos-spinner-prev" else 1
            c["spinner"] = keys[(i + step) % len(keys)]
        elif bid == "cos-glow":
            c["glow"] = not c["glow"]
        elif bid == "cos-speed":
            keys = list(cosmetics.SPEEDS.keys())
            i = keys.index(c["speed"]) if c["speed"] in keys else -1
            c["speed"] = keys[(i + 1) % len(keys)]
        elif bid.startswith("cos-cat-"):
            cat = bid[len("cos-cat-"):]
            if cat in c["cats"]:
                c["cats"][cat] = not c["cats"][cat]
        elif bid.startswith("cos-color-"):
            slot = bid[len("cos-color-"):]
            # the 'model' slot also offers 'family' (keep per-family colors) as an option
            pal = (["family"] + cosmetics.PALETTE) if slot == "model" else cosmetics.PALETTE
            cur = c.setdefault("colors", {}).get(slot, cosmetics.COLOR_SLOTS.get(slot, pal[0]))
            i = pal.index(cur) if cur in pal else -1
            c["colors"][slot] = pal[(i + 1) % len(pal)]
        else:
            return
        cosmetics.save(c)
        self.app.apply_cosmetics()
        self._sync_labels()


class Panel(Static):
    """A collapsible fleet panel. Click the TITLE ROW (the top border, y==0) to collapse/expand; click
    the BODY for the panel's action (if any). When collapsed, a click anywhere expands it again."""
    def on_click(self, event) -> None:
        pid = self.id
        if self.app._is_collapsed(pid) or getattr(event, "y", 1) == 0:
            self.app._toggle_section(pid)
            return
        self.on_body_click(event)

    def on_body_click(self, event) -> None:
        pass   # override for a body action (a modal)

    def _clicked_row(self, event) -> int:
        """0-based CONTENT-line index a body click landed on (accounts for the top border + any scroll)."""
        return max(0, getattr(event, "y", 1) - 1 + int(self.scroll_offset.y))


class InboxStatic(Panel):
    """The inbox panel — click the body to open the pending-items detail modal."""
    def on_body_click(self, event) -> None:
        self.app.action_show_inbox()


class JobsStatic(Panel):
    """The jobs panel — click a JOB ROW to drill into that job (config + output + run-now)."""
    def on_body_click(self, event) -> None:
        jobs_list = (getattr(self.app, "_data", None) or {}).get("jobs", [])
        idx = self._clicked_row(event)
        if 0 <= idx < len(jobs_list):
            self.app.push_screen(JobDetailModal(jobs_list[idx]))
        else:
            self.app.action_show_jobs()          # clicked past the last job → all-jobs output


class ResearchPlaylistsStatic(Panel):
    """Research Playlists — click a playlist ROW (▶ check) to ask Claude to check it for new videos and
    stage them for the research team. The TUI is NOT an orchestrator: the click writes a request intent to
    a file + fires a Telegram confirmation; Claude runs the actual check→stage flow."""
    def on_body_click(self, event) -> None:
        pls = (getattr(self.app, "_data", None) or {}).get("research_playlists", [])
        idx = self._clicked_row(event)
        if 0 <= idx < len(pls):                  # rows past the playlists (the hint line) are no-ops
            self.app.request_playlist_check(pls[idx])


class HealthStatic(Panel):
    """The health panel — click the body to open the recent tool-FAILURES modal."""
    def on_body_click(self, event) -> None:
        self.app.action_show_failures()


class PostureStatic(Panel):
    """Backup / supply-chain / upstream posture — click the body to open the pending-alerts INBOX
    (where a backup/supply alert is cleared). Title-row click still collapses."""
    def on_body_click(self, event) -> None:
        self.app.action_show_inbox()


class ModelsStatic(Panel):
    """The MODELS panel — click the body to see WHAT each in-flight model/leg is working on (its dispatch
    title + brief); if nothing is in-flight, opens the on-disk model inventory instead (also on the `m` key).
    Title-row click still collapses."""
    def on_body_click(self, event) -> None:
        d = getattr(self.app, "_data", None) or {}
        entries = inflight.build_inflight(d.get("models", []), d.get("cloud", []), d.get("dispatches", []))
        if entries:
            self.app.push_screen(InFlightTasksModal(entries))
        else:
            self.app.action_models_list()          # nothing in-flight → on-disk inventory (also: `m`)


class JobDetailModal(FleetModal):
    """Per-job drill-in — config + latest output + a ▶ Run-now trigger (Hermes jobs only)."""
    BINDINGS = [("escape", "dismiss", "Close")]

    def __init__(self, job):
        super().__init__()
        self._job = job

    def compose(self) -> ComposeResult:
        j = self._job
        with Vertical(id="modalbox"):
            yield Static(f"JOB — {j.name}", id="modaltitle")
            with VerticalScroll(id="modalbody"):
                yield Static(
                    f"[b]kind[/b]      {j.kind}\n"
                    f"[b]schedule[/b]  {j.schedule}\n"
                    f"[b]status[/b]    {j.last_status}\n"
                    f"[b]next run[/b]  {j.next_run or '—'}\n"
                    f"[b]last run[/b]  {j.last_run or '—'}\n"
                    f"[b]id[/b]        {j.id or '(system cron — no Hermes id)'}",
                    classes="itembody")
                tail = joboutput.job_output_tail(getattr(j, "id", "") or "")
                if tail:
                    yield Static("[b]latest output[/b]", classes="itemtitle")
                    yield Static(fmt._colorize_log(tail), classes="itembody")
            if j.id:
                with Horizontal(id="jobdetail_btns"):
                    yield Button("▶ Run now", id="job-run", variant="primary")
            yield Static("Esc to close", id="modalhint")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "job-run":
            ok = jobs.run_now(self._job.id)
            if ok:
                self.app.notify(f"▶ {self._job.name} triggered — runs on the next scheduler tick", timeout=6)
            else:
                self.app.notify("run-now failed", severity="error", timeout=6)
            event.button.disabled = True


class AlertsModal(FleetModal):
    """Rolling history of proactive alerts (job-fail / service-down / hot / Xid) that flashed by as toasts."""
    BINDINGS = [("escape", "dismiss", "Close")]

    def __init__(self, history):
        super().__init__()
        self._history = list(history)

    def compose(self) -> ComposeResult:
        with Vertical(id="modalbox"):
            yield Static("⚠  ALERTS — recent history", id="modaltitle")
            with VerticalScroll(id="modalbody"):
                if self._history:
                    for when, msg in reversed(self._history):
                        yield Static(f"[dim]{when}[/]  {msg}", classes="itembody")
                else:
                    yield Static("no alerts this session — all clear.", classes="itembody")
            yield Static("click anywhere or Esc to close", id="modalhint")

    def on_click(self) -> None:
        self.dismiss()


class FailuresModal(FleetModal):
    """Recent tool FAILURES — what tool, who (model), when, and the task it was working on."""
    BINDINGS = [("escape", "dismiss", "Close")]

    def __init__(self, fails):
        super().__init__()
        self._fails = fails

    def compose(self) -> ComposeResult:
        with Vertical(id="modalbox"):
            yield Static("RECENT TOOL FAILURES", id="modaltitle")
            with VerticalScroll(id="modalbody"):
                if not self._fails:
                    yield Static("No failed tool calls in the recent window. ✓")
                else:
                    for f in self._fails:
                        yield Static(f"[{f['when']}]  {f['tool']}  ·  {_color_model(f['model'])}",
                                     classes="itemtitle")
                        if f.get("task"):
                            yield Static(f"  task:  {f['task']}", classes="itembody")
                        yield Static(f"  error: {f['error']}", classes="itembody")
            yield Static("click anywhere or press Esc to close", id="modalhint")

    def on_click(self) -> None:
        self.dismiss()


class PassbackModal(FleetModal):
    """WinClaude→Fleet passback files, newest-first with an unread (●) marker. Opening marks all seen
    so the header's pb counter clears. Read-only: it never touches the passback files, only the seen-state."""
    BINDINGS = [("escape", "dismiss", "Close")]

    def __init__(self, items):
        super().__init__()
        self._items = items

    def compose(self) -> ComposeResult:
        with Vertical(id="modalbox"):
            n_new = sum(1 for it in self._items if it.get("new"))
            yield Static(f"WINCLAUDE PASSBACK — {len(self._items)} file(s), {n_new} new", id="modaltitle")
            with VerticalScroll(id="modalbody"):
                if not self._items:
                    yield Static("No passback files yet. ✓")
                else:
                    for it in self._items:
                        dot = "[cyan]●[/] " if it.get("new") else "[gray]○[/] "
                        yield Static(f"{dot}[b]{escape(str(it.get('title','?')))}[/b]  "
                                     f"[dim]· {escape(str(it.get('age','')))}[/]", classes="itemtitle")
                        yield Static(f"  [dim]{escape(str(it.get('name','')))}[/]", classes="itembody")
            yield Static("newest first · ● = new since last view · Esc to close", id="modalhint")

    def on_mount(self) -> None:
        # viewing the list = acknowledging it (clears the header pb counter); seen-state only, files untouched
        try:
            passback.mark_all_seen()
        except Exception:
            pass

    def on_click(self) -> None:
        self.dismiss()


class JobOutputModal(FleetModal):
    """Recent output for jobs that ran — the tail of each job's latest Hermes cron output file."""
    BINDINGS = [("escape", "dismiss", "Close")]

    def __init__(self, outputs):
        super().__init__()
        self._outputs = outputs

    def compose(self) -> ComposeResult:
        with Vertical(id="modalbox"):
            yield Static("JOBS — recent output", id="modaltitle")
            with VerticalScroll(id="modalbody"):
                if not self._outputs:
                    yield Static("No recent job output on disk (Hermes crons write to ~/.hermes/cron/output).")
                else:
                    for o in self._outputs:
                        yield Static(o["name"], classes="itemtitle")
                        yield Static(fmt._colorize_log(o["tail"]), classes="itembody")
            yield Static("click anywhere or press Esc to close", id="modalhint")

    def on_click(self) -> None:
        self.dismiss()


# ---------------------------------------------------------------- command palette

class FleetCommands(Provider):
    """Adds the fleet's actions (with descriptions) to the Ctrl+P command palette — a discoverable,
    fuzzy-searchable control surface so nothing has to be memorized (great on phone/textual-web). Every
    entry just invokes an EXISTING gated action; the palette adds discovery, not new capability."""
    def _entries(self):
        app = self.app
        return [
            # — attention / inbox surfaces —
            ("Inbox: pending items + acknowledge",
             "Open the INBOX detail — pending alerts across all channels; ack clears them. Same as i.",
             app.action_show_inbox),
            ("Passback: WinClaude → Fleet files",
             "Open the WinClaude passback inbox (newest-first, unread markers). Same as p.",
             app.action_show_passback),
            ("Alerts: proactive alert history",
             "The rolling log of proactive attention alerts (fail / down / hot). Same as a.",
             app.action_alerts),
            ("Curation: pass log + trigger a pass",
             "Recent curation passes (what each changed) + a button to queue a new pass. Same as C.",
             app.action_show_curation),
            ("Failures: recent tool failures",
             "Recent failed tool calls — tool, model, task, error. Same as x.",
             app.action_show_failures),
            # — dispatch / jobs —
            ("Dispatch: send a brief to a fleet leg",
             "Open the dispatch box (targets + presets) to send a brief to a gated fleet leg. Same as d.",
             app.action_dispatch),
            ("Jobs: recent Hermes cron output",
             "Color-coded tail of recent job output. Same as o.",
             app.action_show_jobs),
            # — models —
            ("Models: warm one into VRAM",
             "Warm a model into VRAM (pick from the list). Same as w.",
             app.action_warm_model),
            ("Models: unload idle models",
             "Free VRAM by unloading idle loaded models. Same as u.",
             app.action_unload_idle_models),
            ("Models: installed inventory",
             "Every installed model on disk + its size. Same as m.",
             app.action_models_list),
            # — view —
            ("Refresh now",
             "Force an immediate data refresh (don't wait for the 1s tick). Same as r.",
             app.action_refresh_now),
            ("Terminal: toggle embedded shell",
             "Show/hide the embedded pyte terminal. Same as Ctrl+`.",
             app.action_toggle_terminal),
            ("Screenshot the TUI",
             "Save an SVG screenshot of the current view. Same as s.",
             app.action_screenshot),
            # — focus / cosmetics —
            ("Focus Mode: toggle on/off",
             "Pause/resume the noisy watchers (curation-watcher, github-activity-watch) so background "
             "triggers don't interrupt you. Same as pressing f.",
             app.action_toggle_focus),
            ("Focus Mode: what does it do?",
             "Open a full explanation of focus mode and which loops it pauses.",
             app.action_focus_help),
            ("Cosmetics: animation appearance + where",
             "Customize the animated spinners/glow — style, glow, speed, and which panels animate. Same as pressing c.",
             app.action_cosmetics),
        ]

    async def discover(self) -> Hits:
        for name, help_text, cb in self._entries():
            yield DiscoveryHit(name, cb, help=help_text)

    async def search(self, query: str) -> Hits:
        matcher = self.matcher(query)
        for name, help_text, cb in self._entries():
            score = matcher.match(name)
            if score > 0:
                yield Hit(score, matcher.highlight(name), cb, help=help_text)


class WarmModal(FleetModal):
    """Pick a COLD (on-disk) model to warm into VRAM. Thin runner over `ollama run <model> ok` (detached) —
    the same load path fleet-model uses; ollama keeps it resident (keep_alive). The counterpart to `u` (unload)."""
    BINDINGS = [("escape", "dismiss", "Close")]

    def compose(self) -> ComposeResult:
        from fleet_tui.sources import modelstate
        with Vertical(id="modalbox"):
            yield Static("🔥 WARM A MODEL — load it into VRAM", id="modaltitle")
            with VerticalScroll(id="modalbody"):
                self._cold = [m for m in modelstate.list_models() if not m.loaded]
                if self._cold:
                    for j, m in enumerate(self._cold):
                        with Horizontal(classes="raterow"):
                            yield Button(f"🔥 {m.name}", id=f"warm-{j}", variant="success")
                            gb = getattr(m, "gb", 0) or 0
                            yield Static(f"  {gb:.0f}GB" if gb else "", classes="itembody")
                else:
                    yield Static("all pulled models are already loaded.", classes="itembody")
            yield Static("🔥 warm = ollama loads it (keep_alive) · a big model may evict others · Esc to close", id="modalhint")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if not bid.startswith("warm-"):
            return
        try:
            m = self._cold[int(bid[5:])]
        except (ValueError, IndexError):
            return
        import subprocess
        try:
            # argv form (no shell, injection-proof); detached so it survives + loads resident per keep_alive
            subprocess.Popen(["ollama", "run", m.name, "ok"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            self.app.notify(f"🔥 warming {m.name} — loading into VRAM (a few seconds)…", timeout=8)
        except Exception:
            self.app.notify(f"warm failed ({m.name})", severity="error", timeout=6)
        self.dismiss()


class ModelsListModal(FleetModal):
    """Read-only inventory: EVERY model on disk with its size + detail (params · quant · family · date),
    largest first. Opened by clicking the MODELS panel's IDLE section (or the `m` key). Lets the owner
    track exactly what's pulled — independent of what's loaded in VRAM."""
    BINDINGS = [("escape", "dismiss", "Close")]

    def compose(self) -> ComposeResult:
        from rich.markup import escape
        from fleet_tui.sources import modelstate
        self._installed = modelstate.installed_models()   # already sorted by size desc
        total = sum(getattr(m, "size_gb", 0) or 0 for m in self._installed)
        with Vertical(id="modalbox"):
            yield Static(f"💾 INSTALLED MODELS — {len(self._installed)} on disk · {total:.0f}GB total", id="modaltitle")
            with VerticalScroll(id="modalbody"):
                if self._installed:
                    for m in self._installed:
                        det = " · ".join(x for x in (m.param_size, m.quant, m.family, m.modified) if x)
                        line = f"[b]{m.size_gb:>5.1f}GB[/b]  {escape(m.name)}"
                        if det:
                            line += f"   [dim]{escape(det)}[/]"
                        yield Static(line, classes="itembody")
                else:
                    yield Static("no models found (ollama unreachable?).", classes="itembody")
            yield Static("read-only inventory · click anywhere or Esc to close", id="modalhint")

    def on_click(self) -> None:
        self.dismiss()


class CurationModal(FleetModal):
    """Curation-loop LOG — recent passes (what each changed) + trigger status + a button to QUEUE a pass.
    The button flips the EXISTING gated `.trigger` to pending; the next orchestrator turn runs the pass.
    The TUI never runs a curation pass itself (monitor, not orchestrator)."""
    BINDINGS = [("escape", "dismiss", "Close")]

    def compose(self) -> ComposeResult:
        st = curation.trigger_status()
        self._passes = curation.recent_passes(25)
        with Vertical(id="modalbox"):
            yield Static("🔄 CURATION PASSES — recent log + trigger", id="modaltitle")
            if st.get("pending"):
                reasons = ", ".join(str(r) for r in st.get("reasons", []))[:90]
                yield Static(f"[yellow]● a pass is QUEUED[/] (pass {st.get('pass_n','?')}) — runs on the next "
                             f"orchestrator turn{('  · ' + escape(reasons)) if reasons else ''}", classes="itembody")
            else:
                yield Static("[dim]no pass queued right now[/]", classes="itembody")
            with Horizontal(id="cur_tools"):
                yield Button("▶ Trigger curation pass", id="cur-trigger", variant="primary")
            with VerticalScroll(id="modalbody"):
                if not self._passes:
                    yield Static("no curation passes logged yet.")
                else:
                    for p in self._passes:
                        kc = "green" if p.get("kind") == "CHANGE" else "gray"
                        head = f" — {escape(p['headline'])}" if p.get("headline") else ""
                        yield Static(f"[{kc}]PASS {escape(str(p.get('pass_n','?')))}[/] · "
                                     f"[dim]{escape(str(p.get('date',''))[:16])}[/] · "
                                     f"[{kc}]{escape(str(p.get('kindraw','')))}[/]{head}", classes="itemtitle")
                        if p.get("summary"):
                            yield Static(f"  [dim]{escape(str(p['summary'])[:150])}[/]", classes="itembody")
            yield Static("▶ queues a pass (Claude runs it next turn) · full log: CURATION_LEDGER.md · Esc to close",
                         id="modalhint")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cur-trigger":
            if curation.queue_pass():
                self.app.notify("🔄 curation pass QUEUED — runs on the next orchestrator turn", timeout=8)
            else:
                self.app.notify("couldn't queue the pass (trigger write failed)", severity="error", timeout=5)
            self.dismiss()


# ---------------------------------------------------------------- app

class FleetTUI(App):
    """Main application for the Fleet Fleet TUI."""

    TITLE = "Fleet Fleet"
    # Footer legend was overcrowded (all bindings show → overflow, some hidden). Keep only the
    # highest-traffic keys visible (show=True); everything else stays fully active but hidden from the
    # footer — the complete list lives in the ? help overlay + the Ctrl+P command palette. (2026-07-07)
    BINDINGS = [
        Binding("question_mark", "help", "Help"),                 # visible: the discoverability anchor
        Binding("d", "dispatch", "Dispatch"),                     # visible
        Binding("i", "show_inbox", "Inbox"),                      # visible
        Binding("p", "show_passback", "Passback"),                # visible
        Binding("q", "confirm_quit", "Quit"),                     # visible
        # Priority so they fire even when the embedded terminal has focus + swallows keys:
        Binding("ctrl+grave_accent", "toggle_terminal", "Terminal", priority=True),   # visible
        Binding("ctrl+q", "confirm_quit", "Quit", priority=True, show=False),
        # — active but hidden from the footer (in ? help + Ctrl+P palette) —
        Binding("f", "toggle_focus", "Focus mode", show=False),
        Binding("r", "refresh_now", "Refresh", show=False),
        Binding("o", "show_jobs", "Job output", show=False),
        Binding("x", "show_failures", "Failures", show=False),
        Binding("s", "screenshot", "Screenshot", show=False),
        Binding("c", "cosmetics", "Cosmetics", show=False),
        Binding("a", "alerts", "Alerts", show=False),
        Binding("u", "unload_idle_models", "Unload idle", show=False),
        Binding("w", "warm_model", "Warm model", show=False),
        Binding("m", "models_list", "Installed models", show=False),
        Binding("C", "show_curation", "Curation log", show=False),
        # Ops-tab keyboard navigation (vim-style; only act on the Ops tab, else pass through) —
        # makes the master-detail fully keyboard-drivable for SSH/no-mouse use.
    ]
    COMMANDS = App.COMMANDS | {FleetCommands}
    CSS = """
    /* Embedded terminal: hidden by default; Ctrl+` toggles it (shell stays alive when hidden). */
    #terminal { display: none; height: 14; }
    TabPane Static {
        border: round $accent;
        border-title-color: $accent;
        border-title-style: bold;
        padding: 0 1;
        margin: 0 1;
        width: 100%;
        height: 1fr;            /* panels SHARE the vertical space (fr) → collapsing one grows the rest */
        overflow-y: auto;       /* tall content scrolls within the panel's share instead of clipping */
    }
    /* content-weighted shares so a 1-line FOCUS doesn't eat as much as the 16-line JOBS */
    #research_playlists { margin-top: 1; height: 2fr; }
    #health { height: 3fr; }
    #models { height: 2fr; }
    #jobs   { height: 3fr; }
    #posture { height: 2fr; }
    #inbox  { height: 2fr; }
    /* Ops tab — master-detail split (the list scrolls, so nothing gets buried below the fold) */
    #ops-actions Button { margin: 0 1 0 0; }
    /* collapsed → a thin titled bar; !important beats the #id height rules above */
    Static.collapsed { height: 3 !important; overflow: hidden; }

    DetailModal, FocusHelpModal { align: center middle; }
    #modalbox {
        width: 80%; max-width: 100; height: auto; max-height: 80%;
        border: round $accent; background: $surface; padding: 1 2;
    }
    #modaltitle { text-style: bold; color: $accent; border: none; padding: 0; margin: 0; }
    #modalbody { border: none; padding: 0; margin: 0; height: auto; max-height: 12; }  /* fixed rows (a % vs the auto-height parent never caps) → recents SCROLL, never push the targets off-screen */
    #modalbody Static, #modalbox Static { border: none; padding: 0; margin: 0; }
    #modalhint { color: $text-muted; margin-top: 1; }
    .itemtitle { text-style: bold; margin-top: 1; }
    .itembody { color: $text; margin-bottom: 1; }
    #dispatch_input { height: 8; margin: 1 0 0 0; }
    #dispatch_tools { height: auto; margin-top: 1; }
    #dispatch_tools Button { margin: 0 1 0 0; }
    .raterow { height: auto; margin: 0 0 1 1; }
    .raterow Button { margin: 0 1 0 0; min-width: 6; }
    .alertrow { height: auto; margin: 0 0 1 1; }
    .alertrow Button { margin: 0 1 0 0; min-width: 6; }
    #cur_tools { height: auto; margin: 0 0 1 0; }
    #dispatch_targets { height: auto; max-height: 16; margin-top: 1; border: none; }
    .tgtgroup { color: $text-muted; text-style: bold; padding: 0; margin: 1 0 0 0; border: none; }
    .tgtrow { height: auto; }
    .tgtrow Button { margin: 0 1 0 0; min-width: 12; }
    #dispatch_legend { margin-top: 1; padding: 0 1; color: $text-muted; }
    #cos-spinner-row { height: auto; }
    #cos-spinner-row Button { margin: 0 1 0 0; }
    #quit_btns { height: auto; margin-top: 1; }
    #quit_btns Button { margin: 0 1 0 0; }
    #jobdetail_btns { height: auto; margin-top: 1; }
    """

    PANEL_TITLES = {"research_playlists": "RESEARCH PLAYLISTS", "health": "HEALTH  (click / x for failures)", "models": "MODELS", "jobs": "JOBS", "posture": "POSTURE", "inbox": "INBOX  (click / press i for detail)"}

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True, time_format="%I:%M:%S %p")   # 12-hour clock
        with TabbedContent(initial="tab-fleet"):
            with TabPane("Fleet", id="tab-fleet"):
                with Vertical():
                    yield ResearchPlaylistsStatic(id="research_playlists")
                    yield HealthStatic(id="health")
                    yield ModelsStatic(id="models")
                    yield JobsStatic(id="jobs")
                    yield PostureStatic(id="posture")
                    yield InboxStatic(id="inbox")
                    # Add terminal widget here (hidden by default)
                    yield TerminalPane(id="terminal")
        yield Footer()

    def on_mount(self) -> None:
        self._inbox_items = []
        self._data = None
        self._frame = 0
        self._fx = True   # cosmetic animations ON (the cosmetics menu will drive this later)
        self._collapsed = set()   # ids of minimized panels (click a title row to toggle)
        self._ops_selected = None  # id of the selected OpsItem in the Ops tab (master-detail)
        self._ops_filter = "all"  # filter for ops list: all, running, stale, fail, feedback-due, cloud, local, cron
        self._ops_text = ""       # live "/ filter-as-you-type" substring over the Ops list (title/detail/id)
        self._alert_history = deque(maxlen=60)   # rolling proactive-alert log (press a)
        # Trends tab: rolling ~90-sample history (1 sample / data refresh) for the GPU/CPU util+temp plots
        self._trend = {k: deque(maxlen=90) for k in
                       ("gpu0", "gpu1", "cpu", "gpu0_t", "gpu1_t", "cpu_t")}
        self._trends_built = False
        # restore the last-chosen theme (Textual doesn't persist it) BEFORE arming the save-on-change
        try:
            saved = open(THEME_FILE).read().strip()
            if saved and saved in self.available_themes:
                self.theme = saved
        except Exception:
            pass
        self._theme_loaded = True
        for pid, title in self.PANEL_TITLES.items():
            self.query_one(f"#{pid}", Static).border_title = title
        self._update_subtitle()   # Header sub-title: version + attention counter + theme (always visible)
        self._cos = cosmetics.load()               # restore saved cosmetics prefs (style/glow/speed/cats)
        self.apply_cosmetics()                     # sets _fx + anim style + the speed-driven cosmetic timer
        self.set_interval(1.0, self.refresh_panels)   # 1s data refresh (heavy probes stay cache-throttled)
        self.refresh_panels()

    def watch_theme(self, theme: str) -> None:
        # keep the Header's theme label in sync the instant the theme changes (command palette)
        self._update_subtitle(theme=theme)

    def _update_subtitle(self, theme: str = None) -> None:
        """Header sub-title = version · attention counter (alerts/partial/feedback/passback) · theme.
        The counter is the single-glance 'what needs me' target before choosing a tab (QoL wave 4).
        Pure string work off the cached _data; never raises."""
        theme = theme if theme is not None else self.theme
        try:
            d = getattr(self, "_data", None) or {}
            n_alert = len(getattr(self, "_alerts", None) or d.get("alerts", []) or [])
            n_partial = sum(1 for x in d.get("dispatches", []) if x.get("partial"))
            n_fb = sum(1 for o in d.get("ops", []) if "FEEDBACK DUE" in (getattr(o, "detail", "") or ""))
            n_pb = sum(1 for p in d.get("passback", []) if p.get("new"))
            chips = []
            if n_alert:   chips.append(f"⚠{n_alert}")
            if n_partial: chips.append(f"partial{n_partial}")
            if n_fb:      chips.append(f"fb{n_fb}")
            if n_pb:      chips.append(f"pb{n_pb}")
            # NB: the Header sub_title renders markup LITERALLY (verified) — keep this plain text, no [color] tags
            counter = ("  " + " ".join(chips)) if chips else "  ✓clear"
        except Exception:
            counter = ""
        self.sub_title = f"v{VERSION}{counter} · {theme}"
        # persist ONLY user-initiated changes (after on_mount restore) so it survives reopen
        if getattr(self, "_theme_loaded", False):
            try:
                os.makedirs(os.path.dirname(THEME_FILE), exist_ok=True)
                with open(THEME_FILE, "w") as f:
                    f.write(theme)
            except Exception:
                pass

    @work(thread=True, exclusive=True)
    def refresh_panels(self) -> None:
        data = gather_data()
        self.call_from_thread(self._apply, data)

    def _apply(self, data: dict) -> None:
        """Called on each 3s DATA refresh: cache the raw data, run the once-per-refresh side effects
        (inbox items + new-item cue + alert notifications), then paint."""
        self._data = data
        self._inbox_items = data["inbox"]
        # inbox NEW-ITEM cue — notify when something lands that wasn't there last refresh
        cur_keys = {(i.source, i.title) for i in data["inbox"]}
        prev = getattr(self, "_inbox_keys", None)
        if prev is not None:
            fresh = cur_keys - prev
            if fresh:
                titles = ", ".join(t for _, t in fresh)[:70]
                self.notify(f"📥 inbox: {len(fresh)} new — {titles}", title="Inbox", timeout=10)
        self._inbox_keys = cur_keys
        # proactive alerts — notify on NEW attention conditions (fail / down / hot)
        cur_alerts = set(data.get("alerts", []))
        pa = getattr(self, "_alerts", None)
        if pa is not None:
            for msg in sorted(cur_alerts - pa):
                self.notify(msg, title="Alert", severity="warning", timeout=15)
                self._alert_history.append((time.strftime("%H:%M:%S"), msg))   # rolling log (press a)
        self._alerts = cur_alerts
        self._paint()
        self._update_subtitle()   # refresh the header attention counter with this cycle's data
        self._sample_trends(data)   # append one history point + redraw the Trends plots (1/refresh, not per frame)

    def _is_collapsed(self, pid) -> bool:
        return pid in getattr(self, "_collapsed", set())

    def _toggle_section(self, pid) -> None:
        """Minimize/restore a panel — open panels re-share the freed space (fr). Title-row click hook."""
        if pid in self._collapsed:
            self._collapsed.discard(pid)
        else:
            self._collapsed.add(pid)
        try:
            self.query_one(f"#{pid}", Static).set_class(pid in self._collapsed, "collapsed")
        except Exception:
            pass
        self._paint()

    def _title(self, pid, base, active) -> str:
        """Panel border title = collapse chevron (▼ open / ▶ minimized) + an active-work spinner + label."""
        chevron = "▶" if self._is_collapsed(pid) else "▼"
        spin = f" {anim.spin(self._frame)}" if (active and not self._is_collapsed(pid)) else ""
        return f"{chevron}{spin} {base}"

    def _paint(self) -> None:
        """Format the cached raw data (with the current animation frame) → update panels + titles.
        Called by _apply (3s data refresh) AND _cosmetic_tick (~8fps). PURE string work, no I/O —
        so animating never spawns a subprocess (holds the crash-hardening rule). Collapsed panels get
        empty bodies (just their titled bar shows)."""
        d = getattr(self, "_data", None)
        if not d:
            return
        f = self._frame if getattr(self, "_fx", True) else None
        cats = (getattr(self, "_cos", None) or {}).get("cats", {})
        fj = f if cats.get("jobs", True) else None       # per-panel animation gate (cosmetics menu)
        fc = f if cats.get("coding", True) else None
        fh = f if cats.get("health", True) else None
        fp = f if cats.get("posture", True) else None

        def put(pid, text):
            self.query_one(f"#{pid}", Static).update("" if self._is_collapsed(pid) else text)

        try:
            put("research_playlists", fmt.format_research_playlists(d.get("research_playlists", [])))
            put("health", fmt.format_health(d["health"], fh, net=d.get("network")))
            cloud = list(d.get("cloud", [])) + list(d.get("bg_agents", []))
            put("models", "\n".join((fmt.format_models(d["models"], f),
                                      fmt.format_box_models(d.get("boxes", []), d.get("models_by_box", {}), d.get("throughput", {})),
                                      fmt.format_cloud_legs(cloud, f),
                                      fmt.format_receipt_grid(d.get("receipts", []), d.get("boxes", [])),
                                      fmt.format_lanes(d.get("lanes", []), d.get("boxes", [])),
                                      fmt.format_downloads(d.get("downloads", [])))))
            put("jobs", fmt.format_jobs(d["jobs"], fj))
            put("posture", fmt.format_posture(d.get("posture")))
            put("inbox", fmt.format_inbox(d["inbox"]))
            # Ops tab (master-detail) — auto-(re)select a valid item so the detail pane is never stale/blank.
            # The list is FILTERED by the active Ops filter (F); the summary keeps FULL-fleet totals.
            ops_list = d.get("ops", [])
            visible = ops.filter_ops(ops_list, getattr(self, "_ops_filter", "all"), getattr(self, "_ops_text", ""))
            ops_ids = [o.id for o in visible]
            if getattr(self, "_ops_selected", None) not in ops_ids:
                self._ops_selected = ops_ids[0] if ops_ids else None
            ops_sel = next((o for o in visible if o.id == self._ops_selected), None)
        except Exception:
            return
        # titles: collapse chevron + live count + an active spinner (respecting the per-panel anim gate)
        jobs_active = any(getattr(j, "running", False) for j in d["jobs"]) and fj is not None
        coding_active = any(x.get("running") for x in d["dispatches"]) or \
            any(getattr(m, "busy", False) and getattr(m, "loaded", False) for m in d["models"])
        # HEALTH heartbeat: a breathing ● — palegreen when calm, red on alarm — so the panel always reads "live"
        h_collapsed = self._is_collapsed("health")
        _hb_color = "red" if bool(getattr(self, "_alerts", None)) else "palegreen"
        _hb = f" {anim.glow('●', _hb_color, self._frame)}" if (fh is not None and not h_collapsed) else ""
        _rp_n = len(d.get("research_playlists", []))
        self.query_one("#research_playlists", Static).border_title = self._title("research_playlists", f"RESEARCH PLAYLISTS ({_rp_n})", False)
        self.query_one("#health", Static).border_title = f"{'▶' if h_collapsed else '▼'}{_hb} HEALTH  (body / x = failures)"
        cloud_n = len(d.get("cloud", [])) + len(d.get("bg_agents", []))
        self.query_one("#models", Static).border_title = self._title("models", "MODELS" + (f" · ☁ {cloud_n}" if cloud_n else ""), bool(cloud_n) and f is not None)
        self.query_one("#jobs", Static).border_title = self._title("jobs", f"JOBS ({len(d['jobs'])}) · body / o = output", jobs_active)
        # POSTURE title carries an attention chip when a backup/supply alert is pending or an upstream CRITICAL is behind.
        # The chip breathes (glow) when the posture animation cat is on, and its color is the recolorable 'attn' slot.
        _pos = d.get("posture", {}) or {}
        _pos_warn = bool((_pos.get("backup", {}) or {}).get("alert_pending")
                         or (_pos.get("supply", {}) or {}).get("alert_pending")
                         or (_pos.get("upstream", {}) or {}).get("critical"))
        _attn_c = anim.color("attn", "red")
        if _pos_warn:
            _dot = anim.glow("●", _attn_c, self._frame) if fp is not None else f"[{_attn_c}]●[/]"
            _pos_chip = f" {_dot} [{_attn_c}]attn[/]"
        else:
            _pos_chip = ""
        self.query_one("#posture", Static).border_title = self._title("posture", f"POSTURE{_pos_chip} · body = alerts", False)
        self.query_one("#inbox", Static).border_title = self._title("inbox", f"INBOX ({len(d['inbox'])}) · body / i = detail", False)
        cpre = f"{anim.spin(self._frame)} " if (coding_active and fc is not None) else ""
        ops_list = d.get("ops", [])
        ops_active = any(getattr(o, "status", "") == "running" for o in ops_list) and f is not None
        filt = getattr(self, "_ops_filter", "all")
        ftag = "" if filt == "all" else f" ({filt})"              # parens, NOT [brackets] — [x] parses as Rich markup in a border title
        txt = getattr(self, "_ops_text", "")
        ttag = f" [cyan]/{escape(txt)}[/]" if txt else ""         # active free-text filter (press / to edit, cyan so it stands out)
        n_stale = len(ops.filter_ops(ops_list, "stale"))          # full-fleet stale count (independent of the active filter)
        stale_tag = f" [red]⚠{n_stale}[/]" if n_stale else ""     # early in the title so a narrow border never truncates the warning

    def _sample_trends(self, data: dict) -> None:
        """Append one sample to each trend buffer (GPU0/1 + CPU util & temp) and redraw the two Trends
        plots. Called once per DATA refresh (1s) — NOT per cosmetic frame — so plotext work stays cheap.
        Never raises (a plotext/API hiccup must not take down the refresh loop)."""
        try:
            snap = data.get("health")
            gpu = (getattr(snap, "gpu", None) or []) if snap else []
            def g(i, key):
                c = gpu[i] if i < len(gpu) else {}
                return c.get(key) or 0
            self._trend["gpu0"].append(g(0, "util")); self._trend["gpu1"].append(g(1, "util"))
            self._trend["gpu0_t"].append(g(0, "temp")); self._trend["gpu1_t"].append(g(1, "temp"))
            self._trend["cpu"].append((getattr(snap, "cpu_util", None) or 0) if snap else 0)
            self._trend["cpu_t"].append((getattr(snap, "cpu_temp", 0) or 0) if snap else 0)
        except Exception:
            return
        # only redraw when the Trends tab is the active one (no point rendering an unseen plot)
        try:
            if self.query_one(TabbedContent).active != "tab-trends":
                return
        except Exception:
            return
        self._redraw_trends()

    def on_tabbed_content_tab_activated(self, event) -> None:
        """Redraw the Trends plots the moment the owner switches to that tab (else it's blank until the
        next 1s refresh). Guarded so other tab switches are no-ops."""
        try:
            if self.query_one(TabbedContent).active == "tab-trends":
                self._redraw_trends()
        except Exception:
            pass

    def _anything_active(self) -> bool:
        """Is there any live work worth animating? (skip the cosmetic repaint entirely when idle)."""
        d = getattr(self, "_data", None)
        if not d:
            return False
        if (getattr(self, "_cos", None) or {}).get("cats", {}).get("health", True):
            return True                                  # the HEALTH monitoring heartbeat beats continuously
        if any(getattr(j, "running", False) for j in d["jobs"]):
            return True
        if any(x.get("running") for x in d["dispatches"]):
            return True
        if any(getattr(m, "busy", False) and getattr(m, "loaded", False) for m in d["models"]):
            return True
        if d.get("util", 0) >= 20:                       # GPU computing
            return True
        # POSTURE attn chip breathes while there's an attention condition + its cat is on
        if (getattr(self, "_cos", None) or {}).get("cats", {}).get("posture", True):
            _pos = d.get("posture", {}) or {}
            if ((_pos.get("backup", {}) or {}).get("alert_pending")
                    or (_pos.get("supply", {}) or {}).get("alert_pending")
                    or (_pos.get("upstream", {}) or {}).get("critical")):
                return True
        return bool(getattr(self, "_alerts", None))      # health alarm pulsing

    def _cosmetic_tick(self) -> None:
        """Fast (~8fps) frame advance + repaint — ONLY when something's active (idle = near-zero cost)."""
        if not getattr(self, "_fx", True):
            return
        self._frame += 1
        if self._anything_active():
            self._paint()

    def action_toggle_focus(self) -> None:
        if focus.is_on():
            focus.turn_off()
        else:
            focus.turn_on(scope="noisy", by="tui")
        self.refresh_panels()

    def request_playlist_check(self, playlist) -> None:
        """Owner clicked a Research Playlists row → write the check-request intent + fire a Telegram
        confirmation. The TUI does NOT run the check (it is not an orchestrator); the request surfaces to
        Claude, who runs the actual check→stage flow. Best-effort; never blocks or crashes the UI."""
        name = getattr(playlist, "name", "")
        try:
            research_playlists.request_check(name, getattr(playlist, "url", ""))
        except Exception:
            pass
        try:
            import subprocess
            subprocess.Popen(
                ["bash", os.path.expanduser("~/.claude/curation/claude_tg.sh"),
                 f"📋 TUI: requested a new-video check of the '{name}' research playlist — to be staged for the research team."],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass
        self.notify(f"▶ Requested check: {name} — Claude will stage any new videos", title="Research", timeout=5)
        self.refresh_panels()

    def action_refresh_now(self) -> None:
        self.refresh_panels()

    def action_show_inbox(self) -> None:
        self.push_screen(DetailModal(getattr(self, "_inbox_items", [])))

    def action_show_passback(self) -> None:
        self.push_screen(PassbackModal(passback.list_passback()))

    def action_show_jobs(self) -> None:
        self.push_screen(JobOutputModal(joboutput.recent_outputs(jobs.list_jobs())))

    def action_show_failures(self) -> None:
        self.push_screen(FailuresModal(failures.recent_failures()))

    def on_input_changed(self, event: Input.Changed) -> None:
        """Live-filter the Ops list as the owner types in the `/` box (other Inputs bubble through)."""
        if getattr(event.input, "id", None) == "ops-filter-input":
            self._ops_text = event.value or ""
            self._ops_selected = None   # let the paint auto-select the first now-visible row
            self._paint()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Enter in the `/` filter box → keep the filter, release focus so single-key bindings work again."""
        if getattr(event.input, "id", None) == "ops-filter-input":
            try:
                self.screen.set_focus(None)
            except Exception:
                pass

    def action_unload_idle_models(self) -> None:
        """Unload idle loaded models."""
        from fleet_tui.sources import modelstate
        import subprocess

        models = modelstate.list_models()
        unloaded = []
        for m in models:
            if m.loaded and not m.busy:
                try:
                    # Run ollama stop <model_name> asynchronously — argv form (no shell, injection-proof)
                    subprocess.Popen(["ollama", "stop", m.name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    unloaded.append(m.name)
                except Exception:
                    pass  # Continue with other models if one fails

        if unloaded:
            self.notify(f"✅ Unloaded idle models: {', '.join(unloaded)}", timeout=6)
        else:
            self.notify("No idle models to unload.", severity="warning", timeout=4)

    def action_warm_model(self) -> None:
        """Open the warm picker — load a cold on-disk model into VRAM (counterpart to `u`)."""
        self.push_screen(WarmModal())

    def action_models_list(self) -> None:
        """Open the installed-models inventory (every model on disk + its size)."""
        self.push_screen(ModelsListModal())

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Ops-tab action bar (▶ Run / 📄 Output). Modal buttons are handled by their own screens; this
        only acts on the ops-* ids — any other button bubbles through here as a no-op."""
        bid = getattr(event.button, "id", "") or ""
        if bid == "ops-run":
            self._ops_action_run()
        elif bid == "ops-output":
            self._ops_action_output()

    def _selected_ops_item(self):
        ops_list = (getattr(self, "_data", None) or {}).get("ops", [])
        return next((o for o in ops_list if o.id == getattr(self, "_ops_selected", None)), None)

    def _on_ops_tab(self) -> bool:
        try:
            return self.query_one(TabbedContent).active == "tab-ops"
        except Exception:
            return False

    def action_dispatch(self) -> None:
        self.push_screen(DispatchModal())

    def action_cosmetics(self) -> None:
        self.push_screen(CosmeticsModal())

    def apply_cosmetics(self) -> None:
        """Push the cosmetics config into the running app: master on/off, spinner+glow style, and the
        cosmetic-timer speed (re-created at the chosen interval). Then repaint to reflect it now."""
        c = getattr(self, "_cos", None) or cosmetics.DEFAULTS
        self._fx = c["enabled"]
        anim.set_style(c["spinner"], c["glow"])
        anim.set_colors(c.get("colors", {}))
        interval = cosmetics.SPEEDS.get(c["speed"], 0.12)
        t = getattr(self, "_cos_timer", None)
        if t is not None:
            try:
                t.stop()
            except Exception:
                pass
        self._cos_timer = self.set_interval(interval, self._cosmetic_tick)
        self._paint()

    def action_focus_help(self) -> None:
        self.push_screen(FocusHelpModal())

    def action_help(self) -> None:
        self.push_screen(HelpModal())

    def action_alerts(self) -> None:
        self.push_screen(AlertsModal(getattr(self, "_alert_history", [])))

    def action_show_curation(self) -> None:
        self.push_screen(CurationModal())

    def action_toggle_terminal(self) -> None:
        """Toggle the visibility of the embedded terminal pane."""
        terminal = self.query_one("#terminal", TerminalPane)
        if terminal.display:
            # Hiding - just minimize (keep it mounted)
            terminal.display = False
            # Return focus to main content
            # This will select a main panel
            pass  # Focus is handled by Textual automatically when panels change visibility
        else:
            # Showing - make visible and focus the terminal
            terminal.display = True
            terminal.focus()

    def action_confirm_quit(self) -> None:
        self.push_screen(QuitConfirmModal())

    def action_screenshot(self) -> None:
        import os, time
        outdir = os.path.expanduser("~/Pictures/Screenshots")
        try:
            os.makedirs(outdir, exist_ok=True)
            svg = _tighten_svg(self.export_screenshot(title="Fleet Fleet"))
            path = os.path.join(outdir, "Fleet_Fleet_" + time.strftime("%Y-%m-%dT%H-%M-%S") + ".svg")
            with open(path, "w", encoding="utf-8") as f:
                f.write(svg)
            self.notify(f"screenshot saved: {path}", timeout=6)
        except Exception as e:
            self.notify(f"screenshot failed: {e}", severity="error", timeout=6)


def main():
    """Main entry point for the application."""
    FleetTUI().run()


if __name__ == "__main__":
    main()
