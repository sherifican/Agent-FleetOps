"""Pure display formatters for fleet_tui fleet TUI.

These are PURE functions with no framework dependencies.
They convert dataclass records into plain display strings.
"""
import datetime
import re
import time
import zlib
from rich.markup import escape
from fleet_tui.models import Job, InboxItem, HealthSnapshot, LoadedModel, FocusState, ModelState, Playlist
from fleet_tui.widgets import anim


# log-line coloring for the job-output view — matched by priority (first hit wins), word-boundaried so
# "token"/"broken" don't false-match "ok". Each raw line is Rich-ESCAPED first so brackets/paths in the
# log ([2026-…], [cron], list reprs) can't be parsed as markup.
_LOG_RULES = [
    (re.compile(r"\b(error|errors|traceback|exception|fatal|critical|failed|failure|fail|denied|refused)\b", re.I), "red"),
    (re.compile(r"\b(warn|warning|deprecat\w*|retry|retrying|skipped|timeout)\b", re.I), "yellow"),
    (re.compile(r"\b(success|succeeded|completed|complete|done|passed|ok|ready|started|✓)\b", re.I), "green"),
]
_LOG_TS = re.compile(r"^(\s*(?:\[[^\]]+\]|\d{4}-\d\d-\d\d[ T][\d:.,]+|\d\d:\d\d:\d\d))")


def _colorize_log(text: str) -> str:
    """Color a block of log output by level (red=error / yellow=warn / green=success), dimming a leading
    timestamp. Pure + markup-safe (escapes each line first). Blank/None → ''."""
    if not text:
        return ""
    out = []
    for line in text.splitlines():
        color = next((c for rx, c in _LOG_RULES if rx.search(line)), None)
        m = _LOG_TS.match(line)
        if m:
            ts = escape(m.group(1))
            rest = escape(line[m.end():])
            body = f"[dim]{ts}[/]" + (f"[{color}]{rest}[/]" if color else rest)
        else:
            e = escape(line)
            body = f"[{color}]{e}[/]" if color else e
        out.append(body)
    return "\n".join(out)


# --- coloring (Rich console markup; Textual Static renders it) -----------------------------------
# Model FAMILY → color (same family shares a color; unknown families get a stable crc32-picked color).
_FAMILY_COLORS = {
    "qwen": "cyan", "gemma": "green", "ornith": "gold", "glm": "magenta", "lfm": "orange",
    "deepseek": "dodgerblue", "qwythos": "mediumpurple", "hermes": "springgreen",
    "kimi": "salmon", "mistral": "deepskyblue", "llama": "yellow", "codex": "silver",
}
_FALLBACK_COLORS = ["cyan", "green", "gold", "magenta", "orange", "dodgerblue",
                    "mediumpurple", "springgreen", "salmon", "deepskyblue", "yellow"]


def _model_family(name: str) -> str:
    """Family key = leading alpha run of the cleaned name (qwen3-coder→qwen, gemma4→gemma, Ornith-1.0→ornith)."""
    m = re.match(r"[A-Za-z]+", _clean_model_name(name).lstrip())
    return m.group(0).lower() if m else "?"


def _model_color(name: str) -> str:
    fam = _model_family(name)
    return _FAMILY_COLORS.get(fam) or _FALLBACK_COLORS[zlib.crc32(fam.encode()) % len(_FALLBACK_COLORS)]


def _model_slot(default: str) -> str:
    """The configured 'model' label color, or `default` if it's the 'family' sentinel (keep per-family)."""
    mc = anim.color("model", "family")
    return default if mc == "family" else mc


def _color_model(name: str) -> str:
    """Cleaned model name in its color — per-family by default, or the cosmetics 'model' color if set."""
    disp = _clean_model_name(name)
    col = _model_slot(_model_color(name))
    return f"[{col}]{disp}[/]"


# uniform temp thresholds (owner 2026-07-03): above 59°C → yellow, above 85°C → red, for ALL components.
TEMP_WARN = 60   # ≥60 (i.e. above 59) → yellow
TEMP_HOT = 86    # ≥86 (i.e. above 85) → red
HEALTHY_GREEN = "palegreen"   # lighter green for healthy/low readings (temps, VRAM fill, disk, util %)


_SPARK = "▁▂▃▄▅▆▇█"


def sparkline(values, lo: float, hi: float) -> str:
    """A block-char sparkline of `values` scaled to [lo, hi]. Empty string for no data."""
    if not values:
        return ""
    span = (hi - lo) or 1
    out = []
    for v in values:
        i = int((v - lo) / span * (len(_SPARK) - 1))
        out.append(_SPARK[max(0, min(len(_SPARK) - 1, i))])
    return "".join(out)


def _temp(val: int, warn: int = TEMP_WARN, hot: int = TEMP_HOT) -> str:
    """A temperature reading colored green/yellow/red. Defaults to the uniform 59/85 thresholds."""
    color = "red" if val >= hot else ("yellow" if val >= warn else HEALTHY_GREEN)
    return f"[{color}]{val}°C[/]"


