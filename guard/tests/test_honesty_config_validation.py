"""CLI configuration boundaries, using copied hooks and synthetic turns only.

Creation coverage is independent of load_config/compile_config helper names. The
optional HONESTY_CONFIG_TEST_SOURCE selects an old or deliberately mutated source
copy for discrimination runs; normal runs exercise the tracked public hook.
Neither probe commands nor file-writing commands in transcripts are executed.
"""
from pathlib import Path
import json
import os
import runpy
import shlex
import shutil
import subprocess
import sys
import unicodedata

import pytest


HOOK = Path(__file__).resolve().parents[1] / "honesty_stop_gate.py"
DEFAULT_PROBES = ("pgrep", "ps", "systemctl", "docker", "kubectl", "curl")
REQUIRED_LISTS = ("claim_patterns", "verification_commands", "subjects")
FILE_LISTS = ("write_path_keys", "heredoc_sinks", "arg_sinks")
# Independent Unicode 14.0.0 property fixture, from DerivedCoreProperties.txt:
# https://www.unicode.org/Public/14.0.0/ucd/DerivedCoreProperties.txt
# Keep this independent of the hook so deletion of a production range is caught.
DEFAULT_IGNORABLE_RANGES = tuple(
    tuple(int(part, 16) for part in item.split(".."))
    for item in """00AD..00AD 034F..034F 061C..061C 115F..1160 17B4..17B5
    180B..180D 180E..180E 180F..180F 200B..200F 202A..202E 2060..2064
    2065..2065 2066..206F 3164..3164 FE00..FE0F FEFF..FEFF FFA0..FFA0
    FFF0..FFF8 1BCA0..1BCA3 1D173..1D17A E0000..E0000 E0001..E0001
    E0002..E001F E0020..E007F E0080..E00FF E0100..E01EF E01F0..E0FFF""".split()
)
DEFAULT_IGNORABLE_POINTS = frozenset(
    point for start, end in DEFAULT_IGNORABLE_RANGES for point in range(start, end + 1))
# Each excluded Unicode category has a witness; Cf includes both reported cases.
INVISIBLE_TEXT = [
    pytest.param("\u200b", id="zero-width-space"),
    pytest.param("\ufeff", id="byte-order-mark"),
    pytest.param("\u00a0", id="no-break-space"),
    pytest.param("\u0301", id="nonspacing-mark"),
    pytest.param("\u20dd", id="enclosing-mark"),
    pytest.param("\x00", id="control"),
    pytest.param("\u2028", id="line-separator"),
    pytest.param("\u2029", id="paragraph-separator"),
    pytest.param("".join(map(chr, sorted(DEFAULT_IGNORABLE_POINTS))), id="all-default-ignorables"),
    pytest.param("\u2800", id="blank-braille"),
    pytest.param("\ufffe", id="plane-noncharacter"),
    pytest.param("\ufdd0", id="noncharacter"),
    pytest.param("\ud800", id="surrogate"),
    pytest.param("\ufffc", id="object-replacement"),
]
BAD_LISTS = [
    pytest.param([], id="empty"),
    pytest.param(None, id="null-container"),
    pytest.param({}, id="object-container"),
    pytest.param(True, id="bool-container"),
    pytest.param(7, id="number-container"),
    pytest.param("jobs", id="string-container"),
    pytest.param([None], id="null-element"),
    pytest.param(["valid", None], id="mixed-elements"),
    pytest.param([False], id="bool-element"),
    pytest.param([7], id="number-element"),
    pytest.param([{}], id="object-element"),
    pytest.param([[]], id="list-element"),
    pytest.param([""], id="empty-string"),
    pytest.param([" \t"], id="blank-string"),
    *(pytest.param([case.values[0]], id=case.id) for case in INVISIBLE_TEXT),
]


def prose(text):
    return {"type": "assistant", "message": {"content": [
        {"type": "text", "text": text}]}}


def probe(command, completed=True):
    records = [{"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Bash", "id": "probe-1",
         "input": {"command": command}}]}}]
    if completed:
        records.append({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "probe-1", "content": "synthetic observation"}]}})
    return records


