#!/usr/bin/env python3
"""SEMANTIC candidate selector — change the REPRESENTATION, not the change-detector.

WHY THIS SHAPE (three research legs, 2026-08-01; codex's leg supplied the correction).
Four cheap selectors were built and refuted in one session — dHash prefilter, phase-correlation motion gate,
per-frame structure features, and a temporally-binned Stage-A budget. Every one of them asked a variant of
*"did the pixels change?"*, which is the axis the field abandoned. Codex's leg rejected the obvious next step
too: a better shot-boundary detector (PySceneDetect / TransNetV2) decides from adjacent-frame visual
difference, so adopting one as the gate would have repeated the same failure wearing a library's name.

The prior-art answer is to change what the cheap stage LOOKS AT: semantic embeddings instead of pixels, with
temporal coverage as a hard contract rather than an emergent hope. This module is that selector.

TWO PIECES, DELIBERATELY SPLIT:
  * `select()` and everything under it is PURE NUMPY — deterministic, no torch, no network, unit-testable in
    the system interpreter. That is where every decision is made.
  * `embed` (the CLI subcommand) needs torch and runs in the whisperx venv. It only produces vectors.
The split means the DECIDING code is testable on a box with no GPU and no model, and a broken encoder can
never silently change a selection rule.

WHAT IS NOT HERE (v1): OCR novelty. No OCR engine is installed on this box (no tesseract, no pytesseract,
no easyocr), and codex's leg rejected raw single-frame OCR as a SOLE gate anyway — ACE finds it noisy and
low-precision on programming video without cross-frame consolidation. Embedding diversity is the half that
also preserves textless diagrams, so it is the honest v1. OCR novelty is an additive follow-up, not a
missing prerequisite.

⚠ UNVALIDATED UNTIL MEASURED. Nothing in the pipeline consumes this. The bar it must clear is the one the
Stage-A budget failed: SAVED-frame recall at or very near 100% on `vision_measure/replay_stage_a.py`-style
replay, where the budget scored 31.8%.
"""
from __future__ import annotations

import json, os, sys

import numpy as np

EMBED_MODEL = os.environ.get("VI_EMBED_MODEL", "openai/clip-vit-base-patch32")

# Temporal COVERAGE is a property of the video, not of how much we are willing to spend, so the bin count is
# FIXED and independent of the budget. Coupling them (n_bins = budget, the first version) made recall
# NON-MONOTONE: raising the budget re-partitioned the timeline, so budget 80 was not budget 60 plus twenty
# more frames — it was a different question. Measured 2026-08-01: lm-studio 5/7 -> 4/7 -> 4/7 and
# 5-biggest-lies 2/4 -> 4/4 -> 3/4 across budgets 40/60/80, i.e. MORE budget lost frames. With a fixed
# partition a larger budget is a superset and recall cannot go backwards.
SEM_BINS = int(os.environ.get("VI_SEM_BINS", "12"))

# NOVELTY WEIGHT. Facility location alone optimises COVERAGE, and coverage gain scales with CLUSTER SIZE:
# a talking-head frame that represents 50 near-identical frames always outbids a unique diagram that
# represents only itself. Measured consequence (2026-08-01, budget 140): lm-studio lost 2 of 7 published
# frames while RETAINING 81% of candidates — not a budget shortfall, the objective actively preferring
# typical frames over informative ones. 105 of our 109 published companions score 9-10, so the frames this
# loses are load-bearing figures, which is the worst place for a residual failure.
# lam=0 is pure coverage (the measured 90.9% baseline); lam=1 is pure novelty. Swept, not guessed.
# ⛔ MEASURED AND REFUTED 2026-08-01 — default 0.0 (pure coverage). Swept on 5 real videos at budget 140:
#     lam   0.0    0.25   0.5    0.75   1.0
#     all   89.2%  83.8%  75.7%  73.0%  75.7%
#     >=9   88.6%  82.9%  77.1%  74.3%  74.3%
# Monotone DECLINE, and tencent collapsed 8/10 -> 6 -> 5 -> 3 -> 3. The reason is written in select()'s own
# docstring, which predicted it: novelty rewards being UNLIKE what is chosen, and the frames most unlike
# everything are motion-blur and mid-transition junk, not diagrams. Coverage is the right objective after
# all. Kept as a knob because the sweep is the evidence, and a future change should have to re-run it.
SEM_NOVELTY = float(os.environ.get("VI_SEM_NOVELTY", "0.0"))

