"""The package must pass its own publication gate, and the SUITE must be what notices.

WHY THIS FILE EXISTS. ``_tools/scan_gate.py`` decides whether this repository may be published,
and ``guard/hooks/pre-push`` runs it over the archived tree of every commit in a push range.
Nothing ran it against THIS repository. It was red for eight rounds -- six key-shaped literals
inside the scanner's own test file, a literal the file's own header already forbids -- and the
release cycle did not notice, because the release cycle runs the suite and the suite never ran the
gate. Every round measured the scanner against synthetic trees; none measured it against the tree
it would ship.

That is this package's own defect class: a guard nobody runs is not a guard. The repair is not to
remember to run it. It is to put it where the thing that already runs every round will run it.

WHAT THIS FILE DOES NOT ESTABLISH.
  * It judges ONE tree at ONE moment. A clean HEAD is not a clean history: the hook scans each
    commit's archived tree against THAT commit's own allowlist, so a literal introduced and later
    removed stays refused in every commit that carries it. Whether a range is pushable is a
    separate measurement over that range, one archive-and-scan per commit.
  * The owner-identity term list is a gitignored private input. Where it is absent -- any fresh
    clone -- this file runs the gate with a synthetic term in its place: every SECRET shape and
    every identity-INDEPENDENT personal shape (email, RFC1918 address, home path) is measured in
    full, and only the private owner terms are not. The assertion message says which of the two
    happened, because a green that silently dropped an arm is the failure this file exists about.
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
# leave this arm unable to tell a working identity arm from a disabled one.
#
# ASSEMBLED, and the first draft was not. Written as one literal, the term sat in this file, this
# file is inside the tree this arm scans, and the scan reported one PERSONAL hit: the synthetic
# term matched itself. The arm was right and the instrument was the defect. A terms file is
# scanned content like any other, so a term chosen to be absent has to be absent from the source
# that names it.
_ABSENT_TERM = "zz-synthetic-identity-term-" + "absent-from-every-tracked-file"

_loaded: dict = {}


def _load_scanner_and_terms_source() -> tuple[object, str]:
    """Return the scanner to run and a plain-language note saying which terms it will read.

    With the private file present the tool runs where it lives, so the scan is the exact one the
    pre-push hook performs. Without it the tool is copied beside a synthetic terms file, because
    the scanner reads its terms from its own directory and refuses outright when that file is
    missing -- correctly: a personal-data scan with no identity list cannot fail.
    """
    if "mod" in _loaded:
        return _loaded["mod"], _loaded["note"]
    if not TOOL.is_file():
        pytest.fail("%s is missing -- the publication gate cannot be the thing that ran" % TOOL)
    if PRIVATE_TERMS.is_file():
        source, note = TOOL, "the private _tools/identity_terms.txt (full coverage)"
    else:
        scratch = tempfile.mkdtemp(prefix="publishable-gate-")
        source = pathlib.Path(scratch) / "scan_gate.py"
        shutil.copyfile(TOOL, source)
        (source.parent / "identity_terms.txt").write_text(_ABSENT_TERM + "\n", encoding="utf8")
        note = ("a SYNTHETIC identity term: the private _tools/identity_terms.txt is absent here, "
                "so the owner-identity arm matched nothing. Every SECRET shape and every "
                "identity-independent personal shape was measured in full")
    spec = importlib.util.spec_from_file_location("scan_gate_under_test", source)
    if spec is None or spec.loader is None:
        pytest.fail("could not load the publication gate from %s" % source)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    _loaded["mod"], _loaded["note"] = module, note
    return module, note


def test_this_repository_passes_its_own_publication_gate() -> None:
    """REPAIRED: red on a890af9 and on the seven commits before it; green once the key-shaped
    literals in the suite are assembled at run time the way that file's own header already says
    they are. The entry point is ``scan()`` -- what ``guard/hooks/pre-push`` runs per commit."""
    scanner, terms_note = _load_scanner_and_terms_source()
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
        % (terms_note,
           "\n".join("  %s %s %s %s:%d" % (cls, name, surface, rel, line)
                     for rel, line, cls, name, surface in hits)))


def test_the_gate_this_arm_runs_can_still_refuse() -> None:
    """CONTROL: the arm above reports zero, and a checker that can only ever report zero reports
    it on a dirty tree too and reads as proof forever. This plants a key-shaped value in a scratch
    tree and requires the same entry point, loaded the same way, to refuse it."""
    scanner, _ = _load_scanner_and_terms_source()
    with tempfile.TemporaryDirectory() as scratch:
        tree = pathlib.Path(scratch)
        planted = "AKIA" + "IOSFODNN7EXAMPLE"     # assembled: this source carries no such literal
        (tree / "docs").mkdir()
        (tree / "docs" / "plant.md").write_text("k = '%s'\n" % planted, encoding="utf8")
        hits = scanner.scan(str(tree))
    assert hits, (
        "CONTROL: the publication gate reported CLEAN on a tree carrying a planted key-shaped "
        "value. The arm above is then satisfied by construction and measures nothing.")
    assert not any(planted in str(field) for hit in hits for field in hit), (
        "CONTROL: the planted value came back inside the hit tuple. A finding names where it is, "
        "not what it was.")