def written(text, channel="Write", path_key="file_path", sink=None):
    if channel == "Write":
        data = {path_key: "/synthetic/report.md", "content": text}
        name = "Write"
    elif channel == "heredoc":
        data = {"command": f"{sink or 'cat'} > /synthetic/report.md <<'END'\n{text}\nEND"}
        name = "Bash"
    else:
        data = {"command": f"{sink or 'echo'} {shlex.quote(text)} > /synthetic/report.md"}
        name = "Bash"
    return {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": name, "id": "write-1", "input": data}]}}


class HookCLI:
    def __init__(self, root):
        self.root = root
        self.hook = root / "hook" / "honesty_stop_gate.py"
        self.hook.parent.mkdir()
        source = Path(os.environ.get("HONESTY_CONFIG_TEST_SOURCE", str(HOOK)))
        shutil.copyfile(source, self.hook)
        self.config = root / "selected.json"
        self.transcript = root / "synthetic.jsonl"
        self.executed = root / "probe-was-executed"
        binaries = root / "bin"
        binaries.mkdir()
        for name in (*DEFAULT_PROBES, "probecheck"):
            command = binaries / name
            command.write_text(
                "#!/bin/sh\nprintf unexpected > " + shlex.quote(str(self.executed)) + "\nexit 97\n",
                encoding="utf-8")
            command.chmod(0o755)
        home = root / "cli-home"
        home.mkdir()
        self.env = {"PATH": str(binaries), "HOME": str(home), "USERPROFILE": str(home),
                    "LC_ALL": "C.UTF-8", "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1",
                    "HONESTY_GATE_CONFIG": str(self.config)}

    def settings(self, value):
        self.config.write_text(json.dumps(value), encoding="utf-8")

    def run(self, *args, records=None):
        payload = ""
        if records is not None:
            human = {"type": "user", "message": {"content": "Synthetic test turn."}}
            self.transcript.write_text(
                "\n".join(json.dumps(row) for row in [human, *records]) + "\n", encoding="utf-8")
            payload = json.dumps({"transcript_path": str(self.transcript)})
        result = subprocess.run([sys.executable, str(self.hook), *args], env=self.env,
                                input=payload, text=True, capture_output=True, timeout=15)
        assert not self.executed.exists(), "A command named in a synthetic transcript was executed"
        return result

    def rejected(self, field):
        result = self.run("--check-config")
        output = result.stdout + result.stderr
        assert result.returncode == 1, output
        assert "check-config: PROBLEMS" in result.stdout, output
        assert field in output, f"Refusal must identify {field}: {output}"
        assert "check-config: OK" not in output, output
        assert "Traceback" not in output, output
        return output

    def accepted(self):
        result = self.run("--check-config")
        assert result.returncode == 0, result.stdout + result.stderr
        assert "check-config: OK" in result.stdout, result.stdout
        assert "PROBLEMS" not in result.stdout, result.stdout

    def blocked(self, records, claim="still running"):
        result = self.run(records=records)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "Traceback" not in result.stderr, result.stderr
        assert result.stdout.strip(), "Expected blocking JSON for an unbacked claim; hook returned no verdict"
        verdict = json.loads(result.stdout)
        assert verdict["decision"] == "block", verdict
        assert claim in verdict["reason"], verdict
        assert "CANNOT CHECK" not in verdict["reason"], verdict

    def quiet(self, records):
        result = self.run(records=records)
        assert result.returncode == 0, result.stdout + result.stderr
        assert not result.stdout.strip(), result.stdout
        assert "Traceback" not in result.stderr, result.stderr


@pytest.fixture
def cli(tmp_path):
    return HookCLI(tmp_path)


def test_unicode_visibility_covers_derived_ranges_and_assigned_positive_controls():
    """Exhaust every DICP point; sample each assigned L/N/P/S category per plane.

    Derive the negative corpus from the full property ranges, not five reported
    glyphs. First/middle/last assigned points in every category/plane bucket
    exercise BMP and supplementary scripts and symbols without a font dependency.
    """
    hook = runpy.run_path(os.environ.get("HONESTY_CONFIG_TEST_SOURCE", str(HOOK)))
    visible = hook["_has_visible_text"]
    negatives = DEFAULT_IGNORABLE_POINTS | {0x2800, 0xFFFC, 0x13441, 0x13442, 0x1D159}
    accepted_negatives = [f"U+{point:04X}" for point in sorted(negatives) if visible(chr(point))]
    assert not accepted_negatives, accepted_negatives
    assert hook["_DEFAULT_IGNORABLE_RANGES"] == DEFAULT_IGNORABLE_RANGES
    buckets = {}
    for point in range(sys.maxunicode + 1):
        category = unicodedata.category(chr(point))
        if category[0] in "LNPS" and point not in negatives:
            buckets.setdefault((category, point >> 16), []).append(point)
    assert {category[0] for category, _ in buckets} == set("LNPS")
    assert any(plane > 0 for _, plane in buckets)
    for points in buckets.values():
        for point in (points[0], points[len(points) // 2], points[-1]):
            assert visible(chr(point)), f"assigned positive control U+{point:04X}"
    # Refusing a blank value must not strip formatting from a visible value.
    assert visible("A" + "".join(map(chr, sorted(negatives))))


@pytest.mark.parametrize("field", REQUIRED_LISTS + FILE_LISTS)
@pytest.mark.parametrize("value", BAD_LISTS)
def test_malformed_lists_are_refused_and_fall_back_per_field(cli, field, value):
    cli.settings({"verification_commands": ["jobs"], field: value})
    cli.rejected(field)
    channel = {"write_path_keys": "Write", "heredoc_sinks": "heredoc", "arg_sinks": "arg"}.get(field)
    claim = "The deploy is still running."
    record = written(claim, channel) if channel else prose(claim)
    cli.blocked([record])
    cli.quiet(probe("jobs deploy") + [record])


@pytest.mark.parametrize("value", [None, False, 8, {}, [], "", " \t", *INVISIBLE_TEXT])
def test_malformed_completion_pattern_is_refused_and_falls_back(cli, value):
    cli.settings({"verification_commands": ["jobs"], "completion_pattern": value})
    cli.rejected("completion_pattern")
    # Subjectless in-turn completion is permitted only by the completion rule.
    cli.quiet([prose("The edits are finished.")])
    cli.blocked([prose("The deploy has finished.")], claim="has finished")
    cli.quiet(probe("jobs deploy") + [prose("The deploy has finished.")])


@pytest.mark.parametrize("value", [None, False, 8, {}, "the", [None], ["the", None], [3], [[]], [{}]])
def test_malformed_non_subjects_is_refused_and_falls_back(cli, value):
    cli.settings({"verification_commands": ["jobs"], "non_subjects": value})
    cli.rejected("non_subjects")
    cli.blocked([prose("The deploy is still running.")])
    cli.quiet(probe("jobs deploy") + [prose("The deploy is still running.")])


@pytest.mark.parametrize("field", (*REQUIRED_LISTS, "completion_pattern"))
def test_bad_regex_refusal_does_not_echo_the_supplied_pattern(cli, field):
    marker = "CONFIG_VALUE_MUST_NOT_BE_ECHOED["
    cli.settings({"verification_commands": ["jobs"], field: marker if field == "completion_pattern" else [marker]})
    output = cli.rejected(field)
    assert marker not in output, output
    cli.blocked([prose("The deploy is still running.")])
    cli.quiet(probe("jobs deploy") + [prose("The deploy is still running.")])


@pytest.mark.parametrize("field,pattern", [
    ("claim_patterns", "(?i)still running"),
    ("verification_commands", "(?i)jobs"),
    ("subjects", "(?i)deploy"),
])
def test_validation_compiles_the_wrapped_expression(cli, field, pattern):
    # Each fragment compiles alone; its global flags fail inside the gate's wrapper.
    cli.settings({"verification_commands": ["jobs"], field: [pattern]})
    cli.rejected(field)
    cli.blocked([prose("The deploy is still running.")])
    cli.quiet(probe("jobs deploy") + [prose("The deploy is still running.")])


@pytest.mark.parametrize("field", ["claim_patterns", "verification_commands", "subjects", "completion_pattern", "non_subjects"])
def test_one_refused_field_preserves_valid_custom_choices(cli, field):
    config = {"subjects": [r"celery[\w-]*"], "verification_commands": ["probecheck"],
              "claim_patterns": ["is available"], "non_subjects": []}
    config[field] = None
    cli.settings(config)
    cli.rejected(field)
    subject = "deploy" if field == "subjects" else "celery"
    phrase = "is still running" if field == "claim_patterns" else "is available"
    command = "jobs" if field == "verification_commands" else "probecheck"
    record = prose(f"The {subject} {phrase}.")
    cli.blocked([record], claim=phrase)
    cli.quiet(probe(f"{command} {subject}") + [record])
    cli.blocked(probe(f"{command} {subject}2") + [record], claim=phrase)


def test_multiple_refusals_do_not_hide_each_other_or_replace_valid_choices(cli):
    cli.settings({"subjects": [r"celery[\w-]*"], "verification_commands": ["probecheck"],
                  "claim_patterns": ["is available"], "completion_pattern": None, "non_subjects": [None]})
    output = cli.rejected("completion_pattern")
    assert "non_subjects" in output, output
    cli.blocked([prose("The celery is available.")], claim="is available")
    cli.quiet(probe("probecheck celery") + [prose("The celery is available.")])


@pytest.mark.parametrize("document", [None, [], "settings", 2, False])
def test_nonobject_config_document_is_refused_with_defaults_retained(cli, document):
    cli.settings(document)
    cli.rejected("configuration")
    cli.blocked([prose("The deploy is still running.")])
    cli.quiet(probe("jobs deploy") + [prose("The deploy is still running.")])


@pytest.mark.parametrize("document", [b'{"subjects": [', b'\xff\xfeinvalid-json'])
def test_unparseable_config_document_is_refused_without_echo(cli, document):
    cli.config.write_bytes(document)
    output = cli.rejected("configuration")
    assert "invalid-json" not in output, output
    cli.blocked([prose("The deploy is still running.")])
    cli.quiet(probe("jobs deploy") + [prose("The deploy is still running.")])


@pytest.mark.parametrize("kind", ["missing", "directory"])
def test_explicit_unavailable_config_is_refused(cli, kind):
    if kind == "directory":
        cli.config.mkdir()
    cli.rejected("configuration")
    cli.blocked([prose("The deploy is still running.")])


def test_unreadable_selected_config_is_refused(cli):
    cli.settings({})
    cli.config.chmod(0)
    try:
        if os.access(cli.config, os.R_OK):
            pytest.skip("This account can still read a mode-000 fixture")
        cli.rejected("configuration")
        cli.blocked([prose("The deploy is still running.")])
    finally:
        cli.config.chmod(0o600)


@pytest.mark.parametrize("mode", ["absent-default", "empty-object", "empty-non-subjects", "unknown-key"])
def test_valid_defaults_and_omitted_keys_remain_valid(cli, mode):
    if mode == "absent-default":
        cli.env.pop("HONESTY_GATE_CONFIG")
    else:
        cli.settings({"non_subjects": []} if mode == "empty-non-subjects" else
                     {"not_a_supported_key": "ignored"} if mode == "unknown-key" else {})
    if mode == "unknown-key":
        cli.rejected("not_a_supported_key")
    else:
        cli.accepted()
    cli.blocked([prose("The deploy is still running.")])
    cli.quiet(probe("jobs deploy") + [prose("The deploy is still running.")])


def test_valid_narrowed_vocabulary_and_probe_are_not_reset(cli):
    cli.settings({"claim_patterns": ["is available"], "subjects": [r"celery[\w-]*"],
                  "verification_commands": ["probecheck"], "non_subjects": []})
    cli.accepted()
    record = prose("The celery is available.")
    cli.blocked([record], claim="is available")
    cli.quiet(probe("probecheck celery") + [record])
    cli.blocked(probe("pgrep -af celery") + [record], claim="is available")
    cli.blocked(probe("probecheck celery", completed=False) + [record], claim="is available")


@pytest.mark.parametrize("field,channel,kwargs", [
    ("write_path_keys", "Write", {"path_key": "target_file"}),
    ("heredoc_sinks", "heredoc", {"sink": "capture"}),
    ("arg_sinks", "arg", {"sink": "record"}),
])
def test_valid_file_scan_names_survive_an_unrelated_refusal(cli, field, channel, kwargs):
    name = kwargs.get("path_key") or kwargs["sink"]
    cli.settings({"verification_commands": ["jobs"], field: [name], "completion_pattern": None})
    cli.rejected("completion_pattern")
    record = written("The deploy is still running.", channel, **kwargs)
    cli.blocked([record])
    cli.quiet(probe("jobs deploy") + [record])


def test_syntactically_valid_never_match_remains_a_semantic_limit(cli):
    cli.settings({"claim_patterns": ["(?!)"], "verification_commands": ["jobs"]})
    cli.accepted()
    cli.quiet([prose("The deploy is still running.")])


def test_unresolved_probe_keeps_the_standing_negative_control_contract(cli):
    name = "nonexistent-verifier-9f3a"
    cli.settings({"verification_commands": [name]})
    output = cli.rejected("verification command")
    assert f"verification command '{name}' does not resolve" in output, output


def test_printf_claim_uses_arg_sink_fallback(cli):
    cli.settings({"verification_commands": ["jobs"], "arg_sinks": [None]})
    cli.rejected("arg_sinks")
    record = written("The deploy is still running.", "arg", sink="printf")
    cli.blocked([record])
    cli.quiet(probe("jobs deploy") + [record])


def test_empty_non_subjects_and_malformed_fallback_are_observably_different(cli):
    # The existing filename rule binds the probe to 'both'. With default exclusions
    # the prose is subjectless and blocks; removing exclusions makes that same
    # recorded probe sufficient. This tests the config value, not just its shape.
    records = probe("jobs both.log") + [prose("Both are still running.")]
    cli.settings({"verification_commands": ["jobs"]})
    cli.accepted()
    cli.blocked(records)
    cli.settings({"verification_commands": ["jobs"], "non_subjects": []})
    cli.accepted()
    cli.quiet(records)
    cli.settings({"verification_commands": ["jobs"], "non_subjects": [None]})
    cli.rejected("non_subjects")
    cli.blocked(records)


def test_blank_non_subject_string_is_valid_and_does_not_crash(cli):
    cli.settings({"verification_commands": ["jobs"], "non_subjects": [""]})
    cli.accepted()
    cli.blocked([prose("The deploy is still running.")])
    cli.quiet(probe("jobs deploy") + [prose("The deploy is still running.")])


def test_well_typed_wrong_path_key_remains_an_adopter_semantic_limit(cli):
    cli.settings({"verification_commands": ["jobs"], "write_path_keys": ["different_field"]})
    cli.accepted()
    # A syntactically valid key cannot establish the adopting harness's schema.
    cli.quiet([written("The deploy is still running.")])
    cli.blocked([written("The deploy is still running.", path_key="different_field")])
    cli.blocked([prose("The deploy is still running.")])


@pytest.mark.parametrize("field,value", [
    ("write_skip_suffixes", "x"),
    ("write_skip_paths", None),
    ("verify_hint", None),
])
def test_malformed_skip_containers_and_hint_keep_runtime_defaults(cli, field, value):
    valid = {"verification_commands": ["jobs"], "subjects": ["celery"]}
    claim = "The celery is still running."
    report = written(claim)
    source = written(claim)
    source["message"]["content"][0]["input"]["file_path"] = "/synthetic/source.py"
    changelog = written(claim)
    changelog["message"]["content"][0]["input"]["file_path"] = "/synthetic/CHANGELOG"
    turns = [[prose(claim)], [report], [source], [changelog],
             probe("jobs celery") + [report]]
    cli.settings(valid)
    baseline = [cli.run(records=turn) for turn in turns]
    # Positive block and skip/probe controls make the comparison meaningful.
    assert all(json.loads(result.stdout)["decision"] == "block" for result in baseline[:2])
    assert all(result.stdout == "" for result in baseline[2:])
    assert all(result.returncode == 0 and result.stderr == "" for result in baseline)
    cli.settings({**valid, field: value})
    cli.rejected(field)
    for turn, expected in zip(turns, baseline):
        actual = cli.run(records=turn)
        assert (actual.returncode, actual.stdout, actual.stderr) == (
            expected.returncode, expected.stdout, expected.stderr)


def test_lone_surrogate_probe_reports_refusal_without_traceback(cli):
    pattern = "\ud800probe\\b"
    cli.settings({"verification_commands": [pattern]})
    output = cli.rejected("verification command")
    assert ascii(pattern) in output, output


@pytest.mark.parametrize("value", ["", " ", " \t\n", "\u2003", *INVISIBLE_TEXT])
def test_blank_verify_hint_is_refused_with_byte_identical_runtime_fallback(cli, value):
    valid = {"verification_commands": ["jobs"], "subjects": ["celery"]}
    turns = [[prose("The celery is still running.")],
             [written("The celery is still running.")],
             probe("jobs celery") + [prose("The celery is still running.")]]
    cli.settings(valid)
    expected = [cli.run(records=turn) for turn in turns]
    assert all(json.loads(result.stdout)["decision"] == "block" for result in expected[:2])
    assert expected[2].stdout == ""
    cli.settings({**valid, "verify_hint": value})
    cli.rejected("verify_hint")
    for turn, baseline in zip(turns, expected):
        actual = cli.run(records=turn)
        assert (actual.returncode, actual.stdout, actual.stderr) == (
            baseline.returncode, baseline.stdout, baseline.stderr)


def test_every_unknown_key_is_reported_with_nearest_suggestion_and_ignored_at_runtime(cli):
    valid = {"verification_commands": ["jobs"], "subjects": ["celery"]}
    unknown = {"verify_hnt": "VALUE_MUST_NOT_APPEAR", "subjets": [],
               "unrelated_zzzz": {}, "_notes": "not a documented comment key",
               "$schema": "not a documented schema convention", "_refused": ["injected"]}
    turns = [[prose("The celery is still running.")],
             [written("The celery is still running.")],
             probe("jobs celery") + [prose("The celery is still running.")]]
    cli.settings(valid)
    expected = [cli.run(records=turn) for turn in turns]
    assert all(json.loads(result.stdout)["decision"] == "block" for result in expected[:2])
    assert expected[2].stdout == ""
    cli.settings({**valid, **unknown})
    output = cli.rejected("verify_hnt")
    for key in unknown:
        line = next(line for line in output.splitlines() if repr(key) in line)
        assert "unknown" in line, line
        if key in {"verify_hnt", "subjets"}:
            assert {"verify_hnt": "verify_hint", "subjets": "subjects"}[key] in line, line
        if key == "unrelated_zzzz":
            assert "did you mean" not in line, line
    assert "VALUE_MUST_NOT_APPEAR" not in output
    assert "injected" not in output
    for turn, baseline in zip(turns, expected):
        actual = cli.run(records=turn)
        assert (actual.returncode, actual.stdout, actual.stderr) == (
            baseline.returncode, baseline.stdout, baseline.stderr)


def test_all_documented_optional_keys_and_comment_conventions_remain_valid(cli):
    # The full example carries every supported optional field, plus // comments.
    # Other repository config examples use exactly _comment, not arbitrary _ keys.
    settings = json.loads((HOOK.parent / "honesty_gate.config.example.json").read_text())
    settings["_comment"] = "Repository config annotation convention"
    settings["//additional explanation"] = "An ignored JSON comment"
    cli.settings(settings)
    cli.accepted()
    cli.blocked([prose("The deploy is still running.")])
    cli.quiet(probe("jobs deploy") + [prose("The deploy is still running.")])


@pytest.mark.parametrize("value", INVISIBLE_TEXT)
@pytest.mark.parametrize("field", ["write_tools", "write_skip_paths"])
def test_invisible_file_scan_entries_are_refused(cli, field, value):
    entry = {"Write": [value]} if field == "write_tools" else [value]
    cli.settings({"verification_commands": ["jobs"], field: entry})
    cli.rejected(field)
    record = written("The deploy is still running.")
    cli.blocked([record])
    cli.quiet(probe("jobs deploy") + [record])


def test_visible_verify_hint_preserves_zero_width_character(cli):
    hint = "Run jobs\u200b deploy."
    cli.settings({"verification_commands": ["jobs"], "verify_hint": hint})
    cli.accepted()
    records = [prose("The deploy is still running.")]
    cli.blocked(records)
    result = cli.run(records=records)
    assert hint in json.loads(result.stdout)["reason"]
    cli.quiet(probe("jobs deploy") + records)


def test_unicode_assignment_delta_has_stable_verdicts():
    """Every newly assigned point from UCD 14 to 16, grouped by Unicode block."""
    fixture = json.loads((HOOK.parent / 'tests/fixtures/unicode_visibility_delta.json').read_text())
    visible = runpy.run_path(os.environ.get('HONESTY_CONFIG_TEST_SOURCE', str(HOOK)))['_has_visible_text']
    rejected_categories = {'Cc', 'Cf', 'Cs', 'Zs', 'Zl', 'Zp', 'Mn', 'Me'}
    count = 0
    for block in fixture['blocks']:
        for start, end, category in block['ranges']:
            for point in range(start, end + 1):
                expected = (category not in rejected_categories and point not in DEFAULT_IGNORABLE_POINTS
                            and point not in {0x13441, 0x13442})
                assert visible(chr(point)) == expected, (block['block'], hex(point), category)
                count += 1
    assert count == fixture['count'] == 10301
    assert len(fixture['blocks']) == 40
    noncharacters = set(range(0xFDD0, 0xFDF0)) | {
        (plane << 16) + tail for plane in range(17) for tail in (0xFFFE, 0xFFFF)}
    assert len(noncharacters) == 66
    assert all(not visible(chr(point)) for point in noncharacters)
    assert visible('\u0378'), 'unassigned is accepted unless independently excluded'


@pytest.mark.parametrize('text', ['🫨', '\U00011f04\U00011f05', '\U00031350', '\U0001e4d0', '\u0378', '\ue000', '\U000f0000', '\U00100000', '\U00010940', '\U00011db0', '\U00016ea0', '\U0001e6c0'])
def test_new_or_unassigned_text_keeps_operator_config(cli, text):
    cli.settings({'verification_commands': ['jobs'], 'subjects': ['celery', text], 'verify_hint': text})
    cli.accepted()
    result = cli.run(records=[prose('The celery is still running.')])
    assert text in json.loads(result.stdout)['reason']
    cli.blocked([prose('The celery is still running.')])
    cli.quiet(probe('jobs celery') + [prose('The celery is still running.')])


@pytest.mark.parametrize('point', [0x13441, 0x13442])
def test_egyptian_blank_config_is_refused(cli, point):
    cli.settings({'verification_commands': ['jobs'], 'subjects': ['deploy', chr(point)],
                  'verify_hint': chr(point)})
    cli.rejected('subjects')
    cli.rejected('verify_hint')


@pytest.mark.parametrize('point', range(0x13443, 0x13447))
def test_egyptian_lost_signs_remain_text(cli, point):
    cli.settings({'verification_commands': ['jobs'], 'verify_hint': chr(point)})
    cli.accepted()


def test_null_notehead_is_not_config_text(cli):
    cli.settings({'verification_commands': ['jobs'], 'verify_hint': '\U0001d159'})
    cli.rejected('verify_hint')


@pytest.mark.parametrize('text', ['\U0001d165', '\u093e', '\u2400', '\u2422', '\u2205'])
def test_drawn_marks_and_blank_named_symbols_remain_text(cli, text):
    cli.settings({'verification_commands': ['jobs'], 'verify_hint': text})
    cli.accepted()
