"""Claude-authored gate for guard/research_properties.py. The implementer must NOT edit this file.

Every property gets BOTH a clean case (ok=True) and a planted-positive case (ok=False). A detector that
has never gone red on a known positive is unproven — that rule is the whole reason this subsystem exists,
so the gate itself obeys it.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from guard.research_properties import (  # noqa: E402
    Property,
    extract,
    extract_file,
    load_id_registry,
)

REAL_ID = "dQw4w9WgXcQ"          # in the registry below
FAKE_ID = "aNiBFVjvoqk"          # NOT in the registry — a real fabrication from 2026-07-31
REGISTRY = frozenset({REAL_ID, "NgAglRc_ccs", "ro5FHh_voqk"})

CLEAN = f"""# RESULT — a clean leg report

Source: https://youtu.be/{REAL_ID}

RELEVANCE: HomeLLM = NONE
RELEVANCE: ParaKit = NONE
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


def props(text, registry=REGISTRY):
    return extract(text, id_registry=registry)


def test_returns_property_objects_keyed_by_name():
    p = props(CLEAN)
    assert isinstance(p, dict) and p
    for key, val in p.items():
        assert isinstance(val, Property), f"{key} is not a Property"
        assert val.name == key, f"{key} keyed under the wrong name ({val.name})"


EXPECTED_KEYS = {
    "relevance_lines_present", "relevance_values_valid", "relevance_calibrated",
    "table_present", "row_count", "actions_in_vocab", "no_banned_verbs",
    "buckets_in_vocab", "priorities_in_vocab", "video_ids_resolve",
    "word_count", "has_source_link",
}


def test_all_twelve_properties_always_present_even_on_garbage():
    for text in (CLEAN, "", "not a report at all", "|||\n|---|\n", "\x00\x01binary"):
        p = props(text)
        assert EXPECTED_KEYS <= set(p), f"missing {EXPECTED_KEYS - set(p)} for {text[:20]!r}"


def test_clean_report_satisfies_every_judging_property():
    p = props(CLEAN)
    for key in EXPECTED_KEYS:
        assert p[key].ok, f"clean report failed {key}: {p[key].detail}"


def test_extract_never_raises():
    for text in ("", "\x00", "|" * 5000, "RELEVANCE: = \n", "a\n" * 10000):
        extract(text, id_registry=REGISTRY)  # must not raise


# --- planted positives: one per property -----------------------------------------------------------

def test_relevance_lines_present_goes_red_when_one_is_dropped():
    text = CLEAN.replace("RELEVANCE: Memory = NONE\n", "")
    p = props(text)
    assert not p["relevance_lines_present"].ok
    assert "Memory" in p["relevance_lines_present"].detail


def test_relevance_values_valid_goes_red_on_a_bogus_rating():
    p = props(CLEAN.replace("Fleet-Ops = HIGH", "Fleet-Ops = CRITICAL"))
    assert not p["relevance_values_valid"].ok


def test_relevance_calibrated_goes_red_when_everything_is_high():
    text = CLEAN
    for proj in ("HomeLLM", "ParaKit", "Fleet-Ops", "Tooling-Infra", "Research-Pipeline", "Memory"):
        text = text.replace(f"{proj} = NONE", f"{proj} = HIGH")
        text = text.replace(f"{proj} = MED", f"{proj} = HIGH")
        text = text.replace(f"{proj} = LOW", f"{proj} = HIGH")
    p = props(text)
    assert not p["relevance_calibrated"].ok, "all-HIGH must read as uncalibrated"


def test_relevance_calibrated_stays_green_with_a_low_present():
    assert props(CLEAN)["relevance_calibrated"].ok


def test_table_present_goes_red_when_the_table_is_gone():
    text = CLEAN.split("## §5")[0]
    assert not props(text)["table_present"].ok


def test_table_present_accepts_an_explicit_none_declaration():
    text = CLEAN.split("## §5")[0] + "\n## §5 — ACTIONABLE ITEMS\n\nNONE — nothing in this video.\n"
    assert props(text)["table_present"].ok


def test_row_count_counts_data_rows_not_separators():
    assert props(CLEAN)["row_count"].value == 2


def test_row_count_reports_zero_without_judging():
    text = CLEAN.split("| uv |")[0]
    p = props(text)
    assert p["row_count"].value == 0
    assert p["row_count"].ok, "row_count reports, it does not judge"


def test_actions_in_vocab_goes_red_on_an_unknown_verb():
    p = props(CLEAN.replace("| GET |", "| YOINK |"))
    assert not p["actions_in_vocab"].ok


def test_no_banned_verbs_goes_red_on_TRY_and_fires_apart_from_actions_in_vocab():
    p = props(CLEAN.replace("| GET |", "| TRY |"))
    assert not p["no_banned_verbs"].ok, "TRY is a banned synonym"
    assert "TRY" in str(p["no_banned_verbs"].value) or "TRY" in p["no_banned_verbs"].detail


def test_buckets_in_vocab_goes_red_on_an_invented_bucket():
    p = props(CLEAN.replace("| Tooling-Infra | GET |", "| DevOps | GET |"))
    assert not p["buckets_in_vocab"].ok


