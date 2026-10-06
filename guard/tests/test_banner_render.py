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
import shutil
import struct
import subprocess
import zlib

import pytest

MODULE_PATH = pathlib.Path(__file__).resolve().parent.parent / "banner_render.py"
_spec = importlib.util.spec_from_file_location("banner_render", MODULE_PATH)
banner_render = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(banner_render)

SVG_BODY = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 4 2" width="4" height="2"><rect width="4" height="2" rx="1"/></svg>'
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


@pytest.mark.parametrize('namespace', ['', ' xmlns="urn:other"'])
def test_banner_requires_svg_namespace(tmp_path, namespace):
    body = SVG_BODY.replace(' xmlns="http://www.w3.org/2000/svg"', namespace)
    code, report = banner_render.check(str(_trio(tmp_path, svg_body=body)))
    assert code == 2, report


def test_stale_png_mutation_goes_red(tmp_path):
    """THE NAMED MUTATION `stale-png`: edit the SVG, leave the PNG and stamp behind it."""
    root = _trio(tmp_path)
    (root / "docs" / "banner.svg").write_text(
        SVG_BODY.replace("<rect ", "<rect x='1' "), encoding="utf-8")
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


def test_a_stamp_with_only_a_png_digest_does_not_pass(tmp_path):
    """Adversarial review, B1: a PNG-only stamp bypassed SVG identity entirely.

    The first version guarded the SVG comparison behind `recorded.get("svg")`, so a stamp carrying
    only a matching PNG digest skipped the SVG check, fell through to a matching PNG, and reported
    "rendered from the current banner.svg" — about an SVG it had never compared.
    """
    root = _trio(tmp_path)
    png_digest = hashlib.sha256((root / "docs" / "banner.png").read_bytes()).hexdigest()
    (root / "docs" / "banner.stamp").write_text("png %s\n" % png_digest, encoding="utf-8")
    code, report = banner_render.check(str(root))
    assert code == 2, ("a stamp that cannot identify the SVG is UNMEASURED", report)


def test_both_halves_of_a_wrong_stamp_are_reported_not_just_the_first(tmp_path):
    """Adversarial review, B2: an if/elif chain let one identity failure hide the other.

    With both digests wrong the SVG branch fired and the PNG problem was never mentioned, so a reader
    fixing the named problem would still be left with an unverified image.
    """
    root = _trio(tmp_path)
    (root / "docs" / "banner.stamp").write_text(
        "svg %s\npng %s\n" % ("0" * 64, "1" * 64), encoding="utf-8")
    code, report = banner_render.check(str(root))
    assert code != 0, report
    blob = "\n".join(report)
    assert "banner.svg" in blob and "banner.png" in blob, \
        ("both identities must be named, not just the first to fail", report)


def test_the_checkers_exit_status_really_becomes_the_scripts_exit_status(tmp_path):
    """Adversarial review: the arm above greps the script's text, and text is not behaviour.

    `|| true` is one way to discard a status. `set +e`, an `if` wrapper, or a trailing `exit 0`
    are others, and every one of them passes a grep for `|| true`. So this arm runs the real
    script and reads what the shell actually returns.

    No headless browser is needed: the script takes CHROME from the environment, so a stub that
    writes the bytes Chrome would write is enough to reach the final line. The checker is stubbed
    to a known non-zero status, and the test asserts the script returns THAT status — not merely
    that it is non-zero, so a script that failed earlier for an unrelated reason cannot pass.
    """
    repo = pathlib.Path(banner_render.__file__).resolve().parent.parent
    sandbox = tmp_path / "tree"
    (sandbox / "docs").mkdir(parents=True)
    (sandbox / "guard").mkdir(parents=True)
    shutil.copy2(repo / "docs" / "render_banner.sh", sandbox / "docs" / "render_banner.sh")
    (sandbox / "docs" / "banner.svg").write_text(SVG_BODY, encoding="utf-8")

    chrome = tmp_path / "fake_chrome.sh"
    chrome.write_text(
        '#!/usr/bin/env bash\n'
        'for arg in "$@"; do\n'
        '  case "$arg" in --screenshot=*) out="${arg#--screenshot=}" ;; esac\n'
        'done\n'
        '[ -n "$out" ] || exit 9\n'
        'printf "not really a png but not empty either" > "$out"\n',
        encoding="utf-8")
    chrome.chmod(0o755)

    checker = sandbox / "guard" / "banner_render.py"

    def run_with_checker_exiting(status):
        checker.write_text(f"import sys\nsys.exit({status})\n", encoding="utf-8")
        return subprocess.run(["bash", "docs/render_banner.sh"], cwd=str(sandbox),
                              env={**os.environ, "CHROME": str(chrome)},
                              capture_output=True, text=True)

    ok = run_with_checker_exiting(0)
    assert ok.returncode == 0, (
        "CONTROL: with a passing checker the script must succeed, or this fixture proves nothing "
        f"about status propagation:\n{ok.stdout}\n{ok.stderr}")
    assert (sandbox / "docs" / "banner.png").exists(), "CONTROL: the render step actually ran"

    for status in (1, 2, 3):
        red = run_with_checker_exiting(status)
        assert red.returncode == status, (
            f"the checker exited {status} and the script returned {red.returncode}; a swallowed "
            f"verdict leaves a bad banner in the tree behind a clean exit:\n{red.stderr}")