# OCR-NOVELTY WEIGHT. This targets the ONE failure mode the embedding selector measurably has: a screencast
# holds dozens of near-identical UI frames, CLIP embeds them almost identically, and coverage keeps one
# representative — but only one frame in that cluster carries the readable content the judge scored 9.
# Measured 2026-08-01: 3 of the 4 residual misses were text-bearing UI (a Connected Apps interface, a
# terminal/IDE split screen, a CAD window). Appearance cannot separate "the terminal with the important
# output" from the same terminal 2s earlier; TEXT can.
# ⚠ A frame with NO text must not be punished for it — textless diagrams are exactly what the embedding term
# is for. Frames without tokens fall back to their coverage score rather than scoring zero novelty.
SEM_OCR_W = float(os.environ.get("VI_SEM_OCR_W", "0.35"))
_WORD_RE = None


# ==================================================================================================
# PURE SELECTION MATH — no torch, no network, fully deterministic
# ==================================================================================================

def cosine_sim(V: np.ndarray) -> np.ndarray:
    """n x n cosine similarity from n x d vectors. Rows are L2-normalised first."""
    V = np.asarray(V, dtype=np.float64)
    n = np.linalg.norm(V, axis=1, keepdims=True)
    n[n == 0] = 1.0
    U = V / n
    return U @ U.T


def _gain(S: np.ndarray, cur: np.ndarray) -> np.ndarray:
    """Facility-location marginal gain of adding each item, given current per-item best similarity."""
    return np.maximum(S, cur[:, None]).sum(axis=0) - cur.sum()


def _unit(x: np.ndarray) -> np.ndarray:
    """Min-max to [0,1]; all-equal input -> zeros, so a constant term never dominates a blend."""
    lo, hi = float(np.min(x)), float(np.max(x))
    return np.zeros_like(x) if hi <= lo else (x - lo) / (hi - lo)


def ocr_novelty(tokens, sel):
    """Fraction of each frame's text tokens NOT already present in the selected frames.

    `tokens` is a list of sets (one per candidate; empty set = no text found). Returns (values, has_text)
    so the caller can leave textless frames out of the blend entirely instead of scoring them 0.
    """
    seen = set()
    for i in sel:
        seen |= tokens[i]
    vals = np.zeros(len(tokens), dtype=np.float64)
    has = np.zeros(len(tokens), dtype=bool)
    for i, tk in enumerate(tokens):
        if not tk:
            continue
        has[i] = True
        vals[i] = len(tk - seen) / len(tk)
    return vals, has


def _score(S, cur, sel, lam, tokens=None, ocr_w=0.0):
    """Blend COVERAGE (represents the set) with NOVELTY (unlike what is already chosen).

    Both terms are min-max normalised each round before blending, because they live on different scales:
    coverage gain grows with n, novelty is bounded in [0,2] for cosine. Blending raw values would let n
    silently decide the weighting.
    """
    gain = _gain(S, cur)
    if lam <= 0 and not (tokens and ocr_w > 0):
        return gain                                   # exact pre-novelty behaviour; the control arm
    base = _unit(gain)
    out = base.copy()
    if lam > 0:
        nov = np.ones(S.shape[0]) if not sel else 1.0 - S[:, sel].max(axis=1)
        out = (1.0 - lam) * base + lam * _unit(nov)
    if tokens and ocr_w > 0:
        onov, has = ocr_novelty(tokens, sel)
        blended = (1.0 - ocr_w) * out + ocr_w * _unit(onov)
        out = np.where(has, blended, out)             # textless frames keep their coverage score untouched
    return out


