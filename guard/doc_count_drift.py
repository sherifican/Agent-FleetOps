#!/usr/bin/env python3
"""Pin counts written into prose to the thing that can actually be counted.

A number in a sentence is a copy of a measurement, and copies decay independently of
what they describe. This repository shipped three surfaces reading 170, 218 and 309
hermetic unit gates at the same time, because whoever corrected one corrected only the
one they were looking at; the same week, two surfaces still described a 55-row operating
log that had grown to 67. Every one of those numbers was right when it was typed. None
of them had an instrument.

So each documented count gets one. A CHECK pairs a live measurement with the narrow
phrasings that assert it, and every assertion found is compared against the measurement.

Exit codes follow the runner's vocabulary:

  0  every documented count matches its instrument
  1  a documented count disagrees  (drift — the thing this exists to catch)
  2  UNMEASURED — an instrument could not be read, or a check found nothing to check

That last clause is the load the rest of this file carries. A checker that scans for a
pattern nothing matches prints a clean sweep, and a clean sweep is indistinguishable
from a real one. If prose is reworded so a pattern stops matching, this guard silently
stops being able to fail — so finding zero claim sites is reported as UNMEASURED, which
the runner treats as worse than a violation, rather than as a pass.

Breadth is not rigour, either. The first cut matched any "<n> hermetic tests" and
immediately flagged a changelog line reading "6 hermetic tests" — a true sentence about
how many tests one change added. A guard that reports true sentences as drift gets
muted, and a muted guard catches nothing, so the patterns below name their subject.
"""

import csv
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ---------------------------------------------------------------- instruments

def measure_guard_suite(root):
    """Tests collected from guard/tests/ — the collector run_guards.sh itself runs."""
    try:
        p = subprocess.run(
            [sys.executable, "-m", "pytest", os.path.join("guard", "tests"),
             "--collect-only", "-q"],
            cwd=root, capture_output=True, text=True, timeout=180)
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"(\d+)\s+tests?\s+collected", p.stdout)
    return int(m.group(1)) if m else None


def _bench_rows(root):
    path = os.path.join(root, "bench", "local_model_throughput.csv")
    try:
        with open(path, newline="", encoding="utf-8") as fh:
            return list(csv.DictReader(fh))
    except (OSError, csv.Error):
        return None


def measure_bench_rows(root):
    rows = _bench_rows(root)
    return None if rows is None else len(rows)


def measure_bench_tags(root):
    rows = _bench_rows(root)
    if rows is None:
        return None
    return len({r["model"] for r in rows if r.get("model")})


# ---------------------------------------------------------------- claim sites

def _claims_guard_suite(line):
    """A count of THIS suite: the repo's own term for it, or a line that runs it."""
    out = [int(m.group(1)) for m in
           re.finditer(r"(\d+)\s+hermetic\s+unit\s+gates?", line, re.I)]
    if not out and "guard/tests" in line:
        out = [int(m.group(1)) for m in re.finditer(r"(\d+)\s+tests?\b", line, re.I)]
    return out


def _claims_bench_rows(line):
    return ([int(m.group(1)) for m in
             re.finditer(r"(\d+)\s+measurements\b", line, re.I)] +
            [int(m.group(1)) for m in
             re.finditer(r"\|\s*Total measurements\s*\|\s*\*{0,2}(\d+)\*{0,2}\s*\|", line, re.I)])


def _claims_bench_tags(line):
    return [int(m.group(1)) for m in re.finditer(r"(\d+)\s+model tags\b", line, re.I)]


CHECKS = [
    ("guard unit suite", measure_guard_suite, _claims_guard_suite),
    ("bench measurement rows", measure_bench_rows, _claims_bench_rows),
    ("bench model tags", measure_bench_tags, _claims_bench_tags),
]


# ---------------------------------------------------------------- the sweep

def _docs(root):
    try:
        out = subprocess.run(["git", "ls-files", "-z", "*.md"], cwd=root,
                             capture_output=True, text=True, timeout=60)
        if out.returncode == 0 and out.stdout.strip("\0"):
            return [f for f in out.stdout.split("\0") if f]
    except (OSError, subprocess.SubprocessError):
        pass
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        for fn in filenames:
            if fn.endswith(".md"):
                found.append(os.path.relpath(os.path.join(dirpath, fn), root))
    return sorted(found)


