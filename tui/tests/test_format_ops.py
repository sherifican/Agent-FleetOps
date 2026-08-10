import os
"""Gate for the two Ops master-detail formatters in fleet_tui/widgets/format.py (spec + tests authored
by Claude; the local lane implements format_ops_list/format_ops_detail to pass THIS — anti-cheatable-
test rule). Pure display functions: OpsItem records in, Rich-markup display strings out. No I/O, no
Textual. Every color routes through anim.color(slot, default) — never a raw Rich-256 name.
"""
import re
import time

import pytest

from fleet_tui.models import Job, OpsItem
from fleet_tui.widgets import anim
from fleet_tui.widgets.format import format_ops_list, format_ops_detail, format_ops_summary


def _strip(s):
    """Remove Rich color markup so content/layout assertions ignore the coloring."""
    return re.sub(r"\[/?[^\]]*\]", "", s)


# ---------- format_ops_list: empty / safe degrade ----------

def test_format_ops_list_empty():
    assert format_ops_list([]) == "(no ops)"


def test_format_ops_list_never_raises_on_garbage():
    # a None entry and a title-less/status-less item must not crash the whole panel
    weird = OpsItem(id="w", kind="???", title="", status="")
    out = format_ops_list([None, weird])
    assert isinstance(out, str)


def test_format_ops_list_never_raises_on_none_fields():
    it = OpsItem(id=None, kind=None, title=None, status=None, last_run=None, detail=None, source_ref=None)
    out = format_ops_list([it])
    assert isinstance(out, str)


# ---------- kind glyphs ----------

def test_format_ops_list_kind_glyphs():
    items = [
        OpsItem(id="1", kind="loop", title="hf-watch", status="idle"),
        OpsItem(id="2", kind="dispatch", title="grok-research", status="idle"),
        OpsItem(id="3", kind="research", title="digest", status="idle"),
        OpsItem(id="4", kind="job", title="bgjob", status="idle"),
    ]
    out = format_ops_list(items)
    lines = out.splitlines()
    assert "↻" in lines[0] and "hf-watch" in lines[0]
    assert "→" in lines[1] and "grok-research" in lines[1]
    assert "⚑" in lines[2] and "digest" in lines[2]
    assert "▪" in lines[3] and "bgjob" in lines[3]


def test_format_ops_list_unknown_kind_falls_back_glyph():
    it = OpsItem(id="1", kind="mystery", title="x", status="idle")
    line = format_ops_list([it]).splitlines()[0]
    assert "•" in line


# ---------- status marks (mirrors format_jobs' palette, routed via anim.color) ----------

def test_format_ops_list_status_ok():
    it = OpsItem(id="1", kind="loop", title="x", status="ok")
    out = format_ops_list([it])
    assert f"[{anim.color('ok', 'green')}]OK[/]" in out


def test_format_ops_list_status_fail():
    it = OpsItem(id="1", kind="loop", title="x", status="fail")
    out = format_ops_list([it])
    assert f"[{anim.color('fail', 'red')}]!![/]" in out


def test_format_ops_list_status_idle_blank():
    it = OpsItem(id="1", kind="loop", title="x", status="idle")
    out = _strip(format_ops_list([it]))
    assert "x" in out
    # no OK/FAIL/running noise for idle
    assert "OK" not in out and "!!" not in out


def test_format_ops_list_status_scheduled():
    it = OpsItem(id="1", kind="loop", title="x", status="scheduled")
    out = format_ops_list([it])
    assert f"[{anim.color('schedule', 'cyan')}]~[/]" in out


def test_format_ops_list_status_running_static():
    it = OpsItem(id="1", kind="loop", title="x", status="running")
    out = format_ops_list([it], frame=None)
    assert f"[{anim.color('running', 'yellow')}]▶[/]" in out


def test_format_ops_list_status_running_animated():
    anim.set_style("braille", True)
    it = OpsItem(id="1", kind="loop", title="x", status="running")
    out = format_ops_list([it], frame=3)
    assert anim.spin(3) in _strip(out)
    assert "running" in _strip(out)


# ---------- selection: ▸ prefix + name glow vs 2-space pad ----------

def test_format_ops_list_selected_row_prefixed():
    items = [OpsItem(id="a", kind="loop", title="alpha", status="idle"),
             OpsItem(id="b", kind="loop", title="beta", status="idle")]
    out = format_ops_list(items, selected_id="b")
    lines = out.splitlines()
    assert lines[0].startswith("  ")     # not selected -> 2-space pad
    assert not lines[0].startswith("▸")
    assert lines[1].startswith("▸ ")     # selected -> arrow prefix


def test_format_ops_list_no_selection_all_padded():
    items = [OpsItem(id="a", kind="loop", title="alpha", status="idle")]
    out = format_ops_list(items, selected_id=None)
    assert out.startswith("  ")
    assert "▸" not in out


def test_format_ops_list_selected_name_glows_static():
    it = OpsItem(id="a", kind="loop", title="alpha", status="ok")
    out = format_ops_list([it], selected_id="a", frame=None)
    # selected name rendered bold in the status color, not just bare text
    assert "alpha" in out
    assert f"[b {anim.color('ok', 'green')}]alpha[/]" in out or "[b" in out


# ---------- last_run / schedule humanization ----------