def _pct_color(pct) -> str:
    """Color a 0-100 usage % by load band: lighter-green (low) / yellow (mid) / orange (high) / red (near full)."""
    try:
        p = float(pct)
    except (TypeError, ValueError):
        return HEALTHY_GREEN
    if p >= 90:
        return "red"
    if p >= 70:
        return "orange"
    if p >= 40:
        return "yellow"
    return HEALTHY_GREEN


def _status(up: bool) -> str:
    return f"[{anim.color('ok', 'green')}]up[/]" if up else f"[{anim.color('fail', 'red')}]DOWN[/]"


def _okfail(ok) -> str:
    return f"[{anim.color('ok', 'green')}]ok[/]" if ok else f"[{anim.color('fail', 'red')}]FAIL[/]"


def _pad_markup(plain: str, markup: str, w: int) -> str:
    """Pad a possibly-marked-up cell to visible width `w` (padding computed on the PLAIN text)."""
    if len(plain) > w:
        return plain[:w]          # too long → drop color, truncate (rare; loaded cells are short)
    return markup + " " * (w - len(plain))


def _clean_model_name(name: str) -> str:
    """DISPLAY-ONLY tidy of an ollama model name — strips the HF registry prefix + redundant -GGUF so
    `hf.co/deepreinforce-ai/Ornith-1.0-35B-GGUF:Q4_K_M` shows as `Ornith-1.0-35B:Q4_K_M`. The real name
    (used for /api calls, matching, etc.) is never changed — this only touches the rendered string."""
    if not name:
        return name
    display = name.rsplit("/", 1)[-1] if "/" in name else name   # drop registry host + org path
    display = re.sub(r"-GGUF(?=:|$)", "", display, flags=re.IGNORECASE)  # drop the redundant GGUF marker
    # llama-server SIDECARS come named `<id>-<gguf-quant> (:<port>)` and overflow the kanban column
    # (truncation drops the family color → the "gray" bug). Strip the dash-joined GGUF quant ONLY when a
    # `(:port)` suffix follows, so ollama colon-quants (e.g. `:Q4_K_M`) are never touched.
    display = re.sub(r"-q\d+(?:[_-][a-z0-9]+)*(?=\s*\(:)", "", display, flags=re.IGNORECASE)
    return display


def _sched(job) -> str:
    """The schedule cell, colored + ESCAPED by kind: hermes crons (humanized 'every 3hrs') → cyan;
    system crons (raw '0 */6 * * *') → dim. (The old `[{kind}]` tag was invisible — Rich swallowed it
    as bad markup — so the color IS the kind cue now.)"""
    s = escape(job.schedule or "")
    return f"[gray]{s}[/]" if job.kind == "systemcron" else f"[{anim.color('schedule', 'cyan')}]{s}[/]"


def format_jobs(jobs: list, frame=None) -> str:
    """Format a list of jobs into a display string. Status marks are colored (green OK / red !! / a
    yellow ▶ when firing), schedules colored by kind. `frame` (int) animates actively-firing jobs
    (pulsing spinner + 'running…' + glowing name); frame=None renders static (no animation)."""
    if not jobs:
        return "(no jobs)"

    lines = []
    for job in jobs:
        if job.running:
            if frame is not None:
                rc = anim.color("running", "yellow")
                lines.append(f"{anim.active('running', rc, frame)}  {anim.glow(job.name, rc, frame)}  {_sched(job)}")
                continue
            mark = "[yellow]▶ [/]"  # actively firing right now
        elif job.last_status == "ok":
            mark = f"[{anim.color('ok', 'green')}]OK[/]"
        elif job.last_status == "fail":
            mark = f"[{anim.color('fail', 'red')}]!![/]"
        else:
            mark = "  "  # unknown status: blank (no noisy marker)
        lines.append(f"{mark} {job.name}  {_sched(job)}")

    return "\n".join(lines)


def format_inbox(items: list) -> str:
    """Format a list of inbox items into a display string."""
    if not items:
        return "inbox clear"
    
    lines = []
    for item in items:
        if item.priority == "crit":
            tag = "[!]"
        elif item.priority == "fyi":
            tag = "[.]"
        else:
            tag = "[•]"
        line = f"{tag} {item.title} - {item.detail}"
        lines.append(line)
    
    return "\n".join(lines)


