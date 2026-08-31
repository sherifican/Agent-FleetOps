"""Cosmetics config — pure load/save of the animation preferences the cosmetics menu drives.
Persisted to ~/.config/fleet_tui/cosmetics.json (same pattern as the theme). Safe-by-construction:
load() ALWAYS returns a complete, type-checked config (unknown/missing/corrupt → defaults), never raises.
"""
import json
import os

CONFIG_PATH = os.path.expanduser("~/.config/fleet_tui/cosmetics.json")

# spinner keys live in widgets/anim.SPINNERS; validation there falls back to braille on unknown, so this
# module only type-checks here (no import of anim → keeps this source framework-free + trivially testable).
SPEEDS = {"chill": 0.20, "normal": 0.12, "lively": 0.07}   # cosmetic-timer interval per speed
CATS = ("jobs", "coding", "health", "posture")   # per-panel animation gates (menu auto-lists these)

# per-slot animation colors the owner can recolor (slot -> default). The palette is what the menu cycles.
# All names are TEXTUAL (CSS/web) colors — NOT Rich 256-palette names like 'yellow3'/'gold1', which
# Textual can't resolve and render as blank/gray (owner-reported bug 2026-07-03).
COLOR_SLOTS = {
    # animated status text
    "running": "yellow", "dispatching": "gold", "computing": "cyan", "in_flight": "orange",
    # static labels (owner: recolor OK/fail/schedule/model labels). "model" = "family" keeps the per-family
    # scheme; any color overrides ALL model labels to that color.
    "ok": "green", "fail": "red", "schedule": "cyan", "model": "family",
    # attention color — the POSTURE alert/CRITICAL markup, its breathing ● attn chip, and the header
    # attention counter (⚠/partial/fb/pb) all route through this so it's recolorable in one place.
    "attn": "red",
}
PALETTE = ["yellow", "gold", "orange", "coral", "red", "deeppink", "magenta", "mediumpurple",
           "dodgerblue", "deepskyblue", "cyan", "springgreen", "green", "white"]

DEFAULTS = {
    "enabled": True,
    "spinner": "braille",
    "glow": True,
    "speed": "normal",
    "cats": {c: True for c in CATS},
    "colors": dict(COLOR_SLOTS),
}


def load(path: str = None) -> dict:
    """Return a complete cosmetics config, merging any saved values over the defaults. Never raises.
    `path=None` resolves CONFIG_PATH at CALL time (not import time) so tests can redirect it + never
    pollute the owner's real config."""
    path = path or CONFIG_PATH
    cfg = {**DEFAULTS, "cats": dict(DEFAULTS["cats"]), "colors": dict(DEFAULTS["colors"])}
    try:
        d = json.load(open(path))
        if isinstance(d, dict):
            if isinstance(d.get("colors"), dict):
                for slot in COLOR_SLOTS:
                    v = d["colors"].get(slot)
                    if isinstance(v, str) and v:
                        cfg["colors"][slot] = v
            if isinstance(d.get("enabled"), bool):
                cfg["enabled"] = d["enabled"]
            if isinstance(d.get("glow"), bool):
                cfg["glow"] = d["glow"]
            if isinstance(d.get("spinner"), str) and d["spinner"]:
                cfg["spinner"] = d["spinner"]            # anim.set_style validates + falls back
            if d.get("speed") in SPEEDS:
                cfg["speed"] = d["speed"]
            if isinstance(d.get("cats"), dict):
                for c in CATS:
                    if isinstance(d["cats"].get(c), bool):
                        cfg["cats"][c] = d["cats"][c]
    except Exception:
        pass
    return cfg


def save(cfg: dict, path: str = None) -> bool:
    """Persist the config. Returns True on success, False on any error (never raises). `path=None`
    resolves CONFIG_PATH at call time (see load)."""
    path = path or CONFIG_PATH
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(cfg, f, indent=2)
        return True
    except Exception:
        return False
