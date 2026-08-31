#!/usr/bin/env python3
"""passback_send_check.py — does a peer agent actually hold what this check thinks was sent?

Backlog D19. The rule "writing a file into the outbox is not sending it" was written 2026-07-28 and did
not prevent four replies sitting undelivered for a day on 08-01, or a stale `run_guards.sh` on 08-03. A rule
that has failed twice is a mechanism that does not exist.

★ THE DESIGN CONSTRAINT THAT DECIDES EVERYTHING ELSE: the evidence must come from the RECIPIENT, never from
this check's own send log. A receipt written at push time is produced by the same act it is evidence about — it can
confirm "I ran topc" and can never confirm "they have it". So this asks the PC directly and hashes the file
THERE.

Three states, because two is what caused the original failure:
  SENT        remote hash == local hash
  DIVERGED    present remotely, content differs  -> VIOLATION. The one a "did it leave the box?" check
              misses (measured 08-03: they held run_guards.sh at 2,468 B while local was 2,683 B, 9h stale)
  NOT-PUSHED  absent from the PC transfer dirs   -> a DECLARED SCOPE LIMIT, printed every run. Not a
              violation, and (see the exit-code note) not folded into the exit code either.

★ WHY "NOT-PUSHED" IS NOT A VIOLATION — this check was WRONG on its first run and this is the
correction. There are TWO delivery paths, and this box can only observe one:
  (1) push script -> lands in the receiver's dated transfer dir  -- observable here
  (2) a peer agent reading the outbox IN PLACE from this box over the .100 SSH link (documented in
      the channel's SSH key and its logins are real)  -- NOT observable
So "absent from Downloads" cannot distinguish NEVER-SENT from READ-IN-PLACE. Reporting it as a violation
would assert knowledge this check does not have, and would be the same two-states-one-output defect this whole
subsystem exists to catch — a check that cries wolf trains you to ignore it, which is worse than no check.
The first run flagged 17 files this way; that number is a QUESTION, not a verdict.
**To make absence meaningful, the SEND side has to change** — if every outbound file goes via `topc`, then
absence becomes evidence. That is a process decision, not something this script can assume.

EXIT CODES — and this script is a DELIBERATE, NARROW EXCEPTION to the subsystem's "2 DOMINATES 1":
  0 = every OBSERVABLE file matches  ·  1 = DIVERGED  ·  2 = the recipient was unreachable (nothing learned)
★ Why the exception, found by this script's OWN teeth test (2026-08-03): the first cut rolled the
not-pushed set in as UNMEASURED. That set is PERMANENTLY non-empty, so the exit code was permanently 2 and a
real DIVERGED violation could never surface in it — the check detected the defect and returned an unusable
code. **An always-2 exit is exactly as informative as an always-0 one.** The convention exists for *a check
that did not run and you do not know what you missed*; here the check ran and the unobservable population is
known, enumerated and printed. That is a SCOPE LIMIT, which belongs in the OUTPUT, not in the verdict.
Generalisable: UNMEASURED-dominates is right when unmeasured is EXCEPTIONAL, and becomes a zero-information
generator the moment unmeasured is the permanent baseline.

PRE-CONVENTION FILES ARE NEVER COUNTED AS VERIFIED. Dated transfer dirs on the receiver
only start 2026-07-28; anything older crossed by other means and cannot be checked from here. They are
printed with a count on every run, never silently dropped — a check that quietly excludes what it cannot
judge reports a cleanliness it did not measure.

Usage:  passback_send_check.py [--json] [--quiet]
"""
import base64, hashlib, json, os, subprocess, sys

# NO DEFAULT. A fallback here would ship one machine's directory layout to every adopter, and
# a check pointed at a path that does not exist on this box reads "outbox empty" — a clean-
# looking 2 for a check that was never aimed at anything. Unset means CANNOT_CHECK, out loud.
OUTBOX = os.path.expanduser(os.environ.get("PASSBACK_OUTBOX", "") or "")
PCSH = os.environ.get("PASSBACK_RECIPIENT_SHELL", "pcsh")
REMOTE_GLOB = r"C:\Users\<user>\Downloads\fleet-to-peer_*"
CONVENTION_START = "2026-07-28"          # first dated transfer dir; older sends are unverifiable from here

CLEAN, VIOLATION, UNMEASURED = 0, 1, 2


def _roll(worst, rc):
    """2 dominates 1 dominates 0. Never a plain max() — that is right by accident here and wrong later."""
    if rc == UNMEASURED or worst == UNMEASURED:
        return UNMEASURED
    return VIOLATION if (rc or worst) else CLEAN


def local_files():
    out = {}
    for root, _dirs, names in os.walk(OUTBOX):
        for n in names:
            p = os.path.join(root, n)
            try:
                with open(p, "rb") as fh:
                    out[n] = (hashlib.sha256(fh.read()).hexdigest(), p, os.path.getmtime(p))
            except OSError:
                continue
    return out