def format_posture(p: dict) -> str:
    """Backup + supply-chain + upstream posture, one compact block. Pure, markup-safe, never raises on a
    partial/missing snapshot (every field is looked up defensively). `p` = sources.posture.snapshot()."""
    try:
        p = p or {}
        b = p.get("backup", {}) or {}
        s = p.get("supply", {}) or {}
        u = p.get("upstream", {}) or {}
        ac = anim.color("attn", "red")   # recolorable attention color (cosmetics 'attn' slot)
        lines = []

        # — backup —
        if b.get("alert_pending"):
            lines.append(f"[{ac}]⚠ backup ALERT pending — clear in INBOX[/]")
        last = b.get("last")
        if last:
            gl = "[green]✓[/]" if last.get("ok") else f"[{ac}]⚠[/]"
            lines.append(f"backup {gl} last {escape(str(last.get('ts','?')))}")
        else:
            lines.append("[gray]backup: no log[/]")
        repos, mirror = b.get("last_repos_ok"), b.get("last_mirror_ok")
        lines.append(f"  repos ✓ {escape(str(repos))}   mirror ✓ {escape(str(mirror))}"
                     if (repos or mirror) else "  [gray]no good push recorded[/]")
        ab = b.get("last_abort")
        if ab:
            lines.append(f"  [{ac}]last abort {escape(str(ab.get('ts','?')))}: "
                         f"{escape(str(ab.get('reason',''))[:60])}[/]")

        # — supply-chain —
        if s.get("ts"):
            al = s.get("alerts", 0)
            alc = f"[{ac}]" if al else "[green]"
            lines.append(f"supply {escape(str(s.get('ts')))} · {alc}alerts:{al}[/] · "
                         f"hooks:{s.get('install_hooks',0)} · new:{s.get('new_since_last',0)}"
                         + (f"  [{ac}]⚠ alert pending[/]" if s.get("alert_pending") else ""))
        else:
            lines.append("[gray]supply: no scan log[/]")

        # — upstream —
        behind = u.get("behind", 0)
        crit = u.get("critical", []) or []
        if behind:
            bc = f"[{ac}]" if crit else "[yellow]"
            lines.append(f"upstream {bc}{behind} behind[/]"
                         + (f", [{ac}]{len(crit)} CRITICAL[/]" if crit else ""))
            for c in crit[:3]:
                lines.append(f"  [{ac}]‣ {escape(str(c.get('name','?')))} "
                             f"{escape(str(c.get('local','?')))}→{escape(str(c.get('latest','?')))}[/]")
        else:
            lines.append("[green]upstream: all current[/]")
        return "\n".join(lines)
    except Exception:
        return "[gray]posture unavailable[/]"


def _ram_pct_color(pct) -> str:
    """RAM usage % -> colour. Red at 90+, yellow at 75+."""
    try:
        p = float(pct)
    except (TypeError, ValueError):
        return HEALTHY_GREEN
    return "red" if p >= 90 else ("yellow" if p >= 75 else HEALTHY_GREEN)


def _swap_pct_color(pct) -> str:
    """Swap usage % -> colour. DELIBERATELY TIGHTER than RAM: red at 75+, yellow at 50+.

    Not an oversight and not to be "tidied" to match _ram_pct_color. On 2026-08-08 this box hit
    "device memory is nearly full" while RAM sat at ~24% and swap at ~86%. RAM percentage alone
    would have shown green through the whole incident. Sustained swap use here means the machine
    is already thrashing, so swap earns an earlier warning than RAM does.
    """
    try:
        p = float(pct)
    except (TypeError, ValueError):
        return HEALTHY_GREEN
    return "red" if p >= 75 else ("yellow" if p >= 50 else HEALTHY_GREEN)