def select(vectors, timestamps, budget, n_bins=None, eligible=None, novelty=None,
           tokens=None, ocr_w=None):
    """Pick `budget` candidates: one reserved per temporal bin, remainder by facility-location diversity.

    Facility location maximises how well the SELECTED set represents the WHOLE candidate set, which is the
    published objective for representative-subset selection — not "most different from each other", which
    would chase outliers (a motion-blurred frame is maximally unlike everything and worth nothing).

    Temporal bins are a COVERAGE CONTRACT, not a selector. This is the correction that killed the previous
    attempt: that one used bins to CHOOSE (take the middle frame of each bin), which is uncorrelated with
    value and scored 31.8% recall. Here each bin merely reserves a slot, and the choice inside it is made by
    the same representativeness objective as everywhere else.

    Determinism: greedy with `argmax`, which returns the FIRST maximal index; ties therefore resolve by
    candidate order, and callers pass candidates sorted by timestamp. No randomness anywhere.

    ⚠ KNOWN PROPERTY, not a defect: facility location maximises COVERAGE of the candidate set, so the
    FIRST pick is the bulk-representative frame — with 50 near-identical frames and 5 distinctive ones, the
    typical frame covers more of the set than the unusual one. Distinctive frames are picked on subsequent
    rounds, once the bulk is covered. This costs one slot out of a 40-60 budget, but it means the objective
    is COVERAGE, not novelty, and those are not the same thing. Codex's leg flagged exactly this gap: the
    published objectives optimise importance/representativeness/diversity, and NONE of them is "carries
    evidence a transcript cannot". If measurement shows coverage is the wrong target here, the fix is an
    explicit novelty term — not a bigger budget.

    Returns (indices_sorted_by_time, reasons) where reason is 'bin-reserved' or 'representative'.
    """
    V = np.asarray(vectors, dtype=np.float64)
    ts = np.asarray(timestamps, dtype=np.float64)
    n = len(ts)
    if n == 0:
        return [], {}
    budget = int(min(budget, n))
    if budget >= n:
        return list(range(n)), {i: "all" for i in range(n)}

    S = cosine_sim(V)
    lam = SEM_NOVELTY if novelty is None else float(novelty)
    ow = SEM_OCR_W if ocr_w is None else float(ocr_w)
    ok = np.ones(n, bool) if eligible is None else np.asarray(eligible, bool)
    n_bins = SEM_BINS if n_bins is None else int(n_bins)
    n_bins = max(1, min(n_bins, budget))   # can never exceed the budget; reservations must fit

    lo, hi = ts.min(), ts.max()
    span = (hi - lo) or 1.0
    bin_of = np.minimum(((ts - lo) / span * n_bins).astype(int), n_bins - 1)

    sel: list[int] = []
    reasons: dict[int, str] = {}
    # cosine similarity is bounded below by -1, so that is the correct "nothing selected yet" baseline.
    # -inf would make the FIRST gain computation +inf for every candidate, the argmax would tie across all
    # of them, and the first bin's reservation would silently fall back to first-index instead of merit.
    cur = np.full(n, -1.0)

    # --- pass 1: reserve one slot per bin, choosing WITHIN the bin by representativeness -------------
    for b in range(n_bins):
        if len(sel) >= budget:
            break
        members = np.where((bin_of == b) & ok)[0]
        if members.size == 0:
            continue                      # empty bin is RECORDED by absence, never back-filled from elsewhere
        g = _score(S, cur, sel, lam, tokens, ow)
        pick = int(members[int(np.argmax(g[members]))])
        sel.append(pick); reasons[pick] = "bin-reserved"
        cur = np.maximum(cur, S[:, pick])

    # --- pass 2: spend the remaining budget globally on representativeness --------------------------
    while len(sel) < budget:
        g = _score(S, cur, sel, lam, tokens, ow)
        g[sel] = -np.inf
        g[~ok] = -np.inf
        if not np.isfinite(g).any():
            break
        pick = int(np.argmax(g))
        sel.append(pick); reasons[pick] = "representative"
        cur = np.maximum(cur, S[:, pick])

    sel.sort(key=lambda i: ts[i])
    return sel, reasons



