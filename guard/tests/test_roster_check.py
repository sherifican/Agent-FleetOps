"""Gate for templates/roster-check.sh.template — the roster arm must fail when the
routing table names a model the live roster does not serve.

Red demo (standing): the fake-roster case below serves only one of two routed models and
the arm must go red naming the missing one. Mutation RK1 in guard/mutation_harness.py
blinds the missing-tag flag and this gate must go red.

Runs two ways: under pytest and standalone —
`python3 guard/tests/test_roster_check.py` — printing the all-pass marker the mutation
harness anchors on.
"""
import os
import subprocess
import sys
import tempfile

MARKER = "ROSTER CHECK HAS TEETH - ALL CHECKS PASSED"

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TEMPLATE = os.path.join(REPO, "templates", "roster-check.sh.template")


def _run(models, roster_cmd):
    with tempfile.TemporaryDirectory() as d:
        mf = os.path.join(d, "routing_models.txt")
        with open(mf, "w", encoding="utf-8") as fh:
            fh.write(models)
        env = dict(os.environ, ROUTING_MODELS_FILE=mf, ROSTER_CMD=roster_cmd)
        p = subprocess.run(["bash", TEMPLATE], capture_output=True, text=True, env=env)
    return p.returncode, p.stdout + p.stderr


def test_missing_routed_model_goes_red_and_is_named():
    rc, out = _run("model-alpha:7b\nmodel-beta:26b\n",
                   "printf 'model-alpha:7b\\n'")
    assert rc == 1, "a routed model absent from the roster must fail the arm:\n" + out
    assert "model-beta:26b" in out, "the missing tag must be NAMED:\n" + out


def test_full_roster_is_green():
    rc, out = _run("model-alpha:7b\nmodel-beta:26b\n",
                   "printf 'model-alpha:7b\\nmodel-beta:26b\\nextra-model:9b\\n'")
    assert rc == 0, "every routed model served must pass:\n" + out


def test_failed_roster_command_is_cannot_check():
    rc, out = _run("model-alpha:7b\n", "exit 3")
    assert rc == 2, "a failed roster lookup is not an empty roster — CANNOT CHECK:\n" + out


def test_empty_roster_output_is_cannot_check():
    rc, out = _run("model-alpha:7b\n", "true")
    assert rc == 2, "an empty answer cannot prove absence — CANNOT CHECK, never red:\n" + out


def test_unedited_template_refuses_to_run():
    p = subprocess.run(["bash", TEMPLATE], capture_output=True, text=True,
                       env={k: v for k, v in os.environ.items()
                            if k not in ("ROUTING_MODELS_FILE", "ROSTER_CMD")})
    assert p.returncode == 2, "placeholders unreplaced must refuse, not guess"


def test_comment_only_models_file_is_cannot_check():
    rc, out = _run("# no models yet\n", "printf 'model-alpha:7b\\n'")
    assert rc == 2, "a zero-model check proves nothing — it must be loud:\n" + out


ALL = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
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
