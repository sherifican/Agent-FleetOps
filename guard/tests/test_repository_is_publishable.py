"""The package must pass its own publication gate, and the SUITE must be what notices.

WHY THIS FILE EXISTS. ``_tools/scan_gate.py`` decides whether this repository may be published,
and ``guard/hooks/pre-push`` runs it over the archived tree of every commit in a push range.
Nothing ran it against THIS repository. It was red for eight rounds -- six key-shaped literals
inside the scanner's own test file, a literal that file's own header already forbids -- and the
release cycle did not notice, because the release cycle runs the suite and the suite never ran the
gate. Every round measured the scanner against synthetic trees; none measured it against the tree
it would ship.

That is this package's own defect class: a guard nobody runs is not a guard. The repair is not to
remember to run it. It is to put it where the thing that already runs every round will run it.

WHAT THIS FILE DOES NOT ESTABLISH.
  * IT IS NOT THE HOOK'S MEASUREMENT. The hook extracts an ARCHIVE of each commit and scans that.
    This runs the same entry point over the tree it is sitting in -- the INDEX where that tree is a
    checkout, since the scanner judges the index when it finds one, and filesystem bytes otherwise.
    Same gate, different input. An earlier draft of this paragraph called the private-file run "the
    exact one the hook performs"; a shared entry point does not make the inputs the same, and
    whether a RANGE is publishable stays a separate measurement over that range (team review,
    gate 63).
  * ONE TREE AT ONE MOMENT IS NOT A HISTORY. The hook scans each commit's tree against THAT
    commit's own allowlist, so a literal introduced and later removed stays refused in every commit
    that carries it, and a green here says nothing about any of them.
  * THE OWNER-IDENTITY TERM LIST IS A PRIVATE INPUT, absent in any fresh clone. Where it is absent
    this file runs the gate with a synthetic term in its place: every SECRET shape and every
    identity-INDEPENDENT personal shape (email, RFC1918 address, home path) is measured, and only
    the private owner terms are not. A run that took that branch says so through the suite's own
    skip channel, which the release cycle already reads -- see the second arm. A green that
    silently dropped an arm is the failure this file exists about, and a note that only appears
    inside an assertion message appears only when the assertion FAILS (team review, gate 63).
"""
import importlib.util
import pathlib
import shutil
import sys
import tempfile

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
TOOL = REPO_ROOT / "_tools" / "scan_gate.py"
PRIVATE_TERMS = TOOL.parent / "identity_terms.txt"

# A term that cannot occur in the tree, so the identity ALTERNATION is non-empty and its machinery
# genuinely runs. A comment-only terms file compiles to a pattern that matches nothing, which would
# leave this file unable to tell a working identity arm from a disabled one.
#
# ASSEMBLED, and the first draft was not. Written as one literal, the term sat in this file, this
# file is inside the tree these arms scan, and the scan reported one PERSONAL hit: the synthetic
# term matched itself. The arm was right and the instrument was the defect. A terms file is scanned
# content like any other, so a term chosen to be absent has to be absent from the source naming it.
_ABSENT_TERM = "zz-synthetic-identity-term-" + "absent-from-every-tracked-file"

PRIVATE_TERMS_USED = "the private identity list"
SYNTHETIC_TERMS_USED = "a synthetic identity term"

_loaded: dict = {}


def _scanner_and_terms() -> tuple:
    """The scanner to run, and which identity terms it will read.

    With the private file present the tool runs where it lives. Without it the tool is copied
    beside a synthetic terms file, because the scanner reads its terms from its own directory and
    refuses outright when that file is missing -- correctly: a personal-data scan with no identity
    list is a check that cannot fail."""
    if "mod" in _loaded:
        return _loaded["mod"], _loaded["terms"]
    if not TOOL.is_file():
        pytest.fail("%s is missing -- the publication gate cannot be the thing that ran" % TOOL)
    if PRIVATE_TERMS.is_file():
        source, terms = TOOL, PRIVATE_TERMS_USED
    else:
        scratch = tempfile.mkdtemp(prefix="publishable-gate-")
        _loaded["scratch"] = scratch
        source = pathlib.Path(scratch) / "scan_gate.py"
        shutil.copyfile(TOOL, source)
        (source.parent / "identity_terms.txt").write_text(_ABSENT_TERM + "\n", encoding="utf8")
        terms = SYNTHETIC_TERMS_USED
    spec = importlib.util.spec_from_file_location("scan_gate_under_test", source)
    if spec is None or spec.loader is None:
        pytest.fail("could not load the publication gate from %s" % source)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    _loaded["mod"], _loaded["terms"] = module, terms
    return module, terms


