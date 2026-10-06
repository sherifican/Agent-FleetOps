"""pytest entry for the detector-verdict checks that live in guard/tests/teeth_fetch_gate.py.

The plan homes the fetch-gate teeth in teeth_fetch_gate.py, which the mutation harness runs
as a script; pytest collects only test_*.py, so without this file the A1 RED cases would be
invisible to `pytest guard/tests/`. The checks themselves stay in the teeth file (one owner);
this module only loads it by path and re-exports the test_verdict_envelope_* functions.
"""
import importlib.util
import pathlib

_TEETH = pathlib.Path(__file__).resolve().parent / "teeth_fetch_gate.py"
_spec = importlib.util.spec_from_file_location("teeth_fetch_gate", _TEETH)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

for _fn in _mod.VERDICT_ENVELOPE_CHECKS + _mod.TRAILING_WHITESPACE_CHECKS + _mod.STORED_VALUE_CHECKS:
    globals()[_fn.__name__] = _fn


def test_verdict_envelope_checks_are_wired_into_the_teeth_entry_point():
    """The harness marker must depend on the A1 checks, or a RED here is silent there."""
    assert _mod.VERDICT_ENVELOPE_CHECKS, "no test_verdict_envelope_* checks found in teeth_fetch_gate.py"
    src = _TEETH.read_text(encoding="utf-8")
    assert "for fn in VERDICT_ENVELOPE_CHECKS" in src, "main() does not run the A1 checks"


def test_trailing_whitespace_rstrip_checks_are_wired_into_the_teeth_entry_point():
    """The decorated-verdict checks must reach the harness marker the same way."""
    assert _mod.TRAILING_WHITESPACE_CHECKS, "no test_trailing_whitespace_* checks found in teeth_fetch_gate.py"
    src = _TEETH.read_text(encoding="utf-8")
    assert "VERDICT_ENVELOPE_CHECKS + TRAILING_WHITESPACE_CHECKS" in src, "main() does not run the regression A1 checks"


# =============================================================================================
# The two wiring tests above grep the harness SOURCE for a name;
# a main() loop that drops the regression checks while the name survives in a comment passes them
# and still prints the all-pass marker. This pins the BEHAVIOUR: with one regression check made to
# fail, the harness entry point must not report all-pass.
# =============================================================================================
import contextlib  # noqa: E402
import io  # noqa: E402


def _run_harness_main():
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = _mod.main()
    return rc, buf.getvalue()


def test_control_the_untouched_harness_reports_all_pass():
    rc, out = _run_harness_main()
    assert rc == 0 and _mod.MARKER in out, (rc, out)


def test_a_failing_trailing_whitespace_check_makes_the_harness_entry_point_report_not_sound(monkeypatch):
    victim = _mod.TRAILING_WHITESPACE_CHECKS[0]

    def planted_failure():
        raise AssertionError("harness_wiring planted failure in %s" % victim.__name__)
    planted_failure.__name__ = victim.__name__

    monkeypatch.setattr(_mod, "TRAILING_WHITESPACE_CHECKS", [planted_failure] + list(_mod.TRAILING_WHITESPACE_CHECKS[1:]))
    rc, out = _run_harness_main()
    assert _mod.MARKER not in out, (
        "the harness printed the all-pass marker while a regression check was failing — the entry "
        "point does not run the regression checks:\n" + out)
    assert rc != 0, "the harness exited 0 with a failing regression check:\n" + out
    assert "harness_wiring planted failure" in out, "the failing check's message must reach the output:\n" + out


# =============================================================================================
# The G6 test plants its failure only in TRAILING_WHITESPACE_CHECKS[0], so a
# harness loop that runs only the FIRST regression check passes it. Every member must be reachable
# from the entry point: the planted failure is parametrised over all of them.
# =============================================================================================
import pytest  # noqa: E402

_TRAILING_WHITESPACE_NAMES = [fn.__name__ for fn in _mod.TRAILING_WHITESPACE_CHECKS]


def test_control_there_is_more_than_one_trailing_whitespace_check_to_plant_in():
    assert len(_mod.TRAILING_WHITESPACE_CHECKS) >= 2, _TRAILING_WHITESPACE_NAMES


