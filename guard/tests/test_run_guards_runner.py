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
OPTIONAL = ("PASSBACK_OUTBOX", "PASSBACK_TEETH_TARGET", "SCRUB_OVERLAY", "SCRUB_PROFILE",
            "COMMS_ROOT", "RUN_MUTATION_HARNESS")


def _run_the_runner(extra_env=None):
    env = {k: v for k, v in os.environ.items() if k not in OPTIONAL}
    env[NESTED] = "1"
    env.update(extra_env or {})
    # The runner selects python3 from this PATH, independently of the parent pytest.
    # Measure that child's dependency before interpreting the runner's counters.
    unavailable = 'UNMEASURED: child pytest unavailable: '
    probe = subprocess.run(['python3', '-c',
        "import sys\ntry:\n    import pytest\nexcept ImportError as exc:\n"
        "    print(%r + type(exc).__name__ + ': ' + str(exc))\n    sys.exit(77)\n" % unavailable],
        cwd=REPO, env=env, capture_output=True, text=True, timeout=20)
    if probe.returncode == 77 and probe.stdout.startswith(unavailable):
        pytest.skip(probe.stdout.strip())
    assert probe.returncode == 0, (probe.returncode, probe.stdout, probe.stderr)
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
    assert steps["skipped"] == "4", (
        "an optional integration nobody supplied must be a skip, not UNMEASURED:\n" + out)
    # The one remaining UNMEASURED is the leg-liveness DRY RUN: a check that ran and declined to
    # assert, because no leg was probed. That distinction is the whole point of the split.
    assert steps["unmeasured"] == "1", out


@pytest.mark.skipif(os.environ.get(NESTED) == "1", reason="inner run of the guard runner")
def test_a_misfiled_comms_tree_reaches_the_runner_as_a_violation(tmp_path):
    """The wiring, observed end to end rather than inferred from a grep.

    A module-level unit test and an `rg` for the module's name can both pass while the runner never
    invokes the check. So this arm supplies a comms tree with an ANSWER parked in the ASK queue and
    reads the runner's own violation count, then moves the same file into replies/ as the control:
    one arm alone would not distinguish "the guard fired" from "the runner is always red".
    """
    root = tmp_path / "comms"
    for direction in ("outbound", "inbound"):
        for folder in ("requests", "replies"):
            (root / direction / folder).mkdir(parents=True, exist_ok=True)
    misfiled = root / "outbound" / "requests" / "REPLY_to_a_question.md"
    misfiled.write_text("an answer parked in the ask queue\n")

    _, out = _run_the_runner({"COMMS_ROOT": str(root)})
    assert _summary(out)["violations"] == "1", (
        "a misfiled answer must reach the runner as a violation:\n" + out)

    misfiled.rename(root / "outbound" / "replies" / "REPLY_to_a_question.md")
    _, out = _run_the_runner({"COMMS_ROOT": str(root)})
    assert _summary(out)["violations"] == "0", (
        "the same file, correctly filed, must leave the runner clean:\n" + out)


@pytest.mark.skipif(os.environ.get(NESTED) == "1", reason="inner run of the guard runner")
def test_a_configured_check_that_cannot_run_is_still_unmeasured(tmp_path):
    """The other half: configuring the outbox means the passback check WAS asked for."""
    _, out = _run_the_runner({"PASSBACK_OUTBOX": str(tmp_path)})
    steps = _summary(out)
    assert steps["violations"] == "0", out
    assert steps["skipped"] == "3", out
    assert steps["unmeasured"] == "2", (
        "a configured check with nothing to compare must stay UNMEASURED:\n" + out)


