#!/usr/bin/env bash
# run_guards.sh — the single entry point for the research-pipeline drift guards.
#
# Adapted from the ParaKit `_breaker/` stack (a peer agent’s reply, 2026-07-31). The transferable part was
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
note() { printf '\n\033[1m== %s ==\033[0m\n' "$1"; }
# 2 must win over 1, so never take a plain max of the raw codes.
roll() { local rc=$1; if [ "$rc" = 2 ] || [ "$worst" = 2 ]; then worst=2; elif [ "$rc" != 0 ]; then worst=1; fi; }

note "1. TEETH-PROVER — can every guard actually fail?"
echo "   (nothing below this line means anything until this passes)"
python3 guard/teeth_prover.py; roll $?

note "2. CONTRACT AGREEMENT — do all surfaces state the same contract?"
python3 guard/contract_agreement.py; roll $?

note "3. GUARD UNIT GATES — do the guards themselves still behave?"
python3 -m pytest guard/tests/ -q; roll $?

note "4. LEG LIVENESS"
if [ "$WITH_CANARY" = 1 ]; then
  python3 guard/leg_canary.py; roll $?
else
  python3 guard/leg_canary.py --dry-run; roll $?
  echo "   NOTE: dry-run proves the WIRING only — no leg was probed, so this returns 2 = UNMEASURED"
  echo "   and 2 DOMINATES a violation. That is deliberate: until 2026-08-03 the dry run wrote"
  echo "   fabricated ALIVE state and reported a PASS, so the staleness check could never fire."
  echo "   Run with --with-canary (or the daily cron) for real liveness."
fi

note "RESULT"
case "$worst" in
  0) echo "clean — every guard proved, every surface agrees" ;;
  1) echo "VIOLATIONS found (see above)" ;;
  2) echo "UNMEASURED — something did not get checked. Treat as worse than a violation." ;;
esac
exit "$worst"
