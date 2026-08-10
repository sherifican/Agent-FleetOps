#!/usr/bin/env bash
# install.sh — put the version-controlled hooks into .git/hooks (which git does not track, so a reclone
# silently loses them; that silence is why the source of truth lives in guard/hooks/).
set -euo pipefail
cd "$(dirname "$0")/../.."
for h in guard/hooks/*; do
  n=$(basename "$h"); [ "$n" = "install.sh" ] && continue
  install -m 755 "$h" ".git/hooks/$n"; echo "installed .git/hooks/$n"
done
