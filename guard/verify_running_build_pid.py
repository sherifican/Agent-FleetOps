#!/usr/bin/env python3
"""verify_running_build_pid.py — check a deployed path against a serving PID.

verify-running-build's first two checks are each blind to a third failure: Check 1 diffs
FILES and Check 2 proves A process restarted — nothing proves that the process which
restarted is serving the files that were diffed. A restart of the wrong unit passes
Check 2; a correct tree passes Check 1; together they can still bless a serving PID whose
process-derived path differs from the deployed path.

This arm proves two narrower facts on Linux: the process-derived path is the deployed path,
and the file's current modification time predates the process start by more than an
uncertainty margin. Only after those facts hold does it compare the current disk hash with
the expected deployment hash.

It does not inspect the source bytes an interpreter read, derived bytecode it may execute,
or current process memory. Those are different objects. For a compiled binary, `/proc`'s
exe link and file-backed mappings can identify the running image, but this arm does not
perform that stronger mapping inspection.

Modification times are forgeable: archive extraction and timestamp-preserving copies can
present an older time. This ordering check catches accidental stale deploys and drift, not
an adversary. The implementation is Linux-only because it reads `/proc`; other platforms
return CANNOT-CHECK.

Verdicts: "bound" · "not-bound" · "cannot-prove" · "cannot-check".
Exit codes: 0 bound · 1 not bound · 2 cannot check · 3 cannot prove.
"""
import hashlib
import os
import subprocess
import sys
import tempfile
import time


