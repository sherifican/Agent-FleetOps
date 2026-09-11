"""Gate for guard/banner_render.py — the PNG must still be the SVG that is in the tree.

The module ships a --selftest, and this is the pytest-collected caller for it plus a NAMED mutation
arm of its own: `stale-png`, where the SVG is edited and the PNG is left alone. That pair is
indistinguishable from a correct pair by every other property the checker measures — same size,
same picture, same alpha — which is exactly why the stamp exists and why this arm is the one worth
naming.

The fixture is built here rather than copied from the module, so the arm exercises `check()` the way
a caller would.
"""
import hashlib
import importlib.util
import os
import pathlib
import struct
import zlib

MODULE_PATH = pathlib.Path(__file__).resolve().parent.parent / "banner_render.py"
_spec = importlib.util.spec_from_file_location("banner_render", MODULE_PATH)
banner_render = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(banner_render)

SVG_BODY = '<svg viewBox="0 0 4 2" width="4" height="2"><rect/></svg>'
CLEAR = (0, 0, 0, 0)
SOLID = (14, 13, 24, 255)
W, H = 8, 4          # 2x the 4x2 viewBox


def _png(w, h, pixels):
    raw = bytearray()
    for y in range(h):
        raw.append(0)
        for x in range(w):
            raw.extend(pixels[y * w + x])

    def chunk(tag, body):
        return (struct.pack(">I", len(body)) + tag + body +
                struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw)))
            + chunk(b"IEND", b""))


def _trio(root, svg_body=SVG_BODY, stamp_of=None, stamp=True):
    docs = root / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    (docs / "banner.svg").write_text(svg_body, encoding="utf-8")
    pixels = [SOLID] * (W * H)
    for i in (0, W - 1, W * (H - 1), W * H - 1):
        pixels[i] = CLEAR
    (docs / "banner.png").write_bytes(_png(W, H, pixels))
    if stamp:
        body = stamp_of if stamp_of is not None else svg_body
        png_digest = hashlib.sha256((docs / "banner.png").read_bytes()).hexdigest()
        (docs / "banner.stamp").write_text(
            "svg %s\npng %s\n" % (hashlib.sha256(body.encode()).hexdigest(), png_digest),
            encoding="utf-8")
    return root


def test_a_consistent_trio_passes(tmp_path):
    code, report = banner_render.check(str(_trio(tmp_path)))
    assert code == 0, report


def test_stale_png_mutation_goes_red(tmp_path):
    """THE NAMED MUTATION `stale-png`: edit the SVG, leave the PNG and stamp behind it."""
    root = _trio(tmp_path)
    (root / "docs" / "banner.svg").write_text(
        SVG_BODY.replace("<rect/>", "<rect x='1'/>"), encoding="utf-8")
    code, report = banner_render.check(str(root))
    assert code == 1, report
    assert any("STALE" in line for line in report), report


def test_a_missing_stamp_is_unmeasured_not_a_pass(tmp_path):
    root = _trio(tmp_path, stamp=False)
    code, report = banner_render.check(str(root))
    assert code == 2, report


def test_the_shipped_selftest_still_passes():
    """The module's own mutation set, run from its pytest caller rather than only from the runner."""
    assert banner_render._selftest() == 0


def test_a_swapped_png_is_caught_even_when_the_svg_is_untouched(tmp_path):
    """The stamp records which SVG the stamp was written for. Nothing identifies the PNG.

    So a PNG replaced, re-rendered elsewhere, or corrupted in a way that still parses passes every
    check: geometry matches the viewBox, the corners are still clear, and freshness compares the
    stamp to the SVG — which nobody touched. The check reports "matches its source" about an image
    it never hashed. The replacement below keeps the geometry and the transparent corners and
    changes only the interior pixels, so it is exactly the case the other arms cannot see.
    """
    root = _trio(tmp_path)
    code, report = banner_render.check(str(root))
    assert code == 0, ("CONTROL: the untouched trio passes", report)

    other = [(20, 19, 30, 255)] * (W * H)
    for i in (0, W - 1, W * (H - 1), W * H - 1):
        other[i] = CLEAR
    (root / "docs" / "banner.png").write_bytes(_png(W, H, other))

    code, report = banner_render.check(str(root))
    assert code != 0, ("a PNG that is not the one the stamp vouches for must not pass", report)
    assert any("PNG" in line or "png" in line for line in report), report


def test_an_old_single_hash_stamp_is_unmeasured_not_a_silent_pass(tmp_path):
    """A stamp written before PNG identity existed cannot vouch for the PNG.

    Treating it as a pass would make every tree that has not re-rendered look verified. It is a
    cannot-check, which in this subsystem is worse than a violation and must be reported as such.
    """
    root = _trio(tmp_path)
    (root / "docs" / "banner.stamp").write_text(
        hashlib.sha256(SVG_BODY.encode()).hexdigest(), encoding="utf-8")
    code, report = banner_render.check(str(root))
    assert code == 2, ("a stamp that cannot identify the PNG is UNMEASURED", report)


def test_the_render_script_does_not_discard_the_checkers_verdict():
    """The render script ends by running the checker, and swallowed its exit status.

    By then the PNG has already been replaced, so a red check left a bad banner in the tree while
    the script exited 0 — a verdict that cannot change the outcome, which is the same defect as a
    guard that never fires. This is a source assertion rather than an end-to-end run because the
    real script needs a headless browser, which this suite deliberately does not require; it can
    still fail, and it fails on exactly the construct that caused the defect.
    """
    script = (pathlib.Path(banner_render.__file__).resolve().parent.parent
              / "docs" / "render_banner.sh").read_text(encoding="utf-8")
    checker_lines = [ln for ln in script.splitlines() if "banner_render.py" in ln
                     and not ln.strip().startswith("#")]
    assert checker_lines, "CONTROL: the script must actually invoke the checker"
    for ln in checker_lines:
        assert "|| true" not in ln and "|| :" not in ln, (
            "the checker's verdict must reach the caller, not be discarded: " + ln.strip())
