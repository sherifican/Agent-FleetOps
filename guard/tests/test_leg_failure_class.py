"""Claude-authored gate for guard/leg_failure_class.py. The implementer must NOT edit this file.

Each rule is pinned to the real incident that motivated it, so a future edit that breaks one breaks a
test that names the cost.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from guard.leg_failure_class import OK, RETRYABLE, TERMINAL, classify, main  # noqa: E402


def c(**kw):
    base = dict(rc=0, stdout_bytes=0, stderr_bytes=0, artifact_bytes=0,
                elapsed_s=10.0, timeout_s=1500.0)
    base.update(kw)
    return classify(**base)


# --- rule 1: judge the ARTIFACT, never the exit code --------------------------------------------------

def test_good_artifact_is_OK_regardless_of_exit_code():
    """grok exits rc=2 on healthy runs — verified 2026-07-31 by a live canary probe."""
    assert c(rc=2, artifact_bytes=30000).outcome == OK
    assert c(rc=1, artifact_bytes=30000).outcome == OK
    assert c(rc=0, artifact_bytes=30000).outcome == OK


def test_good_artifact_wins_even_against_the_quota_signature():
    v = c(rc=1, stdout_bytes=0, stderr_bytes=0, elapsed_s=3.0, artifact_bytes=30000)
    assert v.outcome == OK


# --- rule 2: the kimi quota cap -----------------------------------------------------------------------

def test_quota_cap_signature_is_TERMINAL():
    """rc=1, empty stdout AND stderr, fails in seconds. Burned both attempts on 2026-07-31."""
    v = c(rc=1, stdout_bytes=0, stderr_bytes=0, elapsed_s=4.2)
    assert v.outcome == TERMINAL and v.signature == "quota-cap"


def test_quota_cap_reason_names_its_evidence():
    v = c(rc=1, stdout_bytes=0, stderr_bytes=0, elapsed_s=4.2)
    assert "stdout" in v.reason.lower() and ("4.2" in v.reason or "4" in v.reason)


def test_slow_silent_failure_is_NOT_the_quota_signature():
    """Fast-and-silent is the signature. Slow-and-silent is something else — do not over-claim."""
    v = c(rc=1, stdout_bytes=0, stderr_bytes=0, elapsed_s=400.0)
    assert v.signature != "quota-cap"


def test_silent_failure_with_stderr_is_not_quota_cap():
    v = c(rc=1, stdout_bytes=0, stderr_bytes=200, elapsed_s=4.0)
    assert v.signature != "quota-cap"


# --- rule 3: timeout ----------------------------------------------------------------------------------

def test_timeout_is_TERMINAL_because_a_retry_costs_another_full_window():
    v = c(rc=124, elapsed_s=1500.0, timeout_s=1500.0)
    assert v.outcome == TERMINAL and v.signature == "timeout"


def test_near_timeout_counts_as_timeout():
    v = c(rc=1, elapsed_s=1440.0, timeout_s=1500.0)   # 96%
    assert v.signature == "timeout"


def test_well_short_of_timeout_is_not_timeout():
    v = c(rc=1, stdout_bytes=50, elapsed_s=600.0, timeout_s=1500.0)
    assert v.signature != "timeout"


# --- rule 4: missing binary ---------------------------------------------------------------------------

def test_rc127_is_TERMINAL():
    """A missing binary never fixes itself — this is how grok sat dead for 15 days."""
    v = c(rc=127, stderr_bytes=60, elapsed_s=0.2)
    assert v.outcome == TERMINAL and v.signature == "no-such-command"


# --- rules 5/6: genuinely retryable -------------------------------------------------------------------

def test_short_artifact_is_RETRYABLE():
    v = c(rc=0, artifact_bytes=1200)
    assert v.outcome == RETRYABLE and v.signature == "short-artifact"


def test_failure_with_output_is_RETRYABLE():
    v = c(rc=1, stdout_bytes=4000, elapsed_s=200.0)
    assert v.outcome == RETRYABLE


# --- rule 7: the default must be the CHEAPER error -----------------------------------------------------

def test_unknown_state_defaults_to_RETRYABLE_not_TERMINAL():
    """Wrongly retrying costs one attempt. Wrongly failing over abandons a leg that might have
    worked. The cheaper error is the retry."""
    v = c(rc=3, stdout_bytes=0, stderr_bytes=0, elapsed_s=300.0)
    assert v.outcome == RETRYABLE


def test_unclassified_says_so_rather_than_hiding_the_gap():
    v = c(rc=3, stdout_bytes=0, stderr_bytes=0, elapsed_s=300.0)
    assert "unclassified" in (v.signature + v.reason).lower()


# --- robustness ---------------------------------------------------------------------------------------

def test_never_raises_on_absurd_input():
    base = dict(rc=1, stdout_bytes=0, stderr_bytes=0, artifact_bytes=0,
                elapsed_s=1.0, timeout_s=1500.0)
    for kw in (dict(rc=-5, elapsed_s=-1.0), dict(timeout_s=0.0), dict(artifact_bytes=-3),
               dict(elapsed_s=1e12, timeout_s=1e-9)):
        assert classify(**{**base, **kw}).outcome in (OK, RETRYABLE, TERMINAL)


def test_deterministic():
    a = c(rc=1, stdout_bytes=0, stderr_bytes=0, elapsed_s=4.0)
    b = c(rc=1, stdout_bytes=0, stderr_bytes=0, elapsed_s=4.0)
    assert (a.outcome, a.signature, a.reason) == (b.outcome, b.signature, b.reason)


def test_module_has_no_clock_or_network():
    import guard.leg_failure_class as m
    src = open(m.__file__, encoding="utf-8").read()
    for banned in ("time.time(", "datetime.now(", "requests.", "urllib.request", "subprocess"):
        assert banned not in src, f"{banned} — classify() takes measurements, it does not make them"


# --- the CLI contract the bash loop branches on -------------------------------------------------------

def test_cli_exit_codes_are_0_ok_1_retryable_2_terminal():
    ok = main(["--rc", "0", "--stdout-bytes", "0", "--stderr-bytes", "0",
               "--artifact-bytes", "30000", "--elapsed", "10", "--timeout", "1500"])
    retry = main(["--rc", "0", "--stdout-bytes", "0", "--stderr-bytes", "0",
                  "--artifact-bytes", "1200", "--elapsed", "10", "--timeout", "1500"])
    term = main(["--rc", "1", "--stdout-bytes", "0", "--stderr-bytes", "0",
                 "--artifact-bytes", "0", "--elapsed", "4", "--timeout", "1500"])
    assert (ok, retry, term) == (0, 1, 2)


def test_cli_prints_outcome_signature_and_reason(capsys):
    main(["--rc", "1", "--stdout-bytes", "0", "--stderr-bytes", "0",
          "--artifact-bytes", "0", "--elapsed", "4", "--timeout", "1500"])
    out = capsys.readouterr().out.strip()
    assert out.startswith("TERMINAL") and "quota-cap" in out and len(out) > 30


def test_bad_args_degrade_to_RETRYABLE_never_traceback(capsys):
    """A broken classifier must fall back to existing retry behaviour, not abandon the leg."""
    assert main(["--rc", "not-a-number"]) == 1
    assert main([]) == 1
