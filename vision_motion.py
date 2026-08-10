#!/usr/bin/env python3
"""Phase-correlation MOTION discriminator for the video-frame pipeline.

THE PROBLEM IT SOLVES.  The dHash prefilter measures PIXEL change, and on real video pixel change is
anti-correlated with information: a camera pan or b-roll cut moves every pixel and carries nothing, while a
new terminal line moves a handful of pixels and carries everything.  Measured over four videos, the video
where the prefilter kept the MOST (83%) yielded the LEAST (1.4%) and cost 145 model calls per qualified
frame.  Raising the dHash threshold cannot fix that: any Hamming distance large enough to suppress a pan
also suppresses a small-but-real screen change.  Hamming distance is the wrong AXIS, not the wrong value.

THE PRINCIPLE.  Camera motion is *the same content, translated*.  New information is *different content in
place*.  Translation is exactly what phase correlation detects, cheaply and deterministically.

FAIL-OPEN BY CONSTRUCTION.  Zoom, rotation and parallax are not translations, so alignment fails, `resid`
stays high, and the frame is classified STATIC and gets scored anyway.  That costs one wasted model call and
never loses content.  The only fail-CLOSED risk is a slide transition animated as a push/slide — which IS a
translation — and run-collapse covers it, because the settle frame at the end of the run is the one kept.

NOTHING HERE DELETES ANYTHING.  This module only classifies.  Shadow mode (`shadow_report`) exists so the
gate can be measured against real stored scores before it is ever allowed to drop a frame.
"""
from __future__ import annotations

import os

import numpy as np

# Thresholds — all overridable, all documented against the measurement that set them.
RESID_MAX = float(os.environ.get("VI_MOTION_RESID", "0.45"))   # translation must explain most of the change
MIN_SHIFT = int(os.environ.get("VI_MOTION_MINSHIFT", "2"))     # px at 160x90; below this it isn't a pan
MIN_RAW = float(os.environ.get("VI_MOTION_MINRAW", "4.0"))     # /255; below this nothing really changed
# ★ ABSOLUTE residual ceiling — added 2026-08-01 after the guard found a real false positive.
# PERIODIC content (a table, a grid, code indentation, a repeating texture) can ALIAS: phase correlation
# locks onto the content's own period and reports a large "shift" that partially explains a genuine content
# change. The guard's fixture hit this exactly — a periodic texture, vertically flipped, produced dy=-30 and
# ratio 0.337, i.e. classified MOTION, which would have DELETED a new screen. The RATIO alone cannot
# separate the two cases, but the absolute residual can: a true translation aligns to resid ~0-3, while the
# aliased match left resid 14.8. This is the fail-CLOSED direction, so it gets a second, independent bound.
MAX_RESID_ABS = float(os.environ.get("VI_MOTION_RESID_ABS", "8.0"))  # /255

W, H = 160, 90


def to_gray_small(img) -> np.ndarray:
    """PIL Image -> 160x90 float32 grayscale in 0..255. Deterministic (fixed resample filter)."""
    from PIL import Image
    return np.asarray(img.convert("L").resize((W, H), Image.BILINEAR), dtype=np.float32)


def load_gray(path: str) -> np.ndarray | None:
    from PIL import Image
    try:
        with Image.open(path) as im:
            return to_gray_small(im)
    except Exception:
        return None          # unreadable -> caller treats as STATIC (fail-open), never as motion


def _valid_mask(dy: int, dx: int) -> np.ndarray:
    """np.roll WRAPS, so the strip rolled in from the far edge is garbage. Exclude it from both residuals."""
    m = np.ones((H, W), dtype=bool)
    if dy > 0:
        m[:dy, :] = False
    elif dy < 0:
        m[dy:, :] = False
    if dx > 0:
        m[:, :dx] = False
    elif dx < 0:
        m[:, dx:] = False
    return m