# =============================================================================================
# Runner execution failures and release-mode plumbing.
# Written BEFORE the fixes.
#
# Two instruments, both hermetic and both aimed at the ACTUAL entry point (`bash
# guard/run_guards.sh`) or at its ACTUAL aggregation code:
#
#   * The stubbed runner: a `python3` shim placed first on PATH logs every child invocation the
#     runner makes and exits 0 for all of them (benign controls — including the dry-run canary,
#     so aggregate 2 is attributable to the child under test, not to the runner's own
#     "LEG LIVENESS" step, whose `guard/leg_canary.py --dry-run` returns 2 by design).
#     One child can be made abnormal (exit 4, 127, ...), and the count step can be routed through
#     the A5 seam wrapper, which runs the real doc_count_drift.py `__main__` with only the pytest
#     collection faked as an ImportError. Each run takes well under a second.
#   * The isolated prologue: the runner's own text from the line after `cd ...` up to the first
#     `note "1.` step — counters, roll(), skip() and any helper — executed under bash with a
#     scripted sequence of roll calls. It is the real function, not a copy, so it cannot drift.
# =============================================================================================
import re  # noqa: E402
import sys  # noqa: E402

RUNNER = os.path.join(REPO, "guard", "run_guards.sh")
COUNT_SEAM = os.path.join(REPO, "guard", "tests", "fixtures", "count_measurement_seam.py")
ABNORMAL_STEP = "guard/contract_agreement.py"

SHIM = """#!/usr/bin/env bash
# python3 shim for guard/tests/test_run_guards_runner.py. Logs, stubs, injects.
printf '%s\\n' "$*" >> "$RUNNER_SHIM_LOG"
if [ -n "${RUNNER_ABNORMAL_STEP:-}" ] && [ "${1:-}" = "$RUNNER_ABNORMAL_STEP" ]; then
  exit "$RUNNER_ABNORMAL_RC"
fi
if [ -n "${COUNT_MEASUREMENT_SEAM:-}" ] && [ "${1:-}" = "guard/doc_count_drift.py" ] && [ "${2:-}" != "--selftest" ]; then
  shift
  exec "__REAL_PY__" "$COUNT_MEASUREMENT_SEAM" "$@"
fi
exit 0
"""


def _stubbed_runner(tmp_path, args=(), abnormal=None, count_seam=False, extra_env=None):
    """Run the real runner with every python3 child stubbed. Returns (proc, out, invocations)."""
    shim_dir = tmp_path / "shim"
    shim_dir.mkdir(exist_ok=True)
    shim = shim_dir / "python3"
    shim.write_text(SHIM.replace("__REAL_PY__", sys.executable))
    shim.chmod(0o755)
    log = tmp_path / "invocations.log"
    if log.exists():
        log.unlink()
    env = {k: v for k, v in os.environ.items() if k not in OPTIONAL}
    env[NESTED] = "1"
    env["PATH"] = str(shim_dir) + os.pathsep + env.get("PATH", "")
    env["RUNNER_SHIM_LOG"] = str(log)
    if abnormal:
        env["RUNNER_ABNORMAL_STEP"], env["RUNNER_ABNORMAL_RC"] = abnormal[0], str(abnormal[1])
    if count_seam:
        env["COUNT_MEASUREMENT_SEAM"] = COUNT_SEAM
    env.update(extra_env or {})
    proc = subprocess.run(["bash", "guard/run_guards.sh", *args], cwd=REPO, env=env,
                          capture_output=True, text=True)
    invocations = log.read_text().splitlines() if log.exists() else []
    return proc, proc.stdout + proc.stderr, invocations


def _prologue(runner=RUNNER):
    lines = open(runner, encoding="utf-8").read().splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith('cd "$(dirname')) + 1
    end = next(i for i, ln in enumerate(lines) if ln.startswith('note "1.'))
    return "\n".join(lines[start:end])


