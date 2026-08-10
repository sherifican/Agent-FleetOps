#!/usr/bin/env python3
"""Check that the research-output contract says the same thing everywhere."""
from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
import os
import re
import sys


# Repo-root-anchored ABSOLUTE paths, computed at runtime — the unit gate asserts isabs()
# because a cwd-relative surface silently reads the wrong file when invoked from elsewhere.
# (A sanitization pass once made these "./"-relative; that exact gate caught it.)
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SURFACES = [
    ("validator", os.path.join(_ROOT, "check_leg_contract.py")),
    ("addendum", os.path.join(_ROOT, "ACTIONABLE_ADDENDUM.md")),
    ("rollup", os.path.join(_ROOT, "actionable_rollup.py")),
    ("preamble", os.path.join(_ROOT, "stage_video_research.py")),
]

VOCABS = ("ACTIONS", "BUCKETS", "PRIOS", "RATINGS", "PROJECTS")
SENTINELS = {
    ("rollup", "BUCKETS"): {"Unsorted"},
    ("rollup", "PRIOS"): {"—", "-", "--"},
}


@dataclass(frozen=True)
class Reading:
    surface: str
    vocab: str
    values: frozenset[str] | None
    error: str = ""


def _empty_readings(surface: str, error: str) -> list[Reading]:
    return [Reading(surface, vocab, None, error) for vocab in VOCABS]


def _literal_strings(node: ast.AST) -> frozenset[str] | None:
    try:
        value = ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError):
        return None
    if not isinstance(value, (list, tuple, set)) or not all(isinstance(v, str) for v in value):
        return None
    return frozenset(value)


def _markdown_region(text: str, matcher) -> str:
    headings = list(re.finditer(r"^(#{1,6})\s+(.+?)\s*$", text, re.M))
    for index, heading in enumerate(headings):
        if not matcher(heading.group(2)):
            continue
        level = len(heading.group(1))
        end = len(text)
        for following in headings[index + 1:]:
            if len(following.group(1)) <= level:
                end = following.start()
                break
        # The compact form may put values directly in the heading (for example,
        # "### Priority — exactly one of: `P0` · `P1` · `P2`").
        return text[heading.start():end]
    return ""


def _markdown_readings(surface: str, text: str) -> list[Reading]:
    stripped = re.sub(r"~~.*?~~", "", text, flags=re.S)
    actions = _markdown_region(
        stripped, lambda heading: "action" in heading.lower() and "closed vocabulary" in heading.lower())
    buckets = _markdown_region(stripped, lambda heading: "bucket" in heading.lower())
    prios = _markdown_region(stripped, lambda heading: "priority" in heading.lower())

    tokens = lambda region: re.findall(r"`([^`]+)`", region)
    relevance = re.findall(
        r"^\s*RELEVANCE:\s*([A-Za-z][A-Za-z-]*)\s*=\s*<([^>]+)>", stripped, re.M)
    ratings = {rating.strip() for _, choices in relevance for rating in choices.split("|")}
    values = {
        "ACTIONS": {token for token in tokens(actions) if re.fullmatch(r"[A-Z]{3,8}", token)},
        "BUCKETS": {
            token for token in tokens(buckets)
            if re.fullmatch(r"[A-Za-z][A-Za-z-]+", token) and any(ch.isupper() for ch in token)
        },
        "PRIOS": {token for token in tokens(prios) if re.fullmatch(r"P[0-9]", token)},
        "RATINGS": ratings,
        "PROJECTS": {project for project, _ in relevance},
    }
    return [Reading(surface, vocab, frozenset(values[vocab]) if values[vocab] else None) for vocab in VOCABS]


def _preamble_projects(text: str) -> frozenset[str] | None:
    """Extract the explicitly named project list in the generated-dispatch prose."""
    match = re.search(r"Then rate to each Fleet project:\s*(.*?)(?:\.|\n)", text, re.S)
    if not match:
        return None
    projects = re.findall(r"\*\*([^*]+)\*\*", match.group(1))
    return frozenset(projects) if projects else None