def remote_files():
    """name -> sha256, hashed ON THE PC. Returns None if the recipient could not be reached at all."""
    ps = (
        f'$d = Get-ChildItem -Path "{REMOTE_GLOB}" -Directory -ErrorAction SilentlyContinue; '
        'if (-not $d) {{ Write-Output "NODIRS" }} else {{ '
        'foreach ($x in $d) {{ Get-ChildItem -Path $x.FullName -File -ErrorAction SilentlyContinue | '
        'ForEach-Object {{ "{0}`t{1}" -f $_.Name, (Get-FileHash $_.FullName -Algorithm SHA256).Hash }} }} }}'
    ).replace("{{", "{").replace("}}", "}")
    enc = base64.b64encode(ps.encode("utf-16-le")).decode()
    try:
        r = subprocess.run([PCSH, f"powershell -NoProfile -EncodedCommand {enc}"],
                           capture_output=True, text=True, timeout=180)
    except (subprocess.TimeoutExpired, OSError):
        return None
    if r.returncode != 0:
        return None
    got = {}
    for line in r.stdout.splitlines():
        if "\t" in line:
            name, h = line.rsplit("\t", 1)
            got[name.strip()] = h.strip().lower()
    return got


def main():
    as_json = "--json" in sys.argv
    quiet = "--quiet" in sys.argv
    if not OUTBOX:
        print("2 UNMEASURED: PASSBACK_OUTBOX is not set, so there is no outbox to compare and\n"
              "   nothing was checked. This check has no default: pointing it at a guessed path\n"
              "   would read as 'outbox empty' and look like a result.", file=sys.stderr)
        return UNMEASURED
    local = local_files()
    if not local:
        print("2 UNMEASURED: outbox is empty or unreadable — nothing was checked", file=sys.stderr)
        return UNMEASURED

    remote = remote_files()
    if remote is None:
        print("2 UNMEASURED: the PC could not be reached, so NOTHING about delivery was verified.\n"
              "   An unreachable recipient is exactly when the least is known — this is not 'clean'.",
              file=sys.stderr)
        return UNMEASURED

    sent, unsent, diverged, unverifiable = [], [], [], []
    for name, (lh, path, _mt) in sorted(local.items()):
        pre = os.path.basename(path).find(CONVENTION_START) == -1 and _is_pre_convention(name)
        rh = remote.get(name)
        if rh is None:
            (unverifiable if pre else unsent).append(name)
        elif rh != lh:
            diverged.append(name)
        else:
            sent.append(name)

    # ★ SCOPE LIMIT is not the same as UNMEASURED — corrected 2026-08-03 by this script's own teeth test.
    # First cut rolled the not-pushed set in as UNMEASURED. Because that set is PERMANENTLY non-empty, the
    # exit code was permanently 2, so a real DIVERGED violation could never surface in it — the check was
    # detecting the defect and reporting an unusable code. An always-2 exit carries no information, which is
    # the very class this subsystem exists to kill.
    # The convention "2 dominates 1" is for *a check that did not run and you do not know what you missed*.
    # Here the check ran, and the unobservable population is known, enumerated and printed every time. That
    # is a declared SCOPE LIMIT, not a failure to measure. So:
    #   1  DIVERGED — positive evidence the recipient holds something different (the only real violation)
    #   2  the recipient could not be reached at all — genuinely nothing was learned (handled above)
    #   0  every observable file matches, WITH the scope limit stated out loud on every run, never silently
    worst = VIOLATION if diverged else CLEAN

    if as_json:
        print(json.dumps({"sent": sent, "unsent": unsent, "diverged": diverged,
                          "unverifiable_pre_convention": unverifiable, "exit": worst}, indent=2))
        return worst

    if not quiet:
        print(f"passback send check — {len(local)} local file(s), {len(remote)} on the PC")
        print(f"  ✓ SENT and identical : {len(sent)}")
    for n in diverged:
        print(f"  ⛔ DIVERGED  {n}  — they hold a DIFFERENT version; a 'did it leave?' check passes this")
    if unsent and not quiet:
        print(f"  ? NOT-PUSHED {len(unsent)} file(s) — absent from the PC transfer dirs. DECLARED SCOPE LIMIT,"
              f" not a violation and not a pass:\n"
              f"    they were either never sent OR read in place over SSH, and this box cannot tell which.")
        for n in unsent[:8]:
            print(f"      · {n}")
        if len(unsent) > 8:
            print(f"      · … and {len(unsent) - 8} more (use --json for the full list; nothing is hidden)")
    if unverifiable and not quiet:
        print(f"  ? UNMEASURED {len(unverifiable)} pre-{CONVENTION_START} file(s) — crossed before the dated"
              f" transfer dirs existed; cannot be verified from here, NOT counted clean")
    if not quiet:
        print({CLEAN: "\nclean — every OBSERVABLE file matches; scope limit above is not a pass, it is a stated gap",
               VIOLATION: "\nVIOLATIONS — see above",
               UNMEASURED: "\nUNMEASURED — something was not checked. Worse than a violation."}[worst])
    return worst


def _is_pre_convention(name):
    """A filename carrying a date older than the dated-transfer-dir convention."""
    import re
    m = re.search(r"(20\d\d)-(\d\d)-(\d\d)", name)
    return bool(m) and f"{m.group(1)}-{m.group(2)}-{m.group(3)}" < CONVENTION_START


if __name__ == "__main__":
    sys.exit(main())