# Alpha fixtures deliberately rasterize by sampling the SVG circle equation;
# they do not call the checker's shape parser or pixel classification helpers.
def _shape_trio(root, *, x=0, y=0, width=120, height=32, radius=8, stroke=0):
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 32">'
           f'<defs><rect width="1" height="1"/></defs>'
           f'<rect x="{x}" y="{y}" width="{width}" height="{height}" '
           f'rx="{radius}" fill="#123456" stroke="#123456" '
           f'stroke-width="{stroke}"/></svg>')
    pixels = []
    for py in range(64):
        for px in range(240):
            hits = 0
            for sy in (0.125, 0.375, 0.625, 0.875):
                for sx in (0.125, 0.375, 0.625, 0.875):
                    u, v = (px + sx) / 2, (py + sy) / 2
                    cx = min(max(u, x + radius), x + width - radius)
                    cy = min(max(v, y + radius), y + height - radius)
                    hits += ((u - cx)**2 + (v - cy)**2 <= (radius + stroke/2)**2)
            pixels.append((14, 13, 24, round(255 * hits / 16)))
    docs = root / "docs"
    docs.mkdir(parents=True)
    (docs / "banner.svg").write_text(svg, encoding="utf-8")
    _write_shape_png(root, pixels)
    code, report = banner_render.check(str(root))
    assert code == 0, ("CONTROL: independently sampled shape must pass", report)
    return pixels


def _write_shape_png(root, pixels):
    docs = root / "docs"
    (docs / "banner.png").write_bytes(_png(240, 64, pixels))
    # Re-stamp each corruption: identity checks cannot supply a spurious RED.
    (docs / "banner.stamp").write_text("".join(
        f"{kind} {hashlib.sha256((docs / ('banner.' + kind)).read_bytes()).hexdigest()}\n"
        for kind in ("svg", "png")), encoding="utf-8")


def test_painted_shape_rejects_an_interior_transparent_block(tmp_path):
    pixels = _shape_trio(tmp_path)
    # Lower-right blank region, scaled to this fixture from the host failure.
    for y in range(51, 61):
        for x in range(25, 230):
            pixels[y * 240 + x] = CLEAR
    _write_shape_png(tmp_path, pixels)
    code, report = banner_render.check(str(tmp_path))
    assert code == 1, report
    assert any("interior" in line for line in report), report


def test_painted_shape_rejects_one_nonopaque_interior_pixel(tmp_path):
    pixels = _shape_trio(tmp_path)
    for alpha in (0, 254):
        pixels[32 * 240 + 120] = (14, 13, 24, alpha)
        _write_shape_png(tmp_path, pixels)
        code, report = banner_render.check(str(tmp_path))
        assert code == 1, (alpha, report)
        assert any("interior" in line for line in report), report


def test_painted_shape_rejects_opaque_corners(tmp_path):
    pixels = _shape_trio(tmp_path)
    for i in (0, 239, 63 * 240, 64 * 240 - 1):
        pixels[i] = SOLID
    _write_shape_png(tmp_path, pixels)
    code, report = banner_render.check(str(tmp_path))
    assert code == 1, report
    assert any("corner pixels" in line for line in report), report


