"""Gate for the tracked example identity list, `_tools/identity_terms.example.txt`.

Two properties, and the second is the one that would hurt:

  1. the example still NAMES every class an adopter has to supply — a class silently dropped from
     the example is a class nobody fills in, and the scan gate then runs with real teeth against a
     list that is missing the term that mattered;
  2. no line of the example matches any pattern the gate itself ships. An example file is published
     bytes. One carrying a real value would publish the very string the private file exists to keep
     out, and it would do it in the file that documents the redaction.

This module imports SECRET_PATTERNS and PERSONAL_SHAPES **only**. It must never import a symbol
that needs the gitignored private list: the whole point of the lazy identity load is that a fresh
clone, which has no such file, can still collect and run this test.
"""
import os
import re
import sys

TOOLS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "_tools")
sys.path.insert(0, TOOLS)

from scan_gate import PERSONAL_SHAPES, SECRET_PATTERNS  # noqa: E402

EXAMPLE = os.path.join(TOOLS, "identity_terms.example.txt")

# The classes the refusal message tells an adopter to supply. Kept as regexes over the example's
# own prose so a reworded heading still passes while a DELETED class fails.
REQUIRED_CLASSES = {
    "personal and account names": r"personal and account names",
    "machine nicknames and short hostnames": r"machine nicknames and short hostnames",
    "box alias in benchmark or log annotations": r"box alias used in benchmark or log annotations",
    "LAN domain suffixes": r"lan domain suffixes",
    "personal email local-parts": r"personal email local-parts",
}


def _example_text():
    with open(EXAMPLE, encoding="utf-8") as fh:
        return fh.read()


def _folded(text):
    """Prose wraps. A class named across a line break is still named, so the class check runs over
    the text with every run of whitespace (and the comment markers that start a wrapped line)
    collapsed to a single space — otherwise this gate would fail on a re-wrap and pass on a
    deletion, which is exactly backwards."""
    return re.sub(r"[\s#]+", " ", text)


def test_the_example_is_present_and_tracked():
    assert os.path.isfile(EXAMPLE), (
        "the example list is what the refusal message points an adopter at; without it the "
        "message names a file that does not exist")


def test_every_required_class_is_named():
    text = _folded(_example_text()).lower()
    missing = [name for name, pat in REQUIRED_CLASSES.items()
               if not re.search(pat, text, re.I)]
    assert not missing, f"the example no longer names: {missing}"


def test_the_example_carries_no_value_the_gate_would_flag():
    """The example must not itself be a leak. Every shipped shape is run over every line."""
    hits = []
    for i, line in enumerate(_example_text().splitlines(), 1):
        for name, pat in list(SECRET_PATTERNS) + list(PERSONAL_SHAPES):
            if pat.search(line):
                hits.append(f"{name} at line {i}")
    assert not hits, f"the example matches the gate's own patterns: {hits}"


def test_the_shapes_import_without_the_private_list():
    """A fresh clone has no _tools/identity_terms.txt. Importing the shapes must not need one."""
    names = {name for name, _pat in PERSONAL_SHAPES}
    assert "owner-identity" not in names, (
        "owner-identity belongs to personal_patterns(), which reads the private file at scan time; "
        "if it appears in the module-level shapes the import is no longer clone-safe")
    assert {"email", "rfc1918-ip", "home-user-path"} <= names
