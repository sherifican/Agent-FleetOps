"""Hermetic regression tests for the repaired ``_tools/scan_gate.py`` (the publication scanner).

WHAT THE OLD SCANNER GOT WRONG (each group below guards one of these):
  * ``_tools`` sat in the skip set, so the scanner could not scan itself or anything beside it.
  * No Anthropic key arm: a bare ``sk-ant-`` key with no quoted assignment around it passed.
  * Content came from WORKTREE bytes even when Git selected the file list, so a staged secret
    with a cleaned worktree passed and an untracked-but-dirty worktree blocked.
  * An empty index, a failed ``git ls-files`` or an unreadable file each fell back to something
    quieter (a filesystem walk, a silent ``continue``) and reported CLEAN.
  * An export nested inside an unrelated checkout scanned the ANCESTOR's index, not itself.
  * A missing path, an ordinary file or ``--help`` was accepted as a staging directory; a
    report directory was created under it and CLEAN printed.
  * Only a UTF-8 (errors ignored) view existed, so a UTF-16/32 payload was invisible.

HARNESS CONVENTIONS:
  The scanner under test is copied into a temp TOOL directory (``<tmp>/tool/scan_gate.py``)
  with a nonempty synthetic ``identity_terms.txt`` beside it (the scanner reads its terms from
  its own directory, and a terms file that sat inside the scanned tree would itself be a hit).
  The scanned STAGING tree is a separate directory. The scanner runs via ``sys.executable`` in
  an environment built from scratch: HOME under tmp_path, ``GIT_CONFIG_NOSYSTEM=1``,
  ``GIT_CONFIG_GLOBAL=/dev/null``, no inherited ``GIT_*`` variable, ``LC_ALL=C``; signing is
  off and no network or real credential is involved. Every key-shaped value, identity term and
  suspicious assignment field name is assembled from fragments at run time so this tracked
  source never carries a contiguous key-shaped literal. Every test asserts its synthetic content
  values are absent from stdout, stderr and ``_reports/scan_report.txt`` (filenames that carry a
  shape are diagnostics, not a redaction promise, and are excluded from that assertion).
  Docstrings label assertions as CONTROL (preservation: passes on the OLD scanner too) or
  REPAIRED (fails on the OLD scanner); the assertion messages carry the same prefix so a parent
  run's failure line names the class of the assertion that fired.
  Type discipline: ``tmp_path`` is a ``pathlib.Path``; helpers take and return ``Path``; ``str``
  only at the subprocess argv boundary.
"""
import ast
import hashlib
import importlib.util
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCANNER = REPO / "_tools" / "scan_gate.py"
REAL_GIT = shutil.which("git")
REPORT_REL = Path("_reports") / "scan_report.txt"

# ---------------------------------------------------------------------------------------------
# Synthetic values — assembled from fragments at RUN TIME, never contiguous in this file.
# ---------------------------------------------------------------------------------------------
IDENTITY_TERM = "synthetic" + "fixture" + "person"
ANT_PREFIX = "sk-" + "ant-"
ANT_API03 = "api" + "03-"
OPENAI_PREFIX = "sk-"
GITHUB_PREFIX = "gh" + "p_"
AWS_PREFIX = "AK" + "IA"
PEM_HEADER = "-----BEGIN " + "PRIVATE" + " KEY-----"
API_KEY_FIELD = "api" + "_key"
ANT_ENV_FIELD = "ANTHROPIC" + "_API" + "_KEY"
EMAIL_VALUE = "fixture.person" + "@" + "gm" + "ail.com"


def _body(n: int, alphabet: str) -> str:
    """Deterministic n-character body cycling through ``alphabet``."""
    return "".join(alphabet[i % len(alphabet)] for i in range(n))


def openai_plant() -> str:
    """The OpenAI-shape plant the OLD scanner already detects (``sk-`` + 24 alnum)."""
    return OPENAI_PREFIX + _body(24, "Q7w3Zx9Kf2")


def ant_key(kind: str) -> str:
    """A24: 24-char mixed body with ``_``/``-`` inside the first 20 (kills an alnum-only arm);
    A95: ``api03-`` + 89 chars with ``_``/``-`` coverage, 95 total."""
    if kind == "A24":
        return ANT_PREFIX + _body(24, "aB3_cD4-")
    if kind == "A95":
        return ANT_PREFIX + ANT_API03 + _body(89, "Xy9_Zw8-")
    raise ValueError(kind)


def wrap(shape: str, key: str) -> str:
    """The six G1 wrappers. S1-S4 have no quoted generic assignment (OLD scanner blind);
    C1/C2 are the quoted assignment forms the OLD generic arm already catches."""
    return {
        "S1": key,
        "S2": ANT_ENV_FIELD + "=" + key,
        "S3": '{"anthropic": "' + key + '"}',
        "S4": "Authorization: Bearer " + key,
        "C1": '{"' + API_KEY_FIELD + '": "' + key + '"}',
        "C2": API_KEY_FIELD + ' = "' + key + '"',
    }[shape]


# ---------------------------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------------------------

def clean_env(tmp_path: Path, path_prefix: Path | None = None) -> dict:
    """An environment built from scratch: nothing inherited but PATH (so ``git`` resolves)."""
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    path = os.environ.get("PATH", "")
    if path_prefix is not None:
        path = str(path_prefix) + os.pathsep + path
    env = {
        "PATH": path,
        "HOME": str(home),
        "TMPDIR": str(tmp_path),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_AUTHOR_NAME": "fixture",
        "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
        "GIT_COMMITTER_NAME": "fixture",
        "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
        "LC_ALL": "C",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUTF8": "1",
    }
    # Built from scratch, so no inherited GIT_* (or anything else) reaches the scanner's process.
    return env


def make_tool(tmp_path: Path, source: str | None = None, name: str = "tool") -> Path:
    """Copy the scanner under test into ``<tmp>/<name>/`` with a synthetic terms file beside it.
    Returns the path of the copied scanner (the DRIVER)."""
    tool = tmp_path / name
    tool.mkdir(exist_ok=True)
    driver = tool / "scan_gate.py"
    if source is None:
        shutil.copy(SCANNER, driver)
    else:
        driver.write_text(source, encoding="utf8")
    (tool / "identity_terms.txt").write_text(IDENTITY_TERM + "\n", encoding="utf8")
    return driver


