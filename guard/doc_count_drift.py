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

def _claims_guard_suite(line, rel):
    """A count of THIS suite: the repo's own term for it, or a line that runs it."""
    out = [int(m.group(1)) for m in
           re.finditer(r"(\d+)\s+hermetic\s+unit\s+gates?", line, re.I)]
    if not out and "guard/tests" in line:
        out = [int(m.group(1)) for m in re.finditer(r"(\d+)\s+tests?\b", line, re.I)]
    return out


def _claims_bench_rows(line, rel):
    return ([int(m.group(1)) for m in
             re.finditer(r"(\d+)\s+measure(?:ments|d rows)\b", line, re.I)] +
            [int(m.group(1)) for m in
             re.finditer(r"\|\s*Total measurements\s*\|\s*\*{0,2}(\d+)\*{0,2}\s*\|", line, re.I)])


def _claims_bench_tags(line, rel):
    return [int(m.group(1)) for m in re.finditer(r"(\d+)\s+model tags\b", line, re.I)]


def _claims_tui_suite(line, rel):
    return [int(m.group(1)) for m in
            re.finditer(r"(\d+)[- ]test hermetic suite", line, re.I)]


def _count_files(root, rel_dir, pred):
    d = os.path.join(root, rel_dir)
    if not os.path.isdir(d):
        return None, f"{rel_dir} is not present"
    return len([f for f in os.listdir(d) if pred(f)]), None


def measure_protocol_specs(root):
    return _count_files(root, "specs", lambda f: f.endswith(".md"))


def measure_skills(root):
    d = os.path.join(root, "skills")
    if not os.path.isdir(d):
        return None, "skills/ is not present"
    return len([s for s in os.listdir(d)
                if os.path.isfile(os.path.join(d, s, "SKILL.md"))]), None


def measure_adoption_steps(root):
    return _count_files(root, "adopt",
                        lambda f: re.match(r"^\d+_.*\.md$", f) is not None)


def measure_guards(root):
    """Modules that can actually go red in a run: what the runner invokes, plus the
    script-guards the mutation harness drives. Counting `guard/*.py` instead would count
    shared helpers that no run can turn red, which is not what the banner claims."""
    rg = os.path.join(root, "guard", "run_guards.sh")
    mh = os.path.join(root, "guard", "mutation_harness.py")
    try:
        with open(rg, encoding="utf-8") as fh:
            invoked = set(re.findall(r"python3 guard/([a-z_]+)\.py", fh.read()))
        with open(mh, encoding="utf-8") as fh:
            scripts = set(re.findall(r'\("guard/(test_[a-z_]+)\.py"', fh.read()))
    except OSError:
        return None, "the runner or the mutation harness could not be read"
    if not invoked:
        return None, "no invocations found in the runner — the pattern may have gone stale"
    return len(invoked | scripts), None


def measure_repo_tests(root):
    """Both hermetic suites. Skipped whenever either half cannot be fully collected."""
    g, gn = measure_guard_suite(root)
    u, un = measure_tui_suite(root)
    if g is None:
        return None, gn
    if u is None:
        return None, un
    return g + u, None


# These labels are ordinary English, so they are read only where the number OPENS the
# statement — the shape a banner stat has, and the shape a passing mention does not.
# Unanchored, "<n> guards" matched a quoted anecdote about a DIFFERENT system's harness
# ("28/28 guards have teeth") and reported it as this repo's guard count drifting.
def _stat(label):
    pat = re.compile(rf"^\s*(\d+)\s+{label}\b", re.I)

    def finder(line, rel):
        m = pat.match(line)
        return [int(m.group(1))] if m else []
    return finder


_claims_protocol_specs = _stat("protocol specs")
_claims_skills = _stat("skills")
_claims_adoption_steps = _stat("adoption steps")
_claims_guards = _stat("guards")


def _claims_repo_tests(line, rel):
    # "<n> tests" is far too common in prose to match everywhere, so this claim is read
    # only off the banner, where the label is the whole sentence.
    if not rel.endswith(".svg"):
        return []
    return _stat("tests")(line, rel)


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
    ("protocol specs", measure_protocol_specs, _claims_protocol_specs,
     lambda n: f"{n} protocol specs"),
    ("guards that can go red", measure_guards, _claims_guards,
     lambda n: f"{n} guards"),
    ("skills", measure_skills, _claims_skills, lambda n: f"{n} skills"),
    ("adoption steps", measure_adoption_steps, _claims_adoption_steps,
     lambda n: f"{n} adoption steps"),
    ("both hermetic suites", measure_repo_tests, _claims_repo_tests,
     lambda n: f"{n} tests"),
]


# ---------------------------------------------------------------- the sweep

TEXT_NODE = re.compile(r">([^<>]+)</text>")


def _svg_lines(text):
    """An SVG banner states a count in one <text> node and its subject in the next.

    Neither element is a sentence, so a line-oriented sweep reads them as two unrelated
    fragments and matches nothing — which is how the most public surface in the repo went
    unchecked while every number on it drifted. Pairing consecutive nodes reconstructs the
    claim the reader actually sees.
    """
    nodes = [n.strip() for n in TEXT_NODE.findall(text)]
    return [f"{a} {b}" for a, b in zip(nodes, nodes[1:])]


def _docs(root):
    try:
        out = subprocess.run(["git", "ls-files", "-z", "*.md", "*.svg"], cwd=root,
                             capture_output=True, text=True, timeout=60)
        if out.returncode == 0 and out.stdout.strip("\0"):
            return [f for f in out.stdout.split("\0") if f]
    except (OSError, subprocess.SubprocessError):
        pass
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        for fn in filenames:
            if fn.endswith((".md", ".svg")):
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
                body = fh.read()
        except OSError:
            continue
        docs.append((rel, _svg_lines(body) if rel.endswith(".svg") else body.splitlines()))

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
                 for c in claimer(line, rel)]
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
        svg = pathlib.Path(td) / "DOC.svg"

        def rc_for(text, measured=None):
            """Plant the same claims in both shapes a real surface uses.

            Some claims are read only off a banner, so a fixture that wrote prose alone
            left those checks with nothing to compare and turned every case UNMEASURED —
            on a machine where they could run. The SVG mirrors the banner: the count in
            one text node, its subject in the next.
            """
            doc.write_text(text)
            nodes = []
            for line in text.splitlines():
                m = re.match(r"^\s*(\d+)\s+(.*)$", line)
                if m:
                    nodes.append(f"<text>{m.group(1)}</text>")
                    nodes.append(f"<text>{m.group(2)}</text>")
            svg.write_text("<svg>" + "".join(nodes) + "</svg>")
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
