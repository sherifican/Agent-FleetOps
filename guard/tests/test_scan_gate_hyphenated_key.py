"""The publication scanner must catch an ``sk-`` key whose body carries hyphens.

WHAT WAS WRONG. The ``openai-style-key`` arm was ``sk-[A-Za-z0-9]{20,}``: twenty ALPHANUMERIC
characters straight after the prefix. Current provider keys are segmented -- a word, a hyphen,
then a long body that itself carries ``-`` and ``_`` -- so the character class breaks at the
first hyphen and the arm never reaches twenty. A bare segmented key in a README, a ``.env`` line or
a JSON blob passed the gate clean. ``skills/eval-integrity`` already documented this exact failure
for a different scanner; this repository's own gate still carried it.

THE TRAP IN THE OBVIOUS REPAIR. Admitting ``-`` into the class catches the key and also fires on
ordinary hyphenated prose, because ``sk-`` is the tail of many words: a skill named
``task-dependency-sequencing`` carries the prefix inside its first word. Measured over this
repository's tracked files before this change, the naive class hit three times in two skills. A publication gate that refuses
the repository's own skills gets switched off, which is worse than the miss it was meant to fix.
The prose arm below exists so that repair cannot land.

WHAT THIS FILE DOES NOT ESTABLISH. It does not scan the real tree;
``test_repository_is_publishable.py`` does, and it is what notices a false hit in a tracked file.
It does not pin the pattern NAME that reports a segmented key, or whether the fix is one arm or
two. It pins the class of input that must go red, the class that must stay clean, and that the
scanner's own ``--self-test`` can tell a hyphen-blind arm from a working one.

Assertion prefixes follow the sibling files: CONTROL passes on the old scanner too, REPAIRED fails
on it. Every key-shaped value is assembled from fragments at run time, because the repository test
above scans this file.
"""
import ast
import hashlib
import os
import re
import shutil
import subprocess
import sys
import unicodedata
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCANNER = REPO / "_tools" / "scan_gate.py"
REPORT_REL = Path("_reports") / "scan_report.txt"

IDENTITY_TERM = "synthetic" + "fixture" + "person"
BANNED_FIXTURE_PHRASE = "prohibited" + "-" + "fixture" + "-" + "phrase"
SK = "s" + "k-"
ANT = "an" + "t-"
OLD_FLAT_ARM = "sk-[A-Za-z0-9]{20,}"

# Segment words that real providers put after the prefix. The body alphabet carries ``-`` and
# ``_`` often enough that no alphanumeric run reaches twenty, which is exactly the shape the old
# arm could not see. The alphabet holds no ``s`` or ``k``, so no body can contain the prefix.
SEGMENTS = ["pro" + "j", "svc" + "acct", "ad" + "min", "pro" + "d"]
BODY_ALPHABET = "Tq7_Rw2-Mz9Vb4-Xn8_Lp3"


def _body(n: int, alphabet: str = BODY_ALPHABET) -> str:
    return "".join(alphabet[i % len(alphabet)] for i in range(n))


def segmented_key(segment: str, n: int = 48) -> str:
    return SK + segment + "-" + _body(n)


def flat_key() -> str:
    """The shape the old arm already caught: prefix plus 24 alphanumerics."""
    return SK + _body(24, "Q7w3Zx9Kf2")


WRAPPERS = {
    "line-start": lambda k: k,
    "env-assign": lambda k: "OPENAI" + "_API" + "_KEY=" + k,
    "json-quoted": lambda k: '{"credential": "' + k + '"}',
    "bearer": lambda k: "Authorization: Bearer " + k,
    "prose": lambda k: "the value was " + k + " in the log",
}

# Hyphenated prose whose words end in ``sk`` and run on for twenty or more eligible characters.
# The first is the tracked skill name that broke the naive repair.
PROSE_TRAPS = [
    "tas" + "k-dependency-sequencing",
    "ris" + "k-assessment-and-mitigation-plan",
    "des" + "k-research-methodology-notes",
    "dis" + "k-usage-monitoring-dashboard-config",
    "as" + "k-before-acting-if-you-disagree",
    "my_tas" + "k-queue_worker_pool_settings",
    # A digit in the tail satisfies the segmented arm's body rule, so only the letter directly
    # before the prefix keeps these two clean: they are what pins the left boundary.
    "tas" + "k-migration-2024-q3-rollout-plan",
    "des" + "k-v2-research-methodology-notes-2025",
]


