#!/usr/bin/env python3
# purpose: check banner geometry, transparency, and source stamp during guard runs and after rendering.
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
  * its painted ground is opaque inside and transparent outside, except edge AA
  * the PNG was rendered from the SVG that is in the tree right now

The last one is the staleness check. `docs/render_banner.sh` records which SVG it
rendered; if the SVG is edited and the PNG is not regenerated, the recorded hash
stops matching and this goes red. Without it, an edited SVG and an untouched PNG
are indistinguishable from a correct pair.

  0  the banner matches its source and keeps its corners
  1  a violation — white corners, wrong geometry, or a PNG behind its SVG
  2  UNMEASURED — the files or the PNG's encoding could not be read

GUARD-CLASS: guard — the rendered PNG and its stamp must still match the SVG they came from
"""

import hashlib
import math
import os
import re
import struct
import sys
import xml.etree.ElementTree as ET
from xml.parsers import expat
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


def svg_number(text, *, percentage=False):
    """Finite CSS Syntax 3 number token (also a subset of SVG 1.1 number).

    ASCII digits only; optional sign, fraction and exponent. Unit suffixes,
    trailing decimal points and non-XML whitespace are outside this model.
    Outer XML whitespace is allowed. Gradient coordinates additionally admit
    a percentage token: the number immediately followed by %, without a gap.
    """
    text = text.strip(' \t\r\n')
    grammar = r'[+-]?(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?'
    if re.fullmatch(grammar + (r'%?' if percentage else ''), text) is None:
        raise ValueError('unsupported SVG number: ' + repr(text))
    if percentage and text.endswith('%'):
        text = text[:-1]
    value = float(text)
    if not math.isfinite(value):
        raise ValueError('non-finite SVG number')
    return value


def _svg_tree(path):
    # Reject declarations before ElementTree can expand entities.
    parser = expat.ParserCreate()
    def unsupported_xml(*args):
        raise ValueError('processing instructions and DOCTYPE/entities are unsupported')
    parser.ProcessingInstructionHandler = unsupported_xml
    parser.StartDoctypeDeclHandler = unsupported_xml
    parser.EntityDeclHandler = unsupported_xml
    with open(path, 'rb') as source:
        body = source.read()
    parser.Parse(body, True)
    return ET.fromstring(body)


def _svg_viewbox(svg):
    if 'viewBox' in svg.attrib:
        # Model XML whitespace and comma separators, never Unicode whitespace.
        text = svg.get('viewBox').strip(' \t\r\n')
        parts = re.split(r'(?:[ \t\r\n]+,?[ \t\r\n]*|,[ \t\r\n]*)', text)
        vb = [svg_number(part) for part in parts]
    else:
        vb = [0, 0, svg_number(svg.attrib['width']), svg_number(svg.attrib['height'])]
    if len(vb) != 4 or min(vb[2:]) <= 0:
        raise ValueError('invalid SVG viewBox')
    return vb


def svg_viewbox(path):
    try:
        return tuple(_svg_viewbox(_svg_tree(path))[2:])
    except (ET.ParseError, expat.ExpatError, KeyError, ValueError) as exc:
        raise Unreadable(f'cannot derive painted ground or SVG viewport: {exc}') from exc


def svg_ground(path):
    """Return the circular rounded ground's inner box and radius in PNG pixels.

    The first drawable root child is the ground (defs are not painted). Refuse
    unsupported geometry rather than silently measuring a different shape.
    """
    try:
        svg = _svg_tree(path)
        namespace = '{http://www.w3.org/2000/svg}'
        if svg.tag != namespace + 'svg':
            raise ValueError('unsupported SVG namespace or root')
        for node in svg.iter():
            name = node.tag.removeprefix(namespace)
            if name in {'set', 'animate', 'animateTransform', 'animateMotion',
                        'animateColor', 'discard', 'style', 'script'}:
                raise ValueError(f'active SVG element {name} is unsupported')
            if name.startswith('{') or any(key.lower().startswith('on') for key in node.attrib):
                raise ValueError('foreign elements and event handlers are unsupported')

        def attributes(node, allowed):
            unknown = set(node.attrib) - allowed
            if unknown:
                raise ValueError(f'unsupported {node.tag.rsplit("}", 1)[-1]} attributes: '
                                 + ', '.join(sorted(unknown)))

        # Explicit element/attribute allowlists; later artwork cannot introduce
        # executable or document-wide styling constructs (checked above).
        attributes(svg, {'id', 'viewBox', 'width', 'height', 'role', 'aria-label', 'opacity'})
        children = [node for node in svg if node.tag.rsplit('}', 1)[-1]
                    not in ('defs', 'title', 'desc', 'metadata')]
        if not children or children[0].tag.rsplit('}', 1)[-1] != 'rect':
            raise ValueError('the first painted SVG child must be the ground rect')
        ground = children[0]
        attributes(ground, {'id', 'x', 'y', 'width', 'height', 'rx', 'ry',
                            'fill', 'fill-opacity', 'opacity', 'stroke',
                            'stroke-width', 'stroke-opacity'})
        if len(ground):
            raise ValueError('ground rect children are unsupported')
        for node in (svg, ground):
            if svg_number(node.get('opacity', '1')) != 1:
                raise ValueError('the ground must be opaque')

        def number(name, default=None):
            value = svg_number(ground.attrib[name] if default is None
                          else ground.get(name, default))
            if not math.isfinite(value):
                raise ValueError(f'non-finite ground {name}')
            return value

        x, y = number('x', '0'), number('y', '0')
        width, height = number('width'), number('height')
        rx = number('rx', ground.get('ry', '0'))
        ry = number('ry', ground.get('rx', '0'))
        if width <= 0 or height <= 0 or min(rx, ry) < 0:
            raise ValueError('invalid ground dimensions or radius')
        rx, ry = min(rx, width / 2), min(ry, height / 2)
        if rx != ry:
            raise ValueError('elliptical ground corners are unsupported')
        def opaque_paint(value):
            # Deliberately narrow: no alpha-bearing colors, paint servers or
            # context-dependent colors may be treated as a solid opaque shape.
            value = value.strip().lower()
            return (re.fullmatch(r'#[0-9a-f]{3}(?:[0-9a-f]{3})?', value) is not None
                    or value in {'black', 'silver', 'gray', 'white', 'maroon',
                                 'red', 'purple', 'fuchsia', 'green', 'lime',
                                 'olive', 'yellow', 'navy', 'blue', 'teal', 'aqua'})

        def gradient_properties(node, *, stop=False):
            # Only a bounded inline CSS grammar is modeled. Check both attribute
            # and style opacity: an override must not hide a translucent input.
            paints = {'stop-color', 'stop-opacity'} if stop else set()
            allowed_style = {'opacity', 'fill-opacity'} | paints
            geometry = ({'offset'} if stop else
                        {'x1', 'y1', 'x2', 'y2', 'gradientUnits', 'spreadMethod'})
            if any(key.rsplit('}', 1)[-1] == 'href' for key in node.attrib):
                raise ValueError('gradient inheritance is unsupported')
            attributes(node, allowed_style | geometry | {'id', 'style'})
            for key in geometry - {'gradientUnits', 'spreadMethod'}:
                if key in node.attrib:
                    value = node.get(key)
                    svg_number(value, percentage=True)
            if not stop:
                if node.get('gradientUnits', 'objectBoundingBox') not in {'objectBoundingBox', 'userSpaceOnUse'}:
                    raise ValueError('unsupported gradient units')
                if node.get('spreadMethod', 'pad') not in {'pad', 'reflect', 'repeat'}:
                    raise ValueError('unsupported gradient spread')
            properties = {key: node.get(key) for key in allowed_style if key in node.attrib}
            declarations = {}
            for part in node.get('style', '').split(';'):
                if not part.strip():
                    continue
                key, sep, value = part.partition(':')
                key, value = key.strip().lower(), value.strip()
                if not sep or key not in allowed_style or key in declarations:
                    raise ValueError('unsupported gradient or stop style')
                declarations[key] = value
            for source in (properties, declarations):
                for key, value in source.items():
                    if key.endswith('opacity') and svg_number(value) != 1:
                        raise ValueError('gradient and every stop must be opaque')
                    if key == 'stop-color' and not opaque_paint(value):
                        raise ValueError('unsupported gradient stop color')
            properties.update(declarations)
            return properties

        fill = ground.get('fill', 'black').strip()
        if not opaque_paint(fill):
            reference = re.fullmatch(r'url\(#([^\s()#]+)\)', fill)
            if reference is None:
                raise ValueError('unsupported ground fill; requires opaque solid or local gradient')
            candidates = [node for node in svg.iter() if node.get('id') == reference[1]]
            if len(candidates) != 1:
                raise ValueError('ground gradient id must resolve uniquely in this document')
            gradient = candidates[0]
            if gradient.tag != namespace + 'linearGradient':
                raise ValueError('ground paint server must be a linearGradient; radial focal geometry is unmodeled')
            parents = {child: parent for parent in svg.iter() for child in parent}
            ancestor = parents[gradient]
            while ancestor is not svg:
                if ancestor.tag != namespace + 'defs':
                    raise ValueError('ground gradient must be defined under plain defs')
                attributes(ancestor, {'id'})
                ancestor = parents[ancestor]
            gradient_properties(gradient)
            stops = list(gradient)
            if not stops:
                raise ValueError('ground gradient needs at least one stop')
            for stop in stops:
                if stop.tag != namespace + 'stop' or len(stop):
                    raise ValueError('ground gradient requires static stops only')
                properties = gradient_properties(stop, stop=True)
                if not opaque_paint(properties.get('stop-color', '')):
                    raise ValueError('gradient stops require explicit opaque solid colors')
        if number('fill-opacity', '1') != 1:
            raise ValueError('the ground fill must be opaque')
        stroke = 0
        if ground.get('stroke', 'none') != 'none':
            if not opaque_paint(ground.get('stroke')):
                raise ValueError('unsupported ground stroke; requires explicit opaque solid paint')
            stroke = number('stroke-width', '1')
            if stroke < 0 or number('stroke-opacity', '1') != 1:
                raise ValueError('the ground stroke must be nonnegative and opaque')
            if stroke and (rx == 0 or 'vector-effect' in ground.attrib):
                raise ValueError('square or non-scaling ground strokes are unsupported')
        vb = _svg_viewbox(svg)
        for key, extent in (('width', vb[2]), ('height', vb[3])):
            if key in svg.attrib and svg_number(svg.get(key)) != extent:
                raise ValueError('SVG viewport must equal viewBox extent')
        return ((x + rx - vb[0]) * SCALE, (y + ry - vb[1]) * SCALE,
                (x + width - rx - vb[0]) * SCALE,
                (y + height - ry - vb[1]) * SCALE, (rx + stroke / 2) * SCALE)
    except (ET.ParseError, expat.ExpatError, KeyError, ValueError) as exc:
        raise Unreadable(f'cannot derive painted ground: {exc}') from exc


def check_alpha_shape(w, h, colortype, rows, shape):
    """Require full coverage inside, zero coverage outside, edge AA only.

    A rounded rect is its inner box dilated by a disk; the opaque centered
    stroke expands that disk by half its width. Compare the nearest and farthest
    points of each *pixel square* to the inner box. Only squares intersecting
    the boundary are exempt, plus 0.25 device pixels of curved-edge rounding.
    The committed render has two alpha=1 pixels whose squares miss the ideal
    curve by 0.151 pixels. A quarter pixel covers that measured raster fringe;
    sqrt(2)/2 + 0.25 < 1 pixel bounds the center-to-edge allowance. Straight
    edges get no extra tolerance, so an aligned interior row cannot disappear.
    Known limit: an rx=0 edge inside a pixel can falsely reject partial coverage.
    """
    left, top, right, bottom, radius = shape
    radius2 = radius * radius
    curve_inside2 = max(0, radius - 0.25)**2
    curve_outside2 = (radius + 0.25)**2
    bpp = CHANNELS[colortype]
    # Separate x/y distances avoid recomputing horizontal geometry per row.
    near_x = [max(left - (x + 1), x - right, 0)**2 for x in range(w)]
    far_x = [max(left - x, x + 1 - right, 0)**2 for x in range(w)]
    interior, exterior, band, nonopaque = 0, 0, 0, 0
    first_inside = first_outside = None
    for y, row in enumerate(rows):
        near_y = max(top - (y + 1), y - bottom, 0)**2
        far_y = max(top - y, y + 1 - bottom, 0)**2
        for x, alpha in enumerate(row[bpp - 1::bpp]):
            nonopaque += alpha != 255
            inside2 = curve_inside2 if far_x[x] and far_y else radius2
            outside2 = curve_outside2 if near_x[x] and near_y else radius2
            if far_x[x] + far_y <= inside2:
                if alpha != 255:
                    interior += 1
                    if first_inside is None:
                        first_inside = (x, y, alpha)
            elif near_x[x] + near_y >= outside2:
                if alpha != 0:
                    exterior += 1
                    if first_outside is None:
                        first_outside = (x, y, alpha)
            else:
                band += 1
    lines = [f"   alpha shape   : {interior} non-opaque interior, "
             f"{exterior} non-transparent exterior; {band} edge pixels allowed",
             f"   non-opaque    : {nonopaque} pixels total"]
    bad = []
    for count, first, label in ((interior, first_inside, 'interior'),
                                 (exterior, first_outside, 'exterior')):
        if count:
            bad.append(f"{count} {label} alpha violations; first at "
                       f"({first[0]}, {first[1]}) alpha {first[2]}")
    return lines, bad


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

    lines, bad, unmeasured = [], [], []

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

    try:
        shape = svg_ground(svg)
    except (Unreadable, OSError) as exc:
        unmeasured.append(str(exc))
    else:
        if colortype in ALPHA_TYPES and (w, h) == want:
            shape_lines, shape_bad = check_alpha_shape(w, h, colortype, rows, shape)
            lines.append('   ground shape  : MEASURED')
            lines.extend(shape_lines)
            bad.extend(shape_bad)

    with open(svg, "rb") as fh:
        svg_digest = hashlib.sha256(fh.read()).hexdigest()
    with open(png, "rb") as fh:
        png_digest = hashlib.sha256(fh.read()).hexdigest()
    # A missing stamp is a cannot-check, but returning here would let it MASK a
    # violation already found above — reporting "no stamp" while the corners are
    # visibly white. Both are carried, and the violations still get named.
    #
    # The stamp records BOTH digests, one per line, "svg <hex>" and "png <hex>".
    # Recording only the SVG answered "which SVG was this stamp written for" while the
    # report claimed the banner matched its source: a PNG swapped, re-rendered elsewhere,
    # or altered in a way that still parses passed every check, because nothing ever
    # hashed the image the check was vouching for.
    recorded = {}
    if not os.path.isfile(stamp):
        unmeasured.append("the render stamp is missing, so a stale PNG would look "
                          "identical to a current one")
        lines.append("   freshness     : no stamp — cannot tell which SVG this PNG came from")
    else:
        with open(stamp, encoding="utf-8") as fh:
            for raw in fh.read().split("\n"):
                parts = raw.split()
                if len(parts) == 2:
                    recorded[parts[0]] = parts[1]
                elif len(parts) == 1 and parts[0]:
                    recorded.setdefault("svg", parts[0])   # the old single-hash format
        # Each half is a separate fact about a separate file, so each is checked and reported on
        # its own. An if/elif chain let the first failure suppress the second, and let a stamp
        # carrying only one digest report the OTHER file as verified when it had never been read.
        verified = True
        if "svg" not in recorded:
            verified = False
            unmeasured.append("the stamp records no SVG digest, so it cannot say which "
                              "banner.svg this PNG came from — re-run docs/render_banner.sh")
            lines.append("   svg identity  : stamp records no SVG digest — cannot verify banner.svg")
        elif recorded["svg"] != svg_digest:
            verified = False
            bad.append("the PNG was rendered from a different banner.svg than the one "
                       "in the tree — re-run docs/render_banner.sh")
            lines.append("   svg identity  : STALE (stamp does not match banner.svg)")
        if "png" not in recorded:
            verified = False
            unmeasured.append("the stamp records no PNG digest, so it cannot vouch for the "
                              "banner.png in the tree — re-run docs/render_banner.sh")
            lines.append("   png identity  : stamp predates PNG identity — cannot verify banner.png")
        elif recorded["png"] != png_digest:
            verified = False
            bad.append("the banner.png in the tree is not the PNG this stamp vouches for "
                       "— re-run docs/render_banner.sh")
            lines.append("   png identity  : STALE (stamp does not match banner.png)")
        if verified:
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

    svg_body = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 4 2" width="4" height="2"><rect width="4" height="2" rx="1"/></svg>'
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
        W, H = 8, 4          # 2x the 4x2 viewBox

        def stamp_now():
            # The stamp vouches for BOTH files, so it is written from what is on disk
            # rather than from what the fixture believes it wrote.
            with open(svg_p, "rb") as fh:
                sd = _h.sha256(fh.read()).hexdigest()
            with open(png_p, "rb") as fh:
                pd = _h.sha256(fh.read()).hexdigest()
            with open(stamp_p, "w", encoding="utf-8") as fh:
                fh.write("svg %s\npng %s\n" % (sd, pd))

        def write(pixels, colortype=6):
            with open(png_p, "wb") as fh:
                fh.write(_png(W, H, pixels, colortype))
            stamp_now()          # each case then measures ITS defect, not a stale stamp

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
            fh.write(svg_body.replace("<rect ", "<rect x='1' "))
        case("an edited SVG with an unrendered PNG goes red (stale)", check(td)[0] == 1)

        with open(svg_p, "w", encoding="utf-8") as fh:
            fh.write(svg_body)
        os.remove(stamp_p)
        case("a missing stamp is UNMEASURED, not a pass", check(td)[0] == 2)

        wrong = [solid] * (W * H)
        for i in (0, W - 1, W * (H - 1), W * H - 1):
            wrong[i] = clear
        with open(png_p, "wb") as fh:
            fh.write(_png(W, H // 2, wrong[:W * (H // 2)]))
        stamp_now()
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
