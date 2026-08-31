#!/usr/bin/env python3
"""TEETH TEST for the vision perceptual prefilter (owner-requested, 2026-08-01).

The owner's concern when approving dense 2s sampling was "make sure we have everything set up right so we
don't get any made up outputs / fake comparisons / context overloads / empty returns".  For a filter, the
two ways to be silently wrong are mirror images and BOTH look like a working filter from the outside:

    always-matches  -> drops every frame  -> "0 frames to score", pipeline starves
    never-matches   -> drops no frame     -> the filter is INERT, the pipeline pays 10x for nothing

Reporting only "N dropped" cannot distinguish a real filter from either.  So this proves BOTH directions
against REAL frames on disk: it must FIRE on duplicates and must NOT fire on genuinely different frames.

Run:  python3 guard/test_perceptual_prefilter.py
Exit: 0 all proven · 1 a property failed · 2 UNMEASURED (no corpus frames found)
"""
import glob
import os
import shutil
import sys
from PIL import Image
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import vision_ingest as vi

CORPUS = os.environ.get("VISION_CORPUS_GLOB", "./video/*/vision/keeps/*.jpg")  # EDIT ME
FAILS = []
PROVEN = []


def check(name, cond, detail=""):
    (PROVEN if cond else FAILS).append(f"{name}{(' — ' + detail) if detail else ''}")
    print(f"  {'OK  ' if cond else 'FAIL'} {name}{(' — ' + detail) if detail else ''}")


