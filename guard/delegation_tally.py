#!/usr/bin/env python3
"""delegation_tally.py — read out the D20 measurement. Answers "is HARD DELEGATE wrong, or is the practice?"

Backlog D20. The rule under measurement says the orchestrating agent does ONLY orchestration and
dispatch, and hands the rest to someone else. An audit found ~17/24 commits with no
delegation trace — but "no trace" != "not delegated" while no trace convention exists, so BOTH readings
survived the same evidence. That unfalsifiability was the defect. From 2026-08-03 a commit-msg hook requires
one trailer per commit, so the question becomes answerable.

★ COMMITS FROM BEFORE THE HOOK ARE REPORTED SEPARATELY AND NEVER IMPUTED. Counting an untagged pre-hook
commit as "direct" would manufacture the datum this exists to collect — the same defect as a dry run that
writes the state it claims to preview. They are shown as UNTAGGED (pre-measurement) with a count.

Usage: delegation_tally.py [--since 2026-08-03] [--repo /path]
"""
import collections, re, subprocess, sys

HOOK_START = "2026-08-03"          # the day the commit-msg hook went in; nothing before it is attributable
# `Authored-directly` is the tool-neutral name the hook asks for. The third alternative is the
# name that trailer carried before 2026-08-30 and is kept for READING history only — dropping it
# would silently re-bucket every earlier commit as untagged, which is a fabricated datum.
TRAILER = re.compile(r"^(Delegated-to|Authored-directly|Claude-direct):\s*(.+?)\s*$", re.M)


def main():
    since = HOOK_START
    repo = "."
    for i, a in enumerate(sys.argv):
        if a == "--since" and i + 1 < len(sys.argv):
            since = sys.argv[i + 1]
        if a == "--repo" and i + 1 < len(sys.argv):
            repo = sys.argv[i + 1]

    # ⚠ A BARE `--since=YYYY-MM-DD` IS A SILENT UNDER-COUNTER. git's approxidate resolves it to that date at
    # the CURRENT TIME OF DAY, not midnight — so on 2026-08-03 at 21:51 it excluded a commit made at 21:48
    # and the tally read "no commits", i.e. it would have permanently dropped everything committed before
    # 21:51 on day one and called the result zero. Pin midnight explicitly.
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", since):
        since += " 00:00:00"
    log = subprocess.run(["git", "-C", repo, "log", f"--since={since}", "--format=%H%x00%B%x1e"],
                         capture_output=True, text=True)
    if log.returncode != 0:
        print("2 UNMEASURED: could not read the git log", file=sys.stderr)
        return 2

    delegated, direct, untagged = collections.Counter(), collections.Counter(), 0
    total = 0
    for rec in log.stdout.split("\x1e"):
        if "\x00" not in rec:
            continue
        total += 1
        body = rec.split("\x00", 1)[1]
        found = TRAILER.findall(body)
        if not found:
            untagged += 1
            continue
        for kind, val in found:
            (delegated if kind == "Delegated-to" else direct)[val] += 1

    if not total:
        print(f"no commits since {since} — nothing measured yet (this is not a result)")
        return 0

    tagged = total - untagged
    print(f"D20 delegation measurement — {repo.split('/')[-1]}, since {since}")
    print(f"  commits: {total}   tagged: {tagged}   UNTAGGED: {untagged}")
    if untagged:
        print(f"    ⚠ {untagged} untagged — these predate the hook (or bypassed it). NOT counted as either;"
              f" imputing them would fabricate the measurement.")
    d_total, c_total = sum(delegated.values()), sum(direct.values())
    if tagged:
        print(f"\n  DELEGATED {d_total}/{tagged} ({100*d_total//tagged}%)   "
              f"AUTHORED-DIRECTLY {c_total}/{tagged} ({100*c_total//tagged}%)")
    for label, counter in (("Delegated-to", delegated), ("Authored-directly", direct)):
        if counter:
            print(f"\n  {label}:")
            for val, n in counter.most_common():
                print(f"    {n:3}  {val}")
    print("\n  Read this as: which carve-outs are REAL and critical, and is the 'ONLY orchestration'")
    print("  wording defensible? A carve-out used once is noise; one used constantly is the rule as lived.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