# ---------------------------------------------------------------------------------------------
# Harness: the same conventions as test_scan_gate_publication.py, kept local so this file
# stands alone.
# ---------------------------------------------------------------------------------------------

def clean_env(tmp_path: Path) -> dict:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    return {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(home),
        "TMPDIR": str(tmp_path),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "LC_ALL": "C",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUTF8": "1",
    }


def make_tool(tmp_path: Path, source: str | None = None, name: str = "tool") -> Path:
    tool = tmp_path / name
    tool.mkdir(exist_ok=True)
    driver = tool / "scan_gate.py"
    if source is None:
        shutil.copy(SCANNER, driver)
    else:
        driver.write_text(source, encoding="utf8")
    (tool / "identity_terms.txt").write_text(IDENTITY_TERM + "\n", encoding="utf8")
    norm = unicodedata.normalize("NFC", BANNED_FIXTURE_PHRASE).casefold()
    (tool / "owner_banned.hashes").write_text(
        "# synthetic fixture policy\n"
        f"{hashlib.sha256(norm.encode('utf-8')).hexdigest()} {len(norm)}\n", encoding="utf8")
    return driver


def run(tmp_path: Path, driver: Path, *argv: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(driver), *argv], capture_output=True, text=True,
                          encoding="utf8", errors="replace", env=clean_env(tmp_path))


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf8")


def report(staging: Path) -> str:
    p = staging / REPORT_REL
    return p.read_text(encoding="utf8") if p.is_file() else ""


def secret_hits(staging: Path, rel: str) -> list[str]:
    """Report lines of class SECRET, surface content, at exactly ``rel`` line 1."""
    out = []
    for ln in report(staging).splitlines():
        parts = ln.split("\t")
        if len(parts) == 4 and parts[0] == "SECRET" and parts[2] == "content" \
                and parts[3] == rel + ":1":
            out.append(parts[1])
    return out


def assert_values_absent(values: list[str], staging: Path, proc: subprocess.CompletedProcess) -> None:
    for value in values:
        for label, text in (("stdout", proc.stdout), ("stderr", proc.stderr),
                            ("report", report(staging))):
            assert value not in text, f"CONTROL: synthetic value leaked into {label}"


# ---------------------------------------------------------------------------------------------
# 1. Segmented keys go red, in every wrapper a real leak arrives in.
# ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("segment", SEGMENTS)
@pytest.mark.parametrize("wrapper", sorted(WRAPPERS))
def test_segmented_key_is_a_secret(tmp_path: Path, segment: str, wrapper: str) -> None:
    """REPAIRED: a bare segmented key blocks the scan and is reported as a SECRET at its line.

    The env-assign and json-quoted wrappers kill a repair whose left boundary demands whitespace:
    a key after ``=`` or ``"`` is the commonest real leak. The prose wrapper kills a boundary that
    demands line start.
    """
    driver = make_tool(tmp_path)
    staging = tmp_path / "staging"
    key = segmented_key(segment)
    write(staging / "docs" / "leak.txt", WRAPPERS[wrapper](key) + "\n")
    proc = run(tmp_path, driver, str(staging))
    assert proc.returncode == 1, \
        f"REPAIRED: a segmented key ({segment}, {wrapper}) must block the scan, rc={proc.returncode}"
    assert secret_hits(staging, "docs/leak.txt"), \
        f"REPAIRED: the segmented key ({segment}, {wrapper}) must be reported as a SECRET at line 1"
    assert_values_absent([key[len(SK):]], staging, proc)


# ---------------------------------------------------------------------------------------------
# 2. What must stay exactly as it was.
# ---------------------------------------------------------------------------------------------

def test_flat_key_still_fires(tmp_path: Path) -> None:
    """CONTROL: the flat shape the old arm caught is still caught."""
    driver = make_tool(tmp_path)
    staging = tmp_path / "staging"
    key = flat_key()
    write(staging / "docs" / "flat.txt", "value " + key + "\n")
    proc = run(tmp_path, driver, str(staging))
    assert proc.returncode == 1, "CONTROL: a flat key still blocks"
    assert secret_hits(staging, "docs/flat.txt"), "CONTROL: a flat key is still a SECRET at line 1"
    assert_values_absent([key[len(SK):]], staging, proc)


# Kebab-case identifiers that themselves BEGIN with the prefix: a CSS class, a library-style name,
# a flag. The old arm never fired on them (a hyphen ends its class). A repair that simply admits
# hyphens into the body fires on every one, and a publication gate that refuses a stylesheet gets
# switched off. Assembled so no contiguous prefix-led literal sits in this file.
KEBAB_NON_KEYS = [
    SK + "learn-linear-regression",
    SK + "primary-button-background",
    SK + "one-two-three-four-five",
    'class="' + SK + 'primary-foreground-muted"',
    SK + "experimental-feature-enabled",
    SK + "dependency-sequencing",
    SK + "-" * 20,
]


