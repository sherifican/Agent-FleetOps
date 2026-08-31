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
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from guard import scrub_arm  # noqa: E402

MARKER = "SCRUB ARM HAS TEETH - ALL CHECKS PASSED"

# Built by concatenation — see the module docstring.
ADDR = "10." + "20.30.40"
HOME_UNIX = "/ho" + "me/plantedperson/notes.md"
HOME_WIN = "C:" + "\\Users" + "\\plantedperson\\work"
TILDE_ACCOUNT = "~" + "plantedperson/projects/notes.md"
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


def test_baseline_flags_the_tilde_account_form():
    """The tilde shorthand names the same account as the full home path, and the arm could not
    see it: only the spelled-out form was covered."""
    rc, out = _scan_one("the tree also lives at %s\n" % TILDE_ACCOUNT)
    assert rc == 1 and "home-path-tilde" in out, out


def test_the_account_less_home_anchors_stay_clean():
    """The stated scope limit, kept honest: a bare home anchor names no account, and the corpus
    documents adopter config paths that way. What ships one as a LAYOUT is a fallback default,
    gated by guard/tests/test_shipped_defaults.py."""
    rc, out = _scan_one("keep artifacts under ~/artifacts and $HOME/.config/tool.json\n")
    assert rc == 0, out


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


def _overlay_run(corpus_files, overlay_text, extra=()):
    """Scan one tree with an overlay kept OUTSIDE it — the arrangement the docs describe.

    Both overlay gates used to write overlay.txt INSIDE --root, where each literal rule matched
    its OWN line. Both passed on that self-hit alone: making the intended target undetectable
    left them green and the suite still printed its all-pass marker. The overlay now lives in a
    separate directory, and the assertions name the FILE and LINE the hit must land on.
    """
    with tempfile.TemporaryDirectory() as scanned, tempfile.TemporaryDirectory() as private:
        _corpus(scanned, corpus_files)
        ov = os.path.join(private, "overlay.txt")
        with open(ov, "w", encoding="utf-8") as fh:
            fh.write(overlay_text)
        return _run(["--root", scanned, "--profile", "maintainer", "--overlay", ov, *extra])


def test_maintainer_overlay_phrase_is_flagged():
    rc, out = _overlay_run(
        {"doc.md": "an ordinary first line\ncarries the planted overlay phrase here\n"},
        "# private overlay\nplanted overlay phrase\n")
    flags = [l.strip() for l in out.splitlines() if l.strip().startswith("\u26d4")]
    assert rc == 1 and len(flags) == 1, out
    assert flags[0].startswith("\u26d4 doc.md:2 [private-material/overlay:2]"), out


def test_overlay_quoted_speech_class_prefix():
    rc, out = _overlay_run(
        {"doc.md": "an ordinary first line\na planted spoken marker sits here\n"},
        "quoted-speech:planted spoken marker\n")
    flags = [l.strip() for l in out.splitlines() if l.strip().startswith("\u26d4")]
    assert rc == 1 and len(flags) == 1, out
    assert flags[0].startswith("\u26d4 doc.md:2 [quoted-speech/overlay:1]"), out


def test_an_overlay_that_compiles_to_zero_rules_is_cannot_check():
    for text in ("", "\n\n", "# only a comment\n#and another\n"):
        rc, out = _overlay_run({"doc.md": "nothing private here\n"}, text)
        assert rc == 2 and "CANNOT_CHECK" in out, repr(text) + ":\n" + out
        assert "ZERO rules" in out, out


def test_an_overlay_under_a_non_maintainer_profile_is_refused():
    """The runner defaults to the adopter profile, so this is the easy mistake to make."""
    with tempfile.TemporaryDirectory() as scanned, tempfile.TemporaryDirectory() as private:
        _corpus(scanned, {"doc.md": "the planted overlay phrase ships here\n"})
        ov = os.path.join(private, "overlay.txt")
        with open(ov, "w", encoding="utf-8") as fh:
            fh.write("planted overlay phrase\n")
        rc, out = _run(["--root", scanned, "--profile", "adopter", "--overlay", ov])
    assert rc != 0, "an ignored overlay printed a green —\n" + out
    assert rc == 2 and "CANNOT_CHECK" in out, out


def test_an_overlay_inside_the_scanned_tree_is_refused():
    with tempfile.TemporaryDirectory() as d:
        _corpus(d, {"doc.md": "the planted overlay phrase ships here\n"})
        ov = os.path.join(d, "overlay.txt")
        with open(ov, "w", encoding="utf-8") as fh:
            fh.write("planted[ ]overlay phrase\n")     # regex syntax: it never flags itself
        rc, out = _run(["--root", d, "--profile", "maintainer", "--overlay", ov])
    assert rc == 2 and "OUTSIDE the scanned tree" in out, \
        "a denylist inside the corpus ships the strings it exists to keep out —\n" + out