def _roll(calls, runner=RUNNER):
    """Execute the runner's real prologue, then `roll <rc> <step>` for each call.

    Returns (counters, diagnostics) where diagnostics is everything the prologue and the roll
    calls printed before the final counter line."""
    script = "set -uo pipefail\n" + _prologue(runner) + "\n"
    script += "".join(f"roll {rc} {step}\n" for rc, step in calls)
    script += ("printf 'RUNNER-ROLL worst=%s ok=%s violations=%s unmeasured=%s\\n' "
               "\"$worst\" \"$n_ok\" \"$n_violation\" \"$n_unmeasured\"\n")
    p = subprocess.run(["bash", "-c", script], cwd=REPO, capture_output=True, text=True)
    out = p.stdout + p.stderr
    m = re.search(r"RUNNER-ROLL worst=(\d+) ok=(\d+) violations=(\d+) unmeasured=(\d+)", out)
    assert m, "the prologue did not run to the counter line (rc=%d):\n%s" % (p.returncode, out)
    counters = dict(zip(("worst", "ok", "violations", "unmeasured"), map(int, m.groups())))
    return counters, out[:m.start()]


def _diagnostic_present(text, step, rc):
    return any("UNMEASURED" in ln and step in ln and re.search(r"(?<!\d)%d(?!\d)" % rc, ln)
               for ln in text.splitlines())


# --- A4 RED: isolated aggregation ------------------------------------------------------------

@pytest.mark.parametrize("rc", [4, 127])
def test_an_abnormal_child_status_increments_only_unmeasured_and_aggregates_to_2(rc):
    c, diag = _roll([(rc, ABNORMAL_STEP)])
    assert c["unmeasured"] == 1 and c["violations"] == 0 and c["ok"] == 0, (
        "exit %d is not a verdict; it must count as UNMEASURED only: %r" % (rc, c))
    assert c["worst"] == 2, "exit %d must aggregate to 2, got %r" % (rc, c)


@pytest.mark.parametrize("order", [[(1, "guard/a.py"), (4, "guard/b.py")],
                                   [(4, "guard/a.py"), (1, "guard/b.py")]])
def test_a_violation_mixed_with_an_abnormal_status_aggregates_to_2_in_either_order(order):
    c, _ = _roll(order)
    assert c["worst"] == 2, "sequence %r must aggregate to 2 (2 dominates 1): %r" % (order, c)
    assert c["violations"] == 1 and c["unmeasured"] == 1, order


@pytest.mark.parametrize("rc", [4, 127])
def test_roll_reports_the_step_name_and_raw_status_of_an_abnormal_child(rc):
    _, diag = _roll([(rc, ABNORMAL_STEP)])
    assert _diagnostic_present(diag, ABNORMAL_STEP, rc), (
        "no line names UNMEASURED, the step %r and the raw status %d:\n%r" % (ABNORMAL_STEP, rc, diag))


# --- A4 RED: the actual entry point -----------------------------------------------------------

@pytest.mark.skipif(os.environ.get(NESTED) == "1", reason="inner run of the guard runner")
@pytest.mark.parametrize("rc", [127, 4])
def test_the_runner_classifies_an_abnormal_child_as_unmeasured_and_names_it(tmp_path, rc):
    control, cout, cinv = _stubbed_runner(tmp_path)
    cs = _summary(cout)
    assert control.returncode == 0 and cs["violations"] == "0" and cs["unmeasured"] == "0", (
        "benign-control run must be clean (shim not in effect?):\n" + cout)
    assert ABNORMAL_STEP in cinv, "the control run never invoked the step under test: %r" % cinv

    proc, out, inv = _stubbed_runner(tmp_path, abnormal=(ABNORMAL_STEP, rc))
    s = _summary(out)
    assert s["violations"] == "0", "exit %d was counted as a violation:\n%s" % (rc, out)
    assert s["unmeasured"] == "1", "exit %d must count once as UNMEASURED:\n%s" % (rc, out)
    assert int(s["ok"]) == int(cs["ok"]) - 1 and s["skipped"] == cs["skipped"], (s, cs)
    assert proc.returncode == 2, "aggregate must be 2 with a benign canary, got %d:\n%s" % (
        proc.returncode, out)
    assert _diagnostic_present(out, ABNORMAL_STEP, rc), (
        "the runner output names neither the failing step nor the raw status %d:\n%s" % (rc, out))


# --- A4 CONTROL --------------------------------------------------------------------------------

