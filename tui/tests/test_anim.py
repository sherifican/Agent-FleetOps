"""Tests for the cosmetic animation helpers (pure — frame in, markup out; no framework/IO)."""
from fleet_tui.widgets import anim


def test_spin_cycles():
    anim.set_style("braille", True)                     # known baseline
    n = len(anim._active_frames)
    frames = [anim.spin(i) for i in range(n)]
    assert len(set(frames)) == n                        # every frame distinct
    assert anim.spin(0) == anim.spin(n)                 # wraps around


def test_rich_catalog_loaded():
    # our custom set is present AND Rich's bundled spinners folded in → a big catalog for the menu
    for k in ("braille", "arc", "bar"):
        assert k in anim.SPINNERS
    assert len(anim.SPINNERS) > 40                       # ~5 custom + ~70 from Rich
    assert anim.SPINNER_KEYS[0] == "braille"             # stable, custom-first ordering
    # every entry is a non-empty list of frame strings
    assert all(isinstance(v, list) and v for v in anim.SPINNERS.values())


def test_set_style():
    anim.set_style("arc", True)
    assert anim._active_frames == anim.SPINNERS["arc"]
    anim.set_style("nonexistent", True)                 # unknown → safe fallback to braille
    assert anim._active_frames == anim.SPINNERS["braille"]
    # glow off → breathe returns no style prefix (spinner still cycles)
    anim.set_style("braille", False)
    assert anim.breathe(0) == "" and anim.breathe(3) == ""
    anim.set_style("braille", True)                     # restore default for other tests


def test_set_colors():
    anim.set_colors({"running": "magenta", "computing": "cyan"})
    assert anim.color("running", "yellow") == "magenta"    # overridden
    assert anim.color("computing", "gold") == "cyan"
    assert anim.color("dispatching", "gold") == "gold"  # unset → default
    anim.set_colors({})                                     # reset
    assert anim.color("running", "yellow") == "yellow"


def test_breathe_and_glow():
    # breathe returns a valid style prefix that cycles bold/normal/dim
    styles = {anim.breathe(i) for i in range(8)}
    assert styles <= {"b ", "", "dim "}
    # glow wraps text in the color + breathe style, and is balanced markup
    g = anim.glow("running", "yellow", 0)
    assert "yellow" in g and g.endswith("[/]") and "running" in g


def test_active_has_spinner_and_verb():
    a = anim.active("dispatching", "cyan", 3)
    assert anim.spin(3) in a and "dispatching" in a and "cyan" in a
    assert a.count("[") == a.count("]")                 # balanced markup, no stray brackets