def select_union(vectors, timestamps, budget, tokens=None, n_bins=None, ocr_w=0.5):
    """SHIPPING CONFIGURATION: the union of a coverage-only and a coverage+OCR selection.

    Neither arm is good enough alone and a single tuned weight is worse than both, because the right weight
    depends on whether a video's value lives in TEXT or in DIAGRAMS — and that is a property of the video we
    have no reliable cheap way to detect (the video-level triage that tried was refuted). The two arms fail
    on DIFFERENT videos, so their union sidesteps the question entirely.

    MEASURED 2026-08-01, 6 videos, budget 140, against frames we actually published:
        coverage only (ocr_w=0)   90.9%   (misses text-bearing UI: lm-studio 5/7)
        coverage+OCR  (ocr_w=0.5) 81.8%   (misses diagrams in text-heavy video: tencent 4/10)
        UNION                     95.5%   42/44, five of six videos perfect, 36% of vision calls saved
    Only tencent still loses 2 of 10 — a 39-minute video with 521 candidates.

    Cost note: the union needs OCR on ALL candidates, but OCR is local onnxruntime CPU (~0.2s/frame) while
    the calls it avoids are TWO vision-model calls per frame. The trade is heavily favourable.
    """
    a, ra = select(vectors, timestamps, budget, n_bins=n_bins, novelty=0.0, tokens=None, ocr_w=0.0)
    b, rb = select(vectors, timestamps, budget, n_bins=n_bins, novelty=0.0, tokens=tokens, ocr_w=ocr_w)
    idx = sorted(set(a) | set(b), key=lambda i: timestamps[i])
    reasons = {}
    for i in idx:
        tags = []
        if i in ra: tags.append("cov:" + ra[i])
        if i in rb: tags.append("ocr:" + rb[i])
        reasons[i] = "+".join(tags)
    return idx, reasons

def bins_covered(timestamps, selected_idx, n_bins):
    """Diagnostic: which temporal bins got a slot. An UNFILLED bin must be visible, never papered over."""
    ts = np.asarray(timestamps, float)
    lo, hi = ts.min(), ts.max()
    span = (hi - lo) or 1.0
    got = {min(int((ts[i] - lo) / span * n_bins), n_bins - 1) for i in selected_idx}
    return sorted(got), [b for b in range(n_bins) if b not in got]


# ==================================================================================================
# ENCODER — needs torch; run under the whisperx venv. Produces vectors only, decides nothing.
# ==================================================================================================

def embed_paths(paths, batch=32):
    import torch
    from PIL import Image
    from transformers import CLIPModel, CLIPProcessor
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    model = CLIPModel.from_pretrained(EMBED_MODEL).to(dev).eval()
    proc = CLIPProcessor.from_pretrained(EMBED_MODEL)
    out = []
    with torch.no_grad():
        for i in range(0, len(paths), batch):
            imgs = [Image.open(p).convert("RGB") for p in paths[i:i + batch]]
            inp = proc(images=imgs, return_tensors="pt").to(dev)
            v = model.get_image_features(**inp)
            out.append(v.cpu().numpy())
    return np.concatenate(out, axis=0) if out else np.zeros((0, 512), np.float32)


def ocr_token_sets(paths, min_conf=0.5):
    """Normalised text-token set per frame. Local, CPU, onnxruntime — no model calls, no network."""
    import re
    from rapidocr_onnxruntime import RapidOCR
    from PIL import Image
    engine = RapidOCR()
    word = re.compile(r"[a-z0-9_./-]{2,}")
    out = []
    for p in paths:
        try:
            res, _ = engine(np.array(Image.open(p).convert("RGB")))
        except Exception:
            out.append(set()); continue
        toks = set()
        for item in (res or []):
            try:
                txt, conf = item[1], float(item[2])
            except Exception:
                continue
            if conf >= min_conf:
                toks |= set(word.findall(str(txt).lower()))
        out.append(toks)
    return out


def main():
    if len(sys.argv) < 3 or sys.argv[1] != "embed":
        print(__doc__); return 2
    manifest = json.load(open(sys.argv[2]))          # [{frame, ts, ...}]
    root = os.path.dirname(sys.argv[2])
    paths = [os.path.join(root, m["frame"]) for m in manifest]
    V = embed_paths(paths)
    np.save(sys.argv[3], V)
    print(f"embedded {len(paths)} frames -> {sys.argv[3]} shape={V.shape}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