def test_priorities_in_vocab_goes_red_on_P3():
    p = props(CLEAN.replace("| P1 |", "| P3 |"))
    assert not p["priorities_in_vocab"].ok


def test_priorities_tolerates_decoration():
    assert props(CLEAN.replace("| P1 |", "| **P1** |"))["priorities_in_vocab"].ok


# --- the fabrication guard --------------------------------------------------------------------------

def test_video_ids_resolve_goes_red_on_a_fabricated_id():
    p = props(CLEAN.replace(REAL_ID, FAKE_ID))
    assert not p["video_ids_resolve"].ok, "a fabricated id must not pass"
    assert FAKE_ID in p["video_ids_resolve"].value


def test_video_ids_resolve_accepts_ids_containing_underscores():
    text = CLEAN + "\nAlso RESULT_some-slug_NgAglRc_ccs and https://youtu.be/ro5FHh_voqk\n"
    p = props(text)
    assert p["video_ids_resolve"].ok, f"underscore ids mis-parsed: {p['video_ids_resolve'].detail}"


def test_video_ids_resolve_is_not_silently_skipped_without_a_registry():
    p = extract(CLEAN.replace(REAL_ID, FAKE_ID), id_registry=None)
    v = p["video_ids_resolve"]
    assert v.ok, "no registry means not evaluated, which is not a failure"
    assert v.detail, "an unevaluated check MUST say so — a silent skip is indistinguishable from a pass"
    assert "not evaluated" in v.detail.lower()


def test_video_ids_resolve_does_not_false_positive_on_prose():
    text = CLEAN + "\nThe quick brown fox jumped over an elevenchars word boundary.\n"
    assert props(text)["video_ids_resolve"].ok


def test_has_source_link_is_judged_for_a_FINAL():
    """A hub card links from the FINAL, so there the source URL is load-bearing."""
    stripped = CLEAN.replace(f"https://youtu.be/{REAL_ID}", "(no link)")
    assert extract(CLEAN, id_registry=REGISTRY, kind="final")["has_source_link"].ok
    assert not extract(stripped, id_registry=REGISTRY, kind="final")["has_source_link"].ok


def test_has_source_link_is_NOT_judged_for_a_LEG_report():
    """Measured 2026-07-31: this fired on 36.7% of the leg corpus, but the contract never asks a
    leg for the URL — the brief GIVES it to them. A property that fires on a third of a corpus that
    was never asked to satisfy it is the 'always fires' defect, and it erodes trust in every other
    guard beside it. It must stay green for legs, and SAY that it was not judged."""
    stripped = CLEAN.replace(f"https://youtu.be/{REAL_ID}", "(no link)")
    p = extract(stripped, id_registry=REGISTRY)["has_source_link"]
    assert p.ok, "a leg must not be failed for something it was never asked to do"
    assert "not judged" in p.detail, "an unjudged check MUST say so — a silent skip reads as a pass"


def test_word_count_is_a_plain_token_count():
    p = extract("one two three", id_registry=REGISTRY)
    assert p["word_count"].value == 3 and p["word_count"].ok


# --- determinism + purity ---------------------------------------------------------------------------

def test_extraction_is_deterministic():
    a, b = props(CLEAN), props(CLEAN)
    assert {k: (v.ok, v.value) for k, v in a.items()} == {k: (v.ok, v.value) for k, v in b.items()}


def test_tuple_values_are_sorted_for_stable_output():
    text = CLEAN.replace("| GET |", "| ZULU |").replace("| ADOPT |", "| ALPHA |")
    val = props(text)["actions_in_vocab"].value
    assert list(val) == sorted(val), "tuple values must be sorted so output is byte-stable"


def test_vocabularies_are_imported_not_redeclared():
    """A duplicated vocabulary is the exact drift bug this subsystem exists to prevent."""
    import guard.research_properties as rp
    src = open(rp.__file__, encoding="utf-8").read()
    assert "from check_leg_contract import" in src, "must import the vocabularies from the one owner"
    for name in ("BUCKETS =", "ACTIONS =", "PRIOS =", "RATINGS =", "PROJECTS ="):
        assert name not in src, f"{name} is re-declared locally — import it instead"


# --- registry + file helpers ------------------------------------------------------------------------

def test_load_id_registry_reads_ids_and_skips_missing_files(tmp_path):
    f = tmp_path / "backlog.txt"
    f.write_text(f"some-slug_{REAL_ID}\nanother-slug_NgAglRc_ccs\n", encoding="utf-8")
    reg = load_id_registry([str(f), str(tmp_path / "does-not-exist.txt")])
    assert REAL_ID in reg and "NgAglRc_ccs" in reg


def test_extract_file_reports_unreadable_rather_than_raising(tmp_path):
    p = extract_file(str(tmp_path / "nope.md"))
    assert p["readable"].ok is False


def test_extract_file_matches_extract(tmp_path):
    f = tmp_path / "r.md"
    f.write_text(CLEAN, encoding="utf-8")
    a = extract_file(str(f), id_registry=REGISTRY)
    b = extract(CLEAN, id_registry=REGISTRY)
    assert {k: v.ok for k, v in a.items()} == {k: v.ok for k, v in b.items()}


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
