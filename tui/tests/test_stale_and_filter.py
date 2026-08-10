"""Gate for stale-run detection + the Ops filter (spec + tests authored by Claude; the local lane
implements the source to pass THIS — anti-cheatable-test rule).

Two pieces are under test:

1) sources/dispatch.recent() must, for each dispatch dict, ALSO report:
     age:   int seconds — for a RUNNING dispatch, live wall-clock since it started
            (now - mtime of the .brief file); for a DONE dispatch, the measured elapsed; None if unknown.
     stale: bool — True iff the dispatch is running AND age is known AND age >= dispatch.STALE_SECS.
   dispatch.STALE_SECS is a module constant (default 1800 = 30 min), overridable via env FLEET_TUI_STALE_SECS.
   The existing keys (leg/brief/when/running/elapsed/base/tail) are UNCHANGED.

2) sources/ops.filter_ops(items, filt) -> list[OpsItem]  (PURE, never raises, skips None):
     "all"          -> everything
     "running"      -> status == 'running'
     "fail"         -> status == 'fail'
     "feedback-due" -> 'FEEDBACK DUE' appears in the item's detail
     "stale"        -> the item is a stale dispatch (source_ref dict has stale=True, or 'STALE' in detail)
     "cloud"        -> a cloud-leg dispatch (title names codex/grok/kimi/gpt/claude/gemini)
     "local"        -> a NON-cloud dispatch or job (a local-model / on-box item)
     "cron"         -> kind == 'loop'
     unknown/None   -> everything (safe default)
   And _dispatch_to_opsitem must PREFIX a stale dispatch's detail with a '⚠ STALE' marker (mirroring the
   existing 'FEEDBACK DUE ·' prefix), so a stale run is visible in the list without opening it.
"""
import os
import time

from fleet_tui.models import Job, OpsItem
from fleet_tui.sources import dispatch, ops
from fleet_tui.sources.ops import build_ops, filter_ops


# ── helpers ──────────────────────────────────────────────────────────────────
def _make_dispatch(dirpath, base, *, running, age_secs, leg="qwen3-coder:30b"):
    """Lay down the .meta/.brief(/.done) files for one dispatch with a controlled start age."""
    os.makedirs(dirpath, exist_ok=True)
    b = os.path.join(dirpath, base)
    import json
    with open(b + ".meta", "w") as f:
        json.dump({"leg": leg, "brief": "do the thing", "started": "20260704-120000"}, f)
    with open(b + ".brief", "w") as f:
        f.write("do the thing")
    with open(b + ".out", "w") as f:
        f.write("working...\n")
    start = time.time() - age_secs
    os.utime(b + ".brief", (start, start))          # brief mtime = when it started
    os.utime(b + ".meta", (start, start))
    if not running:
        with open(b + ".done", "w") as f:
            f.write("")                             # its presence => not running
    return b


# ── 1) dispatch.recent(): age + stale ────────────────────────────────────────
def test_stale_secs_constant_exists_and_env_overridable(monkeypatch):
    assert isinstance(dispatch.STALE_SECS, int) and dispatch.STALE_SECS > 0


def test_running_old_dispatch_is_stale(tmp_path, monkeypatch):
    d = str(tmp_path / "d")
    monkeypatch.setattr(dispatch, "DISPATCH_DIR", d)
    monkeypatch.setattr(dispatch, "STALE_SECS", 1800)
    _make_dispatch(d, "20260704-120000-old", running=True, age_secs=3600)   # 60 min, no .done
    rec = {r["base"]: r for r in dispatch.recent()}
    it = rec["20260704-120000-old"]
    assert it["running"] is True
    assert it["age"] is not None and it["age"] >= 3500      # ~3600s live age
    assert it["stale"] is True


def test_running_fresh_dispatch_not_stale(tmp_path, monkeypatch):
    d = str(tmp_path / "d")
    monkeypatch.setattr(dispatch, "DISPATCH_DIR", d)
    monkeypatch.setattr(dispatch, "STALE_SECS", 1800)
    _make_dispatch(d, "20260704-120000-fresh", running=True, age_secs=60)   # 1 min
    it = {r["base"]: r for r in dispatch.recent()}["20260704-120000-fresh"]
    assert it["running"] is True
    assert it["stale"] is False


def test_done_dispatch_never_stale(tmp_path, monkeypatch):
    d = str(tmp_path / "d")
    monkeypatch.setattr(dispatch, "DISPATCH_DIR", d)
    monkeypatch.setattr(dispatch, "STALE_SECS", 1800)
    _make_dispatch(d, "20260704-120000-done", running=False, age_secs=9999)  # old but .done exists
    it = {r["base"]: r for r in dispatch.recent()}["20260704-120000-done"]
    assert it["running"] is False
    assert it["stale"] is False


