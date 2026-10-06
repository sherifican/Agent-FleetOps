"""Source-only checks for TUI build and install metadata."""

from fnmatch import fnmatchcase
from pathlib import Path
import tomllib


PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def _metadata():
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def test_setuptools_build_discovers_only_tui_packages():
    metadata = _metadata()
    assert metadata["build-system"] == {
        "requires": ["setuptools>=64"],
        "build-backend": "setuptools.build_meta",
    }
    finder = metadata["tool"]["setuptools"]["packages"]["find"]
    assert finder["where"] == ["."]
    assert finder["include"] == ["fleet_tui*"]
    assert finder["exclude"] == ["specs*", "tests*"]
    assert finder["namespaces"] is False
    for package in ("fleet_tui", "fleet_tui.sources", "fleet_tui.widgets", "fleet_tui.fleet_cli"):
        assert any(fnmatchcase(package, pattern) for pattern in finder["include"])
        assert not any(fnmatchcase(package, pattern) for pattern in finder["exclude"])
    for package in ("specs", "tests"):
        assert not any(fnmatchcase(package, pattern) for pattern in finder["include"])
        assert any(fnmatchcase(package, pattern) for pattern in finder["exclude"])


def test_project_install_metadata_remains_stable():
    project = _metadata()["project"]
    assert project["name"] == "fleet_tui"
    assert project["version"] == "4.0"
    assert project["requires-python"] == ">=3.11"
    assert project["dependencies"] == [
        "rich", "textual>=0.60", "pyte>=0.8", "textual-serve>=1.1",
    ]
    assert project["optional-dependencies"]["dev"] == ["pytest>=8.4", "pytest-asyncio>=1"]
    assert "scripts" not in project
