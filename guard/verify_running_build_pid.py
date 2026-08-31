#!/usr/bin/env python3
"""verify_running_build_pid.py — bind the deploy proof to the SERVING PID.

verify-running-build's first two checks are each blind to a third failure: Check 1 diffs
FILES and Check 2 proves A process restarted — nothing proves that the process which
restarted is serving the files that were diffed. A restart of the wrong unit passes
Check 2; a correct tree passes Check 1; together they still bless a serving PID that
loaded neither.

The concept, once: **resolve which file the serving PID actually loaded, then
content-hash that resolved file.** A path is identity, not bytes — a process serving
from the right path can still be running stale content it loaded before the copy landed.

Mechanism per platform:
  Linux   — `/proc/<pid>/exe`, `/proc/<pid>/cmdline`, `/proc/<pid>/cwd` resolve the
            interpreter and the script/config the process loaded.
  Windows — the process table's executable path for the PID.
  EVERY platform — the content hash of the resolved file, compared against the hash of
            the bytes you deployed. The hash is the binding; the path alone is not.

This module implements the Linux mechanism; on a platform without /proc it returns
CANNOT-CHECK (never a pass — an absent check must be loud).

Verdicts from bind_check(): status "bound" · "not-bound" · "cannot-check".
Exit codes (CLI): 0 bound · 1 not bound · 2 cannot check.
Gate: guard/tests/test_verify_running_build_pid.py — a marker file the serving PID never
loaded goes RED while the file-diff and uptime checks stay green.

`--selftest` runs the PATH-MISMATCH branch against a LIVE child process: it starts a
server, asks the binding whether that PID is serving a marker file the PID never loaded,
and requires red — with the same PID's real file as the green control, so the arm is shown
to discriminate rather than to refuse everything. On a host without /proc there is nothing
to exercise, and the selftest says so and returns 2 (CANNOT CHECK) rather than passing.
"""
import hashlib
import os
import subprocess
import sys
import tempfile
import time


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def pid_identity(pid):
    """The kernel's account of what the PID is running. None when /proc cannot answer."""
    base = "/proc/%d" % pid
    if not os.path.isdir(base):
        return None
    try:
        exe = os.readlink(os.path.join(base, "exe"))
        cwd = os.readlink(os.path.join(base, "cwd"))
        with open(os.path.join(base, "cmdline"), "rb") as fh:
            cmdline = [a.decode("utf-8", "replace") for a in fh.read().split(b"\0") if a]
    except OSError:
        return None
    return {"exe": exe, "cwd": cwd, "cmdline": cmdline}


def resolve_loaded_file(identity):
    """The file the process actually loaded: the first cmdline argument (after argv0 and
    interpreter flags) that resolves to an existing file, made absolute via the PID's own
    cwd — falling back to the executable itself. This is the RESOLVED identity; the
    caller's idea of the deployed path plays no part here, which is the point."""
    for arg in identity["cmdline"][1:]:
        if arg.startswith("-"):
            continue
        cand = arg if os.path.isabs(arg) else os.path.join(identity["cwd"], arg)
        if os.path.isfile(cand):
            return os.path.realpath(cand)
    return os.path.realpath(identity["exe"])


def bind_check(pid, deployed_path, expected_sha256):
    """Bind the serving PID to the deployed bytes. Returns a dict:
    {"status": "bound"|"not-bound"|"cannot-check", "reason": str,
     "resolved": path|None, "resolved_sha256": hex|None}"""
    identity = pid_identity(pid)
    if identity is None:
        return {"status": "cannot-check", "resolved": None, "resolved_sha256": None,
                "reason": "no /proc identity for pid %s — cannot bind here; an absent "
                          "check must be loud, never green" % pid}
    resolved = resolve_loaded_file(identity)
    try:
        resolved_sha = sha256_file(resolved)
    except OSError as exc:
        return {"status": "cannot-check", "resolved": resolved, "resolved_sha256": None,
                "reason": "resolved file unreadable: %s" % exc}
    if os.path.realpath(deployed_path) != resolved:
        return {"status": "not-bound", "resolved": resolved, "resolved_sha256": resolved_sha,
                "reason": "pid %d resolved %s, not the deployed file %s — the serving "
                          "process never loaded the file that was diffed"
                          % (pid, resolved, deployed_path)}
    if resolved_sha != expected_sha256:
        return {"status": "not-bound", "resolved": resolved, "resolved_sha256": resolved_sha,
                "reason": "path bound but bytes differ (%s… != expected %s…) — a path is "
                          "identity, not bytes" % (resolved_sha[:12], expected_sha256[:12])}
    return {"status": "bound", "resolved": resolved, "resolved_sha256": resolved_sha,
            "reason": "pid %d serves the deployed bytes" % pid}


