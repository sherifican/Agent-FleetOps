"""Claude-authored gate for guard/leg_canary.py. The implementer must NOT edit this file.

Every test injects a fake runner: the module must be fully provable with zero cloud spend, or it will
not be run often enough to matter. The headline case is the rc=0-but-dead leg.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from guard.leg_canary import (  # noqa: E402
    CANARY_TOKEN,
    LEGS,
    Leg,
    Probe,
    load_state,
    main,
    probe,
    probe_all,
    save_state,
    stale,
)

L = Leg("testleg", ["echo"])


def runner_returning(rc, text):
    def r(argv, prompt, timeout):
        return rc, text
    return r


def test_token_in_response_is_ALIVE():
    p = probe(L, runner=runner_returning(0, f"{CANARY_TOKEN}\n"))
    assert p.outcome == "ALIVE" and isinstance(p, Probe)


def test_token_match_is_whitespace_and_case_tolerant():
    for text in (f"  {CANARY_TOKEN}  ", CANARY_TOKEN.lower(), f"\n{CANARY_TOKEN}\n\n"):
        assert probe(L, runner=runner_returning(0, text)).outcome == "ALIVE"


# --- the rule: judge the artifact, never the exit code ------------------------------------------------

def test_rc_zero_with_no_token_is_DEAD():
    """A leg can exit 0 having written nothing. This is exactly how a queue reported 25/25 for 13."""
    p = probe(L, runner=runner_returning(0, ""))
    assert p.outcome == "DEAD", "rc=0 must not be mistaken for alive"


def test_rc_zero_with_wrong_content_is_DEAD():
    p = probe(L, runner=runner_returning(0, "I'm sorry, your quota is exhausted."))
    assert p.outcome == "DEAD"


def test_nonzero_rc_with_the_token_is_still_ALIVE():
    """A leg can exit non-zero having produced a good answer. The artifact is the verdict."""
    p = probe(L, runner=runner_returning(3, f"{CANARY_TOKEN}"))
    assert p.outcome == "ALIVE"


def test_rc_is_recorded_as_evidence_even_though_it_is_not_the_verdict():
    assert probe(L, runner=runner_returning(7, CANARY_TOKEN)).rc == 7


def test_the_kimi_quota_shape_is_DEAD_not_UNMEASURED():
    """kimi's cap presents as rc=1 with empty stdout AND empty stderr. We asked and got a wrong answer."""
    assert probe(L, runner=runner_returning(1, "")).outcome == "DEAD"


# --- DEAD vs UNMEASURED are different failures --------------------------------------------------------

def test_runner_raising_is_UNMEASURED_not_DEAD():
    def boom(argv, prompt, timeout):
        raise OSError("No such file or directory: 'grok-dispatch.sh'")
    p = probe(L, runner=boom)
    assert p.outcome == "UNMEASURED", "a broken probe is not a broken leg"


def test_unmeasured_evidence_names_the_reason():
    def boom(argv, prompt, timeout):
        raise FileNotFoundError("missing-binary")
    assert "missing-binary" in probe(L, runner=boom).evidence


def test_probe_never_raises():
    for bad in (lambda *a: (_ for _ in ()).throw(RuntimeError("x")),
                lambda *a: None,
                lambda *a: ("not", "a", "pair", "at", "all")):
        probe(L, runner=bad)


def test_dead_evidence_shows_what_came_back_instead():
    """A human must be able to tell a quota message from a crash without re-running anything."""
    p = probe(L, runner=runner_returning(1, "Error 429: rate limited, retry after 60s"))
    assert "429" in p.evidence


# --- probe_all ----------------------------------------------------------------------------------------

def test_probe_all_returns_one_probe_per_leg():
    legs = [Leg("a", ["x"]), Leg("b", ["y"])]
    ps = probe_all(legs, runner=runner_returning(0, CANARY_TOKEN))
    assert [p.leg for p in ps] == ["a", "b"]


def test_one_dead_leg_does_not_stop_the_others():
    legs = [Leg("a", ["x"]), Leg("b", ["y"])]

    def mixed(argv, prompt, timeout):
        return (0, "") if argv == ["x"] else (0, CANARY_TOKEN)
    outs = {p.leg: p.outcome for p in probe_all(legs, runner=mixed)}
    assert outs == {"a": "DEAD", "b": "ALIVE"}


# --- staleness ----------------------------------------------------------------------------------------

def test_a_leg_never_seen_alive_is_stale():
    """Absence of evidence is not evidence of health; a fresh state file is not a clean bill."""
    assert stale({}, [Leg("grok", ["x"])], now=100) == ["grok"]


def test_a_recently_alive_leg_is_not_stale():
    st = {"grok": {"last_alive_seq": 90}}
    assert stale(st, [Leg("grok", ["x"])], now=100, max_age_hours=26) == []