def check(root=ROOT, measured=None):
    """Return (exit_code, report_lines).

    `measured` maps a check name to its value. It is a parameter rather than an
    unconditional call so the selftest can point the doc sweep at a planted tree while
    still comparing against real numbers — a fixture supplying its own expected count
    would be checking the fixture, not the guard.
    """
    docs = [(rel, open(os.path.join(root, rel), encoding="utf-8", errors="replace").read()
             .splitlines()) for rel in _docs(root)
            if os.path.isfile(os.path.join(root, rel))]

    lines, worst = [], 0
    for name, instrument, claimer in CHECKS:
        n = (measured or {}).get(name)
        if n is None:
            n = instrument(root)
        if n is None:
            lines.append(f"   UNMEASURED  {name}: instrument could not be read")
            worst = max(worst, 2)
            continue

        sites = [(rel, i, c)
                 for rel, body in docs
                 for i, line in enumerate(body, 1)
                 for c in claimer(line)]
        if not sites:
            lines.append(f"   UNMEASURED  {name}: measured {n}, but no documented count "
                         f"matched any known phrasing — this check verified nothing")
            worst = max(worst, 2)
            continue

        bad = [s for s in sites if s[2] != n]
        lines.append(f"   {'DRIFT     ' if bad else 'ok        '}  {name}: measured {n} · "
                     f"{len(sites)} claim(s) in {len({s[0] for s in sites})} file(s)")
        for rel, i, c in sites:
            if c != n:
                lines.append(f"                  {rel}:{i} says {c}")
        if bad:
            worst = max(worst, 1)

    head = {0: "every documented count matches its instrument",
            1: "documented counts disagree with what was measured — the prose is stale",
            2: "UNMEASURED — a count could not be checked; worse than a violation"}[worst]
    return worst, [head] + lines


# ---------------------------------------------------------------- selftest

def _selftest():
    import tempfile
    import pathlib
    failures = []

    def case(name, ok):
        print(f"   {'ok  ' if ok else 'FAIL'}  {name}")
        if not ok:
            failures.append(name)

    real = {name: fn(ROOT) for name, fn, _ in CHECKS}
    if any(v is None for v in real.values()):
        print(f"SELFTEST UNMEASURED: an instrument could not be read: {real}")
        return 2

    g = real["guard unit suite"]
    rows = real["bench measurement rows"]
    tags = real["bench model tags"]

    with tempfile.TemporaryDirectory() as td:
        doc = pathlib.Path(td) / "DOC.md"

        def rc_for(text):
            doc.write_text(text)
            return check(td, measured=real)[0]

        allthree = ("{g} hermetic unit gates\n{r} measurements\n{t} model tags\n")

        case("every count correct passes (green)",
             rc_for(allthree.format(g=g, r=rows, t=tags)) == 0)
        case("a wrong suite count is caught (red)",
             rc_for(allthree.format(g=g + 1, r=rows, t=tags)) == 1)
        case("a wrong bench row count is caught (red)",
             rc_for(allthree.format(g=g, r=rows + 1, t=tags)) == 1)
        case("a wrong model-tag count is caught (red)",
             rc_for(allthree.format(g=g, r=rows, t=tags + 1)) == 1)
        case("a table row is checked (red)",
             rc_for(f"{g} hermetic unit gates\n| Total measurements | **{rows + 5}** |\n"
                    f"{tags} model tags\n") == 1)
        case("a line that RUNS the suite is checked (red)",
             rc_for(f"| gates | `pytest guard/tests/ -q` | {g + 7} tests |\n"
                    f"{rows} measurements\n{tags} model tags\n") == 1)
        case("a changelog's per-change test count is not read as a suite claim",
             rc_for(f"this change adds 6 hermetic tests\n{g} hermetic unit gates\n"
                    f"{rows} measurements\n{tags} model tags\n") == 0)
        case("a tree asserting nothing is UNMEASURED, not a pass",
             rc_for("the suite is hermetic and needs no live fleet\n") == 2)
        case("one silent check makes the whole sweep UNMEASURED",
             rc_for(f"{g} hermetic unit gates\n{rows} measurements\n") == 2)

    case("the instruments returned real values",
         all(isinstance(v, int) and v > 0 for v in real.values()))

    if failures:
        print(f"SELFTEST FAILED ({len(failures)}): " + ", ".join(failures))
        return 1
    print("doc_count_drift selftest: goes red on every drifted count, stays quiet on "
          "counts about other things, and refuses to pass on a check that verified nothing")
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    code, report = check()
    print("doc count drift — " + report[0])
    for ln in report[1:]:
        print(ln)
    raise SystemExit(code)
