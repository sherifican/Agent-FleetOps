"""Gate for guard/verify_running_build_pid.py — the PID binding must be able to go red.

The scenario the arm exists for: a marker file the serving PID never loaded reads green on
the file-diff check (its content matches what was deployed) and green on the uptime check
(the process is fresh) — and the bind must still go RED, because the PID resolved a
different file. And on the same path, matching identity with differing bytes must go RED:
a path is identity, not bytes.

Runs two ways: under pytest and standalone —
`python3 guard/tests/test_verify_running_build_pid.py` — printing the all-pass marker the
mutation harness anchors on. Red demo: mutation PB1 drops the content-hash comparison and
this gate must go red.
"""
import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from guard.verify_running_build_pid import (  # noqa: E402
    bind_check, pid_identity, selftest, sha256_file)

MARKER = "PID BIND HAS TEETH - ALL CHECKS PASSED"
HAVE_PROC = os.path.isdir("/proc")

try:
    import pytest
    pytestmark = pytest.mark.skipif(not HAVE_PROC, reason="no /proc — Linux mechanism only")
except ImportError:  # standalone run
    pytest = None

SERVED_SRC = "import time\nwhile True:\n    time.sleep(0.2)\n"


class _Serving:
    def __enter__(self):
        self.dir = tempfile.TemporaryDirectory()
        self.served = os.path.join(self.dir.name, "served.py")
        with open(self.served, "w", encoding="utf-8") as fh:
            fh.write(SERVED_SRC)
        self.proc = subprocess.Popen([sys.executable, self.served], cwd=self.dir.name)
        self.started = time.monotonic()
        # Wait until the CHILD's own argv is visible: between fork and exec, /proc/<pid>/cmdline
        # still shows the parent's argv, and the resolver would honestly resolve the parent's
        # script — a control failure, not a gate verdict. Proven live: ~40% flake rate without this.
        deadline = self.started + 10.0
        while time.monotonic() < deadline:
            ident = pid_identity(self.proc.pid)
            if ident and any(a.endswith("served.py") for a in ident["cmdline"]):
                break
            time.sleep(0.01)
        else:
            raise AssertionError("serving process never came up — control failure")
        return self

    def __exit__(self, *exc):
        self.proc.terminate()
        self.proc.wait(timeout=10)
        self.dir.cleanup()


def test_marker_file_the_pid_never_loaded_goes_red():
    with _Serving() as s:
        marker = os.path.join(s.dir.name, "deployed_marker.py")
        new_content = "VERSION = 'new'\n"
        with open(marker, "w", encoding="utf-8") as fh:
            fh.write(new_content)
        # Check 1 (file-diff) is GREEN: the deployed marker file carries the new content.
        with open(marker, encoding="utf-8") as fh:
            assert fh.read() == new_content, "file-diff control broken: marker must read green"
        # Check 2 (uptime) is GREEN: the process is fresh.
        assert time.monotonic() - s.started < 60, "uptime control broken: process must be fresh"
        # Check 3 must go RED anyway: the serving PID never loaded the marker file.
        rec = bind_check(s.proc.pid, marker, sha256_file(marker))
        assert rec["status"] == "not-bound", (
            "file-diff green + uptime green, and the bind still passed a file the PID "
            "never loaded — the third check adds nothing: %r" % rec)
        assert "never loaded" in rec["reason"]


def test_bound_pid_and_bytes_is_green():
    with _Serving() as s:
        rec = bind_check(s.proc.pid, s.served, sha256_file(s.served))
        assert rec["status"] == "bound", "true binding must pass: %r" % rec


def test_right_path_wrong_bytes_goes_red():
    with _Serving() as s:
        wrong = "0" * 64
        rec = bind_check(s.proc.pid, s.served, wrong)
        assert rec["status"] == "not-bound", (
            "path matched with differing bytes and the bind passed — a path is identity, "
            "not bytes: %r" % rec)
        assert "bytes" in rec["reason"]


def test_dead_pid_is_cannot_check_never_green():
    with _Serving() as s:
        pass  # exited context: process terminated, dir gone
    rec = bind_check(2 ** 22 + 11, "/nonexistent", "0" * 64)
    assert rec["status"] == "cannot-check", "an unanswerable bind must be loud, not green: %r" % rec


def test_selftest_is_green():
    """The runner advertises "every proof-carrying tool must prove itself"; --selftest used
    to be a usage error (exit 2), so this arm had nothing to run."""
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = selftest()
    assert rc == 0, "selftest red:\n" + buf.getvalue()


ALL = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    if not HAVE_PROC:
        print("CANNOT CHECK — no /proc on this platform; the Linux mechanism was not exercised")
        sys.exit(2)
    failures = 0
    for fn in ALL:
        try:
            fn()
            print("  ok    %s" % fn.__name__)
        except AssertionError as exc:
            failures += 1
            print("  FAIL  %s: %s" % (fn.__name__, exc))
    if failures:
        print("RESULT: %d check(s) RED" % failures)
        sys.exit(1)
    print(MARKER)