def test_the_fifteen_day_dead_leg_is_caught():
    """15 days = 360 hours. This is the incident that motivated the whole module."""
    st = {"grok": {"last_alive_seq": 0}}
    assert stale(st, [Leg("grok", ["x"])], now=360) == ["grok"]


def test_stale_boundary_is_26_hours_by_default():
    legs = [Leg("g", ["x"])]
    assert stale({"g": {"last_alive_seq": 75}}, legs, now=100) == []      # 25h old
    assert stale({"g": {"last_alive_seq": 73}}, legs, now=100) == ["g"]   # 27h old


def test_stale_returns_sorted_names():
    legs = [Leg(n, ["x"]) for n in ("zeta", "alpha", "mid")]
    assert stale({}, legs, now=10) == ["alpha", "mid", "zeta"]


def test_state_round_trips(tmp_path):
    p = str(tmp_path / "state.json")
    save_state(p, {"grok": {"last_alive_seq": 5}})
    assert load_state(p)["grok"]["last_alive_seq"] == 5


def test_load_state_of_a_missing_or_corrupt_file_is_empty_not_a_crash(tmp_path):
    assert load_state(str(tmp_path / "absent.json")) == {}
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert load_state(str(bad)) == {}


# --- state must never launder a failure ---------------------------------------------------------------

def test_a_dead_probe_never_updates_last_alive(tmp_path, monkeypatch):
    """Writing 'last alive' for a failed probe launders the failure into the health record."""
    p = str(tmp_path / "state.json")
    save_state(p, {"testleg": {"last_alive_seq": 10}})
    monkeypatch.setattr("guard.leg_canary.LEGS", [L])
    monkeypatch.setattr("guard.leg_canary._default_runner", runner_returning(0, ""))
    main(["--state", p, "--max-age-hours", "9999"])
    assert load_state(p).get("testleg", {}).get("last_alive_seq") == 10, "stale value must survive"


def test_an_alive_probe_updates_state(tmp_path, monkeypatch):
    p = str(tmp_path / "state.json")
    monkeypatch.setattr("guard.leg_canary.LEGS", [L])
    monkeypatch.setattr("guard.leg_canary._default_runner", runner_returning(0, CANARY_TOKEN))
    main(["--state", p])
    assert "testleg" in load_state(p)


# --- exit codes ---------------------------------------------------------------------------------------

def test_exit_zero_when_everything_alive(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("guard.leg_canary.LEGS", [L])
    monkeypatch.setattr("guard.leg_canary._default_runner", runner_returning(0, CANARY_TOKEN))
    rc = main(["--state", str(tmp_path / "s.json")])
    assert rc == 0
    assert "ALIVE" in capsys.readouterr().out


def test_exit_one_when_a_leg_is_dead(tmp_path, monkeypatch):
    monkeypatch.setattr("guard.leg_canary.LEGS", [L])
    monkeypatch.setattr("guard.leg_canary._default_runner", runner_returning(0, "nope"))
    assert main(["--state", str(tmp_path / "s.json")]) == 1


def test_exit_two_when_a_leg_is_unmeasured(tmp_path, monkeypatch):
    def boom(argv, prompt, timeout):
        raise OSError("gone")
    monkeypatch.setattr("guard.leg_canary.LEGS", [L])
    monkeypatch.setattr("guard.leg_canary._default_runner", boom)
    assert main(["--state", str(tmp_path / "s.json")]) == 2


def test_dry_run_spends_nothing_and_says_so(tmp_path, capsys):
    """A dry run must never be mistakable for a real probe.

    ⚠ CONTRACT CHANGED 2026-08-03, deliberately. This test previously asserted `rc == 0`, which
    CONTRADICTED its own docstring: returning 0 is precisely what makes a dry run mistakable for a real
    probe, and it is why `run_guards.sh` reported a clean leg-liveness pass for weeks while probing
    nothing. A dry run proves WIRING only, so it now returns 2 = UNMEASURED (which dominates 1 in this
    subsystem) and must not persist state — `_dry_runner` returns a hardcoded success, so writing it
    stamped every leg 'freshly alive' on fabricated evidence and the staleness check could never fire.
    This assertion was updated to match a deliberate contract change, not to turn a red test green.
    """
    state = tmp_path / "s.json"
    rc = main(["--state", str(state), "--dry-run"])
    out = capsys.readouterr().out.lower()
    assert rc == 2, "a dry run measured nothing — it must report UNMEASURED, never a pass"
    assert "dry" in out
    assert not state.exists(), "a FAKE probe must never write liveness state"


def test_real_legs_are_declared():
    names = {l.name for l in LEGS}
    assert {"kimi", "grok"} <= names, "the leg that died for 15 days must be covered"
    for l in LEGS:
        assert l.argv and isinstance(l.argv, list)


def test_module_has_no_network_calls_outside_the_default_runner():
    import guard.leg_canary as lc
    src = open(lc.__file__, encoding="utf-8").read()
    assert src.count("subprocess") <= 3, "subprocess use belongs only in the default runner"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
