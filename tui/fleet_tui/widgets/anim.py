"""Cosmetic animation helpers — pure functions that turn a frame counter into a spinner glyph + a
'breathing' glow style. NO framework deps, NO subprocess, NO I/O. The app drives these from a fast
(~8fps) cosmetic timer that ONLY repaints markup (never re-gathers data), so they cost ~nothing and
never touch the "don't spawn nvidia-smi in the refresh loop" crash rule.

Design: a spinner glyph cycles every frame; the glow pulses on a SLOWER cycle (bold→normal→dim→normal)
so the two aren't in lockstep and it reads as organic 'breathing'. Glow uses only style modifiers
(b / dim) on the caller's color, so it never risks an unknown Rich color-shade name.
"""

# Spinner registry: name -> list of frame STRINGS (a frame may be multi-char, e.g. Rich's 'bouncingBar').
# The hand-picked ones come first, then Rich's bundled catalog (~70 more) is folded in — Rich is already a
# dependency, so this is a big, SAFE expansion (no fragile external plugin; cf. the textual-terminal miss).
_CUSTOM = {
    "braille":    "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏",   # the classic CLI spinner
    "dots-heavy": "⣾⣽⣻⢿⡿⣟⣯⣷",     # heavier braille orbit
    "arc":        "◜◠◝◞◡◟",         # rotating arc
    "bounce":     "⠁⠂⠄⡀⢀⠠⠐⠈",     # a single dot bouncing round
    "bar":        "▁▃▄▅▆▇▆▅▄▃",     # a pulsing vertical bar
}
SPINNERS = {k: list(v) for k, v in _CUSTOM.items()}
try:                                                     # fold in Rich's bundled cli-spinners catalog
    from rich._spinners import SPINNERS as _RICH_SPINNERS
    for _name, _spec in _RICH_SPINNERS.items():
        if _name in SPINNERS:
            continue
        _frames = _spec.get("frames") if isinstance(_spec, dict) else None
        if _frames:
            SPINNERS[_name] = list(_frames)
except Exception:                                        # Rich private module moved → keep the custom set
    pass

SPINNER_KEYS = list(SPINNERS.keys())                     # stable order for the cosmetics-menu browser
_BREATHE = ["b ", "", "dim ", ""]   # bold → normal → dim → normal (a soft pulse)

_active_frames = SPINNERS["braille"]  # mutated by set_style() from the cosmetics config
_glow_on = True
_colors = {}                          # slot -> color, set by set_colors() from the cosmetics config


def set_style(spinner: str = "braille", glow: bool = True) -> None:
    """Point the animation at a spinner style + glow on/off (driven by the cosmetics menu)."""
    global _active_frames, _glow_on
    _active_frames = SPINNERS.get(spinner) or SPINNERS["braille"]
    _glow_on = bool(glow)


def set_colors(colors: dict) -> None:
    """Override the per-slot animation colors (running/dispatching/computing/in_flight) from the cosmetics menu."""
    global _colors
    _colors = dict(colors or {})


def color(slot: str, default: str) -> str:
    """The configured color for an animation slot, or `default` if unset."""
    return _colors.get(slot) or default


def spin(frame: int) -> str:
    """The spinner frame for this tick (from the active style). May be multi-char (Rich spinners)."""
    return _active_frames[frame % len(_active_frames)]


def breathe(frame: int) -> str:
    """The style prefix (bold/dim) for this frame — slower than the spinner so they don't lockstep.
    Returns '' when glow is off (spinner still cycles, but no bold/dim pulse)."""
    if not _glow_on:
        return ""
    return _BREATHE[(frame // 2) % len(_BREATHE)]


def glow(text: str, color: str, frame: int) -> str:
    """`text` in `color`, pulsing bold↔dim by frame. Rich markup (Textual Static renders it)."""
    return f"[{breathe(frame)}{color}]{text}[/]"


def active(verb: str, color: str, frame: int) -> str:
    """A pulsing spinner + status verb, e.g. '⠙ dispatching…' — the CLI 'Thinking…' vibe."""
    return glow(f"{spin(frame)} {verb}", color, frame)
