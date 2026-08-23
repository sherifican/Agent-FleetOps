"""teeth_prover.py — prove every guard can actually fail.

Every guard in research_properties was written after its incident, and nothing tests that any of
them still works. A good check whose input shape moved is indistinguishable from a check at all —
it just never fires. The rule this module enforces:

    Every guard ships with a mutation that makes it go red. If you cannot write a mutation that
    makes your check fail, you have not written a check.

This module proves guards are REACHABLE, not that they are CORRECT. It mutates a known-clean
fixture (mutating an already-broken artifact proves nothing — a red light there says nothing about
which check watched it), asserts the target property holds in the baseline, applies the mutation,
and compares `ok` flags ONLY. `value` is never compared: row_count/word_count change value on
almost any edit while staying ok=True, and treating that as a flip would poison every result.

Purity contract: no network, no clock, no randomness, no writes. Files are read only via the
--fixture/--registry CLI options. prove() never raises; internal errors become UNMEASURED results.
"""

import argparse
import os
import re
import sys
from dataclasses import dataclass
from typing import Callable

try:
    from . import research_properties as _rp
except ImportError:  # invoked as a loose script, not as guard.teeth_prover
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from guard import research_properties as _rp  # noqa: E402

extract = _rp.extract
load_id_registry = _rp.load_id_registry
_report_ids = getattr(_rp, "_report_ids", None)

# --- outcomes: five plus the error case, and the distinctions are load-bearing -----------------------

HAS_TEETH = "HAS_TEETH"        # target flipped ok True->False, nothing else flipped
OVERBROAD = "OVERBROAD"        # target flipped, collateral too — informational, not a failure
VACUOUS = "VACUOUS"            # mutation applied, target did NOT flip — the guard is lying (loud)
NOT_APPLIED = "NOT_APPLIED"    # mutation could not bind — just as loud as VACUOUS, different cause
BAD_FIXTURE = "BAD_FIXTURE"    # baseline already violates the target — flipping it proves nothing
UNMEASURED = "UNMEASURED"      # extraction raised on baseline or mutant, or apply() raised

OUTCOMES = (HAS_TEETH, OVERBROAD, VACUOUS, NOT_APPLIED, BAD_FIXTURE, UNMEASURED)

# Reporting properties have no failure mode to reach; they are EXPECTED-unprovable, never a gap.
REPORTING_ONLY = {"row_count", "word_count"}

# A real fabricated id this pipeline emitted on 2026-07-31. The mutation reproduces an actual
# defect, not a hypothetical one.
FABRICATED_ID = "aNiBFVjvoqk"

# When --fixture is given without --registry, video_ids_resolve is evaluated against this backlog.
DEFAULT_REGISTRY = os.environ.get("TEETH_REGISTRY", "./fixtures/backlog_ids.txt")  # EDIT ME: your worklist

# A clean in-file report: every judging property ok=True. The prover runs with zero external
# dependencies, and a mutation test on a dirty fixture proves nothing — so this premise is checked
# first by the gate.
DEFAULT_FIXTURE = """# RESULT — teeth-prover self-test fixture (clean)

Source: https://youtu.be/dQw4w9WgXcQ

RELEVANCE: Local-Models = NONE
RELEVANCE: Flagship-App = NONE
RELEVANCE: Fleet-Ops = HIGH
RELEVANCE: Tooling-Infra = MED
RELEVANCE: Research-Pipeline = LOW
RELEVANCE: Memory = NONE

## §5 — ACTIONABLE ITEMS

| Item | What it is | Bucket | Action | Priority | Why | [MM:SS] |
| :--- | :--- | :---: | :---: | :---: | :--- | :---: |
| uv | fast python package manager | Tooling-Infra | GET | P1 | 40 unlinted scripts | 04:12 |
| ruff | linter | Tooling-Infra | ADOPT | P2 | same tree | 05:30 |
"""


@dataclass(frozen=True)
class Mutation:
    name: str                             # e.g. "drop-one-relevance-line"
    target: str                           # the property name it must make fire
    apply: Callable[[str], "str | None"]  # mutated text, or None when it cannot bind


@dataclass(frozen=True)
class Result:
    mutation: str
    target: str
    outcome: str                # one of OUTCOMES
    collateral: tuple           # other property names that also flipped, sorted
    detail: str


# --- the mutations -----------------------------------------------------------------------------------
# Each checks its anchor is present and returns None when it is not. That check is the whole point
# of NOT_APPLIED: a mutation whose anchor moved must be as loud as a guard whose logic went inert.

