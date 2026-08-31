"""Gate for guard/run_guards.sh's step accounting — measured by running it, not by reading it.

The runner is what an adopter is told to run, so the property under test is what a stranger's
clone reports. Two outcomes that used to be one:

  NOT CONFIGURED — an optional integration nobody supplied. Nothing to run, nothing missing.
  UNMEASURED (2) — a check that WAS configured and could not run, so you do not know what it
                   would have said. Worse than a violation, on purpose.

Collapsing the first into the second made every pristine clone report worse-than-a-violation
forever, which carries exactly as much information as always reporting clean.

The runner runs the unit gates, so this file would recurse: GUARD_RUNNER_NESTED marks the inner
run and these tests step aside there.
"""
import os
import subprocess

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
NESTED = "GUARD_RUNNER_NESTED"
OPTIONAL = ("PASSBACK_OUTBOX", "PASSBACK_TEETH_TARGET", "SCRUB_OVERLAY", "SCRUB_PROFILE")


def _run_the_runner(extra_env=None):
    env = {k: v for k, v in os.environ.items() if k not in OPTIONAL}
    env[NESTED] = "1"
    env.update(extra_env or {})
    proc = subprocess.run(["bash", "guard/run_guards.sh"], cwd=REPO, env=env,
                          capture_output=True, text=True)
    return proc, proc.stdout + proc.stderr


def _summary(out):
    for line in reversed(out.splitlines()):
        if line.startswith("steps: "):
            return dict(part.split("=") for part in line[len("steps: "):].split())
    raise AssertionError("the runner printed no machine-readable step summary:\n" + out)


@pytest.mark.skipif(os.environ.get(NESTED) == "1", reason="inner run of the guard runner")
def test_unconfigured_optional_integrations_are_skipped_not_unmeasured():
    _, out = _run_the_runner()
    steps = _summary(out)
    assert steps["violations"] == "0", out
    assert steps["skipped"] == "2", (
        "an optional integration nobody supplied must be a skip, not UNMEASURED:\n" + out)
    # The one remaining UNMEASURED is the leg-liveness DRY RUN: a check that ran and declined to
    # assert, because no leg was probed. That distinction is the whole point of the split.
    assert steps["unmeasured"] == "1", out


@pytest.mark.skipif(os.environ.get(NESTED) == "1", reason="inner run of the guard runner")
def test_a_configured_check_that_cannot_run_is_still_unmeasured(tmp_path):
    """The other half: configuring the outbox means the passback check WAS asked for."""
    _, out = _run_the_runner({"PASSBACK_OUTBOX": str(tmp_path)})
    steps = _summary(out)
    assert steps["violations"] == "0", out
    assert steps["skipped"] == "1", out
    assert steps["unmeasured"] == "2", (
        "a configured check with nothing to compare must stay UNMEASURED:\n" + out)