def selftest():
    """Teeth against a live process: the PATH-MISMATCH branch must go RED on a marker file
    the serving PID never loaded, the same PID's real file must go GREEN (so the red is
    discriminating, not blanket), and a dead PID must be CANNOT CHECK."""
    if not os.path.isdir("/proc"):
        print("CANNOT CHECK — this reference implementation resolves the loaded file from "
              "/proc, and there is no /proc here. Nothing was exercised; an absent check "
              "must be loud, never green.")
        return 2
    failures = []
    with tempfile.TemporaryDirectory() as d:
        served = os.path.join(d, "served.py")
        with open(served, "w", encoding="utf-8") as fh:
            fh.write("import time\nwhile True:\n    time.sleep(0.2)\n")
        marker = os.path.join(d, "marker.py")
        with open(marker, "w", encoding="utf-8") as fh:
            fh.write("# a file this process never loaded\n")
        proc = subprocess.Popen([sys.executable, served], cwd=d)
        try:
            # Between fork and exec, /proc/<pid>/cmdline still shows the PARENT's argv, and
            # the resolver would honestly resolve this script. Waiting for the child's own
            # argv is a CONTROL, and its failure is a control failure, not a verdict.
            deadline = time.monotonic() + 10.0
            while time.monotonic() < deadline:
                ident = pid_identity(proc.pid)
                if ident and any(a.endswith("served.py") for a in ident["cmdline"]):
                    break
                time.sleep(0.01)
            else:
                print("  FAIL  the serving process never came up — CONTROL failure, not a "
                      "verdict; nothing was measured")
                return 2
            rec = bind_check(proc.pid, marker, sha256_file(marker))
            if rec["status"] != "not-bound":
                failures.append("a marker file the PID never loaded reported %r — the "
                                "path-mismatch branch did not fire" % rec["status"])
            elif "never loaded" not in rec["reason"] or served not in rec["reason"]:
                failures.append("the refusal must name what the PID actually resolved: %r"
                                % rec["reason"])
            rec = bind_check(proc.pid, served, sha256_file(served))
            if rec["status"] != "bound":
                failures.append("the PID's OWN file reported %r — a check that refuses "
                                "everything discriminates nothing: %s"
                                % (rec["status"], rec["reason"]))
        finally:
            proc.terminate()
            proc.wait(timeout=10)
        dead = bind_check(proc.pid, served, sha256_file(served))
        if dead["status"] != "cannot-check":
            failures.append("a dead PID reported %r — no identity to bind against must be "
                            "CANNOT CHECK, never a pass" % dead["status"])
    for f in failures:
        print("  FAIL  %s" % f)
    if failures:
        print("pid bind selftest: %d check(s) RED" % len(failures))
        return 1
    print("pid bind selftest: a marker file the live PID never loaded is REFUSED and the "
          "resolved file is named; the same PID's own file binds; a dead PID is CANNOT CHECK")
    return 0


def main(argv):
    if argv and argv[0] == "--selftest":
        return selftest()
    if len(argv) != 3:
        print("usage: verify_running_build_pid.py <pid> <deployed-file> <expected-sha256>")
        print("       verify_running_build_pid.py --selftest")
        return 2
    rec = bind_check(int(argv[0]), argv[1], argv[2])
    print("%s: %s" % (rec["status"].upper(), rec["reason"]))
    return {"bound": 0, "not-bound": 1, "cannot-check": 2}[rec["status"]]


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
