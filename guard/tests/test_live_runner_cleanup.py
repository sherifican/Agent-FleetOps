"""A live runner call cleans up after itself on every exit path, and its nested pytest step counts.

The bounded helper in test_run_guards_runner.py stops the runner's process group when the deadline
passes. Review of that helper found three exits it did not cover and one counter nothing checked:

  * an interrupt (Ctrl-C, a pytest-timeout) raised while the call waits left the runner's group
    alive, and start_new_session keeps a terminal Ctrl-C from reaching that group;
  * a child that called setsid and kept the captured pipes open blocked the helper's last read past
    its deadline, so the helper never failed;
  * a normal exit left a background child of the runner alive;
  * dropping the `roll` after the nested (narrowed) pytest step passed every test.

Written BEFORE the fix. Tests 1-3 failed on the helper as first written; test 4 failed on a
runner whose nested step dropped its `roll`; test 5 failed on checks that accepted any non-empty
marker, compared it as a number, matched it as a prefix, or also accepted `true`.
"""
import importlib.util
import os
import pathlib
import subprocess
import threading
import time

import pytest

HERE = pathlib.Path(__file__).resolve().parent
NESTED = "GUARD_RUNNER_NESTED"
pytestmark = pytest.mark.skipif(os.environ.get(NESTED) == "1", reason="inner run of the guard runner")


