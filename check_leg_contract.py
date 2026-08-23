#!/usr/bin/env python3
"""check_leg_contract.py <result-file> [...] — validate a research leg against the output contract.

WHY. Contract drift was being caught by the ORCHESTRATOR at reconcile time, one video at a time, by
noticing a field was missing. That is an automation gap (see feedback-automation-does-its-own-job): a human
catch is a detector that should exist. Observed violations that motivated this, all from real runs:
  · invented action verbs — `TRY` (two different cloud legs, independently), `MONITOR`
  · §3 relevance ratings omitted ENTIRELY (one leg wrote prose subsections with no HIGH/MED/LOW)
  · `ADOPT` used for things the leg never ran — 6 videos running, one leg
  · project relevance over-rated (everything HIGH) — uncalibrated, not thorough

Exit codes:  0 = clean · 1 = violations found · 2 = file unreadable
Run standalone, or let dispatch_video_research.sh call it after the legs return.
"""
import re, sys, os

# ⚠ SIX buckets — must stay in lockstep with PROJECTS below and with §3b in ACTIONABLE_ADDENDUM.md.
# "Memory" was missing here while §3b asked legs to RATE Memory, so legs that used it as a bucket were
# flagged for being consistent. When a vocabulary lives in two places it drifts; this is the second time
# (see the TRY/MONITOR case). (fix 2026-07-30, caught by the watchdog.)
BUCKETS = {"Flagship-App", "Local-Models", "Fleet-Ops", "Tooling-Infra", "Research-Pipeline", "Memory"}
ACTIONS = {"GET", "ADOPT", "ADAPT", "VET", "EXPLORE", "WATCH", "REJECT"}
BANNED = {"TRY": "VET or EXPLORE", "MONITOR": "WATCH", "CONSIDER": "EXPLORE",
          "INVESTIGATE": "VET", "TBD": "pick one of the seven", "TEST": "VET"}
PRIOS = {"P0", "P1", "P2"}
PROJECTS = ["Local-Models", "Flagship-App", "Fleet-Ops", "Tooling-Infra", "Research-Pipeline", "Memory"]
RATINGS = {"HIGH", "MED", "LOW", "NONE"}


def check(path):
    try:
        t = open(path, encoding="utf-8", errors="replace").read()
    except OSError as e:
        return None, [f"unreadable: {e}"]
    v = []

    # --- §3b RELEVANCE lines -------------------------------------------------------------------------
    found = {}
    for m in re.finditer(r"^\s*RELEVANCE:\s*([A-Za-z-]+)\s*=\s*([A-Za-z]+)", t, re.M):
        proj, rat = m.group(1), m.group(2).upper()
        found[proj] = rat
        if rat not in RATINGS:
            v.append(f"RELEVANCE {proj}: '{rat}' is not one of HIGH/MED/LOW/NONE")
    missing = [p for p in PROJECTS if p not in found]
    if len(found) == 0:
        v.append("NO `RELEVANCE:` verdict lines at all — §3b omitted entirely")
    elif missing:
        v.append(f"RELEVANCE lines missing for: {', '.join(missing)}")
    if found and not (set(found.values()) & {"NONE", "LOW"}):
        v.append(f"every project rated {'/'.join(sorted(set(found.values())))} — uncalibrated "
                 f"(most videos are NONE/LOW for most projects)")

    # --- §5 table ------------------------------------------------------------------------------------
    lines, rows, hdr_found = t.splitlines(), 0, False
    for i, l in enumerate(lines):
        if not l.lstrip().startswith("|"):
            continue
        hdr = [c.strip().lower() for c in l.strip().strip("|").split("|")]

        def col(*names):
            for n in names:
                for k, c in enumerate(hdr):
                    if c.startswith(n):
                        return k
            return None
        ci, cb, ca, cp = col("item"), col("bucket"), col("action", "suggested"), col("prior")
        if ci is None or ca is None or ci == ca:
            continue
        hdr_found = True
        j = i + 2
        while j < len(lines) and lines[j].lstrip().startswith("|"):
            c = [x.strip().strip("*` ") for x in lines[j].strip().strip("|").split("|")]

            def g(k):
                return c[k] if k is not None and k < len(c) else ""
            item = g(ci)
            if item and not set(item) <= set(":- "):
                rows += 1
                lbl = item[:38]
                a = g(ca).upper().strip()
                a_clean = re.sub(r"[^A-Z/]", "", a)
                if a_clean not in ACTIONS:
                    hit = next((b for b in BANNED if b in a_clean), None)
                    if hit:
                        v.append(f"row '{lbl}': INVENTED VERB '{hit}' — use {BANNED[hit]}")
                    elif a_clean:
                        v.append(f"row '{lbl}': action '{a}' not in the closed vocabulary")
                    else:
                        v.append(f"row '{lbl}': no action")
                if cb is not None:
                    b = g(cb).strip()
                    if b and b not in BUCKETS:
                        v.append(f"row '{lbl}': bucket '{b}' is not one of the six exact strings")
                    elif not b:
                        v.append(f"row '{lbl}': no bucket")
                if cp is not None:
                    p = re.sub(r"[^P012]", "", g(cp).upper())
                    if p and p not in PRIOS:
                        v.append(f"row '{lbl}': priority '{g(cp)}' is not P0/P1/P2")
            j += 1
        break
    if not hdr_found and "NONE —" not in t and "NONE -" not in t:
        v.append("NO §5 actionable table found (and no explicit 'NONE — <why>')")
    return rows, v


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    bad = 0
    for p in sys.argv[1:]:
        rows, v = check(p)
        name = os.path.basename(p)
        if v:
            bad = 1
            print(f"⚠ {name}  ({rows if rows is not None else '?'} §5 rows) — {len(v)} violation(s)")
            for x in v:
                print(f"    · {x}")
        else:
            print(f"✓ {name}  ({rows} §5 rows) — contract clean")
    sys.exit(bad)


if __name__ == "__main__":
    main()