def test_a_tracked_overlay_is_refused_even_from_outside_the_root():
    with tempfile.TemporaryDirectory() as home:
        repo = os.path.join(home, "repo")
        os.makedirs(os.path.join(repo, "sub"))
        _new_git_tree(repo)
        ov = os.path.join(repo, "overlay.txt")
        with open(ov, "w", encoding="utf-8") as fh:
            fh.write("planted[ ]overlay phrase\n")
        _corpus(os.path.join(repo, "sub"), {"doc.md": "the planted overlay phrase ships here\n"})
        subprocess.run(["git", "-C", repo, "add", "-A"], check=True, capture_output=True)
        rc, out = _run(["--root", os.path.join(repo, "sub"), "--profile", "maintainer",
                        "--overlay", ov])
    assert rc == 2 and "git tracks it" in out, \
        "an overlay outside --root but tracked still gets published —\n" + out


def test_adopter_profile_does_not_require_overlay():
    rc, out = _scan_one("nothing private here\n", ("--profile", "adopter"))
    assert rc == 0, out


def test_only_the_named_plant_is_exempt_other_fixtures_are_scanned():
    """REPLACES test_fixture_dir_is_excluded_from_scan, which pinned the defect.

    That test asserted a DIRECTORY-prefix exemption as intended behaviour, so a private address
    planted in any other fixture was invisible and the suite called that correct. The exemption
    is now per-file: the committed plant is excused by name, and every other fixture is scanned
    like any other tracked file.
    """
    with tempfile.TemporaryDirectory() as d:
        _corpus(d, {"guard/tests/fixtures/other_fixture.txt": "at %s\n" % ADDR,
                    "guard/tests/fixtures/scrub_plant.txt": "PLANT: at %s\n" % ADDR,
                    "doc.md": "clean\n"})
        rc, out = _run(["--root", d])
    assert rc == 1 and "other_fixture.txt" in out, \
        "a private address in a non-plant fixture must be caught —\n" + out
    assert "scrub_plant.txt" not in out, "the named plant is the ONLY exemption —\n" + out


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


# ── bytes that do not decode are still bytes git will publish (B4) ──────────────────────────
ADDR2 = "192." + "168.1.7"


def _write_bytes(d, rel, data):
    with open(os.path.join(d, rel), "wb") as fh:
        fh.write(data)


def test_latin1_bytes_carrying_a_private_address_are_not_skipped():
    with tempfile.TemporaryDirectory() as d:
        _write_bytes(d, "legacy.txt", ("caf\u00e9 host at %s\n" % ADDR).encode("latin-1"))
        rc, out = _run(["--root", d])
    assert rc != 0, "a file that failed to decode was reported CLEAN —\n" + out
    assert rc == 1 and "private-address-range" in out, out


def test_utf16_bytes_carrying_a_private_address_are_not_skipped():
    for codec in ("utf-16", "utf-16-le", "utf-16-be"):
        with tempfile.TemporaryDirectory() as d:
            _write_bytes(d, "wide.txt", ("peer at %s\n" % ADDR2).encode(codec))
            rc, out = _run(["--root", d])
        assert rc == 1 and "private-address-range" in out, codec + ":\n" + out


def test_a_file_that_cannot_be_READ_is_still_unmeasured():
    """The distinction the B4 fix must not blur: a decode failure is not an IO failure.

    Bytes that arrive but do not decode get scanned. Bytes that never arrive are UNMEASURED (2),
    because nobody looked at them.
    """
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        return                     # root reads anything; the permission bit proves nothing here
    with tempfile.TemporaryDirectory() as d:
        _corpus(d, {"locked.md": "clean\n"})
        os.chmod(os.path.join(d, "locked.md"), 0)
        try:
            rc, out = _run(["--root", d])
        finally:
            os.chmod(os.path.join(d, "locked.md"), 0o600)
    assert rc == 2 and "UNMEASURED" in out, out


# ── the corpus and the bytes must be the same version (B5) ──────────────────────────────────
def _new_git_tree(d):
    for args in (("init", "-q"), ("config", "user.email", "gate@example.invalid"),
                 ("config", "user.name", "gate")):
        subprocess.run(["git", "-C", d, *args], check=True, capture_output=True)


def test_staged_bytes_are_scanned_not_the_overwritten_worktree():
    with tempfile.TemporaryDirectory() as d:
        _new_git_tree(d)
        _corpus(d, {"staged.md": "reachable at %s here\n" % ADDR})
        subprocess.run(["git", "-C", d, "add", "staged.md"], check=True, capture_output=True)
        _corpus(d, {"staged.md": "public text only\n"})   # worktree hides what is staged
        rc, out = _run(["--root", d])
    assert rc == 1 and "staged.md" in out and "private-address-range" in out, \
        "the scan read the worktree, not the bytes git will publish —\n" + out


def test_a_symlink_is_scanned_as_the_path_it_stores():
    with tempfile.TemporaryDirectory() as d:
        _new_git_tree(d)
        _corpus(d, {"notes.md": "clean\n"})
        os.symlink(HOME_UNIX, os.path.join(d, "shortcut"))
        subprocess.run(["git", "-C", d, "add", "-A"], check=True, capture_output=True)
        rc, out = _run(["--root", d])
    assert rc == 1 and "shortcut" in out and "home-path" in out, \
        "a symlink's public bytes are its stored path —\n" + out


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