@pytest.mark.parametrize("text", KEBAB_NON_KEYS, ids=range(len(KEBAB_NON_KEYS)))
def test_kebab_identifier_starting_with_the_prefix_is_clean(tmp_path: Path, text: str) -> None:
    """CONTROL (the old arm passes): a lowercase kebab-case identifier that starts with the prefix
    is not a key. Kills the naive widened class, which counts hyphens toward the length."""
    driver = make_tool(tmp_path)
    staging = tmp_path / "staging"
    write(staging / "web" / "style.md", "the element uses " + text + " here\n")
    proc = run(tmp_path, driver, str(staging))
    assert proc.returncode == 0 and report(staging) == "scan_gate: CLEAN\n", \
        f"CONTROL: kebab identifier #{KEBAB_NON_KEYS.index(text)} must stay clean; report={report(staging)!r}"


def test_key_glued_after_letters_still_fires(tmp_path: Path) -> None:
    """CONTROL: the old arm had no left boundary, so a flat key concatenated onto a word
    (``KEY`` + key) fired. A repair must not lose that class for the flat shape."""
    driver = make_tool(tmp_path)
    staging = tmp_path / "staging"
    key = flat_key()
    write(staging / "docs" / "glued.txt", "KEY" + key + "\n")
    proc = run(tmp_path, driver, str(staging))
    assert proc.returncode == 1 and secret_hits(staging, "docs/glued.txt"), \
        "CONTROL: a flat key glued after letters still blocks and is a SECRET"
    assert_values_absent([key[len(SK):]], staging, proc)


@pytest.mark.parametrize("glue", ["-", "_"])
def test_key_glued_after_a_separator_still_fires(tmp_path: Path, glue: str) -> None:
    """CONTROL: the old arm had no left boundary, so a flat key glued to a word by ``-`` or ``_``
    fired. A left boundary that also excludes those separators is a regression on a class the old
    scanner caught; the boundary only has to exclude a letter or digit before the prefix."""
    driver = make_tool(tmp_path)
    staging = tmp_path / "staging"
    key = flat_key()
    write(staging / "docs" / "glued.txt", "config" + glue + key + "\n")
    proc = run(tmp_path, driver, str(staging))
    assert proc.returncode == 1, f"CONTROL: a key glued after {glue!r} still blocks"
    assert secret_hits(staging, "docs/glued.txt"), f"CONTROL: a key glued after {glue!r} is a SECRET"
    assert_values_absent([key[len(SK):]], staging, proc)


def test_anthropic_key_still_reported_by_its_own_arm(tmp_path: Path) -> None:
    """CONTROL: a widened arm must not displace the dedicated one. Whether the widened arm ALSO
    reports it is pinned elsewhere (test_key_boundary_controls asserts the dedicated arm alone)."""
    driver = make_tool(tmp_path)
    staging = tmp_path / "staging"
    key = SK + ANT + _body(40, "aB3_cD4-")
    write(staging / "docs" / "ant.txt", "value " + key + "\n")
    proc = run(tmp_path, driver, str(staging))
    assert proc.returncode == 1, "CONTROL: an anthropic-shaped key still blocks"
    assert "anthropic-key" in secret_hits(staging, "docs/ant.txt"), \
        "CONTROL: the dedicated anthropic arm still reports its own shape"


@pytest.mark.parametrize("trap", PROSE_TRAPS)
def test_hyphenated_prose_is_clean(tmp_path: Path, trap: str) -> None:
    """CONTROL (passes on the old arm, and must survive the repair): a word ending in ``sk``
    followed by a long hyphenated tail is prose, not a key. Kills the naive widened class; the two
    digit-bearing traps also kill a segmented arm that drops its letter-or-digit left boundary.
    A key after ``_`` or ``-`` must still fire (``test_key_glued_after_a_separator_still_fires``).
    """
    driver = make_tool(tmp_path)
    staging = tmp_path / "staging"
    write(staging / "skills" / "prose.md",
          "Run the " + trap + " procedure first, then `" + trap + "` again.\n")
    proc = run(tmp_path, driver, str(staging))
    assert proc.returncode == 0 and report(staging) == "scan_gate: CLEAN\n", \
        f"CONTROL: hyphenated prose {trap!r} must stay clean; report={report(staging)!r}"