def test_painted_shape_rejects_one_nontransparent_exterior_pixel(tmp_path):
    pixels = _shape_trio(tmp_path)
    # Outside the circular edge, but not one of the four legacy corner probes.
    for alpha in (1, 255):
        pixels[1 * 240 + 1] = (14, 13, 24, alpha)
        _write_shape_png(tmp_path, pixels)
        code, report = banner_render.check(str(tmp_path))
        assert code == 1, (alpha, report)
        assert any("exterior" in line for line in report), report


def test_painted_shape_uses_svg_position_size_radius_and_stroke(tmp_path):
    for name, geom in (
        ("inset", dict(x=5, y=3, width=110, height=26, radius=4)),
        ("stroke", dict(x=5, y=3, width=110, height=26, radius=9, stroke=1.5)),
    ):
        root = tmp_path / name
        pixels = _shape_trio(root, **geom)
        pixels[32 * 240 + 4] = SOLID  # margin created by x=5, away from corners
        _write_shape_png(root, pixels)
        code, report = banner_render.check(str(root))
        assert code == 1, (name, report)
        assert any("exterior" in line for line in report), report


def test_painted_shape_has_no_tolerance_along_aligned_straight_edges(tmp_path):
    pixels = _shape_trio(tmp_path)
    for x, y in ((120, 0), (0, 32), (239, 32), (120, 63)):
        altered = list(pixels)
        altered[y * 240 + x] = CLEAR
        _write_shape_png(tmp_path, altered)
        code, report = banner_render.check(str(tmp_path))
        assert code == 1, ((x, y), report)
        assert any("interior" in line for line in report), report



def test_painted_shape_accepts_correct_antialiased_png(tmp_path):
    pixels = _shape_trio(tmp_path, x=5, y=3, width=110, height=26, radius=9, stroke=1.5)
    assert any(0 < pixel[3] < 255 for pixel in pixels), "CONTROL: real AA samples exist"
    assert any(pixel[3] == 0 for pixel in pixels), "CONTROL: exterior is present"
    assert any(pixel[3] == 255 for pixel in pixels), "CONTROL: interior is present"


def test_painted_shape_unsupported_geometry_keeps_existing_checks(tmp_path):
    root = _trio(tmp_path)
    svg = root / "docs" / "banner.svg"
    for body in (SVG_BODY.replace('<rect ', '<rect transform="translate(1)" '),
                 SVG_BODY.replace('<svg ', '<svg stroke="black" stroke-width="1.5" ')):
        svg.write_text(body, encoding="utf-8")
        code, report = banner_render.check(str(root))
        assert code == 2, report
        assert any("cannot derive painted ground" in line for line in report), report
        assert any("STALE" in line for line in report), report


def test_ground_refuses_unmodeled_paint(tmp_path):
    for attribute in ('fill', 'stroke'):
        for paint in ('transparent', 'url(#gradient)', 'currentColor',
                      'rgba(0,0,0,0)', '#1230', 'inherit'):
            body = SVG_BODY.replace('<rect ', f'<rect {attribute}="{paint}" ')
            root = _trio(tmp_path, svg_body=body)
            code, report = banner_render.check(str(root))
            assert code == 2, (attribute, paint, report)
            assert any('cannot derive painted ground' in line for line in report), report
    body = SVG_BODY.replace('<svg ', '<svg fill="currentColor" ')
    code, report = banner_render.check(str(_trio(tmp_path, svg_body=body)))
    assert code == 2, report
    assert any('unsupported svg attributes' in line for line in report), report


def test_ground_accepts_explicit_opaque_solid_fills(tmp_path):
    for paint in ('#123', '#123456', '#AbCdEf', 'black', 'WHITE'):
        body = SVG_BODY.replace('<rect ', f'<rect fill="{paint}" ')
        code, report = banner_render.check(str(_trio(tmp_path, svg_body=body)))
        assert code == 0, (paint, report)


GRADIENT_STOPS = ('<stop offset="0%" stop-color="#0a0b12"/>'
                  '<stop offset="100%" stop-color="#12101e"/>')


def _gradient_svg(*, kind='linearGradient', stops=GRADIENT_STOPS,
                  gradient_attrs='', ground_attrs='', fill='url(#bg)'):
    # The real banner's two stops and percentage endpoints, with small geometry
    # for an independently constructed alpha fixture. No browser is involved.
    return (f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 4 2">'
            f'<defs><{kind} id="bg" x1="0%" y1="0%" x2="100%" y2="100%" '
            f'{gradient_attrs}>{stops}</{kind}></defs>'
            f'<rect width="4" height="2" rx="1" fill="{fill}" {ground_attrs}/></svg>')


