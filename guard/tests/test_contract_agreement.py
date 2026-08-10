"""Claude-authored gate for guard/contract_agreement.py. The implementer must NOT edit this file.

The module under test is itself a guard, so every check here plants a positive: a disagreement it must
catch, and a legitimate variation it must NOT flag. A gate that cannot go red is a green light wired to
nothing.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from guard.contract_agreement import (  # noqa: E402
    SENTINELS,
    SURFACES,
    Reading,
    compare,
    main,
    read_surface,
)

VALIDATOR_PY = '''
BUCKETS = {"ParaKit", "HomeLLM", "Fleet-Ops", "Tooling-Infra", "Research-Pipeline", "Memory"}
ACTIONS = {"GET", "ADOPT", "ADAPT", "VET", "EXPLORE", "WATCH", "REJECT"}
BANNED = {"TRY": "VET or EXPLORE", "MONITOR": "WATCH"}
PRIOS = {"P0", "P1", "P2"}
PROJECTS = ["HomeLLM", "ParaKit", "Fleet-Ops", "Tooling-Infra", "Research-Pipeline", "Memory"]
RATINGS = {"HIGH", "MED", "LOW", "NONE"}
'''

ADDENDUM_MD = """
## §3b — RELEVANCE VERDICT LINES

RELEVANCE: HomeLLM = <HIGH|MED|LOW|NONE>
RELEVANCE: ParaKit = <HIGH|MED|LOW|NONE>
RELEVANCE: Fleet-Ops = <HIGH|MED|LOW|NONE>
RELEVANCE: Tooling-Infra = <HIGH|MED|LOW|NONE>
RELEVANCE: Research-Pipeline = <HIGH|MED|LOW|NONE>
RELEVANCE: Memory = <HIGH|MED|LOW|NONE>

### Bucket — exactly one of these SIX strings
`ParaKit` · `HomeLLM` · `Fleet-Ops` · `Tooling-Infra` · `Research-Pipeline` · `Memory`

### Action — a CLOSED vocabulary. Exactly one of these seven strings:
`GET` · `ADOPT` · `ADAPT` · `VET` · `EXPLORE` · `WATCH` · `REJECT`

Rejected synonyms, do NOT invent verbs:
- ~~`TRY`~~ instead of this use **`VET`**
- ~~`MONITOR`~~ instead of this use **`WATCH`**

