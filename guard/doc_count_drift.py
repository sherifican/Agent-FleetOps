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

# A collector that could not import part of a suite still prints a total, and that total
# is a floor, not a count. Measuring `tui/tests` without `textual` installed reports 326
# where the suite holds 364 — so comparing prose against it would flag a CORRECT number
# as drift. A partial read is not a smaller reading; it is a different question answered.
SKIP_DEPS = "the suite's own test dependencies are not installed here"


def _collect(root, rel):
    """(count, note). count is None whenever the number would be a floor, not a total."""
    try:
        p = subprocess.run(
            [sys.executable, "-m", "pytest", rel, "--collect-only", "-q"],
            cwd=root, capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.SubprocessError):
        return None, "the collector could not be run"
    out = p.stdout + p.stderr
    if not os.path.isdir(os.path.join(root, rel)):
        return None, f"{rel} is not present"
    if "ModuleNotFoundError" in out or "ImportError" in out:
        return None, SKIP_DEPS
    if re.search(r"\b\d+\s+errors?\b", out):
        return None, "the collector reported errors, so its total is a floor"
    m = re.search(r"(\d+)\s+tests?\s+collected", out)
    if not m:
        return None, "the collector printed no total"
    return int(m.group(1)), None


def measure_guard_suite(root):
    """Tests collected from guard/tests/ — the collector run_guards.sh itself runs."""
    return _collect(root, os.path.join("guard", "tests"))


def measure_tui_suite(root):
    return _collect(root, os.path.join("tui", "tests"))


def _bench_rows(root):
    path = os.path.join(root, "bench", "local_model_throughput.csv")
    try:
        with open(path, newline="", encoding="utf-8") as fh:
            return list(csv.DictReader(fh))
    except (OSError, csv.Error):
        return None


def measure_bench_rows(root):
    rows = _bench_rows(root)
    return (None, "the operating log could not be read") if rows is None else (len(rows), None)


def measure_bench_tags(root):
    rows = _bench_rows(root)
    if rows is None:
        return None, "the operating log could not be read"
    return len({r["model"] for r in rows if r.get("model")}), None


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


def _claims_tui_suite(line):
    return [int(m.group(1)) for m in
            re.finditer(r"(\d+)[- ]test hermetic suite", line, re.I)]