@pytest.mark.parametrize("idx", range(len(_mod.TRAILING_WHITESPACE_CHECKS)), ids=_TRAILING_WHITESPACE_NAMES)
def test_a_failure_planted_in_any_trailing_whitespace_check_makes_the_harness_report_not_sound(
        monkeypatch, idx):
    victim = _mod.TRAILING_WHITESPACE_CHECKS[idx]
    marker = "harness_each_check planted failure in %s" % victim.__name__

    def planted_failure():
        raise AssertionError(marker)
    planted_failure.__name__ = victim.__name__

    checks = list(_mod.TRAILING_WHITESPACE_CHECKS)
    checks[idx] = planted_failure
    monkeypatch.setattr(_mod, "TRAILING_WHITESPACE_CHECKS", checks)
    rc, out = _run_harness_main()
    assert _mod.MARKER not in out, (
        "the harness printed the all-pass marker while trailing-whitespace check #%d (%s) was failing — the "
        "entry point does not run that check:\n%s" % (idx, victim.__name__, out))
    assert rc != 0, "the harness exited 0 with trailing-whitespace check #%d failing:\n%s" % (idx, out)
    assert marker in out, "the failing check's message must reach the output:\n" + out


# =============================================================================================
# The earlier fetch-gate checks live in teeth_fetch_gate.py like the
# others. They must reach both entry points: re-exported above for pytest, and run by the
# harness main(), which is pinned by behaviour: a failure planted in EACH of them.
# =============================================================================================
_STORED_VALUE_NAMES = [fn.__name__ for fn in _mod.STORED_VALUE_CHECKS]


def test_stored_value_controls_are_collected():
    assert len(_mod.STORED_VALUE_CHECKS) >= 5, _STORED_VALUE_NAMES
    assert all(globals().get(n) is fn for n, fn in zip(_STORED_VALUE_NAMES, _mod.STORED_VALUE_CHECKS))


@pytest.mark.parametrize("idx", range(len(_mod.STORED_VALUE_CHECKS)), ids=_STORED_VALUE_NAMES)
def test_stored_value_a_failure_planted_in_any_check_makes_the_harness_report_not_sound(
        monkeypatch, idx):
    victim = _mod.STORED_VALUE_CHECKS[idx]
    marker = "stored_value_witness planted failure in %s" % victim.__name__

    def planted_failure():
        raise AssertionError(marker)
    planted_failure.__name__ = victim.__name__

    checks = list(_mod.STORED_VALUE_CHECKS)
    checks[idx] = planted_failure
    monkeypatch.setattr(_mod, "STORED_VALUE_CHECKS", checks)
    rc, out = _run_harness_main()
    assert _mod.MARKER not in out, (
        "the harness printed the all-pass marker while stored-value check #%d (%s) was failing:\n%s"
        % (idx, victim.__name__, out))
    assert rc != 0, "the harness exited 0 with stored-value check #%d failing:\n%s" % (idx, out)
    assert marker in out, "the failing check's message must reach the output:\n" + out


# =============================================================================================
# Mutation validity: a published fetch-gate mutation must still MUTATE something.
# Found by an earlier reviewer and reproduced by the orchestrator: FG1 removed "scan-error" from
# BLOCKING_PREFIXES, but verdict_blocks() no longer reads that constant, so the mutant
# changed nothing, the teeth entry point stayed green, and the full harness would report the guard as
# having lost the property FG1 names ("a scan error stops counting as a reason to withhold the
# payload") although it had not. `--verify-anchors` still said the needle "points at live code": it
# measures that the needle BINDS, not that the mutation changes behaviour.
# Pinned here (pytest only: the teeth entry point runs inside the sandbox and must not recurse):
#   - every fetch_gate mutation the harness publishes is KILLED by the teeth entry point when applied
#     to a sandbox built the harness's own way (its FILES list, its REPO retargeting), after a control
#     that the same sandbox is green unmutated;
#   - FG1 reintroduces what it claims: the mutated module RELEASES a scan-error verdict;
#   - --verify-anchors does not claim more than it measured.
# =============================================================================================
import os  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
import tempfile  # noqa: E402

_REPO = _TEETH.parents[2]


def _c8d_harness():
    spec = importlib.util.spec_from_file_location("c8d_mutation_harness", _REPO / "guard" / "mutation_harness.py")
    mh = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mh)
    return mh


