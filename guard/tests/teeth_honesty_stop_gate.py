"""Runnable teeth proof for guard/honesty_stop_gate.py, in the shape the mutation harness runs.

The harness executes a guard as `python3 <path>` with no arguments, so the module's `--self-test`
flag is not reachable from there. This wrapper is that entry point; the nine pinned cases live in
the module, where the runner also reaches them.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from guard import honesty_stop_gate  # noqa: E402

MARKER = "HONESTY STOP GATE HAS TEETH - ALL CHECKS PASSED"


def main():
    rc = honesty_stop_gate.self_test()
    print(MARKER if rc == 0 else "HONESTY STOP GATE IS NOT SOUND — do not rely on it")
    return rc


if __name__ == "__main__":
    sys.exit(main())