def format_health(snap: HealthSnapshot, frame=None, hist=None, net=None) -> str:
    """Format a health snapshot into a display string. `frame` (int) pulses genuine ALARM states
    (a DOWN service breathes red); `hist` draws trend sparklines; `net` (from sources.network.status)
    draws the PC/Telegram bridge line."""
    lines = []

    # services — a DOWN one pulses when animating
    if snap.services:
        def svc(n, up):
            if not up and frame is not None:
                return f"{n} {anim.glow('DOWN', 'red', frame)}"
            return f"{n} {_status(up)}"
        lines.extend(svc(n, up) for n, up in snap.services.items())   # each service on its own row
    else:
        lines.append("services: (none)")

    # bridges — PC↔Fleet direct link + Telegram bridge (sources/network.py, local-lane authored)
    if net:
        pc, tg, cx = net.get("pc", {}), net.get("telegram", {}), net.get("codex", {})
        okc, failc = anim.color("ok", "green"), anim.color("fail", "red")
        pc_s = f"[{okc}]up[/]" if pc.get("reachable") else (f"[yellow]link-only[/]" if pc.get("link_up") else f"[{failc}]down[/]")
        tg_s = f"[{okc}]up[/]" if tg.get("gateway_up") else f"[{failc}]down[/]"
        _cxst = cx.get("state")
        seg = [f"PC {pc_s}", f"telegram {tg_s}"]
        if _cxst and _cxst != "disabled":   # owner-disabled bridge is omitted entirely, not shown as "off"
            cx_s = f"[{okc}]up[/]" if _cxst == "up" else (f"[{failc}]down[/]" if _cxst == "down" else "[gray]off[/]")
            cx_label = escape(str(cx.get("host_label", "WinPC")))
            seg.append(f"codex↔{cx_label} {cx_s}")
        lines.append("bridges: " + "  ·  ".join(seg))
    
    # gpu (per-card VRAM + temp + utilization %)
    if snap.gpu:
        cards = []
        for i, c in enumerate(snap.gpu):
            used, total = c.get("used", 0) / 1024, c.get("total", 0) / 1024
            frac = (c.get("used", 0) / c.get("total", 1)) if c.get("total") else 0
            vcolor = "red" if frac >= 0.85 else ("yellow" if frac >= 0.6 else HEALTHY_GREEN)
            util = c.get("util")
            util_s = f" [{_pct_color(util)}]{int(util)}%[/]" if util is not None else ""
            cards.append(f"gpu{i}: [{vcolor}]{used:.1f}[/]/{total:.1f}GB {_temp(c.get('temp', 0))}{util_s}")
        lines.extend(cards)   # each GPU on its own row (easier to read than packed side-by-side)

    # cpu + ssd temps on ONE row (uniform 59/85 thresholds; owner 2026-07-03) — combined 2026-07-07 so all
    # three sensors stay visible after the POSTURE panel shrank the HEALTH panel's vertical share (ssd2 was
    # the first line to clip below the fold). cpu carries a load % beside its temp, matching the GPU rows.
    temps = []
    if snap.cpu_temp or getattr(snap, "cpu_util", None) is not None:
        cu = getattr(snap, "cpu_util", None)
        cpu_util_s = f" [{_pct_color(cu)}]{int(cu)}%[/]" if cu is not None else ""   # matches the gpu util % format
        cpu_temp_s = _temp(snap.cpu_temp) if snap.cpu_temp else ""
        temps.append((f"cpu {cpu_temp_s}{cpu_util_s}").rstrip())
    if snap.ssd_temp:
        temps.append(f"ssd {_temp(snap.ssd_temp)}")
    if snap.ssd_ext_temp:
        temps.append(f"ssd2 {_temp(snap.ssd_ext_temp)}")
    if temps:
        lines.append("  ·  ".join(temps))   # cpu · ssd · ssd2 on a single dense row

    # disk — free space on the model/log partition (fills up silently); color by % free
    if snap.disk_total_gb:
        pct = snap.disk_free_gb / snap.disk_total_gb if snap.disk_total_gb else 1
        dcolor = "red" if (pct < 0.08 or snap.disk_free_gb < 15) else ("yellow" if pct < 0.15 else HEALTHY_GREEN)
        lines.append(f"disk: [{dcolor}]{snap.disk_free_gb:.0f}[/]/{snap.disk_total_gb:.0f}GB free")

    # memory — RAM and swap. Added 2026-08-08 after the box OOM-killed an application with no memory
    # readout anywhere in the TUI. Both rows matter: swap is the half that was actually in danger.
    if snap.ram_total_gb:
        lines.append(f"ram: [{_ram_pct_color(snap.ram_pct)}]{snap.ram_used_gb:.1f}[/]"
                     f"/{snap.ram_total_gb:.1f}GB ({snap.ram_pct}%)")
    if snap.swap_total_gb:
        lines.append(f"swap: [{_swap_pct_color(snap.swap_pct)}]{snap.swap_used_gb:.1f}[/]"
                     f"/{snap.swap_total_gb:.1f}GB ({snap.swap_pct}%)")

    # (GPU util % now shows inline on the gpu line above; the sparkline trend graphs were removed 2026-07-03)

    # loaded
    if snap.loaded:
        mcol = _model_slot("magenta")   # 'model' color slot; default magenta (owner's earlier pick)
        loaded_lines = [f"[{mcol}]{_clean_model_name(m.name)}[/] {m.gb}GB" for m in snap.loaded]
        lines.append("loaded: " + ", ".join(loaded_lines))
    else:
        lines.append("loaded: none")

    # vram note
    if snap.vram_note:
        lines.append(f"vram: {snap.vram_note}")
    
    # critical caps
    if snap.critical_caps:
        critical_lines = [f"{c.get('cap','')} {_okfail(c.get('ok'))}" for c in snap.critical_caps]
        lines.append("critical: " + ", ".join(critical_lines))

    # stability — uptime + last GPU Xid/hang (from the forensics logger)
    if snap.uptime:
        lines.append(f"up {snap.uptime}  ·  Xid: {snap.xid}")

    # reliability — the event counts (402s / tool-errors / loop-breaks) over the report window
    if snap.reliability_tail:
        lines.append(f"reliability: {snap.reliability_tail} (1d)")

    return "\n".join(lines)


# ── Ops master-detail formatters (OpsItem records -> Rich-markup display strings) ──
# All colors route through anim.color(slot, default) so they stay CSS-valid under Textual (never Rich-256).
_OPS_GLYPH = {"loop": "↻", "dispatch": "→", "research": "⚑", "job": "▪"}