def main():
    frames = sorted(glob.glob(CORPUS))
    if len(frames) < 6:
        print(f"UNMEASURED: found {len(frames)} corpus frames, need >= 6. Not asserting anything.")
        return 2
    print(f"perceptual prefilter teeth test — {len(frames)} real corpus frames available\n")

    # ---- 1. DETERMINISM. A hash that varies per call makes every later comparison meaningless. -------
    a = vi._dhash(frames[0])
    b = vi._dhash(frames[0])
    check("determinism: same file hashes identically", a is not None and a == b, f"{a}")

    # ---- 2. IDENTITY. A file against itself must be distance 0, i.e. the filter CAN match. ----------
    check("identity: distance(f, f) == 0", vi._hamming(a, b) == 0)

    # ---- 3. DISCRIMINATION. Distinct frames from DIFFERENT videos must exceed the drop threshold.
    #         If this fails the filter is 'always-matches' and would delete the corpus. ---------------
    by_video = {}
    for f in frames:
        by_video.setdefault(f.split("/vision/keeps/")[0], []).append(f)
    vids = [v for v in by_video if len(by_video[v]) >= 2]
    if len(vids) < 2:
        print("UNMEASURED: need frames from >= 2 videos.")
        return 2
    f1, f2 = by_video[vids[0]][0], by_video[vids[1]][0]
    d_cross = vi._hamming(vi._dhash(f1), vi._dhash(f2))
    check("discrimination: distinct frames exceed the drop threshold",
          d_cross > vi.PHASH_DIST, f"distance={d_cross} > PHASH_DIST={vi.PHASH_DIST}")

    # ---- 4. TEETH — the filter FIRES. N copies of one frame must collapse to exactly 1. -------------
    tmp = tempfile.mkdtemp(prefix="phash_teeth_")
    try:
        dupes = []
        for i in range(8):
            d = os.path.join(tmp, f"dup_{i}.jpg")
            shutil.copyfile(frames[0], d)
            dupes.append({"ts": i * 2.0, "path": d, "src": "floor"})
        kept, dropped, _ = vi._perceptual_prefilter(dupes)
        check("TEETH: 8 identical frames collapse to 1", len(kept) == 1 and dropped == 7,
              f"kept={len(kept)} dropped={dropped}")

        # ---- 5. NOT OVERBROAD — the filter must NOT fire on genuinely different frames. -------------
        distinct = []
        picked = [by_video[v][0] for v in vids[:6]]
        for i, src in enumerate(picked):
            d = os.path.join(tmp, f"dist_{i}.jpg")
            shutil.copyfile(src, d)
            distinct.append({"ts": i * 2.0, "path": d, "src": "floor"})
        kept2, dropped2, _ = vi._perceptual_prefilter(distinct)
        check("NOT OVERBROAD: distinct frames all survive",
              len(kept2) == len(distinct) and dropped2 == 0,
              f"kept={len(kept2)}/{len(distinct)} dropped={dropped2}")

        # ---- 6. UNREADABLE -> KEEP. A corrupt frame must never be silently dropped. -----------------
        bad = os.path.join(tmp, "corrupt.jpg")
        open(bad, "wb").write(b"not a jpeg")
        mixed = [{"ts": 0.0, "path": frames[0], "src": "floor"},
                 {"ts": 2.0, "path": bad, "src": "floor"}]
        kept3, _, stats = vi._perceptual_prefilter(mixed)
        check("unreadable frame is KEPT, not silently dropped",
              len(kept3) == 2 and stats["unreadable"] == 1, f"kept={len(kept3)} {stats}")

        # ---- 7. The threshold is CRITICAL. At dist=64 everything matches; the filter must then
        #         collapse distinct frames — proving the knob actually drives behaviour rather than the
        #         result being an artifact of something else. -----------------------------------------
        kept4, dropped4, _ = vi._perceptual_prefilter(distinct, dist=64)
        check("threshold is critical: dist=64 collapses everything",
              len(kept4) == 1, f"kept={len(kept4)} at dist=64 (vs {len(kept2)} at {vi.PHASH_DIST})")

        # ---- 8. THE ANTI-DRIFT PROPERTY (added 2026-08-02 — mutation PF2 survived without it).
        #         The docstring promises the anchor is the last KEPT frame, not the previous one, "so a
        #         slow drift across many frames still eventually registers instead of every step being
        #         'close enough' and the whole run collapsing to one frame." Nothing tested that, so
        #         moving the anchor to the previous frame passed every check.
        #         The fixture drifts in HASH SPACE: an 8x9 block grid (dHash's own geometry) with ONE
        #         cell flipped per frame, so each step moves ~1 bit while the total moves far more.
        import numpy as np
        rng = np.random.default_rng(7)
        grid = rng.integers(40, 215, size=(8, 9)).astype("uint8")
        cells = [(r, c) for r in range(8) for c in range(9)]
        rng.shuffle(cells)
        drift = []
        for i in range(14):
            if i:
                r, c = cells[i % len(cells)]
                grid[r, c] = 255 if grid[r, c] < 128 else 0
            dp = os.path.join(tmp, f"drift_{i:02d}.png")
            Image.fromarray(grid).resize((360, 320), Image.NEAREST).save(dp)
            drift.append({"ts": i * 2.0, "path": dp, "src": "floor"})

        dh = [vi._dhash(f["path"]) for f in drift]
        consec = [vi._hamming(dh[i], dh[i + 1]) for i in range(len(dh) - 1)]
        total = vi._hamming(dh[0], dh[-1])
        # PRECONDITIONS — assert the fixture still tests what it claims. If a future dHash change makes
        # the steps large, this stops being a drift fixture and must SAY so rather than pass vacuously.
        check("drift fixture precondition: every STEP is within the threshold",
              max(consec) <= vi.PHASH_DIST, f"max step {max(consec)} vs dist {vi.PHASH_DIST}")
        check("drift fixture precondition: the TOTAL drift exceeds the threshold",
              total > vi.PHASH_DIST, f"total {total} vs dist {vi.PHASH_DIST}")
        kept5, _, _ = vi._perceptual_prefilter(drift)
        check("a slow drift eventually REGISTERS (anchor is the last KEPT frame, not the previous)",
              len(kept5) > 1,
              f"kept {len(kept5)}/{len(drift)} — 1 would mean the anchor follows the previous frame")

        # ---- 9. THE THRESHOLD BOUNDARY IS INCLUSIVE (mutation PF3 survived without it).
        #         The rule is `<= dist` = same picture. No fixture sat at EXACTLY dist, so flipping the
        #         comparison to `<` was invisible. Measure a real pair's distance and pin both sides.
        d_exact = vi._hamming(dh[0], dh[3])
        if d_exact >= 1:
            pair = [drift[0], drift[3]]
            k_at, _, _ = vi._perceptual_prefilter(pair, dist=d_exact)
            k_below, _, _ = vi._perceptual_prefilter(pair, dist=d_exact - 1)
            check(f"distance EXACTLY at the threshold ({d_exact}) is DROPPED (<= is inclusive)",
                  len(k_at) == 1, f"kept={len(k_at)} at dist={d_exact}")
            check(f"one below the threshold ({d_exact - 1}) is KEPT",
                  len(k_below) == 2, f"kept={len(k_below)} at dist={d_exact - 1}")
        else:
            check("boundary fixture is usable (needs a pair at distance >= 1)", False,
                  f"measured distance {d_exact} — cannot pin the boundary")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{len(PROVEN)} proven · {len(FAILS)} failed")
    if FAILS:
        for f in FAILS:
            print(f"  ✗ {f}")
        return 1
    print("PREFILTER HAS TEETH and is not overbroad.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