def _derive_boot_epoch_once():
    """Return (epoch_ns_at_boot, sampling_uncertainty_ns), sampled once per run."""
    if not sys.platform.startswith("linux"):
        return None, None
    try:
        before = time.time_ns()
        since_boot = time.clock_gettime_ns(time.CLOCK_BOOTTIME)
        after = time.time_ns()
    except (AttributeError, OSError):
        return None, None
    return ((before + after) // 2 - since_boot, (after - before + 1) // 2)


_BOOT_EPOCH_NS, _BOOT_SAMPLE_UNCERTAINTY_NS = _derive_boot_epoch_once()
if not sys.platform.startswith("linux"):
    _CLOCK_TICKS = None
else:
    try:
        _CLOCK_TICKS = os.sysconf("SC_CLK_TCK")
    except (AttributeError, OSError, ValueError):
        _CLOCK_TICKS = None
_TICK_NS = ((1_000_000_000 + _CLOCK_TICKS - 1) // _CLOCK_TICKS
            if _CLOCK_TICKS else 0)
ORDERING_MARGIN_NS = max(20_000_000, _TICK_NS + (_BOOT_SAMPLE_UNCERTAINTY_NS or 0))


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def pid_identity(pid):
    """Linux kernel identity and start ticks. None when `/proc` cannot answer."""
    if not sys.platform.startswith("linux"):
        return None
    base = "/proc/%d" % pid
    if not os.path.isdir(base):
        return None
    try:
        exe = os.readlink(os.path.join(base, "exe"))
        cwd = os.readlink(os.path.join(base, "cwd"))
        with open(os.path.join(base, "cmdline"), "rb") as fh:
            cmdline = [a.decode("utf-8", "replace") for a in fh.read().split(b"\0") if a]
        with open(os.path.join(base, "stat"), encoding="ascii") as fh:
            stat_tail = fh.read().rsplit(")", 1)[1].split()
        start_ticks = int(stat_tail[19])
    except (IndexError, OSError, ValueError):
        return None
    return {"exe": exe, "cwd": cwd, "cmdline": cmdline,
            "start_ticks": start_ticks}


def process_start_time_ns(identity):
    """Convert one `/proc/<pid>/stat` start value with this run's boot reference."""
    if _BOOT_EPOCH_NS is None or not _CLOCK_TICKS:
        raise RuntimeError("Linux boot-time reference unavailable")
    return _BOOT_EPOCH_NS + identity["start_ticks"] * 1_000_000_000 // _CLOCK_TICKS


# Interpreter options that mean "the entry point is NOT a path in argv". With any of these
# present, every remaining argv item is program ARGUMENTS, and picking the first one that
# happens to exist on disk identifies a file the process never loaded.
#
# Measured 2026-09-19: `python3 -B -c "exec(open('real_server.py').read())" decoy.py` made this
# function return decoy.py, and bind_check then returned **bound** for decoy.py with its own real
# hash -- a file the process never executed. No forged timestamp was needed; decoy.py only had to
# be an ordinary already-deployed file older than process start. Control: the same call with a
# wrong hash returned not-bound, so the check was live. That is a guard failing in the direction
# that looks like success, which is the exact class this module exists to catch.
#
# Precision about the word "argv", added 2026-09-19 after a peer challenged the `-m` half:
# this module reads /proc/<pid>/cmdline, which is the EXEC-time argv and keeps the literal
# `-m pkg.worker`. It is not the in-process sys.argv, which cpython rewrites for `-m` so that
# sys.argv[0] becomes the module's full file path once the module has been located. Both are
# called "argv" and only one of them is visible from outside the process. Measured here:
# `python3 -m pkg.worker` gave cmdline ['python3', '-m', 'pkg.worker'], and 'pkg.worker' is a
# dotted module name that resolves to no file, so the pre-fix code fell through to
# /proc/<pid>/exe and would have hashed the INTERPRETER as the loaded file.
# So the claim this constant encodes is the narrow, checkable one -- from outside the process,
# for these two modes, cmdline carries no path to the entry point -- and not the broader
# "python cannot tell you which module ran", which is false.
_NO_SCRIPT_IN_ARGV = ("-c", "-m")


def resolve_loaded_file(identity):
    """Resolve the process-derived script path.

    Returns None when the launch mode means no argv item is the entry point; the caller must
    treat that as CANNOT-PROVE rather than guessing at a path.
    """
    for arg in identity["cmdline"][1:]:
        if arg in _NO_SCRIPT_IN_ARGV:
            return None
        # a clustered short-option group such as -Bc also selects one of those modes
        if arg.startswith("-") and not arg.startswith("--") and len(arg) > 1:
            if any(ch in arg[1:] for ch in ("c", "m")):
                return None
        if arg.startswith("-"):
            continue
        cand = arg if os.path.isabs(arg) else os.path.join(identity["cwd"], arg)
        if os.path.isfile(cand):
            return os.path.realpath(cand)
    return os.path.realpath(identity["exe"])


def bind_check(pid, deployed_path, expected_sha256):
    """Check process-derived path, modification ordering, then current disk hash."""
    identity = pid_identity(pid)
    if identity is None:
        return {"status": "cannot-check", "resolved": None, "resolved_sha256": None,
                "reason": "Linux /proc identity unavailable for pid %s; cannot check" % pid}
    resolved = resolve_loaded_file(identity)
    if resolved is None:
        return {"status": "cannot-prove", "resolved": None, "resolved_sha256": None,
                "reason": "pid %d was launched in a mode whose entry point is not a path in "
                          "/proc/<pid>/cmdline (-c / -m); no cmdline item may be treated as the "
                          "loaded file. "
                          "Identify the served source another way." % pid}
    deployed = os.path.realpath(deployed_path)
    if deployed != resolved:
        return {"status": "not-bound", "resolved": resolved, "resolved_sha256": None,
                "reason": "pid %d resolved path %s, not deployed path %s"
                          % (pid, resolved, deployed)}
    try:
        process_started_ns = process_start_time_ns(identity)
        before = os.stat(resolved)
    except (OSError, RuntimeError) as exc:
        return {"status": "cannot-check", "resolved": resolved,
                "resolved_sha256": None, "reason": "ordering evidence unavailable: %s" % exc}
    delta_ns = before.st_mtime_ns - process_started_ns
    if delta_ns >= -ORDERING_MARGIN_NS:
        relation = ("after process start" if delta_ns > ORDERING_MARGIN_NS
                    else "within the ordering margin of process start")
        return {"status": "cannot-prove", "resolved": resolved,
                "resolved_sha256": None, "process_started_ns": process_started_ns,
                "file_mtime_ns": before.st_mtime_ns,
                "ordering_margin_ns": ORDERING_MARGIN_NS,
                "reason": "file timestamp is %s; current disk bytes cannot establish "
                          "what the interpreter read" % relation}
    try:
        resolved_sha = sha256_file(resolved)
        after = os.stat(resolved)
    except OSError as exc:
        return {"status": "cannot-check", "resolved": resolved, "resolved_sha256": None,
                "reason": "resolved file unreadable: %s" % exc}
    if (before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_ino, after.st_size, after.st_mtime_ns):
        return {"status": "cannot-prove", "resolved": resolved,
                "resolved_sha256": resolved_sha,
                "reason": "file changed while it was hashed; ordering cannot be proved"}
    if resolved_sha != expected_sha256:
        return {"status": "not-bound", "resolved": resolved, "resolved_sha256": resolved_sha,
                "reason": "path and ordering hold, but current disk bytes differ "
                          "(%s… != expected %s…)"
                          % (resolved_sha[:12], expected_sha256[:12])}
    return {"status": "bound", "resolved": resolved, "resolved_sha256": resolved_sha,
            "process_started_ns": process_started_ns,
            "file_mtime_ns": before.st_mtime_ns,
            "ordering_margin_ns": ORDERING_MARGIN_NS,
            "reason": "pid %d resolved the deployed path; the file predates process "
                      "start beyond the margin; current disk hash matches" % pid}


def selftest():
    """Exercise bound, path mismatch, post-start change, and absent identity live."""
    if not sys.platform.startswith("linux") or not os.path.isdir("/proc"):
        print("CANNOT-CHECK — Linux /proc is unavailable; nothing was exercised")
        return 2
    failures = []
    with tempfile.TemporaryDirectory() as d:
        served = os.path.join(d, "served.py")
        with open(served, "w", encoding="utf-8") as fh:
            fh.write("import time\nwhile True:\n    time.sleep(0.2)\n")
        old_ns = time.time_ns() - 1_000_000_000
        os.utime(served, ns=(old_ns, old_ns))
        marker = os.path.join(d, "marker.py")
        with open(marker, "w", encoding="utf-8") as fh:
            fh.write("# path-mismatch control\n")
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
                failures.append("path mismatch reported %r" % rec["status"])
            rec = bind_check(proc.pid, served, sha256_file(served))
            if rec["status"] != "bound":
                failures.append("untouched path reported %r: %s"
                                % (rec["status"], rec["reason"]))
            time.sleep(max(0.05, ORDERING_MARGIN_NS / 1_000_000_000 + 0.01))
            with open(served, "w", encoding="utf-8") as fh:
                fh.write("DEPLOYED = 'new'\nimport time\nwhile True:\n    time.sleep(0.2)\n")
            rec = bind_check(proc.pid, served, sha256_file(served))
            if rec["status"] != "cannot-prove":
                failures.append("post-start overwrite reported %r" % rec["status"])
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
    print("pid path/order selftest: bound, not-bound, cannot-prove, and cannot-check exercised")
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
    return {"bound": 0, "not-bound": 1, "cannot-check": 2,
            "cannot-prove": 3}[rec["status"]]


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