def _ops_status_color(status):
    """anim.color slot for a status, or None for idle/unknown (mirrors format_jobs' palette)."""
    return {
        "ok": anim.color("ok", "green"),
        "fail": anim.color("fail", "red"),
        "running": anim.color("running", "yellow"),
        "scheduled": anim.color("schedule", "cyan"),
    }.get(status or "", None)


def _ops_mark(status, frame):
    """Status mark as (markup, plain) — colored via anim.color (OK / !! / ~ / ▶ / spinner / blank)."""
    if status == "running":
        rc = anim.color("running", "yellow")
        g = anim.spin(frame) if frame is not None else "▶"
        return (f"[{rc}]{g}[/]", g)
    if status == "ok":
        return (f"[{anim.color('ok', 'green')}]OK[/]", "OK")
    if status == "fail":
        return (f"[{anim.color('fail', 'red')}]!![/]", "!!")
    if status == "scheduled":
        return (f"[{anim.color('schedule', 'cyan')}]~[/]", "~")
    return ("", "")  # idle / unknown


def _ops_ago(ts):
    """Humanize an epoch timestamp as a relative age; '' if not a usable number."""
    try:
        delta = time.time() - float(ts)
    except (TypeError, ValueError):
        return ""
    if delta < 60:
        return "just now"
    if delta < 3600:
        return f"{int(delta // 60)}m ago"
    if delta < 86400:
        return f"{int(delta // 3600)}h ago"
    return f"{int(delta // 86400)}d ago"


def _ops_source(ref):
    """One-line summary of an OpsItem.source_ref (a Job record or a dispatch dict); '' if none/unknown. Never raises."""
    try:
        if ref is None:
            return ""
        if isinstance(ref, dict):
            leg = ref.get("leg", "") or ref.get("name", "") or ""
            base = ref.get("base", "") or ref.get("when", "") or ""
            return " · ".join(str(p) for p in (leg, base) if p)
        rid = getattr(ref, "id", None)
        rkind = getattr(ref, "kind", None)
        if rid or rkind:
            return " · ".join(str(p) for p in (rid, rkind) if p)
        return ""
    except Exception:
        return ""


def format_ops_list(items, selected_id=None, frame=None) -> str:
    """Master list for the Ops tab: one line per OpsItem — kind glyph, status mark (via anim.color), the
    name (bold + glowing in the status color and ▸-prefixed when selected), and a humanized last-run /
    schedule trailer. `frame` (int) animates running rows. Pure, markup-safe, never raises on a bad item."""
    if not items:
        return "(no ops)"
    lines = []
    for item in items:
        if item is None:
            continue
        try:
            glyph = _OPS_GLYPH.get(getattr(item, "kind", None), "•")
            status = getattr(item, "status", "") or ""
            mark_markup, mark_plain = _ops_mark(status, frame)
            name_plain = escape(str(getattr(item, "title", "") or ""))
            selected = selected_id is not None and getattr(item, "id", None) == selected_id
            if selected:
                sc = _ops_status_color(status)
                name_markup = f"[b {sc}]{name_plain}[/]" if sc else f"[b]{name_plain}[/]"
            else:
                name_markup = name_plain
            prefix = "▸ " if selected else "  "
            last_run = getattr(item, "last_run", None)
            lastrun = _ops_ago(last_run) if isinstance(last_run, (int, float)) else ""
            ref = getattr(item, "source_ref", None)
            sched = str(getattr(ref, "schedule", "") or "") if (ref is not None and not isinstance(ref, dict)) else ""
            if status == "running":
                timing = "running…"
            elif sched:
                timing = sched
            elif status == "scheduled":
                timing = "scheduled"
            else:
                timing = ""
            row = (prefix + glyph + " "
                   + _pad_markup(mark_plain, mark_markup, 3) + " "
                   + _pad_markup(name_plain, name_markup, 26) + " "
                   + lastrun.ljust(9) + " "
                   + escape(timing))
            lines.append(row.rstrip())
        except Exception:
            continue
    return "\n".join(lines) if lines else "(no ops)"


def format_ops_detail(item) -> str:
    """Detail pane for the Ops tab: the selected OpsItem's fields + a source summary. None → placeholder.
    Pure, markup-safe (data is escaped), never raises."""
    if item is None:
        return "(select an item)"
    try:
        kind = escape(str(getattr(item, "kind", "") or ""))
        title = escape(str(getattr(item, "title", "") or ""))
        status = getattr(item, "status", "") or ""
        sc = _ops_status_color(status)
        status_r = f"[{sc}]{status}[/]" if sc else escape(str(status))
        last_run = getattr(item, "last_run", None)
        lr = (_ops_ago(last_run) or "never") if isinstance(last_run, (int, float)) else "never"
        detail = escape(str(getattr(item, "detail", "") or ""))
        lines = [f"kind: {kind}", f"title: {title}", f"status: {status_r}", f"last run: {lr}"]
        ref = getattr(item, "source_ref", None)
        if isinstance(ref, dict):                        # dispatch → show the brief
            brief = escape(str(ref.get("brief", "") or "")[:120])
            if brief:
                lines.append(f"brief: {brief}")
        elif ref is not None:                            # Job → schedule / next-run / last-exit / command
            for label, attr in (("schedule", "schedule"), ("next run", "next_run")):
                v = getattr(ref, attr, "")
                if v:
                    lines.append(f"{label}: {escape(str(v))}")
            ls = getattr(ref, "last_status", "")
            if ls and str(ls) != "unknown":
                lines.append(f"last exit: {escape(str(ls))}")
            cmd = getattr(ref, "command", "")
            if cmd:
                lines.append(f"command: {escape(str(cmd)[:120])}")
        if detail:
            lines.append(detail)
        src = _ops_source(ref)
        if src:
            lines.append(f"source: {escape(src)}")
        return "\n".join(lines)
    except Exception:
        return "(item unavailable)"


