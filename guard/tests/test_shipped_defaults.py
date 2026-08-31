"""No shipped fallback default may name a home-anchored path.

WHY THIS IS A TEST AND NOT A SCRUB RULE. The scrub arm's private-material class catches home
paths that name an ACCOUNT. The account-less anchors — a bare tilde-slash, or the home
environment variable — name nobody, and a corpus-wide regex for them measures 151 tracked lines
in this repo: documentation legitimately writes adopter config paths that way. A rule that fires
on 151 legitimate lines is a rule everyone learns to ignore, which is the same as no rule.

What is NOT legitimate is baking one machine's directory layout into shipped code as a FALLBACK
default. Two costs, both measured here before this gate existed:
  * it publishes somebody's actual tree to every adopter, and
  * it aims a check at a path that does not exist on the adopter's box, where it reads "nothing
    found" — a clean-looking result from a check that was never pointed at anything.

SCOPE, STATED OUT LOUD: this asserts the property for the guard subsystem, the part of the tree
an adopter is told to run. `findings(REPO, "")` widens it to the whole repository; at the time of
writing that returns four more, all in the terminal-UI subsystem, whose defaults are a running
application's behaviour and an owner's call to change — not something a defect fix may quietly
alter. Widening the scope is a one-argument change once that call is made.
"""
import os
import re
import subprocess

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SUBSYSTEM = "guard"

# A home anchor supplied as the fallback value of a lookup: ${VAR:-<home path>} in shell, or the
# second argument of an environment read in Python.
SHIPPED_DEFAULT = re.compile(
    r"(?:\$\{[A-Za-z_][A-Za-z0-9_]*:-\s*|(?:environ\.get|getenv)\([^)\n]{0,80}?,\s*[\"'])"
    r"(?:\$HOME|~)/", re.IGNORECASE)


def findings(root, scope):
    """(path, line number, text) for every shipped home-anchored default under `scope`."""
    listing = subprocess.run(["git", "-C", root, "ls-files", "--", scope or "."],
                             capture_output=True, text=True)
    assert listing.returncode == 0, listing.stderr
    out = []
    for rel in listing.stdout.split():
        try:
            with open(os.path.join(root, rel), encoding="utf-8") as fh:
                text = fh.read()
        except (OSError, UnicodeDecodeError):
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if SHIPPED_DEFAULT.search(line):
                out.append((rel, n, line.strip()[:120]))
    return out


def test_the_guard_subsystem_ships_no_home_anchored_default():
    found = findings(REPO, SUBSYSTEM)
    assert not found, "shipped home-anchored default(s):\n" + "\n".join(
        "  %s:%d  %s" % f for f in found)


def test_the_check_can_fire():
    """A gate nobody has seen fail is not a gate. Both shapes of the defect, on synthetic lines."""
    # Assembled from pieces so this control line does not read as an instance of the defect —
    # the same by-construction discipline the scrub arm's own rule definitions use.
    shell = "OUTBOX=\"${OUTBOX:-" + "$HOME" + "/somewhere/nested}\""
    python = "OUTBOX = os.environ.get(\"OUTBOX\", \"" + "~/" + "somewhere/nested\")"
    for line in (shell, python):
        assert SHIPPED_DEFAULT.search(line), line
    assert not SHIPPED_DEFAULT.search("copy the example config to $HOME/.config/tool.json")
    assert not SHIPPED_DEFAULT.search("${EDITOR:-vi} \"$HOME/.config/tool.json\"")
