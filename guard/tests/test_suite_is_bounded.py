"""The guard suite must finish in bounded time without dropping what it measures.

3857c81 ran past GitHub's 6-hour limit. Two re-entries multiplied the expensive fetch-gate teeth:

  * every planted-failure witness in test_fetch_gate_stored_values.py swapped ONLY its victim and
    then ran the real harness main(), which runs every other real teeth check after the planted
    one fails (~68 full main() runs per suite; one main() measured at ~64 s);
  * the four live runner tests each ran `bash guard/run_guards.sh`, whose pytest line re-ran the
    whole guard tree with no timeout.

Every test below except the two controls fails by assertion on 3857c81 (or, for the
bounded-runner test, by its own watchdog), never by a missing interface; the controls pass there
and must keep passing. Written BEFORE the fix.
"""
import importlib.util
import os
import pathlib
import threading
import time

import pytest

HERE = pathlib.Path(__file__).resolve().parent
NESTED = "GUARD_RUNNER_NESTED"
FULL_TREE = "-m pytest guard/tests/ -q"


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# =============================================================================================
# 1. A planted-failure witness tests the harness's dispatch, not the other teeth checks.
#    Every registered check is swapped for a recorder before the witness runs. A witness that
#    still lets main() walk the registered list executes those recorders; a witness that
#    replaces every slot with its own sentinels does not. The witness must still enter the
#    real main() exactly once, or it proves nothing about the entry point.
# =============================================================================================
WITNESSES = [
    ("test_a_failing_trailing_whitespace_check_makes_the_harness_entry_point_report_not_sound", None),
    ("test_a_failure_planted_in_any_trailing_whitespace_check_makes_the_harness_report_not_sound", 0),
    ("test_a_failure_planted_in_any_trailing_whitespace_check_makes_the_harness_report_not_sound", -1),
    ("test_stored_value_a_failure_planted_in_any_check_makes_the_harness_report_not_sound", 0),
    ("test_stored_value_a_failure_planted_in_any_check_makes_the_harness_report_not_sound", -1),
]
LISTS = ("VERDICT_ENVELOPE_CHECKS", "TRAILING_WHITESPACE_CHECKS", "STORED_VALUE_CHECKS")


@pytest.mark.parametrize("witness,idx", WITNESSES, ids=lambda v: str(v))
def test_a_planted_failure_witness_runs_no_registered_teeth_check(witness, idx):
    wrapper = _load("_bounded_fetch_wrapper", "test_fetch_gate_stored_values.py")
    mod = wrapper._mod
    executed, entered = [], []

    def recorder(name):
        def rec():
            executed.append(name)
        rec.__name__ = name
        return rec

    real_main = mod.main

    def counting_main(*a, **kw):
        entered.append(1)
        return real_main(*a, **kw)

    with pytest.MonkeyPatch.context() as mp:
        for lst in LISTS:
            mp.setattr(mod, lst, [recorder(fn.__name__) for fn in getattr(mod, lst)])
        mp.setattr(mod, "main", counting_main)
        fn = getattr(wrapper, witness)
        with pytest.MonkeyPatch.context() as witness_mp:
            if idx is None:
                fn(witness_mp)
            else:
                size = len(getattr(mod, "STORED_VALUE_CHECKS" if "stored_value" in witness
                                   else "TRAILING_WHITESPACE_CHECKS"))
                fn(witness_mp, idx % size)
    assert entered == [1], "the witness must enter the real harness main() exactly once, got %d" % len(entered)
    assert executed == [], (
        "the witness let main() execute %d registered teeth checks (first: %s); each planted index "
        "then re-runs the whole teeth corpus" % (len(executed), executed[:3]))


def test_control_the_untouched_harness_still_runs_every_registered_check():
    """The all-pass control must stay on the REAL lists: it is the one full main() a suite keeps."""
    wrapper = _load("_bounded_fetch_wrapper_ctl", "test_fetch_gate_stored_values.py")
    mod = wrapper._mod
    executed = []

    def recorder(name):
        def rec():
            executed.append(name)
        rec.__name__ = name
        return rec

    with pytest.MonkeyPatch.context() as mp:
        names = []
        for lst in LISTS:
            recs = [recorder(fn.__name__) for fn in getattr(mod, lst)]
            names += [r.__name__ for r in recs]
            mp.setattr(mod, lst, recs)
        wrapper.test_control_the_untouched_harness_reports_all_pass()
    assert executed == names, "the untouched control must run every registered check once, in order"


# =============================================================================================
# 2. The runner's nested pytest line is narrowed only when the test marks the run as nested,
#    says so out loud, and runs the full tree otherwise. Measured with the real runner and the
#    existing python3 shim, which logs every child's argv.
# =============================================================================================
def _runner_module():
    return _load("_bounded_runner_tests", "test_run_guards_runner.py")


def _pytest_lines(invocations):
    return [ln for ln in invocations if ln.startswith("-m pytest")]


@pytest.mark.skipif(os.environ.get(NESTED) == "1", reason="inner run of the guard runner")
def test_a_nested_runner_does_not_rerun_the_whole_guard_tree(tmp_path):
    proc, out, inv = _runner_module()._stubbed_runner(tmp_path)
    lines = _pytest_lines(inv)
    assert lines, "the nested runner started no pytest at all:\n" + out
    assert FULL_TREE not in lines, (
        "under %s=1 the runner re-ran the whole guard tree (%r); the live runner tests run it four "
        "times per suite" % (NESTED, FULL_TREE))
    assert any("narrowed" in ln and NESTED in ln for ln in out.splitlines()), (
        "a narrowed nested pytest must announce itself on one output line naming %s and the word "
        "'narrowed':\n%s" % (NESTED, out))


