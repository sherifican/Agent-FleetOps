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

GUARD-CLASS: guard — a documented count that no longer matches its instrument must go red
"""

import csv
import json
import os
import re
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ---------------------------------------------------------------- instruments

# A collector that could not import part of a suite still prints a total, and that total
# is a floor, not a count. Measuring a suite whose dependencies are absent silently omits
# every test in the modules that failed to import, so comparing prose against that partial
# collection would flag a CORRECT number as drift. A partial read is not a smaller reading;
# it is a different question answered. Deliberately no integers here: an illustrative count
# in a comment goes stale exactly like the prose this file exists to catch.
SKIP_DEPS = "the suite's own test dependencies are not installed here"


class DependencySkip(str):
    """Preserve the skip marker API while carrying the measured missing names."""

    def __new__(cls, modules):
        note = super().__new__(cls, SKIP_DEPS)
        note.modules = tuple(sorted(set(modules)))
        return note


# Keep pytest session state and exception evidence separate from captured output.
# Neither stdout nor stderr participates in the count decision.
_COLLECT_PROBE = r"""
import importlib.util, json, sys
from pathlib import Path
import pytest
class Receipt:
    def __init__(self):
        self.failed = set()
        self.causes = {}
        self.collected = None
        self.deselected = 0
        self.collection_finished = False
    def pytest_deselected(self, items):
        self.deselected += len(items)
    def pytest_collection_finish(self, session):
        self.collection_finished = True
    def pytest_sessionfinish(self, session, exitstatus):
        self.collected = session.testscollected
    def pytest_collectreport(self, report):
        if report.failed:
            self.failed.add(report.nodeid)
    def pytest_exception_interact(self, node, call, report):
        if isinstance(report, pytest.CollectReport):
            exc = call.excinfo.value
            # Pytest wraps import failures in its own collection exception.
            if type(exc) is pytest.Collector.CollectError:
                exc = exc.__cause__
            name = exc.name if type(exc) is ModuleNotFoundError else None
            absent = False
            if isinstance(name, str) and name:
                try:
                    # A top-level lookup does not import a parent package.
                    absent = importlib.util.find_spec(name.split('.')[0]) is None
                except (ImportError, ValueError, AttributeError):
                    pass  # an indeterminate lookup is not evidence of absence
            self.causes[report.nodeid] = {'name': name, 'absent': absent}
receipt = Receipt()
status = pytest.main(sys.argv[2:], plugins=[receipt])
Path(sys.argv[1]).write_text(json.dumps(
    {'collected': receipt.collected, 'deselected': receipt.deselected,
     'collection_finished': receipt.collection_finished,
     'failures': [receipt.causes.get(node) for node in sorted(receipt.failed)]}),
    encoding='utf-8')