def git(tmp_path: Path, repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    """Run the REAL git in the clean environment, signing off, hooks bypassed where relevant."""
    assert REAL_GIT is not None, "git is required by the scanner under test"
    return subprocess.run([REAL_GIT, "-C", str(repo), "-c", "commit.gpgsign=false",
                           "-c", "core.autocrlf=false", *args],
                          capture_output=True, text=True, encoding="utf8", errors="replace",
                          env=clean_env(tmp_path), check=check)


def make_staging(tmp_path: Path, name: str = "staging", git_repo: bool = False) -> Path:
    """A scanned tree. With ``git_repo`` it is an initialised checkout on branch ``main``."""
    staging = tmp_path / name
    staging.mkdir(parents=True, exist_ok=True)
    if git_repo:
        git(tmp_path, staging, "init", "-q", "-b", "main")
    return staging


def commit_all(tmp_path: Path, repo: Path, message: str = "fixture") -> None:
    git(tmp_path, repo, "add", "-A")
    git(tmp_path, repo, "commit", "-q", "--no-verify", "-m", message)


def write(path: Path, data: str | bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, bytes):
        path.write_bytes(data)
    else:
        path.write_text(data, encoding="utf8")
    return path


def run_scan(tmp_path: Path, driver: Path, *argv: str, cwd: Path | None = None,
             path_prefix: Path | None = None) -> subprocess.CompletedProcess:
    """Run the driver copy via ``sys.executable`` in the clean environment."""
    return subprocess.run([sys.executable, str(driver), *argv],
                          capture_output=True, text=True, encoding="utf8", errors="replace",
                          env=clean_env(tmp_path, path_prefix),
                          cwd=str(cwd) if cwd is not None else None)


def scan(tmp_path: Path, driver: Path, staging: Path, **kw) -> subprocess.CompletedProcess:
    return run_scan(tmp_path, driver, str(staging), **kw)


def report(staging: Path) -> str | None:
    """The report text, or None when no report exists."""
    p = staging / REPORT_REL
    return p.read_text(encoding="utf8") if p.is_file() else None


def hit(cls: str, name: str, surface: str, rel: str, line: int) -> str:
    return f"{cls}\t{name}\t{surface}\t{rel}:{line}"


def hits_for(staging: Path, rel: str) -> set[str]:
    """Report lines whose path column is exactly ``rel``."""
    text = report(staging) or ""
    out = set()
    for ln in text.splitlines():
        parts = ln.split("\t")
        if len(parts) == 4 and parts[3].rsplit(":", 1)[0] == rel:
            out.add(ln)
    return out


def assert_values_absent(values: list[str], staging: Path, proc: subprocess.CompletedProcess) -> None:
    """CONTROL: no synthetic content value is echoed to stdout, stderr or the report."""
    surfaces = {"stdout": proc.stdout, "stderr": proc.stderr, "report": report(staging) or ""}
    for value in values:
        for label, text in surfaces.items():
            assert value not in text, f"CONTROL: synthetic value leaked into {label}"


def git_shim(tmp_path: Path, failing_subcommand: str) -> Path:
    """A ``git`` on PATH that fails deterministically for ONE subcommand and otherwise execs
    the real git. Deterministic subprocess failure, no permission assumptions."""
    shim_dir = tmp_path / "shim"
    shim_dir.mkdir(exist_ok=True)
    shim = shim_dir / "git"
    shim.write_text(
        "#!/bin/sh\n"
        "for a in \"$@\"; do\n"
        f"  if [ \"$a\" = \"{failing_subcommand}\" ]; then\n"
        f"    echo \"fixture: injected failure for {failing_subcommand}\" >&2; exit 128\n"
        "  fi\n"
        "done\n"
        f"exec \"{REAL_GIT}\" \"$@\"\n", encoding="utf8")
    shim.chmod(shim.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return shim_dir


def import_driver(driver: Path, tag: str):
    """Import the DRIVER copy as a module (for the imported ``scan()`` surface)."""
    spec = importlib.util.spec_from_file_location("scan_gate_under_test_" + tag, str(driver))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def encode_wide(text: str, codec: str, bom: bool) -> bytes:
    boms = {"utf-16-le": b"\xff\xfe", "utf-16-be": b"\xfe\xff",
            "utf-32-le": b"\xff\xfe\x00\x00", "utf-32-be": b"\x00\x00\xfe\xff"}
    return (boms[codec] if bom else b"") + text.encode(codec)


# =============================================================================================
# GROUP 1
# =============================================================================================

@pytest.mark.parametrize("mode", ["git", "export"])
def test_tools_content_is_scanned(tmp_path: Path, mode: str) -> None:
    """Broken behaviour: ``_tools`` was in the skip set, so a key inside ``_tools/`` was never
    read while the same key beside it was.

    CONTROL (old scanner passes): the same OpenAI-shape plant in ``docs/probe.txt`` fires with
    exact path/class/pattern; the ordinary sibling ``_tools/sibling.txt`` is not reported; the
    plant value is absent from stdout/stderr/report.
    REPAIRED (old scanner fails): ``_tools/probe.txt`` is reported with the exact
    (SECRET, openai-style-key, content, path:line) tuple, on both the Git index path and the
    non-Git export path; the imported ``scan()`` returns the same tuple.

    Kills: M-SKIP-TOOLS (restore ``_tools`` in the skip set).
    """
    driver = make_tool(tmp_path)
    staging = make_staging(tmp_path, git_repo=(mode == "git"))
    plant = openai_plant()
    write(staging / "_tools" / "probe.txt", "probe " + plant + "\n")
    write(staging / "_tools" / "sibling.txt", "nothing secret beside the probe\n")
    write(staging / "docs" / "probe.txt", "control " + plant + "\n")
    if mode == "git":
        commit_all(tmp_path, staging)

    proc = scan(tmp_path, driver, staging)
    assert proc.returncode == 1, "CONTROL: a tree with a plant must exit 1"
    assert hit("SECRET", "openai-style-key", "content", "docs/probe.txt", 1) in hits_for(
        staging, "docs/probe.txt"), "CONTROL: plant outside _tools fires"
    assert hits_for(staging, "_tools/sibling.txt") == set(), "CONTROL: clean sibling stays clean"
    assert_values_absent([plant], staging, proc)

    assert hits_for(staging, "_tools/probe.txt") == {
        hit("SECRET", "openai-style-key", "content", "_tools/probe.txt", 1)
    }, "REPAIRED: the plant inside _tools must be reported with exact path/class/pattern"

    module = import_driver(driver, "g1_" + mode)
    found = set(module.scan(str(staging)))
    assert ("_tools/probe.txt", 1, "SECRET", "openai-style-key", "content") in found, \
        "REPAIRED: imported scan() must return the _tools hit tuple"
    assert not any(h[0] == "_tools/sibling.txt" for h in found), "CONTROL: scan() sibling clean"


# =============================================================================================
# GROUP 2
# =============================================================================================

def test_scanner_file_itself_is_covered(tmp_path: Path) -> None:
    """Broken behaviour: with ``_tools`` skipped, the scanner's OWN source was never scanned, so
    a key left in a comment of ``_tools/scan_gate.py`` would publish.

    The planted copy is DATA in the staging tree; a separate unplanted DRIVER copy runs. The
    plant is a harmless comment, never executed.
    CONTROL (old passes): the unplanted candidate source scanned as ``_tools/scan_gate.py`` is
    CLEAN (rc 0, exact report) in both git and export modes; plant value absent from outputs.
    REPAIRED (old fails): the planted ``_tools/scan_gate.py`` is reported at the exact comment
    line with (SECRET, openai-style-key, content), in git and export modes.

    Kills: M-SKIP-SELF (exempt only ``_tools/scan_gate.py`` while other tools remain scanned).
    """
    driver = make_tool(tmp_path)
    source = SCANNER.read_text(encoding="utf8")
    plant = openai_plant()
    planted = source.rstrip("\n") + "\n# fixture comment carrying a key shape: " + plant + "\n"
    plant_line = planted.count("\n")  # the appended comment is the last line

    for mode in ("git", "export"):
        clean = make_staging(tmp_path, "clean_" + mode, git_repo=(mode == "git"))
        write(clean / "_tools" / "scan_gate.py", source)
        write(clean / "docs" / "readme.md", "nothing private here\n")
        if mode == "git":
            commit_all(tmp_path, clean)
        proc = scan(tmp_path, driver, clean)
        assert proc.returncode == 0, f"CONTROL[{mode}]: unplanted candidate source self-scan is clean"
        assert report(clean) == "scan_gate: CLEAN\n", f"CONTROL[{mode}]: exact clean report"

        dirty = make_staging(tmp_path, "dirty_" + mode, git_repo=(mode == "git"))
        write(dirty / "_tools" / "scan_gate.py", planted)
        write(dirty / "_tools" / "other.txt", "another tool file, clean\n")
        write(dirty / "docs" / "readme.md", "nothing private here\n")
        if mode == "git":
            commit_all(tmp_path, dirty)
        proc = scan(tmp_path, driver, dirty)
        assert_values_absent([plant], dirty, proc)
        assert proc.returncode == 1, f"REPAIRED[{mode}]: planted scanner source must block"
        assert hits_for(dirty, "_tools/scan_gate.py") == {
            hit("SECRET", "openai-style-key", "content", "_tools/scan_gate.py", plant_line)
        }, f"REPAIRED[{mode}]: _tools/scan_gate.py reported at the planted comment line"
        assert hits_for(dirty, "_tools/other.txt") == set(), f"CONTROL[{mode}]: other tool clean"


# =============================================================================================
# GROUP 3
# =============================================================================================

@pytest.mark.parametrize("body", ["A24", "A95"])
@pytest.mark.parametrize("shape", ["S1", "S2", "S3", "S4", "C1", "C2"])
def test_anthropic_shapes(tmp_path: Path, shape: str, body: str) -> None:
    """Broken behaviour: no Anthropic arm. S1 bare, S2 unquoted env-var assignment, S3 renamed
    JSON field ``anthropic`` and S4 Bearer header all passed the old scanner because only the
    quoted generic assignment arm existed.

    CONTROL (old passes; C1/C2 only): C1 quoted JSON api-key field and C2 quoted assignment
    exit 1 and carry the legacy ``generic-key-assign`` arm — positive controls, not new fixes.
    Key body absent from stdout/stderr/report (all shapes).
    REPAIRED (old fails): S1-S4 exit 1 (old exits 0); every shape, both bodies, carries the
    exact (SECRET, anthropic-key, content, path:1) line. For C1/C2 the old scanner fails ONLY
    this anthropic-arm assertion, after the blocking control has passed.
    A24 is a mixed body with ``_`` and ``-`` inside the first 20 characters; A95 is the
    ``api03-`` form with ``_``/``-`` coverage.

    Kills: M-NO-ANTHROPIC (arm removed); M-ANT-ALNUM-ONLY (body class ``[A-Za-z0-9]`` only:
    A24 has no 20-run of alphanumerics, so it would pass).
    """
    driver = make_tool(tmp_path)
    staging = make_staging(tmp_path)
    key = ant_key(body)
    rel = "cfg/" + shape + ".txt"
    write(staging / rel, wrap(shape, key) + "\n")

    proc = scan(tmp_path, driver, staging)
    assert_values_absent([key[len(ANT_PREFIX):]], staging, proc)
    found = hits_for(staging, rel)
    if shape in ("C1", "C2"):
        assert proc.returncode == 1, "CONTROL: quoted assignment blocks on the legacy arm"
        assert hit("SECRET", "generic-key-assign", "content", rel, 1) in found, \
            "CONTROL: legacy generic-key-assign arm fires for the quoted form"
    else:
        assert proc.returncode == 1, "REPAIRED: bare/unquoted/renamed/Bearer key must exit 1"
    assert hit("SECRET", "anthropic-key", "content", rel, 1) in found, \
        "REPAIRED: anthropic-key arm must report this shape"


# =============================================================================================
# GROUP 4
# =============================================================================================

def test_key_boundary_controls(tmp_path: Path) -> None:
    """Broken behaviour: no Anthropic arm at all; and the repaired arm must sit at the documented
    boundary (20 eligible body characters), not below it, and must not be a side effect of
    another arm.

    CONTROL (old passes): 19-character body, the bare documented prefix and prose mentioning
    the prefix are all CLEAN (rc 0, exact report); existing OpenAI/GitHub/AWS/private-key
    shapes each fire with their own pattern name; values absent from outputs.
    REPAIRED (old fails): a 20-character eligible body (with ``_``/``-`` inside) exits 1 and is
    reported by ``anthropic-key`` ONLY — no other arm supplies the detection.

    Kills: M-ANT-SHORT (threshold lowered: the 19-char file would fire); M-ANT-THRESHOLD
    (threshold raised: the 20-char file would pass); an always-clean scanner dies on the
    positive controls.
    """
    driver = make_tool(tmp_path)
    below = make_staging(tmp_path, "below")
    nineteen = ANT_PREFIX + _body(19, "aB3_cD4-")
    write(below / "docs" / "nineteen.txt", "value " + nineteen + "\n")
    write(below / "docs" / "prefix.txt", "the documented prefix is " + ANT_PREFIX + " alone\n")
    write(below / "docs" / "prose.txt", "keys begin with " + ANT_PREFIX + " and are long strings\n")
    proc = scan(tmp_path, driver, below)
    assert proc.returncode == 0, "CONTROL: 19-char body, bare prefix and prose are clean"
    assert report(below) == "scan_gate: CLEAN\n", "CONTROL: exact clean report"
    assert_values_absent([nineteen[len(ANT_PREFIX):]], below, proc)

    above = make_staging(tmp_path, "above")
    twenty = ANT_PREFIX + _body(20, "aB3_cD4-")
    controls = {
        "docs/openai.txt": (openai_plant(), "openai-style-key"),
        "docs/github.txt": (GITHUB_PREFIX + _body(24, "Zq8Lm3Rt"), "github-token"),
        "docs/aws.txt": (AWS_PREFIX + _body(16, "QWERTY2345"), "aws-key"),
        "docs/pem.txt": (PEM_HEADER, "private-key-block"),
    }
    write(above / "docs" / "twenty.txt", "value " + twenty + "\n")
    for rel, (value, _name) in controls.items():
        write(above / rel, "control " + value + "\n")
    proc = scan(tmp_path, driver, above)
    assert proc.returncode == 1, "CONTROL: existing shapes still block"
    for rel, (_value, name) in controls.items():
        assert hit("SECRET", name, "content", rel, 1) in hits_for(above, rel), \
            f"CONTROL: {name} still fires on its own shape"
    assert_values_absent([twenty[len(ANT_PREFIX):]] + [v for v, n in controls.values()
                                                       if n != "private-key-block"], above, proc)

    assert hits_for(above, "docs/twenty.txt") == {
        hit("SECRET", "anthropic-key", "content", "docs/twenty.txt", 1)
    }, "REPAIRED: 20-char eligible body fires via anthropic-key and via no other arm"


# =============================================================================================
# GROUP 5
# =============================================================================================

@pytest.mark.parametrize("arm", ["staged-secret", "worktree-secret"])
def test_index_content_authority(tmp_path: Path, arm: str) -> None:
    """Broken behaviour: the file LIST came from ``git ls-files`` but the CONTENT came from the
    worktree, so the bytes judged were not the bytes that publish.

    staged-secret — REPAIRED (old fails): a secret is staged, then the worktree file is
    replaced with clean bytes; the scanner must still block (rc 1, exact hit on the path).
    worktree-secret — REPAIRED (old fails): clean bytes are staged, then the worktree file is
    dirtied; the scanner must pass (rc 0, exact CLEAN report).
    CONTROL (old passes): the staged OID equals the SHA-1 of the expected blob and ``git
    cat-file`` returns exactly the expected bytes (compared by digest so no value is printed);
    the secret value is absent from stdout/stderr/report.

    Kills: M-WORKTREE-READ (read file bytes instead of the index blob).
    """
    driver = make_tool(tmp_path)
    staging = make_staging(tmp_path, git_repo=True)
    plant = openai_plant()
    dirty = ("token line " + plant + "\n").encode("utf8")
    clean = b"clean bytes only\n"
    staged, worktree = (dirty, clean) if arm == "staged-secret" else (clean, dirty)

    target = write(staging / "docs" / "cfg.txt", staged)
    git(tmp_path, staging, "add", "docs/cfg.txt")
    target.write_bytes(worktree)

    stage_line = git(tmp_path, staging, "ls-files", "--stage", "--", "docs/cfg.txt").stdout.split()
    oid = stage_line[1]
    expected_oid = hashlib.sha1(b"blob " + str(len(staged)).encode() + b"\0" + staged).hexdigest()
    assert oid == expected_oid, "CONTROL: staged OID is the expected blob"
    blob = subprocess.run([REAL_GIT, "-C", str(staging), "cat-file", "blob", oid],
                          capture_output=True, env=clean_env(tmp_path), check=True).stdout
    assert hashlib.sha256(blob).hexdigest() == hashlib.sha256(staged).hexdigest(), \
        "CONTROL: staged bytes are the expected bytes"
    assert hashlib.sha256(target.read_bytes()).hexdigest() == hashlib.sha256(worktree).hexdigest(), \
        "CONTROL: worktree bytes differ from the index as the arm intends"

    proc = scan(tmp_path, driver, staging)
    assert_values_absent([plant], staging, proc)
    if arm == "staged-secret":
        assert proc.returncode == 1, "REPAIRED: staged secret blocks even with a clean worktree"
        assert hits_for(staging, "docs/cfg.txt") == {
            hit("SECRET", "openai-style-key", "content", "docs/cfg.txt", 1)
        }, "REPAIRED: the index blob is the content that is judged"
    else:
        assert proc.returncode == 0, "REPAIRED: a dirty worktree over clean staged bytes passes"
        assert report(staging) == "scan_gate: CLEAN\n", "REPAIRED: exact CLEAN report"


# =============================================================================================
# GROUP 6
# =============================================================================================

@pytest.mark.parametrize("arm", ["empty-index", "ls-files-error", "cat-file-error", "unmerged"])
def test_index_empty_and_failure(tmp_path: Path, arm: str) -> None:
    """Broken behaviour: an EMPTY index fell back to a filesystem walk (scanning bytes that could
    never publish), a FAILED ``git ls-files`` fell back the same way and reported CLEAN, blob
    read errors were not a case at all, and unmerged entries were listed and read from the
    worktree.

    empty-index — REPAIRED (old fails): an initialised checkout with nothing staged and an
    untracked plant is CLEAN (rc 0, exact report); the plant is not in the report.
    ls-files-error — REPAIRED (old fails): a deterministic ``git ls-files`` failure (a ``git``
    shim on PATH that fails that one subcommand) refuses with rc 2, no CLEAN anywhere, no
    report written.
    cat-file-error — REPAIRED (old fails): a deterministic ``git cat-file`` failure refuses
    with rc 2, no CLEAN, no report.
    unmerged — REPAIRED (old fails): stage-1/2/3 entries from a real merge conflict refuse with
    rc 2, no CLEAN.
    CONTROL (old passes): the shim is exercised by a direct call and fails as intended; the
    unmerged fixture really carries non-zero stages; values absent from outputs.

    Kills: M-EMPTY-FALLBACK, M-GIT-ERROR-FALLBACK, M-BLOB-ERROR-CONTINUE, M-UNMERGED-ACCEPT.
    """
    driver = make_tool(tmp_path)
    staging = make_staging(tmp_path, git_repo=True)
    plant = openai_plant()

    if arm == "empty-index":
        write(staging / "docs" / "untracked.txt", "untracked " + plant + "\n")
        proc = scan(tmp_path, driver, staging)
        assert_values_absent([plant], staging, proc)
        assert proc.returncode == 0, "REPAIRED: an empty index publishes nothing, so CLEAN"
        assert report(staging) == "scan_gate: CLEAN\n", "REPAIRED: exact CLEAN report"
        return

    write(staging / "docs" / "tracked.txt", "tracked clean bytes\n")
    commit_all(tmp_path, staging)

    if arm in ("ls-files-error", "cat-file-error"):
        sub = "ls-files" if arm == "ls-files-error" else "cat-file"
        shim_dir = git_shim(tmp_path, sub)
        probe = subprocess.run(["git", "-C", str(staging), sub, "-h"], capture_output=True,
                               text=True, env=clean_env(tmp_path, shim_dir))
        assert probe.returncode == 128 and "injected failure" in probe.stderr, \
            "CONTROL: the shim fails the targeted subcommand"
        proc = scan(tmp_path, driver, staging, path_prefix=shim_dir)
        assert proc.returncode == 2, f"REPAIRED: a failed git {sub} must refuse, not fall back"
        assert "CLEAN" not in proc.stdout + proc.stderr, "REPAIRED: no CLEAN after a git failure"
        assert report(staging) is None, "REPAIRED: no report is written for an unmeasured scan"
        return

    # unmerged: two branches editing the same tracked file, merged without resolution.
    git(tmp_path, staging, "checkout", "-q", "-b", "side")
    write(staging / "docs" / "tracked.txt", "side edit\n")
    commit_all(tmp_path, staging, "side")
    git(tmp_path, staging, "checkout", "-q", "main")
    write(staging / "docs" / "tracked.txt", "main edit\n")
    commit_all(tmp_path, staging, "main")
    merge = git(tmp_path, staging, "merge", "--no-commit", "side", check=False)
    assert merge.returncode != 0, "CONTROL: the merge conflicts"
    stages = {ln.split()[2] for ln in git(tmp_path, staging, "ls-files", "--stage").stdout.splitlines()}
    assert stages - {"0"}, "CONTROL: the index carries non-zero stages"
    proc = scan(tmp_path, driver, staging)
    assert proc.returncode == 2, "REPAIRED: unmerged index entries refuse"
    assert "CLEAN" not in proc.stdout + proc.stderr, "REPAIRED: no CLEAN on an unmerged index"
    assert report(staging) is None, "REPAIRED: no report for an unmeasured scan"


# =============================================================================================
# GROUP 7
# =============================================================================================

@pytest.mark.parametrize("arm", ["nested-export", "standalone-export", "linked-worktree"])
def test_nested_export_uses_own_tree(tmp_path: Path, arm: str) -> None:
    """Broken behaviour: Git discovery walked UP from the target, so an export directory nested
    inside an unrelated checkout was scanned through the ANCESTOR's index (tracked files under
    that subdirectory) and the export's own untracked plant was never read.

    nested-export — REPAIRED (old fails): ``outer/export`` (no ``.git`` of its own) contains a
    tracked-by-ancestor clean file and an untracked plant; the plant must be reported (rc 1,
    exact hit relative to the export).
    standalone-export — CONTROL (old passes): the same export outside any checkout reports the
    plant identically.
    linked-worktree — CONTROL (old passes): a linked worktree (``.git`` is a FILE) is a
    checkout root: its own index is scanned (rc 1 on the branch that carries the plant) and the
    primary checkout stays CLEAN (rc 0). Guards the ``.git``-file marker of the repaired root
    detection.
    All arms: plant value absent from outputs.

    Kills: M-ANCESTOR-INDEX (accept any successful ``git -C`` query as the file list).
    """
    driver = make_tool(tmp_path)
    plant = openai_plant()

    if arm == "linked-worktree":
        outer = make_staging(tmp_path, "outer", git_repo=True)
        write(outer / "docs" / "clean.txt", "clean tracked bytes\n")
        commit_all(tmp_path, outer)
        wt = tmp_path / "linked"
        git(tmp_path, outer, "worktree", "add", "-q", "-b", "feature", str(wt))
        assert (wt / ".git").is_file(), "CONTROL: a linked worktree's .git is a file"
        write(wt / "docs" / "plant.txt", "wt " + plant + "\n")
        commit_all(tmp_path, wt, "plant on feature")
        proc = scan(tmp_path, driver, wt)
        assert_values_absent([plant], wt, proc)
        assert proc.returncode == 1, "CONTROL: linked worktree scans its own index"
        assert hits_for(wt, "docs/plant.txt") == {
            hit("SECRET", "openai-style-key", "content", "docs/plant.txt", 1)}
        proc = scan(tmp_path, driver, outer)
        assert proc.returncode == 0 and report(outer) == "scan_gate: CLEAN\n", \
            "CONTROL: the primary checkout does not see the feature branch plant"
        return

    if arm == "nested-export":
        outer = make_staging(tmp_path, "outer", git_repo=True)
        export = outer / "export"
        write(export / "clean.txt", "clean bytes tracked by the ancestor\n")
        commit_all(tmp_path, outer)
        inside = git(tmp_path, export, "rev-parse", "--is-inside-work-tree", check=False)
        assert inside.stdout.strip() == "true", "CONTROL: the export sits inside a checkout"
    else:
        export = make_staging(tmp_path, "standalone")
        write(export / "clean.txt", "clean bytes\n")
        inside = git(tmp_path, export, "rev-parse", "--is-inside-work-tree", check=False)
        assert inside.stdout.strip() != "true", "CONTROL: the standalone export is in no checkout"
    write(export / "plant.txt", "export " + plant + "\n")
    assert not (export / ".git").exists(), "CONTROL: the export has no .git marker"

    proc = scan(tmp_path, driver, export)
    assert_values_absent([plant], export, proc)
    label = "REPAIRED" if arm == "nested-export" else "CONTROL"
    assert proc.returncode == 1, f"{label}: the export's own plant must block"
    assert hits_for(export, "plant.txt") == {
        hit("SECRET", "openai-style-key", "content", "plant.txt", 1)
    }, f"{label}: the export itself is scanned, not an ancestor's index"


# =============================================================================================
# GROUP 8
# =============================================================================================

@pytest.mark.parametrize("arm", ["missing", "file", "dangling", "help-option", "clean-dir"])
def test_invalid_staging_refused(tmp_path: Path, arm: str) -> None:
    """Broken behaviour: any argument was accepted as the staging directory; a missing path
    walked to nothing, printed CLEAN and CREATED ``<missing>/_reports``; ``--help`` was taken
    as a relative directory name.

    missing / dangling — REPAIRED (old fails): rc 2, no CLEAN, the target is not created, and
    the refusal names the guard itself (``REFUSED invalid-staging-directory``), not a downstream
    ``unreadable-walk`` from ``os.walk`` failing on the missing path.
    file — REPAIRED (old fails): rc 2, no CLEAN, the file is untouched (old: traceback, rc 1),
    same refusal token.
    help-option — REPAIRED (old fails): ``--help`` refuses with rc 2 even when a directory of
    that exact name exists in the working directory, no report is created under it, and the
    refusal names ``unsupported-arguments``.
    clean-dir — CONTROL (old passes): an existing clean directory with a valid in-tree
    allowlist policy passes with rc 0 and the exact CLEAN report.

    Kills: M-NO-PATH-GUARD (reading A, the ``isdir`` guard line alone: the mutant still refuses
    via ``unreadable-walk``, so only the token assertion distinguishes it — added after mutant
    verification found it surviving); M-OPTION-AS-PATH.
    """
    driver = make_tool(tmp_path)
    if arm == "clean-dir":
        staging = make_staging(tmp_path)
        write(staging / "docs" / "readme.md", "nothing private here\n")
        write(staging / "_tools" / "scan_allow.tsv",
              "# path<TAB>pattern<TAB>surface\ndocs/readme.md\towner-identity\tcontent\n")
        proc = scan(tmp_path, driver, staging)
        assert proc.returncode == 0, "CONTROL: an existing clean dir with a valid policy passes"
        assert report(staging) == "scan_gate: CLEAN\n", "CONTROL: exact CLEAN report"
        return

    if arm == "missing":
        target = tmp_path / "absent" / "staging"
        argv, created_probe = str(target), target
    elif arm == "file":
        target = write(tmp_path / "ordinary.txt", "an ordinary file\n")
        argv, created_probe = str(target), tmp_path / "ordinary.txt" / "_reports"
    elif arm == "dangling":
        target = tmp_path / "dangling"
        target.symlink_to(tmp_path / "gone")
        argv, created_probe = str(target), tmp_path / "gone"
    else:
        (tmp_path / "--help").mkdir()
        argv, created_probe = "--help", tmp_path / "--help" / "_reports"

    proc = run_scan(tmp_path, driver, argv, cwd=tmp_path)
    assert proc.returncode == 2, f"REPAIRED[{arm}]: an invalid staging argument must refuse"
    assert "CLEAN" not in proc.stdout + proc.stderr, f"REPAIRED[{arm}]: no CLEAN on refusal"
    # (named 'reason', not 'token': a variable called token assigned a quoted string IS the
    # generic credential-assignment shape and would make this file block its own publication)
    reason = "unsupported-arguments" if arm == "help-option" else "invalid-staging-directory"
    assert "REFUSED " + reason in proc.stderr, \
        f"REPAIRED[{arm}]: the refusal must name the guard ({reason}), not a downstream walk error"
    assert not created_probe.exists(), f"REPAIRED[{arm}]: nothing is created under the target"
    if arm == "file":
        assert target.read_text(encoding="utf8") == "an ordinary file\n", "REPAIRED: file untouched"


# =============================================================================================
# GROUP 9
# =============================================================================================

@pytest.mark.parametrize("mode", ["index", "export"])
@pytest.mark.parametrize("bom", ["bom", "nobom"])
@pytest.mark.parametrize("codec", ["utf-16-le", "utf-16-be", "utf-32-le", "utf-32-be"])
def test_wide_credentials(tmp_path: Path, codec: str, bom: str, mode: str) -> None:
    """Broken behaviour: content was decoded as UTF-8 with errors ignored and nothing else, so a
    key written as UTF-16/32 (NUL-interleaved bytes) was invisible.

    CONTROL (old passes): the plain UTF-8 C1 counterpart in the same tree fires on the legacy
    ``generic-key-assign`` arm; a benign same-codec file alone is CLEAN (rc 0); the key body is
    absent from outputs. Because the UTF-8 control is in the tree, rc is 1 on both scanners.
    REPAIRED (old fails), isolated first on the quoted generic assignment (an arm the old
    scanner HAS, so the failure is the wide view and not the missing Anthropic arm): wide C1
    and C2 carry the exact ``generic-key-assign`` line. Then all six wide wrappers carry the
    exact ``anthropic-key`` line. Each codec, with and without BOM, via the index and export
    paths.

    Kills: M-UTF8-ONLY, M-BOM-ONLY (BOM-less arms), M-UTF16-ONLY (UTF-32 arms), M-FIRST-VIEW
    (only the first decoded view scanned), M-BOM-PREFIX-ORDER (UTF-16-LE BOM matched before the
    UTF-32-LE BOM that starts with the same two bytes).
    """
    driver = make_tool(tmp_path)
    key = ant_key("A24")
    with_bom = bom == "bom"

    benign = make_staging(tmp_path, "benign", git_repo=(mode == "index"))
    write(benign / "docs" / "benign.txt", encode_wide("nothing secret in wide text\n", codec, with_bom))
    if mode == "index":
        commit_all(tmp_path, benign)
    proc = scan(tmp_path, driver, benign)
    assert proc.returncode == 0 and report(benign) == "scan_gate: CLEAN\n", \
        "CONTROL: benign same-codec text passes"

    staging = make_staging(tmp_path, git_repo=(mode == "index"))
    write(staging / "wide" / "utf8_C1.txt", wrap("C1", key) + "\n")
    for shape in ("S1", "S2", "S3", "S4", "C1", "C2"):
        write(staging / "wide" / (shape + ".txt"), encode_wide(wrap(shape, key) + "\n", codec, with_bom))
    if mode == "index":
        commit_all(tmp_path, staging)

    proc = scan(tmp_path, driver, staging)
    assert_values_absent([key[len(ANT_PREFIX):]], staging, proc)
    assert proc.returncode == 1, "CONTROL: the tree blocks (UTF-8 control present)"
    assert hit("SECRET", "generic-key-assign", "content", "wide/utf8_C1.txt", 1) in hits_for(
        staging, "wide/utf8_C1.txt"), "CONTROL: plain UTF-8 quoted assignment fires on the legacy arm"

    for shape in ("C1", "C2"):
        assert hit("SECRET", "generic-key-assign", "content", "wide/" + shape + ".txt", 1) in \
            hits_for(staging, "wide/" + shape + ".txt"), \
            f"REPAIRED[{codec}/{bom}/{mode}]: wide {shape} must fire on the legacy generic arm"
    for shape in ("S1", "S2", "S3", "S4", "C1", "C2"):
        assert hit("SECRET", "anthropic-key", "content", "wide/" + shape + ".txt", 1) in \
            hits_for(staging, "wide/" + shape + ".txt"), \
            f"REPAIRED[{codec}/{bom}/{mode}]: wide {shape} must carry the anthropic-key arm"


# =============================================================================================
# GROUP 10
# =============================================================================================

@pytest.mark.parametrize("codec", ["utf-16-le", "utf-16-be", "utf-32-le", "utf-32-be"])
def test_wide_personal_and_errors(tmp_path: Path, codec: str) -> None:
    """Broken behaviour: wide text was invisible for PERSONAL terms too, and a BOM-declared but
    malformed wide file was silently read as UTF-8 noise and passed.

    CONTROL (old passes): valid same-codec clean text (BOM and no BOM) alone is CLEAN; a benign
    binary/image control (PNG signature + all 256 byte values, NULs included) is CLEAN; the
    identity term is absent from outputs.
    REPAIRED (old fails): the synthetic identity term in valid wide text is reported as
    (PERSONAL, owner-identity, content) with and without BOM; a BOM-declared file truncated by
    one byte (an incomplete code unit, deterministic) refuses with rc 2 and no CLEAN.

    Kills: M-WIDE-SECRET-ONLY (wide views fed only to the SECRET arms); M-LOSSY-DECLARED-DECODE
    (declared BOM decoded with errors ignored instead of strict); M-NUL-BLOCK-ALL (refuse any
    file containing NUL) dies on the clean binary control.
    """
    driver = make_tool(tmp_path)

    clean = make_staging(tmp_path, "clean")
    write(clean / "docs" / "bom.txt", encode_wide("nothing private here\n", codec, True))
    write(clean / "docs" / "nobom.txt", encode_wide("nothing private here\n", codec, False))
    write(clean / "docs" / "image.png", b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + bytes(range(256)))
    proc = scan(tmp_path, driver, clean)
    assert proc.returncode == 0 and report(clean) == "scan_gate: CLEAN\n", \
        "CONTROL: valid clean wide text and a benign binary pass"

    personal = make_staging(tmp_path, "personal")
    write(personal / "docs" / "bom.txt", encode_wide("ask " + IDENTITY_TERM + " about it\n", codec, True))
    write(personal / "docs" / "nobom.txt", encode_wide("ask " + IDENTITY_TERM + " about it\n", codec, False))
    proc = scan(tmp_path, driver, personal)
    assert_values_absent([IDENTITY_TERM], personal, proc)
    assert proc.returncode == 1, "REPAIRED: an identity term in wide text must block"
    for rel in ("docs/bom.txt", "docs/nobom.txt"):
        assert hit("PERSONAL", "owner-identity", "content", rel, 1) in hits_for(personal, rel), \
            f"REPAIRED[{codec}]: identity term found in {rel}"

    malformed = make_staging(tmp_path, "malformed")
    truncated = encode_wide("declared wide text\n", codec, True)[:-1]
    write(malformed / "docs" / "broken.txt", truncated)
    proc = scan(tmp_path, driver, malformed)
    assert proc.returncode == 2, f"REPAIRED[{codec}]: malformed BOM-declared wide text refuses"
    assert "CLEAN" not in proc.stdout + proc.stderr, "REPAIRED: no CLEAN for undecodable input"
    assert report(malformed) is None, "REPAIRED: no report for an unmeasured scan"


# =============================================================================================
# GROUP 11
# =============================================================================================

REFUSAL_STALE_REASON = (
    "candidate exits before write_report on refusal (rc 2), so a previous run's "
    "'scan_gate: CLEAN' report survives beside the rc 2; recorded, not hidden"
)


@pytest.mark.parametrize("arm", [
    "clean-report", "dirty-report", "stale-overwritten", "report-excluded",
    "read-failure", "walk-failure", "report-write-failure",
    "refusal-clears-stale-clean",   # was a strict xfail; REPAIRED 2026-09-11 — the arm is now a live gate
])
def test_report_and_read_failure(tmp_path: Path, arm: str) -> None:
    """Broken behaviour: an unreadable file was silently skipped (``continue``) and the scan
    reported CLEAN; an unreadable directory was silently skipped by ``os.walk``; a report-write
    failure was an uncaught traceback rather than a refusal.

    clean-report / dirty-report / stale-overwritten / report-excluded — CONTROL (old passes):
    exact ``scan_gate: CLEAN\\n`` bytes; dirty schema is ``CLASS<TAB>NAME<TAB>SURFACE<TAB>
    path:line`` with no value; a stale report is overwritten in both directions; a previous
    report under ``_reports`` is not itself scanned on rerun.
    read-failure — REPAIRED (old fails): a dangling symlink in an export refuses with rc 2, no
    CLEAN, no report.
    walk-failure — REPAIRED (old fails): an unreadable subdirectory refuses with rc 2 (the OS
    enforcing the mode is checked first; if it does not, the arm is skipped with that reason).
    report-write-failure — REPAIRED (old tracebacks with rc 1): ``_reports`` existing as a
    FILE refuses with rc 2 and names ``report-write-error``.
    refusal-clears-stale-clean — xfail(strict) RECORD: after a CLEAN run, a refusal (rc 2)
    leaves the previous CLEAN report in place; the assertion that consumers cannot read a
    stale CLEAN beside an rc 2 fails on the candidate and is recorded as such.

    Kills: M-NO-REPORT, M-REPORT-RECURSION, M-READ-ERROR-CONTINUE, M-REPORT-ERROR-CLEAN.
    """
    driver = make_tool(tmp_path)
    staging = make_staging(tmp_path)
    plant = openai_plant()

    if arm == "clean-report":
        write(staging / "docs" / "readme.md", "nothing private here\n")
        proc = scan(tmp_path, driver, staging)
        assert proc.returncode == 0 and "scan_gate: CLEAN" in proc.stdout, "CONTROL: clean run"
        assert (staging / REPORT_REL).read_bytes() == b"scan_gate: CLEAN\n", "CONTROL: exact bytes"
        return

    if arm == "dirty-report":
        write(staging / "docs" / "cfg.txt", "line one\nline two\nvalue " + plant + "\n")
        write(staging / "docs" / ("notes-" + IDENTITY_TERM + ".md"), "clean body\n")
        proc = scan(tmp_path, driver, staging)
        assert proc.returncode == 1, "CONTROL: dirty run exits 1"
        assert_values_absent([plant], staging, proc)
        lines = (report(staging) or "").splitlines()
        assert lines and "CLEAN" not in report(staging), "CONTROL: no CLEAN in a dirty report"
        for ln in lines:
            cls, name, surface, loc = ln.split("\t")
            assert cls in ("SECRET", "PERSONAL") and surface in ("content", "name") \
                and loc.rsplit(":", 1)[1].isdigit() and name, "CONTROL: report schema"
        assert hit("SECRET", "openai-style-key", "content", "docs/cfg.txt", 3) in lines, \
            "CONTROL: content hit carries path and line, no value"
        assert hit("PERSONAL", "owner-identity", "name", "docs/notes-" + IDENTITY_TERM + ".md", 0) \
            in lines, "CONTROL: name hit carries line 0 and surface name"
        return

    if arm == "stale-overwritten":
        write(staging / "docs" / "readme.md", "nothing private here\n")
        write(staging / REPORT_REL, "SECRET\tstale\tcontent\told.txt:9\n")
        proc = scan(tmp_path, driver, staging)
        assert proc.returncode == 0 and report(staging) == "scan_gate: CLEAN\n", \
            "CONTROL: a stale dirty report is overwritten by CLEAN"
        write(staging / "docs" / "cfg.txt", "value " + plant + "\n")
        proc = scan(tmp_path, driver, staging)
        assert proc.returncode == 1 and "CLEAN" not in (report(staging) or "CLEAN"), \
            "CONTROL: a stale CLEAN report is overwritten by the hits"
        return

    if arm == "report-excluded":
        write(staging / "docs" / "readme.md", "nothing private here\n")
        write(staging / REPORT_REL, "previous run echoed " + plant + "\n")
        proc = scan(tmp_path, driver, staging)
        assert proc.returncode == 0 and report(staging) == "scan_gate: CLEAN\n", \
            "CONTROL: _reports is not scanned on rerun and the report is rewritten"
        assert_values_absent([plant], staging, proc)
        return

    if arm == "read-failure":
        write(staging / "docs" / "readme.md", "nothing private here\n")
        (staging / "docs" / "ghost.txt").symlink_to(tmp_path / "nowhere")
        proc = scan(tmp_path, driver, staging)
        assert proc.returncode == 2, "REPAIRED: an unreadable input refuses instead of skipping"
        assert "CLEAN" not in proc.stdout + proc.stderr, "REPAIRED: no CLEAN on a read failure"
        assert report(staging) is None, "REPAIRED: no report for an unmeasured scan"
        return

    if arm == "walk-failure":
        write(staging / "docs" / "readme.md", "nothing private here\n")
        locked = staging / "locked"
        write(locked / "inner.txt", "unreachable\n")
        locked.chmod(0)
        try:
            try:
                os.scandir(str(locked)).close()
                enforced = False
            except PermissionError:
                enforced = True
            if not enforced:
                pytest.skip("the OS does not enforce directory mode 000 here (root?); arm not measurable")
            proc = scan(tmp_path, driver, staging)
            assert proc.returncode == 2, "REPAIRED: an unreadable directory refuses the walk"
            assert "CLEAN" not in proc.stdout + proc.stderr, "REPAIRED: no CLEAN on a walk failure"
            assert report(staging) is None, "REPAIRED: no report for an unmeasured scan"
        finally:
            locked.chmod(0o755)
        return

    if arm == "report-write-failure":
        write(staging / "docs" / "readme.md", "nothing private here\n")
        write(staging / "_reports", "not a directory\n")
        proc = scan(tmp_path, driver, staging)
        assert proc.returncode == 2, "REPAIRED: a report-write failure refuses with rc 2"
        assert "CLEAN" not in proc.stdout + proc.stderr, "REPAIRED: no CLEAN when the report failed"
        assert "report-write-error" in proc.stderr, "REPAIRED: the refusal names the report write"
        return

    # refusal-clears-stale-clean (xfail strict on the candidate)
    write(staging / "docs" / "readme.md", "nothing private here\n")
    proc = scan(tmp_path, driver, staging)
    assert proc.returncode == 0 and report(staging) == "scan_gate: CLEAN\n", "CONTROL: first run CLEAN"
    (staging / "docs" / "ghost.txt").symlink_to(tmp_path / "nowhere")
    proc = scan(tmp_path, driver, staging)
    assert proc.returncode == 2, "REPAIRED: the second run refuses"
    assert report(staging) != "scan_gate: CLEAN\n", \
        "RECORD: a stale CLEAN report must not survive beside an rc 2 refusal"


# =============================================================================================
# GROUP 12
# =============================================================================================

@pytest.mark.parametrize("arm", [
    "exact-content-row-allows", "other-path-blocked", "other-pattern-blocked",
    "two-col-row-keeps-name", "name-row-excuses-name",
    "secret-row-ignored-content", "secret-row-ignored-name",
])
def test_allowlist_surfaces_and_secret_refusal(tmp_path: Path, arm: str) -> None:
    """Preservation controls for the allowlist (all CONTROL: the old scanner passes them too).
    Guards the exact-tuple semantics: one path, one pattern, one SURFACE per row.

    exact-content-row-allows: the term in ``docs/a.md`` with the in-tree row
    ``docs/a.md<TAB>owner-identity<TAB>content`` passes (rc 0, exact CLEAN); the in-tree
    allowlist is read with no extra argument.
    other-path-blocked: the same row plus a SUBSTRING row ``b.md`` do not excuse ``docs/b.md``.
    other-pattern-blocked: an owner-identity row does not excuse an ``email`` hit in that file.
    two-col-row-keeps-name: a two-column row means content; the name hit on
    ``docs/notes-<term>.md`` stays (line 0, surface name).
    name-row-excuses-name: a three-column ``name`` row silences the name hit and the tree is
    CLEAN. Note: the repaired scanner reads ``_tools/scan_allow.tsv`` as content, and a name
    row necessarily carries the term in its path column, so the fixture also carries the
    content row for the allowlist file itself; the old scanner never read ``_tools`` so that
    row is inert there.
    secret-row-ignored-content / secret-row-ignored-name: SECRET hits (content, and a key-shaped
    filename) stay blocked despite a matching SECRET row.
    Term/values absent from outputs except where the FILENAME carries them (diagnostic, not a
    redaction promise).

    Kills: M-IGNORE-ALLOWLIST, M-SUBSTRING-ALLOW, M-CONTENT-EXCUSES-NAME, M-SECRET-ALLOW.
    """
    driver = make_tool(tmp_path)
    staging = make_staging(tmp_path)
    tsv = staging / "_tools" / "scan_allow.tsv"
    header = "# path<TAB>pattern<TAB>surface\n"
    plant = openai_plant()
    named = "docs/notes-" + IDENTITY_TERM + ".md"

    if arm == "exact-content-row-allows":
        write(staging / "docs" / "a.md", "ask " + IDENTITY_TERM + " about it\n")
        write(tsv, header + "docs/a.md\towner-identity\tcontent\n")
        proc = scan(tmp_path, driver, staging)
        assert_values_absent([IDENTITY_TERM], staging, proc)
        assert proc.returncode == 0 and report(staging) == "scan_gate: CLEAN\n", \
            "CONTROL: the exact (path, pattern, content) row excuses the hit"
        return

    if arm == "other-path-blocked":
        write(staging / "docs" / "a.md", "ask " + IDENTITY_TERM + " about it\n")
        write(staging / "docs" / "b.md", "ask " + IDENTITY_TERM + " about it\n")
        write(tsv, header + "docs/a.md\towner-identity\tcontent\nb.md\towner-identity\tcontent\n")
        proc = scan(tmp_path, driver, staging)
        assert_values_absent([IDENTITY_TERM], staging, proc)
        assert proc.returncode == 1, "CONTROL: another path stays blocked"
        assert hits_for(staging, "docs/a.md") == set(), "CONTROL: the exact row still excuses a.md"
        assert hit("PERSONAL", "owner-identity", "content", "docs/b.md", 1) in hits_for(staging, "docs/b.md"), \
            "CONTROL: a substring row does not excuse docs/b.md"
        return

    if arm == "other-pattern-blocked":
        write(staging / "docs" / "a.md", "contact " + EMAIL_VALUE + "\n")
        write(tsv, header + "docs/a.md\towner-identity\tcontent\n")
        proc = scan(tmp_path, driver, staging)
        assert_values_absent([EMAIL_VALUE], staging, proc)
        assert proc.returncode == 1, "CONTROL: another pattern stays blocked"
        assert hit("PERSONAL", "email", "content", "docs/a.md", 1) in hits_for(staging, "docs/a.md")
        return

    if arm == "two-col-row-keeps-name":
        write(staging / named, "nothing private in this body\n")
        write(tsv, header + named + "\towner-identity\n")
        proc = scan(tmp_path, driver, staging)
        assert proc.returncode == 1, "CONTROL: a content row never excuses the name arm"
        assert hit("PERSONAL", "owner-identity", "name", named, 0) in hits_for(staging, named), \
            "CONTROL: the name hit stays with a two-column row"
        return

    if arm == "name-row-excuses-name":
        write(staging / named, "nothing private in this body\n")
        write(tsv, header + named + "\towner-identity\tname\n"
              + "_tools/scan_allow.tsv\towner-identity\tcontent\n")
        proc = scan(tmp_path, driver, staging)
        assert hits_for(staging, named) == set(), "CONTROL: a name row excuses the name hit"
        assert proc.returncode == 0 and report(staging) == "scan_gate: CLEAN\n", \
            "CONTROL: nothing else is reported"
        return

    if arm == "secret-row-ignored-content":
        write(staging / "docs" / "s.md", "value " + plant + "\n")
        write(tsv, header + "docs/s.md\topenai-style-key\tcontent\n")
        proc = scan(tmp_path, driver, staging)
        assert_values_absent([plant], staging, proc)
        assert proc.returncode == 1, "CONTROL: a SECRET row never excuses secret content"
        assert hit("SECRET", "openai-style-key", "content", "docs/s.md", 1) in hits_for(staging, "docs/s.md")
        return

    keyname = "docs/" + AWS_PREFIX + _body(16, "QWERTY2345") + ".txt"
    write(staging / keyname, "clean body under a key-shaped name\n")
    write(tsv, header + keyname + "\taws-key\tname\n")
    proc = scan(tmp_path, driver, staging)
    assert proc.returncode == 1, "CONTROL: a SECRET row never excuses a key-shaped name"
    assert hit("SECRET", "aws-key", "name", keyname, 0) in hits_for(staging, keyname)


# =============================================================================================
# GROUP 13
# =============================================================================================

def _fold_strings(node: ast.AST) -> str | None:
    """Fold ``'a' + 'b'`` (any depth) or a bare string constant into one string; else None."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _fold_strings(node.left), _fold_strings(node.right)
        if left is not None and right is not None:
            return left + right
    return None


def _m1_fixture_node(source: str) -> ast.AST:
    """The string expression written as the self-test's planted-secret fixture (``cfg = {``)."""
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and node.args:
            folded = _fold_strings(node.args[0])
            if folded is not None and folded.startswith("cfg = {"):
                return node.args[0]
    raise AssertionError("CONTROL: the self-test's planted-secret fixture was not found")


@pytest.mark.parametrize("arm", ["candidate-source-self-scan", "fixture-bytes-equal",
                                 "self-test-passes", "unsplit-literal-rejected"])
def test_self_test_fixture_is_still_live(tmp_path: Path, arm: str) -> None:
    """Guards the self-test fixture after the scanner started scanning its own source: the
    planted-secret literal had to be SPLIT so the scanner's own bytes stay clean, and the split
    must still reassemble into the exact original plant or the self-test's dirty arm dies.

    candidate-source-self-scan — CONTROL: the unmodified candidate source scanned as
    ``_tools/scan_gate.py`` is CLEAN (rc 0, exact report).
    fixture-bytes-equal — CONTROL: the runtime-concatenated fixture (folded from the AST, not
    executed) equals the original plant bytes assembled here from fragments.
    self-test-passes — CONTROL: ``--self-test`` exits 0 and prints PASS naming the live arms
    (the old scanner's self-test also passes; this is not a parent RED). The same arm then shows
    the self-test is ABLE to fail: a driver copy with the ``generic-key-assign`` arm deleted must
    exit 1 and print FAIL with ``content_mutations=False`` (CONTROL: the old scanner's self-test
    fails the same way). A hard-coded PASS verdict cannot satisfy this — added after mutant
    verification found reading B (``ok = True``) surviving.
    unsplit-literal-rejected — REPAIRED (old fails: it never read ``_tools``): a copy of the
    candidate source with the fixture re-joined into one literal is rejected by the self-scan
    at exactly that line via ``generic-key-assign``.

    Kills: M-UNSPLIT-LITERAL; M-TOOTHLESS-SELFTEST reading A (a dirty arm that cannot go red)
    and reading B (the verdict hard-coded true).
    """
    driver = make_tool(tmp_path)
    source = SCANNER.read_text(encoding="utf8")
    expected = "cfg = {" + '"' + API_KEY_FIELD + '": "' + "abcDEF123456789" + "xyzKLMNO" + '"}\n'

    if arm == "candidate-source-self-scan":
        staging = make_staging(tmp_path)
        write(staging / "_tools" / "scan_gate.py", source)
        proc = scan(tmp_path, driver, staging)
        assert proc.returncode == 0 and report(staging) == "scan_gate: CLEAN\n", \
            "CONTROL: the candidate's own source is clean under its own scan"
        return

    if arm == "fixture-bytes-equal":
        node = _m1_fixture_node(source)
        assert _fold_strings(node) == expected, "CONTROL: the split fixture reassembles to the original bytes"
        return

    if arm == "self-test-passes":
        proc = run_scan(tmp_path, driver, "--self-test")
        assert proc.returncode == 0, "CONTROL: --self-test passes"
        assert "self-test: PASS" in proc.stdout and "mutations red" in proc.stdout, \
            "CONTROL: the self-test reports its clean and dirty arms live"
        assert "abcDEF123456789" not in proc.stdout + proc.stderr, "CONTROL: fixture value not echoed"
        # The verdict must be computed, not asserted: a driver that lost the generic arm cannot
        # turn its planted-secret fixture red, so its own self-test must say FAIL and exit 1.
        arm_lines = [ln for ln in source.splitlines(keepends=True) if '("generic-key-assign",' in ln]
        assert len(arm_lines) == 1, "CONTROL: the generic arm is defined exactly once in the scanner"
        toothless = make_tool(tmp_path, source=source.replace(arm_lines[0], ""), name="tool_noarm")
        proc = run_scan(tmp_path, toothless, "--self-test")
        assert proc.returncode == 1, "CONTROL: a scanner that lost an arm must FAIL its own self-test"
        assert "self-test: FAIL" in proc.stdout and "content_mutations=False" in proc.stdout, \
            "CONTROL: the failing self-test names the dead content arm"
        assert "self-test: PASS" not in proc.stdout, "CONTROL: no PASS verdict from a toothless scanner"
        return

    node = _m1_fixture_node(source)
    lines = source.splitlines(keepends=True)
    assert node.lineno == node.end_lineno, "CONTROL: the fixture expression sits on one line"
    line = lines[node.lineno - 1]
    unsplit = line[:node.col_offset] + repr(expected) + line[node.end_col_offset:]
    lines[node.lineno - 1] = unsplit
    staging = make_staging(tmp_path)
    write(staging / "_tools" / "scan_gate.py", "".join(lines))
    proc = scan(tmp_path, driver, staging)
    assert "abcDEF123456789" not in proc.stdout + proc.stderr + (report(staging) or ""), \
        "CONTROL: fixture value not echoed"
    assert proc.returncode == 1, "REPAIRED: an unsplit fixture literal in the scanner source is rejected"
    assert hit("SECRET", "generic-key-assign", "content", "_tools/scan_gate.py", node.lineno) in \
        hits_for(staging, "_tools/scan_gate.py"), "REPAIRED: rejected at exactly the re-joined line"


# The regex-special separators a LEGITIMATE first identity term can carry: a hyphenated or dotted
# short hostname, a plus-addressed local-part, a parenthesised alias. Assembled at run time so no
# contiguous term-shaped literal sits in this tracked source.
FIRST_TERM_SEPARATORS = {"hyphen": "-", "dot": ".", "plus": "+", "paren": "("}


@pytest.mark.parametrize("sep", sorted(FIRST_TERM_SEPARATORS))
def test_self_test_accepts_regex_special_first_term(tmp_path: Path, sep: str) -> None:
    """Broken behaviour: ``--self-test`` recovered its planted identity term by re-parsing the
    compiled owner-identity pattern (``re.escape``'d, ``|``-joined), so whenever the FIRST term
    in ``identity_terms.txt`` carried a regex-special character the plant was written WITH its
    escape backslash: the identity and NAME-arm mutations could not go red, the self-test
    printed FAIL with rc 1, and every caller that gates on this self-test — the pre-push hook
    (``guard/hooks/pre-push``) and the installer's activation preflight — refused a legitimate
    terms file; a hyphenated hostname as first term was enough.

    REPAIRED (old fails: rc 1, ``self-test: FAIL`` with ``content_mutations=False``): with the
    special-character term FIRST (``zqx`` + separator + ``term``) and a plain term second, the
    driver's ``--self-test`` exits 0 and prints the scanner's own PASS line naming its live arms.
    CONTROL (old passes): the terms file really leads with the special term; the term is not
    echoed to stdout or stderr.

    Kills: M-ESCAPED-TERM-REPLANT (derive the plant from the compiled pattern again).
    """
    driver = make_tool(tmp_path)
    first = "zqx" + FIRST_TERM_SEPARATORS[sep] + "term"
    terms = driver.parent / "identity_terms.txt"
    terms.write_text(first + "\n" + IDENTITY_TERM + "\n", encoding="utf8")
    assert terms.read_text(encoding="utf8").split("\n")[0] == first, \
        "CONTROL: the special-character term is the FIRST entry the scanner will load"

    proc = run_scan(tmp_path, driver, "--self-test")
    assert first not in proc.stdout + proc.stderr, "CONTROL: the identity term is not echoed"
    assert proc.returncode == 0, \
        f"REPAIRED[{sep}]: --self-test must pass with a regex-special first identity term"
    assert "self-test: PASS" in proc.stdout and "mutations red" in proc.stdout, \
        f"REPAIRED[{sep}]: the self-test prints its PASS line naming the live arms"
    assert "self-test: FAIL" not in proc.stdout, f"REPAIRED[{sep}]: no FAIL verdict"
