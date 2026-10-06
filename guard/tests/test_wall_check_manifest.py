"""The provenance manifest: every way it can be unusable must end UNMEASURED (2), never 1.

In this repository 1 means "violation" and 2 means "could not measure". A manifest the gate cannot
read is not evidence of a violation; before this test an undecodable manifest escaped as a
traceback, which exits 1 and so reported a crash as a violation. `guard/README.md` (in-repository
provenance) states: absent or invalid manifest -> 2; a file the manifest omits -> 1.
"""
import pathlib
import subprocess
import sys

import pytest

WALL_CHECK = pathlib.Path(__file__).resolve().parents[2] / "_tools" / "wall_check.py"


def _run(staging: pathlib.Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(WALL_CHECK), str(staging)],
                          capture_output=True, text=True, timeout=60)


def _staging(tmp_path: pathlib.Path, manifest: bytes | None) -> pathlib.Path:
    (tmp_path / "a.txt").write_text("hello\n")
    if manifest is not None:
        (tmp_path / "_reports").mkdir()
        (tmp_path / "_reports" / "provenance.tsv").write_bytes(manifest)
    return tmp_path


def test_undecodable_manifest_is_unmeasured(tmp_path):
    r = _run(_staging(tmp_path, b"\xff\xfe not utf-8\n"))
    assert r.returncode == 2, (
        f"an undecodable manifest must be UNMEASURED (2), got {r.returncode}; "
        f"stderr tail: {r.stderr.strip().splitlines()[-1:] }")
    assert "Traceback" not in r.stderr, "an unreadable manifest must be reported, not crash"


@pytest.mark.parametrize("name, manifest, want", [
    ("absent", None, 2),
    ("three_fields", b"a.txt\ta.txt\textra\n", 2),
    ("valid", b"a.txt\ta.txt\n", 0),
    ("omits_the_file", b"# nothing listed\n", 1),
])
def test_control_manifest_exit_codes(tmp_path, name, manifest, want):
    r = _run(_staging(tmp_path, manifest))
    assert r.returncode == want, f"{name}: expected {want}, got {r.returncode}"


# =============================================================================================
# Earlier integration coverage. Written BEFORE the fix.
#
# R6: on exit 2 the tool must not claim a hit count. Today an absent or invalid manifest exits 2
#     (correct) and then prints `wall_check: 0 hit(s) — batch is NOT publishable`, which reads as
#     a measured result of zero. It must say UNMEASURED instead.
# A7b_decode_only: gate gap — a manifest read that catches only UnicodeDecodeError passed the
#     regression suite, because no arm made the manifest UNREADABLE (OSError) rather than undecodable.
# =============================================================================================
import builtins  # noqa: E402
import importlib.util  # noqa: E402
import os  # noqa: E402

VALID_MANIFEST = b"a.txt\ta.txt\n"


# --- R6 RED -----------------------------------------------------------------------------------

@pytest.mark.parametrize("name, manifest", [
    ("absent", None),
    ("three_fields", b"a.txt\ta.txt\textra\n"),
    ("undecodable", b"\xff\xfe not utf-8\n"),
])
def test_exit_2_says_unmeasured_and_claims_no_hit_count(tmp_path, name, manifest):
    r = _run(_staging(tmp_path, manifest))
    out = r.stdout + r.stderr
    assert r.returncode == 2, f"{name}: expected exit 2, got {r.returncode}:\n{out}"
    assert "UNMEASURED" in out, f"{name}: exit 2 must say UNMEASURED, it did not:\n{out}"
    assert "0 hit(s)" not in out, (
        f"{name}: exit 2 printed a hit count — nothing was measured, so there is no count:\n{out}")


# --- R6 CONTROL -------------------------------------------------------------------------------

def test_control_a_real_violation_still_reports_its_hit_count(tmp_path):
    r = _run(_staging(tmp_path, b"# nothing listed\n"))
    out = r.stdout + r.stderr
    assert r.returncode == 1 and "1 hit(s)" in out and "UNMEASURED" not in out, out


def test_control_a_clean_tree_still_reports_clean(tmp_path):
    r = _run(_staging(tmp_path, VALID_MANIFEST))
    out = r.stdout + r.stderr
    assert r.returncode == 0 and "CLEAN" in out and "UNMEASURED" not in out, out


