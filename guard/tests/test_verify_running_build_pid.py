"""Gate for guard/verify_running_build_pid.py — path and ordering must both have teeth.

The critical scenario starts a live interpreter from a script and then overwrites that
same path in place with the intended deployment bytes. The disk hash is green, but the
arm must return CANNOT-PROVE because the file changed after the process started. The gate
also keeps an untouched positive control, a path mismatch, and every abstention state.

Runs two ways: under pytest and standalone —
`python3 guard/tests/test_verify_running_build_pid.py` — printing the all-pass marker the
mutation harness anchors on. The stable wrong-hash case keeps mutation PB1 meaningful;
the post-start overwrite case goes red if the ordering check is removed.
"""
import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from guard.verify_running_build_pid import (  # noqa: E402
    bind_check, pid_identity, process_start_time_ns, selftest, sha256_file)

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
        # Positive controls need an unambiguous order rather than a timestamp in the
        # uncertainty margin around process start.
        old_ns = time.time_ns() - 1_000_000_000
        os.utime(self.served, ns=(old_ns, old_ns))
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


def test_different_deployed_path_is_not_bound():
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
        # Check 3 must go RED anyway: the process-derived path is not the deployed path.
        rec = bind_check(s.proc.pid, marker, sha256_file(marker))
        assert rec["status"] == "not-bound", (
            "file-diff green + uptime green, but the process-derived path differs and "
            "the path check still passed: %r" % rec)
        assert "resolved path" in rec["reason"]
        assert s.served in rec["reason"] and marker in rec["reason"]


def test_untouched_file_correct_path_is_bound():
    with _Serving() as s:
        rec = bind_check(s.proc.pid, s.served, sha256_file(s.served))
        assert rec["status"] == "bound", "untouched matching path must pass: %r" % rec


def test_in_place_overwrite_after_start_is_cannot_prove():
    """Disk now has the intended bytes, but the live interpreter started before them."""
    with _Serving() as s:
        time.sleep(0.05)
        intended = "DEPLOYED = 'new'\n" + SERVED_SRC
        with open(s.served, "w", encoding="utf-8") as fh:
            fh.write(intended)
        with open(s.served, encoding="utf-8") as fh:
            assert fh.read() == intended, "file-diff control broken: intended bytes absent"
        rec = bind_check(s.proc.pid, s.served, sha256_file(s.served))
        assert rec["status"] == "cannot-prove", (
            "post-start in-place overwrite must abstain even when disk now has the "
            "intended bytes: %r" % rec)


def test_stable_path_wrong_expected_hash_is_not_bound():
    with _Serving() as s:
        wrong = "0" * 64
        rec = bind_check(s.proc.pid, s.served, wrong)
        assert rec["status"] == "not-bound", (
            "path matched with differing bytes and the bind passed — a path is identity, "
            "not bytes: %r" % rec)
        assert "bytes" in rec["reason"]


def test_timestamp_within_start_margin_is_cannot_prove():
    with _Serving() as s:
        identity = pid_identity(s.proc.pid)
        assert identity is not None, "live child identity disappeared"
        start_ns = process_start_time_ns(identity)
        os.utime(s.served, ns=(start_ns, start_ns))
        rec = bind_check(s.proc.pid, s.served, sha256_file(s.served))
        assert rec["status"] == "cannot-prove", (
            "timestamp at process start must abstain instead of guessing an order: %r" % rec)
        assert "margin" in rec["reason"]


def test_dead_pid_is_cannot_check_never_green():
    with _Serving() as s:
        pass  # exited context: process terminated, dir gone
    rec = bind_check(2 ** 22 + 11, "/nonexistent", "0" * 64)
    assert rec["status"] == "cannot-check", "an unanswerable bind must be loud, not green: %r" % rec


def test_non_linux_import_skips_sysconf_and_returns_cannot_check():
    module_path = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                               "verify_running_build_pid.py")
    probe = "\n".join((
        "import hashlib, os, runpy, subprocess, sys, tempfile, time",
        "sys.platform = 'win32'",
        "del os.sysconf",
        "namespace = runpy.run_path(sys.argv[1])",
        "record = namespace['bind_check'](12345, 'unused', '0' * 64)",
        "print(record['status'])",
    ))
    result = subprocess.run([sys.executable, "-c", probe, module_path],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "cannot-check", result.stdout


def test_selftest_is_green():
    """The runner advertises "every proof-carrying tool must prove itself"; --selftest used
    to be a usage error (exit 2), so this arm had nothing to run."""
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = selftest()
    assert rc == 0, "selftest red:\n" + buf.getvalue()
    output = buf.getvalue().lower()
    for status in ("bound", "not-bound", "cannot-prove", "cannot-check"):
        assert status in output, "selftest did not expose %s:\n%s" % (status, output)


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
