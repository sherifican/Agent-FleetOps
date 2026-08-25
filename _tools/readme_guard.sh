#!/usr/bin/env bash
# readme_guard — refuse a commit that DELETES critical README content.
# Every other gate in this repo asks "is bad content present?"; deletion passes all of them and even
# makes the naming grep greener. This asks the opposite question. (Twice-burned 2026-08-22.)
set -uo pipefail
R="${1:-README.md}"
REQUIRED=(
  "Motherboard" "PCIe 4.0 x8" "PCIe 3.0 x4" "2933 MT/s" "deliberately mismatched"
  "What you'd actually need to reproduce this" "Radeon AI PRO R9700" "LPDDR5-8000"
  "Box A" "Box B"
)
miss=0
for k in "${REQUIRED[@]}"; do
  grep -qF -- "$k" "$R" || { echo "readme_guard: MISSING required content: $k" >&2; miss=1; }
done
[ $miss -eq 0 ] && echo "readme_guard: all ${#REQUIRED[@]} critical items present" || exit 1