sys.exit(status)
"""


def _declared_imports(root, rel):
    """Read only the suite project's runtime/dev and requirements declarations.

    Distribution spelling maps to a lowercase import with runs of '-', '.', '_'
    replaced by '_'. This covers this repository's declarations, not arbitrary
    distribution/import aliases or undeclared transitive dependencies. Markers,
    extras and version constraints do not change this name-only inventory.
    """
    project = Path(root) / Path(rel).parent
    requirements = []
    try:
        pyproject = project / 'pyproject.toml'
        if pyproject.is_file():
            data = tomllib.loads(pyproject.read_text(encoding='utf-8')).get('project', {})
            requirements.extend(data.get('dependencies', []))
            requirements.extend(data.get('optional-dependencies', {}).get('dev', []))
        reqfile = project / 'requirements.txt'
        if reqfile.is_file():
            requirements.extend(line.split('#', 1)[0].strip()
                                for line in reqfile.read_text(encoding='utf-8').splitlines()
                                if line.split('#', 1)[0].strip())
        names = set()
        for requirement in requirements:
            match = re.match(r"([A-Za-z0-9][A-Za-z0-9._-]*)(?=\s*(?:\[|[<>=!~;@]|$))",
                             requirement)
            if not match:
                return set()  # unsupported declarations cannot authorize a skip
            names.add(re.sub(r"[-_.]+", "_", match[1]).lower())
        return names
    except (OSError, ValueError, TypeError, AttributeError):
        return set()


def _external_missing_modules(root, causes, declared):
    """Require declared names, child-measured absence and no repository ownership."""
    if not isinstance(causes, list) or not causes:
        return False
    tops = set()
    for cause in causes:
        if not isinstance(cause, dict):
            return False
        name = cause.get('name')
        if not isinstance(name, str) or not re.fullmatch(r"[\w]+(?:\.[\w]+)*", name):
            return False
        if name not in declared or cause.get('absent') is not True:
            return False
        tops.add(name.split('.')[0])
    # Include untracked source in flat/src/nested layouts, but prune conventional
    # environment roots and roots carrying venv/conda markers. Never follow links.
    def environment(path):
        return (path.name in {'.venv', 'venv', '.env', 'env', 'site-packages', 'dist-packages'}
                or (path / 'pyvenv.cfg').is_file() or (path / 'conda-meta').is_dir())

    def unreadable(error):
        raise error

    try:
        for directory, dirs, files in os.walk(root, onerror=unreadable):
            dirs[:] = [d for d in dirs if d not in ('.git', '__pycache__', '.pytest_cache')
                       and not environment(Path(directory) / d)]
            if tops.intersection(dirs) or any(top + '.py' in files for top in tops):
                return False
    except OSError:
        return False
    return True


def _collect(root, rel):
    """(count, note). Counts require success; deselection is explicitly unmeasured."""
    evidence = None
    try:
        with tempfile.TemporaryDirectory(prefix="count-receipt-") as directory:
            receipt = Path(directory) / "collection.json"
            p = subprocess.run(
                [sys.executable, "-c", _COLLECT_PROBE, str(receipt), rel,
                 "--collect-only", "-q", "--color=no"],
                cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=300,
                env=dict(os.environ, PYTEST_ADDOPTS=""))
            if receipt.is_file():
                evidence = json.loads(receipt.read_text(encoding="utf-8"))
    except (OSError, subprocess.SubprocessError, ValueError):
        return None, "the collector could not be run or its collection receipt could not be read"
    if not os.path.isdir(os.path.join(root, rel)):
        return None, f"{rel} is not present"
    # The child receipt is a trusted pytest observation, not authenticated data.
    # Reject absent/malformed observations even if stdout claims a valid count.
    if (not isinstance(evidence, dict)
            or type(evidence.get('collected')) is not int
            or evidence['collected'] < 0
            or type(evidence.get('deselected')) is not int
            or evidence['deselected'] < 0
            or type(evidence.get('collection_finished')) is not bool
            or not isinstance(evidence.get('failures'), list)):
        return None, "the collector supplied no valid collection receipt"
    causes = evidence['failures']
    if p.returncode == 5:
        if evidence['deselected']:
            return None, "the collector reported deselection; the suite total is unmeasured"
        return None, "the collector collected no tests (pytest exit status 5)"
    if p.returncode != 0 or causes:
        if p.returncode in (1, 2) and _external_missing_modules(root, causes, _declared_imports(root, rel)):
            return None, DependencySkip(cause['name'] for cause in causes)
        return None, "the collector reported errors, so its total is a floor"
    if not evidence['collection_finished']:
        return None, "the collector did not finish collection"
    if evidence['deselected']:
        return None, "the collector reported deselection; the suite total is unmeasured"
    return evidence['collected'], None


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
    if not out and rel.endswith(".svg"):
        m = re.match(r"^\s*(\d+)\s+guard\b", line, re.I)
        if m:
            out = [int(m.group(1))]
    return out


def _claims_bench_rows(line, rel):
    return ([int(m.group(1)) for m in
             re.finditer(r"(\d+)\s+measure(?:ments|d rows)\b", line, re.I)] +
            [int(m.group(1)) for m in
             re.finditer(r"\|\s*Total measurements\s*\|\s*\*{0,2}(\d+)\*{0,2}\s*\|", line, re.I)])


def _claims_bench_tags(line, rel):
    return [int(m.group(1)) for m in re.finditer(r"(\d+)\s+model tags\b", line, re.I)]


def _claims_tui_suite(line, rel):
    out = [int(m.group(1)) for m in
           re.finditer(r"(\d+)[- ]test hermetic suite", line, re.I)]
    # The adoption guides publish a pytest result rather than a suite label, and two of them
    # publish the CURRENT acceptance result in different words. Scope the alternate phrasing to
    # those guides' VERIFY assertion, so a historical result elsewhere in the tree is still not
    # read as a claim about the current TUI suite.
    if rel in ("adopt/10_tui.md", "adopt/90_verify_all.md") and \
            "**VERIFY — expected output:**" in line:
        out.extend(int(m.group(1)) for m in
                   re.finditer(r"reports `(\d+) passed`", line, re.I))
    return out


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
    return [pair for _line, pair in _svg_numbered(text)]


def _svg_numbered(text):
    """[(source line, pair string)] — the pairs `_svg_lines` returns, each tagged with the
    SOURCE line of its first node, the one carrying the number.

    Reports used to cite the pair INDEX ("docs/banner.svg:19") where the number
    actually sat on source line 56, so the line the reviewer was sent to did not exist.
    """
    found = [(text.count("\n", 0, m.start(1)) + 1, m.group(1).strip())
             for m in TEXT_NODE.finditer(text)]
    return [(line_a, f"{a} {b}") for (line_a, a), (_line_b, b) in zip(found, found[1:])]


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


def check(root=ROOT, measured=None, release=False):
    """Return (exit_code, report_lines).

    `measured` maps a check name to its value, letting the selftest point the doc sweep
    at a planted tree while still comparing against real numbers — a fixture supplying
    its own expected count would be checking the fixture, not the guard.

    `release` makes count coverage MANDATORY: an instrument that
    could not be read because the suite's dependencies are absent (SKIP_DEPS) is, in
    ordinary mode, a skip — optional, disclosed, never counted as a pass. Before a release
    that is not good enough: the banner's combined total was left unverified by a run that
    printed a green seven-check summary, because the one instrument behind that claim had
    been skipped. In release mode a skipped instrument with documented claim sites is
    UNMEASURED (2), and every affected claim site is listed so the reviewer can see exactly
    which published numbers nobody measured. Claim sites are computed BEFORE the skip
    disposition so that listing is possible; a check with zero claim sites stays UNMEASURED
    in both modes, as before.

    Inventory note: `_docs()` takes its filenames from `git ls-files`, so release mode
    measures the STAGED/COMMITTED snapshot's file list against WORKING-TREE bytes. Stage
    every intended document before trusting a release-mode verdict; an untracked document
    is outside the inventory and a clean verdict says nothing about it.
    """
    docs, unreadable = [], []
    for rel in _docs(root):
        full = os.path.join(root, rel)
        # A document the inventory lists but the run cannot read is NOT silently
        # dropped — its claim sites vanish from the sweep, and a vanished site cannot drift.
        # It is UNMEASURED in both modes, named by path.
        if not os.path.isfile(full):
            unreadable.append((rel, "listed by the inventory but missing from the working tree"))
            continue
        try:
            with open(full, encoding="utf-8", errors="replace") as fh:
                body = fh.read()
        except OSError as exc:
            unreadable.append((rel, "could not be read: %s" % (exc.strerror or exc)))
            continue
        # Each document is a list of (source line, text). For an SVG the text is a reconstructed
        # node pair and the line is where its number node sits in the file (R5).
        docs.append((rel, _svg_numbered(body) if rel.endswith(".svg")
                     else list(enumerate(body.splitlines(), 1))))

    lines, worst, verified, skipped = [], 0, 0, 0
    for rel, why in unreadable:
        lines.append(f"   UNMEASURED  document {rel}: {why} — any count it documents was not "
                     f"checked")
        worst = max(worst, 2)
    for name, instrument, claimer, _plant in CHECKS:
        if measured and name in measured:
            n, note = measured[name]          # (value, note), same shape an instrument returns
        else:
            n, note = instrument(root)

        # Claim sites first, BEFORE the skip disposition: a skipped instrument still has to
        # say which published numbers it left unverified.
        sites = [(rel, i, c)
                 for rel, body in docs
                 for i, line in body
                 for c in claimer(line, rel)]

        # Zero claim sites is UNMEASURED in BOTH modes, whether the instrument
        # answered or skipped — a check that has nothing to compare verified nothing, and a
        # "skipped" line for it would read as an optional integration rather than a silent gap.
        if not sites:
            reading = f"measured {n}" if n is not None else f"instrument not read ({note})"
            lines.append(f"   UNMEASURED  {name}: {reading}, but no documented count "
                         f"matched any known phrasing — this check verified nothing")
            worst = max(worst, 2)
            continue

        if n is None:
            detail = (f"{note} (missing: {', '.join(note.modules)})"
                      if isinstance(note, DependencySkip) else note)
            if note == SKIP_DEPS and not release:
                skipped += 1
                lines.append(f"   skipped     {name}: {detail} "
                             f"(NOT CONFIGURED — not counted as a pass; "
                             f"{len(sites)} documented claim(s) left unverified)")
                for rel, i, c in sites:
                    lines.append(f"                  {rel}:{i} says {c} — not verified")
            elif note == SKIP_DEPS:
                lines.append(f"   UNMEASURED  {name}: {detail} — release mode requires every "
                             f"instrument; {len(sites)} documented claim(s) cannot be verified")
                for rel, i, c in sites:
                    lines.append(f"                  {rel}:{i} says {c} — affected")
                worst = max(worst, 2)
            else:
                lines.append(f"   UNMEASURED  {name}: {note}")
                worst = max(worst, 2)
            continue

        verified += 1
        bad = [s for s in sites if s[2] != n]
        lines.append(f"   {'DRIFT     ' if bad else 'ok        '}  {name}: measured {n} · "
                     f"{len(sites)} claim(s) in {len({s[0] for s in sites})} file(s)")
        for rel, i, c in sites:
            if c != n:
                lines.append(f"                  {rel}:{i} says {c}")
            elif release:
                # Release mode emits the full claim inventory the README sends the
                # reviewer to read — every verified site, not only the drifted ones.
                lines.append(f"                  {rel}:{i} says {c} — verified")
        if bad:
            worst = max(worst, 1)

    if verified == 0:
        worst = max(worst, 2)
        lines.append("   UNMEASURED  no check verified anything — a sweep that compared "
                     "nothing reports the same green as a real one")

    if skipped:
        # Ordinary mode with a skipped instrument: a pass, but SAY the coverage is partial.
        # The receipt that motivated release mode read "7 checked" and nothing else.
        green = (f"every documented count that could be measured matches its instrument "
                 f"({verified} checked, {skipped} skipped — coverage is partial)")
    else:
        green = f"every documented count matches its instrument ({verified} checked)"
    head = {0: green,
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
                # An instrument this box cannot read still gets a claim SITE (its number is
                # never compared), so the check is a disclosed skip. Without one it has zero
                # sites, and zero sites is UNMEASURED in both modes (R4) — which would turn
                # every green case below red on a box lacking the optional dependencies.
                out.append(plant(999))
                continue
            out.append(plant(v + delta if name == bump else v))
        return "\n".join(out) + "\n" + extra

    with tempfile.TemporaryDirectory() as td:
        doc = pathlib.Path(td) / "DOC.md"
        svg = pathlib.Path(td) / "DOC.svg"

        def rc_for(text, measured=None, release=False):
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
            return check(td, measured=measured or sim, release=release)[0]

        case("every count correct passes (green)", rc_for(doc_for()) == 0)
        tui = real["fleet-tui suite"]
        if tui is not None:
            adoption = pathlib.Path(td) / "adopt" / "10_tui.md"
            adoption.parent.mkdir()
            def acceptance(n):
                return ("**VERIFY — expected output:** pytest exits `0`; this export's "
                        f"acceptance run reports `{n} passed`.\n")
            adoption.write_text(acceptance(tui - 5))
            case("a stale TUI adoption acceptance count is caught (red)",
                 rc_for(doc_for()) == 1)
            adoption.write_text(acceptance(tui))
            case("the current TUI adoption acceptance count passes (green)",
                 rc_for(doc_for()) == 0)
            adoption.write_text(f"A historical run reported `{tui - 5} passed`.\n")
            case("a historical adoption result is not a current suite claim",
                 rc_for(doc_for()) == 0)
            adoption.unlink()
            other = adoption.parent / "30_guards.md"
            other.write_text(acceptance(tui - 5))
            case("another adoption guide's acceptance is not a TUI claim",
                 rc_for(doc_for()) == 0)
            other.unlink()
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
        # Same planted tree (correct counts + the 999 TUI claim the skipped instrument cannot
        # judge): ordinary mode must SAY its coverage is partial; release mode (A5) must refuse
        # to pass — UNMEASURED, naming the check and the affected claim site.
        _head = check(td, measured=forced)[1][0]
        _n_skip = sum(1 for v in forced.values() if v[1] == SKIP_DEPS)   # forced + any live skip
        case("ordinary mode says its coverage is partial when a check skipped",
             "partial" in _head and f"{_n_skip} skipped" in _head)
        _rc, _report = check(td, measured=forced, release=True)
        case("release mode: a skipped instrument behind a live claim is UNMEASURED (2)",
             _rc == 2 and any("UNMEASURED" in ln and "fleet-tui suite" in ln for ln in _report)
             and any("DOC.md" in ln for ln in _report))
        case("a skip does not hide a real drift elsewhere",
             rc_for(doc_for("guard unit suite", +1,
                            extra="behind a 999-test hermetic suite\n"),
                    measured=forced) == 1)
        # With every instrument readable, release mode is exactly as strict as ordinary mode:
        # correct counts pass, a wrong one is a violation. Instruments this box cannot read
        # are given a planted value here so the case does not depend on local dependencies.
        complete = {name: (v if v is not None else 11, None) for name, v in real.items()}
        full_doc = "\n".join(plant(complete[name][0]) for name, _f, _c, plant in CHECKS) + "\n"
        case("release mode: every count correct passes (green)",
             rc_for(full_doc, measured=complete, release=True) == 0)
        stale = dict(complete, **{"skills": (complete["skills"][0] + 1, None)})
        case("release mode: a wrong count is still a violation (red), not UNMEASURED",
             rc_for(full_doc, measured=stale, release=True) == 1)

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
    # --release: count coverage is mandatory — a skipped instrument behind a documented
    # claim is UNMEASURED (2), with the affected claim sites listed. Run it against the
    # staged publication snapshot before tagging (see check()).
    code, report = check(release="--release" in sys.argv)
    print("doc count drift — " + report[0])
    for ln in report[1:]:
        print(ln)
    raise SystemExit(code)
