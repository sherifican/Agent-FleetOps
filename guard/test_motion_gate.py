#!/usr/bin/env python3
"""Both-directions guard for the phase-correlation motion gate.

This gate is allowed to DELETE frames, so it must be proven in both directions before it ever runs live:
it must FIRE on real translation, and it must stay SILENT on new content. Synthetic fixtures are used on
purpose — ground truth is constructed, not labelled, so a wrong verdict cannot be argued away.

Run: python3 guard/test_motion_gate.py
"""
import os, sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import vision_motion as vm

FAILED = []
rng = np.random.default_rng(20260801)   # fixed seed — this suite must be deterministic


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))
    if not cond:
        FAILED.append(name)


def scene(kind="texture"):
    """A synthetic 'screen' with real structure — flat noise would make alignment trivial and the test weak."""
    g = np.zeros((vm.H, vm.W), dtype=np.float32)
    yy, xx = np.mgrid[0:vm.H, 0:vm.W]
    g += 40 * np.sin(xx / 7.0) + 30 * np.cos(yy / 5.0)          # texture
    g[20:40, 30:90] = 200.0                                      # a bright block (a 'slide element')
    g[55:70, 20:140] = 15.0                                      # a dark bar (a 'terminal line')
    if kind == "texture":
        g += rng.normal(0, 6, g.shape)
    return np.clip(g + 110, 0, 255).astype(np.float32)


def translate(g, dx, dy):
    """Shift with EDGE padding, not wraparound — a real pan reveals new content at the leading edge."""
    out = np.empty_like(g)
    src = np.roll(np.roll(g, dy, axis=0), dx, axis=1)
    out[:] = src
    if dy > 0:   out[:dy, :] = g[0, :]
    elif dy < 0: out[dy:, :] = g[-1, :]
    if dx > 0:   out[:, :dx] = g[:, 0:1]
    elif dx < 0: out[:, dx:] = g[:, -1:]
    return out


print("=" * 78)
print("MOTION GATE GUARD — must fire on translation, must stay silent on new content")
print("=" * 78)

base = scene()

# ---------- 1. TEETH: fires on real translation ----------
print("\n[1] FIRES on translation (true positives)")
for dx, dy in [(3, 0), (6, 0), (0, 3), (-5, 2), (12, -4), (2, 2)]:
    cur = translate(base, dx, dy)
    v = vm.motion_verdict(base, cur)
    got = vm.is_motion(*v)
    check(f"pan dx={dx:+d} dy={dy:+d} -> MOTION", got,
          "" if got else f"dx={v[0]} dy={v[1]} resid/raw={v[2]/max(v[3],1e-9):.3f}")

# ---------- 2. NOT OVERBROAD: silent on new content in place ----------
print("\n[2] SILENT on new content (true negatives — the frames that must never be lost)")
# (a) a slide change: the bright block's content is replaced, nothing translates
slide = base.copy(); slide[20:40, 30:90] = 20.0
v = vm.motion_verdict(base, slide); check("slide element replaced -> STATIC", not vm.is_motion(*v),
                                          f"resid/raw={v[2]/max(v[3],1e-9):.3f} shift=({v[0]},{v[1]})")
# (b) a new terminal line appears — small, local, high-value: the exact frame the dHash filter misses
line = base.copy(); line[72:78, 20:140] = 10.0
v = vm.motion_verdict(base, line); check("new terminal line -> STATIC", not vm.is_motion(*v),
                                         f"resid/raw={v[2]/max(v[3],1e-9):.3f}")
# (c) a wholly different screen — must be GENUINELY unrelated. An earlier fixture used the same periodic
#     texture flipped, which is not "different", it is nearly "translated" (see [2e]).
v = vm.motion_verdict(base, np.clip(rng.normal(128, 45, (vm.H, vm.W)), 0, 255).astype(np.float32))
check("entirely different screen -> STATIC", not vm.is_motion(*v),
      f"ratio={v[2]/max(v[3],1e-9):.3f}")
# (e) ★ REGRESSION: periodic content must not ALIAS into a false pan. This is the fail-CLOSED direction —
#     a false MOTION here would delete a genuinely new screen. Found by this guard on 2026-08-01: a flipped
#     periodic texture produced dy=-30, ratio 0.337 -> MOTION, until the absolute residual bound was added.
aliased = scene()[::-1].copy()
v = vm.motion_verdict(base, aliased)
check("periodic content flipped -> STATIC (aliasing must not read as a pan)", not vm.is_motion(*v),
      f"shift=({v[0]},{v[1]}) resid={v[2]:.2f} ratio={v[2]/max(v[3],1e-9):.3f}")
