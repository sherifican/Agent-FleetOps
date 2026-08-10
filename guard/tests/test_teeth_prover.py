"""Claude-authored gate for guard/teeth_prover.py. The implementer must NOT edit this file.

This is the harness that proves other harnesses can fail, so the gate plants a positive for each of its
own outcomes — including a deliberately lying guard and a deliberately non-binding mutation. A prover
that cannot detect a vacuous guard is itself a green light wired to nothing.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from guard.teeth_prover import (  # noqa: E402
    DEFAULT_FIXTURE,
    MUTATIONS,
    REPORTING_ONLY,
    Mutation,
    Result,
    coverage,
    main,
    prove,
)
from guard.research_properties import extract  # noqa: E402

REAL_ID = "dQw4w9WgXcQ"
REGISTRY = frozenset({REAL_ID})


def outcomes(results):
    return {r.mutation: r.outcome for r in results}


# --- the fixture must be clean, or nothing below means anything ---------------------------------------

def test_default_fixture_is_clean():
    """A mutation test on a dirty fixture proves nothing. Verify the premise first."""
    p = extract(DEFAULT_FIXTURE, id_registry=None)
    dirty = [k for k, v in p.items() if not v.ok and k != "video_ids_resolve"]
    assert not dirty, f"DEFAULT_FIXTURE already violates {dirty} — mutations against it are meaningless"


def test_default_fixture_ids_resolve_against_its_own_registry():
    ids = extract(DEFAULT_FIXTURE, id_registry=None)
    assert ids["has_source_link"].ok, "fixture needs a source link for strip-source-link to bind"


# --- the real mutation set ---------------------------------------------------------------------------

def test_every_shipped_mutation_has_teeth():
    """The headline claim. Every declared guard must be reachable by at least its own mutation."""
    results = prove(DEFAULT_FIXTURE, id_registry=None)
    bad = [(r.mutation, r.outcome, r.detail) for r in results
           if r.outcome not in ("HAS_TEETH", "OVERBROAD")]
    assert not bad, f"mutations that did not prove their guard: {bad}"


def test_results_are_returned_for_every_mutation():
    results = prove(DEFAULT_FIXTURE, id_registry=None)
    assert len(results) == len(MUTATIONS)
    assert all(isinstance(r, Result) for r in results)


def test_the_fabrication_mutation_is_present_and_bites():
    """The 2026-07-31 fabricated-id defect must be a permanently reproduced test case."""
    assert any(m.target == "video_ids_resolve" for m in MUTATIONS)
    results = prove(DEFAULT_FIXTURE, id_registry=REGISTRY)
    r = next(r for r in results if r.target == "video_ids_resolve")
    assert r.outcome in ("HAS_TEETH", "OVERBROAD"), f"fabrication guard unproven: {r.outcome} {r.detail}"


# --- planted positive: a guard that lies --------------------------------------------------------------

def test_a_vacuous_mutation_is_caught():
    """A mutation that applies but does not move its declared target must read VACUOUS, not pass."""
    liar = Mutation(
        name="cosmetic-edit-claiming-to-break-actions",
        target="actions_in_vocab",
        apply=lambda t: t.replace("# RESULT", "# RESULT (edited)") if "# RESULT" in t else None,
    )
    r = prove(DEFAULT_FIXTURE, [liar], id_registry=None)[0]
    assert r.outcome == "VACUOUS", f"expected VACUOUS, got {r.outcome}: {r.detail}"


# --- planted positive: a mutation that cannot bind ----------------------------------------------------

def test_a_non_binding_mutation_is_NOT_APPLIED_not_a_pass():
    """26 dead mutations once hid behind exactly this. It must be loud and distinct."""
    ghost = Mutation(name="anchor-that-does-not-exist", target="actions_in_vocab",
                     apply=lambda t: None)
    r = prove(DEFAULT_FIXTURE, [ghost], id_registry=None)[0]
    assert r.outcome == "NOT_APPLIED"


def test_a_mutation_returning_unchanged_text_is_NOT_APPLIED():
    noop = Mutation(name="returns-input-unchanged", target="actions_in_vocab", apply=lambda t: t)
    r = prove(DEFAULT_FIXTURE, [noop], id_registry=None)[0]
    assert r.outcome == "NOT_APPLIED", "an unchanged return is a mutation that did not apply"


def test_NOT_APPLIED_is_distinct_from_VACUOUS():
    ghost = Mutation(name="g", target="actions_in_vocab", apply=lambda t: None)
    liar = Mutation(name="l", target="actions_in_vocab",
                    apply=lambda t: t.replace("# RESULT", "# R") if "# RESULT" in t else None)
    o = outcomes(prove(DEFAULT_FIXTURE, [ghost, liar], id_registry=None))
    assert o["g"] == "NOT_APPLIED" and o["l"] == "VACUOUS"


# --- planted positive: mutating an already-broken fixture ---------------------------------------------

def test_a_dirty_baseline_reads_BAD_FIXTURE():
    """'It fails on the old broken version' is not evidence the guard watches this defect."""
    dirty = DEFAULT_FIXTURE.replace("RELEVANCE: Memory", "XXRELEVANCE: Memory")
    m = next(m for m in MUTATIONS if m.target == "relevance_lines_present")
    r = prove(dirty, [m], id_registry=None)[0]
    assert r.outcome == "BAD_FIXTURE", f"expected BAD_FIXTURE, got {r.outcome}"


# --- collateral / overbroad ---------------------------------------------------------------------------

def test_collateral_is_reported_when_a_mutation_disturbs_more_than_its_target():
    wide = Mutation(name="nuke-everything", target="table_present", apply=lambda t: "")
    r = prove(DEFAULT_FIXTURE, [wide], id_registry=None)[0]
    assert r.outcome in ("OVERBROAD", "HAS_TEETH")
    if r.outcome == "OVERBROAD":
        assert r.collateral, "OVERBROAD must name what else flipped"
        assert list(r.collateral) == sorted(r.collateral)


def test_reporting_properties_do_not_count_as_collateral():
    """row_count/word_count change value on almost any edit; comparing values would poison every result."""
    assert "row_count" in REPORTING_ONLY and "word_count" in REPORTING_ONLY
    for r in prove(DEFAULT_FIXTURE, id_registry=None):
        assert "row_count" not in r.collateral and "word_count" not in r.collateral


# --- coverage: what the prover cannot reach -----------------------------------------------------------

def test_coverage_is_complete_for_the_shipped_mutation_set():
    assert coverage(DEFAULT_FIXTURE, id_registry=None) == ()


def test_coverage_reports_a_property_no_mutation_targets():
    """A property nobody can make fire is indistinguishable from one that does not work."""
    partial = [m for m in MUTATIONS if m.target != "buckets_in_vocab"]
    assert "buckets_in_vocab" in coverage(DEFAULT_FIXTURE, partial, id_registry=None)


def test_coverage_excludes_reporting_only_properties():
    gaps = coverage(DEFAULT_FIXTURE, [], id_registry=None)
    assert "row_count" not in gaps and "word_count" not in gaps


# --- robustness ---------------------------------------------------------------------------------------

def test_prove_never_raises_even_on_a_mutation_that_throws():
    boom = Mutation(name="throws", target="actions_in_vocab",
                    apply=lambda t: (_ for _ in ()).throw(RuntimeError("boom")))
    r = prove(DEFAULT_FIXTURE, [boom], id_registry=None)[0]
    assert r.outcome == "UNMEASURED"
    assert "boom" in r.detail


def test_prove_never_raises_on_garbage_input():
    for text in ("", "\x00", "|" * 1000):
        prove(text, id_registry=None)


def test_prover_writes_nothing_to_disk(tmp_path, monkeypatch):
    """It operates on strings in memory; a prover that writes artifacts can corrupt real ones."""
    import builtins
    real_open = builtins.open
    opened = []

    def spy(path, mode="r", *a, **k):
        if any(w in str(mode) for w in ("w", "a", "+", "x")):
            opened.append(str(path))
        return real_open(path, mode, *a, **k)

    monkeypatch.setattr(builtins, "open", spy)
    prove(DEFAULT_FIXTURE, id_registry=None)
    assert not opened, f"prove() opened files for writing: {opened}"


# --- exit codes ---------------------------------------------------------------------------------------

def test_main_exits_zero_on_the_shipped_set(capsys):
    rc = main([])
    out = capsys.readouterr().out
    assert rc == 0, f"shipped mutation set is not fully proven:\n{out}"
    assert "HAS_TEETH" in out, "a clean run must print evidence it looked at something"


def test_main_exits_one_when_a_guard_is_vacuous(monkeypatch):
    liar = Mutation(name="liar", target="actions_in_vocab",
                    apply=lambda t: t.replace("# RESULT", "# R") if "# RESULT" in t else None)
    monkeypatch.setattr("guard.teeth_prover.MUTATIONS", [liar])
    assert main([]) == 1


def test_main_exits_one_on_an_unprovable_property(monkeypatch):
    monkeypatch.setattr("guard.teeth_prover.MUTATIONS",
                        [m for m in MUTATIONS if m.target != "buckets_in_vocab"])
    assert main([]) == 1


def test_main_prints_UNPROVABLE_for_unreached_properties(monkeypatch, capsys):
    monkeypatch.setattr("guard.teeth_prover.MUTATIONS",
                        [m for m in MUTATIONS if m.target != "buckets_in_vocab"])
    main([])
    assert "UNPROVABLE" in capsys.readouterr().out


def test_main_exits_two_when_something_is_unmeasured(monkeypatch):
    boom = Mutation(name="boom", target="actions_in_vocab",
                    apply=lambda t: (_ for _ in ()).throw(RuntimeError("x")))
    monkeypatch.setattr("guard.teeth_prover.MUTATIONS", [boom])
    assert main([]) == 2


def test_main_accepts_a_real_fixture_file(tmp_path, capsys):
    f = tmp_path / "RESULT_x.md"
    f.write_text(DEFAULT_FIXTURE, encoding="utf-8")
    rc = main(["--fixture", str(f)])
    assert rc in (0, 1, 2)
    assert capsys.readouterr().out.strip(), "must print something for a real fixture"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
