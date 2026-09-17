"""The OWNER_POLICY arm: a third class that is neither a credential nor an identity.

WHY THIS ARM EXISTS. The owner's standing rule is that one English phrase must never appear in a
published tree. A 2026-08-25 scrub removed it; it came back TWICE within 21 days, in code comments,
through a gate that was working exactly as designed — because the gate had two arms (credential
shapes, identity terms) and the phrase is neither. A one-shot tree edit does not hold a rule; a
checker does.

WHY A TRACKED HASH FILE. A public plaintext list publishes the phrase it bans. A second GITIGNORED
list cannot be enforced from a fresh clone, and the adopter path is already
``identity_terms.example.txt`` -> ``identity_terms.txt``: a second required private file means the
arm is quietly absent on any machine that forgot it, which is a check that cannot fail. Hashes are
tracked, so the arm travels with the clone.

WHAT THESE TESTS USE. A SYNTHETIC phrase, assembled from fragments per the convention at the top of
test_scan_gate_publication.py. The real prohibited phrase appears in no fixture, no tree, and no
tracked source here — testing the mechanism does not require possessing the value.
"""
import hashlib
import importlib.util
import re
from pathlib import Path
import shutil
import subprocess
import unicodedata

import pytest

REPO = Path(__file__).resolve().parents[2]
SCANNER = REPO / "_tools" / "scan_gate.py"
HASHES = REPO / "_tools" / "owner_banned.hashes"

FIXTURE_PHRASE = "prohibited" + "-" + "fixture" + "-" + "phrase"
IDENTITY_TERM = "synthetic" + "fixture" + "person"


def _norm(text: str) -> str:
    """NFC + casefold — ASCII-equivalent to the module's `_banned_norm` for these fixtures.

    Kept separate ONLY because the digest has to be computed BEFORE a scanner copy exists to import.
    It is pinned to the real normaliser by `test_the_fixture_normaliser_matches_production`, so the
    two cannot drift apart on a future row that is not plain ASCII (team review, 2026-09-17).
    """
    return unicodedata.normalize("NFC", text).casefold()


def load_scanner(tmp_path: Path, hashes_body: str | None = None):
    """Import a copy of the scanner with synthetic policy files beside it."""
    tool = tmp_path / "tool"
    tool.mkdir(exist_ok=True)
    driver = tool / "scan_gate.py"
    shutil.copy(SCANNER, driver)
    (tool / "identity_terms.txt").write_text(IDENTITY_TERM + "\n", encoding="utf-8")
    if hashes_body is None:
        digest = hashlib.sha256(_norm(FIXTURE_PHRASE).encode("utf-8")).hexdigest()
        hashes_body = f"# synthetic fixture policy\n{digest} {len(_norm(FIXTURE_PHRASE))}\n"
    (tool / "owner_banned.hashes").write_text(hashes_body, encoding="utf-8")
    spec = importlib.util.spec_from_file_location(f"scan_gate_{tmp_path.name}", driver)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def banned_hits(module, staging: Path):
    return [h for h in module.scan(str(staging)) if h[2] == "BANNED"]


def make_staging(tmp_path: Path) -> Path:
    staging = tmp_path / "staging"
    staging.mkdir(exist_ok=True)
    return staging


# ---------------------------------------------------------------- it CAN fire

def test_phrase_in_content_is_caught(tmp_path):
    module = load_scanner(tmp_path)
    staging = make_staging(tmp_path)
    (staging / "notes.md").write_text(
        f"the disclosure is only {FIXTURE_PHRASE} while it is accurate\n", encoding="utf-8")
    hits = banned_hits(module, staging)
    assert hits == [("notes.md", 1, "BANNED", "owner-phrase", "content")], hits


def test_phrase_in_a_FILENAME_is_caught(tmp_path):
    """A path is published bytes too — the name arm must carry the same rule as the content arm."""
    module = load_scanner(tmp_path)
    staging = make_staging(tmp_path)
    (staging / f"{FIXTURE_PHRASE}.md").write_text("nothing prohibited in the body\n", encoding="utf-8")
    hits = banned_hits(module, staging)
    assert hits == [(f"{FIXTURE_PHRASE}.md", 0, "BANNED", "owner-phrase", "name")], hits


def test_case_variant_is_caught(tmp_path):
    """The live recurrence included an UPPERCASE spelling; a case-sensitive arm would have missed it
    exactly as the first hand sweep for it did."""
    module = load_scanner(tmp_path)
    staging = make_staging(tmp_path)
    (staging / "shout.md").write_text(f"# TABLE {FIXTURE_PHRASE.upper()}: see below\n", encoding="utf-8")
    assert len(banned_hits(module, staging)) == 1


# ------------------------------------------------- it does NOT fire on everything

