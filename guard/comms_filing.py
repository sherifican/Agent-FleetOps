#!/usr/bin/env python3
"""comms_filing.py — an ANSWER must never be filed into the ASK queue.

WHY. Twice in one day a message was filed where the recipient was not looking:
  · 2026-07-31, a peer agent -> Fleet: a release-blocking brief landed OUTSIDE CLAUDE-COMMS/
    entirely. Eleven hours of "blocked on Fleet" that was really "blocked on filing".
  · 2026-07-31, Fleet -> a peer agent: a REPLY_* was filed into requests/ (the folder for ASKS).
    a peer agent reported "nothing new from you" and was correct. Sync was healthy; placement was not.

Neither side wrote the convention down, so both broke it. A filing convention nobody asserts is an
implicit contract, and an implicit contract is still a contract — the same class as the yt-dlp
filename shape that produced 0 parsed transcripts, and as the preamble/validator vocabulary drift.
This makes it mechanical.

THE RULE (exactly one, deliberately):
    A file named REPLY_* / NUDGE_* / FOLLOWUP_* must NOT sit in `requests/`.

`requests/` is a queue the recipient works through. An answer parked in it either reads as a new
obligation or is skipped entirely — which is precisely the 2026-07-31 failure.

WHAT THIS DELIBERATELY DOES *NOT* POLICE: `reports/` vs `replies/`. The first draft of this guard
asserted a full prefix->folder mapping and fired on **31 files** across a mature tree, because both
sides file REPLY_* into reports/ routinely. A red rate that high is a hypothesis about the CHECK, not
the corpus — the "always fires" defect, which is exactly as useless as one that never fires and worse,
because the next real red gets waved through. Scope was narrowed to the one misfiling that costs
something. (See feedback-automation-does-its-own-job, third mode.)

Exit codes: 0 = every file correctly filed · 1 = at least one misfiled · 2 = a root is unreadable
(UNMEASURED — never folded into success; a directory we could not check is absent, and absent is loud).

    usage: comms_filing.py [--root DIR] [--fix]
           --fix MOVES misfiled files to the correct sibling folder (prints every move).
"""
import argparse
import os
import shutil
import sys

# No baked-in default: one machine's directory layout is not an adopter's. Set COMMS_ROOT or
# pass --root; unset is CANNOT_CHECK, never a scan of a guessed path that happens to be empty.
DEFAULT_ROOT = os.path.expanduser(os.environ.get("COMMS_ROOT", "") or "") or None

# prefix -> the folder it belongs in
# NARROW BY DESIGN. A first draft asserted a full prefix->folder mapping and fired on 31 files across
# a mature tree — both sides routinely file REPLY_* into reports/. A red rate that high is a hypothesis
# about the CHECK, not the corpus (see feedback-automation-does-its-own-job, third mode). So this guard
# now asserts ONE thing, the only misfiling that actually costs anything:
#
#   AN ANSWER MUST NOT BE FILED AS AN ASK.
#
# requests/ is a queue the recipient works through. A REPLY/NUDGE/FOLLOWUP sitting in it is either
# read as a new obligation or skipped entirely — which is exactly what happened on 2026-07-31.
# reports/ vs replies/ is a genuine style split both sides use interchangeably; policing it would be
# noise, and a guard that cries wolf gets the next real one waved through.
ANSWER_PREFIXES = {"REPLY", "NUDGE", "FOLLOWUP"}
ASK_FOLDER = "requests"


# Only these directions are checked; topic sub-folders (kick_gate_sweep/, corpus_multitier/, …) are
# deliberate working areas and are NOT governed by the prefix rule.
DIRECTIONS = ("outbound", "inbound")  # EDIT ME: your channel direction names
GOVERNED = ("requests", "replies", "reports")


def prefix_of(filename):
    stem = os.path.basename(filename)
    head = stem.split("_", 1)[0].upper()
    return head if head in ANSWER_PREFIXES else None


def check(root):
    """Return (misfiled, unmeasured). misfiled = [(path, actual_folder, expected_folder)]."""
    misfiled, unmeasured = [], []
    for direction in DIRECTIONS:
        base = os.path.join(root, direction)
        if not os.path.isdir(base):
            unmeasured.append(f"UNMEASURED: {base} — not a readable directory")
            continue
        for folder in GOVERNED:
            d = os.path.join(base, folder)
            if not os.path.isdir(d):
                continue  # a direction need not use every folder
            try:
                names = sorted(os.listdir(d))
            except OSError as err:
                unmeasured.append(f"UNMEASURED: {d} — {err}")
                continue
            for name in names:
                p = os.path.join(d, name)
                if not os.path.isfile(p) or not name.lower().endswith(".md"):
                    continue
                pre = prefix_of(name)
                if pre is None:
                    continue  # unrecognised prefix: not governed, not a violation
                if folder == ASK_FOLDER and pre in ANSWER_PREFIXES:
                    misfiled.append((p, folder, "replies"))
    return misfiled, unmeasured


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=DEFAULT_ROOT)
    ap.add_argument("--fix", action="store_true",
                    help="MOVE each misfiled file into its correct sibling folder")
    args = ap.parse_args(argv)
    if not args.root:
        print("2 CANNOT_CHECK: no comms root. Pass --root, or set COMMS_ROOT — this check ships "
              "no default, because a guessed layout would scan nothing and report it as clean.")
        return 2

    misfiled, unmeasured = check(args.root)

    for u in unmeasured:
        print(u)
    if not misfiled:
        print(f"comms filing: clean — every governed message is in the folder its prefix implies "
              f"({args.root})")
    for p, actual, expected in misfiled:
        print(f"MISFILED  {os.path.basename(p)}\n"
              f"          an ANSWER filed in {actual}/ (the ASK queue) — it reads as a new "
              f"obligation or is skipped; belongs in {expected}/")
        if args.fix:
            dest_dir = os.path.join(os.path.dirname(os.path.dirname(p)), expected)
            os.makedirs(dest_dir, exist_ok=True)
            dest = os.path.join(dest_dir, os.path.basename(p))
            if os.path.exists(dest):
                print(f"          NOT MOVED — {dest} already exists (resolve by hand)")
                continue
            shutil.move(p, dest)
            print(f"          -> moved to {expected}/")

    if unmeasured:
        return 2
    return 1 if misfiled else 0


if __name__ == "__main__":
    sys.exit(main())
