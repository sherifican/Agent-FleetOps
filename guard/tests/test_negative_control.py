"""Gate for guard/negative_control.py — the runner's proof that it can still fail.

The acceptance test this replaces was: exit != 0 AND the token appears anywhere in the combined
output. Every case below marked IMPOSTOR was measured passing that test. A control that any
crash can satisfy is a second way to pass, not a control.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from guard import negative_control as nc  # noqa: E402

REAL = ("check-config: PROBLEMS\n"
        "  ✗ verification command '%s' does not resolve on this box (stair to nowhere — it "
        "would read as coverage and verify nothing)\n"
        "Fix these before trusting the gate: drop unresolved commands, fill empty lists.\n"
        % nc.VERIFIER_NAME)


def test_the_real_rejection_is_accepted():
    accepted, why = nc.verdict(1, REAL)
    assert accepted, why


def test_impostor_that_only_echoes_the_token_is_rejected():
    accepted, why = nc.verdict(13, nc.VERIFIER_NAME + "\n")
    assert not accepted, "a bare echo of the token was accepted as proof of teeth: " + why


def test_impostor_that_prints_the_config_it_read_is_rejected():
    with open(nc.FIXTURE, encoding="utf-8") as fh:
        crash = "Traceback (most recent call last):\n" + fh.read() + "\nKeyError: 'subjects'\n"
    assert nc.VERIFIER_NAME in crash, "the fixture must carry the token for this case to mean anything"
    accepted, why = nc.verdict(9, crash)
    assert not accepted, "a crash that echoed the config was accepted: " + why


def test_a_rejection_for_some_other_reason_is_rejected():
    other = ("check-config: PROBLEMS\n"
             "  ✗ required list 'subjects' is empty — the gate would be disabled\n")
    accepted, why = nc.verdict(1, other)
    assert not accepted, "the documented exit code alone was treated as proof: " + why


def test_a_clean_read_of_the_broken_fixture_is_rejected():
    accepted, why = nc.verdict(0, REAL)
    assert not accepted, why


def test_the_committed_fixture_still_reads_as_broken_end_to_end():
    rc, out = nc.run(nc.FIXTURE)
    accepted, why = nc.verdict(rc, out)
    assert accepted, "the standing negative control no longer fires:\n%s\n%s" % (why, out)


def test_the_fixture_parses_to_the_plain_token():
    """The fixture is read by a JSON parser, not by a regex engine.

    It used to carry a regex word-boundary escape on the end, so the value every surface talks
    about and the value the gate actually loads were different strings — a fixture that is not
    the thing it is named after is a control nobody can reason about.
    """
    with open(nc.FIXTURE, encoding="utf-8") as fh:
        cfg = json.load(fh)
    assert cfg["verification_commands"] == [nc.VERIFIER_NAME], cfg["verification_commands"]
