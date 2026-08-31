"""Gate for guard/reconcile_gate.py — an all-agree ACT on an unverified shared premise
must be refused.

The reconcile fixture here is the plan's red case: every leg repeats one false source
premise, all verdicts agree, and the record asks to ACT. The gate must refuse until the
source premise is verified — however many legs agree. Red demo: mutation RG1 in
guard/mutation_harness.py waives the shared-premise verification and this gate must go
red.

Runs two ways: under pytest and standalone —
`python3 guard/tests/test_reconcile_gate.py` — printing the all-pass marker the mutation
harness anchors on.
"""
import contextlib
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from guard.reconcile_gate import gate, selftest  # noqa: E402

MARKER = "RECONCILE GATE HAS TEETH - ALL CHECKS PASSED"


def _record(verified, mechanism="checked the cited source directly", action="ACT"):
    return {"claims": [{
        "id": "c1", "action": action,
        "legs": [{"leg": "A", "verdict": "supported"},
                 {"leg": "B", "verdict": "supported"},
                 {"leg": "C", "verdict": "supported"}],
        "conclusion_verdict": "supported",
        "mechanism_verdict": mechanism,
        "premises": [{"id": "src-1", "shared": True, "verified": verified,
                      "verifier": "independent fetch" if verified else ""}]}]}


def test_unanimous_act_on_unverified_shared_premise_is_refused():
    rc, out = gate(_record(verified=False))
    assert rc == 1, ("three legs repeating one unverified source premise cleared ACT — "
                     "correlated error laundered as confidence")
    assert any("R1" in ln and "correlated" in ln for ln in out), out


def test_verified_shared_premise_clears_act():
    rc, out = gate(_record(verified=True))
    assert rc == 0, "a verified shared premise must clear: %r" % out


def test_act_without_mechanism_verdict_is_refused():
    rc, out = gate(_record(verified=True, mechanism=""))
    assert rc == 1, "the reason must be judged separately from the answer"
    assert any("R2" in ln for ln in out), out


def test_hold_is_not_blocked():
    rc, out = gate(_record(verified=False, action="HOLD"))
    assert rc == 0, "HOLD is already not acted on; blocking it is noise: %r" % out


def test_invalid_record_is_cannot_check():
    rc, out = gate({"claims": "not-a-list"})
    assert rc == 2, "an unreadable record must be loud, never green: %r" % out


def test_selftest_is_green():
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = selftest()
    assert rc == 0, "selftest red:\n" + buf.getvalue()


ALL = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    failures = 0
    for fn in ALL:
        try:
            fn()
            print("  ok    %s" % fn.__name__)
        except AssertionError as exc:
            failures += 1
            print("  FAIL  %s: %s" % (fn.__name__, exc))
    if failures:
        print("RESULT: %d check(s) RED" % failures)
        sys.exit(1)
    print(MARKER)
