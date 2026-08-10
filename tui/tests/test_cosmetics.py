"""Tests for the cosmetics config source (safe load/save, defaults, merge, corruption-proof)."""
import json
from fleet_tui.sources import cosmetics


def test_load_defaults_when_missing(tmp_path):
    cfg = cosmetics.load(str(tmp_path / "nope.json"))
    assert cfg["enabled"] is True and cfg["spinner"] == "braille" and cfg["speed"] == "normal"
    assert cfg["cats"] == {"jobs": True, "coding": True, "health": True, "posture": True}
    assert cfg["colors"] == dict(cosmetics.COLOR_SLOTS)   # per-slot animation colors default


def test_colors_override_and_ignore_junk(tmp_path):
    p = tmp_path / "c.json"
    import json as _j
    p.write_text(_j.dumps({"colors": {"running": "magenta", "computing": 123, "bogus": "x"}}))
    cfg = cosmetics.load(str(p))
    assert cfg["colors"]["running"] == "magenta"          # valid string kept
    assert cfg["colors"]["computing"] == "cyan"        # non-string → default
    assert "bogus" not in cfg["colors"]                   # unknown slot ignored


def test_save_then_load_roundtrip(tmp_path):
    p = str(tmp_path / "cos.json")
    cfg = cosmetics.load(p)
    cfg["enabled"] = False
    cfg["spinner"] = "arc"
    cfg["speed"] = "lively"
    cfg["cats"]["health"] = False
    assert cosmetics.save(cfg, p) is True
    back = cosmetics.load(p)
    assert back["enabled"] is False and back["spinner"] == "arc" and back["speed"] == "lively"
    assert back["cats"]["health"] is False and back["cats"]["jobs"] is True


def test_load_merges_and_ignores_junk(tmp_path):
    p = tmp_path / "cos.json"
    # partial + wrong-typed + unknown-speed → each field falls back safely, defaults fill the rest
    p.write_text(json.dumps({"enabled": "yes", "speed": "warp", "spinner": "dots", "cats": {"jobs": False}}))
    cfg = cosmetics.load(str(p))
    assert cfg["enabled"] is True            # "yes" is not a bool → default
    assert cfg["speed"] == "normal"          # "warp" not a known speed → default
    assert cfg["spinner"] == "dots"          # valid string kept
    assert cfg["cats"]["jobs"] is False and cfg["cats"]["coding"] is True


def test_load_corrupt_file_safe(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{ not json ][")
    cfg = cosmetics.load(str(p))             # must not raise; returns defaults
    assert cfg == {**cosmetics.DEFAULTS, "cats": dict(cosmetics.DEFAULTS["cats"])}
