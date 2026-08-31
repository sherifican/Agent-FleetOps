#!/usr/bin/env python3
"""curation_gate.py — acceptance rules for a self-curation pass record.

A system that edits its own operating rules has one existential failure mode: silent
drift. Single-model approval is the thinnest possible gate over it — the approver shares
training, context, and blind spots with the proposer. This gate validates the PASS RECORD
of a curation cycle and rejects the three shapes that hollow the review out:

  one-model approval          — fewer than two DISTINCT independent reviewers voted;
  vote-before-verification    — the available verifier or dry-run did not run BEFORE the
                                vote (a vote on unverified material is theater);
  unresolved disagreement     — the votes split and no operator verdict is recorded.

Whether unanimous panel agreement may itself ACCEPT a change, or the panel only FILTERS —
rejecting or forwarding to an accountable human — is deployment policy
(specs/curation-loop-architecture.md). BOTH policies produce this same record, and this
gate rejects the record under either policy when it shows one of the three shapes above.

THIS GATE IS NOT HONOR-SYSTEM, AND OMISSION IS NOT A BYPASS
    Measured on the first draft, all returning "clean": two votes carrying the SAME
    `reviewer` id with `independent: true` counted as a panel, so one model echoing itself
    passed the independence floor; `"independent": "false"` and
    `"verified_before_vote": "false"` passed every check, because a quoted "false" is a
    non-empty string and reads as TRUE; and a malformed element crashed with
    AttributeError where the contract advertises exit 2. So the record is VALIDATED before
    it is judged: absent, mistyped, or out-of-vocabulary fields make it UNJUDGEABLE
    (exit 2, which dominates a rejection), booleans must be JSON booleans, and the
    independence floor counts DISTINCT reviewer identities.

RECORD FORMAT (JSON) — types and allowed values are enforced:
  {"proposals": [{
      "id": str,
      "verified_before_vote": bool,                          (required, JSON boolean)
      "votes": [{"reviewer": str,                            (required, non-empty)
                 "independent": bool,                        (required, JSON boolean)
                 "verdict": "accept" | "reject"}, ...],      (required, non-empty list)
      "operator_verdict": str | null
  }, ...]}

Exit codes: 0 record acceptable · 1 rejected (each proposal named with its reason) ·
2 CANNOT CHECK (unreadable, or a record that cannot be judged).
Gate: guard/tests/test_curation_gate.py. Red demos: CG1 lowers the independent-reviewer
floor to one; CG2 stops counting DISTINCT identities so an echo passes; CG3 accepts a
stringly boolean.
"""
import argparse
import json
import sys

VOTE_VERDICTS = ("accept", "reject")


def _bool_field(obj, key, where, problems):
    """A JSON boolean, or None with a problem recorded. Never coerced.

    `"false"` is a five-character string. Every truthiness test in Python passes it, so
    coercing here would turn an explicit negative into a positive."""
    if key not in obj:
        problems.append("%s: required boolean field %r is absent — a contract field that "
                        "can be omitted is not a contract" % (where, key))
        return None
    value = obj[key]
    if not isinstance(value, bool):
        problems.append("%s: %r must be a JSON boolean (true/false), got %r — a quoted "
                        "\"false\" is a non-empty string and reads as TRUE"
                        % (where, key, value))
        return None
    return value


def validate(record):
    """Structural problems that make the record unjudgeable. Empty list = judgeable."""
    problems = []
    if not isinstance(record, dict):
        return ["CANNOT CHECK — the record is not an object"]
    proposals = record.get("proposals")
    if not isinstance(proposals, list):
        return ["CANNOT CHECK — record carries no proposals list"]
    for i, p in enumerate(proposals):
        where = "proposal[%d]" % i
        if not isinstance(p, dict):
            problems.append("%s: not an object (%r) — a malformed element must be "
                            "reported, never raised" % (where, p))
            continue
        where = "proposal %r" % p.get("id", "<unnamed>")
        _bool_field(p, "verified_before_vote", where, problems)
        votes = p.get("votes")
        if not isinstance(votes, list) or not votes:
            problems.append("%s: votes must be a non-empty list — a proposal with no "
                            "recorded votes is not a reviewed proposal" % where)
            continue
        for j, v in enumerate(votes):
            vw = "%s vote[%d]" % (where, j)
            if not isinstance(v, dict):
                problems.append("%s: not an object (%r)" % (vw, v))
                continue
            reviewer = v.get("reviewer")
            if not isinstance(reviewer, str) or not reviewer.strip():
                problems.append("%s: reviewer must be a non-empty identity string; "
                                "independence is counted per identity" % vw)
            _bool_field(v, "independent", vw, problems)
            if v.get("verdict") not in VOTE_VERDICTS:
                problems.append("%s: verdict is %r, not one of %r"
                                % (vw, v.get("verdict"), VOTE_VERDICTS))
        operator = p.get("operator_verdict", None)
        if operator is not None and not isinstance(operator, str):
            problems.append("%s: operator_verdict must be a string or null" % where)
    return problems


