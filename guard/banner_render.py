#!/usr/bin/env python3
"""Hold the rendered banner to the shape its source promises.

The banner SVG has rounded corners. That only survives into the PNG if the render
keeps a transparent ground; a headless browser defaults to opaque white, silently
drops the alpha channel, and paints the corners white. On a dark README those
corners are the first thing a reader sees, and nothing in the repository noticed —
the PNG was still the right size, still the right picture, still committed.

It has now happened on more than one banner update. Both times the render was
retyped by hand and `--default-background-color=00000000` went missing. The second
time it survived a positive control, because the control render carried the flag
and the shipped render did not: proving a renderer works is not the same as
proving the command you shipped with works.

So the property is checked rather than remembered:

  * the PNG carries an alpha channel at all
  * all four corner pixels are fully transparent
  * the PNG is exactly 2x the SVG's own viewBox
  * the PNG was rendered from the SVG that is in the tree right now

The last one is the staleness check. `docs/render_banner.sh` records which SVG it
rendered; if the SVG is edited and the PNG is not regenerated, the recorded hash
stops matching and this goes red. Without it, an edited SVG and an untouched PNG
are indistinguishable from a correct pair.

  0  the banner matches its source and keeps its corners
  1  a violation — white corners, wrong geometry, or a PNG behind its SVG
  2  UNMEASURED — the files or the PNG's encoding could not be read
"""

import hashlib
import os
import re
import struct
import sys
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SVG = os.path.join("docs", "banner.svg")
PNG = os.path.join("docs", "banner.png")
STAMP = os.path.join("docs", "banner.stamp")
SCALE = 2

CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}
ALPHA_TYPES = (4, 6)


class Unreadable(Exception):
    """The image could not be decoded — a cannot-check, never a pass."""


def _paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def read_png(path):
    """(width, height, colortype, rows) with rows as raw unfiltered bytes.

    A deliberately small decoder: the alternative is a third-party imaging
    dependency, which would make this guard skip itself on most machines — and a
    guard that usually skips is the failure it was written to prevent.
    """
    with open(path, "rb") as fh:
        data = fh.read()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise Unreadable("not a PNG")

    pos, idat, ihdr = 8, [], None
    while pos + 8 <= len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        ctype = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + length]
        if ctype == b"IHDR":
            ihdr = struct.unpack(">IIBBBBB", body)
        elif ctype == b"IDAT":
            idat.append(body)
        elif ctype == b"IEND":
            break
        pos += 12 + length

    if ihdr is None:
        raise Unreadable("no IHDR")
    w, h, depth, colortype, _comp, _filt, interlace = ihdr
    if depth != 8 or interlace != 0 or colortype not in CHANNELS:
        raise Unreadable(f"unsupported PNG (depth={depth} colortype={colortype} "
                         f"interlace={interlace})")
    if not idat:
        raise Unreadable("no image data")

    bpp = CHANNELS[colortype]
    stride = w * bpp
    try:
        raw = zlib.decompress(b"".join(idat))
    except zlib.error as exc:
        raise Unreadable(f"image data would not decompress: {exc}")
    if len(raw) < (stride + 1) * h:
        raise Unreadable("image data is shorter than the header declares")

    rows, prev = [], bytearray(stride)
    for y in range(h):
        off = y * (stride + 1)
        ft = raw[off]
        line = bytearray(raw[off + 1:off + 1 + stride])
        if ft == 1:
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 0xFF
        elif ft == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif ft == 3:
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((a + prev[i]) >> 1)) & 0xFF
        elif ft == 4:
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                c = prev[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + _paeth(a, prev[i], c)) & 0xFF
        elif ft != 0:
            raise Unreadable(f"unknown scanline filter {ft}")
        rows.append(bytes(line))
        prev = line
    return w, h, colortype, rows


