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
        (docs / "banner.stamp").write_text(
            hashlib.sha256(body.encode()).hexdigest(), encoding="utf-8")
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
