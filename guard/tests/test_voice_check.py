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
"""
import os
import subprocess
import sys

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