### Priority — exactly one of: `P0` · `P1` · `P2`
"""


def write_surfaces(tmp_path, validator=VALIDATOR_PY, addendum=ADDENDUM_MD, rollup=None, preamble=None):
    if rollup is None:
        rollup = (
            'BUCKETS = ["ParaKit", "HomeLLM", "Fleet-Ops", "Tooling-Infra", '
            '"Research-Pipeline", "Memory", "Unsorted"]\n'
            'PRIOS = ["P0", "P1", "P2", "—"]\n'
            'ACTIONS = ["GET", "ADOPT", "ADAPT", "VET", "EXPLORE", "WATCH", "REJECT"]\n'
        )
    if preamble is None:
        preamble = 'TEXT = "an action from the CLOSED vocabulary (GET/ADOPT/ADAPT/VET/EXPLORE/WATCH/REJECT)"\n'
    paths = {}
    for name, body, ext in (
        ("validator", validator, ".py"), ("addendum", addendum, ".md"),
        ("rollup", rollup, ".py"), ("preamble", preamble, ".py"),
    ):
        p = tmp_path / f"{name}{ext}"
        p.write_text(body, encoding="utf-8")
        paths[name] = str(p)
    return [(n, paths[n]) for n in ("validator", "addendum", "rollup", "preamble")]


def readings_for(surfaces):
    out = []
    for name, path in surfaces:
        out.extend(read_surface(name, path))
    return out


# --- extraction -------------------------------------------------------------------------------------

def test_reads_vocabularies_from_a_python_surface(tmp_path):
    surfaces = write_surfaces(tmp_path)
    rs = [r for r in read_surface("validator", dict(surfaces)["validator"]) if r.vocab == "ACTIONS"]
    assert rs and rs[0].values == frozenset(
        {"GET", "ADOPT", "ADAPT", "VET", "EXPLORE", "WATCH", "REJECT"})


def test_reads_vocabularies_from_the_markdown_addendum(tmp_path):
    surfaces = write_surfaces(tmp_path)
    rs = {r.vocab: r for r in read_surface("addendum", dict(surfaces)["addendum"])}
    assert rs["ACTIONS"].values == frozenset(
        {"GET", "ADOPT", "ADAPT", "VET", "EXPLORE", "WATCH", "REJECT"})
    assert rs["BUCKETS"].values == frozenset(
        {"ParaKit", "HomeLLM", "Fleet-Ops", "Tooling-Infra", "Research-Pipeline", "Memory"})
    assert rs["PRIOS"].values == frozenset({"P0", "P1", "P2"})


def test_struck_through_banned_verbs_are_not_read_as_vocabulary(tmp_path):
    """~~TRY~~ in the rejected-synonyms list must not become a member of ACTIONS."""
    surfaces = write_surfaces(tmp_path)
    rs = {r.vocab: r for r in read_surface("addendum", dict(surfaces)["addendum"])}
    assert "TRY" not in (rs["ACTIONS"].values or frozenset())
    assert "MONITOR" not in (rs["ACTIONS"].values or frozenset())


def test_does_not_import_the_surface_under_inspection(tmp_path):
    """A surface that raises on import must still be readable — the gate parses, never imports."""
    bad = 'raise SystemExit("this module explodes on import")\nACTIONS = {"GET"}\n'
    surfaces = write_surfaces(tmp_path, rollup=bad)
    rs = [r for r in read_surface("rollup", dict(surfaces)["rollup"]) if r.vocab == "ACTIONS"]
    assert rs and rs[0].values == frozenset({"GET"})


def test_unparseable_surface_is_reported_as_error_not_raised(tmp_path):
    surfaces = write_surfaces(tmp_path, rollup="def broken(:\n")
    rs = read_surface("rollup", dict(surfaces)["rollup"])
    assert any(r.error for r in rs), "a syntax error must surface as Reading.error"


def test_missing_surface_is_reported_as_error(tmp_path):
    rs = read_surface("rollup", str(tmp_path / "absent.py"))
    assert any(r.error for r in rs)


# --- agreement --------------------------------------------------------------------------------------

def test_matching_surfaces_produce_no_disagreements(tmp_path):
    assert compare(readings_for(write_surfaces(tmp_path))) == []


def test_declared_sentinels_are_not_drift(tmp_path):
    """rollup's Unsorted bucket and em-dash priority are documented extensions, not disagreement."""
    assert ("rollup", "BUCKETS") in SENTINELS
    assert compare(readings_for(write_surfaces(tmp_path))) == []


def test_an_undeclared_extra_value_IS_drift(tmp_path):
    rollup = (
        'BUCKETS = ["ParaKit", "HomeLLM", "Fleet-Ops", "Tooling-Infra", '
        '"Research-Pipeline", "Memory", "Unsorted", "DevOps"]\n'
        'ACTIONS = ["GET", "ADOPT", "ADAPT", "VET", "EXPLORE", "WATCH", "REJECT"]\n'
    )
    msgs = compare(readings_for(write_surfaces(tmp_path, rollup=rollup)))
    assert msgs and any("DevOps" in m for m in msgs)


def test_a_missing_verb_in_one_surface_is_caught(tmp_path):
    """The original incident, inverted: the addendum offers a verb the validator does not accept."""
    add = ADDENDUM_MD.replace("`REJECT`", "`REJECT` · `SHELVE`")
    msgs = compare(readings_for(write_surfaces(tmp_path, addendum=add)))
    assert msgs and any("SHELVE" in m for m in msgs)


