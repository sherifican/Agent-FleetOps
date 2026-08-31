#!/usr/bin/env bash
# run_guards.sh — the single entry point for the research-pipeline drift guards.
#
# Adapted from a peer agent's `_breaker/` guard stack (2026-07-31). The transferable part was
# the META-harness: machinery that keeps invariants honest, not the invariants themselves.
#
# ORDER MATTERS. The teeth-prover runs FIRST because every result below it is worth nothing until we
# know the guards can actually fail. A green light from an unproven guard is a green light wired to
# nothing — that is the single most expensive failure mode in the sibling system's history.
#
# EXIT CODES (the same three everywhere in this subsystem):
#   0 = clean · 1 = a real violation · 2 = UNMEASURED (something did not get checked)
# 2 DOMINATES 1. A check that did not run is absent, and absent must be loud: not knowing whether a
# guard ran can hide any number of violations beneath it.
#
# Usage:  guard/run_guards.sh [--with-canary]
#   --with-canary  also probe every cloud leg for liveness (costs one trivial cloud call per leg).
#                  Without it the canary runs --dry-run, which proves the WIRING only and says so.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 2

WITH_CANARY=0
[ "${1:-}" = "--with-canary" ] && WITH_CANARY=1

worst=0
n_ok=0; n_violation=0; n_unmeasured=0; n_skipped=0
note() { printf '\n\033[1m== %s ==\033[0m\n' "$1"; }
# 2 must win over 1, so never take a plain max of the raw codes.
roll() { local rc=$1
  case "$rc" in
    0) n_ok=$((n_ok + 1)) ;;
    2) n_unmeasured=$((n_unmeasured + 1)) ;;
    *) n_violation=$((n_violation + 1)) ;;
  esac
  if [ "$rc" = 2 ] || [ "$worst" = 2 ]; then worst=2; elif [ "$rc" != 0 ]; then worst=1; fi; }
# NOT CONFIGURED is a THIRD outcome, and keeping it distinct is the point: 2 = UNMEASURED means
# a check that WAS configured did not run, and you do not know what it would have said. An
# optional integration nobody supplied is not that — there is nothing to run and nothing missing.
# Rolling it in as UNMEASURED made every pristine clone report worse-than-a-violation forever,
# which is the same zero-information failure as always reporting clean.
skip() { n_skipped=$((n_skipped + 1)); printf '   NOT CONFIGURED (skipped, not UNMEASURED): %s\n' "$1"; }

note "1. TEETH-PROVER — can every guard actually fail?"
echo "   (nothing below this line means anything until this passes)"
python3 guard/teeth_prover.py; roll $?

note "2. CONTRACT AGREEMENT — do all surfaces state the same contract?"
python3 guard/contract_agreement.py; roll $?

note "3. GUARD UNIT GATES — do the guards themselves still behave?"
python3 -m pytest guard/tests/ -q; roll $?

note "4. GUARD SELF-TESTS — every proof-carrying tool must prove itself"
python3 guard/honesty_stop_gate.py --self-test; roll $?
python3 guard/envelope_tap.py --selftest; roll $?
python3 guard/scrub_arm.py --selftest; roll $?
python3 guard/population_arm.py --selftest; roll $?
python3 guard/one_writer_gate.py --selftest; roll $?
python3 guard/reconcile_gate.py --selftest; roll $?
python3 guard/brief_scan.py --selftest; roll $?
python3 guard/curation_gate.py --selftest; roll $?
python3 guard/org_lint.py --selftest; roll $?
if [ -f detect_poison.py ]; then
  python3 guard/fetch_gate.py --selftest; roll $?
else
  skip "the fetch-gate selftest needs the adopter-supplied detector (detect_poison.py at the repo root)"
fi
# The passback teeth test needs an outbox that exists on THIS box. There is no default: a
# fallback would ship one machine's directory layout to every adopter, and a check aimed at a
# path that does not exist reads "outbox empty" — a clean-looking result for a check that was
# never aimed at anything.
if [ -n "${PASSBACK_OUTBOX:-}" ]; then
  export PASSBACK_OUTBOX
  PASSBACK_TEETH_FILE="$PASSBACK_OUTBOX/replies/${PASSBACK_TEETH_TARGET:-REPLY_example.md}"
  if [ -f "$PASSBACK_TEETH_FILE" ]; then
    python3 guard/tests/teeth_passback_send_check.py; roll $?
  else
    echo "   PASSBACK_OUTBOX is set but no teeth target sits at \$PASSBACK_OUTBOX/replies (set"
    echo "   PASSBACK_TEETH_TARGET to a reply this box has already sent). This check WAS"
    echo "   configured and could not run: 2 = UNMEASURED."
    roll 2
  fi
else
  skip "the passback teeth test needs PASSBACK_OUTBOX (an outbox directory on this box)"
fi

note "5. NEGATIVE CONTROL — the runner itself must be able to fail"
echo "   A committed, deliberately broken config MUST read as broken. If it reads clean, every"
echo "   green above is a light wired to nothing."
# The acceptance test lives in guard/negative_control.py, where it can be unit-gated against
# impostors. It used to be two lines here: non-zero exit AND the token appearing anywhere in the
# output — which `echo <token>; exit 13` satisfies, as does any crash that prints the config it
# just read. Both were measured being accepted as proof the runner can fail.
python3 guard/negative_control.py; roll $?

note "6. PUBLIC-BYTE SCRUB — no private material in public-bound bytes"
echo "   Two pattern classes: private material and quoted speech. A rule name generalizes;"
echo "   a quoted person does not — removal is the only fix, so the arm catches it pre-publish."
python3 guard/scrub_arm.py --profile "${SCRUB_PROFILE:-adopter}"; roll $?
if [ "${SCRUB_PROFILE:-adopter}" = "adopter" ]; then
  echo "   NOTE: adopter profile = the shipped generic baseline only. A maintainer with a private"
  echo "   overlay (kept OUTSIDE the repo) runs SCRUB_PROFILE=maintainer SCRUB_OVERLAY=<path>;"
  echo "   under that profile an absent overlay is CANNOT_CHECK (2), never a pass."
fi

note "7. LEG LIVENESS"
if [ "$WITH_CANARY" = 1 ]; then
  python3 guard/leg_canary.py; roll $?
else
  python3 guard/leg_canary.py --dry-run; roll $?
  echo "   NOTE: dry-run proves the WIRING only — no leg was probed, so this returns 2 = UNMEASURED"
  echo "   and 2 DOMINATES a violation. That is deliberate: until 2026-08-03 the dry run wrote"
  echo "   fabricated ALIVE state and reported a PASS, so the staleness check could never fire."
  echo "   Run with --with-canary (or the daily cron) for real liveness."
fi

note "8. REPO SHAPE — no stray files at the repository root"
echo "   Exception classes (skills/file-organization): tool-required anchors; runnable entry"
echo "   points named in the README; directories. Everything else at the root is a stray."
python3 guard/org_lint.py; roll $?

note "RESULT"
# One machine-readable line, so the step accounting can be asserted instead of eyeballed.
printf 'steps: ok=%d violations=%d unmeasured=%d skipped=%d\n' \
  "$n_ok" "$n_violation" "$n_unmeasured" "$n_skipped"
case "$worst" in
  0) echo "clean — every guard proved, every surface agrees" ;;
  1) echo "VIOLATIONS found (see above)" ;;
  2) echo "UNMEASURED — something did not get checked. Treat as worse than a violation." ;;
esac
exit "$worst"