def svg_viewbox(path):
    with open(path, encoding="utf-8") as fh:
        tag = re.search(r"<svg[^>]*>", fh.read())
    if not tag:
        raise Unreadable("no <svg> element")
    vb = re.search(r'viewBox="([\d.\s-]+)"', tag.group(0))
    if vb:
        parts = vb.group(1).split()
        return int(float(parts[2])), int(float(parts[3]))
    w = re.search(r'width="(\d+)"', tag.group(0))
    h = re.search(r'height="(\d+)"', tag.group(0))
    if not (w and h):
        raise Unreadable("no viewBox or width/height")
    return int(w.group(1)), int(h.group(1))


def check(root=ROOT):
    svg, png, stamp = (os.path.join(root, p) for p in (SVG, PNG, STAMP))
    for p in (svg, png):
        if not os.path.isfile(p):
            return 2, [f"UNMEASURED: {os.path.relpath(p, root)} is not present"]
    try:
        vw, vh = svg_viewbox(svg)
        w, h, colortype, rows = read_png(png)
    except (Unreadable, OSError) as exc:
        return 2, [f"UNMEASURED: {exc}"]

    lines, bad = [], []

    if colortype not in ALPHA_TYPES:
        bad.append("the PNG has no alpha channel at all, so its rounded corners "
                   "were flattened onto an opaque ground")
        lines.append(f"   alpha channel : ABSENT (colortype {colortype})")
    else:
        bpp = CHANNELS[colortype]
        corners = {
            "top-left": (0, 0), "top-right": (w - 1, 0),
            "bottom-left": (0, h - 1), "bottom-right": (w - 1, h - 1),
        }
        opaque = []
        for name, (x, y) in corners.items():
            alpha = rows[y][x * bpp + bpp - 1]
            if alpha != 0:
                opaque.append(f"{name} (alpha {alpha})")
        if opaque:
            bad.append("corner pixels are not transparent: " + ", ".join(opaque))
            lines.append("   corners       : OPAQUE — " + ", ".join(opaque))
        else:
            lines.append("   corners       : all four transparent")

    want = (vw * SCALE, vh * SCALE)
    if (w, h) != want:
        bad.append(f"the PNG is {w}x{h} where the SVG viewBox at {SCALE}x is "
                   f"{want[0]}x{want[1]}")
        lines.append(f"   geometry      : {w}x{h}, expected {want[0]}x{want[1]}")
    else:
        lines.append(f"   geometry      : {w}x{h} = {SCALE}x the viewBox")

    with open(svg, "rb") as fh:
        digest = hashlib.sha256(fh.read()).hexdigest()
    # A missing stamp is a cannot-check, but returning here would let it MASK a
    # violation already found above — reporting "no stamp" while the corners are
    # visibly white. Both are carried, and the violations still get named.
    unmeasured = []
    if not os.path.isfile(stamp):
        unmeasured.append("the render stamp is missing, so a stale PNG would look "
                          "identical to a current one")
        lines.append("   freshness     : no stamp — cannot tell which SVG this PNG came from")
        recorded = None
    else:
        with open(stamp, encoding="utf-8") as fh:
            recorded = fh.read().strip()
    if recorded is None:
        pass                      # already reported as a cannot-check above
    elif recorded != digest:
        bad.append("the PNG was rendered from a different banner.svg than the one "
                   "in the tree — re-run docs/render_banner.sh")
        lines.append("   freshness     : STALE (stamp does not match banner.svg)")
    else:
        lines.append("   freshness     : rendered from the current banner.svg")

    detail = lines + [f"   -> {b}" for b in bad] + \
        [f"   -> UNMEASURED: {u}" for u in unmeasured]
    if bad and unmeasured:
        return 2, ["the rendered banner does not match its source, AND something "
                   "could not be checked"] + detail
    if bad:
        return 1, ["the rendered banner does not match its source"] + detail
    if unmeasured:
        return 2, ["UNMEASURED — the banner could not be fully checked"] + detail
    return 0, ["the rendered banner keeps its corners and matches its source"] + lines