def motion_verdict(prev_g: np.ndarray, cur_g: np.ndarray):
    """Return (dx, dy, resid, raw).

    `raw`   = mean |difference| with no alignment      -> how much changed at all
    `resid` = mean |difference| after undoing the best translation -> how much a PAN cannot explain
    A pan drives resid far below raw. A slide change leaves resid ~= raw.
    """
    win = np.hanning(H)[:, None] * np.hanning(W)[None, :]
    F1 = np.fft.rfft2(prev_g * win)
    F2 = np.fft.rfft2(cur_g * win)
    R = F1 * np.conj(F2)
    R /= (np.abs(R) + 1e-9)                       # phase only — amplitude carries no shift information
    corr = np.fft.irfft2(R, s=(H, W))
    dy, dx = np.unravel_index(int(np.argmax(corr)), corr.shape)
    dy = int(dy - H) if dy > H // 2 else int(dy)   # unwrap circular shift to a signed offset
    dx = int(dx - W) if dx > W // 2 else int(dx)

    aligned = np.roll(np.roll(cur_g, dy, axis=0), dx, axis=1)
    v = _valid_mask(dy, dx)
    if not v.any():                                # shift so large nothing valid remains -> cannot judge
        return dx, dy, float("inf"), 0.0
    resid = float(np.mean(np.abs(prev_g - aligned)[v]))
    raw = float(np.mean(np.abs(prev_g - cur_g)[v]))
    return dx, dy, resid, raw


def is_motion(dx: int, dy: int, resid: float, raw: float) -> bool:
    """MOTION iff something changed, it changed by a real shift, and translation explains most of it."""
    if raw < MIN_RAW:
        return False                                # nothing meaningfully changed
    if abs(dx) + abs(dy) < MIN_SHIFT:
        return False                                # changed in place -> new content, not a pan
    if resid > MAX_RESID_ABS:
        return False                                # periodic-content aliasing: a "shift" that still leaves
                                                    # a lot unexplained is not a pan (see MAX_RESID_ABS)
    return (resid / raw) <= RESID_MAX               # translation accounts for the change


def classify_sequence(frames):
    """frames: [(key, gray_array_or_None)] in timeline order -> [(key, is_motion, dx, dy, resid, raw)].

    The FIRST frame has no predecessor and is always STATIC — never dropped by this gate.
    """
    out, prev = [], None
    for key, g in frames:
        if prev is None or g is None:
            out.append((key, False, 0, 0, 0.0, 0.0))
            prev = g if g is not None else prev
            continue
        dx, dy, resid, raw = motion_verdict(prev, g)
        out.append((key, is_motion(dx, dy, resid, raw), dx, dy, resid, raw))
        prev = g
    return out


def collapse_runs(verdicts):
    """Consecutive MOTION frames form a run; keep only the LAST (the settle frame) and every STATIC frame.

    A scroll or a pan ENDS somewhere, and the end state is where the new information lives. Returns the set
    of keys to KEEP. Never returns empty when given a non-empty input.
    """
    keep, run = set(), []
    for i, (key, motion, *_rest) in enumerate(verdicts):
        if motion:
            run.append(key)
            continue
        if run:
            keep.add(run[-1])                       # settle frame of the run that just ended
            run = []
        keep.add(key)
    if run:
        keep.add(run[-1])                           # a run ending at the sequence tail still settles
    return keep


def shadow_report(verdicts, qualified_keys):
    """SHADOW MODE — measure what the gate WOULD have done. Drops nothing.

    The safety question is one number: how many frames that actually QUALIFIED would have been dropped.
    """
    keep = collapse_runs(verdicts)
    all_keys = [k for k, *_ in verdicts]
    would_drop = [k for k in all_keys if k not in keep]
    lost = [k for k in would_drop if k in qualified_keys]
    return {
        "n": len(all_keys),
        "motion": sum(1 for _k, m, *_r in verdicts if m),
        "would_drop": len(would_drop),
        "would_keep": len(keep),
        "qualified": len(qualified_keys),
        "qualified_dropped": len(lost),
        "qualified_dropped_keys": lost,
        "calls_saved": 2 * len(would_drop),
    }
