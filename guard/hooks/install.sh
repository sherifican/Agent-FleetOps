#!/usr/bin/env bash
# install.sh — put the version-controlled hooks where Git will actually run them. Git does not
# track its hooks directory, so a reclone silently loses them; that silence is why the source of
# truth lives in guard/hooks/ and why this script verifies what it installed instead of trusting
# its own copy.
#
#   install.sh                                  legacy: install every guard/hooks/* (except this
#                                               script) after the identity-config and scanner self-test preflight
#   install.sh --pre-push-config [--replace]    install ONLY guard/hooks/pre-push at Git's
#                                               effective hook path via the git-config identity
#                                               route; --replace backs up and replaces a
#                                               differing pre-existing hook
#   install.sh --check-pre-push-config          the same preflight plus a parity check, READ-ONLY
#
# Exit codes: 0 ok · 1 refused (state conflict, parity failure, missing input) · 2 usage.
#
# The config route never prints an identity value or scanner output: only source categories,
# paths and the reason for a refusal.
set -euo pipefail

root=$(cd "$(dirname "$0")/../.." && pwd -P)
cd "$root"

tracked="$root/guard/hooks/pre-push"
terms="$root/_tools/identity_terms.txt"
default_policy="$root/_tools/approved_identities.txt"
scanner="$root/_tools/scan_gate.py"

mode=legacy
replace=0
tmp_copy=""
backup_path=""
tracked_digest=""
dest_abs=""
dest_unresolved=""
n_local=0

cleanup() {
  # A staged copy that never passed parity must not outlive the run.
  if [ -n "$tmp_copy" ] && { [ -e "$tmp_copy" ] || [ -L "$tmp_copy" ]; }; then
    rm -f "$tmp_copy"
  fi
}
trap cleanup EXIT

usage_line="usage: install.sh [--pre-push-config [--replace] | --check-pre-push-config]"

usage_error() {
  echo "$usage_line — $*" >&2
  exit 2
}

refuse() {
  echo "refused: $*" >&2
  exit 1
}

digest() {
  local out
  out=$(sha256sum "$1") || return 1
  printf '%s\n' "${out%% *}"
}

mode_of() {
  # Octal permission bits without GNU/BSD stat flag divergence; python3 is already required.
  python3 -c 'import os, stat, sys; print("%o" % stat.S_IMODE(os.lstat(sys.argv[1]).st_mode))' "$1"
}

# verify_parity <installed>: succeeds only when <installed> is a regular, executable file that is
# byte-identical to the tracked pre-push by BOTH sha256sum and cmp -s. Prints the reason and
# returns 1 otherwise; never writes.
verify_parity() {
  local f=$1 d
  if [ ! -f "$f" ]; then
    echo "parity: no regular file at $f" >&2; return 1
  fi
  if [ ! -x "$f" ]; then
    echo "parity: $f is not executable" >&2; return 1
  fi
  if ! d=$(digest "$f"); then
    echo "parity: cannot hash $f" >&2; return 1
  fi
  if [ "$d" != "$tracked_digest" ]; then
    echo "parity: sha256 of $f differs from guard/hooks/pre-push" >&2; return 1
  fi
  if ! cmp -s "$tracked" "$f"; then
    echo "parity: cmp reports $f differs from guard/hooks/pre-push" >&2; return 1
  fi
  return 0
}