# --- A7b_decode_only gate gap: an UNREADABLE manifest ----------------------------------------

def _unreadable(path: pathlib.Path):
    """chmod 000 and prove it took; a process that can read anyway (root, a permissive
    filesystem) cannot exercise this arm, so it is skipped there — never silently green."""
    path.chmod(0)
    if os.geteuid() == 0:
        pytest.skip("running as root: permission bits do not make the manifest unreadable")
    if os.access(path, os.R_OK):
        pytest.skip("chmod 000 did not make the manifest unreadable on this filesystem")


def test_decode_only_unreadable_manifest_is_unmeasured_not_a_traceback(tmp_path):
    staging = _staging(tmp_path, VALID_MANIFEST)
    manifest = staging / "_reports" / "provenance.tsv"
    control = _run(staging)
    assert control.returncode == 0, (
        f"control: a readable valid manifest must be clean, got {control.returncode}:\n"
        f"{control.stdout}{control.stderr}")
    _unreadable(manifest)
    try:
        r = _run(staging)
    finally:
        manifest.chmod(0o644)
    assert r.returncode == 2, (
        f"an unreadable manifest must be UNMEASURED (2), got {r.returncode}; "
        f"stderr tail: {r.stderr.strip().splitlines()[-1:]}")
    assert "Traceback" not in r.stderr, "an unreadable manifest must be reported, not crash"