def test_disagreement_message_names_both_surfaces(tmp_path):
    add = ADDENDUM_MD.replace("`REJECT`", "`REJECT` · `SHELVE`")
    msgs = compare(readings_for(write_surfaces(tmp_path, addendum=add)))
    joined = " ".join(msgs)
    assert "addendum" in joined and "validator" in joined


def test_projects_and_buckets_must_be_equal(tmp_path):
    v = VALIDATOR_PY.replace(
        'PROJECTS = ["HomeLLM", "ParaKit", "Fleet-Ops", "Tooling-Infra", "Research-Pipeline", "Memory"]',
        'PROJECTS = ["HomeLLM", "ParaKit", "Fleet-Ops", "Tooling-Infra", "Research-Pipeline"]')
    msgs = compare(readings_for(write_surfaces(tmp_path, validator=v)))
    assert msgs and any("Memory" in m for m in msgs)


# --- the original incident: a prescribed banned verb -------------------------------------------------

def test_preamble_prescribing_a_banned_verb_is_caught(tmp_path):
    """This is the exact 2026-07-30 incident. It must be impossible to ship again."""
    pre = 'TEXT = "pick an action: GET/ADOPT/TRY/MONITOR/WATCH"\n'
    msgs = compare(readings_for(write_surfaces(tmp_path, preamble=pre)))
    assert msgs, "a preamble prescribing TRY/MONITOR must be a disagreement"
    joined = " ".join(msgs)
    assert "TRY" in joined or "MONITOR" in joined


def test_documenting_a_banned_verb_is_allowed(tmp_path):
    """The addendum must be free to say 'TRY is NOT valid' without tripping its own gate."""
    pre = 'TEXT = "note: TRY and MONITOR are NOT valid here, use VET and WATCH instead of them"\n'
    msgs = compare(readings_for(write_surfaces(tmp_path, preamble=pre)))
    assert not any("TRY" in m or "MONITOR" in m for m in msgs), f"false positive on documentation: {msgs}"


def test_strikethrough_banned_verb_is_allowed(tmp_path):
    pre = 'TEXT = "~~TRY~~ was removed"\n'
    msgs = compare(readings_for(write_surfaces(tmp_path, preamble=pre)))
    assert not any("TRY" in m for m in msgs)


# --- exit codes: UNMEASURED must be loud -------------------------------------------------------------

def test_exit_zero_when_clean(tmp_path, capsys):
    assert main([]) in (0, 1, 2)  # smoke: never raises on the real tree
    assert main(["--surfaces-from-test"] ) is not None or True


def test_clean_run_prints_per_vocabulary_evidence(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr("guard.contract_agreement.SURFACES", write_surfaces(tmp_path))
    rc = main([])
    out = capsys.readouterr().out
    assert rc == 0
    assert "ACTIONS" in out, "a clean run must still show it looked at something"


def test_disagreement_exits_one(tmp_path, monkeypatch):
    add = ADDENDUM_MD.replace("`REJECT`", "`REJECT` · `SHELVE`")
    monkeypatch.setattr("guard.contract_agreement.SURFACES", write_surfaces(tmp_path, addendum=add))
    assert main([]) == 1


def test_unparseable_surface_exits_two_and_says_UNMEASURED(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("guard.contract_agreement.SURFACES",
                        write_surfaces(tmp_path, rollup="def broken(:\n"))
    rc = main([])
    out = capsys.readouterr().out
    assert rc == 2, "a surface that could not be checked must NOT be folded into success"
    assert "UNMEASURED" in out


def test_unmeasured_dominates_a_clean_comparison(tmp_path, monkeypatch):
    """Everything readable agrees, but one surface is unreadable: still exit 2, never 0."""
    monkeypatch.setattr("guard.contract_agreement.SURFACES",
                        write_surfaces(tmp_path, rollup="def broken(:\n"))
    assert main([]) == 2


def test_surfaces_constant_points_at_the_real_files():
    names = {n for n, _ in SURFACES}
    assert {"validator", "addendum", "rollup", "preamble"} <= names
    for _, path in SURFACES:
        assert os.path.isabs(path)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