def test_control_0_1_2_accounting_is_unchanged():
    assert _roll([(0, "s")])[0] == {"worst": 0, "ok": 1, "violations": 0, "unmeasured": 0}
    assert _roll([(1, "s")])[0] == {"worst": 1, "ok": 0, "violations": 1, "unmeasured": 0}
    assert _roll([(2, "s")])[0] == {"worst": 2, "ok": 0, "violations": 0, "unmeasured": 1}
    c, _ = _roll([(0, "a"), (1, "b"), (2, "c"), (0, "d")])
    assert c == {"worst": 2, "ok": 2, "violations": 1, "unmeasured": 1}, c


def test_control_subsequent_successes_cannot_erase_a_failure():
    assert _roll([(1, "a"), (0, "b"), (0, "c")])[0]["worst"] == 1
    assert _roll([(2, "a"), (0, "b"), (1, "c")])[0]["worst"] == 2
    assert _roll([(1, "a"), (2, "b"), (0, "c")])[0]["worst"] == 2


@pytest.mark.skipif(os.environ.get(NESTED) == "1", reason="inner run of the guard runner")
def test_control_stubbed_runner_keeps_skips_and_configured_missing_integrations(tmp_path):
    _, out, inv = _stubbed_runner(tmp_path)
    s = _summary(out)
    assert s["skipped"] == "4" and s["unmeasured"] == "0" and s["violations"] == "0", s
    assert "guard/leg_canary.py --dry-run" in inv, inv
    # a configured passback outbox with no teeth target: UNMEASURED via the runner's own `roll 2`
    _, out, _ = _stubbed_runner(tmp_path, extra_env={"PASSBACK_OUTBOX": str(tmp_path)})
    s = _summary(out)
    assert s["skipped"] == "3" and s["unmeasured"] == "1" and s["violations"] == "0", s


# --- A5 RED: release mode through the runner -------------------------------------------------

@pytest.mark.skipif(os.environ.get(NESTED) == "1", reason="inner run of the guard runner")
def test_the_runner_forwards_release_mode_and_the_count_step_is_unmeasured(tmp_path):
    """Benign canary, real count CLI with the combined instrument unavailable: the count step's
    OWN verdict must be UNMEASURED naming the affected banner claim, and the aggregate 2 must
    come from it."""
    proc, out, inv = _stubbed_runner(tmp_path, args=["--release"], count_seam=True)
    assert "guard/doc_count_drift.py --release" in inv, (
        "--release was not forwarded to the count checker; invocations: %r" % inv)
    assert re.search(r"UNMEASURED.*both hermetic suites", out), (
        "the count step did not report its own UNMEASURED verdict:\n" + out)
    assert "docs/banner.svg" in out, "the affected claim site is not named:\n" + out
    s = _summary(out)
    assert s["unmeasured"] == "1" and s["violations"] == "0", (s, out)
    assert proc.returncode == 2, "expected aggregate 2 from the count step alone, got %d:\n%s" % (
        proc.returncode, out)


@pytest.mark.skipif(os.environ.get(NESTED) == "1", reason="inner run of the guard runner")
@pytest.mark.parametrize("args", [["--with-canary", "--release"], ["--release", "--with-canary"]])
def test_release_and_canary_flags_are_accepted_in_either_order(tmp_path, args):
    proc, out, inv = _stubbed_runner(tmp_path, args=args)
    assert "guard/leg_canary.py" in inv and "guard/leg_canary.py --dry-run" not in inv, (
        "%r: the canary must run live, not dry: %r" % (args, inv))
    assert "guard/doc_count_drift.py --release" in inv, (
        "%r: --release not forwarded to the count checker: %r" % (args, inv))
    assert proc.returncode == 0 and _summary(out)["violations"] == "0", out