@pytest.mark.parametrize("line, why", [
    (FIXTURE_PHRASE.replace("-", " "), "spaces instead of hyphens is a different string"),
    ("-".join(reversed(FIXTURE_PHRASE.split("-"))), "reversed segments"),
    ("the disclosure is only necessary while it is accurate", "the sanctioned synonym"),
    ("the quick brown fox jumps over the lazy dog", "ordinary prose"),
])
def test_near_misses_and_clean_text_do_not_fire(tmp_path, line, why):
    module = load_scanner(tmp_path)
    staging = make_staging(tmp_path)
    (staging / "near.md").write_text(line + "\n", encoding="utf-8")
    assert banned_hits(module, staging) == [], why


# ------------------------------------------- a missing policy REFUSES, never passes

def test_missing_hash_file_refuses_rather_than_scanning(tmp_path):
    module = load_scanner(tmp_path)
    (tmp_path / "tool" / "owner_banned.hashes").unlink()
    staging = make_staging(tmp_path)
    (staging / "notes.md").write_text(f"{FIXTURE_PHRASE}\n", encoding="utf-8")
    with pytest.raises(module.ScanRefused) as caught:
        module.scan(str(staging))
    assert "owner-banned-hashes" in str(caught.value)


def test_comments_only_hash_file_refuses_and_does_not_become_a_never_match(tmp_path):
    """The distinction that matters: a data-empty policy must REFUSE, not compile to a pattern that
    can never fire. A never-match arm reports CLEAN forever and reads as coverage."""
    module = load_scanner(tmp_path, hashes_body="# only comments, no data rows\n\n")
    staging = make_staging(tmp_path)
    (staging / "notes.md").write_text(f"{FIXTURE_PHRASE}\n", encoding="utf-8")
    with pytest.raises(module.ScanRefused) as caught:
        module.scan(str(staging))
    assert "empty-owner-banned-hashes" in str(caught.value)


@pytest.mark.parametrize("body", [
    "not-a-hash 12\n",
    "ae53fbb31ca36bdcab70bec9d53c1c796bda1acd53fcc519c240b7c096610aec\n",   # no width
    "ae53fbb31ca36bdcab70bec9d53c1c796bda1acd53fcc519c240b7c096610aec zero\n",
    "ae53fbb31ca36bdcab70bec9d53c1c796bda1acd53fcc519c240b7c096610aec 0\n",
    # str.isdigit() is True for the compatibility superscripts and int() then raises ValueError,
    # so the previous guard CRASHED here instead of refusing. Failing closed is not the same as
    # failing the way the loader's docstring says it will (team review, 2026-09-17).
    "ae53fbb31ca36bdcab70bec9d53c1c796bda1acd53fcc519c240b7c096610aec \u00b9\u00b2\n",
])
def test_malformed_hash_file_refuses(tmp_path, body):
    module = load_scanner(tmp_path, hashes_body=body)
    staging = make_staging(tmp_path)
    (staging / "notes.md").write_text("ordinary text\n", encoding="utf-8")
    with pytest.raises(module.ScanRefused):
        module.scan(str(staging))


# ------------------------------------------------------- the SHIPPED policy file

def test_the_tracked_hash_file_is_present_and_well_formed():
    """The arm is only as real as the list that travels with the clone."""
    assert HASHES.is_file(), "the tracked policy file must exist or every scan refuses"
    # PRESENT is not TRACKED, and only tracked reaches a fresh clone. An untracked local copy
    # satisfies is_file() on the author's box and leaves every other clone refusing every scan --
    # fail-closed, but the arm would be shipped broken and the author would never see it.
    listed = subprocess.run(["git", "-C", str(REPO), "ls-files", "--error-unmatch",
                             "_tools/owner_banned.hashes"], capture_output=True, text=True)
    assert listed.returncode == 0, (
        "the policy file is present but NOT tracked by git; every fresh clone would refuse: %s"
        % listed.stderr.strip())
    rows = [ln.split() for ln in HASHES.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.startswith("#")]
    assert rows, "a comments-only shipped policy would refuse every scan in every clone"
    for digest, width in rows:
        assert len(digest) == 64 and all(c in "0123456789abcdef" for c in digest.lower())
        # The SAME predicate the loader uses, deliberately. `width.isdigit()` was the guard the
        # loader had to drop: it is True for the compatibility superscripts, and `int()` then raises.
        # Left here it would ERROR rather than FAIL on a superscripted shipped width — a test that
        # crashes instead of reporting is not a check (team review, 2026-09-17).
        assert re.fullmatch(r"[1-9][0-9]*", width), width


def test_the_tracked_hash_file_carries_no_plaintext():
    """Whatever else it holds, it must not hold a phrase."""
    for line in HASHES.read_text(encoding="utf-8").splitlines():
        if line.startswith("#") or not line.strip():
            continue
        digest, width = line.split()
        assert len(line.split()) == 2 and len(digest) == 64
        assert re.fullmatch(r"[1-9][0-9]*", width), width