_RE_REL_LINE = re.compile(r"(RELEVANCE:\s*.+?=\s*)(\S+)")
_RE_URL_ID = re.compile(r"(?:youtu\.be/|watch\?v=|youtube\.com/embed/)([A-Za-z0-9_-]{11})")
_RE_SOURCE_URL = re.compile(r"https?://\S*(?:youtu\.be|youtube\.com)\S*")

_ACTION_CELLS = ("| GET |", "| ADOPT |", "| ADAPT |", "| VET |", "| EXPLORE |", "| WATCH |",
                 "| REJECT |")
_BUCKET_CELLS = ("| Tooling-Infra |", "| Flagship-App |", "| Memory |", "| Fleet-Ops |",
                 "| Local-Models |", "| Research-Pipeline |")
_PRIO_CELLS = ("| P0 |", "| P1 |", "| P2 |")


def _swap_first_cell(text, anchors, new):
    """Replace the first anchor cell present with `new`, or None when none of them bind."""
    for anchor in anchors:
        if anchor in text:
            return text.replace(anchor, new, 1)
    return None


def _drop_one_relevance_line(text):
    anchor = "RELEVANCE: Memory"
    if anchor not in text:
        return None
    kept = [ln for ln in text.splitlines(keepends=True) if not ln.lstrip().startswith(anchor)]
    return "".join(kept)


def _bogus_relevance_rating(text):
    m = _RE_REL_LINE.search(text)
    if m is None:
        return None
    return text[:m.start(2)] + "CRITICAL" + text[m.end(2):]


def _all_high_relevance(text):
    if "RELEVANCE:" not in text:
        return None
    return _RE_REL_LINE.sub(lambda m: m.group(1) + "HIGH", text)


def _remove_actionable_table(text):
    idx = text.find("## §5")
    if idx < 0:
        return None
    return text[:idx]


def _invented_action_verb(text):
    return _swap_first_cell(text, _ACTION_CELLS, "| YOINK |")


def _banned_action_verb(text):
    return _swap_first_cell(text, _ACTION_CELLS, "| TRY |")


def _invented_bucket(text):
    return _swap_first_cell(text, _BUCKET_CELLS, "| DevOps |")


def _out_of_range_priority(text):
    return _swap_first_cell(text, _PRIO_CELLS, "| P3 |")


def _fabricate_video_id(text):
    m = _RE_URL_ID.search(text)
    if m is None:
        return None
    return text[:m.start(1)] + FABRICATED_ID + text[m.end(1):]


def _strip_source_link(text):
    if "youtu.be/" not in text and "youtube.com" not in text:
        return None
    return _RE_SOURCE_URL.sub("", text)


MUTATIONS = [
    Mutation("drop-one-relevance-line", "relevance_lines_present", _drop_one_relevance_line),
    Mutation("bogus-relevance-rating", "relevance_values_valid", _bogus_relevance_rating),
    Mutation("all-high-relevance", "relevance_calibrated", _all_high_relevance),
    Mutation("remove-actionable-table", "table_present", _remove_actionable_table),
    Mutation("invented-action-verb", "actions_in_vocab", _invented_action_verb),
    Mutation("banned-action-verb", "no_banned_verbs", _banned_action_verb),
    Mutation("invented-bucket", "buckets_in_vocab", _invented_bucket),
    Mutation("out-of-range-priority", "priorities_in_vocab", _out_of_range_priority),
    Mutation("fabricated-video-id", "video_ids_resolve", _fabricate_video_id),
    Mutation("strip-source-link", "has_source_link", _strip_source_link),
]


# --- the prover --------------------------------------------------------------------------------------