@pytest.mark.skipif(os.environ.get(NESTED) == "1", reason="inner run of the guard runner")
def test_control_an_unmarked_runner_still_runs_the_whole_guard_tree(tmp_path):
    proc, out, inv = _runner_module()._stubbed_runner(tmp_path, extra_env={NESTED: ""})
    assert FULL_TREE in _pytest_lines(inv), (
        "the adopter path (no %s) must run %r:\n%s" % (NESTED, FULL_TREE, out))
    assert not any("narrowed" in ln for ln in out.splitlines()), out


# =============================================================================================
# 3. A live runner call is bounded and leaves no process behind. A planted run_guards.sh starts a
#    long sleeper in the same process tree and waits on it. The helper must give up at its
#    deadline, fail the test naming the runner and the timeout, and kill the whole group.
# =============================================================================================
HANG = """#!/usr/bin/env bash
echo $$ > "$PLANT_DIR/parent.pid"
sleep 300 &
echo $! > "$PLANT_DIR/child.pid"
wait
"""


def _alive(pid):
    try:
        with open("/proc/%d/stat" % pid, "rb") as fh:
            return fh.read().rsplit(b")", 1)[1].split()[0] != b"Z"
    except OSError:
        return False
    except (ValueError, IndexError):
        # The pid entry exists; an unreadable survivor must not look like successful cleanup.
        return True


def _kill(pid):
    for sig in (15, 9):
        try:
            os.kill(pid, sig)
        except OSError:
            pass


@pytest.mark.skipif(os.environ.get(NESTED) == "1", reason="inner run of the guard runner")
def test_a_hung_live_runner_call_fails_at_its_deadline_and_kills_its_process_group(tmp_path):
    runner = _runner_module()
    fake_repo = tmp_path / "repo"
    (fake_repo / "guard").mkdir(parents=True)
    script = fake_repo / "guard" / "run_guards.sh"
    script.write_text(HANG)
    script.chmod(0o755)
    plant = tmp_path / "plant"
    plant.mkdir()
    result = {}

    def call():
        try:
            result["ret"] = runner._run_the_runner({"PLANT_DIR": str(plant)})
        except BaseException as exc:  # pytest.fail raises a BaseException subclass
            result["exc"] = exc

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(runner, "REPO", str(fake_repo))
        mp.setattr(runner, "RUNNER_TIMEOUT_S", 2, raising=False)
        t = threading.Thread(target=call, daemon=True)
        t.start()
        t.join(30)
    pids = []
    for name in ("parent.pid", "child.pid"):
        p = plant / name
        if p.exists():
            pids.append(int(p.read_text().strip()))
    hung = t.is_alive()
    if hung:
        for pid in pids:
            _kill(pid)
        t.join(10)
    assert not hung, "a hung run_guards.sh kept the live runner call blocked past 30 s: no deadline"
    assert len(pids) == 2, "the planted runner did not start (pids %r): %r" % (pids, result)
    assert "exc" in result, "a runner that hit its deadline must fail the test, got %r" % (result,)
    msg = str(result["exc"])
    assert "run_guards.sh" in msg and "TIMEOUT" in msg.upper(), (
        "the failure must name the runner and the timeout: %r" % msg)
    deadline = time.monotonic() + 5
    while any(_alive(p) for p in pids) and time.monotonic() < deadline:
        time.sleep(0.1)
    survivors = [p for p in pids if _alive(p)]
    for pid in survivors:
        _kill(pid)
    assert not survivors, "the timeout left the runner's process tree alive: %r" % survivors


# Survivor checks must handle process names that are arbitrary kernel bytes.
def test_alive_reads_a_non_utf8_process_name_and_zombie_state(tmp_path):
    import select
    import subprocess
    import sys

    if not pathlib.Path("/proc/self/stat").is_file():
        pytest.skip("/proc process stat is unavailable (requires Linux)")
    child_code = r"""
import ctypes
import sys
import time
libc = ctypes.CDLL(None)
prctl = getattr(libc, "prctl", None)
if prctl is None or prctl(15, ctypes.c_char_p(b"\xff\xfechild)"), 0, 0, 0) != 0:
    sys.exit(77)
print("named", flush=True)
time.sleep(30)
"""
    child = subprocess.Popen([sys.executable, "-c", child_code],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        assert select.select([child.stdout], [], [], 5)[0], "child did not signal its name"
        ready = child.stdout.readline()
        if not ready and child.wait(timeout=5) == 77:
            pytest.skip("prctl(PR_SET_NAME) is unavailable")
        assert ready == b"named\n", child.stderr.read()
        assert _alive(child.pid) is True
        child.kill()
        # Wait for the exit without reaping: the zombie keeps its non-UTF-8 name in /proc/<pid>/stat,
        # so only a bytes parse reaches the `Z` state; a text parse raises and reports it alive.
        os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOWAIT)
        assert pathlib.Path("/proc/%d/stat" % child.pid).is_file()
        assert _alive(child.pid) is False
        child.wait(timeout=5)
        assert _alive(child.pid) is False
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=5)
        child.stdout.close()
        child.stderr.close()


@pytest.mark.parametrize("content", [b"", b"truncated", b"name)"])
def test_alive_keeps_an_existing_unparseable_stat_alive(monkeypatch, content):
    import io

    monkeypatch.setattr("builtins.open", lambda *a, **kw: io.BytesIO(content))
    assert _alive(123) is True