# ------------------------------------- a WRAP is the shape the failure class actually takes

def test_a_phrase_wrapped_across_a_line_break_is_caught(tmp_path):
    """The two live recurrences each sat on one line, so a per-line scan found them. What PUT
    them there was an editor reflowing a comment, and the next reflow can land the break inside
    the phrase instead of beside it. A guard calibrated to the instances already found is
    calibrated to the sample, not to the class."""
    module = load_scanner(tmp_path)
    staging = make_staging(tmp_path)
    head, tail = FIXTURE_PHRASE[:13], FIXTURE_PHRASE[13:]
    (staging / "wrapped.md").write_text(f"a sentence ending in {head}\n{tail} and continuing\n",
                                        encoding="utf-8")
    assert banned_hits(module, staging), "a phrase split across a line break must still be caught"


def test_a_wrap_carrying_a_continuation_comment_marker_is_caught(tmp_path):
    """What a wrap inserts into the phrase is the continuation line's own marker."""
    module = load_scanner(tmp_path)
    staging = make_staging(tmp_path)
    head, tail = FIXTURE_PHRASE[:13], FIXTURE_PHRASE[13:]
    (staging / "wrapped.py").write_text(f"# a comment ending in {head}\n# {tail} and continuing\n",
                                        encoding="utf-8")
    assert banned_hits(module, staging), "a reflowed COMMENT is the exact live failure mode"


def test_a_wrap_is_reported_once_not_twice(tmp_path):
    module = load_scanner(tmp_path)
    staging = make_staging(tmp_path)
    head, tail = FIXTURE_PHRASE[:13], FIXTURE_PHRASE[13:]
    (staging / "wrapped.md").write_text(f"ending in {head}\n{tail} and continuing\n",
                                        encoding="utf-8")
    assert len(banned_hits(module, staging)) == 1


@pytest.mark.parametrize("line, why", [
    ("a line that simply ends here", "an ordinary line break joins nothing"),
    (FIXTURE_PHRASE.split("-")[0] + "\n" + "-".join(FIXTURE_PHRASE.split("-")[1:]),
     "a break at a hyphen that DROPS the hyphen is a different string"),
])
def test_ordinary_line_breaks_do_not_fire(tmp_path, line, why):
    module = load_scanner(tmp_path)
    staging = make_staging(tmp_path)
    (staging / "plain.md").write_text(line + "\nand another line entirely\n", encoding="utf-8")
    assert banned_hits(module, staging) == [], why


# --------------------------------- what a WORD PROCESSOR changes is still the same phrase

@pytest.mark.parametrize("variant, why", [
    (FIXTURE_PHRASE.replace("-", "\u2010", 1), "U+2010 HYPHEN, category Pd"),
    (FIXTURE_PHRASE.replace("-", "\u2011", 1), "U+2011 NON-BREAKING HYPHEN, category Pd"),
    (FIXTURE_PHRASE.replace("-", "\u2013", 1), "U+2013 EN DASH, category Pd"),
    (FIXTURE_PHRASE.replace("-", "\u2212", 1), "U+2212 MINUS SIGN, category Sm"),
    (FIXTURE_PHRASE[:5] + "\u200b" + FIXTURE_PHRASE[5:], "ZERO WIDTH SPACE, category Cf"),
    (FIXTURE_PHRASE[:5] + "\u00ad" + FIXTURE_PHRASE[5:], "SOFT HYPHEN, category Cf"),
    ("\ufeff" + FIXTURE_PHRASE, "a BOM in front of it"),
])
def test_editor_substitutions_are_still_the_same_phrase(tmp_path, variant, why):
    """NFC composes; it does not strip format characters and does not fold punctuation dashes.
    A phrase pasted back out of a word processor reads identically to whoever sees it published,
    so it must read identically here. Calibrated at sloppy editors, NOT at an adversary: base64,
    percent-encoding and homoglyphs are deliberately out of scope."""
    module = load_scanner(tmp_path)
    staging = make_staging(tmp_path)
    (staging / "pasted.md").write_text(variant + "\n", encoding="utf-8")
    assert banned_hits(module, staging), why


def test_the_fixture_normaliser_matches_production(tmp_path):
    """The fixtures hash with `_norm`; the scanner matches with `_banned_norm`. On ASCII they agree,
    so a divergence would be silent until the first non-ASCII policy row — at which point every
    fixture would be hashing a different string than the scanner looks for, and the tests would go
    green on an arm that could no longer fire."""
    module = load_scanner(tmp_path)
    for probe in (FIXTURE_PHRASE, FIXTURE_PHRASE.upper(), IDENTITY_TERM, "Plain ASCII Prose"):
        assert _norm(probe) == module._banned_norm(probe), probe
