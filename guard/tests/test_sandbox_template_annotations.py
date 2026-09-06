"""Every bwrap flag the sandbox template SHIPS must be explained in its trailing comments.

WHY A TEST AND NOT A REVIEW HABIT. The template is copied into an adopter's fleet and edited
there. The flags are the whole security argument — which parts of the host the worker can read,
which single path it can write, whether it keeps the network — and an adopter who cannot see
what a flag buys will either keep one that is wrong for the destination or drop one that was
holding the boundary up. An annotation block that drifts one flag behind the command is worse
than none, because it reads complete.

WHAT THIS REPLACES. `bash -n` was the original check. It parses the file and cannot fail on a
comment, so it could not have detected a missing annotation, an absent alternative, or anything
else this file is about: an unfalsifiable check reporting a pass forever. `bash -n` is kept in
the suite as a secondary parse check that nothing rests on.

SCOPE, STATED OUT LOUD. This asserts that every flag is DESCRIBED, not that any description is
CORRECT — no test can check that. It also asserts the narrow-bind alternative is present and
marked incomplete, because a narrow bind set that looks authoritative is the failure this
addition is trying not to introduce: the right set is distribution-specific and belongs to
whoever knows the destination.
"""
import os
import re

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TEMPLATE = os.path.join(REPO, "templates", "sandboxed-dispatch.sh.template")

FLAG = re.compile(r"(?<![-\w])(--[a-z][a-z0-9-]*)")


def _text():
    with open(TEMPLATE, encoding="utf-8") as fh:
        return fh.read()


def _exec_block(text):
    """The `exec bwrap ... -- "$MODEL_CLI"` invocation, as a list of lines.

    Read from the shipped file, not restated here: a local copy of the command would be
    satisfied by construction and could not notice a flag being added upstream.
    """
    lines = text.splitlines()
    starts = [i for i, ln in enumerate(lines) if ln.strip().startswith("exec bwrap")]
    assert len(starts) == 1, "expected exactly one `exec bwrap` invocation, found %d" % len(starts)
    block = []
    for ln in lines[starts[0]:]:
        block.append(ln)
        if not ln.rstrip().endswith("\\"):
            break
    return block


def _annotation_block(text):
    """The trailing `# --flag: explanation` comment lines."""
    return [ln for ln in text.splitlines()
            if ln.startswith("#") and FLAG.search(ln)]


def test_every_shipped_flag_is_annotated():
    text = _text()
    block = _exec_block(text)
    used = []
    for ln in block:
        # A commented line inside the invocation is an alternative, not a shipped flag.
        if ln.lstrip().startswith("#"):
            continue
        for flag in FLAG.findall(ln):
            if flag not in used:
                used.append(flag)
    assert used, "no flags parsed out of the exec block — the parser, not the template, is broken"

    annotated = set()
    for ln in _annotation_block(text):
        annotated.update(FLAG.findall(ln))

    missing = [f for f in used if f not in annotated]
    assert not missing, (
        "shipped bwrap flag(s) with no line in the trailing annotation block: %s" % missing)


def test_a_narrow_bind_alternative_is_offered_and_marked_incomplete():
    """The wide default stays, but the reader must be shown the other choice and its cost."""
    text = _text()
    lowered = text.lower()
    assert "--ro-bind / /" in text, "the shipped wide default must remain the shipped default"

    # The alternative has to be present as a COMMENT, never as a second live bind.
    alternative = [ln for ln in text.splitlines()
                   if ln.lstrip().startswith("#") and "--ro-bind" in ln and " / /" not in ln]
    assert alternative, (
        "no commented narrow --ro-bind alternative found; the reader is shown one option only")

    assert "incomplete" in lowered, (
        "the narrow alternative is not marked incomplete — a narrow bind set that reads "
        "authoritative is worse than no example, because the right set is distribution-specific")


def test_the_parser_can_fail():
    """Positive control: the flag check must go RED on a template missing an annotation.

    Without this, a parser that silently found nothing would report the same green as a
    genuinely complete annotation block.
    """
    text = _text()
    block = _exec_block(text)
    used = [f for ln in block if not ln.lstrip().startswith("#") for f in FLAG.findall(ln)]
    assert "--die-with-parent" in used, "expected the real template to ship --die-with-parent"

    stripped = "\n".join(ln for ln in text.splitlines()
                         if not ln.startswith("# --die-with-parent"))
    annotated = set()
    for ln in stripped.splitlines():
        if ln.startswith("#"):
            annotated.update(FLAG.findall(ln))
    assert "--die-with-parent" not in annotated, (
        "removing the annotation did not change what the check sees — the check is blind")