@pytest.mark.parametrize('kind', ['linearGradient', 'radialGradient'])
def test_ground_accepts_real_banner_gradient_form(tmp_path, kind):
    root = _trio(tmp_path, svg_body=_gradient_svg(kind=kind))
    code, report = banner_render.check(str(root))
    if kind == 'radialGradient':
        assert code == 2, report
        assert any('radial focal geometry' in line for line in report)
        return
    assert code == 0, report
    assert any('alpha shape' in line for line in report), report
    assert not any('UNMEASURED' in line for line in report), report


@pytest.mark.parametrize('stops,attrs', [
    ('<stop stop-color="#abc"/>', ''),
    ('<stop stop-color="WHITE" stop-opacity="1.0"/>', 'opacity="1" fill-opacity="1"'),
    ('<stop style="stop-color: #123456; stop-opacity: 1"/>',
     'style="opacity: 1; fill-opacity: 1;"'),
])
def test_ground_accepts_bounded_opaque_gradient_styles(tmp_path, stops, attrs):
    body = _gradient_svg(stops=stops, gradient_attrs=attrs)
    code, report = banner_render.check(str(_trio(tmp_path, svg_body=body)))
    assert code == 0, report


@pytest.mark.parametrize('change', [
    {'stops': GRADIENT_STOPS.replace('offset="0%"', 'offset="0%" stop-opacity="0.5"')},
    {'stops': GRADIENT_STOPS.replace('offset="0%"', 'offset="0%" style="stop-opacity: 0.5"')},
    {'stops': GRADIENT_STOPS.replace('#0a0b12', 'transparent')},
    {'stops': GRADIENT_STOPS.replace('#0a0b12', 'rgba(0,0,0,1)')},
    {'stops': GRADIENT_STOPS.replace('#0a0b12', 'currentColor')},
    {'stops': GRADIENT_STOPS.replace('#0a0b12', '#123f')},
    {'stops': GRADIENT_STOPS.replace('#0a0b12', '#123456ff')},
    {'fill': 'url(#missing)'},
    {'kind': 'pattern'},
    {'gradient_attrs': 'href="#other"'},
    {'gradient_attrs': 'xlink:href="#other"'},
    {'gradient_attrs': 'href="https://example.invalid/paint.svg#other"'},
    {'stops': ''},
    {'gradient_attrs': 'opacity="0.5"'},
    {'gradient_attrs': 'fill-opacity="0.5"'},
    {'gradient_attrs': 'style="opacity: 0.5"'},
    {'gradient_attrs': 'style="fill-opacity: 0.5"'},
    {'gradient_attrs': 'style="display: none"'},
    {'gradient_attrs': 'filter="url(#f)"'},
    {'gradient_attrs': 'mask="url(#m)"'},
    {'ground_attrs': 'opacity="0.5"'},
    {'ground_attrs': 'fill-opacity="0.5"'},
    {'ground_attrs': 'style="fill-opacity: 0.5"'},
    {'ground_attrs': 'filter="url(#f)"'},
    {'ground_attrs': 'mask="url(#m)"'},
    {'stops': '<stop stop-color="black" style="stop-color: transparent"/>'},
    {'stops': '<stop stop-color="black" style="stop-opacity: 1; stop-opacity: 0.5"/>'},
    {'stops': '<stop stop-color="black" style="stop-opacity: var(--alpha)"/>'},
    {'stops': '<stop stop-color="black" style="unknown: 1"/>'},
    {'stops': '<stop stop-color="black" stop-opacity="nan"/>'},
    {'stops': '<stop stop-color="black" stop-opacity="0.5" style="stop-opacity: 1"/>'},
    {'stops': '<stop stop-color="black"><animate attributeName="stop-opacity"/></stop>'},
    {'stops': '<stop stop-color="black"/><animate attributeName="opacity"/>'},
    {'stops': '<stop/>'},
    {'stops': '<stop stop-color="black" filter="url(#f)"/>'},
    {'stops': '<stop stop-color="black" stop-opacity="1.1"/>'},
])
def test_ground_refuses_unsupported_gradient_paint(tmp_path, change):
    # Fresh matching stamps ensure paint refusal, rather than stale identity,
    # is the reason this otherwise alpha-correct PNG is not accepted.
    body = _gradient_svg(**change)
    code, report = banner_render.check(str(_trio(tmp_path, svg_body=body)))
    assert code == 2, report
    assert any('cannot derive painted ground' in line for line in report), report
    assert not any('STALE' in line for line in report), report