# (name, instrument, claim-finder, canonical phrasing). The fourth entry exists so the
# selftest can plant a claim for EVERY check from the registry itself. A fixture that
# hand-lists three of four counts passes on a machine where the fourth is skipped and
# fails on one where it is not — the fixture has to track the registry, not a memory of it.
CHECKS = [
    ("guard unit suite", measure_guard_suite, _claims_guard_suite,
     lambda n: f"{n} hermetic unit gates"),
    ("bench measurement rows", measure_bench_rows, _claims_bench_rows,
     lambda n: f"{n} measurements"),
    ("bench model tags", measure_bench_tags, _claims_bench_tags,
     lambda n: f"{n} model tags"),
    ("fleet-tui suite", measure_tui_suite, _claims_tui_suite,
     lambda n: f"behind a {n}-test hermetic suite"),
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

    `measured` maps a check name to its value, letting the selftest point the doc sweep
    at a planted tree while still comparing against real numbers — a fixture supplying
    its own expected count would be checking the fixture, not the guard.
    """
    docs = []
    for rel in _docs(root):
        full = os.path.join(root, rel)
        if not os.path.isfile(full):
            continue
        try:
            with open(full, encoding="utf-8", errors="replace") as fh:
                docs.append((rel, fh.read().splitlines()))
        except OSError:
            continue

    lines, worst, verified = [], 0, 0
    for name, instrument, claimer, _plant in CHECKS:
        if measured and name in measured:
            n, note = measured[name]          # (value, note), same shape an instrument returns
        else:
            n, note = instrument(root)

        if n is None:
            if note == SKIP_DEPS:
                lines.append(f"   skipped     {name}: {note} "
                             f"(NOT CONFIGURED — not counted as a pass)")
            else:
                lines.append(f"   UNMEASURED  {name}: {note}")
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

        verified += 1
        bad = [s for s in sites if s[2] != n]
        lines.append(f"   {'DRIFT     ' if bad else 'ok        '}  {name}: measured {n} · "
                     f"{len(sites)} claim(s) in {len({s[0] for s in sites})} file(s)")
        for rel, i, c in sites:
            if c != n:
                lines.append(f"                  {rel}:{i} says {c}")
        if bad:
            worst = max(worst, 1)

    if verified == 0:
        worst = max(worst, 2)
        lines.append("   UNMEASURED  no check verified anything — a sweep that compared "
                     "nothing reports the same green as a real one")

    head = {0: f"every documented count matches its instrument ({verified} checked)",
            1: "documented counts disagree with what was measured — the prose is stale",
            2: "UNMEASURED — a count could not be checked; worse than a violation"}[worst]
    return worst, [head] + lines


def _selftest():
    import tempfile
    import pathlib
    failures = []

    def case(name, ok):
        print(f"   {'ok  ' if ok else 'FAIL'}  {name}")
        if not ok:
            failures.append(name)

    real, notes = {}, {}
    for name, fn, _c, _p in CHECKS:
        real[name], notes[name] = fn(ROOT)

    # Replay each instrument's real answer — value AND note — so a check that legitimately
    # skips here keeps skipping in the fixture instead of turning into a false UNMEASURED.
    sim = {name: (real[name], notes[name]) for name, _f, _c, _p in CHECKS}
    if all(v is None for v in real.values()):
        print(f"SELFTEST UNMEASURED: no instrument could be read: {notes}")
        return 2

    g = real["guard unit suite"]
    rows = real["bench measurement rows"]
    tags = real["bench model tags"]
    if g is None or rows is None or tags is None:
        print(f"SELFTEST UNMEASURED: a core instrument was unreadable: {notes}")
        return 2

    def doc_for(bump=None, delta=0, extra=""):
        """A doc asserting the correct count for every measurable check.

        `bump` names one check whose planted count is wrong by `delta`, so exactly one
        thing differs between the green case and each red one.
        """
        out = []
        for name, _f, _c, plant in CHECKS:
            v = real[name]
            if v is None:
                continue
            out.append(plant(v + delta if name == bump else v))
        return "\n".join(out) + "\n" + extra

    with tempfile.TemporaryDirectory() as td:
        doc = pathlib.Path(td) / "DOC.md"

        def rc_for(text, measured=None):
            doc.write_text(text)
            return check(td, measured=measured or sim)[0]

        case("every count correct passes (green)", rc_for(doc_for()) == 0)
        case("a wrong suite count is caught (red)",
             rc_for(doc_for("guard unit suite", +1)) == 1)
        case("a wrong bench row count is caught (red)",
             rc_for(doc_for("bench measurement rows", +1)) == 1)
        case("a wrong model-tag count is caught (red)",
             rc_for(doc_for("bench model tags", +1)) == 1)
        case("a table row is checked (red)",
             rc_for(doc_for(extra=f"| Total measurements | **{rows + 5}** |\n")) == 1)
        case("a line that RUNS the suite is checked (red)",
             rc_for(doc_for(extra=f"| gates | `pytest guard/tests/ -q` | {g + 7} tests |\n")) == 1)
        case("a changelog's per-change test count is not a suite claim",
             rc_for(doc_for(extra="this change adds 6 hermetic tests\n")) == 0)
        case("a tree asserting nothing is UNMEASURED, not a pass",
             rc_for("the suite is hermetic and needs no live fleet\n") == 2)
        case("one silent check makes the whole sweep UNMEASURED",
             rc_for(f"{g} hermetic unit gates\n{rows} measurements\n") == 2)

        # A skipped instrument must not be compared against. Forced here rather than
        # depending on whether this machine happens to have the optional deps.
        forced = dict(sim, **{"fleet-tui suite": (None, SKIP_DEPS)})
        case("a suite whose deps are absent is SKIPPED, never compared",
             rc_for(doc_for(extra="behind a 999-test hermetic suite\n"),
                    measured=forced) == 0)
        case("a skip does not hide a real drift elsewhere",
             rc_for(doc_for("guard unit suite", +1,
                            extra="behind a 999-test hermetic suite\n"),
                    measured=forced) == 1)

    case("the instruments that answered returned real values",
         all(v > 0 for v in real.values() if v is not None))

    if failures:
        print(f"SELFTEST FAILED ({len(failures)}): " + ", ".join(failures))
        return 1
    print("doc_count_drift selftest: goes red on every drifted count, stays quiet on counts "
          "about other things, skips a suite it cannot fully collect, and refuses to pass "
          "on a check that verified nothing")
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    code, report = check()
    print("doc count drift — " + report[0])
    for ln in report[1:]:
        print(ln)
    raise SystemExit(code)