def format_ops_summary(ops) -> str:
    """One-line counts bar for the Ops tab header — total + by-status + by-kind, colored via anim.color."""
    try:
        if not ops:
            return "[dim]no ops[/]"
        n = len(ops)
        def c(st):
            return sum(1 for o in ops if getattr(o, "status", "") == st)
        def k(kd):
            return sum(1 for o in ops if getattr(o, "kind", "") == kd)
        parts = [
            f"[b]{n}[/b] ops",
            f"[{anim.color('running', 'yellow')}]▶ {c('running')} running[/]",
            f"[{anim.color('schedule', 'cyan')}]~ {c('scheduled')} scheduled[/]",
            f"[{anim.color('ok', 'green')}]{c('ok')} ok[/]",
        ]
        if c("fail"):
            parts.append(f"[{anim.color('fail', 'red')}]{c('fail')} fail[/]")
        parts.append(f"[dim]{k('loop')} loops · {k('dispatch')} dispatch[/]")
        return "   ".join(parts)
    except Exception:
        return "[dim]ops[/]"


def _ops_dur(started):
    """Elapsed since an epoch start, compact ('45s' / '2m' / '1h'); '' if not a usable number."""
    try:
        delta = time.time() - float(started)
    except (TypeError, ValueError):
        return ""
    if delta < 60:
        return f"{int(delta)}s"
    if delta < 3600:
        return f"{int(delta // 60)}m"
    return f"{int(delta // 3600)}h"


def format_cloud_legs(cloud, frame=None) -> str:
    """The ☁ CLOUD sub-section of the MODELS panel: which cloud legs (codex/grok/kimi) are running a fleet
    dispatch right now + what they're doing + how long. Pure, markup-safe, never raises."""
    if not cloud:
        return "[dim]☁ cloud: none active[/]"
    lines = ["[b]☁ CLOUD[/b]"]
    for c in cloud:
        try:
            c = c or {}
            name = escape(str(c.get("name", "?") or "?"))
            act = escape(str(c.get("activity", "") or "")[:60])
            dur = _ops_dur(c.get("started"))
            lead = f"[cyan]{anim.spin(frame)}[/] " if frame is not None else "[cyan]☁[/] "
            body = f" · {act}" if act else ""
            tail = f" · [dim]{dur}[/]" if dur else ""
            lines.append(f"{lead}[cyan]{name}[/]{body}{tail}")
        except Exception:
            continue
    return "\n".join(lines)


def format_models(states: list, frame=None) -> str:
    """KANBAN: models by state across 3 columns — IN-FLIGHT (busy) | LOADED (warm) | IDLE (on disk).
    `frame` (int) pulses the IN-FLIGHT markers so busy models visibly 'work'; frame=None = static."""
    if not states:
        return "(no models)"
    flight = [s for s in states if s.loaded and s.busy]
    loaded = [s for s in states if s.loaded and not s.busy]
    idle = [s for s in states if not s.loaded]

    def cell(mp, mm, s, extra=""):
        name = _clean_model_name(s.name)
        plain = f"{mp} {name} {s.gb}GB{extra}"
        markup = f"{mm} {_color_model(s.name)} {s.gb}GB{extra}"
        return (plain, markup)

    if frame is not None:
        g = anim.spin(frame)
        ifc = anim.color("in_flight", "yellow")
        flight_cells = [cell(g, anim.glow(g, ifc, frame), s) for s in flight] or [("—", "—")]
    else:
        flight_cells = [cell("●", "●", s) for s in flight] or [("—", "—")]
    loaded_cells = [cell("◍", "◍", s, f" ({s.idle_in})" if s.idle_in else "") for s in loaded] or [("—", "—")]
    idle_cells = [(f"○ {len(idle)} on disk",) * 2] if idle else [("—", "—")]

    cols = [(f"▶ IN-FLIGHT ({len(flight)})", flight_cells),
            (f"◍ LOADED ({len(loaded)})", loaded_cells),
            (f"○ IDLE ({len(idle)})", idle_cells)]
    W = 30
    n = max(len(c[1]) for c in cols)
    out = ["".join(f"[b]{h}[/b]" + " " * (W - len(h)) for h, _ in cols)]   # header: bold, pad on plain len
    for i in range(n):
        row = "".join(_pad_markup(c[1][i][0], c[1][i][1], W) if i < len(c[1]) else " " * W for c in cols)
        out.append(row.rstrip())
    return "\n".join(out)


