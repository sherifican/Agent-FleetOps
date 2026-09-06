"""The voice guard's SCOPE is the part of it that can be wrong silently.

The word-matching half of `guard/voice_check.py` is covered by its own pinned self-test. The
half nothing watched is `in_scope()`: a file the guard never opens produces exactly the same
green as a file it opened and cleared. That is the failure this file exists for — a scope that
quietly narrows reports "the published prose is first-person singular" about prose it never read.

WHAT CHANGED AND WHY. Scope was "any README, plus docs/, adopt/, specs/". A root-level document
that is not called README — an addendum pasted into an adopting fleet's brief, a staging note —
is as front-facing as the README beside it and was outside. Root-level `.md` is now in scope.

SCOPE, STATED OUT LOUD. This asserts which paths the guard READS. It does not assert that any
particular verdict is right; the self-test covers that. It imports the shipping module rather
than restating its rule, so a change to `in_scope()` reaches these assertions.

THE BOUNDARY, ADDED LATER. README.md line 61 links the shipped skill by its NAME,
`[should-we](skills/should-we/SKILL.md)`, and the `\b`-bounded pattern read both tokens as the
pronoun: a hyphen is a word break to `\b`. The doctrine in the guard's header is to fix the
checker rather than rewrite correct prose, so the second half of this file pins the boundary —
a hyphen-joined compound is a name and must not fire; the bare pronoun in prose must still fire
(those are the preservation controls, green before and after); and an exemption declared for a
file whose only "plural" was a compound is now STALE, because the declaration is checked.
"""
import os
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "guard"))

import voice_check  # noqa: E402  (path set above, deliberately)


def test_a_root_level_document_that_is_not_a_readme_is_in_scope():
    """The A13 case. Front-facing is about where a visitor looks, not about a filename."""
    assert voice_check.in_scope("ACTIONABLE_ADDENDUM.md"), (
        "a root-level document is front-facing whatever it is called")
    assert voice_check.in_scope("STAGING_README.md")


def test_the_widening_stops_at_the_root_and_at_markdown():
    """Negative control on the same rule — a scope that admits everything is not a scope.

    Without this, `in_scope` returning True unconditionally would satisfy the test above.
    """
    assert not voice_check.in_scope("skills/example/SKILL.md"), (
        "adopted material keeps the adopting team's voice; it must stay out of scope")
    assert not voice_check.in_scope("guard/specs/SPEC_contract_agreement.md")
    assert not voice_check.in_scope("LICENSE")
    assert not voice_check.in_scope("actionable_rollup.py"), (
        "root-level widening is for documents, not for source")


def test_the_previously_covered_surfaces_are_still_covered():
    """A widening must not drop what it replaced — the pinning-defect check, run forwards."""
    for rel in ("README.md", "guard/README.md", "docs/anything.md",
                "adopt/anything.md", "specs/anything.md"):
        assert voice_check.in_scope(rel), rel