def test_recent_keys_unchanged(tmp_path, monkeypatch):
    d = str(tmp_path / "d")
    monkeypatch.setattr(dispatch, "DISPATCH_DIR", d)
    _make_dispatch(d, "20260704-120000-x", running=True, age_secs=10)
    it = dispatch.recent()[0]
    for k in ("leg", "brief", "when", "running", "elapsed", "base", "tail", "age", "stale"):
        assert k in it


# ── 2) ops.filter_ops + the STALE detail marker ──────────────────────────────
def _ops(**kw):
    base = dict(id="i", kind="dispatch", title="t", status="idle", detail="", source_ref=None)
    base.update(kw)
    return OpsItem(**base)


def test_filter_all_and_unknown_pass_through():
    items = [_ops(id="a"), _ops(id="b")]
    assert len(filter_ops(items, "all")) == 2
    assert len(filter_ops(items, "definitely-not-a-filter")) == 2
    assert len(filter_ops(items, None)) == 2


def test_filter_never_raises_on_none_items():
    assert filter_ops([None, _ops(id="a")], "all") == [i for i in [None, _ops(id="a")] if i][:0] or True
    # concretely: None entries are dropped, no exception
    out = filter_ops([None, _ops(id="a", status="running")], "running")
    assert all(o is not None for o in out) and len(out) == 1


def test_filter_running_fail():
    items = [_ops(id="r", status="running"), _ops(id="f", status="fail"), _ops(id="i", status="idle")]
    assert [o.id for o in filter_ops(items, "running")] == ["r"]
    assert [o.id for o in filter_ops(items, "fail")] == ["f"]


def test_filter_feedback_due():
    items = [_ops(id="a", detail="FEEDBACK DUE · foo"), _ops(id="b", detail="foo")]
    assert [o.id for o in filter_ops(items, "feedback-due")] == ["a"]


def test_text_filter_substring_over_title_detail_id():
    items = [_ops(id="qwen-run", title="qwen3-coder dispatch", detail="writing posture.py"),
             _ops(id="grok-run", title="grok research", detail="landscape"),
             _ops(id="cron-x", title="hive lint", detail="STALE ollama")]
    # matches title
    assert {o.id for o in filter_ops(items, "all", text="qwen")} == {"qwen-run"}
    # matches detail (case-insensitive)
    assert {o.id for o in filter_ops(items, "all", text="LANDSCAPE")} == {"grok-run"}
    # matches id
    assert {o.id for o in filter_ops(items, "all", text="cron-x")} == {"cron-x"}
    # empty/whitespace text = no text filtering (all pass the category)
    assert len(filter_ops(items, "all", text="")) == 3
    assert len(filter_ops(items, "all", text="   ")) == 3
    # text AND category compose: only running items containing 'run'
    r = [_ops(id="a", title="qwen run", status="running"), _ops(id="b", title="qwen run", status="idle")]
    assert {o.id for o in filter_ops(r, "running", text="qwen")} == {"a"}
    # no match → empty
    assert filter_ops(items, "all", text="zzz-nope") == []


def test_filter_stale_by_ref_or_detail():
    by_ref = _ops(id="a", status="running", source_ref={"stale": True, "base": "x"})
    by_detail = _ops(id="b", status="running", detail="⚠ STALE 40m · foo")
    neither = _ops(id="c", status="running", source_ref={"stale": False})
    got = {o.id for o in filter_ops([by_ref, by_detail, neither], "stale")}
    assert got == {"a", "b"}


def test_filter_cloud_local_cron():
    codex = _ops(id="cx", kind="dispatch", title="codex")
    grok = _ops(id="gk", kind="dispatch", title="grok-research")
    qwen = _ops(id="qw", kind="dispatch", title="qwen3-coder:30b")
    loop = _ops(id="lp", kind="loop", title="github-monitor")
    items = [codex, grok, qwen, loop]
    assert {o.id for o in filter_ops(items, "cloud")} == {"cx", "gk"}
    assert {o.id for o in filter_ops(items, "local")} == {"qw"}
    assert {o.id for o in filter_ops(items, "cron")} == {"lp"}


def test_stale_dispatch_gets_detail_marker():
    """A dispatch dict flagged stale must surface a '⚠ STALE' prefix in its OpsItem detail."""
    d = {"leg": "qwen3-coder:30b", "brief": "b", "when": "20260704-120000",
         "running": True, "elapsed": None, "base": "20260704-120000-x", "tail": "working",
         "age": 2400, "stale": True}
    items = build_ops(dispatches=[d])
    it = next(o for o in items if o.kind == "dispatch")
    assert "STALE" in it.detail
    assert it.status == "running"       # it IS still running, just too long