def test_gradient_acceptance_still_checks_pixels(tmp_path):
    root = _trio(tmp_path, svg_body=_gradient_svg())
    assert banner_render.check(str(root))[0] == 0
    # Corrupt an interior alpha byte, then stamp those bytes honestly.
    pixels = [SOLID] * (W * H)
    for i in (0, W - 1, W * (H - 1), W * H - 1, W + 3):
        pixels[i] = CLEAR
    png = root / 'docs' / 'banner.png'
    png.write_bytes(_png(W, H, pixels))
    (root / 'docs' / 'banner.stamp').write_text(
        'svg %s\npng %s\n' % (
            hashlib.sha256((root / 'docs' / 'banner.svg').read_bytes()).hexdigest(),
            hashlib.sha256(png.read_bytes()).hexdigest()))
    code, report = banner_render.check(str(root))
    assert code == 1, report
    assert any('1 non-opaque interior' in line for line in report), report


def test_ground_refuses_ambiguous_gradient_id(tmp_path):
    body = _gradient_svg().replace('</defs>',
        '<radialGradient id="bg"><stop stop-color="black"/></radialGradient></defs>')
    code, report = banner_render.check(str(_trio(tmp_path, svg_body=body)))
    assert code == 2, report
    assert any('resolve uniquely' in line for line in report), report


@pytest.mark.parametrize('change', [
    'ground-set', 'ground-visibility', 'ground-display', 'stylesheet-pi',
    'remote-set', 'remote-animate', 'root-visibility', 'stroke-dasharray',
    'radial-focus', 'animateTransform', 'animateMotion', 'animateColor',
    'discard', 'style', 'doctype', 'entity', 'unknown-ground', 'unknown-root',
    'unknown-gradient', 'unknown-stop', 'script', 'event-handler',
])
def test_ground_document_allowlist(tmp_path, change):
    body = _gradient_svg(stops=GRADIENT_STOPS.replace('<stop ', '<stop id="stop" ', 1))
    if change == 'ground-set':
        body = body.replace('fill="url(#bg)" />', 'fill="url(#bg)"><set attributeName="fill" to="transparent"/></rect>')
    elif change in {'ground-visibility', 'ground-display', 'stroke-dasharray', 'unknown-ground'}:
        attr = {'ground-visibility': 'visibility="hidden"', 'ground-display': 'display="none"',
                'stroke-dasharray': 'stroke-dasharray="1 100"', 'unknown-ground': 'paint-order="stroke"'}[change]
        body = body.replace('<rect ', '<rect ' + attr + ' ')
    elif change == 'stylesheet-pi':
        body = '<?xml-stylesheet type="text/css" href="data:text/css,rect%7Bopacity:0.2%7D"?>' + body
    elif change == 'remote-set':
        body = body.replace('</svg>', '<set href="#stop" attributeName="stop-opacity" to="0"/></svg>')
    elif change == 'remote-animate':
        body = body.replace('</svg>', '<animate href="#bg" attributeName="opacity" values="1;0" dur="1s"/></svg>')
    elif change in {'root-visibility', 'unknown-root', 'event-handler'}:
        attr = {'root-visibility': 'visibility="hidden"', 'unknown-root': 'overflow="visible"',
                'event-handler': 'onload="void(0)"'}[change]
        body = body.replace('<svg ', '<svg ' + attr + ' ')
    elif change == 'radial-focus':
        body = body.replace('linearGradient', 'radialGradient').replace('id="bg"', 'id="bg" fx="200%" fy="200%"')
    elif change in {'doctype', 'entity'}:
        body = ('<!DOCTYPE svg>' if change == 'doctype' else '<!DOCTYPE svg [<!ENTITY paint "black">]>') + body
    elif change in {'unknown-gradient', 'unknown-stop'}:
        tag = 'linearGradient' if change == 'unknown-gradient' else 'stop'
        body = body.replace('<' + tag + ' ', '<' + tag + ' visibility="hidden" ', 1)
    else:
        body = body.replace('</svg>', '<' + change + '/></svg>')
    code, report = banner_render.check(str(_trio(tmp_path, svg_body=body)))
    print(change, code, ' | '.join(report))
    assert code == 2, report
    assert any('cannot derive painted ground' in line for line in report), report
    assert not any('STALE' in line for line in report), report


