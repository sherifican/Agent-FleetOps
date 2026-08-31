#!/usr/bin/env python3
"""reconcile_gate.py — agreement is not verification.

Every leg agreeing is exactly what a shared unchecked premise produces: correlated error.
N models fed one wrong source converge on one wrong answer, and the convergence reads as
confidence. This gate runs over a reconcile record BEFORE anything is acted on and
refuses an ACT whose shared premises never passed a verifier.

It also requires TWO verdicts per acted claim, recorded separately:
  conclusion_verdict — is the ANSWER right?
  mechanism_verdict  — is the REASON sound?
A true conclusion protects a false reason from scrutiny; keeping the verdicts separate
stops a right-answer-wrong-mechanism claim from banking credibility for its premise.

THIS GATE IS NOT HONOR-SYSTEM, AND OMISSION IS NOT A BYPASS
    Measured on the first draft, all returning "clean": an ACT with no `conclusion_verdict`
    FIELD at all; an ACT with `premises` omitted, and one with `premises: []`; a premise
    carrying `"verified": "false"` with `"verifier": "none"` — because a quoted "false" is
    a non-empty string and every truthiness test passes it. A malformed element crashed
    with AttributeError where the contract advertises exit 2. So the record is now
    VALIDATED before it is judged: a required field that is absent, of the wrong type, or
    carrying a value outside its documented set makes the record UNJUDGEABLE (exit 2),
    which dominates a refusal. Booleans must be JSON booleans. Structure errors are
    reported, never raised.

RECORD FORMAT (JSON) — types and allowed values are enforced:
  {"claims": [{
      "id": str,
      "action": "ACT" | "PROVISIONAL" | "HOLD",
      "legs": [{"leg": str, "verdict": str}, ...],
      "conclusion_verdict": "supported" | "refuted" | "unproven",   (required for ACT)
      "mechanism_verdict":  "supported" | "refuted" | "unproven",   (required for ACT)
      "premises": [{"id": str, "shared": bool, "verified": bool, "verifier": str}, ...]
                                                                    (required for ACT)
  }, ...]}

Rules, applied ONLY to claims with action == ACT (HOLD/PROVISIONAL are already not being
acted on):
  R1  every premise marked shared must carry verified:true AND a named verifier —
      unanimity across legs does not substitute, however many legs agree;
  R2  mechanism_verdict must be "supported" — the record must SHOW the reason was judged
      and held, not only the answer;
  R3  an ACT must LIST at least one shared premise. Zero listed shared premises is not a
      claim that stands on nothing; it is a claim whose premises were never written down,
      and the correlated-error check has nothing to run against;
  R4  conclusion_verdict must be "supported" — acting on a conclusion the record itself
      marks refuted or unproven is incoherent.

Exit codes: 0 no refused claims · 1 refused claim(s), each named with its rule ·
2 CANNOT CHECK (unreadable, or a record that cannot be judged).
Gate: guard/tests/test_reconcile_gate.py. Red demos: RG1 waives the shared-premise
verification; RG2 waives the required-verdict-field check; RG3 accepts a stringly boolean.
"""
import argparse
import json
import sys

ACTIONS = ("ACT", "PROVISIONAL", "HOLD")
VERDICTS = ("supported", "refuted", "unproven")
VERDICT_FIELDS = ("conclusion_verdict", "mechanism_verdict")


def _bool_field(obj, key, where, problems):
    """A JSON boolean, or None with a problem recorded. Never coerced.

    `"false"` is a five-character string. Every truthiness test in Python passes it, so
    coercing here would turn an explicit negative into a positive — the exact bypass this
    gate exists to close."""
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
    claims = record.get("claims")
    if not isinstance(claims, list):
        return ["CANNOT CHECK — record carries no claims list"]
    for i, c in enumerate(claims):
        where = "claim[%d]" % i
        if not isinstance(c, dict):
            problems.append("%s: not an object (%r) — a malformed element must be "
                            "reported, never raised" % (where, c))
            continue
        where = "claim %r" % c.get("id", "<unnamed>")
        action = c.get("action")
        if action not in ACTIONS:
            problems.append("%s: action %r is not one of %r" % (where, action, ACTIONS))
            continue
        legs = c.get("legs", [])
        if not isinstance(legs, list) or any(not isinstance(x, dict) for x in legs):
            problems.append("%s: legs must be a list of objects" % where)
        for field in VERDICT_FIELDS:
            if field not in c:
                if action == "ACT":
                    problems.append("%s: %r is absent. An ACT records BOTH verdicts, "
                                    "precisely so being accidentally right is "
                                    "distinguishable from having diagnosed correctly."
                                    % (where, field))
                continue
            if c[field] not in VERDICTS:
                problems.append("%s: %r is %r, not one of %r"
                                % (where, field, c[field], VERDICTS))
        premises = c.get("premises")
        if premises is None:
            if action == "ACT":
                problems.append("%s: premises is absent. An ACT with no premises recorded "
                                "cannot be checked for correlated error at all." % where)
            continue
        if not isinstance(premises, list):
            problems.append("%s: premises must be a list" % where)
            continue
        for j, p in enumerate(premises):
            pw = "%s premise[%d]" % (where, j)
            if not isinstance(p, dict):
                problems.append("%s: not an object (%r)" % (pw, p))
                continue
            shared = _bool_field(p, "shared", pw, problems)
            if shared is True:
                _bool_field(p, "verified", pw, problems)
                if not isinstance(p.get("verifier", ""), str):
                    problems.append("%s: verifier must be a string" % pw)
    return problems