def format_coding(dispatches: list, models: list, util: int, frame=None) -> str:
    """The coding/activity tab — what the fleet is DOING right now: GPU util, active dispatches,
    loaded/busy models (family-colored), and recent completions. `frame` (int) animates the LIVE bits
    (computing util, running dispatches, in-flight models = pulsing spinners); frame=None = static."""
    ucolor = "red" if util >= 80 else ("yellow" if util >= 20 else "green")
    if util >= 20:
        tag = anim.active("computing", anim.color("computing", ucolor), frame) if frame is not None else "[yellow](computing)[/]"
    else:
        tag = "[green](idle)[/]"
    lines = [f"GPU utilization: [{ucolor}]{util}%[/]   {tag}", ""]

    running = [d for d in dispatches if d.get("running")]
    lines.append("[b]● ACTIVE DISPATCHES[/b]")
    if running:
        dc = anim.color("dispatching", "yellow")
        for d in running:
            mark = anim.glow(anim.spin(frame), dc, frame) if frame is not None else "[yellow]▶[/]"
            lines.append(f"  {mark} {d.get('leg','?')} · {(d.get('brief') or '')[:90]}")
    else:
        lines.append("  (none running — press d to dispatch)")
    lines.append("")

    loaded = [m for m in models if m.loaded]
    lines.append("[b]● MODELS IN VRAM[/b]")
    if loaded:
        for m in loaded:
            if m.busy:
                state = anim.active("in-flight", anim.color("in_flight", "yellow"), frame) if frame is not None else "[yellow]▶ in-flight[/]"
            else:
                state = "[green]◍ warm[/]    "
            tail = f"  idle in {m.idle_in}" if m.idle_in else ""
            lines.append(f"  {state}  {_color_model(m.name)} {m.gb}GB{tail}")
    else:
        lines.append("  (none loaded — GPU idle)")
    lines.append("")

    done = [d for d in dispatches if not d.get("running")][:5]
    if done:
        lines.append("[b]● RECENT DISPATCHES[/b]")
        for d in done:
            lines.append(f"  [{anim.color('ok', 'green')}]✓[/] [{d.get('when','')}] {d.get('leg','?')} · {(d.get('brief') or '')[:70]}")
    return "\n".join(lines)


def format_focus(state: FocusState) -> str:
    """Format a focus state into a display string."""
    if state.on:
        if state.since:
            return f"FOCUS ON - scope={state.scope} since {state.since}"
        else:
            return f"FOCUS ON - scope={state.scope}"
    else:
        return "focus: off"


def _human_checked(ts: str) -> str:
    """Display-only: 'never checked' or a relative 'checked Nm/h/d ago'. Never mutates the stored value;
    any parse issue degrades to a muted 'checked recently' rather than raising."""
    if not ts:
        return "never checked"
    try:
        t = datetime.datetime.fromisoformat(ts)
        if t.tzinfo is None:
            t = t.replace(tzinfo=datetime.timezone.utc)
        secs = (datetime.datetime.now(datetime.timezone.utc) - t).total_seconds()
        if secs < 90:
            return "checked just now"
        if secs < 5400:
            return f"checked {int(secs // 60)}m ago"
        if secs < 129600:
            return f"checked {int(secs // 3600)}h ago"
        return f"checked {int(secs // 86400)}d ago"
    except (ValueError, TypeError):
        return "checked recently"


def format_research_playlists(playlists, frame=None) -> str:
    """Render the Research Playlists panel: one clickable row per playlist (▶ check = ask Claude to check
    it for new videos + stage them). Pure — no I/O, no state. Names are escaped (Rich markup safety)."""
    if not playlists:
        return "[dim]No research playlists configured (~/.fleet_tui/research_playlists.json).[/]"
    lines = []
    for p in playlists:
        lines.append(f"[cyan]▶ check[/]  [b]{escape(p.name)}[/]   [dim]{_human_checked(p.last_checked)}[/]")
    lines.append("[dim]click a playlist → Claude checks it for new videos + stages them for the research team[/]")
    return "\n".join(lines)


def _receipt_size(size):
    """Receipt size with the established KB/MB bands; zero is an explicit empty result."""
    try:
        size = max(0, int(size))
    except (TypeError, ValueError):
        size = 0
    if size >= 1024 * 1024:
        return f"[dodgerblue]{size / (1024 * 1024):.1f}MB[/]"
    kb = size / 1024
    color = "green" if kb <= 2 else ("yellow" if kb <= 9 else ("coral" if kb <= 20 else "hotpink"))
    return f"[{color}]{kb:.1f}KB[/]"


