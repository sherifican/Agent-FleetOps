"""Pytest conftest for guard/tests/ — disposable-environment isolation.

WHAT IS ISOLATED
  Every test in this directory runs with HOME / USERPROFILE redirected to a
  per-test temporary directory, the working directory moved to a second
  temporary directory, and a fixed set of ambient environment variables
  deleted.  No test can write into the real home directory or the repository
  tree, and an ambient variable cannot silently change what a test measures.

SINK INVENTORY (named list — NOT a tree walk)
  _reports/scan_report.txt              the staging scan report
  docs/banner.png                       the rendered banner
  docs/banner.stamp                     the render stamp binding the PNG to the SVG
  leg_canary_state.json                 the canary's CWD-relative default state file
  guard/tests/leg_canary_state.json     the same default, if a test ever ran from this directory
  <real home directory>                 covered by the fixture's own assertions

AXIS NOT SWEPT
  The inventory above is a NAMED LIST, not a tree walk.  A sink created at a
  path nobody listed is outside the check.
"""

import hashlib
import os
import pathlib
import site

import pytest

# The real user site-packages, resolved ONCE, before any redirect. Moving HOME also moves
# site.getusersitepackages(), and on a box whose pytest is installed there a SUBPROCESS started by
# a test loses its own test runner: measured here, `HOME=<tmp> python3 -m pytest --version` prints
# "No module named pytest", which took guard/doc_count_drift.py's collector from a count to
# "the collector printed no total" inside guard/run_guards.sh. The isolation this file provides is
# about WRITES, not about amputating the interpreter, so the real user site is pinned onto
# PYTHONPATH for the redirected environment.
_REAL_USER_SITE = site.getusersitepackages()

# ---------------------------------------------------------------------------
# SINK INVENTORY
# ---------------------------------------------------------------------------

SINK_INVENTORY: list[tuple[str, str]] = [
    ("_reports/scan_report.txt", "the staging scan report"),
    ("docs/banner.png", "the rendered banner"),
    ("docs/banner.stamp", "the render stamp binding the PNG to the SVG"),
    ("leg_canary_state.json", "the canary's CWD-relative default state file"),
    ("guard/tests/leg_canary_state.json", "the same default, if a test ever ran from this directory"),
]
# The real home directory itself as a class is covered by the fixture's own
# assertions (see _env_isolation below).

# ---------------------------------------------------------------------------
# Ambient variables cleared for every test
# ---------------------------------------------------------------------------

_AMBIENT_VARS = (
    "PASSBACK_OUTBOX",
    "PASSBACK_RECIPIENT_SHELL",
    "COMMS_ROOT",
    "SCRUB_OVERLAY",
    "SCRUB_PROFILE",
    "ORG_LINT_ROOT",
    "RUN_MUTATION_HARNESS",
)

# ---------------------------------------------------------------------------
# Marker registration
# ---------------------------------------------------------------------------


def pytest_configure(config):
    """Register the ``no_env_isolation`` marker.

    This marker is the ONLY sanctioned way out of the clear-list.  A test
    using it takes responsibility for what it touches.
    """
    config.addinivalue_line(
        "markers",
        "no_env_isolation: opt out of the ambient-environment clear-list; "
        "the test takes responsibility for what it touches",
    )

# ---------------------------------------------------------------------------
# Per-test environment isolation
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _env_isolation(request, monkeypatch, tmp_path):
    """Redirect HOME, chdir, and clear ambient variables for the duration of
    one test.  Skipped when the test (or a parent) carries the
    ``no_env_isolation`` marker.
    """
    if request.node.get_closest_marker("no_env_isolation"):
        return

    home_dir = tmp_path / "home"
    home_dir.mkdir()
    work_dir = tmp_path / "work"
    work_dir.mkdir()

    # Point every runtime form of "the home directory" at the temp dir.
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("USERPROFILE", str(home_dir))
    if os.path.isdir(_REAL_USER_SITE):
        existing = os.environ.get("PYTHONPATH", "")
        monkeypatch.setenv(
            "PYTHONPATH",
            _REAL_USER_SITE + (os.pathsep + existing if existing else ""))

    # Assert inside the fixture that all three forms resolve under the
    # temporary directory, both bare and separator-terminated.
    expected_bare = str(home_dir)
    expected_sep = str(home_dir) + os.sep
    for label, value in (
        ("HOME env var", os.environ["HOME"]),
        ("os.path.expanduser('~')", os.path.expanduser("~")),
        ("pathlib.Path.home()", str(pathlib.Path.home())),
    ):
        assert value == expected_bare, (
            f"{label}: bare mismatch — got {value!r}, want {expected_bare!r}"
        )
        assert value + os.sep == expected_sep, (
            f"{label}: separator-terminated mismatch — "
            f"got {value + os.sep!r}, want {expected_sep!r}"
        )

    # CWD-relative default output paths land in the work dir, not the repo.
    monkeypatch.chdir(work_dir)

    # Delete ambient inputs so they cannot silently change test behaviour.
    for var in _AMBIENT_VARS:
        monkeypatch.delenv(var, raising=False)

# ---------------------------------------------------------------------------
# Session-scoped sink guard
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True, scope="session")
def _sink_guard():
    """Record the state of every inventoried sink before the session and
    assert it is unchanged after.  Detects both CREATION (ABSENT → exists)
    and MODIFICATION (hash change / deletion).
    """
    repo_root = pathlib.Path(__file__).resolve().parent.parent.parent

    def _record(path: pathlib.Path) -> str:
        if path.is_file():
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            return f"PRESENT:{digest}"
        return "ABSENT"

    baseline: dict[str, str] = {}
    for rel, _reason in SINK_INVENTORY:
        baseline[rel] = _record(repo_root / rel)

    yield

    for rel, reason in SINK_INVENTORY:
        full = repo_root / rel
        current = _record(full)
        expected = baseline[rel]

        if expected == "ABSENT" and current != "ABSENT":
            pytest.fail(
                f"sink guard: {rel!r} ({reason}) was ABSENT before the session "
                f"but now exists — CREATION detected"
            )
        if expected.startswith("PRESENT:") and current != expected:
            if current == "ABSENT":
                pytest.fail(
                    f"sink guard: {rel!r} ({reason}) was PRESENT before the "
                    f"session but is now ABSENT — DELETION detected"
                )
            pytest.fail(
                f"sink guard: {rel!r} ({reason}) content changed — "
                f"MODIFICATION detected (was {expected}, now {current})"
            )