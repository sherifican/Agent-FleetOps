#!/usr/bin/env python3
"""curation_gate.py — acceptance rules for a self-curation pass record.

A system that edits its own operating rules has one existential failure mode: silent
drift. Single-model approval is the thinnest possible gate over it — the approver shares
training, context, and blind spots with the proposer. This gate validates the PASS RECORD
of a curation cycle and rejects the three shapes that hollow the review out:

  one-model approval          — fewer than two INDEPENDENT reviewers voted;
  vote-before-verification    — the available verifier or dry-run did not run BEFORE the
                                vote (a vote on unverified material is theater);
  unresolved disagreement     — the votes split and no operator verdict is recorded.

Whether unanimous panel agreement may itself ACCEPT a change, or the panel only FILTERS —
rejecting or forwarding to an accountable human — is deployment policy
(specs/curation-loop-architecture.md). BOTH policies produce this same record, and this
gate rejects the record under either policy when it shows one of the three shapes above.

RECORD FORMAT (JSON):
  {"proposals": [{
      "id": str,
      "verified_before_vote": bool,
      "votes": [{"reviewer": str, "independent": bool, "verdict": "accept"|"reject"}, ...],
      "operator_verdict": str | null
  }, ...]}

Exit codes: 0 record acceptable · 1 rejected (each proposal named with its reason) ·
2 CANNOT CHECK (unreadable/invalid record).
Gate: guard/tests/test_curation_gate.py. Red demo: mutation CG1 lowers the independent-
reviewer floor to one and the gate must go red.
"""
import argparse
import json
import sys


def gate(record):
    """Returns (rc, findings). rc 0 acceptable · 1 rejected · 2 invalid record."""
    proposals = record.get("proposals")
    if not isinstance(proposals, list):
        return 2, ["CANNOT CHECK — record carries no proposals list"]
    findings = []
    for p in proposals:
        pid = p.get("id", "<unnamed>")
        votes = [v for v in p.get("votes", []) if isinstance(v, dict)]
        independent = [v for v in votes if v.get("independent")]
        if len(independent) < 2:
            findings.append("%s: one-model approval — %d independent reviewer(s) voted; "
                            "a rule-base change needs a panel, not an echo" % (pid, len(independent)))
        if not p.get("verified_before_vote"):
            findings.append("%s: vote-before-verification — the verifier/dry-run must run "
                            "BEFORE the vote; a vote on unverified material is theater" % pid)
        verdicts = {v.get("verdict") for v in votes}
        if len(verdicts) > 1 and not str(p.get("operator_verdict") or "").strip():
            findings.append("%s: disagreement without an operator verdict — dissent goes "
                            "to the operator, it is never averaged away" % pid)
    return (1 if findings else 0), findings


def _fixture(n_independent=2, verified=True, split=False, operator=None):
    votes = [{"reviewer": "model-%d" % i, "independent": i < n_independent,
              "verdict": "accept"} for i in range(max(n_independent, 2))]
    if split:
        votes.append({"reviewer": "model-x", "independent": True, "verdict": "reject"})
    return {"proposals": [{"id": "prop-1", "verified_before_vote": verified,
                           "votes": votes, "operator_verdict": operator}]}


def selftest():
    """Teeth: each red shape must be rejected; the well-formed record must pass."""
    failures = []
    rc, out = gate(_fixture(n_independent=1))
    if rc != 1 or not any("one-model approval" in o for o in out):
        failures.append("one-model approval must be rejected")
    rc, out = gate(_fixture(verified=False))
    if rc != 1 or not any("vote-before-verification" in o for o in out):
        failures.append("vote-before-verification must be rejected")
    rc, out = gate(_fixture(split=True))
    if rc != 1 or not any("disagreement" in o for o in out):
        failures.append("disagreement without an operator verdict must be rejected")
    rc, _ = gate(_fixture(split=True, operator="operator kept the dissenting reading"))
    if rc != 0:
        failures.append("a split WITH an operator verdict is the process working; it must pass")
    rc, _ = gate(_fixture())
    if rc != 0:
        failures.append("the well-formed record must pass")
    rc, _ = gate({"proposals": "no"})
    if rc != 2:
        failures.append("an invalid record must be CANNOT CHECK, never a pass")
    for f in failures:
        print("  FAIL  %s" % f)
    if failures:
        print("curation gate selftest: %d check(s) RED" % len(failures))
        return 1
    print("curation gate selftest: one-model approval, vote-before-verification, and "
          "unresolved disagreement all rejected; the well-formed record passes")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("record", nargs="?", help="curation pass record (JSON file)")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    if not args.record:
        print("CANNOT CHECK — no record given (or run --selftest)")
        return 2
    try:
        with open(args.record, encoding="utf-8") as fh:
            record = json.load(fh)
    except (OSError, ValueError) as exc:
        print("CANNOT CHECK — unreadable record: %s" % exc)
        return 2
    rc, lines = gate(record)
    for ln in lines:
        print(ln)
    if rc == 0:
        print("curation gate clean — panel present, verification preceded the vote, "
              "dissent resolved by the operator")
    return rc


if __name__ == "__main__":
    sys.exit(main())
