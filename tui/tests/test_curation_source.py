"""Claude-authored gate for sources/curation.py — pass-log parsing + the trigger control. No textual."""
import json
from fleet_tui.sources import curation


LEDGER = """\
## PASS 65 — 2026-07-06 — CHANGE (1 memory-update) — validate-at-shipping-level ADDENDUM 3
- CONTEXT: watcher fired at the B2 ship-gate result.
- APPLIED (1 gated): ADDENDUM 3 to feedback-validate-at-shipping-level — a swap's win can invert.
- HYGIENE: clean (126 files).

## PASS 59 — 2026-07-06T07:43:58 — NO-OP
- no activity: lines=+0 mem=N harvest=N

## PASS 66 — 2026-07-06 — CHANGE (2 memory-create + 4 revise) — Fable-5 handover capture
- CONTEXT: owner had 3 legs review two PDFs.
- APPLIED (2 NEW + 4 revise): NEW feedback-boundary-first-verification · NEW dependency-cascade.
- HYGIENE: clean (128 files).
"""


def _wire(tmp_path, monkeypatch, ledger=LEDGER, trigger=None):
    lg = tmp_path / "CURATION_LEDGER.md"; lg.write_text(ledger)
    tg = tmp_path / ".trigger"; tg.write_text(json.dumps(trigger if trigger is not None else {}))
    monkeypatch.setattr(curation, "LEDGER", str(lg))
    monkeypatch.setattr(curation, "TRIGGER", str(tg))
    return lg, tg


def test_recent_passes_sorted_by_date_not_file_order(tmp_path, monkeypatch):
    # ordering is by DATE (newest first), NOT file order — the ledger isn't chronological. Date-only
    # CHANGE passes (2026-07-06) normalize to end-of-day, so they sort above the same-day 07:43 NO-OP.
    _wire(tmp_path, monkeypatch)
    passes = curation.recent_passes()
    assert [p["pass_n"] for p in passes] == ["65", "66", "59"]
    p66 = next(p for p in passes if p["pass_n"] == "66")
    assert p66["kind"] == "CHANGE" and "Fable-5 handover" in p66["headline"]
    assert "APPLIED (2 NEW + 4 revise)" in p66["summary"]
    p59 = next(p for p in passes if p["pass_n"] == "59")
    assert p59["kind"] == "NO-OP" and "no activity" in p59["summary"]


def test_cross_day_ordering_newest_day_first(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch, ledger=(
        "## PASS 70 — 2026-07-06T10:00:00 — NO-OP\n- no activity: lines=+0\n\n"
        "## PASS 71 — 2026-07-08 — CHANGE (1) — newer day\n- APPLIED (1): x\n\n"
        "## PASS 72 — 2026-07-07T09:00:00 — NO-OP\n- no activity: lines=+0\n"))
    assert [p["pass_n"] for p in curation.recent_passes()] == ["71", "72", "70"]   # 07-08, 07-07, 07-06


def test_recent_passes_limit_and_empty(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch)
    assert len(curation.recent_passes(limit=2)) == 2
    _wire(tmp_path, monkeypatch, ledger="")
    assert curation.recent_passes() == []
    # missing file → [], never raises
    monkeypatch.setattr(curation, "LEDGER", str(tmp_path / "nope.md"))
    assert curation.recent_passes() == []


def test_trigger_status(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch, trigger={"pending": True, "pass_n": 66, "reasons": ["+1742 lines"]})
    st = curation.trigger_status()
    assert st["pending"] is True and st["pass_n"] == 66 and st["reasons"] == ["+1742 lines"]
    # corrupt/missing trigger → safe defaults
    monkeypatch.setattr(curation, "TRIGGER", str(tmp_path / "gone"))
    st2 = curation.trigger_status()
    assert st2["pending"] is False and st2["reasons"] == []


def test_queue_pass_flips_pending_true_preserving_json(tmp_path, monkeypatch):
    _, tg = _wire(tmp_path, monkeypatch, trigger={"pending": False, "pass_n": 66, "signal": {"x": 1}})
    assert curation.queue_pass() is True
    d = json.loads(tg.read_text())
    assert d["pending"] is True
    assert d["signal"] == {"x": 1}                 # preserved the rest of the trigger
    assert d["reasons"] == ["manual trigger from TUI"]
    assert curation.trigger_status()["pending"] is True