def _load_wall_check():
    spec = importlib.util.spec_from_file_location("manifest_read_wall_check", WALL_CHECK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_decode_only_in_process_oserror_on_the_manifest_read_is_unmeasured(
        tmp_path, monkeypatch, capsys):
    """The same fact where permissions cannot be enforced: the manifest EXISTS (isfile is true)
    and the read raises OSError. check() must return 2 with no hits, not propagate."""
    staging = _staging(tmp_path, VALID_MANIFEST)
    mod = _load_wall_check()
    rc, hits = mod.check(str(staging))
    assert (rc, hits) == (0, []), f"control: readable manifest must be clean in-process: {(rc, hits)}"

    real_open = builtins.open
    target = str(staging / "_reports" / "provenance.tsv")

    def refusing_open(file, *args, **kwargs):
        if str(file) == target:
            raise PermissionError(13, "injected: manifest read refused", target)
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", refusing_open)
    try:
        rc, hits = mod.check(str(staging))
    except OSError as exc:
        raise AssertionError(
            f"check() let an OSError from the manifest read escape ({type(exc).__name__}: {exc}) "
            f"— that is a traceback and exit 1, not UNMEASURED") from None
    finally:
        monkeypatch.setattr(builtins, "open", real_open)
    assert rc == 2, f"an unreadable manifest must be UNMEASURED (2), got {rc} with hits {hits}"
    assert hits == [], f"nothing was measured, so there can be no hits: {hits}"


# =============================================================================================
# Additional boundary coverage.
#
# G5 (gate gap): the R6 test rejects only the literal "0 hit(s)"; an exit-2 message saying
#     "1 hit(s)" passes it. On exit 2 NO "<digits> hit(s)" may appear anywhere in the output.
# G7 (gate gap): the earlier in-process arm injects PermissionError only, so a manifest read that
#     catches only (PermissionError, UnicodeDecodeError) passes it while a plain OSError (EIO)
#     escapes as a traceback. Parametrised over three OSError shapes.
# =============================================================================================
import errno  # noqa: E402
import re  # noqa: E402

HIT_COUNT = re.compile(r"\b\d+ hit\(s\)")


@pytest.mark.parametrize("name, manifest", [
    ("absent", None),
    ("three_fields", b"a.txt\ta.txt\textra\n"),
    ("undecodable", b"\xff\xfe not utf-8\n"),
])
def test_exit_2_output_carries_no_hit_count_of_any_value(tmp_path, name, manifest):
    r = _run(_staging(tmp_path, manifest))
    out = r.stdout + r.stderr
    assert r.returncode == 2, f"{name}: expected exit 2, got {r.returncode}:\n{out}"
    assert "UNMEASURED" in out, f"{name}: exit 2 must say UNMEASURED:\n{out}"
    m = HIT_COUNT.search(out)
    assert m is None, (f"{name}: exit 2 claimed a hit count ({m.group(0)!r}) — nothing was measured, "
                       f"so there is no count of any value:\n{out}")


def test_control_exit_1_still_prints_its_hit_count():
    """Positive control for the regex: a real violation's count IS matched by it."""
    assert HIT_COUNT.search("wall_check: 1 hit(s) — batch is NOT publishable").group(0) == "1 hit(s)"


def test_control_exit_1_output_prints_the_count_the_regex_matches(tmp_path):
    r = _run(_staging(tmp_path, b"# nothing listed\n"))
    out = r.stdout + r.stderr
    assert r.returncode == 1 and HIT_COUNT.search(out) is not None and "UNMEASURED" not in out, out


G7_ERRORS = [
    ("plain-OSError-EIO", lambda p: OSError(errno.EIO, "injected: input/output error", p)),
    ("IsADirectoryError", lambda p: IsADirectoryError(errno.EISDIR, "injected: is a directory", p)),
    ("PermissionError", lambda p: PermissionError(errno.EACCES, "injected: permission denied", p)),
]


@pytest.mark.parametrize("label, make", G7_ERRORS, ids=[lbl for lbl, _m in G7_ERRORS])
def test_any_oserror_on_the_manifest_read_is_unmeasured_in_process(
        tmp_path, monkeypatch, label, make):
    staging = _staging(tmp_path, VALID_MANIFEST)
    mod = _load_wall_check()
    assert mod.check(str(staging)) == (0, []), "control: readable manifest must be clean in-process"

    real_open = builtins.open
    target = str(staging / "_reports" / "provenance.tsv")

    def refusing_open(file, *args, **kwargs):
        if str(file) == target:
            raise make(target)
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", refusing_open)
    try:
        prov = mod.provenance(str(staging))
        rc, hits = mod.check(str(staging))
    except OSError as exc:
        raise AssertionError(
            f"{label}: the manifest read let {type(exc).__name__} escape ({exc}) — a traceback and "
            f"exit 1, not UNMEASURED") from None
    finally:
        monkeypatch.setattr(builtins, "open", real_open)
    assert prov is None, f"{label}: provenance() must return None for an unreadable manifest, got {prov!r}"
    assert (rc, hits) == (2, []), f"{label}: check() must be (2, []), got {(rc, hits)}"


# =============================================================================================
# Additional boundary coverage.
#
# G5b (gap): HIT_COUNT matches only "<digits> hit(s)"; an exit-2 message saying "1 hits" passes.
#     Any digit adjacent to hit/hits in any spelling or case is a claimed count.
# G7b (gap): every G7 error is raised by open(); a provenance() that guards OSError around open()
#     only, and UnicodeDecodeError around the read only, passes while an EIO raised by the READ
#     escapes. This arm lets open() succeed and makes the read raise OSError(EIO).
# =============================================================================================
HIT_CLAIM = re.compile(r"\d+\s*(?:hit\(s\)|hits?\b)", re.IGNORECASE)


def test_control_the_hit_claim_regex_matches_every_spelling_and_not_prose():
    """Positive and negative controls for the instrument itself."""
    for text in ("1 hit(s)", "0 hit(s)", "1 hits", "12 hit", "1HITS", "3 Hits", "2\thit"):
        assert HIT_CLAIM.search(text), "the regex must match %r" % text
    for text in ("no hit count", "there is no hit count", "hits: none", "unit 7 hitting the wall"):
        assert not HIT_CLAIM.search(text), "the regex must not match %r" % text


@pytest.mark.parametrize("name, manifest", [
    ("absent", None),
    ("three_fields", b"a.txt\ta.txt\textra\n"),
    ("undecodable", b"\xff\xfe not utf-8\n"),
])
def test_exit_2_output_claims_no_hit_count_in_any_spelling(tmp_path, name, manifest):
    r = _run(_staging(tmp_path, manifest))
    out = r.stdout + r.stderr
    assert r.returncode == 2, f"{name}: expected exit 2, got {r.returncode}:\n{out}"
    assert "UNMEASURED" in out, f"{name}: exit 2 must say UNMEASURED:\n{out}"
    m = HIT_CLAIM.search(out)
    assert m is None, (f"{name}: exit 2 claimed a hit count ({m.group(0)!r}) — nothing was measured, "
                       f"so there is no count in any spelling:\n{out}")


def test_control_exit_1_output_still_claims_its_count(tmp_path):
    r = _run(_staging(tmp_path, b"# nothing listed\n"))
    out = r.stdout + r.stderr
    assert r.returncode == 1 and HIT_CLAIM.search(out) is not None and "UNMEASURED" not in out, out


class _ReadFails:
    """A file object whose open() succeeded and whose every read raises OSError(EIO)."""

    def __init__(self, fh, target):
        self._fh, self._target = fh, target

    def _boom(self, *_a, **_k):
        raise OSError(errno.EIO, "injected: read failed after open succeeded", self._target)

    read = readline = readlines = _boom

    def __iter__(self):
        return self._boom()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return self._fh.__exit__(*exc)

    def __getattr__(self, name):
        return getattr(self._fh, name)


def test_an_oserror_raised_by_the_manifest_read_after_open_succeeds_is_unmeasured(
        tmp_path, monkeypatch):
    staging = _staging(tmp_path, VALID_MANIFEST)
    mod = _load_wall_check()
    assert mod.check(str(staging)) == (0, []), "control: readable manifest must be clean in-process"

    real_open = builtins.open
    target = str(staging / "_reports" / "provenance.tsv")
    opened = []

    def read_failing_open(file, *args, **kwargs):
        fh = real_open(file, *args, **kwargs)
        if str(file) == target:
            opened.append(file)
            return _ReadFails(fh, target)
        return fh

    monkeypatch.setattr(builtins, "open", read_failing_open)
    try:
        prov = mod.provenance(str(staging))
        rc, hits = mod.check(str(staging))
    except OSError as exc:
        raise AssertionError(
            f"the manifest READ let {type(exc).__name__} escape ({exc}) — open() succeeded, the read "
            f"failed, and that is a traceback and exit 1, not UNMEASURED") from None
    finally:
        monkeypatch.setattr(builtins, "open", real_open)
    assert opened, "positive control: the manifest was never opened through the seam"
    assert prov is None, f"provenance() must return None when the read fails, got {prov!r}"
    assert (rc, hits) == (2, []), f"check() must be (2, []), got {(rc, hits)}"


# =============================================================================================
# HIT_CLAIM matches a digit only BEFORE hit/hits; "hits: 1" and
# "hit count 1" pass. The class: on exit 2, no output line that contains the word hit/hits (any
# case) may also contain a digit. Exit 1 must still carry its count on such a line.
# =============================================================================================
HIT_WORD = re.compile(r"\bhits?\b", re.IGNORECASE)
ANY_DIGIT = re.compile(r"\d")


def _hit_lines_with_digits(out):
    return [ln for ln in out.splitlines() if HIT_WORD.search(ln) and ANY_DIGIT.search(ln)]


def test_control_the_instrument_sees_a_digit_anywhere_on_a_hit_line():
    for text in ("hits: 1", "hit count 1", "1 hit(s)", "HIT total = 0", "3 Hits"):
        assert _hit_lines_with_digits(text) == [text], text
    for text in ("there is no hit count", "hits: none", "unit 7 hitting the wall", "1 file checked"):
        assert _hit_lines_with_digits(text) == [], text


@pytest.mark.parametrize("name, manifest", [
    ("absent", None),
    ("three_fields", b"a.txt\ta.txt\textra\n"),
    ("undecodable", b"\xff\xfe not utf-8\n"),
])
def test_no_exit_2_line_pairs_the_word_hit_with_any_digit(tmp_path, name, manifest):
    r = _run(_staging(tmp_path, manifest))
    out = r.stdout + r.stderr
    assert r.returncode == 2, f"{name}: expected exit 2, got {r.returncode}:\n{out}"
    assert "UNMEASURED" in out, f"{name}: exit 2 must say UNMEASURED:\n{out}"
    bad = _hit_lines_with_digits(out)
    assert bad == [], (f"{name}: exit 2 printed a line pairing 'hit' with a digit — that reads as a "
                       f"count where nothing was measured: {bad!r}\n{out}")


def test_control_exit_1_still_pairs_hit_with_its_count(tmp_path):
    r = _run(_staging(tmp_path, b"# nothing listed\n"))
    out = r.stdout + r.stderr
    assert r.returncode == 1 and _hit_lines_with_digits(out) and "UNMEASURED" not in out, out
