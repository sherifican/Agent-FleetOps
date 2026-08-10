"""Tests for the dispatch win-tracking log (append + per-target summary; corruption-proof)."""
from fleet_tui.sources import ratings


def test_rate_and_summary(tmp_path):
    p = str(tmp_path / "r.jsonl")
    assert ratings.summary(p) == {}                       # no file → empty
    ratings.rate("qwen3-coder", True, note="clean", speed_s=12, path=p)
    ratings.rate("qwen3-coder", True, speed_s=8, path=p)
    ratings.rate("qwen3-coder", False, note="missed an edge case", speed_s=20, path=p)
    ratings.rate("gen→audit", True, speed_s=140, path=p)

    s = ratings.summary(p)
    q = s["qwen3-coder"]
    assert q["up"] == 2 and q["down"] == 1 and q["n"] == 3
    assert q["win_rate"] == 67                             # 2/3
    assert q["avg_speed_s"] == 13                          # round((12+8+20)/3)
    assert q["last_note"] == "missed an edge case"
    assert s["gen→audit"]["win_rate"] == 100 and s["gen→audit"]["avg_speed_s"] == 140


def test_corrupt_and_missing_safe(tmp_path):
    p = tmp_path / "r.jsonl"
    p.write_text('{"target":"x","up":true,"speed_s":5}\nnot json\n{"target":"x","up":false}\n')
    s = ratings.summary(str(p))
    assert s["x"]["up"] == 1 and s["x"]["down"] == 1        # bad line skipped, good ones counted
    # rate to an unwritable path → False, never raises
    assert ratings.rate("x", True, path="/no/such/dir/r.jsonl") is False