def read_surface(name: str, path: str) -> list[Reading]:
    """Read one surface without importing it, returning errors as data."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        return _empty_readings(name, str(exc))

    if path.lower().endswith(".md"):
        readings = _markdown_readings(name, text)
    else:
        try:
            tree = ast.parse(text, filename=path)
        except SyntaxError as exc:
            return _empty_readings(name, f"{exc.__class__.__name__}: {exc}")
        values: dict[str, frozenset[str] | None] = {vocab: None for vocab in VOCABS}
        banned: frozenset[str] | None = None
        for statement in tree.body:
            if not isinstance(statement, ast.Assign):
                continue
            literal = _literal_strings(statement.value)
            for target in statement.targets:
                if not isinstance(target, ast.Name):
                    continue
                if target.id in values and literal is not None:
                    values[target.id] = literal
                if target.id == "BANNED":
                    try:
                        banned_value = ast.literal_eval(statement.value)
                    except (ValueError, TypeError, SyntaxError):
                        continue
                    if isinstance(banned_value, dict) and all(isinstance(key, str) for key in banned_value):
                        banned = frozenset(banned_value)
        if name == "preamble" and values["PROJECTS"] is None:
            values["PROJECTS"] = _preamble_projects(text)
        readings = [Reading(name, vocab, values[vocab]) for vocab in VOCABS]
        if banned is not None:
            readings.append(Reading(name, "__BANNED__", banned))

    # Private source lines let compare() distinguish prescriptions from documented rejections.
    readings.append(Reading(name, "__TEXT__", frozenset(text.splitlines())))
    return readings


def _usable(readings: list[Reading], vocab: str) -> list[Reading]:
    return [reading for reading in readings if reading.vocab == vocab and not reading.error and reading.values is not None]


def _normalised(reading: Reading) -> frozenset[str]:
    return (reading.values or frozenset()) - SENTINELS.get((reading.surface, reading.vocab), set())


def _pair_differences(vocab: str, left: Reading, right: Reading) -> list[str]:
    left_values, right_values = _normalised(left), _normalised(right)
    messages = []
    only_left = sorted(left_values - right_values)
    only_right = sorted(right_values - left_values)
    if only_left:
        messages.append(f"{vocab}: {left.surface} has {', '.join(only_left)} not in {right.surface}")
    if only_right:
        messages.append(f"{vocab}: {right.surface} has {', '.join(only_right)} not in {left.surface}")
    return messages


def _banned_prescriptions(readings: list[Reading]) -> list[str]:
    banned = next((reading.values for reading in readings
                   if reading.surface == "validator" and reading.vocab == "__BANNED__"), frozenset())
    messages = []
    exempt = re.compile(r"NOT valid|not valid|do NOT|Rejected|banned|instead of", re.I)
    for surface in ("addendum", "preamble"):
        lines = next((reading.values for reading in readings
                      if reading.surface == surface and reading.vocab == "__TEXT__"), frozenset())
        for line in lines or ():
            if "~~" in line or exempt.search(line):
                continue
            for verb in banned or ():
                if re.search(rf"\b{re.escape(verb)}\b", line):
                    message = f"{surface} prescribes banned verb {verb!r}"
                    if message not in messages:
                        messages.append(message)
    return messages


def compare(readings: list[Reading]) -> list[str]:
    """Return all independently measurable contract disagreements."""
    messages: list[str] = []
    for vocab in VOCABS:
        declared = _usable(readings, vocab)
        for index, left in enumerate(declared):
            for right in declared[index + 1:]:
                messages.extend(_pair_differences(vocab, left, right))

    projects, buckets = _usable(readings, "PROJECTS"), _usable(readings, "BUCKETS")
    for project_reading in projects:
        for bucket_reading in buckets:
            messages.extend(_pair_differences("PROJECTS/BUCKETS", project_reading, bucket_reading))
    messages.extend(_banned_prescriptions(readings))
    return messages


def main(argv=None) -> int:
    del argv  # The public hook is intentionally argument-free for use from tests and CI.
    readings = [reading for name, path in SURFACES for reading in read_surface(name, path)]
    errors: dict[str, str] = {}
    for reading in readings:
        if reading.error:
            errors.setdefault(reading.surface, reading.error)
    disagreements = compare(readings)

    for surface, error in errors.items():
        print(f"UNMEASURED: {surface} — {error}")
    for message in disagreements:
        print(message)

    for vocab in VOCABS:
        if any(message.startswith(vocab + ":") or message.startswith("PROJECTS/BUCKETS:") and vocab in {"PROJECTS", "BUCKETS"}
               for message in disagreements):
            continue
        declared = _usable(readings, vocab)
        if declared:
            print(f"{vocab}: {len(declared)} surfaces agree ({len(_normalised(declared[0]))} values)")

    if disagreements:
        return 1
    return 2 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