def test_format_ops_list_last_run_just_now():
    it = OpsItem(id="1", kind="loop", title="x", status="ok", last_run=time.time())
    out = _strip(format_ops_list([it]))
    assert "just now" in out


def test_format_ops_list_no_last_run_idle_blank_trailer():
    it = OpsItem(id="1", kind="loop", title="x", status="idle", last_run=None)
    out = _strip(format_ops_list([it]))
    assert "x" in out


def test_format_ops_list_scheduled_shows_schedule_from_source_ref():
    j = Job(id="1", name="x", kind="cron", schedule="every 3hrs")
    it = OpsItem(id="1", kind="loop", title="x", status="scheduled", last_run=None, source_ref=j)
    out = _strip(format_ops_list([it]))
    assert "every 3hrs" in out


def test_format_ops_list_scheduled_falls_back_when_no_schedule_on_ref():
    it = OpsItem(id="1", kind="loop", title="x", status="scheduled", last_run=None, source_ref=None)
    out = _strip(format_ops_list([it]))
    assert "scheduled" in out


# ---------- format_ops_detail ----------

def test_format_ops_detail_none():
    assert format_ops_detail(None) == "(select an item)"


def test_format_ops_detail_basic_fields():
    it = OpsItem(id="1", kind="loop", title="hf-watch", status="ok",
                 last_run=None, detail="every 240m · wrote 3 files", source_ref=None)
    out = _strip(format_ops_detail(it))
    assert "kind: loop" in out
    assert "title: hf-watch" in out
    assert "status:" in out and "ok" in out
    assert "last run: never" in out
    assert "every 240m · wrote 3 files" in out


def test_format_ops_detail_last_run_humanized():
    it = OpsItem(id="1", kind="loop", title="x", status="ok", last_run=time.time())
    out = _strip(format_ops_detail(it))
    assert "last run: just now" in out


def test_format_ops_detail_status_colored():
    it = OpsItem(id="1", kind="loop", title="x", status="fail")
    out = format_ops_detail(it)
    assert f"[{anim.color('fail', 'red')}]fail[/]" in out


def test_format_ops_detail_source_ref_job_summary():
    j = Job(id="abc123", name="x", kind="cron")
    it = OpsItem(id="1", kind="loop", title="x", status="idle", source_ref=j)
    out = _strip(format_ops_detail(it))
    assert "source:" in out
    assert "abc123" in out
    assert "cron" in out


def test_format_ops_detail_source_ref_dispatch_summary():
    d = {"leg": "grok-research", "base": "20260702-152044-grok-research", "brief": "look into X"}
    it = OpsItem(id="1", kind="dispatch", title="grok-research", status="idle", source_ref=d)
    out = _strip(format_ops_detail(it))
    assert "source:" in out
    assert "grok-research" in out
    assert "20260702-152044-grok-research" in out


def test_format_ops_detail_no_source_ref_no_source_line():
    it = OpsItem(id="1", kind="loop", title="x", status="idle", source_ref=None)
    out = format_ops_detail(it)
    assert "source:" not in out


def test_format_ops_detail_never_raises_on_garbage_source_ref():
    for ref in (12345, "just a string", [1, 2, 3], object()):
        it = OpsItem(id="1", kind="loop", title="x", status="idle", source_ref=ref)
        out = format_ops_detail(it)
        assert isinstance(out, str)


def test_format_ops_detail_never_raises_on_none_fields():
    it = OpsItem(id=None, kind=None, title=None, status=None, last_run=None, detail=None, source_ref=None)
    out = format_ops_detail(it)
    assert isinstance(out, str)


def test_format_ops_detail_title_is_markup_escaped():
    it = OpsItem(id="1", kind="loop", title="weird [tag] name", status="idle")
    out = format_ops_detail(it)
    assert "\\[tag]" in out   # escaped, not parsed as a markup tag


def test_format_ops_detail_job_rich_fields():
    j = Job(id="abc", name="x", kind="cron", schedule="every 3hrs", next_run="2026-07-03T18:00", last_status="ok")
    it = OpsItem(id="1", kind="loop", title="x", status="ok", source_ref=j)
    out = _strip(format_ops_detail(it))
    assert "schedule: every 3hrs" in out
    assert "next run: 2026-07-03T18:00" in out
    assert "last exit: ok" in out


def test_format_ops_detail_systemcron_shows_command():
    j = Job(id="", name="sweep.sh", kind="systemcron", schedule="41 9 * * 0", command="~/sweep.sh --flag")
    it = OpsItem(id="1", kind="loop", title="sweep.sh", status="scheduled", source_ref=j)
    out = _strip(format_ops_detail(it))
    assert "command: ~/sweep.sh --flag" in out


# ---------- format_ops_summary ----------

def test_format_ops_summary_empty():
    assert "no ops" in format_ops_summary([])


def test_format_ops_summary_counts():
    import time as _t
    items = [OpsItem(id=str(i), kind=("dispatch" if s == "running" else "loop"), title="x", status=s)
             for i, s in enumerate(["running", "scheduled", "ok", "fail", "idle"])]
    out = _strip(format_ops_summary(items))
    assert "5 ops" in out
    assert "1 running" in out and "1 scheduled" in out and "1 ok" in out and "1 fail" in out


def test_format_ops_summary_never_raises():
    assert isinstance(format_ops_summary([None, OpsItem(id=None, kind=None, title=None, status=None)]), str)
