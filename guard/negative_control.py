#!/usr/bin/env python3
"""negative_control.py — prove the runner can still fail, and for the RIGHT reason.

A committed, deliberately broken config must read as broken on every run. If it ever reads
clean, every green above it is a light wired to nothing.

★ WHY THE ACCEPTANCE TEST IS THIS NARROW. The first version accepted the step if the command
exited non-zero AND the token appeared anywhere in its combined output. Both halves are
satisfiable by accident: a crash that merely PRINTS the config it just read carries the token,
and so does `echo <token>; exit 13`. Measured — both were accepted as "the runner can fail".
A negative control that accepts any failure is not a control; it is a second way to pass.

So the verdict requires all three, together:
  * the gate's DOCUMENTED exit code for a bad config (1) — not merely non-zero,
  * the marker line the gate prints when it rejects a config,
  * the gate's own sentence about THIS token, which only its config validation can produce.

A crash, an import error, a missing interpreter, an environment problem: each is non-zero, none
of them produces those three. That failure is UNMEASURED (2) — the control did not run — and is
reported as such rather than being counted as proof of teeth.

Exit codes: 0 the control behaved · 1 it did not (the runner has lost the ability to fail)
· 2 the control itself could not be run.
"""
import argparse
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
GATE = os.path.join(HERE, "honesty_stop_gate.py")
FIXTURE = os.path.join(HERE, "tests", "fixtures", "honesty_gate.config.broken.json")

# Named VERIFIER_NAME rather than VERIFIER_NAME: this is the NAME of a verification command that must
# fail to resolve, not a credential — and a constant called VERIFIER_NAME assigned a quoted string is
# what a secret scanner is built to flag, so the old name tripped this repo's own publish gate.
VERIFIER_NAME = "nonexistent-verifier-9f3a"
EXPECTED_RC = 1                      # the gate's documented "this config is bad" exit
MARKER = "check-config: PROBLEMS"    # printed only by the config validation's reject path
REASON = re.compile(r"verification command '%s' does not resolve" % re.escape(VERIFIER_NAME))


def verdict(rc, out):
    """(accepted, reason) for one run of the gate against the broken fixture."""
    if rc != EXPECTED_RC:
        return False, ("exit %s, but a rejected config exits %s here. A non-zero for some other "
                       "reason is not evidence the check fired." % (rc, EXPECTED_RC))
    if MARKER not in out:
        return False, "exit %s without the %r line — that verdict came from somewhere else." % (
            rc, MARKER)
    if not REASON.search(out):
        return False, ("the config was rejected, but not for the fixture's own planted reason. "
                       "Echoing the token is not detecting it.")
    return True, "rejected with exit %s, the marker line, and its own named reason" % rc


def run(config, gate=GATE):
    env = dict(os.environ, HONESTY_GATE_CONFIG=config)
    try:
        proc = subprocess.run([sys.executable, gate, "--check-config"], env=env,
                              capture_output=True, text=True)
    except OSError as exc:
        return None, str(exc)
    return proc.returncode, proc.stdout + proc.stderr


def main(argv=None):
    ap = argparse.ArgumentParser(description="the runner's standing negative control")
    ap.add_argument("--config", default=FIXTURE, help="the deliberately broken config")
    args = ap.parse_args(argv)

    rc, out = run(args.config)
    if rc is None:
        print("2 UNMEASURED — the control could not be run: %s" % out)
        return 2
    accepted, why = verdict(rc, out)
    if accepted:
        print("   broken fixture detected for its own named reason (%s) — the runner can fail" % why)
        return 0
    print("   ⛔ the broken fixture was NOT detected for its OWN reason: %s" % why)
    print("   The runner has lost the ability to fail; every green above it means nothing.")
    for line in out.splitlines():
        print("   | %s" % line)
    return 1


if __name__ == "__main__":
    sys.exit(main())