def test_documented_prefixes_are_clean(tmp_path: Path) -> None:
    """CONTROL: naming a prefix in documentation is not a leak. A segmented prefix with a short
    tail stays below the threshold."""
    driver = make_tool(tmp_path)
    staging = tmp_path / "staging"
    lines = ["Project keys begin with " + SK + seg + "- and are long strings." for seg in SEGMENTS]
    lines.append("A short placeholder such as " + SK + SEGMENTS[0] + "-abc12 is not a key.")
    write(staging / "docs" / "prefixes.md", "\n".join(lines) + "\n")
    proc = run(tmp_path, driver, str(staging))
    assert proc.returncode == 0 and report(staging) == "scan_gate: CLEAN\n", \
        f"CONTROL: documented prefixes stay clean; report={report(staging)!r}"


# ---------------------------------------------------------------------------------------------
# 3. The scanner's own --self-test must be able to tell a hyphen-blind arm from a working one.
# ---------------------------------------------------------------------------------------------

def _hyphen_aware_arm_sites(source: str) -> list[tuple[int, int, int, int]]:
    """Source spans of every regex string literal in SECRET_PATTERNS that matches a segmented
    key but is not the dedicated anthropic arm. Found by BEHAVIOUR, not by name, so the test
    survives a rename or a split into two arms."""
    probe = segmented_key(SEGMENTS[0])
    tree = ast.parse(source)
    sites = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "SECRET_PATTERNS" for t in node.targets):
            for elt in node.value.elts:
                if not (isinstance(elt, ast.Tuple) and len(elt.elts) == 2):
                    continue
                name, call = elt.elts
                if not (isinstance(call, ast.Call) and call.args
                        and isinstance(call.args[0], ast.Constant)
                        and isinstance(call.args[0].value, str)):
                    continue
                if isinstance(name, ast.Constant) and name.value == "anthropic-key":
                    continue
                if re.search(call.args[0].value, probe):
                    a = call.args[0]
                    sites.append((a.lineno, a.col_offset, a.end_lineno, a.end_col_offset))
    return sites


def _neutralise(source: str, sites: list[tuple[int, int, int, int]]) -> str:
    """Replace each site's regex literal with the old hyphen-blind arm, last site first."""
    lines = source.splitlines(keepends=True)
    for lineno, col, end_lineno, end_col in sorted(sites, reverse=True):
        assert lineno == end_lineno, "CONTROL: each regex literal sits on one line"
        line = lines[lineno - 1]
        lines[lineno - 1] = line[:col] + "r'" + OLD_FLAT_ARM + "'" + line[end_col:]
    return "".join(lines)


def test_self_test_passes_on_the_candidate(tmp_path: Path) -> None:
    """CONTROL: the shipped scanner's own self-test passes."""
    proc = run(tmp_path, make_tool(tmp_path), "--self-test")
    assert proc.returncode == 0 and "self-test: PASS" in proc.stdout, \
        f"CONTROL: --self-test passes; stdout={proc.stdout!r}"


def test_self_test_goes_red_when_the_arm_is_hyphen_blind(tmp_path: Path) -> None:
    """REPAIRED: put the old hyphen-blind regex back in every arm that catches a segmented key,
    and the scanner's own ``--self-test`` must FAIL.

    Why here and not only in pytest: the pre-push hook runs ``--self-test``, not this suite. A
    self-test that plants only a flat key cannot notice the regression this file is about, so the
    hook would keep passing a scanner that had quietly lost the repair.

    On the old scanner no arm catches a segmented key, so there is nothing to neutralise and the
    first assertion is the RED.
    """
    source = SCANNER.read_text(encoding="utf8")
    sites = _hyphen_aware_arm_sites(source)
    assert sites, "REPAIRED: some non-anthropic SECRET arm must match a segmented key"
    blind = _neutralise(source, sites)
    assert not _hyphen_aware_arm_sites(blind), "CONTROL: the mutation removed every such arm"
    driver = make_tool(tmp_path, source=blind, name="tool_blind")
    proc = run(tmp_path, driver, "--self-test")
    assert proc.returncode == 1 and "self-test: FAIL" in proc.stdout, \
        "REPAIRED: a hyphen-blind scanner must fail its own --self-test; " \
        f"rc={proc.returncode} stdout={proc.stdout!r}"
    assert "self-test: PASS" not in proc.stdout, "REPAIRED: no PASS verdict from a hyphen-blind scanner"