def test_xml_declaration_preserves_ground_measurement(tmp_path):
    body = '<?xml version="1.0"?>' + _gradient_svg()
    assert banner_render.check(str(_trio(tmp_path, svg_body=body)))[0] == 0


@pytest.mark.parametrize('old,new', [
    ('width="4"', 'width="0_4"'),
    ('width="4"', 'width="４"'),
    ('rx="1"', 'rx="0_1"'),
    ('height="2"', 'height="２"'),
    ('viewBox="0 0 4 2"', 'viewBox="0 0 0_4 2"'),
    ('rx="1"', 'rx="1px"'),
    ('rx="1"', 'rx="1e999"'),
    ('rx="1"', 'rx="nan"'),
    ('rx="1"', 'rx="1."'),
    ('rx="1"', 'rx="1\u00a0"'),
])
def test_banner_refuses_numbers_outside_svg_grammar(tmp_path, old, new):
    # Replace root AND ground occurrences, where present; fresh stamps ensure
    # malformed geometry cannot hide behind an identity failure.
    body = SVG_BODY.replace(old, new)
    assert body != SVG_BODY
    code, report = banner_render.check(str(_trio(tmp_path, svg_body=body)))
    assert code == 2, report


@pytest.mark.parametrize('value', ['1e0', '+1.0E+0', '.1e1'])
def test_banner_measures_valid_exponents(tmp_path, value):
    body = SVG_BODY.replace('rx="1"', f'rx="{value}"').replace('4', '4e0')
    code, report = banner_render.check(str(_trio(tmp_path, svg_body=body)))
    assert code == 0, report


@pytest.mark.parametrize('body', [
    SVG_BODY.replace('<svg ', '<svg opacity="１" '),
    SVG_BODY.replace('<rect ', '<rect opacity="１" '),
    SVG_BODY.replace('<rect ', '<rect fill-opacity="１" '),
    SVG_BODY.replace('<rect ', '<rect stroke="black" stroke-width="０" stroke-opacity="１" '),
    _gradient_svg(stops='<stop stop-color="black" stop-opacity="１"/>'),
    _gradient_svg(stops='<stop stop-color="black" style="stop-opacity: １"/>'),
    _gradient_svg(gradient_attrs='opacity="１"'),
    _gradient_svg(gradient_attrs='style="fill-opacity: １"'),
    _gradient_svg().replace('x1="0%"', 'x1="０%"'),
    _gradient_svg().replace('offset="0%"', 'offset="０%"'),
])
def test_banner_numeric_paint_uses_the_same_grammar(tmp_path, body):
    code, report = banner_render.check(str(_trio(tmp_path, svg_body=body)))
    assert code == 2, report


@pytest.mark.parametrize('attribute', ['offset', 'x1', 'y1', 'x2', 'y2'])
@pytest.mark.parametrize('value', ['50 %', '50%%', '%'])
def test_gradient_rejects_split_or_malformed_percentage(tmp_path, attribute, value):
    original = '100%' if attribute in ('x2', 'y2') else '0%'
    body = _gradient_svg().replace(f'{attribute}="{original}"', f'{attribute}="{value}"', 1)
    assert body != _gradient_svg(), 'positive control: the intended coordinate changed'
    code, report = banner_render.check(str(_trio(tmp_path, svg_body=body)))
    assert code == 2, report
    assert any('UNMEASURED' in line and 'unsupported SVG number' in line for line in report), report


@pytest.mark.parametrize('value', ['50%', '50% ', ' 50%', '50 '])
@pytest.mark.parametrize('attribute', ['offset', 'x1'])
def test_gradient_accepts_number_tokens_with_outer_xml_whitespace(tmp_path, attribute, value):
    body = _gradient_svg().replace(f'{attribute}="0%"', f'{attribute}="{value}"', 1)
    code, report = banner_render.check(str(_trio(tmp_path, svg_body=body)))
    assert code == 0, report
