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
"""
import hashlib
import os
import sys


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


def main(argv):
    if len(argv) != 3:
        print("usage: verify_running_build_pid.py <pid> <deployed-file> <expected-sha256>")
        return 2
    rec = bind_check(int(argv[0]), argv[1], argv[2])
    print("%s: %s" % (rec["status"].upper(), rec["reason"]))
    return {"bound": 0, "not-bound": 1, "cannot-check": 2}[rec["status"]]


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