def _runner_module():
    spec = importlib.util.spec_from_file_location("_cleanup_runner_tests", HERE / "test_run_guards_runner.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _alive(pid):
    try:
        with open("/proc/%d/stat" % pid) as fh:
            return fh.read().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return False


def _kill(pid):
    for sig in (15, 9):
        try:
            os.kill(pid, sig)
        except OSError:
            pass


def _plant(tmp_path, body):
    fake_repo = tmp_path / "repo"
    (fake_repo / "guard").mkdir(parents=True)
    script = fake_repo / "guard" / "run_guards.sh"
    script.write_text("#!/usr/bin/env bash\n" + body)
    script.chmod(0o755)
    plant = tmp_path / "plant"
    plant.mkdir()
    return fake_repo, plant


def _pids(plant, names):
    out = []
    for name in names:
        p = plant / name
        if p.exists() and p.read_text().strip():
            out.append(int(p.read_text().strip()))
    return out


def _wait_dead(pids, secs=5):
    deadline = time.monotonic() + secs
    while any(_alive(p) for p in pids) and time.monotonic() < deadline:
        time.sleep(0.1)
    return [p for p in pids if _alive(p)]


def _wait_for(path, secs=10):
    deadline = time.monotonic() + secs
    while not (path.exists() and path.read_text().strip()) and time.monotonic() < deadline:
        time.sleep(0.05)
    return path.exists()


# =============================================================================================
# 1. An interrupt while the helper waits stops the runner's whole group, then propagates.
# =============================================================================================
HANG = """echo $$ > "$PLANT_DIR/parent.pid"
sleep 300 &
echo $! > "$PLANT_DIR/child.pid"
wait
"""


def test_an_interrupt_during_a_live_runner_call_stops_its_process_group(tmp_path, monkeypatch):
    runner = _runner_module()
    fake_repo, plant = _plant(tmp_path, HANG)
    real_popen = subprocess.Popen

    class InterruptingPopen(real_popen):
        def communicate(self, *a, **kw):
            _wait_for(plant / "child.pid")
            raise KeyboardInterrupt("planted interrupt")

    monkeypatch.setattr(runner, "REPO", str(fake_repo))
    monkeypatch.setattr(runner.subprocess, "Popen", InterruptingPopen)
    env = dict(os.environ, PLANT_DIR=str(plant))
    raised = None
    try:
        runner._bounded_runner((), env, 60)
    except KeyboardInterrupt as exc:
        raised = exc
    pids = _pids(plant, ("parent.pid", "child.pid"))
    survivors = _wait_dead(pids)
    for pid in survivors:
        _kill(pid)
    assert len(pids) == 2, "the planted runner did not start: %r" % pids
    assert raised is not None, "the interrupt must propagate, not be swallowed"
    assert not survivors, "an interrupt left the runner's process group alive: %r" % survivors


# =============================================================================================
# 2. A setsid child holding the captured pipes cannot hold the helper past its deadline.
# =============================================================================================
SETSID_HOLDER = """echo $$ > "$PLANT_DIR/parent.pid"
setsid sleep 300 &
echo $! > "$PLANT_DIR/child.pid"
wait
"""


def test_a_setsid_child_holding_the_pipes_does_not_block_the_deadline(tmp_path):
    runner = _runner_module()
    fake_repo, plant = _plant(tmp_path, SETSID_HOLDER)
    result = {}

    def call():
        try:
            result["ret"] = runner._bounded_runner((), dict(os.environ, PLANT_DIR=str(plant)), 2)
        except BaseException as exc:  # pytest.fail raises a BaseException subclass
            result["exc"] = exc

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(runner, "REPO", str(fake_repo))
        t = threading.Thread(target=call, daemon=True)
        t.start()
        t.join(20)
    blocked = t.is_alive()
    pids = _pids(plant, ("parent.pid", "child.pid"))
    for pid in pids:
        _kill(pid)  # the setsid child left the group; nothing in the helper can reach it
    t.join(10)
    assert len(pids) == 2, "the planted runner did not start: %r" % pids
    assert not blocked, "a setsid child holding the pipes kept the helper blocked past 20 s"
    assert "exc" in result, "a runner that hit its deadline must fail the test, got %r" % (result,)
    msg = str(result["exc"])
    assert "run_guards.sh" in msg and "TIMEOUT" in msg.upper(), msg


# =============================================================================================
# 3. A normal exit does not leave a background child of the runner alive.
# =============================================================================================
LEAVES_CHILD = """sleep 300 >/dev/null 2>&1 </dev/null &
echo $! > "$PLANT_DIR/child.pid"
exit 0
"""


def test_a_runner_that_exits_normally_leaves_no_child_in_its_group(tmp_path, monkeypatch):
    runner = _runner_module()
    fake_repo, plant = _plant(tmp_path, LEAVES_CHILD)
    monkeypatch.setattr(runner, "REPO", str(fake_repo))
    proc = runner._bounded_runner((), dict(os.environ, PLANT_DIR=str(plant)), 30)
    pids = _pids(plant, ("child.pid",))
    survivors = _wait_dead(pids)
    for pid in survivors:
        _kill(pid)
    assert proc.returncode == 0, proc
    assert len(pids) == 1, "the planted runner did not record its child: %r" % pids
    assert not survivors, "a normal exit left the runner's background child alive: %r" % survivors


# =============================================================================================
# 4. The nested (narrowed) pytest step's exit status is counted. Under the stubbed runner the
#    narrowed seam pytest is the only `-m` child, so making `-m` exit 1 must add one violation.
# =============================================================================================
def test_a_failing_narrowed_pytest_step_is_counted_as_a_violation(tmp_path):
    runner = _runner_module()
    for sub in ("control", "failing"):
        (tmp_path / sub).mkdir()
    control, cout, cinv = runner._stubbed_runner(tmp_path / "control")
    pytest_children = [ln for ln in cinv if ln.startswith("-m")]
    assert pytest_children == ["-m pytest guard/tests/test_runner_nested_seam.py -q"], pytest_children
    cs = runner._summary(cout)
    assert cs["violations"] == "0", cout

    proc, out, _ = runner._stubbed_runner(tmp_path / "failing", abnormal=("-m", 1))
    s = runner._summary(out)
    assert s["violations"] == "1", "a failing narrowed pytest step was not counted:\n" + out
    assert int(s["ok"]) == int(cs["ok"]) - 1, (s, cs)
    assert proc.returncode == 1, out



# =============================================================================================
# 5. Only the exact value `1` narrows the nested pytest step. Any other non-empty value is an
#    adopter's or a typo's, and must still run the whole guard tree.
# =============================================================================================
# Values a numeric (-eq 1), prefix (1*) or truthy comparison would wrongly accept.
NEAR_MISSES = ("yes", "true", "01", " 1", "1 ", "+1", "10", "1yes")


def test_a_marker_other_than_exactly_1_still_runs_the_whole_guard_tree(tmp_path):
    runner = _runner_module()
    for i, value in enumerate(NEAR_MISSES):
        work = tmp_path / ("v%d" % i)
        work.mkdir()
        _, out, inv = runner._stubbed_runner(work, extra_env={NESTED: value})
        pytest_children = [ln for ln in inv if ln.startswith("-m pytest")]
        assert "-m pytest guard/tests/ -q" in pytest_children, (
            "GUARD_RUNNER_NESTED=%r narrowed the pytest step:\n%s" % (value, out))
        assert not any("narrowed" in ln for ln in out.splitlines()), (value, out)
