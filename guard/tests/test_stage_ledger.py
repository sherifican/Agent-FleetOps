"""Claude-authored gate for guard/stage_ledger.py. The implementer must NOT edit this file.

The headline case is reproduced literally: a queue that processed 13 of 25 items and reported success.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from guard.stage_ledger import Ledger, StageRecord, check_file, main  # noqa: E402

STAGES = ["stage", "dispatch", "reconcile", "splice"]


def ledger(tmp_path, run="r1", stages=None):
    return Ledger(str(tmp_path / "ledger.jsonl"), run, stages or STAGES)


def test_record_returns_a_stagerecord_that_closes():
    r = StageRecord("dispatch", 25, 20, {"leg-failed": 5}, "r1")
    assert r.accounted == 25 and r.closes


def test_dropped_counts_toward_accounting():
    r = StageRecord("dispatch", 10, 7, {"timeout": 2, "poison": 1}, "r1")
    assert r.accounted == 10 and r.closes


def test_unexplained_loss_does_not_close():
    assert not StageRecord("dispatch", 25, 13, {}, "r1").closes


# --- the real incident --------------------------------------------------------------------------------

def test_the_thirteen_of_twentyfive_incident_is_caught(tmp_path):
    """A queue printed DONE with every item rc=0 having processed 13 of 25. Only the COUNT betrayed it."""
    lg = ledger(tmp_path)
    for s, i, o in (("stage", 25, 25), ("dispatch", 25, 13), ("reconcile", 13, 13), ("splice", 13, 13)):
        lg.record(s, i, o)
    violations, unmeasured = lg.check()
    assert violations, "a stage that lost 12 items must not pass"
    joined = " ".join(violations)
    assert "dispatch" in joined and "12" in joined, f"must name the stage and the unexplained count: {violations}"


def test_a_fully_closing_run_is_clean(tmp_path):
    lg = ledger(tmp_path)
    lg.record("stage", 25, 25)
    lg.record("dispatch", 25, 22, {"leg-failed": 3})
    lg.record("reconcile", 22, 22)
    lg.record("splice", 22, 22)
    assert lg.check() == ([], [])


# --- chain closure ------------------------------------------------------------------------------------

def test_items_vanishing_between_stages_is_caught(tmp_path):
    """Each stage closes internally, yet items disappear in the gap. Per-stage closure cannot see this."""
    lg = ledger(tmp_path)
    lg.record("stage", 25, 25)
    lg.record("dispatch", 20, 20)  # 25 came out of stage, only 20 went in here
    violations, _ = lg.check()
    assert violations, "a break BETWEEN stages must be caught"
    joined = " ".join(violations)
    assert "stage" in joined and "dispatch" in joined


def test_chain_check_skips_stages_that_did_not_record(tmp_path):
    lg = ledger(tmp_path)
    lg.record("stage", 25, 25)
    lg.record("splice", 25, 25)  # dispatch + reconcile never ran
    violations, unmeasured = lg.check()
    assert len(unmeasured) == 2
    assert not violations, f"absent stages are UNMEASURED, not chain violations: {violations}"


# --- unmeasured: absence must be loud -----------------------------------------------------------------

def test_a_declared_stage_that_never_recorded_is_unmeasured(tmp_path):
    lg = ledger(tmp_path)
    lg.record("stage", 5, 5)
    violations, unmeasured = lg.check()
    assert len(unmeasured) == 3
    assert all("UNMEASURED" in u for u in unmeasured)
    for name in ("dispatch", "reconcile", "splice"):
        assert any(name in u for u in unmeasured)


def test_unmeasured_is_not_a_violation_and_not_a_pass(tmp_path):
    lg = ledger(tmp_path)
    lg.record("stage", 5, 5)
    violations, unmeasured = lg.check()
    assert unmeasured and not violations


# --- bad data is a verdict, never a crash --------------------------------------------------------------

def test_negative_count_is_a_violation(tmp_path):
    lg = ledger(tmp_path)
    lg.record("stage", 5, -1)
    violations, _ = lg.check()
    assert violations


def test_duplicate_stage_record_is_a_violation(tmp_path):
    lg = ledger(tmp_path)
    lg.record("dispatch", 25, 25)
    lg.record("dispatch", 25, 13)
    violations, _ = lg.check()
    assert violations, "a re-run must not silently overwrite a prior result"
    assert any("dispatch" in v for v in violations)


def test_malformed_line_is_a_violation_naming_the_line_and_processing_continues(tmp_path):
    p = tmp_path / "ledger.jsonl"
    good = json.dumps({"stage": "stage", "items_in": 5, "items_out": 5, "dropped": {},
                       "run_id": "r1", "seq": 0})
    bad = "{not json at all"
    good2 = json.dumps({"stage": "dispatch", "items_in": 5, "items_out": 1, "dropped": {},
                        "run_id": "r1", "seq": 1})
    p.write_text(f"{good}\n{bad}\n{good2}\n", encoding="utf-8")
    violations, _ = check_file(str(p), "r1", STAGES)
    assert any("2" in v for v in violations), "must name the offending line number"
    assert any("dispatch" in v for v in violations), "must keep going past the bad line"


def test_check_file_never_raises_on_garbage(tmp_path):
    p = tmp_path / "ledger.jsonl"
    p.write_text("\x00\x01\nnot json\n[]\n", encoding="utf-8")
    check_file(str(p), "r1", STAGES)


# --- storage: append-only ------------------------------------------------------------------------------

def test_records_are_appended_not_rewritten(tmp_path):
    path = str(tmp_path / "ledger.jsonl")
    Ledger(path, "r1", STAGES).record("stage", 1, 1)
    Ledger(path, "r2", STAGES).record("stage", 2, 2)
    lines = [l for l in open(path).read().splitlines() if l.strip()]
    assert len(lines) == 2, "a ledger you can rewrite is a ledger that can launder a bad run"


def test_other_runs_are_preserved_but_ignored(tmp_path):
    path = str(tmp_path / "ledger.jsonl")
    old = Ledger(path, "r1", STAGES)
    old.record("dispatch", 25, 13)  # a broken earlier run
    new = Ledger(path, "r2", STAGES)
    for s in STAGES:
        new.record(s, 5, 5)
    violations, unmeasured = new.check()
    assert not violations and not unmeasured, "r1's failure must not contaminate r2"
    assert "r1" in open(path).read(), "r1's rows must still be on disk"


def test_record_is_readable_by_another_reader_immediately(tmp_path):
    path = str(tmp_path / "ledger.jsonl")
    lg = Ledger(path, "r1", STAGES)
    lg.record("stage", 3, 3)
    assert "stage" in open(path).read(), "record() must flush before returning"


def test_seq_is_monotonic_and_clock_free(tmp_path):
    path = str(tmp_path / "ledger.jsonl")
    lg = Ledger(path, "r1", STAGES)
    for s in STAGES:
        lg.record(s, 1, 1)
    seqs = [json.loads(l)["seq"] for l in open(path).read().splitlines() if l.strip()]
    assert seqs == [0, 1, 2, 3]


def test_records_returns_this_run_in_write_order(tmp_path):
    lg = ledger(tmp_path)
    for s in ("stage", "dispatch"):
        lg.record(s, 2, 2)
    assert [r.stage for r in lg.records()] == ["stage", "dispatch"]


def test_module_uses_no_clock():
    import guard.stage_ledger as sl
    src = open(sl.__file__, encoding="utf-8").read()
    for banned in ("time.time(", "datetime.now(", "random."):
        assert banned not in src, f"{banned} breaks determinism and replay"


# --- exit codes ----------------------------------------------------------------------------------------

def write_run(tmp_path, rows, run="r1"):
    path = str(tmp_path / "ledger.jsonl")
    lg = Ledger(path, run, STAGES)
    for stage, i, o in rows:
        lg.record(stage, i, o)
    return path


def test_exit_zero_when_everything_closes(tmp_path, capsys):
    p = write_run(tmp_path, [(s, 5, 5) for s in STAGES])
    rc = main([p, "--run-id", "r1", "--stages", ",".join(STAGES)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "5" in out, "a clean run must print the numbers it checked, not just stay silent"


def test_exit_one_on_a_violation(tmp_path):
    p = write_run(tmp_path, [("stage", 25, 25), ("dispatch", 25, 13),
                             ("reconcile", 13, 13), ("splice", 13, 13)])
    assert main([p, "--run-id", "r1", "--stages", ",".join(STAGES)]) == 1


def test_exit_two_when_a_stage_is_unmeasured(tmp_path):
    p = write_run(tmp_path, [("stage", 5, 5)])
    assert main([p, "--run-id", "r1", "--stages", ",".join(STAGES)]) == 2


def test_unmeasured_dominates_a_violation(tmp_path, capsys):
    """Not knowing whether a stage ran can hide any number of violations, so it outranks them."""
    p = write_run(tmp_path, [("stage", 25, 25), ("dispatch", 25, 13)])
    rc = main([p, "--run-id", "r1", "--stages", ",".join(STAGES)])
    out = capsys.readouterr().out
    assert rc == 2
    assert "UNMEASURED" in out
    assert "dispatch" in out, "both conditions must still be reported"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