def gate(record):
    """Returns (rc, refusals). rc 0 clean · 1 refused · 2 record cannot be judged."""
    problems = validate(record)
    if problems:
        return 2, ["CANNOT CHECK — the record cannot be judged:"] + problems
    refusals = []
    for c in record["claims"]:
        if c.get("action") != "ACT":
            continue
        cid = c.get("id", "<unnamed>")
        shared_premises = [p for p in c["premises"] if p.get("shared") is True]
        for p in shared_premises:
            ok_premise = p.get("verified") is True and bool(str(p.get("verifier") or "").strip())
            if not ok_premise:
                n_legs = len(c.get("legs", []))
                refusals.append(
                    "%s: R1 — shared premise %r is unverified; %d leg(s) agreeing on it is "
                    "correlated error, not verification. ACT is refused until the premise "
                    "passes a verifier that can fail." % (cid, p.get("id"), n_legs))
        if not shared_premises:
            refusals.append(
                "%s: R3 — the ACT lists no shared premise. That is not a claim resting on "
                "nothing; it is a claim whose premises were never written down, so the "
                "correlated-error check has nothing to run against." % cid)
        if c.get("mechanism_verdict") != "supported":
            refusals.append(
                "%s: R2 — mechanism verdict is %r. The conclusion was judged; the REASON "
                "was not held, and a true conclusion protects a false reason from "
                "scrutiny." % (cid, c.get("mechanism_verdict")))
        if c.get("conclusion_verdict") != "supported":
            refusals.append(
                "%s: R4 — conclusion verdict is %r; acting on a conclusion the record "
                "itself does not support is incoherent." % (cid, c.get("conclusion_verdict")))
    return (1 if refusals else 0), refusals


def _fixture(verified, with_mechanism=True, action="ACT"):
    return {"claims": [{
        "id": "claim-1", "action": action,
        "legs": [{"leg": "A", "verdict": "supported"},
                 {"leg": "B", "verdict": "supported"},
                 {"leg": "C", "verdict": "supported"}],
        "conclusion_verdict": "supported",
        "mechanism_verdict": "supported" if with_mechanism else "unproven",
        "premises": [{"id": "p1", "shared": True, "verified": verified,
                      "verifier": "independent source fetch" if verified else ""}]}]}


def selftest():
    """Teeth: an all-agree ACT on an unverified shared premise must refuse; the same
    record verified must pass; an unheld mechanism verdict must refuse; an ACT with no
    listed shared premise must refuse; a HOLD on an unverified premise passes the gate (it
    is not being acted on); and every omission-shaped bypass must be CANNOT CHECK."""
    failures = []

    def expect(label, record, want_rc, want_token=None):
        rc, out = gate(record)
        if rc != want_rc or (want_token and not any(want_token in o for o in out)):
            failures.append("%s: got rc=%d %r" % (label, rc, out))

    expect("unanimous ACT on an unverified shared premise must be refused",
           _fixture(verified=False), 1, "R1")
    expect("a verified shared premise must clear the gate", _fixture(verified=True), 0)
    expect("an ACT whose mechanism verdict is not supported must be refused",
           _fixture(verified=True, with_mechanism=False), 1, "R2")
    expect("HOLD is already not acted on; the gate must not block it",
           _fixture(verified=False, action="HOLD"), 0)
    expect("an invalid record must be CANNOT CHECK, never a pass", {"nonsense": 1}, 2)

    ok = _fixture(verified=True)["claims"][0]
    missing_verdict = dict(ok)
    del missing_verdict["conclusion_verdict"]
    expect("an ACT with no conclusion_verdict FIELD is unjudgeable",
           {"claims": [missing_verdict]}, 2, "conclusion_verdict")
    no_premises = dict(ok)
    del no_premises["premises"]
    expect("an ACT with premises omitted is unjudgeable",
           {"claims": [no_premises]}, 2, "premises is absent")
    empty_premises = dict(ok, premises=[])
    expect("an ACT listing zero shared premises must be refused",
           {"claims": [empty_premises]}, 1, "R3")
    stringly = dict(ok, premises=[{"id": "p1", "shared": True, "verified": "false",
                                   "verifier": "none"}])
    expect("a stringly boolean must be rejected, not coerced",
           {"claims": [stringly]}, 2, "JSON boolean")
    expect("a non-dict claim must be reported, not raised",
           {"claims": ["nope"]}, 2, "not an object")

    for f in failures:
        print("  FAIL  %s" % f)
    if failures:
        print("reconcile gate selftest: %d check(s) RED" % len(failures))
        return 1
    print("reconcile gate selftest: unverified-shared-premise ACT refused, verified ACT "
          "passes, unheld mechanism verdict refused, premise-less ACT refused, and every "
          "omission-shaped bypass is CANNOT CHECK")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("record", nargs="?", help="reconcile record (JSON file)")
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
        print("reconcile gate clean — every acted claim carries verified shared premises "
              "and both verdicts held")
    return rc


if __name__ == "__main__":
    sys.exit(main())