@pytest.mark.skipif(os.environ.get(NESTED) == "1", reason="inner run of the guard runner")
def test_the_runner_rejects_an_unknown_option(tmp_path):
    proc, out, inv = _stubbed_runner(tmp_path, args=["--relaese"])
    assert "unknown option" in out.lower(), "an unknown option was silently ignored:\n" + out
    assert proc.returncode == 2, "a refused run checked nothing: expected 2, got %d" % proc.returncode
    assert not any(ln.startswith("steps: ") for ln in out.splitlines()), (
        "a refused run must not report step accounting:\n" + out)
    assert inv == [], "a refused run must invoke no guard: %r" % inv


# --- A5 CONTROL --------------------------------------------------------------------------------

# =============================================================================================
# Earlier integration coverage. A4_enum gate gap: a roll() that treats ONLY 4 and
# 127 as UNMEASURED — every other abnormal status falling through to "worst=1, no counter" —
# passed the regression suite, whose abnormal statuses were exactly 4 and 127. The contract is
# "ANY status that is not 0/1/2"; a spread of them is pinned here, through the real prologue and
# once through the real entry point.
# =============================================================================================

OTHER_ABNORMAL_STATUSES = [3, 5, 126, 137, 139, 255]


@pytest.mark.parametrize("rc", OTHER_ABNORMAL_STATUSES)
def test_enum_any_abnormal_status_is_unmeasured_not_a_violation(rc):
    c, diag = _roll([(rc, ABNORMAL_STEP)])
    assert c["unmeasured"] == 1 and c["violations"] == 0 and c["ok"] == 0, (
        "exit %d is not a verdict; it must count once as UNMEASURED and never as a violation: %r"
        % (rc, c))
    assert c["worst"] == 2, "exit %d must aggregate to 2, got %r" % (rc, c)
    assert _diagnostic_present(diag, ABNORMAL_STEP, rc), (
        "no line names UNMEASURED, the step %r and the raw status %d:\n%r" % (ABNORMAL_STEP, rc, diag))


def test_enum_an_abnormal_status_is_never_erased_by_later_successes():
    for rc in OTHER_ABNORMAL_STATUSES:
        c, _ = _roll([(rc, "guard/a.py"), (0, "guard/b.py"), (0, "guard/c.py")])
        assert c["worst"] == 2 and c["unmeasured"] == 1 and c["violations"] == 0, (rc, c)


@pytest.mark.skipif(os.environ.get(NESTED) == "1", reason="inner run of the guard runner")
@pytest.mark.parametrize("rc", [137, 3])
def test_enum_the_runner_classifies_other_abnormal_statuses_as_unmeasured(tmp_path, rc):
    proc, out, inv = _stubbed_runner(tmp_path, abnormal=(ABNORMAL_STEP, rc))
    assert ABNORMAL_STEP in inv, "the run never invoked the step under test: %r" % inv
    s = _summary(out)
    assert s["violations"] == "0", "exit %d was counted as a violation:\n%s" % (rc, out)
    assert s["unmeasured"] == "1", "exit %d must count once as UNMEASURED:\n%s" % (rc, out)
    assert proc.returncode == 2, "aggregate must be 2 with a benign canary, got %d:\n%s" % (
        proc.returncode, out)
    assert _diagnostic_present(out, ABNORMAL_STEP, rc), (
        "the runner output names neither the failing step nor the raw status %d:\n%s" % (rc, out))


@pytest.mark.skipif(os.environ.get(NESTED) == "1", reason="inner run of the guard runner")
def test_control_ordinary_mode_count_step_skips_the_unavailable_suite(tmp_path):
    """Without --release the same seam yields the live receipt's shape: a green count step that
    says it skipped the suite it could not collect, and no release flag on the invocation."""
    proc, out, inv = _stubbed_runner(tmp_path, count_seam=True)
    assert "guard/doc_count_drift.py" in inv and not any(
        ln.startswith("guard/doc_count_drift.py --release") for ln in inv), inv
    assert re.search(r"skipped\s+both hermetic suites", out), out
    s = _summary(out)
    assert s["violations"] == "0" and s["unmeasured"] == "0", (s, out)
    assert proc.returncode == 0, out
