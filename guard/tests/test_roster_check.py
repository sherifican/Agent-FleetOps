"""Gate for templates/roster-check.sh.template — the roster arm must fail when the
routing table names a model the live roster does not serve, and it must read the ROUTING
TABLE to know what those models are.

The defect this holds shut: the arm trusted a separately maintained tag list and so never
read the document it claimed to police. Measured — a routing table naming `model-alpha:7b`
and `model-beta:26b`, a hand-kept list containing only alpha, and a roster serving only
alpha produced "roster check clean — every routed model (1 checked) is served".

Red demos (standing): the fake-roster case serves only one of two models NAMED IN THE
TABLE and the arm must go red naming the missing one; the drift case supplies a committed
list that no longer matches the table and the arm must refuse to compare. Mutation RK1
blinds the missing-tag flag and RK2 waives the committed-list equality check; both must
take this gate red.

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


TABLE = ("# Routing table\n"
         "| Task | Model |\n"
         "|---|---|\n"
         "| audit | `model-alpha:7b` |\n"
         "| draft | `model-beta:26b` |\n")
EXTRACT = "grep -o 'model-[a-z]*:[0-9]*b'"


def _run(roster_cmd, table=TABLE, extract=EXTRACT, committed=None):
    with tempfile.TemporaryDirectory() as d:
        tf = os.path.join(d, "ROUTING.md")
        with open(tf, "w", encoding="utf-8") as fh:
            fh.write(table)
        env = dict(os.environ, ROUTING_TABLE_FILE=tf, ROUTING_TAGS_CMD=extract,
                   ROSTER_CMD=roster_cmd)
        env.pop("ROUTING_MODELS_FILE", None)
        if committed is not None:
            mf = os.path.join(d, "routing_models.txt")
            with open(mf, "w", encoding="utf-8") as fh:
                fh.write(committed)
            env["ROUTING_MODELS_FILE"] = mf
        p = subprocess.run(["bash", TEMPLATE], capture_output=True, text=True, env=env)
    return p.returncode, p.stdout + p.stderr


def test_the_arm_reads_the_routing_table_not_a_separate_list():
    """The measured false green: a committed list naming only alpha, a roster serving only
    alpha, and a table that still names beta. With NO committed list at all the arm must
    still find beta, because the table is the subject of the check."""
    rc, out = _run("printf 'model-alpha:7b\\n'")
    assert rc == 1, ("the arm passed while the routing document named a model the roster "
                     "does not serve:\n" + out)
    assert "model-beta:26b" in out, "the missing tag must be NAMED:\n" + out


def test_a_committed_list_that_drifted_from_the_table_refuses_to_compare():
    """A stale hand-kept list is exactly what turned an absent model into a clean run. It
    must fail and name both sides, not silently win over the table."""
    rc, out = _run("printf 'model-alpha:7b\\nmodel-beta:26b\\n'",
                   committed="model-alpha:7b\n")
    assert rc == 1, "a committed list that no longer matches the table was accepted:\n" + out
    assert "drifted" in out and "model-beta:26b" in out, out


def test_a_committed_list_that_matches_the_table_is_accepted():
    rc, out = _run("printf 'model-alpha:7b\\nmodel-beta:26b\\n'",
                   committed="# routed models\nmodel-alpha:7b\nmodel-beta:26b\n")
    assert rc == 0, "a committed list equal to the derived set must pass:\n" + out


def test_missing_routed_model_goes_red_and_is_named():
    rc, out = _run("printf 'model-alpha:7b\\n'", committed="model-alpha:7b\nmodel-beta:26b\n")
    assert rc == 1, "a routed model absent from the roster must fail the arm:\n" + out
    assert "model-beta:26b" in out, "the missing tag must be NAMED:\n" + out


def test_full_roster_is_green():
    rc, out = _run("printf 'model-alpha:7b\\nmodel-beta:26b\\nextra-model:9b\\n'")
    assert rc == 0, "every routed model served must pass:\n" + out


def test_failed_roster_command_is_cannot_check():
    rc, out = _run("exit 3")
    assert rc == 2, "a failed roster lookup is not an empty roster — CANNOT CHECK:\n" + out


def test_empty_roster_output_is_cannot_check():
    rc, out = _run("true")
    assert rc == 2, "an empty answer cannot prove absence — CANNOT CHECK, never red:\n" + out


def test_an_extractor_that_finds_no_tags_is_cannot_check():
    """An extractor that quietly stops matching the table's format would otherwise report
    a clean roster forever — the never-fires half of the zero-information defect."""
    rc, out = _run("printf 'model-alpha:7b\\n'", extract="grep -o 'no-such-pattern'")
    assert rc == 2, "a zero-tag extraction must be CANNOT CHECK, never a pass:\n" + out


def test_a_missing_routing_document_is_cannot_check():
    with tempfile.TemporaryDirectory() as d:
        env = dict(os.environ, ROUTING_TABLE_FILE=os.path.join(d, "absent.md"),
                   ROUTING_TAGS_CMD=EXTRACT, ROSTER_CMD="printf 'model-alpha:7b\\n'")
        env.pop("ROUTING_MODELS_FILE", None)
        p = subprocess.run(["bash", TEMPLATE], capture_output=True, text=True, env=env)
    assert p.returncode == 2, "no routing document means nothing to police — CANNOT CHECK"


def test_unedited_template_refuses_to_run():
    p = subprocess.run(["bash", TEMPLATE], capture_output=True, text=True,
                       env={k: v for k, v in os.environ.items()
                            if k not in ("ROUTING_TABLE_FILE", "ROUTING_TAGS_CMD",
                                         "ROUTING_MODELS_FILE", "ROSTER_CMD")})
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