def _receipt_row(row, width=58):
    """One receipt with model reserved before filename and a right-flush size/date tail."""
    name = escape(str(getattr(row, "name", "?") or "?"))
    model = escape(str(getattr(row, "model", "") or ""))
    ts = escape(str(getattr(row, "ts", "") or "")[:10])
    status = str(getattr(row, "status", "unknown") or "unknown")
    color = {"ok": "green", "empty": "yellow", "failed": "red"}.get(status, "gray")
    model_cell = f"[cyan]{model}[/] " if model else ""
    tail = f" {_receipt_size(getattr(row, 'bytes', 0))} {ts}".rstrip()
    plain_tail = re.sub(r"\[/?[^\]]+\]", "", tail)
    budget = max(8, width - len(re.sub(r"\[/?[^\]]+\]", "", model_cell)) - len(plain_tail) - 1)
    if len(name) > budget:
        name = name[: max(1, budget - 1)] + "…"
    body = f"{model_cell}[{color}]{name}[/]"
    padding = max(1, width - len(re.sub(r"\[/?[^\]]+\]", "", body)) - len(plain_tail))
    return body + " " * padding + tail


def format_receipt_grid(rows, boxes, width=58):
    """Render the first two configured boxes side-by-side; a single box has no phantom right column."""
    boxes = list(boxes or [])
    if not boxes:
        return "[dim]receipts: n/a[/]"
    columns = []
    for box in boxes[:2]:
        records = [row for row in (rows or []) if getattr(row, "box", "local") == box.name]
        columns.append([f"[b]{escape(box.name)} RECEIPTS[/b]"] + [_receipt_row(row, width) for row in records[:6]] or ["[dim]n/a[/]"])
    if len(columns) == 1:
        return "\n".join(columns[0])
    left, right = columns
    out = []
    for index in range(max(len(left), len(right))):
        a = left[index] if index < len(left) else ""
        b = right[index] if index < len(right) else ""
        av = len(re.sub(r"\[/?[^\]]+\]", "", a))
        out.append(a + " " * max(1, width - av) + " │ " + b)
    return "\n".join(out)


def format_box_models(boxes, models_by_box, throughput=None):
    """Only serving rows receive a rate; labels and colors originate in boxes.json."""
    lines = []
    rates = throughput or {}
    for box in boxes or []:
        rows = list((models_by_box or {}).get(box.name, []) or [])
        lines.append(f"[b]{escape(box.name)} MODELS[/b]")
        serving = [row for row in rows if getattr(row, "loaded", False) or getattr(row, "state", "") in {"asleep", "down"}]
        if not serving:
            lines.append("[dim]n/a[/]")
            continue
        for row in serving:
            device = str(getattr(row, "device", "") or "")
            label = getattr(box, "device_labels", {}).get(device)
            badge = f" [{label.color}]{label.badge}[/]" if label and label.badge and label.color else ""
            state = str(getattr(row, "state", "") or "")
            if state == "busy":
                mark, state_text = "[yellow]▶[/]", "busy"
            elif state == "asleep":
                mark, state_text = "[dim]◌[/]", "asleep"
            elif state == "down":
                mark, state_text = "[red]×[/]", "down"
            else:
                mark, state_text = "◍", "idle"
            rate = (rates.get(box.name, {}) or {}).get(getattr(row, "name", ""))
            serving = (not getattr(row, "port", 0) and getattr(row, "loaded", False)) or state == "busy"
            rate_text = f" [dim]{rate.tok_s:.1f} tok/s[/]" if serving and rate and rate.tok_s > 0 else ""
            wake = " [dim]wake on use[/]" if getattr(row, "wake_on_use", False) else ""
            port = f" [dim]:{row.port}[/]" if getattr(row, "port", 0) else ""
            lines.append(f"{mark} {_color_model(getattr(row, 'name', '?'))}{badge}{port} [dim]{state_text}[/]{rate_text}{wake}")
    return "\n".join(lines) if lines else "[dim]models: n/a[/]"


def format_lanes(lanes, boxes):
    """Union lane rows with each box's admits and a compact +N initial attribution."""
    lines = ["[b]LANES[/b]"]
    for lane in lanes or []:
        cells = []
        for box in boxes or []:
            count = int((getattr(lane, "admits_by_box", {}) or {}).get(box.name, 0) or 0)
            suffix = f" +{count}{box.name[:1].upper()}" if count else ""
            cells.append(f"{box.name}:{count}{suffix}")
        lines.append(f"{escape(str(getattr(lane, 'lane', '?')))} {getattr(lane, 'live', 0)}  " + "  ".join(cells))
    return "\n".join(lines) if len(lines) > 1 else "[b]LANES[/b]\n[dim]n/a[/]"


def format_downloads(rows):
    """Acquisition rows retain their explicit box attribution; no path-based host guess is made."""
    if not rows:
        return "[dim]downloads: n/a[/]"
    return "\n".join(f"[gray]{escape(str(row.file))}[/] [cyan]{escape(str(row.agent or '?'))}[/] [gold]{escape(str(row.box))}[/]" for row in rows)