def gate(record):
    """Returns (rc, findings). rc 0 acceptable · 1 rejected · 2 record cannot be judged."""
    problems = validate(record)
    if problems:
        return 2, ["CANNOT CHECK — the record cannot be judged:"] + problems
    findings = []
    for p in record["proposals"]:
        pid = p.get("id", "<unnamed>")
        votes = p["votes"]
        # DISTINCT identities. Two votes carrying the same reviewer id are one model
        # echoing itself, and an echo is not a second opinion however it is labelled.
        independent = sorted({v["reviewer"].strip() for v in votes
                              if v.get("independent") is True})
        if len(independent) < 2:
            findings.append("%s: one-model approval — %d distinct independent reviewer(s) "
                            "voted (%r); a rule-base change needs a panel, not an echo"
                            % (pid, len(independent), independent))
        echoes = [r for r in independent
                  if len([v for v in votes if v["reviewer"].strip() == r
                          and v.get("independent") is True]) > 1]
        if echoes:
            findings.append("%s: reviewer(s) %r voted more than once as independent; one "
                            "identity is one opinion" % (pid, echoes))
        if p["verified_before_vote"] is not True:
            findings.append("%s: vote-before-verification — the verifier/dry-run must run "
                            "BEFORE the vote; a vote on unverified material is theater" % pid)
        verdicts = {v["verdict"] for v in votes}
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
    """Teeth: each red shape must be rejected; the well-formed record must pass; and every
    omission-shaped bypass must be CANNOT CHECK."""
    failures = []

    def expect(label, record, want_rc, want_token=None):
        rc, out = gate(record)
        if rc != want_rc or (want_token and not any(want_token in o for o in out)):
            failures.append("%s: got rc=%d %r" % (label, rc, out))

    expect("one-model approval must be rejected", _fixture(n_independent=1), 1,
           "one-model approval")
    expect("vote-before-verification must be rejected", _fixture(verified=False), 1,
           "vote-before-verification")
    expect("disagreement without an operator verdict must be rejected",
           _fixture(split=True), 1, "disagreement")
    expect("a split WITH an operator verdict is the process working; it must pass",
           _fixture(split=True, operator="operator kept the dissenting reading"), 0)
    expect("the well-formed record must pass", _fixture(), 0)
    expect("an invalid record must be CANNOT CHECK, never a pass", {"proposals": "no"}, 2)

    echo = {"proposals": [{"id": "prop-1", "verified_before_vote": True,
                           "votes": [{"reviewer": "model-a", "independent": True,
                                      "verdict": "accept"},
                                     {"reviewer": "model-a", "independent": True,
                                      "verdict": "accept"}],
                           "operator_verdict": None}]}
    expect("one model echoing itself is not a panel", echo, 1, "one-model approval")
    stringly = {"proposals": [{"id": "prop-1", "verified_before_vote": "false",
                               "votes": [{"reviewer": "model-a", "independent": "false",
                                          "verdict": "accept"},
                                         {"reviewer": "model-b", "independent": "false",
                                          "verdict": "accept"}],
                               "operator_verdict": None}]}
    expect("a stringly boolean must be rejected, not coerced", stringly, 2, "JSON boolean")
    expect("a non-dict proposal must be reported, not raised", {"proposals": ["nope"]}, 2,
           "not an object")
    expect("a proposal with no votes is unjudgeable",
           {"proposals": [{"id": "p", "verified_before_vote": True, "votes": []}]}, 2,
           "non-empty list")

    for f in failures:
        print("  FAIL  %s" % f)
    if failures:
        print("curation gate selftest: %d check(s) RED" % len(failures))
        return 1
    print("curation gate selftest: one-model approval, a single model echoing itself, "
          "vote-before-verification, and unresolved disagreement all rejected; the "
          "well-formed record passes; every omission-shaped bypass is CANNOT CHECK")
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
