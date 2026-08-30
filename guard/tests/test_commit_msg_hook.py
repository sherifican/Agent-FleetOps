"""Gate for guard/hooks/commit-msg — the trailer contract an adopter has to satisfy.

The hook is a PUBLIC contract surface: it decides which commit messages a stranger's clone
accepts. Its trailer names and its examples therefore have to be tool-neutral. An adopter whose
work is done by people, by contractors, or by agents that share no vocabulary with this repo
must be able to satisfy it without adopting someone else's naming for their subsystems.

Two properties, both measured here rather than asserted in prose:
  1. BEHAVIOUR — the accepted names really are accepted, an untagged message really is rejected.
  2. AGREEMENT — the names the hook ENFORCES and the names its rejection text ADVERTISES are the
     same set. A hook that accepts a name it never mentions is an undocumented back door; one
     that advertises a name it rejects sends the adopter in a circle.
"""
import os
import re
import subprocess
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HOOK = os.path.join(REPO, "guard", "hooks", "commit-msg")

# The tool-neutral pair. Anything vendor-, product- or subsystem-shaped here is the defect.
ACCEPTED = {"Delegated-to", "Authored-directly"}


def _run(message):
    fd, path = tempfile.mkstemp(suffix=".msg")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(message)
        proc = subprocess.run(["bash", HOOK, path], capture_output=True, text=True)
        return proc.returncode, proc.stdout + proc.stderr
    finally:
        os.unlink(path)


def _hook_source():
    with open(HOOK, encoding="utf-8") as fh:
        return fh.read()


def test_delegated_trailer_is_accepted():
    rc, out = _run("guards: a change\n\nDelegated-to: another agent\n")
    assert rc == 0, out


def test_tool_neutral_direct_trailer_is_accepted():
    rc, out = _run("guards: a change\n\nAuthored-directly: guard code\n")
    assert rc == 0, "a tool-neutral direct trailer must be accepted —\n" + out


def test_message_without_a_trailer_is_rejected():
    rc, out = _run("guards: a change with no attribution\n")
    assert rc != 0, out


def test_trailer_with_an_empty_value_is_rejected():
    rc, out = _run("guards: a change\n\nDelegated-to:   \n")
    assert rc != 0, "an empty value records nothing and must not pass —\n" + out


def test_mechanical_commits_are_exempt():
    for subject in ("Merge branch 'x'", "Revert \"a change\"", "fixup! a change"):
        rc, out = _run(subject + "\n")
        assert rc == 0, subject + " -> " + out


def test_enforced_names_are_the_tool_neutral_pair():
    """The set the hook actually enforces — read out of the hook, not out of its prose."""
    match = re.search(r"grep -qE '\^\(([^)]+)\):", _hook_source())
    assert match, "could not read the accepted-trailer alternation out of the hook"
    assert set(match.group(1).split("|")) == ACCEPTED, match.group(1)


def test_rejection_text_advertises_exactly_what_is_enforced():
    _, out = _run("guards: a change with no attribution\n")
    advertised = set(re.findall(r"^\s{6}([A-Za-z][A-Za-z-]*): <", out, re.M))
    assert advertised == ACCEPTED, "enforced %s vs advertised %s\n%s" % (
        sorted(ACCEPTED), sorted(advertised), out)
