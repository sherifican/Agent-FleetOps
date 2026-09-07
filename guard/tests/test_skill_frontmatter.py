"""Every published SKILL.md must have parseable YAML frontmatter.

Written after GitHub refused to render two skills with
"mapping values are not allowed in this context": an unquoted YAML scalar
containing ": " parses as a nested mapping. The renderer is the only thing
that was checking, and it fails silently to a reader who never scrolls.
"""
import glob
import os

import pytest


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SKILLS = sorted(glob.glob(os.path.join(ROOT, "skills", "*", "SKILL.md")))


def _frontmatter(path):
    text = open(path, encoding="utf-8").read()
    assert text.startswith("---"), f"{path}: no frontmatter block"
    return text.split("---")[1]


def test_there_are_skills_to_check():
    # Positive control: an empty glob would make every test below vacuous.
    assert SKILLS, "no SKILL.md files found; the check would pass by examining nothing"


@pytest.mark.parametrize("path", SKILLS, ids=[p.split(os.sep)[-2] for p in SKILLS])
def test_frontmatter_parses(path):
    yaml = pytest.importorskip("yaml")
    data = yaml.safe_load(_frontmatter(path))
    assert isinstance(data, dict), f"{path}: frontmatter is not a mapping"
    assert data.get("name"), f"{path}: frontmatter has no name"
    assert data.get("description"), f"{path}: frontmatter has no description"


def test_a_colon_in_an_unquoted_description_is_caught():
    yaml = pytest.importorskip("yaml")
    # Negative control: the exact shape that broke the two skills must fail.
    with pytest.raises(yaml.YAMLError):
        yaml.safe_load("name: x\ndescription: before the colon: after the colon\n")

def test_description_is_quoted_without_yaml():
    """A text-level check that survives PyYAML being absent.

    The parse tests above skip without PyYAML, and a skipped test reports the same green
    as a passing one. This one needs no parser: the break was an UNQUOTED description
    containing ": ", so require every description to be quoted whenever it contains one.
    """
    assert SKILLS, "no SKILL.md files found; this check would examine nothing"
    offenders = []
    for path in SKILLS:
        for line in _frontmatter(path).split("\n"):
            if not line.startswith("description:"):
                continue
            value = line[len("description:"):].strip()
            if value[:1] not in ('"', "'") and ": " in value:
                offenders.append(os.path.basename(os.path.dirname(path)))
    assert not offenders, (
        "unquoted description containing ': ' (YAML reads it as a nested mapping): "
        + ", ".join(offenders))
