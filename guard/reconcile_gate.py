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

RECORD FORMAT (JSON):
  {"claims": [{
      "id": str,
      "action": "ACT" | "PROVISIONAL" | "HOLD",
      "legs": [{"leg": str, "verdict": str}, ...],
      "conclusion_verdict": str,
      "mechanism_verdict": str,
      "premises": [{"id": str, "shared": bool, "verified": bool, "verifier": str}, ...]
  }, ...]}

Rules, applied ONLY to claims with action == ACT (HOLD/PROVISIONAL are already not being
acted on):
  R1  every premise marked shared must carry verified:true AND a named verifier —
      unanimity across legs does not substitute, however many legs agree;
  R2  mechanism_verdict must be present and non-empty, distinct from conclusion_verdict's
      field (the record must SHOW the reason was judged, not only the answer).

Exit codes: 0 no refused claims · 1 refused claim(s), each named with its rule ·
2 CANNOT CHECK (unreadable/invalid record).
Gate: guard/tests/test_reconcile_gate.py. Red demo: mutation RG1 waives the
shared-premise verification and the gate must go red.
"""
import argparse
import json
import sys


def gate(record):
    """Returns (rc, refusals). rc 0 clean · 1 refused · 2 invalid record."""
    claims = record.get("claims")
    if not isinstance(claims, list):
        return 2, ["CANNOT CHECK — record carries no claims list"]
    refusals = []
    for c in claims:
        if c.get("action") != "ACT":
            continue
        cid = c.get("id", "<unnamed>")
        for p in c.get("premises", []):
            if not p.get("shared"):
                continue
            ok_premise = bool(p.get("verified")) and bool(str(p.get("verifier") or "").strip())
            if not ok_premise:
                n_legs = len(c.get("legs", []))
                refusals.append(
                    "%s: R1 — shared premise %r is unverified; %d leg(s) agreeing on it is "
                    "correlated error, not verification. ACT is refused until the premise "
                    "passes a verifier that can fail." % (cid, p.get("id"), n_legs))
        if not str(c.get("mechanism_verdict") or "").strip():
            refusals.append(
                "%s: R2 — no mechanism verdict recorded. The conclusion was judged; the "
                "REASON was not, and a true conclusion protects a false reason from "
                "scrutiny." % cid)
    return (1 if refusals else 0), refusals


def _fixture(verified, with_mechanism=True, action="ACT"):
    return {"claims": [{
        "id": "claim-1", "action": action,
        "legs": [{"leg": "A", "verdict": "supported"},
                 {"leg": "B", "verdict": "supported"},
                 {"leg": "C", "verdict": "supported"}],
        "conclusion_verdict": "supported",
        "mechanism_verdict": "premise chain checked against the source" if with_mechanism else "",
        "premises": [{"id": "p1", "shared": True, "verified": verified,
                      "verifier": "independent source fetch" if verified else ""}]}]}


def selftest():
    """Teeth: an all-agree ACT on an unverified shared premise must refuse; the same
    record verified must pass; a missing mechanism verdict must refuse; a HOLD on an
    unverified premise passes the gate (it is not being acted on)."""
    failures = []
    rc, out = gate(_fixture(verified=False))
    if rc != 1 or not any("R1" in o for o in out):
        failures.append("unanimous ACT on an unverified shared premise must be refused")
    rc, _ = gate(_fixture(verified=True))
    if rc != 0:
        failures.append("a verified shared premise must clear the gate")
    rc, out = gate(_fixture(verified=True, with_mechanism=False))
    if rc != 1 or not any("R2" in o for o in out):
        failures.append("an ACT without a mechanism verdict must be refused")
    rc, _ = gate(_fixture(verified=False, action="HOLD"))
    if rc != 0:
        failures.append("HOLD is already not acted on; the gate must not block it")
    rc, _ = gate({"nonsense": 1})
    if rc != 2:
        failures.append("an invalid record must be CANNOT CHECK, never a pass")
    for f in failures:
        print("  FAIL  %s" % f)
    if failures:
        print("reconcile gate selftest: %d check(s) RED" % len(failures))
        return 1
    print("reconcile gate selftest: unverified-shared-premise ACT refused, verified ACT "
          "passes, missing mechanism verdict refused")
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
              "and a mechanism verdict")
    return rc


if __name__ == "__main__":
    sys.exit(main())
