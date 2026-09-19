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
import subprocess
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


def _stage_tool(dest: pathlib.Path) -> pathlib.Path:
    """Copy the scanner and EVERY tracked file beside it into `dest`, then plant synthetic terms.

    The scanner resolves each data file against its OWN directory, so staging a hand-listed subset
    silently loses whatever a later arm added. That is precisely how a fresh clone began failing
    with ScanRefused("missing-owner-banned-hashes") while a box holding the private terms file took
    the run-in-place branch and never noticed: the phrase arm's hash file was a second sibling and
    only the terms list was being copied. Staging the whole tracked directory cannot drift as arms
    are added, and it COPIES rather than synthesises the data files, so the arm under test is the
    shipped one.
    """
    listed = subprocess.run(
        ["git", "ls-files", "-z", "--", "_tools/"],
        cwd=str(REPO_ROOT), capture_output=True, check=True).stdout
    staged = 0
    for rel in listed.decode("utf8").split("\0"):
        if not rel:
            continue
        src = REPO_ROOT / rel
        if src.is_file():
            # Keep each file's position under _tools/. Flattening to a basename lets a
            # future _tools/<sub>/owner_banned.hashes overwrite the top-level one, and the
            # scan would then read the wrong bytes while every arm still reported green.
            target = dest / pathlib.Path(rel).relative_to("_tools")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, target)
            staged += 1
    if staged == 0:
        pytest.fail("staged nothing from _tools/ -- git ls-files returned no files")
    # Last, so it overrides any tracked example: the synthetic list must be what the scan reads.
    (dest / "identity_terms.txt").write_text(_ABSENT_TERM + "\n", encoding="utf8")
    return dest / "scan_gate.py"


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
        source = _stage_tool(pathlib.Path(scratch))
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



# ---------------------------------------------------------------------------
# The fresh-clone branch of the fixture above, forced to run on EVERY box.
#
# _scanner_and_terms picks its branch from whether the PRIVATE terms file exists. On a maintainer's
# box it therefore runs the tool in place and the branch CI takes is never executed -- which is why
# a missing sibling shipped green: the machine that ran the tests could not reach the code that
# broke. These arms point PRIVATE_TERMS at a path that does not exist and clear the cache, so the
# fixture's OWN staging runs here. Reverting _stage_tool's copy loop fails these on any box.
#
# DUAL_PARTITION = {"fires":  "test_a_staging_that_drops_a_sibling_refuses",
#                   "silent": "test_the_fixture_fresh_clone_branch_scans_a_planted_tree"}
# ---------------------------------------------------------------------------

_THIS = sys.modules[__name__]


def _force_fresh_clone_branch(monkeypatch, tmp_path, request):
    """Drive the REAL fixture down its fresh-clone branch, whatever this box has."""
    cache: dict = {}
    monkeypatch.setattr(_THIS, "PRIVATE_TERMS", tmp_path / "absent-identity-terms.txt")
    monkeypatch.setattr(_THIS, "_loaded", cache)
    module, terms = _scanner_and_terms()
    assert terms == SYNTHETIC_TERMS_USED, "did not take the fresh-clone branch"
    # The module-scoped cleanup fixture pops "scratch" from the ORIGINAL _loaded, which
    # monkeypatch restores at teardown -- so a directory staged here would outlive the run.
    # Register it against this test instead of leaving it in /tmp.
    scratch = cache.get("scratch")
    if scratch:
        request.addfinalizer(lambda: shutil.rmtree(scratch, ignore_errors=True))
    return module


def test_the_fixture_fresh_clone_branch_scans_a_planted_tree(monkeypatch, tmp_path, request) -> None:
    """End to end: the staged scanner loads its phrase arm AND scans, rather than refusing.

    Asserting only that the module imports would pass with every data file missing, because the
    refusal happens at scan time. So this plants a tree and runs the scan the fixture exists for.
    """
    module = _force_fresh_clone_branch(monkeypatch, tmp_path, request)
    assert module._load_banned_windows(), "the phrase arm loaded no windows from the tracked hashes"
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "ordinary.md").write_text("a line that trips no arm\n", encoding="utf8")
    module.scan(str(tree))


def test_a_staging_that_drops_a_sibling_refuses(monkeypatch, tmp_path) -> None:
    """The teeth: stage the tool WITHOUT its siblings and the scanner must refuse, not pass.

    This is the exact fresh-clone failure. The refusal is the behaviour under test -- a scanner that
    returned CLEAN with its phrase arm absent would be the worse bug, so the arm proves the gate
    fails closed rather than proving it is quiet.
    """
    bare = tmp_path / "bare"
    bare.mkdir()
    shutil.copyfile(TOOL, bare / "scan_gate.py")
    (bare / "identity_terms.txt").write_text(_ABSENT_TERM + "\n", encoding="utf8")
    spec = importlib.util.spec_from_file_location("sg_bare", bare / "scan_gate.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    with pytest.raises(module.ScanRefused) as caught:
        module._load_banned_windows()
    assert "owner-banned-hashes" in str(caught.value)


def test_staging_copies_every_tracked_tools_file(tmp_path) -> None:
    """The list cannot drift because there is no list: staging mirrors what git tracks."""
    _stage_tool(tmp_path)
    listed = subprocess.run(
        ["git", "ls-files", "-z", "--", "_tools/"],
        cwd=str(REPO_ROOT), capture_output=True, check=True).stdout
    tracked = [r for r in listed.decode("utf8").split("\0") if r]
    assert tracked, "git ls-files reported no files under _tools/ -- the control is broken"
    for rel in tracked:
        if (REPO_ROOT / rel).is_file():
            copied = tmp_path / pathlib.Path(rel).relative_to("_tools")
            assert copied.is_file(), "%s was not staged" % rel
            assert copied.read_bytes() == (REPO_ROOT / rel).read_bytes(), (
                "%s was staged but its bytes differ -- a basename collision overwrote it" % rel)