def _c8d_sandbox(mh, td):
    for rel in mh.FILES:
        src = os.path.join(mh.REPO, rel)
        if not os.path.exists(src):
            continue
        dst = os.path.join(td, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(src, encoding="utf-8") as fh:
            content = fh.read().replace(mh.REPO, td)
        with open(dst, "w", encoding="utf-8") as fh:
            fh.write(content)
    # the teeth entry point imports `guard.fetch_gate` from the sandbox root
    for pkg in ("guard", os.path.join("guard", "tests")):
        init = os.path.join(mh.REPO, pkg, "__init__.py")
        if os.path.exists(init):
            shutil.copy(init, os.path.join(td, pkg, "__init__.py"))


def _c8d_fetch_mutations(mh):
    return [m for m in mh.MUTATIONS if m["guard"] == "fetch_gate"]


def test_stored_value_d_every_fetch_gate_mutation_is_killed_by_the_teeth_entry_point():
    mh = _c8d_harness()
    muts = _c8d_fetch_mutations(mh)
    assert muts, "the harness publishes no fetch_gate mutation"
    failures = []
    with tempfile.TemporaryDirectory() as td:
        _c8d_sandbox(mh, td)
        rc, out = mh.run_guard(td, "fetch_gate", timeout=240)
        assert mh.guard_green("fetch_gate", rc, out), "control: the unmutated sandbox is not green:\n" + out[-1500:]
    for m in muts:
        with tempfile.TemporaryDirectory() as td:
            _c8d_sandbox(mh, td)
            path = os.path.join(td, m["file"])
            with open(path, encoding="utf-8") as fh:
                src = fh.read()
            if src.count(m["old"]) != 1:
                failures.append("%s: needle binds %d time(s)" % (m["id"], src.count(m["old"])))
                continue
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(src.replace(m["old"], m["new"]))
            rc, out = mh.run_guard(td, "fetch_gate", timeout=240)
            verdict = mh.classify("fetch_gate", rc, out)
            if verdict[0] != "KILLED":
                failures.append("%s (%s): %s — the mutant changed nothing the teeth check"
                                % (m["id"], m["desc"], verdict))
    assert not failures, "\n  ".join(["a published fetch_gate mutation is not live:"] + failures)


def _c8d_load_fetch_gate(text, label):
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "fg_c8d.py")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        spec = importlib.util.spec_from_file_location("fg_c8d_" + label, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod


# Strengthened verdict coverage: the first version compared only four verdicts, so
# an FG1 that ALSO released POTENTIAL_POISON passed every fetch-gate test (an earlier reviewer's
# mutant, reproduced by the orchestrator: 57 passed). FG1 must change exactly the verdicts it names.
# MUST_RELEASE: every form scan() returns for a failed scan. MUST_NOT_CHANGE: every input that does not
# start with "scan-error" — the whole vocabulary, invalid values, near-misses and lying objects.
# (Texts that start with "scan-error" but are not scan() output are left unpinned on purpose: a
# mutation may reasonably release them or not.)
# Strengthened verdict coverage: the must-release side sampled five identifier-shaped names, so an FG1
# releasing only identifier-shaped names passed (an earlier reviewer's mutant; `scan-error:404` and
# `scan-error:9_Err` are real scan() outputs it left blocked). The set is now GENERATED from the
# sanitizer's output domain by calling the real scan(): each of the 63 kept characters alone, in first and
# in later position, runs of each length 1-80, names whose non-ASCII first letter is dropped to expose a
# leading digit or underscore, an over-long name, a seeded sample, and the InvalidVerdict and fallback forms.
C8D_KEEP_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_"


def _c8d_must_release():
    import random
    k = C8D_KEEP_CHARS
    names = list(k) + ["A" + c for c in k] + [c + "A" for c in k]
    names += [c * n for c in "9_aZ" for n in range(1, 81)]
    names += ["\u00c9404", "\u00c99_Err", "\u00c4_", "\u00df1", "\u00e9_9", "B" * 100, "<<< >>>", "HTTP_404_Error"]
    rng = random.Random(20260923)
    names += ["".join(rng.choice(k) for _ in range(rng.randint(1, 80))) for _ in range(400)]
    outputs = set(_mod._c8c_scan_named(names))
    with _mod._stub_detector("return {'verdict': 'NOT_A_VERDICT'}"):
        outputs.add(fetch_gate_scan_once())
    return sorted(outputs)


def fetch_gate_scan_once():
    return _mod.fetch_gate.scan("some prose", "verdict_envelope")


def _c8d_must_not_change():
    liar, enum_v = _mod._C8bLiar, _mod._C8bVerdict
    return ([("'%s'" % v, v) for v in ("CLEAN", "DATA_QUALITY", "POTENTIAL_POISON", "CERTAIN_POISON",
                                        "UNKNOWN", "NEW_POISON", "", "clean", "CLEAN ", " CLEAN",
                                        "CLEAN:ok", "scan", "scanerror", "scan_error:X", "Scan-error:X",
                                        " scan-error:X", "xscan-error:X", "SCAN-ERROR:X")]
            + [("None", None), ("0", 0), ("1.0", 1.0), ("b'CLEAN'", b"CLEAN"), ("[]", []),
               ("V.CLEAN", enum_v.CLEAN), ("V.DATA_QUALITY", enum_v.DATA_QUALITY),
               ("V.POTENTIAL_POISON", enum_v.POTENTIAL_POISON), ("V.CERTAIN_POISON", enum_v.CERTAIN_POISON),
               ("str storing CERTAIN_POISON claiming scan-error:X", liar("CERTAIN_POISON", "scan-error:X")),
               ("str storing POTENTIAL_POISON claiming CLEAN", liar("POTENTIAL_POISON", "CLEAN")),
               ("str storing CLEAN claiming CERTAIN_POISON", liar("CLEAN", "CERTAIN_POISON"))])


def _c8d_fg1_problems(src, old, new, must_release=None):
    """What an FG1 (old -> new) gets wrong against its claim; [] when it changes exactly scan errors."""
    base = _c8d_load_fetch_gate(src, "unmutated")
    mut = _c8d_load_fetch_gate(src.replace(old, new), "fg1")
    problems = []
    for v in (must_release if must_release is not None else _c8d_must_release()):
        if base.verdict_blocks(v) is not True or mut.verdict_blocks(v) is not False:
            problems.append("%r: unmutated blocks=%r, FG1 blocks=%r (want True then False)"
                            % (v[:40], base.verdict_blocks(v), mut.verdict_blocks(v)))
    for label, v in _c8d_must_not_change():
        b, m = base.verdict_blocks(v), mut.verdict_blocks(v)
        if b != m:
            problems.append("%s: unmutated blocks=%r, FG1 blocks=%r — FG1 changed more than scan errors"
                            % (label, b, m))
    return problems


def test_stored_value_d_fg1_releases_a_scan_error_verdict_as_it_claims():
    mh = _c8d_harness()
    fg1 = [m for m in _c8d_fetch_mutations(mh) if m["id"] == "FG1"]
    assert len(fg1) == 1, "FG1 is not published exactly once"
    src = (_REPO / fg1[0]["file"]).read_text(encoding="utf-8")
    assert src.count(fg1[0]["old"]) == 1
    problems = _c8d_fg1_problems(src, fg1[0]["old"], fg1[0]["new"])
    assert not problems, "\n  ".join(["FG1 does not change exactly the verdicts it claims:"] + problems)


def test_stored_value_d_control_the_fg1_check_catches_a_broader_mutation():
    # The earlier reviewer's mutant: FG1 plus POTENTIAL_POISON. It is KILLED by the teeth entry point,
    # so only this check can show it is too broad.
    mh = _c8d_harness()
    fg1 = [m for m in _c8d_fetch_mutations(mh) if m["id"] == "FG1"][0]
    src = (_REPO / fg1["file"]).read_text(encoding="utf-8")
    broad_new = fg1["new"][:-1] + ' or str.__str__(verdict) == "POTENTIAL_POISON")'
    assert broad_new != fg1["new"] and fg1["new"].endswith(")")
    problems = _c8d_fg1_problems(src, fg1["old"], broad_new)
    assert any("POTENTIAL_POISON" in p for p in problems), problems
    # the earlier reviewer's mutant: releases only identifier-shaped scan-error names
    narrow_new = ('(str.__str__(verdict) in ("CLEAN", "DATA_QUALITY")'
                  ' or (str.__str__(verdict).startswith("scan-error:")'
                  ' and str.__str__(verdict).partition(":")[2].isidentifier()))')
    narrow = _c8d_fg1_problems(src, fg1["old"], narrow_new)
    assert any("scan-error:404" in p or "scan-error:9" in p for p in narrow), narrow
    # and an FG1 that releases nothing is caught on the must-release side
    assert _c8d_fg1_problems(src, fg1["old"], fg1["old"]), "an identity mutation passed the FG1 check"


def test_stored_value_d_control_the_must_release_set_is_real_scan_output():
    rel = _c8d_must_release()
    must = {"scan-error:404", "scan-error:9_Err", "scan-error:_", "scan-error:9", "scan-error:Exception",
            "scan-error:InvalidVerdict", "scan-error:" + "B" * 80, "scan-error:" + "9" * 80}
    assert must <= set(rel), sorted(must - set(rel))
    assert all(type(v) is str and v.startswith("scan-error:") for v in rel)
    firsts = {v[len("scan-error:")] for v in rel}
    assert set(C8D_KEEP_CHARS) <= firsts, "a kept character never appears first: %r" % sorted(set(C8D_KEEP_CHARS) - firsts)
    assert {len(v) - len("scan-error:") for v in rel} >= set(range(1, 81))


def test_stored_value_d_verify_anchors_claims_binding_not_liveness():
    p = subprocess.run([sys.executable, str(_REPO / "guard" / "mutation_harness.py"), "--verify-anchors"],
                       capture_output=True, text=True, timeout=240, cwd=str(_REPO),
                       env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
    assert p.returncode == 0, p.stdout + p.stderr
    assert "bind exactly once" in p.stdout, p.stdout
    assert "live code" not in p.stdout, (
        "--verify-anchors checks that each needle binds; it does not run a mutant, so it cannot say a "
        "mutation still points at live code:\n" + p.stdout)