# Git's EFFECTIVE hook path (honours core.hooksPath and linked worktrees; never assumes .git is a
# directory). Two forms: the absolute one FOLLOWS symlinks (measured: a symlinked destination
# resolves to its target), so the symlink test below is made on the unresolved form.
resolve_dest() {
  local rel
  rel=$(git rev-parse --git-path hooks/pre-push) ||
    refuse "cannot resolve the hook path with git rev-parse (not a git repository?)"
  case "$rel" in
    /*) dest_unresolved=$rel ;;
    *)  dest_unresolved="$root/$rel" ;;
  esac
  dest_abs=$(git rev-parse --path-format=absolute --git-path hooks/pre-push 2>/dev/null) ||
    dest_abs=$dest_unresolved
}

refuse_symlink_dest() {
  local dir=${dest_unresolved%/*}
  if [ -L "$dest_unresolved" ]; then
    refuse "destination is a symlink (ambiguous ownership; parity cannot be established): $dest_unresolved — remove or replace the link yourself, then rerun"
  fi
  if [ -L "$dir" ]; then
    refuse "hooks directory is a symlink (ambiguous ownership): $dir (destination $dest_unresolved)"
  fi
}

# Identity-source preflight for the config route: the hook selects a FILE first
# (FLEETOPS_APPROVED_IDENTITIES, then _tools/approved_identities.txt) and only falls back to
# fleetops.approvedIdentity when the file is empty, so any file/env presence shadows the route
# being activated here. Refuse every shadow; require the repo-local config; require the scanner's
# inputs and self-test. Nothing is written or deleted.
preflight() {
  local v l found
  local -a local_vals=()

  if [ -n "${FLEETOPS_APPROVED_IDENTITIES:-}" ]; then
    refuse "FLEETOPS_APPROVED_IDENTITIES is set and would shadow the git-config identity route; unset it (env -u FLEETOPS_APPROVED_IDENTITIES ...) and rerun"
  fi
  if [ -e "$default_policy" ] || [ -L "$default_policy" ]; then
    refuse "_tools/approved_identities.txt exists (any form — file, empty file, symlink) and would shadow the git-config identity route; move it aside and rerun (nothing was deleted)"
  fi

  while IFS= read -r v; do
    local_vals+=("$v")
  done < <(git config --local --get-all fleetops.approvedIdentity 2>/dev/null || true)
  n_local=0
  for v in ${local_vals[@]+"${local_vals[@]}"}; do
    if [ -n "${v//[[:space:]]/}" ]; then
      n_local=$((n_local + 1))
    fi
  done
  if [ "$n_local" -eq 0 ]; then
    refuse "no nonblank fleetops.approvedIdentity in this repository's --local config; provision it with: git config --local --add fleetops.approvedIdentity <identity>"
  fi
  # Every value Git would resolve must be one this repository provisioned; an identity inherited
  # from global/system/environment config is not accepted for activation.
  while IFS= read -r v; do
    found=0
    for l in ${local_vals[@]+"${local_vals[@]}"}; do
      if [ "$l" = "$v" ]; then found=1; fi
    done
    if [ "$found" -eq 0 ]; then
      refuse "fleetops.approvedIdentity carries a value inherited from outside this repository's --local config (global/system/env); remove it there or add it locally, then rerun (values withheld)"
    fi
  done < <(git config --get-all fleetops.approvedIdentity 2>/dev/null || true)

  if [ ! -r "$terms" ] || [ ! -s "$terms" ]; then
    refuse "_tools/identity_terms.txt is missing, unreadable or empty (see _tools/identity_terms.example.txt)"
  fi
  if [ ! -f "$scanner" ]; then
    refuse "_tools/scan_gate.py is missing; its self-test cannot run"
  fi
  if ! python3 "$scanner" --self-test </dev/null >/dev/null 2>&1; then
    refuse "scanner self-test failed (python3 _tools/scan_gate.py --self-test </dev/null); its output is withheld here — run it yourself"
  fi
  if [ ! -f "$tracked" ]; then
    refuse "tracked hook guard/hooks/pre-push is missing; nothing to install or compare"
  fi
  tracked_digest=$(digest "$tracked") || refuse "cannot hash guard/hooks/pre-push"
}

report_selection() {
  echo "identity source: git-config (fleetops.approvedIdentity in this repository's local config, $n_local value(s); no file or environment shadow)"
  echo "effective hook path: $dest_abs"
}

do_check() {
  resolve_dest
  preflight || exit 1
  report_selection
  refuse_symlink_dest
  if ! verify_parity "$dest_abs"; then
    refuse "parity check failed for $dest_abs (read-only; nothing repaired) — rerun: guard/hooks/install.sh --pre-push-config [--replace]"
  fi
  echo "parity: OK — $dest_abs is executable and byte-identical to guard/hooks/pre-push"
}

make_backup() {
  local bdir common old_mode
  common=$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null) ||
    common=$(git rev-parse --git-common-dir)
  case "$common" in
    /*) ;;
    *)  common="$root/$common" ;;
  esac
  bdir="$common/fleetops-hook-backups"
  mkdir -p "$bdir"
  old_mode=$(mode_of "$dest_abs")
  backup_path="$bdir/pre-push.$(date -u +%Y%m%dT%H%M%SZ).$$.bak"
  cat "$dest_abs" > "$backup_path"
  chmod "$old_mode" "$backup_path"
  if [ "$(digest "$backup_path")" != "$1" ]; then
    rm -f "$backup_path"
    refuse "backup at $backup_path does not match the existing hook; nothing was replaced"
  fi
  echo "backup: $backup_path"
  echo "restore: install -m $old_mode '$backup_path' '$dest_abs'"
}

# Restore the pre-existing hook from the backup this run made (bytes + mode), verified by hash.
restore_backup() {
  local old_mode
  old_mode=$(mode_of "$backup_path")
  cat "$backup_path" > "$dest_abs"
  chmod "$old_mode" "$dest_abs"
  if [ "$(digest "$dest_abs")" = "$(digest "$backup_path")" ]; then
    echo "restored the previous hook at $dest_abs from $backup_path" >&2
  else
    echo "WARNING: restore of $dest_abs from $backup_path did not verify; restore by hand: install -m $old_mode '$backup_path' '$dest_abs'" >&2
  fi
}

do_install() {
  local hooks_dir old_present=0 old_digest="" identical=0 staged_digest

  resolve_dest
  preflight || exit 1
  report_selection
  refuse_symlink_dest
  hooks_dir=${dest_abs%/*}

  if [ -e "$dest_abs" ]; then
    if [ ! -f "$dest_abs" ]; then
      refuse "destination exists and is not a regular file: $dest_abs"
    fi
    old_present=1
    old_digest=$(digest "$dest_abs") || refuse "cannot hash the existing hook at $dest_abs"
    if [ "$old_digest" = "$tracked_digest" ] && cmp -s "$tracked" "$dest_abs"; then
      identical=1
    fi
    if [ "$identical" -eq 0 ] && [ "$replace" -eq 0 ]; then
      refuse "a different hook already exists at $dest_abs; rerun with --pre-push-config --replace to back it up (bytes + mode) and replace it, or remove it yourself. Nothing was changed"
    fi
    if [ "$identical" -eq 0 ]; then
      make_backup "$old_digest"
    fi
  fi

  mkdir -p "$hooks_dir"
  # Stage beside the destination, verify the staged copy, then rename over the destination: the
  # effective path never holds an unverified copy. The copy tool comes from PATH on purpose.
  tmp_copy=$(mktemp "$hooks_dir/.pre-push.install.XXXXXX")
  if ! install -m 755 "$tracked" "$tmp_copy"; then
    refuse "parity: copying guard/hooks/pre-push to $hooks_dir failed; nothing installed${backup_path:+ (backup kept: $backup_path)}"
  fi
  if ! verify_parity "$tmp_copy"; then
    refuse "parity check of the copy failed; $dest_abs was left as it was${backup_path:+ (backup kept: $backup_path)}"
  fi
  staged_digest=$(digest "$tmp_copy")
  mv -f "$tmp_copy" "$dest_abs"
  tmp_copy=""
  if ! verify_parity "$dest_abs"; then
    if [ "$old_present" -eq 1 ] && [ -n "$backup_path" ]; then
      restore_backup
    elif [ "$old_present" -eq 0 ] && [ "$(digest "$dest_abs" 2>/dev/null || true)" = "$staged_digest" ]; then
      rm -f "$dest_abs"
    fi
    refuse "parity check failed after installing to $dest_abs; rolled back${backup_path:+ (backup kept: $backup_path)}"
  fi
  echo "installed $dest_abs (guard/hooks/pre-push, mode 755, parity verified by sha256sum and cmp; identity route: git-config)"
}

do_legacy() {
  local hooks_rel hooks_unresolved hooks_abs h n d
  hooks_rel=$(git rev-parse --git-path hooks) ||
    refuse "cannot resolve the hooks directory with git rev-parse (not a git repository?)"
  case "$hooks_rel" in
    /*) hooks_unresolved=$hooks_rel ;;
    *)  hooks_unresolved="$root/$hooks_rel" ;;
  esac
  hooks_abs=$(git rev-parse --path-format=absolute --git-path hooks 2>/dev/null) ||
    hooks_abs=$hooks_unresolved
  mkdir -p "$hooks_abs"
  for h in guard/hooks/*; do
    n=$(basename "$h"); [ "$n" = "install.sh" ] && continue
    if [ -L "$hooks_unresolved/$n" ]; then
      refuse "destination is a symlink (ambiguous ownership): $hooks_unresolved/$n — remove the link yourself, then rerun"
    fi
    install -m 755 "$h" "$hooks_abs/$n"
    d=$(digest "$hooks_abs/$n")
    if [ "$d" != "$(digest "$h")" ] || ! cmp -s "$h" "$hooks_abs/$n" || [ ! -x "$hooks_abs/$n" ]; then
      refuse "parity check failed for $hooks_abs/$n after copying $h"
    fi
    echo "installed $hooks_abs/$n"
  done
  # The installed hooks need two inputs that live OUTSIDE the tree, and both fail CLOSED when absent:
  # _tools/identity_terms.txt (gitignored; see _tools/identity_terms.example.txt) for the scan gate,
  # and the approved-identity list — _tools/approved_identities.txt, FLEETOPS_APPROVED_IDENTITIES, or
  # `git config --add fleetops.approvedIdentity <identity>` — for the pre-push identity check.
  echo "note: the hooks need _tools/identity_terms.txt and an approved-identity source; both refuse rather than pass when absent"
}

for arg in "$@"; do
  case "$arg" in
    --pre-push-config)
      [ "$mode" = legacy ] || [ "$mode" = install ] || usage_error "--pre-push-config conflicts with --check-pre-push-config"
      mode=install ;;
    --check-pre-push-config)
      [ "$mode" = legacy ] || [ "$mode" = check ] || usage_error "--check-pre-push-config conflicts with --pre-push-config"
      mode=check ;;
    --replace)
      replace=1 ;;
    -h|--help)
      echo "$usage_line"; exit 0 ;;
    *)
      usage_error "unknown argument: $arg" ;;
  esac
done
if [ "$replace" -eq 1 ] && [ "$mode" != install ]; then
  usage_error "--replace is only valid with --pre-push-config"
fi

case "$mode" in
  install) do_install ;;
  check)   do_check ;;
  legacy)  preflight || exit 1; do_legacy ;;
esac