def test_the_shipping_entry_point_reads_the_newly_scoped_files():
    """in_scope() agreeing is not proof the RUN opens them — assert on the shipped output.

    The count line is the aggregate a reader actually sees, so it is the thing asserted.
    """
    run = subprocess.run([sys.executable, os.path.join("guard", "voice_check.py")],
                         cwd=REPO, capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, run.stdout + run.stderr
    assert "scanned 23 text file(s)" in run.stdout, (
        "the two root-level documents did not join the scanned set: " + run.stdout)
    assert "1 declared exemption(s)" in run.stdout, (
        "the addendum's exemption is not being counted: " + run.stdout)


def test_the_self_test_still_passes():
    """The guard's own pinned cases, run through the shipping entry point."""
    run = subprocess.run([sys.executable, os.path.join("guard", "voice_check.py"), "--selftest"],
                         cwd=REPO, capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, run.stdout + run.stderr


# --- the boundary: a hyphen-joined compound is a name, not the pronoun ---------------------------

README_LINE_61 = ("| `skills/` | ... blocked-page retrieval, the [should-we](skills/should-we/SKILL.md) "
                  "directive check (interrogate the premise before executing an imperative), and more. |")

COMPOUNDS = [
    pytest.param(README_LINE_61, id="README.md:61 (the instance)"),
    pytest.param("the should-we check", id="should-we"),
    pytest.param("a we-first design", id="we-first"),
    pytest.param("deployed to us-east", id="us-east"),
    pytest.param("the our-team channel", id="our-team"),
    pytest.param("`we-first`", id="code span"),
    pytest.param("skills/should-we/SKILL.md", id="path"),
    pytest.param("see skills/should-we/SKILL.md, then `us-east-1`.", id="path and code span in prose"),
]

PRONOUNS = [
    pytest.param("we ship", id="we ship"),
    pytest.param("our fleet", id="our fleet"),
    pytest.param("let us", id="let us"),
    pytest.param("We measured it.", id="We at line start"),
    pytest.param("Measured. We shipped.", id="after a full stop"),
    pytest.param("measured, we shipped", id="after a comma"),
    pytest.param("the result (we think) stands", id="in parentheses"),
    pytest.param('the word "we" is the false collective', id="in quotes"),
    pytest.param("we\u2019re done", id="we\u2019re (curly)"),
    pytest.param("let's go", id="let's"),
    pytest.param("ours, ourselves", id="ours / ourselves"),
    pytest.param("the should-we check is one we ship", id="bare pronoun beside a compound"),
    pytest.param("one of us \u2014 not them", id="before an em dash (not a hyphen)"),
]


@pytest.mark.parametrize("text", COMPOUNDS)
def test_a_hyphen_joined_compound_is_a_name_not_the_pronoun(text):
    """RED on the `\b` pattern, GREEN on the hyphen-aware boundary."""
    assert voice_check.PLURAL.search(text) is None, text


@pytest.mark.parametrize("text", PRONOUNS)
def test_the_bare_pronoun_in_prose_still_fires(text):
    """Preservation controls — green before and after, or the fix widened into a blanket."""
    assert voice_check.PLURAL.search(text) is not None, text


def _front_facing_tree(tmp_path):
    root = tmp_path / "repo"
    (root / "guard").mkdir(parents=True)
    return root


def test_the_shipping_scan_clears_a_compound_and_still_refuses_the_pronoun(tmp_path):
    """`PLURAL.search` agreeing is not the verdict a reader sees; `check()` is."""
    root = _front_facing_tree(tmp_path)
    readme = root / "README.md"

    readme.write_text(README_LINE_61 + "\n", encoding="utf-8")
    code, report = voice_check.check(str(root))
    assert code == 0, report

    readme.write_text(README_LINE_61 + "\nIt is the one we ship.\n", encoding="utf-8")
    code, report = voice_check.check(str(root))
    assert code == 1, report
    assert any("README.md" in ln for ln in report), report
    assert any(ln.strip().startswith("2:") for ln in report), (
        "the hit must be the pronoun on line 2, not the compound on line 1: " + "\n".join(report))


def test_an_exemption_for_a_compound_only_file_is_reported_stale(tmp_path):
    """The allow-list is CHECKED. A file exempted because its compounds used to read as plural
    now contains no plural at all, and the declaration must say so rather than sit there."""
    root = _front_facing_tree(tmp_path)
    readme = root / "README.md"
    allow = root / voice_check.ALLOW

    readme.write_text(README_LINE_61 + "\n", encoding="utf-8")
    allow.write_text("README.md\tthe skill name used to trip the regex\n", encoding="utf-8")
    code, report = voice_check.check(str(root))
    assert code == 1, report
    assert any("STALE EXEMPTION" in ln and "README.md" in ln for ln in report), report

    # The same declaration is honoured the moment the file carries a real pronoun.
    readme.write_text(README_LINE_61 + "\nThe word we is quoted here.\n", encoding="utf-8")
    code, report = voice_check.check(str(root))
    assert code == 0, report