# (d) a dialog box pops up in place
dlg = base.copy(); dlg[30:60, 50:120] = 245.0
v = vm.motion_verdict(base, dlg); check("dialog appears in place -> STATIC", not vm.is_motion(*v))

# ---------- 3. FAIL-OPEN: non-translations must not be called motion ----------
print("\n[3] FAIL-OPEN — zoom/rotation are not translations, so they must NOT be dropped")
yy, xx = np.mgrid[0:vm.H, 0:vm.W]
zy = np.clip((yy - vm.H/2) * 0.85 + vm.H/2, 0, vm.H-1).astype(int)
zx = np.clip((xx - vm.W/2) * 0.85 + vm.W/2, 0, vm.W-1).astype(int)
v = vm.motion_verdict(base, base[zy, zx]); check("zoom -> STATIC (fail-open)", not vm.is_motion(*v))
v = vm.motion_verdict(base, base.T[:vm.H, :vm.W].copy() if base.shape[0] == base.shape[1] else np.rot90(base, 2)[:vm.H, :vm.W].copy())
check("180-degree rotation -> STATIC (fail-open)", not vm.is_motion(*v))

# ---------- 4. Below the raw floor ----------
print("\n[4] NOISE FLOOR — codec jitter must not register as anything")
v = vm.motion_verdict(base, base.copy()); check("identical frames -> STATIC", not vm.is_motion(*v))
v = vm.motion_verdict(base, np.clip(base + rng.normal(0, 1.0, base.shape), 0, 255).astype(np.float32))
check("tiny codec noise -> STATIC", not vm.is_motion(*v))

# ---------- 5. Thresholds are critical ----------
print("\n[5] THRESHOLDS critical — a wrong value must change the verdict")
# A NOISELESS synthetic pan has resid == 0.0 exactly, so "ratio <= 0.0" passes and asserts nothing.
# Use a realistic (noisy) pan, where ratio ~0.15, so the threshold is genuinely critical.
cur = np.clip(translate(base, 6, 0) + rng.normal(0, 4, base.shape), 0, 255).astype(np.float32)
v = vm.motion_verdict(base, cur)
assert vm.is_motion(*v), "fixture precondition: a realistic pan must be MOTION at default thresholds"
old = vm.RESID_MAX
vm.RESID_MAX = 0.05
check("tightening resid threshold suppresses a realistic pan", not vm.is_motion(*v),
      f"ratio={v[2]/max(v[3],1e-9):.3f}")
vm.RESID_MAX = old
old_s = vm.MIN_SHIFT; vm.MIN_SHIFT = 999
check("min-shift at 999 suppresses a true pan", not vm.is_motion(*v))
vm.MIN_SHIFT = old_s
check("restored thresholds re-fire the pan", vm.is_motion(*v))
old_a = vm.MAX_RESID_ABS; vm.MAX_RESID_ABS = 0.0
check("absolute-residual bound at 0.0 suppresses a realistic pan", not vm.is_motion(*v))
vm.MAX_RESID_ABS = old_a

# ---------- 6. Run-collapse semantics ----------
print("\n[6] RUN-COLLAPSE — keep the settle frame, never empty, first frame safe")
seq = [("a", False), ("b", True), ("c", True), ("d", True), ("e", False), ("f", True)]
vs = [(k, m, 0, 0, 0.0, 0.0) for k, m in seq]
keep = vm.collapse_runs(vs)
check("a motion run collapses to its LAST frame", keep == {"a", "d", "e", "f"}, f"got {sorted(keep)}")
check("never returns empty", len(vm.collapse_runs([("x", True)])) > 0)
allstatic = vm.collapse_runs([(k, False, 0, 0, 0.0, 0.0) for k in "xyz"])
check("all-static keeps everything", allstatic == {"x", "y", "z"})
first = vm.classify_sequence([("f0", base), ("f1", translate(base, 6, 0))])
check("first frame is never MOTION (no predecessor)", first[0][1] is False)

# ---------- 7. Determinism + unreadable handling ----------
print("\n[7] DETERMINISM and unreadable frames")
r1 = vm.motion_verdict(base, translate(base, 5, 3))
r2 = vm.motion_verdict(base, translate(base, 5, 3))
check("identical inputs -> identical verdict", r1 == r2)
seq = vm.classify_sequence([("a", base), ("b", None), ("c", translate(base, 6, 0))])
check("unreadable frame -> STATIC, never dropped", seq[1][1] is False)

print("\n" + "=" * 78)
if FAILED:
    print(f"FAILED ({len(FAILED)}): " + ", ".join(FAILED)); sys.exit(1)
print("ALL CHECKS PASSED — gate fires on translation, stays silent on new content, fails open.")
