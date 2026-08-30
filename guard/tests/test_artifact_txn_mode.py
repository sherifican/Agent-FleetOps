"""Gate for guard/artifact_txn.py mode preservation. The implementer must NOT edit this file.

A tmp+replace transaction hands the live path the TMP's mode: rewrite an executable through it and
the file silently loses `+x`. This gate rewrites targets at 0755 / 0644 / 0700 and asserts the mode
survives the commit. Red demo: mutation TX1 in guard/mutation_harness.py drops the chmod line and
this gate must go red — a gate that cannot fail is not a gate.

Runs two ways: under pytest (collected with the other unit gates) and standalone —
`python3 guard/tests/test_artifact_txn_mode.py` — printing the all-pass marker the mutation
harness anchors on.
"""
import os
import stat
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from guard.artifact_txn import Transaction  # noqa: E402

MARKER = "TXN MODE PRESERVED - ALL CHECKS PASSED"


def _rewrite_and_mode(mode):
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "tool.sh")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("#!/bin/sh\necho old\n")
        os.chmod(p, mode)
        with Transaction() as t:
            t.stage(p, "#!/bin/sh\necho new\n")
            t.commit()
        got = stat.S_IMODE(os.stat(p).st_mode)
        with open(p, encoding="utf-8") as fh:
            content = fh.read()
        return got, content


def test_0755_target_keeps_its_mode():
    got, content = _rewrite_and_mode(0o755)
    assert content == "#!/bin/sh\necho new\n", "the rewrite itself must land"
    assert got == 0o755, f"executable lost its mode: 0o755 -> {oct(got)}"


def test_0644_target_keeps_its_mode():
    got, _ = _rewrite_and_mode(0o644)
    assert got == 0o644, f"mode changed: 0o644 -> {oct(got)}"


def test_0700_target_keeps_its_mode():
    got, _ = _rewrite_and_mode(0o700)
    assert got == 0o700, f"mode changed: 0o700 -> {oct(got)}"


if __name__ == "__main__":
    failures = 0
    for fn in (test_0755_target_keeps_its_mode, test_0644_target_keeps_its_mode,
               test_0700_target_keeps_its_mode):
        try:
            fn()
            print(f"  ok    {fn.__name__}")
        except AssertionError as exc:
            failures += 1
            print(f"  FAIL  {fn.__name__}: {exc}")
    if failures:
        print(f"RESULT: {failures} check(s) RED")
        sys.exit(1)
    print(MARKER)