# ---------------------------------------------------------------- selftest

def _png(w, h, rgba, colortype=6):
    """Smallest valid PNG carrying `rgba` as a flat pixel list."""
    bpp = CHANNELS[colortype]
    raw = bytearray()
    for y in range(h):
        raw.append(0)
        for x in range(w):
            px = rgba[y * w + x]
            raw.extend(px[:bpp])
    def chunk(tag, body):
        return (struct.pack(">I", len(body)) + tag + body +
                struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, colortype, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw)))
            + chunk(b"IEND", b""))


def _selftest():
    import tempfile
    import hashlib as _h
    failures = []

    def case(name, ok):
        print(f"   {'ok  ' if ok else 'FAIL'}  {name}")
        if not ok:
            failures.append(name)

    svg_body = '<svg viewBox="0 0 4 2" width="4" height="2"><rect/></svg>'
    clear = (0, 0, 0, 0)
    solid = (14, 13, 24, 255)
    white = (255, 255, 255, 255)

    with tempfile.TemporaryDirectory() as td:
        os.makedirs(os.path.join(td, "docs"))
        svg_p = os.path.join(td, "docs", "banner.svg")
        png_p = os.path.join(td, "docs", "banner.png")
        stamp_p = os.path.join(td, "docs", "banner.stamp")

        with open(svg_p, "w", encoding="utf-8") as fh:
            fh.write(svg_body)
        with open(stamp_p, "w", encoding="utf-8") as fh:
            fh.write(_h.sha256(svg_body.encode()).hexdigest())

        W, H = 8, 4          # 2x the 4x2 viewBox

        def write(pixels, colortype=6):
            with open(png_p, "wb") as fh:
                fh.write(_png(W, H, pixels, colortype))

        good = [solid] * (W * H)
        for i in (0, W - 1, W * (H - 1), W * H - 1):
            good[i] = clear
        write(good)
        case("a correct banner passes (green)", check(td)[0] == 0)

        opaque = [solid] * (W * H)
        write(opaque)
        case("opaque corners go red", check(td)[0] == 1)

        whitened = list(good)
        whitened[0] = white
        write(whitened)
        case("even ONE white corner goes red", check(td)[0] == 1)

        write([solid] * (W * H), colortype=2)          # RGB, no alpha at all
        case("a PNG with no alpha channel goes red", check(td)[0] == 1)

        write(good)
        with open(svg_p, "w", encoding="utf-8") as fh:
            fh.write(svg_body.replace("<rect/>", "<rect x='1'/>"))
        case("an edited SVG with an unrendered PNG goes red (stale)", check(td)[0] == 1)

        with open(svg_p, "w", encoding="utf-8") as fh:
            fh.write(svg_body)
        os.remove(stamp_p)
        case("a missing stamp is UNMEASURED, not a pass", check(td)[0] == 2)

        with open(stamp_p, "w", encoding="utf-8") as fh:
            fh.write(_h.sha256(svg_body.encode()).hexdigest())
        wrong = [solid] * (W * H)
        for i in (0, W - 1, W * (H - 1), W * H - 1):
            wrong[i] = clear
        with open(png_p, "wb") as fh:
            fh.write(_png(W, H // 2, wrong[:W * (H // 2)]))
        case("the wrong geometry goes red", check(td)[0] == 1)

        with open(png_p, "wb") as fh:
            fh.write(b"not a png at all")
        case("an undecodable PNG is UNMEASURED, not a pass", check(td)[0] == 2)

    if failures:
        print(f"SELFTEST FAILED ({len(failures)}): " + ", ".join(failures))
        return 1
    print("banner_render selftest: white corners, a dropped alpha channel, wrong "
          "geometry and a PNG behind its SVG each go red; an unreadable image "
          "refuses to pass")
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    code, report = check()
    print("banner render — " + report[0])
    for ln in report[1:]:
        print(ln)
    raise SystemExit(code)
