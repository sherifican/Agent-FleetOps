#!/usr/bin/env bash
# install.sh — put the version-controlled hooks into .git/hooks (which git does not track, so a reclone
# silently loses them; that silence is why the source of truth lives in guard/hooks/).
set -euo pipefail
cd "$(dirname "$0")/../.."
for h in guard/hooks/*; do
  n=$(basename "$h"); [ "$n" = "install.sh" ] && continue
  install -m 755 "$h" ".git/hooks/$n"; echo "installed .git/hooks/$n"
done
# The installed hooks need two inputs that live OUTSIDE the tree, and both fail CLOSED when absent:
# _tools/identity_terms.txt (gitignored; see _tools/identity_terms.example.txt) for the scan gate,
# and the approved-identity list — _tools/approved_identities.txt, FLEETOPS_APPROVED_IDENTITIES, or
# `git config --add fleetops.approvedIdentity <identity>` — for the pre-push identity check.
echo "note: the hooks need _tools/identity_terms.txt and an approved-identity source; both refuse rather than pass when absent"
