"""Gate for guard/curation_gate.py — the acceptance test the plan names: it must reject
one-model approval, vote-before-verification, and disagreement without an operator
verdict.

Red demo: mutation CG1 in guard/mutation_harness.py lowers the independent-reviewer floor
to one and this gate must go red.

Runs two ways: under pytest and standalone —
`python3 guard/tests/test_curation_gate.py` — printing the all-pass marker the mutation
harness anchors on.
"""
import contextlib
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from guard.curation_gate import gate, selftest  # noqa: E402

MARKER = "CURATION GATE HAS TEETH - ALL CHECKS PASSED"


def _record(n_independent=2, verified=True, split=False, operator=None):
    votes = [{"reviewer": "model-%d" % i, "independent": i < n_independent,
              "verdict": "accept"} for i in range(max(n_independent, 2))]
    if split:
        votes.append({"reviewer": "model-x", "independent": True, "verdict": "reject"})
    return {"proposals": [{"id": "prop-1", "verified_before_vote": verified,
                           "votes": votes, "operator_verdict": operator}]}


def test_one_model_approval_is_rejected():
    rc, out = gate(_record(n_independent=1))
    assert rc == 1, "a single independent approver cleared a rule-base change"
    assert any("one-model approval" in ln for ln in out), out


def test_vote_before_verification_is_rejected():
    rc, out = gate(_record(verified=False))
    assert rc == 1, "a vote cast before the verifier ran was accepted"
    assert any("vote-before-verification" in ln for ln in out), out


def test_disagreement_without_operator_verdict_is_rejected():
    rc, out = gate(_record(split=True))
    assert rc == 1, "a split vote with no operator verdict was accepted"
    assert any("disagreement" in ln for ln in out), out


def test_split_with_operator_verdict_passes():
    rc, out = gate(_record(split=True, operator="operator kept the dissenting reading"))
    assert rc == 0, "a resolved split is the process working, not a defect: %r" % out


def test_well_formed_record_passes():
    rc, out = gate(_record())
    assert rc == 0, repr(out)


def test_invalid_record_is_cannot_check():
    rc, out = gate({})
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