def _prove_one(text, mutation, baseline, base_exc, registry):
    name, target = mutation.name, mutation.target
    try:
        if base_exc is not None:
            return Result(name, target, UNMEASURED, (),
                          "baseline extraction raised: %s" % base_exc)
        if target not in baseline:
            return Result(name, target, UNMEASURED, (),
                          "target property %r absent from baseline extraction" % target)
        # The honesty rule: assert the target holds in the known-clean baseline BEFORE mutating.
        # "It fails on the old broken artifact" is not evidence the guard watches this defect.
        if not baseline[target].ok:
            return Result(name, target, BAD_FIXTURE, (),
                          "baseline already violates %s: %s" % (target, baseline[target].detail))
        try:
            mutant = mutation.apply(text)
        except Exception as exc:  # noqa: BLE001 - prove() must never raise
            return Result(name, target, UNMEASURED, (), "apply() raised: %s" % exc)
        if mutant is None:
            return Result(name, target, NOT_APPLIED, (),
                          "apply() returned None — the anchor text is absent")
        if mutant == text:
            return Result(name, target, NOT_APPLIED, (),
                          "apply() returned the text unchanged — nothing was mutated")
        try:
            after = extract(mutant, id_registry=registry, kind="final")
        except Exception as exc:  # noqa: BLE001
            return Result(name, target, UNMEASURED, (), "mutant extraction raised: %s" % exc)
        # Compare ok flags ONLY. Never value: reporting values move on any edit while staying ok.
        flipped = {k for k in baseline.keys() & after.keys()
                   if k not in REPORTING_ONLY and baseline[k].ok != after[k].ok}
        if target not in flipped:
            return Result(name, target, VACUOUS, (),
                          "mutation applied but %s stayed ok=True — the guard did not fire" % target)
        collateral = tuple(sorted(flipped - {target}))
        if collateral:
            return Result(name, target, OVERBROAD, collateral, "")
        return Result(name, target, HAS_TEETH, (), "")
    except Exception as exc:  # noqa: BLE001 - the contract is "never raise"
        return Result(name, target, UNMEASURED, (), "internal error: %s" % exc)


def prove(text, mutations=None, *, id_registry=None):
    """Run every mutation against a known-clean `text` and return one Result per mutation.

    Never raises. When `id_registry` is None, the registry is derived from the report's own ids:
    the baseline then resolves by construction, and any fabricated-id mutation still flips the
    fabrication guard. (Explicit registries passed by callers are used verbatim.)
    """
    if mutations is None:
        mutations = MUTATIONS
    registry = id_registry
    if registry is None and _report_ids is not None:
        try:
            own = _report_ids(text)
        except Exception:  # noqa: BLE001
            own = None
        if own:
            registry = frozenset(own)
    try:
        baseline = extract(text, id_registry=registry, kind="final")
        base_exc = None
    except Exception as exc:  # noqa: BLE001 - extract promises not to raise; belt and braces
        baseline, base_exc = None, exc
    return [_prove_one(text, m, baseline, base_exc, registry) for m in mutations]


def coverage(text, mutations=None, *, id_registry=None):
    """Sorted tuple of baseline property names no mutation targets and not in REPORTING_ONLY.

    A property nobody can make fire is indistinguishable from a property that does not work.
    """
    if mutations is None:
        mutations = MUTATIONS
    try:
        baseline = extract(text, id_registry=id_registry, kind="final")
    except Exception:  # noqa: BLE001
        return ()
    targeted = {m.target for m in mutations}
    return tuple(sorted(k for k in baseline if k not in targeted and k not in REPORTING_ONLY))


# --- CLI ----------------------------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Prove every guard can actually fail: mutate a clean fixture, watch it go red.")
    parser.add_argument("--fixture", default=None,
                        help="replay a real cached RESULT_*.md instead of the in-file fixture")
    parser.add_argument("--registry", action="append", default=None,
                        help="id-registry file for video_ids_resolve (repeatable)")
    args = parser.parse_args(argv)

    if args.fixture:
        try:
            with open(args.fixture, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError as exc:
            print("%s  --fixture %s unreadable: %s" % (UNMEASURED, args.fixture, exc))
            return 2
    else:
        text = DEFAULT_FIXTURE

    if args.registry:
        registry = load_id_registry(args.registry)
    elif args.fixture:
        registry = load_id_registry([DEFAULT_REGISTRY])
    else:
        registry = None

    results = prove(text, id_registry=registry)
    width = max(len(o) for o in OUTCOMES)
    for r in results:
        line = "%s  %s  -> %s" % (r.outcome.ljust(width), r.mutation, r.target)
        extras = []
        if r.collateral:
            extras.append("collateral: " + ", ".join(r.collateral))
        if r.detail:
            extras.append(r.detail)
        if extras:
            line += "  (" + "; ".join(extras) + ")"
        print(line)

    gaps = coverage(text, id_registry=registry)
    for gap in gaps:
        print("UNPROVABLE: %s" % gap)

    counts = {o: 0 for o in OUTCOMES}
    for r in results:
        counts[r.outcome] = counts.get(r.outcome, 0) + 1
    print("SUMMARY: %d mutations — %s; coverage %s" % (
        len(results),
        ", ".join("%s=%d" % (o, counts[o]) for o in OUTCOMES if counts[o]),
        "complete" if not gaps else "INCOMPLETE (%d unprovable)" % len(gaps)))

    if counts[UNMEASURED]:
        return 2
    if counts[VACUOUS] or counts[NOT_APPLIED] or counts[BAD_FIXTURE] or gaps:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