@pytest.fixture(scope="module", autouse=True)
def _drop_the_copied_tool():
    """The synthetic branch copies the tool into a temporary directory; remove it afterwards."""
    yield
    scratch = _loaded.pop("scratch", None)
    if scratch:
        shutil.rmtree(scratch, ignore_errors=True)


def test_the_owner_identity_terms_were_available_to_the_scan() -> None:
    """REPAIRED (team review, gate 63): the terms source was named only inside a failure message,
    so a passing run never said which of the two it took and a private-list scan was
    indistinguishable from a synthetic one. It is a SKIP now -- the channel the release cycle
    already reads with -rfs -- because a run that could not measure the owner-identity arm has
    reduced coverage, and this package's own words are that a skipped arm is not a passing one."""
    scanner, terms = _scanner_and_terms()
    assert scanner is not None
    if terms is SYNTHETIC_TERMS_USED:
        pytest.skip(
            "the private _tools/identity_terms.txt is absent here, so the publication gate below "
            "ran with a synthetic term and the OWNER-IDENTITY arm matched nothing. Every SECRET "
            "shape and every identity-independent personal shape was still measured.")
    assert PRIVATE_TERMS.is_file(), (
        "REPAIRED: this arm reports which terms the gate read, and it read the private list "
        "without that list being there.")


def test_this_repository_passes_its_own_publication_gate() -> None:
    """REPAIRED: red on the reviewed tip and on the seven commits before it; green once the
    key-shaped literals in the suite are assembled at run time the way that file's own header
    already says they are. The entry point is ``scan()``, the one ``guard/hooks/pre-push`` runs --
    over this tree rather than over an archive."""
    scanner, terms = _scanner_and_terms()
    hits = scanner.scan(str(REPO_ROOT))
    assert hits == [], (
        "REPAIRED: this repository does not pass its own publication gate, so `git push` is "
        "refused by guard/hooks/pre-push and this tree must not be published.\n"
        "Terms read: %s.\n"
        "One line per hit -- class, pattern, surface, where. Never the matched bytes:\n%s\n"
        "A hit inside a test fixture is still a hit: those bytes would be published. Two honest "
        "fixes exist. Assemble the value from fragments at run time, which is the convention "
        "guard/tests/test_scan_gate_publication.py states in its own header and already uses; or "
        "declare an exemption out loud in _tools/scan_allow.tsv with the reason written beside "
        "it. Waiving the hook or passing --no-verify is neither."
        % (terms,
           "\n".join("  %s %s %s %s:%d" % (cls, name, surface, rel, line)
                     for rel, line, cls, name, surface in hits)))


def test_the_gate_this_file_runs_can_still_refuse() -> None:
    """CONTROL: the arm above reports zero, and a checker that can only ever report zero reports it
    on a dirty tree too and reads as proof forever. This plants a key-shaped value in a scratch
    tree and requires the same entry point, loaded the same way, to refuse it.

    STRENGTHENED (team review, gate 63): requiring merely SOME hit was satisfiable by one unrelated
    finding, so the planted value could go undetected while the control stayed green. The finding
    must now be the planted one -- its path, its line, its class and the content surface."""
    scanner, _ = _scanner_and_terms()
    with tempfile.TemporaryDirectory() as scratch:
        tree = pathlib.Path(scratch)
        planted = "AKIA" + "IOSFODNN7EXAMPLE"     # assembled: this source carries no such literal
        (tree / "docs").mkdir()
        (tree / "docs" / "plant.md").write_text("k = '%s'\n" % planted, encoding="utf8")
        (tree / "docs" / "clean.md").write_text("an ordinary line of documentation\n", encoding="utf8")
        hits = scanner.scan(str(tree))
    assert hits, (
        "CONTROL: the publication gate reported CLEAN on a tree carrying a planted key-shaped "
        "value. The arm above is then satisfied by construction and measures nothing.")
    planted_hits = [h for h in hits if h[0] == "docs/plant.md" and h[1] == 1
                    and h[2] == "SECRET" and h[4] == "content"]
    assert planted_hits, (
        "CONTROL: the gate returned findings but none of them is the planted one at "
        "docs/plant.md:1 as SECRET on the content surface. Some hit is not the hit: an unrelated "
        "finding would satisfy a bare non-empty check while the planted value went unseen. "
        "Findings were: %s" % [(h[0], h[1], h[2], h[3], h[4]) for h in hits])
    assert not any(h[0] == "docs/clean.md" for h in hits), (
        "CONTROL: the gate also flagged the clean sibling file, so it is not discriminating "
        "between the planted value and ordinary prose. Findings were: %s"
        % [(h[0], h[1], h[2], h[3], h[4]) for h in hits])
    assert not any(planted in str(field) for hit in hits for field in hit), (
        "CONTROL: the planted value came back inside the hit tuple. A finding names where it is, "
        "not what it was.")
