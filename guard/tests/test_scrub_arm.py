"""Gate for guard/scrub_arm.py. The implementer must NOT edit this file.

The arm's contract: two pattern classes (private-material, quoted speech), two profiles
(adopter = shipped baseline; maintainer = baseline + private overlay kept OUTSIDE the repo),
and for the maintainer profile an absent overlay is CANNOT_CHECK (exit 2) — never a pass.
Red demos: mutations SA1-SA4 in guard/mutation_harness.py each disable one of those properties
and this gate must go red — a gate that cannot fail is not a gate.

Planted strings below are built by concatenation so the arm's own scan of this test file
stays clean: the plant must exist at RUNTIME in a scratch corpus, never in tracked bytes.

Runs two ways: under pytest (collected with the other unit gates) and standalone —
`python3 guard/tests/test_scrub_arm.py` — printing the all-pass marker the mutation
harness anchors on.
"""
import contextlib
import io
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from guard import scrub_arm  # noqa: E402

MARKER = "SCRUB ARM HAS TEETH - ALL CHECKS PASSED"

# Built by concatenation — see the module docstring.
ADDR = "10." + "20.30.40"
HOME_UNIX = "/ho" + "me/plantedperson/notes.md"
HOME_WIN = "C:" + "\\Users" + "\\plantedperson\\work"
QUOTE_PERSON = "the ow" + 'ner said "planted words"'
QUOTE_SELF = "h" + "e put it him" + 'self as "planted words"'
CLEAN_LINES = "\n".join([
    "bind to 0.0.0.0 or 127.0.0.1 and keep artifacts under ~/artifacts",
    "a documented example home lives at /home/user/project",
    'the log line "I started the job" is agent voice, not a quoted person',
    "the report says PASS and the video says X — neither quotes a person",
])


def _corpus(d, files):
    for rel, content in files.items():
        path = os.path.join(d, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)


def _run(argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = scrub_arm.main(argv)
    return rc, buf.getvalue()


def _scan_one(content, extra_argv=()):
    with tempfile.TemporaryDirectory() as d:
        _corpus(d, {"doc.md": content})
        return _run(["--root", d, *extra_argv])


def test_baseline_flags_private_address():
    rc, out = _scan_one("reachable at %s here\n" % ADDR)
    assert rc == 1 and "private-address-range" in out, out


def test_baseline_flags_unix_home_path():
    rc, out = _scan_one("tree at %s\n" % HOME_UNIX)
    assert rc == 1 and "home-path" in out, out


def test_baseline_flags_windows_home_path():
    rc, out = _scan_one("staged at %s\n" % HOME_WIN)
    assert rc == 1 and "home-path" in out, out


def test_baseline_flags_quoted_person():
    rc, out = _scan_one("%s\n" % QUOTE_PERSON)
    assert rc == 1 and "person-attribution" in out, out


def test_baseline_flags_self_attributed_quote():
    rc, out = _scan_one("%s\n" % QUOTE_SELF)
    assert rc == 1, out


def test_clean_corpus_is_clean():
    rc, out = _scan_one(CLEAN_LINES + "\n")
    assert rc == 0, "over-breadth: the clean corpus was flagged —\n" + out


def test_maintainer_without_overlay_is_cannot_check_never_a_pass():
    rc, out = _scan_one(CLEAN_LINES + "\n", ("--profile", "maintainer"))
    assert rc == 2, "an absent overlay must be CANNOT_CHECK (2), got rc=%s\n%s" % (rc, out)
    assert "CANNOT_CHECK" in out, out


def test_maintainer_overlay_phrase_is_flagged():
    with tempfile.TemporaryDirectory() as d:
        _corpus(d, {"doc.md": "carries the planted overlay phrase here\n"})
        ov = os.path.join(d, "overlay.txt")  # outside the scanned corpus in real use
        with open(ov, "w", encoding="utf-8") as fh:
            fh.write("# private overlay\nplanted overlay phrase\n")
        rc, out = _run(["--root", d, "--profile", "maintainer", "--overlay", ov])
    assert rc == 1 and "overlay:2" in out, out


def test_overlay_quoted_speech_class_prefix():
    with tempfile.TemporaryDirectory() as d:
        _corpus(d, {"doc.md": "a planted spoken marker sits here\n"})
        ov = os.path.join(d, "overlay.txt")
        with open(ov, "w", encoding="utf-8") as fh:
            fh.write("quoted-speech:planted spoken marker\n")
        rc, out = _run(["--root", d, "--profile", "maintainer", "--overlay", ov])
    assert rc == 1 and "[quoted-speech/overlay:1]" in out, out


def test_adopter_profile_does_not_require_overlay():
    rc, out = _scan_one("nothing private here\n", ("--profile", "adopter"))
    assert rc == 0, out


def test_fixture_dir_is_excluded_from_scan():
    with tempfile.TemporaryDirectory() as d:
        _corpus(d, {"guard/tests/fixtures/plantish.txt": "at %s\n" % ADDR, "doc.md": "clean\n"})
        rc, out = _run(["--root", d])
    assert rc == 0, "the committed-plant dir must be excluded from the scan —\n" + out


def test_selftest_green_on_the_committed_plant():
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = scrub_arm.selftest(scrub_arm.PLANT)
    assert rc == 0, "the committed plant must satisfy every baseline rule —\n" + buf.getvalue()


def test_selftest_reds_on_a_neutered_plant():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "plant.txt")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("PLANT: nothing catchable here\nCLEAN: still nothing\n")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = scrub_arm.selftest(p)
    assert rc == 1, "a selftest that cannot fail proves nothing —\n" + buf.getvalue()


# ── the arm's OWN source is public-bound bytes too ──────────────────────────────────────────
# Built by concatenation, per the module docstring: this file must stay clean in tracked bytes.
SELF_QUOTE = "a person put it " + "him" + "self as " + chr(34) + "a planted quote" + chr(34)


def _arm_source():
    path = os.path.join(os.path.dirname(os.path.abspath(scrub_arm.__file__)), "scrub_arm.py")
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def test_arm_source_does_not_flag_itself():
    """A rule DEFINITION must not read as an instance of the thing it looks for.

    Fixed by construction (the pattern is assembled from pieces), never by excusing the file:
    the companion test below proves a real instance in this same file is still caught.
    """
    with tempfile.TemporaryDirectory() as d:
        _corpus(d, {"scrub_arm.py": _arm_source()})
        rc, out = _run(["--root", d])
    assert rc == 0, "the arm's own source matches its own rules —\n" + out


def test_a_real_self_attribution_inside_the_arm_source_is_still_caught():
    src = _arm_source()
    planted_line = len(src.splitlines()) + 1
    with tempfile.TemporaryDirectory() as d:
        _corpus(d, {"scrub_arm.py": src + "# " + SELF_QUOTE + "\n"})
        rc, out = _run(["--root", d])
    flags = [l.strip() for l in out.splitlines() if l.strip().startswith("\u26d4")]
    assert rc == 1 and len(flags) == 1, "expected exactly the planted hit —\n" + out
    assert "scrub_arm.py:%d" % planted_line in flags[0], out
    assert "self-reference-attribution" in flags[0], out


ALL = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    failures = 0
    for fn in ALL:
        try:
            fn()
            print("  ok    %s" % fn.__name__)
        except AssertionError as exc:
            failures += 1
            print("  FAIL  %s: %s" % (fn.__name__, exc))
    if failures:
        print("RESULT: %d check(s) RED" % failures)
        sys.exit(1)
    print(MARKER)
