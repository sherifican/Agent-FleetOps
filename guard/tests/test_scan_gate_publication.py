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
import errno
import hashlib
import importlib.util
import io
import os
import signal
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


def install_policy(module, report_path: Path, staged: Path) -> None:
    """Call the policy installer the way the publication path does: on a HELD descriptor.

    The installer stopped taking the staged file by NAME at round seventeen. A pathname is what a
    swapped symlink can occupy, and os.chmod on this platform cannot decline to follow one — it is
    not in os.supports_follow_symlinks, and follow_symlinks=False raises NotImplementedError,
    which is not an OSError. Round eighteen moved the DIRECTORY behind a descriptor as well, so the
    installer takes the validated directory fd and basenames: a path joined onto the report
    directory is exactly what a substituted directory re-resolves. Every arm that calls the
    installer directly goes through here, so no arm can quietly keep measuring a contract the code
    stopped offering.
    """
    dirfd = os.open(str(staged.parent), os.O_RDONLY | os.O_DIRECTORY)
    fd = os.open(str(staged), os.O_RDONLY)
    try:
        module._install_posix_acl_policy(dirfd, report_path.name, fd, staged.name)
    finally:
        os.close(fd)
        os.close(dirfd)


def preserve_superseded(module, reports_dir: Path, report_name: str = "scan_report.txt") -> bool:
    """Call preservation the way the publication path does: against the held directory fd."""
    dirfd = os.open(str(reports_dir), os.O_RDONLY | os.O_DIRECTORY)
    try:
        return module._preserve_superseded(dirfd, report_name)
    finally:
        os.close(dirfd)


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
    refusal-clears-stale-clean — formerly xfail(strict), now a LIVE arm (repaired 3a9b87a): after a CLEAN run, a refusal (rc 2)
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

    # refusal-clears-stale-clean (was xfail strict; live since 3a9b87a)
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


# =============================================================================================
# GROUP 14 — a refusal must invalidate a stale CLEAN on EVERY refusal path, and never destroy anything
# =============================================================================================

def test_a_refusal_never_truncates_a_linked_report_target(tmp_path: Path) -> None:
    """Broken behaviour: the refusal writer opened the report path directly, so a report path that is a
    SYMLINK had its target truncated on refusal — destructive, in the very broken-link scenario the
    scanner refuses on.
    CONTROL: first run CLEAN writes a real report. Then the report is replaced by a symlink to a victim
    file and a broken link is planted; the second run refuses (rc 2), the victim keeps its bytes, and
    the report path becomes a regular file carrying the refusal."""
    driver = make_tool(tmp_path)
    staging = make_staging(tmp_path)
    write(staging / "docs" / "readme.md", "nothing private here\n")
    proc = scan(tmp_path, driver, staging)
    assert proc.returncode == 0 and report(staging) == "scan_gate: CLEAN\n", "CONTROL: first run CLEAN"
    victim = write(tmp_path / "victim.txt", "precious bytes\n")
    rp = staging / REPORT_REL
    rp.unlink()
    rp.symlink_to(victim)
    (staging / "docs" / "ghost.txt").symlink_to(tmp_path / "nowhere")
    proc = scan(tmp_path, driver, staging)
    assert proc.returncode == 2, "the second run still refuses"
    assert victim.read_text(encoding="utf8") == "precious bytes\n", "REPAIRED: a linked target is never truncated"
    assert not rp.is_symlink() and rp.is_file(), "REPAIRED: the report path is replaced by a regular file"
    assert (report(staging) or "").startswith("scan_gate: REFUSED"), "and it carries the refusal"


@pytest.mark.parametrize("arm", ["missing-policy", "undecodable-policy"])
def test_a_policy_input_refusal_after_a_clean_run_invalidates_the_stale_report(tmp_path: Path, arm: str) -> None:
    """Broken behaviour: the identity-terms refusal exited before the handler, so a previous CLEAN survived
    it; an undecodable terms file took the module-level OSError/UnicodeError path with the same effect.
    CONTROL: first run CLEAN. Then the policy input is removed / made undecodable; the second run exits 2
    and the report no longer reads CLEAN."""
    driver = make_tool(tmp_path)
    staging = make_staging(tmp_path)
    write(staging / "docs" / "readme.md", "nothing private here\n")
    proc = scan(tmp_path, driver, staging)
    assert proc.returncode == 0 and report(staging) == "scan_gate: CLEAN\n", "CONTROL: first run CLEAN"
    terms = driver.parent / "identity_terms.txt"
    if arm == "missing-policy":
        terms.unlink()
    else:
        write(terms, b"\xff\xfe\x00 not utf8\n")
    proc = scan(tmp_path, driver, staging)
    assert proc.returncode == 2, f"REPAIRED[{arm}]: a policy-input refusal still exits 2"
    assert report(staging) != "scan_gate: CLEAN\n", f"REPAIRED[{arm}]: a stale CLEAN must not survive a policy-input refusal"


def test_a_refusal_never_writes_through_a_symlinked_reports_directory(tmp_path: Path) -> None:
    """Broken behaviour: the refusal writer anchored on dirname(report_path), so a `_reports` directory
    that is a SYMLINK carried the temp file and the atomic replace into an external directory.
    CONTROL: first run CLEAN. Then `_reports` is replaced by a symlink to an external directory holding
    its own scan_report.txt; a broken link is planted; the second run refuses (rc 2) and the external
    file keeps its bytes — nothing is written through the linked parent."""
    driver = make_tool(tmp_path)
    staging = make_staging(tmp_path)
    write(staging / "docs" / "readme.md", "nothing private here\n")
    proc = scan(tmp_path, driver, staging)
    assert proc.returncode == 0 and report(staging) == "scan_gate: CLEAN\n", "CONTROL: first run CLEAN"
    external = tmp_path / "external"
    write(external / "scan_report.txt", "external bytes\n")
    shutil.rmtree(staging / "_reports")
    (staging / "_reports").symlink_to(external, target_is_directory=True)
    (staging / "docs" / "ghost.txt").symlink_to(tmp_path / "nowhere")
    proc = scan(tmp_path, driver, staging)
    assert proc.returncode == 2, "the second run still refuses"
    assert (external / "scan_report.txt").read_text(encoding="utf8") == "external bytes\n", \
        "REPAIRED: nothing is written through a symlinked _reports directory"
    assert (staging / "_reports").is_symlink(), "REPAIRED: the linked parent is left alone, not replaced"


def test_a_read_only_stale_report_no_longer_blocks_the_ordinary_write(tmp_path: Path) -> None:
    """Was: the read-only report file made the ORDINARY write fail, and the arm proved the refusal
    still replaced the stale CLEAN. GROUP 15 made the ordinary writer atomic too, so that state is no
    longer reachable this way: os.replace needs permission on the DIRECTORY, not on the old file. The
    property that a write failure is a refusal is kept below, on a state that can still occur; this arm
    now pins the behaviour the change actually produces, so the improvement cannot regress silently."""
    if os.geteuid() == 0:
        pytest.skip("root ignores file mode bits; this arm needs an unprivileged writer")
    driver = make_tool(tmp_path)
    staging = make_staging(tmp_path)
    write(staging / "docs" / "readme.md", "nothing private here\n")
    proc = scan(tmp_path, driver, staging)
    assert proc.returncode == 0 and report(staging) == "scan_gate: CLEAN\n", "CONTROL: first run CLEAN"
    rp = staging / REPORT_REL
    rp.chmod(0o444)
    try:
        proc = scan(tmp_path, driver, staging)
        assert proc.returncode == 0, "an unwritable STALE report no longer fails the ordinary write"
        assert report(staging) == "scan_gate: CLEAN\n", "the atomic replace put a fresh report in place"
        assert not (staging / REPORT_REL).is_symlink(), "and it is a regular file"
    finally:
        try:
            rp.chmod(0o644)
        except OSError:
            pass


def test_an_ordinary_report_write_failure_is_still_a_named_refusal(tmp_path: Path) -> None:
    """The property the arm above used to carry, on a state that IS still reachable.

    A regular FILE where _reports must be means the ordinary writer cannot create its directory. That is
    an OSError, and the contract is that it becomes a named refusal rather than a pass. The refusal
    writer correctly declines to build anything under a non-directory, so the assertion here is that
    nothing was created and nothing was destroyed — not that a refusal report appeared."""
    driver = make_tool(tmp_path)
    staging = make_staging(tmp_path)
    write(staging / "docs" / "readme.md", "nothing private here\n")
    blocker = staging / "_reports"
    write(blocker, "I AM NOT A DIRECTORY\n")

    proc = scan(tmp_path, driver, staging)

    assert proc.returncode == 2, "an ordinary report-write failure is a refusal, not a pass"
    assert "report-write-error" in proc.stderr, "and it is named"
    assert blocker.read_text(encoding="utf8") == "I AM NOT A DIRECTORY\n", \
        "the blocking file must be left exactly as it was"
    assert blocker.is_file(), "and it must not have been replaced by a directory"

# =============================================================================================
# GROUP 15 — the ORDINARY report writer must not follow a link either
#
# GROUP 14 hardened the refusal writer. Its sibling, write_report, was left with
# makedirs(exist_ok=True) + open(...,"w"): it follows a symlinked _reports directory, truncates a
# symlinked report file, and creates the target of a dangling one — every time reporting rc 0 and
# "scan_gate: CLEAN". GROUP 14 cannot catch this: its fixtures plant the link only AFTER a clean run
# has already created a real directory, so no arm starts with the link in place.
#
# A vacuous pass here asserts only the return code and the report text. Both are already true today
# while the victim is destroyed. Every arm below must open the VICTIM.
# =============================================================================================


def _clean_tree(staging: Path) -> None:
    write(staging / "docs" / "readme.md", "nothing private here\n")


def test_ordinary_clean_write_still_produces_a_real_report(tmp_path: Path) -> None:
    """CONTROL: with no link anywhere, an ordinary clean scan still writes its own report.

    Without this arm the group could be satisfied by refusing everything.
    """
    driver = make_tool(tmp_path)
    staging = make_staging(tmp_path)
    _clean_tree(staging)
    proc = scan(tmp_path, driver, staging)
    assert proc.returncode == 0, "CONTROL: a clean tree still passes"
    assert report(staging) == "scan_gate: CLEAN\n", "CONTROL: and its report is a real file"
    assert not (staging / REPORT_REL).is_symlink(), "CONTROL: written as a regular file"


def test_clean_write_does_not_truncate_a_symlinked_report_file(tmp_path: Path) -> None:
    """A symlinked scan_report.txt must not be written through on the SUCCESS path."""
    driver = make_tool(tmp_path)
    staging = make_staging(tmp_path)
    _clean_tree(staging)
    victim = tmp_path / "victim.txt"
    write(victim, "PRECIOUS BYTES\n")
    (staging / "_reports").mkdir(parents=True, exist_ok=True)
    (staging / REPORT_REL).symlink_to(victim)

    proc = scan(tmp_path, driver, staging)

    assert victim.read_text(encoding="utf8") == "PRECIOUS BYTES\n", \
        "the victim of a symlinked report path must survive an ordinary CLEAN scan"
    assert proc.returncode == 2, "following a linked report path is a refusal, not a pass"
    assert "report-path-unsafe" in proc.stderr, "and the refusal names itself"


def test_clean_write_does_not_follow_a_symlinked_reports_directory(tmp_path: Path) -> None:
    """A symlinked _reports directory, planted BEFORE the first run, must not be written through."""
    driver = make_tool(tmp_path)
    staging = make_staging(tmp_path)
    _clean_tree(staging)
    outside = tmp_path / "outside_reports"
    outside.mkdir()
    write(outside / "scan_report.txt", "SOMEONE ELSE'S REPORT\n")
    (staging / "_reports").symlink_to(outside, target_is_directory=True)

    proc = scan(tmp_path, driver, staging)

    assert (outside / "scan_report.txt").read_text(encoding="utf8") == "SOMEONE ELSE'S REPORT\n", \
        "a linked _reports directory must not receive the scan's report"
    assert proc.returncode == 2, "a linked report directory is a refusal, not a pass"
    assert "report-path-unsafe" in proc.stderr, "and the refusal names itself"


def test_clean_write_does_not_create_the_target_of_a_dangling_report_link(tmp_path: Path) -> None:
    """A dangling report symlink must not be used to CREATE a file outside the staging tree."""
    driver = make_tool(tmp_path)
    staging = make_staging(tmp_path)
    _clean_tree(staging)
    target = tmp_path / "not_yet_there.txt"
    (staging / "_reports").mkdir(parents=True, exist_ok=True)
    (staging / REPORT_REL).symlink_to(target)

    proc = scan(tmp_path, driver, staging)

    assert not target.exists(), "a dangling report link must not bring its target into existence"
    assert proc.returncode == 2, "a linked report path is a refusal, not a pass"
    assert "report-path-unsafe" in proc.stderr, "and the refusal names itself"


# =============================================================================================
# GROUP 16 — the atomic writer must not silently narrow who can READ the report
#
# Adversarial review, S1. GROUP 15 replaced open(...,"w") with mkstemp + os.replace. mkstemp
# creates at 0600 by design and os.replace preserves the source mode, so the committed report
# went from umask-normal (0644 on this box) to owner-only. The report is the artifact a human
# or a later stage reads to see WHY publication was refused; a hardening pass that makes it
# unreadable to everyone but the scanning uid has traded one silent failure for another.
#
# The expected mode is derived from the running umask rather than hard-coded, so the test
# states the property (an ordinary file, readable like any other) and not one box's octal.
# =============================================================================================


def test_the_report_is_left_readable_like_an_ordinary_file(tmp_path: Path) -> None:
    driver = make_tool(tmp_path)
    staging = make_staging(tmp_path)
    write(staging / "docs" / "readme.md", "nothing private here\n")

    proc = scan(tmp_path, driver, staging)
    assert proc.returncode == 0 and report(staging) == "scan_gate: CLEAN\n", "CONTROL: a clean run"

    # The reference is created in the REPORT'S OWN directory, not just somewhere with the same
    # umask: a default ACL on that directory changes what an ordinary create produces there, and a
    # reference taken elsewhere would agree with a umask formula and miss it.
    reference = staging / "_reports" / "umask_reference.txt"
    reference.write_text("what an ordinary create produces here\n", encoding="utf-8")
    # & ~0o007: a new report is published at an ordinary create MINUS every bit for "other".
    # Review measured a findings report at 0644 under the ordinary login umask and 0666 under
    # umask 0; the report carries the class, path and line of every secret found.
    expected = stat.S_IMODE(reference.stat().st_mode) & ~0o007
    reference.unlink()

    # The premise of this arm INVERTED at round eleven. It was written when mkstemp's 0600
    # surviving into the artifact was the defect; owner-only is now the deliberate policy, because
    # inheriting an ordinary create published findings at 0644 under an ordinary umask. What the
    # arm still has to prove is that the OWNER can open it and that nobody else can.
    actual = stat.S_IMODE((staging / REPORT_REL).stat().st_mode)
    assert actual & stat.S_IRUSR and actual & stat.S_IWUSR, (
        f"the committed report is mode {actual:04o}: its own owner cannot read or write it")
    assert not actual & 0o077, (
        f"the committed report is mode {actual:04o}, wider than owner-only. An ordinary create "
        f"here would give {expected:04o}, and that is exactly what must NOT be inherited")


def test_a_refusal_report_is_readable_too(tmp_path: Path) -> None:
    """The refusal path is the one a reader needs MOST, and it has its own mkstemp call.

    Round eleven superseded the other-only cap this arm was first rewritten for. A new report is
    OWNER-ONLY: the gate refused the argument that group access expresses a sharing decision,
    since an ordinary create grants the primary group access with nobody deciding anything. So the
    assertion is no longer "an ordinary create minus other" — it is that nothing beyond the owner
    is granted. Round seventeen removed the second half of the sentence that used to stand here,
    which said a directory policy stricter than 0600 still wins by intersection: it does not any
    more, because the only thing that reliably arrived through that intersection was the umask
    stripping the owner's own bits off the findings. The mode is a constant now.
    """
    driver = make_tool(tmp_path)
    staging = make_staging(tmp_path)
    write(staging / "docs" / "readme.md", "nothing private here\n")
    (staging / "_reports").mkdir(parents=True, exist_ok=True)
    (staging / "_reports" / "scan_report.txt").symlink_to(tmp_path / "elsewhere.txt")

    proc = scan(tmp_path, driver, staging)
    assert proc.returncode == 2, "CONTROL: a linked report path is still a refusal"

    reference = staging / "_reports" / "umask_reference_refusal.txt"
    reference.write_text("what an ordinary create produces here\n", encoding="utf-8")
    expected = stat.S_IMODE(reference.stat().st_mode) & ~0o007   # other capped off, as above
    reference.unlink()

    rp = staging / REPORT_REL
    assert rp.is_file() and not rp.is_symlink(), "the refusal wrote a real report"
    actual = stat.S_IMODE(rp.stat().st_mode)
    assert actual & stat.S_IRUSR, (
        f"the refusal report is mode {actual:04o}: its own owner cannot read it")
    assert not actual & 0o077, (
        f"the refusal report is mode {actual:04o}, wider than owner-only; an ordinary create "
        f"here would give {expected:04o}. The refusal path gets the same policy as any other")


# =============================================================================================
# GROUP 17 — the report's mode is a CONTRACT, and forcing a umask-derived one overrides it
#
# Round-2 adversarial review, F2. GROUP 16 fixed mkstemp's 0600 by forcing 0o666 & ~umask. That
# overcorrected in three measured ways: an existing PRIVATE report (0600) was widened, an existing
# group-writable report (0660) lost group write, and in a directory carrying a default ACL the
# report no longer matched what an ordinary create there produces.
#
# The property is not "some particular octal", and it changed at round eleven. REPLACING a report
# must not change who could read or write it, NARROWED so "other" never gains access — the report
# being replaced lives in the untrusted tree, and a committed one checks out 0644. CREATING one
# lands OWNER-ONLY: the old rule was "exactly where an ordinary create lands", which is how a
# findings report came to be published 0644 under an ordinary umask. A directory policy stricter
# than 0600 still wins, because the inherited mode is intersected and an intersection cannot
# widen. The exact-inheritance promise is deliberately broken, not quietly preserved.
# =============================================================================================


def _mode(p: Path) -> int:
    return stat.S_IMODE(p.stat().st_mode)


def _ordinary_create_mode(directory: Path, name: str = ".ordinary_probe") -> int:
    """What a plain create in THIS directory produces — umask and default ACL included."""
    probe = directory / name
    probe.write_text("probe\n", encoding="utf-8")
    try:
        return _mode(probe)
    finally:
        probe.unlink()


# 0o660 was here until round fourteen and now narrows to 0o640: group WRITE on a report that came
# out of the untrusted tree is the plant an independent review demonstrated — any member of the
# staging tree's group overwrites the published report with a CLEAN line after the scanner returns.
# The presets below are the modes preservation still keeps EXACTLY, which is what this arm is for;
# the narrowing itself is pinned by test_a_planted_existing_report_cannot_widen_the_findings_it_is
# _replaced_by. 0o400 is included so the arm also covers a policy stricter than the cap.
# 0o640 joined 0o660 in being narrowed at round fifteen: both review legs refused keeping group
# READ on a replacement, because a planted 0640 hands the file's group the class, path and line of
# every secret found and "there was an existing file" is not a sharing decision by anyone who
# matters when that file came out of the untrusted tree. What still round-trips EXACTLY is
# owner-only and stricter.
# ROUND SEVENTEEN TURNED THIS ARM AROUND, and the reason is worth keeping. What a replacement
# preserved used to be "the mode it already had, narrowed". 0o400 is the case that decided it: a
# report the OPERATOR had made read-only and a report the UMASK had made read-only are the same
# four bits, and this writer cannot tell them apart. Removing an owner's own bits from a file that
# owner still owns enforces nothing — the uid restores them at will — so the only thing the
# preservation reliably delivered was a findings report its reader could not open. 0o200 is
# included because it is what umask 0400 actually produces.
@pytest.mark.parametrize("preset", [0o600, 0o400, 0o200])
def test_replacing_a_report_publishes_the_one_rule_whatever_it_replaced(
        tmp_path: Path, preset: int) -> None:
    if os.geteuid() == 0:
        pytest.skip("root ignores mode bits; this needs an unprivileged writer")
    driver = make_tool(tmp_path)
    staging = make_staging(tmp_path)
    write(staging / "docs" / "readme.md", "nothing private here\n")

    proc = scan(tmp_path, driver, staging)
    assert proc.returncode == 0, "CONTROL: a clean run, so a report exists to replace"
    rp = staging / REPORT_REL
    rp.chmod(preset)

    proc = scan(tmp_path, driver, staging)
    assert proc.returncode == 0 and report(staging) == "scan_gate: CLEAN\n", "it rewrote the report"
    assert _mode(rp) == 0o600, (
        f"the report stood at {preset:04o} and was republished at {_mode(rp):04o}; ONE RULE means "
        "a replacement lands at 0600 — owner read and write, nothing for group, nothing for other")
    assert rp.read_text(encoding="utf-8") == "scan_gate: CLEAN\n", (
        "and the republished report must be readable by the uid that wrote it")


def test_a_new_report_lands_where_an_ordinary_create_in_that_directory_lands(tmp_path: Path) -> None:
    """Covers the default-ACL case without needing to know whether one is present.

    Round eleven superseded the other-only cap this arm was first rewritten for. A new report is
    OWNER-ONLY: the gate refused the argument that group access expresses a sharing decision,
    since an ordinary create grants the primary group access with nobody deciding anything. So the
    assertion is no longer "an ordinary create minus other" — it is that nothing beyond the owner
    is granted. Round seventeen removed the second half of the sentence that used to stand here,
    which said a directory policy stricter than 0600 still wins by intersection: it does not any
    more, because the only thing that reliably arrived through that intersection was the umask
    stripping the owner's own bits off the findings. The mode is a constant now.
    """
    if os.geteuid() == 0:
        pytest.skip("root ignores mode bits; this needs an unprivileged writer")
    driver = make_tool(tmp_path)
    staging = make_staging(tmp_path)
    write(staging / "docs" / "readme.md", "nothing private here\n")
    reports_dir = staging / "_reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    expected = _ordinary_create_mode(reports_dir) & ~0o007   # other is capped off

    proc = scan(tmp_path, driver, staging)
    assert proc.returncode == 0, "CONTROL: a clean run"
    got = _mode(staging / REPORT_REL)
    assert not got & 0o077, (
        f"a new report is {got:04o}, wider than owner-only. An ordinary create in this directory "
        f"gives {expected:04o}; inheriting that is what published findings at 0644")
    assert got & stat.S_IRUSR, f"and its owner must still be able to read it, not {got:04o}"


def test_a_default_acl_that_would_lock_the_owner_out_does_not_decide_the_report(
        tmp_path: Path) -> None:
    """The arm that changed direction at round seventeen, and why.

    Until then the mode was INTERSECTED with what the directory would give an ordinary create, so
    a default ACL stricter than owner-only decided the published mode. A cold review leg measured
    the consequence: a directory whose default ACL grants the owner read only, or nothing at all,
    published the findings at 0400 or 0000 — a report carrying the class, path and line of every
    secret found, in a file the operator who asked for it cannot open.

    Removing an owner's own bits from a file that owner still owns is not a stricter policy. The
    uid restores them whenever it likes, so nothing was ever enforced by it; what the intersection
    actually bought was an unreadable artifact that reads, to a human, exactly like a scanner that
    found nothing. The directory's answer is still honoured in the direction that can hurt — a
    default ACL granting OTHER is stripped rather than carried, which the arm below measures.
    """
    if os.geteuid() == 0:
        pytest.skip("root ignores mode bits; this needs an unprivileged writer")
    if shutil.which("setfacl") is None:
        pytest.skip("setfacl is not installed; the default-ACL contract cannot be measured here")
    driver = make_tool(tmp_path)
    staging = make_staging(tmp_path)
    write(staging / "docs" / "readme.md", "nothing private here\n")
    reports_dir = staging / "_reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    applied = subprocess.run(["setfacl", "-d", "-m", "u::r,g::-,o::-", str(reports_dir)],
                             capture_output=True, text=True)
    if applied.returncode != 0:
        pytest.skip(f"the filesystem refused a default ACL: {applied.stderr.strip()[:80]}")
    inherited = _ordinary_create_mode(reports_dir)
    if inherited & 0o200:
        pytest.skip(f"this filesystem gave an ordinary create {inherited:04o} despite a read-only "
                    "default ACL; there is nothing for this arm to measure")

    proc = scan(tmp_path, driver, staging)
    assert proc.returncode == 0, "CONTROL: a clean run"
    rp = staging / REPORT_REL
    got = _mode(rp)
    assert got == 0o600, (
        f"an ordinary create in this directory lands at {inherited:04o} and the report came out "
        f"at {got:04o}. The one rule is 0600: a default ACL that would lock the owner out of the "
        "findings must not decide the mode of the report those findings are published in")
    assert rp.read_text(encoding="utf-8") == "scan_gate: CLEAN\n", (
        f"CONTROL: the report must be readable by the uid that wrote it; an ordinary create here "
        f"gives {inherited:04o}, which is the mode this arm exists to stop being used")


def test_a_default_acl_granting_other_does_not_widen_a_new_report(tmp_path: Path) -> None:
    """The direction that must NOT be inherited, which is the whole reason the rule changed.

    A default ACL granting other-read is exactly the configuration that published a findings
    report readable by every account on the box. Inheriting a directory policy is only safe
    downward.
    """
    if os.geteuid() == 0:
        pytest.skip("root ignores mode bits; this needs an unprivileged writer")
    if shutil.which("setfacl") is None:
        pytest.skip("setfacl is not installed; the default-ACL contract cannot be measured here")
    driver = make_tool(tmp_path)
    staging = make_staging(tmp_path)
    write(staging / "docs" / "readme.md", "nothing private here\n")
    reports_dir = staging / "_reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    applied = subprocess.run(["setfacl", "-d", "-m", "u::rw,g::r,o::r", str(reports_dir)],
                             capture_output=True, text=True)
    if applied.returncode != 0:
        pytest.skip(f"the filesystem refused a default ACL: {applied.stderr.strip()[:80]}")
    permissive = _ordinary_create_mode(reports_dir)
    if not permissive & stat.S_IROTH:
        pytest.skip(f"this filesystem gave an ordinary create {permissive:04o}; the ACL did not "
                    "grant other, so there is no widening to refuse")

    proc = scan(tmp_path, driver, staging)
    assert proc.returncode == 0, "CONTROL: a clean run"
    got = _mode(staging / REPORT_REL)
    assert not got & 0o077, (
        f"the report is {got:04o}: an ordinary create here gives {permissive:04o}, and that "
        "policy was inherited. A directory inside the scanned tree does not get to decide who "
        "may read the locations of the secrets found in it")


def test_a_refusal_report_keeps_the_mode_the_report_it_replaces_had(tmp_path: Path) -> None:
    """The refusal writer has its OWN chmod call, and the arms above only exercise the ordinary one.

    Found by reverting each source hunk in turn and requiring something to go red: this one stayed
    green, which means the refusal path's mode contract was shipped with nothing holding it. The
    refusal report is the artifact a reader opens to find out why publication stopped, so its
    permissions matter at least as much as the clean one's.
    """
    if os.geteuid() == 0:
        pytest.skip("root ignores mode bits; this needs an unprivileged writer")
    driver = make_tool(tmp_path)
    staging = make_staging(tmp_path)
    write(staging / "docs" / "readme.md", "nothing private here\n")

    proc = scan(tmp_path, driver, staging)
    assert proc.returncode == 0, "CONTROL: a clean run, so there is a report to replace"
    rp = staging / REPORT_REL
    rp.chmod(0o600)

    locked = staging / "locked"
    write(locked / "inner.txt", "unreachable\n")
    locked.chmod(0)
    try:
        try:
            os.scandir(str(locked)).close()
            pytest.skip("the OS does not enforce directory mode 000 here (root?); arm not measurable")
        except PermissionError:
            pass
        proc = scan(tmp_path, driver, staging)
        assert proc.returncode == 2, "CONTROL: an unreadable directory refuses the scan"
        assert report(staging) is not None and "REFUSED" in report(staging), \
            "CONTROL: the refusal writer actually replaced the stale CLEAN, or nothing was measured"
        assert stat.S_IMODE(rp.stat().st_mode) == 0o600, (
            f"the report was 0600 and the refusal left it {stat.S_IMODE(rp.stat().st_mode):04o}; "
            "replacing a private report with a refusal must not publish it more widely")
    finally:
        locked.chmod(0o755)


# =============================================================================================
# GROUP 19 — equal mode bits do not mean equal access
#
# Round-3 adversarial review, F2. Preserving st_mode across the atomic replace looked like it
# preserved the permission contract. It does not: POSIX ACLs live in an extended attribute, not in
# the mode bits, so a report carrying a named ACL entry comes back with the DIRECTORY's default ACL
# instead of its own — same four octal digits, different set of people who can read it.
#
# Measured in review: before `user::rw-; user:1000:r--; group::---; mask::r--`, after
# `user::rw-; user:1000:r--; group::r--; mask::r--`, with the mode 0640 on both sides. The group
# gained read access and nothing in the mode said so.
# =============================================================================================

ACL_XATTR = "system.posix_acl_access"


def _acl(path: Path):
    try:
        return os.getxattr(str(path), ACL_XATTR)
    except OSError:
        return None


def test_replacing_a_report_strips_the_access_control_list(tmp_path: Path) -> None:
    """SUPERSEDED SUBJECT: this arm asserted the ACL was PRESERVED. Both review legs refused that.

    Copying the old report's ACL was the mode cap's blind channel. A named-user entry on a planted
    report was copied verbatim onto the findings inode, and an inherited default ACL made
    "owner-only" true of the mode bits and false of the actual access. The report now carries NO
    ACL: removing one is always narrowing, so it cannot introduce the failure it prevents.

    Ownership is still preserved — that is a different question from audience, and giving a file
    away is not something this process can undo.
    """
    if os.geteuid() == 0:
        pytest.skip("root ignores these checks; this needs an unprivileged writer")
    if shutil.which("setfacl") is None:
        pytest.skip("setfacl is not installed; the ACL contract cannot be measured here")
    driver = make_tool(tmp_path)
    staging = make_staging(tmp_path)
    write(staging / "docs" / "readme.md", "nothing private here\n")

    proc = scan(tmp_path, driver, staging)
    assert proc.returncode == 0, "CONTROL: a clean run, so there is a report to replace"
    rp = staging / REPORT_REL

    # A DEFAULT ACL on the DIRECTORY, not just an ACL on the old file. The published report is a
    # fresh mkstemp inode: it never inherits the old FILE's ACL, only the directory's default one.
    # Without this the arm passes whether or not the strip happens — measured, by disabling the
    # strip and watching it stay green. An arm that cannot fail is not evidence.
    reports_dir = staging / "_reports"
    inherited = subprocess.run(["setfacl", "-d", "-m", f"u:{os.geteuid()}:r,g::r", str(reports_dir)],
                               capture_output=True, text=True)
    if inherited.returncode != 0:
        pytest.skip(f"the filesystem refused a default ACL: {inherited.stderr.strip()[:80]}")
    named = subprocess.run(["setfacl", "-m", f"u:{os.geteuid()}:r", "--", str(rp)],
                           capture_output=True, text=True)
    if named.returncode != 0:
        pytest.skip(f"the filesystem refused an ACL entry: {named.stderr.strip()[:80]}")
    assert _acl(rp) is not None, "CONTROL: the report really does carry an ACL going in"
    probe = reports_dir / ".inherit_probe"
    probe.write_text("x", encoding="utf-8")
    inherits = _acl(probe) is not None
    probe.unlink()
    if not inherits:
        pytest.skip("this filesystem does not hand a new file the directory default ACL")

    proc = scan(tmp_path, driver, staging)
    assert proc.returncode == 0 and report(staging) == "scan_gate: CLEAN\n", "it rewrote the report"

    assert _acl(rp) is None, (
        "the published report still carries an ACL. A named entry on a planted report is copied "
        "straight onto the findings inode that way, and the mode cap cannot see it")
    assert not stat.S_IMODE(rp.stat().st_mode) & 0o077, (
        f"and it must be owner-only, not {stat.S_IMODE(rp.stat().st_mode):04o}")


def test_a_refusal_report_strips_the_access_control_list_too(tmp_path: Path) -> None:
    """The refusal path gets the same policy, and had to be asserted separately once before.

    The refusal writer has its own mkstemp call and its own publish, so a policy proven on the
    ordinary path says nothing about it — round fourteen hardened the report DIRECTORY in
    write_report only, and the gate found the refusal path still writing into an unhardened one.
    """
    if os.geteuid() == 0:
        pytest.skip("root ignores these checks; this needs an unprivileged writer")
    if shutil.which("setfacl") is None:
        pytest.skip("setfacl is not installed; the ACL contract cannot be measured here")
    driver = make_tool(tmp_path)
    staging = make_staging(tmp_path)
    write(staging / "docs" / "readme.md", "nothing private here\n")
    proc = scan(tmp_path, driver, staging)
    assert proc.returncode == 0, "CONTROL: a clean run, so there is a report to replace"
    rp = staging / REPORT_REL

    # A DEFAULT ACL on the DIRECTORY. The refusal republishes through its own mkstemp inode, which
    # never inherits the old FILE's ACL — only the directory's default one. Gate review caught this
    # arm still unable to fail after I claimed I had fixed both strip arms: I seeded the ordinary
    # one and asserted it of both. An arm declared fixed is worse than one known broken.
    reports_dir = staging / "_reports"
    inherited = subprocess.run(["setfacl", "-d", "-m", f"u:{os.geteuid()}:r,g::r", str(reports_dir)],
                               capture_output=True, text=True)
    if inherited.returncode != 0:
        pytest.skip(f"the filesystem refused a default ACL: {inherited.stderr.strip()[:80]}")
    probe = reports_dir / ".inherit_probe"
    probe.write_text("x", encoding="utf-8")
    inherits = _acl(probe) is not None
    probe.unlink()
    if not inherits:
        pytest.skip("this filesystem does not hand a new file the directory default ACL")
    named = subprocess.run(["setfacl", "-m", f"u:{os.geteuid()}:r", "--", str(rp)],
                           capture_output=True, text=True)
    if named.returncode != 0:
        pytest.skip(f"the filesystem refused an ACL entry: {named.stderr.strip()[:80]}")
    assert _acl(rp) is not None, "CONTROL: the report carries an ACL going in"

    (staging / "docs" / "ghost.txt").symlink_to(tmp_path / "nowhere")
    proc = scan(tmp_path, driver, staging)
    assert proc.returncode == 2, "CONTROL: the second run refuses"

    assert _acl(rp) is None, "a refusal published a report still carrying the old ACL"
    assert not stat.S_IMODE(rp.stat().st_mode) & 0o077, (
        f"and it must be owner-only, not {stat.S_IMODE(rp.stat().st_mode):04o}")


def test_a_report_with_no_acl_does_not_inherit_the_directorys_default(tmp_path: Path) -> None:
    """Absence of an ACL is a policy, not an absence of one.

    The old helper read "no ACL on the source" as "nothing to preserve" and returned. But the
    temporary file was created inside the reports directory, so it already carried that
    directory's DEFAULT ACL — and the replace published it. Every mode bit matches across the
    swap and the set of people who can read the report is different.
    """
    if os.geteuid() == 0:
        pytest.skip("root ignores these checks; this needs an unprivileged writer")
    if shutil.which("setfacl") is None:
        pytest.skip("setfacl is not installed; the ACL contract cannot be measured here")
    driver = make_tool(tmp_path)
    staging = make_staging(tmp_path)
    write(staging / "docs" / "readme.md", "nothing private here\n")

    proc = scan(tmp_path, driver, staging)
    assert proc.returncode == 0, "CONTROL: a clean run, so there is a report to replace"
    rp = staging / REPORT_REL
    reports_dir = staging / "_reports"

    stripped = subprocess.run(["setfacl", "-b", "--", str(rp)], capture_output=True, text=True)
    if stripped.returncode != 0:
        pytest.skip(f"could not strip the report's ACL: {stripped.stderr.strip()[:80]}; without a "
                    "mode-only report this arm has nothing to distinguish")
    # A default ACL of the three BASE entries alone lives in the mode bits and creates no extended
    # attribute, so an inherited policy would be invisible here and this arm would pass against
    # any implementation at all. A NAMED entry is what forces a real xattr into existence. Caught
    # by running this arm against the old scanner and watching it come back green.
    applied = subprocess.run(
        ["setfacl", "-d", "-m", f"u::rw,g::r,o::-,u:{os.geteuid()}:r", str(reports_dir)],
        capture_output=True, text=True)
    if applied.returncode != 0:
        pytest.skip(f"the filesystem refused a default ACL: {applied.stderr.strip()[:80]}")
    canary = reports_dir / ".inheritance_canary"
    canary.write_text("x", encoding="utf-8")
    try:
        inherited = os.getxattr(str(canary), ACL_XATTR)
    except OSError as exc:
        # _acl() swallows every OSError, so an EIO here would have read as "no inheritance" and
        # skipped — a measurement FAILURE wearing an environmental absence's clothes. Only the
        # absence codes may skip; anything else is a broken instrument and must be loud.
        if exc.errno not in (errno.ENODATA, getattr(errno, "ENOATTR", errno.ENODATA),
                             errno.EOPNOTSUPP, errno.ENOTSUP):
            raise AssertionError(
                f"CONTROL: could not measure ACL inheritance ({exc.strerror}); this arm cannot "
                "tell a preserved policy from an inherited one and must not report either") from exc
        inherited = None
    finally:
        canary.unlink()
    if inherited is None:
        pytest.skip("CONTROL: new files here inherit no ACL xattr, so this arm cannot distinguish "
                    "a preserved policy from an inherited one")
    if _acl(rp) is not None:
        pytest.skip("this filesystem keeps an access ACL on the report; the arm needs one without")
    before_mode = stat.S_IMODE(rp.stat().st_mode)

    proc = scan(tmp_path, driver, staging)
    assert proc.returncode == 0, "it rewrote the report"

    assert _acl(rp) is None, (
        "the replaced report inherited the DIRECTORY's default ACL. The old report's policy was "
        "its mode alone; handing the new one an inherited ACL changes who may read it while "
        "every mode bit stays identical — the same failure as round 3, in the other direction")
    assert stat.S_IMODE(rp.stat().st_mode) == before_mode, "and the mode is still preserved"


def test_the_staged_report_is_never_wider_than_the_policy_being_installed(tmp_path: Path) -> None:
    """SUBJECT NARROWED: this arm watched the ORDER of an ACL install, and there is no longer one.

    It was written because reverting the ACL/chmod order left the whole suite green: the staged
    file briefly carried group and other access under the inherited policy before the intended one
    landed. Round fifteen stopped installing ACLs entirely, so that specific window is gone — but
    the property behind it is not, and it is the reason this arm survives rather than being
    deleted: at NO point may the staged inode be wider than the policy being installed, because an
    exposure a later call narrows again is still an exposure while it lasts.

    Every permission-changing call is recorded in order, not just the last one, since a mutant that
    widens and then narrows satisfies any end-state assertion.
    """
    if os.geteuid() == 0:
        pytest.skip("root ignores these checks; this needs an unprivileged writer")
    driver = make_tool(tmp_path)
    module = import_driver(driver, "policy_order")
    reports_dir = tmp_path / "_reports"
    reports_dir.mkdir()
    rp = reports_dir / "scan_report.txt"
    rp.write_text("scan_gate: CLEAN\n", encoding="utf-8")
    rp.chmod(0o600)

    staged = reports_dir / ".scan_report_staged"
    staged.write_text("scan_gate: REFUSED input-error\n", encoding="utf-8")
    staged.chmod(0o600)

    # The mode call moved from a pathname chmod to fchmod on the held descriptor at round
    # seventeen, so the observation moved with it. Reading the mode back through os.fstat(fd)
    # rather than by name is the point: a name can be swapped between the call and the readback,
    # which is the defect this whole move closes.
    real_fchmod = module.os.fchmod
    seen: list[int] = []

    def watching_fchmod(fd, mode, *args, **kwargs):
        result = real_fchmod(fd, mode, *args, **kwargs)
        seen.append(stat.S_IMODE(os.fstat(fd).st_mode))
        return result

    module.os.fchmod = watching_fchmod
    try:
        install_policy(module, rp, staged)
    finally:
        module.os.fchmod = real_fchmod

    assert seen, "CONTROL: no chmod on the staged file was observed, so nothing was measured"
    wider = [oct(m) for m in seen if m & 0o077]
    assert not wider, (
        f"the staged report was wider than owner-only at: {wider}. An exposure that a later call "
        "narrows again is still an exposure while it lasts")
    final = stat.S_IMODE(staged.stat().st_mode)
    assert not final & 0o077, f"and it must end owner-only, not {final:04o}"


def test_a_refusal_that_cannot_publish_replaces_the_stale_clean(tmp_path: Path, monkeypatch) -> None:
    """The half of the permission change nobody had specified.

    Making the policy helper RAISE gave the refusal writer a failure it had no answer for: its
    outer handler swallowed it and left the previous report in place. That report can say CLEAN
    next to an rc 2, which is the one outcome this writer exists to prevent.

    The first fix UNLINKED it, and this arm asserted the removal. Round-5 review found that wrong:
    not every old report says CLEAN, one carrying HITS is evidence worth more than absence, and
    anyone able to provoke a refusal was handed an erasure primitive. The name is now never
    dropped — a private 0600 refusal replaces the old bytes atomically instead.
    """
    driver = make_tool(tmp_path)
    module = import_driver(driver, "refusal_unpublishable")
    staging = tmp_path / "staging"
    reports_dir = staging / "_reports"
    reports_dir.mkdir(parents=True)
    rp = reports_dir / "scan_report.txt"
    rp.write_text("scan_gate: CLEAN\n", encoding="utf-8")

    def refuses(dirfd, src_name, dst_fd, dst_name):
        raise OSError("report-permission-preservation-failed")

    monkeypatch.setattr(module, "_install_posix_acl_policy", refuses)
    module._write_refusal_report(str(staging), module.ScanRefused("report-path-unsafe '_reports'"))

    assert rp.exists(), (
        "the canonical report NAME was dropped. An earlier revision unlinked here; round-5 review "
        "found that destroys an old report carrying HITS, and hands anyone who can provoke a "
        "refusal an erasure primitive through this writer's own authority")
    body = rp.read_text(encoding="utf-8")
    assert body == "scan_gate: REFUSED report-path-unsafe\n", (
        f"the stale CLEAN must be replaced by the refusal, got {body!r}")
    mode = stat.S_IMODE(rp.stat().st_mode)
    assert mode & 0o077 == 0, (
        f"the fallback report published at {mode:04o}: it could not establish the old policy, so "
        "it must not grant more than the private mode it already had")
    leftovers = sorted(p.name for p in reports_dir.iterdir() if p.name.startswith(".scan_report_"))
    assert not leftovers, f"and the temporary file must not be abandoned either: {leftovers}"


def test_a_recoverable_group_difference_is_repaired_rather_than_refused(tmp_path: Path,
                                                                        monkeypatch) -> None:
    """Round-5 review: two defensible changes composed into deletion.

    The ownership guard refused whenever the old report's gid differed from the staged file's, and
    the refusal writer's fallback then removed the report. But a group difference is ordinary and
    the process can usually just fix it: a report written under newgrp, an owner's chgrp, or a
    setgid report directory all produce one with nobody hostile involved. Refusing there spent a
    real artifact on a condition one chown closes.

    The difference is simulated at the stat boundary because making a real one needs membership in
    a second group, which a test cannot assume.
    """
    driver = make_tool(tmp_path)
    module = import_driver(driver, "gid_repair")
    reports_dir = tmp_path / "_reports"
    reports_dir.mkdir()
    rp = reports_dir / "scan_report.txt"
    rp.write_text("scan_gate: CLEAN\n", encoding="utf-8")
    rp.chmod(0o640)
    staged = reports_dir / ".scan_report_staged"
    staged.write_text("scan_gate: REFUSED input-error\n", encoding="utf-8")
    staged.chmod(0o600)

    real_lstat = module.os.lstat
    real_fstat = module.os.fstat
    other_gid = os.getgid() + 1
    staged_ino = staged.stat().st_ino

    chowns: list[dict] = []
    # The simulated chown MOVES the simulated group, exactly where a real one would. An earlier
    # draft made the post-repair verification succeed as soon as ANY chown was recorded, so
    # chowning the WRONG FILE passed the arm — measured by the round-6 reviewer, which ran
    # chown(src, ...) against these assertions and watched them go green. The identity assertion
    # is now on the INODE behind the descriptor, which is strictly harder to fake than a path
    # string: a descriptor pointing at the old report fails it.
    # Keyed by BOTH spellings: round eighteen made the installer read the old report as
    # os.lstat(BASENAME, dir_fd=...), and a fixture keyed only on the full path stopped applying
    # silently — the repair path was never entered and only this arm's CONTROL noticed.
    simulated_path_gid: dict[str, int] = {str(rp): other_gid, rp.name: other_gid}
    simulated_fd_gid: dict[int, int] = {}

    def lstat_with_a_different_group(path, *args, **kwargs):
        # Round eighteen made the installer read the old report as os.lstat(BASENAME, dir_fd=...),
        # so a fixture keyed on the full path silently stopped applying and the repair path was
        # never entered at all. The arm's own CONTROL caught that, which is what it is for.
        st = real_lstat(path, *args, **kwargs)
        gid = simulated_path_gid.get(str(path)) or simulated_path_gid.get(os.path.basename(str(path)))
        if gid is not None:
            return os.stat_result(tuple(st)[:5] + (gid,) + tuple(st)[6:])
        return st

    def fstat_with_a_different_group(fd, *args, **kwargs):
        st = real_fstat(fd, *args, **kwargs)
        gid = simulated_fd_gid.get(fd)
        if gid is not None:
            return os.stat_result(tuple(st)[:5] + (gid,) + tuple(st)[6:])
        return st

    def recording_fchown(fd, uid, gid, *args, **kwargs):
        chowns.append({"ino": real_fstat(fd).st_ino, "uid": uid, "gid": gid})
        simulated_fd_gid[fd] = gid                # a real chown needs a second group a test lacks

    def forbidden_chown(*args, **kwargs):
        raise AssertionError(
            "the repair used a PATHNAME chown; a name can be swapped for a symlink between the "
            "regular-file check and the call, and the descriptor is what cannot be")

    monkeypatch.setattr(module.os, "lstat", lstat_with_a_different_group)
    monkeypatch.setattr(module.os, "fstat", fstat_with_a_different_group)
    monkeypatch.setattr(module.os, "fchown", recording_fchown)
    monkeypatch.setattr(module.os, "chown", forbidden_chown)
    install_policy(module, rp, staged)

    assert chowns, (
        "CONTROL: no chown was attempted, so the repair path was never entered and this arm "
        "would pass against an implementation that simply ignored the group")
    assert chowns[0]["ino"] == staged_ino, (
        f"the repair chowned inode {chowns[0]['ino']}, not the staged file's {staged_ino}. "
        "Repairing the OLD report instead would satisfy every other assertion here while "
        "changing the artifact this function was asked to leave alone")
    assert chowns[0]["uid"] == -1 and chowns[0]["gid"] == other_gid, (
        f"the repair must set the OLD group and leave the owner alone, got {chowns[0]!r}")


def test_a_refusal_preserves_the_findings_it_supersedes(tmp_path: Path, monkeypatch) -> None:
    """Keeping the NAME is not keeping the EVIDENCE.

    Two revisions ago the refusal writer unlinked the report. One revision ago it replaced it
    instead, and the commit message called that preserving a report carrying HITS. It is not:
    os.replace destroys the old bytes exactly as surely as the unlink destroyed the name. Both
    round-6 reviewers said so independently, and they were right — a findings report is the
    artifact most worth keeping and it was the one still being lost.
    """
    driver = make_tool(tmp_path)
    module = import_driver(driver, "refusal_preserves")
    staging = tmp_path / "staging"
    reports_dir = staging / "_reports"
    reports_dir.mkdir(parents=True)
    rp = reports_dir / "scan_report.txt"
    findings = "SECRET\tgeneric_key_assignment\tcontents\tdocs/example.md:12\n"
    rp.write_text(findings, encoding="utf-8")

    original_inode = rp.stat().st_ino

    # NO monkeypatch: this is the ORDINARY refusal, the one that publishes successfully. An
    # earlier revision preserved only on the failure branch, so the common path went on
    # destroying the findings while the commit message said it did not.
    module._write_refusal_report(str(staging), module.ScanRefused("report-path-unsafe '_reports'"))

    assert rp.read_text(encoding="utf-8").startswith("scan_gate: REFUSED"), (
        "CONTROL: the refusal really did replace the canonical report on this run, so there was "
        "something to preserve at the moment it mattered")
    superseded = reports_dir / "scan_report.superseded.txt"
    assert superseded.is_file(), (
        "the findings this refusal replaced are gone. Preserving the NAME is not preserving the "
        "EVIDENCE: a report carrying HITS is what a reader most needs kept, and os.replace "
        "destroys it as surely as an unlink would have")
    assert superseded.stat().st_ino == original_inode, (
        "the preserved artifact is a COPY, not the original inode. A copy is written by this "
        "process with this process's idea of the mode, so it can differ in permissions, owner "
        "and ACL from the report it claims to have preserved; a hard link cannot")
    assert superseded.read_text(encoding="utf-8") == findings, (
        "and it must carry the ORIGINAL findings, not a copy of the refusal")


def test_a_second_refusal_does_not_overwrite_the_preserved_findings(tmp_path: Path,
                                                                    monkeypatch) -> None:
    """The preservation mechanism destroying the thing it preserves.

    The first refusal links the findings report to the sibling. The second one found the sibling
    occupied and cleared it first — so two refusals in a row replaced the evidence with a copy of
    the refusal, and the mechanism read as working the whole time. A single-call arm cannot see
    this; only the second call can.
    """
    driver = make_tool(tmp_path)
    module = import_driver(driver, "refusal_preserves_twice")
    staging = tmp_path / "staging"
    reports_dir = staging / "_reports"
    reports_dir.mkdir(parents=True)
    rp = reports_dir / "scan_report.txt"
    findings = "SECRET\tgeneric_key_assignment\tcontents\tdocs/example.md:12\n"
    rp.write_text(findings, encoding="utf-8")

    module._write_refusal_report(str(staging), module.ScanRefused("report-path-unsafe 'a'"))
    superseded = reports_dir / "scan_report.superseded.txt"
    assert superseded.read_text(encoding="utf-8") == findings, (
        "CONTROL: the first refusal must preserve the findings, or the second call proves nothing")

    module._write_refusal_report(str(staging), module.ScanRefused("report-path-unsafe 'b'"))

    assert superseded.read_text(encoding="utf-8") == findings, (
        "the second refusal overwrote the preserved findings with a copy of the refusal. The "
        "oldest surviving evidence is the one worth keeping; a preservation slot that the next "
        "failure clears preserves nothing that outlives a repeat")


def test_a_group_that_cannot_be_repaired_raises_rather_than_publishing(tmp_path: Path,
                                                                        monkeypatch) -> None:
    """The failure half of the repair, which no arm covered.

    The happy path was pinned as soon as the repair was written. What was not pinned is what the
    repair does when it FAILS — and "publish anyway under the wrong group" is the outcome that
    would have made the whole ownership guard decorative.
    """
    driver = make_tool(tmp_path)
    module = import_driver(driver, "gid_unrepairable")
    reports_dir = tmp_path / "_reports"
    reports_dir.mkdir()
    rp = reports_dir / "scan_report.txt"
    rp.write_text("scan_gate: CLEAN\n", encoding="utf-8")
    rp.chmod(0o640)
    staged = reports_dir / ".scan_report_staged"
    staged.write_text("scan_gate: REFUSED input-error\n", encoding="utf-8")
    staged.chmod(0o600)

    real_lstat = module.os.lstat
    other_gid = os.getgid() + 1

    def lstat_with_a_different_group(path, *args, **kwargs):
        st = real_lstat(path, *args, **kwargs)
        if str(path) in (str(rp), rp.name):
            return os.stat_result(tuple(st)[:5] + (other_gid,) + tuple(st)[6:])
        return st

    def failing_fchown(fd, uid, gid, *args, **kwargs):
        raise PermissionError(1, "not a member of that group")

    monkeypatch.setattr(module.os, "lstat", lstat_with_a_different_group)
    monkeypatch.setattr(module.os, "fchown", failing_fchown)

    with pytest.raises(OSError) as caught:
        install_policy(module, rp, staged)
    assert "report-permission-preservation-failed" in str(caught.value), (
        f"an unrepairable group must refuse, got {caught.value!r}")
    assert stat.S_IMODE(staged.stat().st_mode) & 0o077 == 0, (
        "and the staged file must not have been opened up on the way out: a refusal that leaves "
        "a group-readable temporary behind has published the thing it refused to publish")


def test_a_status_report_does_not_squat_the_preservation_slot(tmp_path: Path) -> None:
    """The "oldest wins" rule eating what it was added to protect.

    Refusing to overwrite the sibling stopped a second refusal destroying preserved findings. It
    also meant a CLEAN report, saved by some earlier refusal, occupied the only slot permanently —
    so the next genuine findings report could not be kept at all. Measured before the fix: the
    sibling still held "scan_gate: CLEAN" after a refusal replaced a report full of hits.
    """
    driver = make_tool(tmp_path)
    module = import_driver(driver, "slot_squatting")
    staging = tmp_path / "staging"
    reports_dir = staging / "_reports"
    reports_dir.mkdir(parents=True)
    rp = reports_dir / "scan_report.txt"
    superseded = reports_dir / "scan_report.superseded.txt"

    rp.write_text("scan_gate: CLEAN\n", encoding="utf-8")
    module._write_refusal_report(str(staging), module.ScanRefused("report-path-unsafe 'a'"))
    assert rp.read_text(encoding="utf-8").startswith("scan_gate: REFUSED"), (
        "CONTROL: the first refusal really did replace the clean report")
    assert not superseded.exists(), (
        "a CLEAN report was parked in the preservation slot. It carries nothing a reader would "
        "mourn, and the slot is refused to later writers, so keeping it costs the findings that "
        "come next")

    findings = "SECRET\tgeneric_key_assignment\tcontents\tdocs/example.md:12\n"
    rp.write_text(findings, encoding="utf-8")
    module._write_refusal_report(str(staging), module.ScanRefused("report-path-unsafe 'b'"))

    assert superseded.is_file() and superseded.read_text(encoding="utf-8") == findings, (
        "the findings were lost because an earlier status report held the slot")


def test_a_stale_sibling_does_not_block_preserving_the_current_findings(tmp_path: Path) -> None:
    """Occupancy is not evidence that the occupant is worth more.

    Refusing to overwrite the slot stopped a second refusal destroying kept findings. It also
    meant an EARLIER generation's copy — or a file someone simply created at that name — held the
    slot against every later writer, so the findings a fresh scan produced could not be kept at
    all. Measured before the fix: after a new scan published new hits, a refusal left the sibling
    still holding the OLD ones.
    """
    driver = make_tool(tmp_path)
    module = import_driver(driver, "stale_sibling")
    staging = tmp_path / "staging"
    reports_dir = staging / "_reports"
    reports_dir.mkdir(parents=True)
    rp = reports_dir / "scan_report.txt"
    superseded = reports_dir / "scan_report.superseded.txt"
    old = "SECRET\tgeneric_key_assignment\tcontents\tdocs/old.md:1\n"
    rp.write_text(old, encoding="utf-8")

    module._write_refusal_report(str(staging), module.ScanRefused("report-path-unsafe 'a'"))
    assert superseded.read_text(encoding="utf-8") == old, (
        "CONTROL: the first refusal preserved the old findings, so there is a stale occupant")

    module.write_report(str(staging), [("docs/new.md", 2, "SECRET", "generic_key_assignment",
                                        "contents")])
    assert "docs/new.md" in rp.read_text(encoding="utf-8"), (
        "CONTROL: a fresh scan really did publish a new findings report")

    module._write_refusal_report(str(staging), module.ScanRefused("report-path-unsafe 'b'"))

    body = superseded.read_text(encoding="utf-8")
    assert "docs/new.md" in body, (
        f"the refusal could not keep the CURRENT findings because an earlier generation held the "
        f"slot; the sibling still reads {body!r}. A file planted at that name does the same thing "
        "permanently, turning a refusal-to-overwrite into a denial of preservation")


def test_a_planted_sibling_does_not_deny_preservation_forever(tmp_path: Path) -> None:
    """The same hole reached without any earlier refusal: someone just creates the name."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "planted_sibling")
    staging = tmp_path / "staging"
    reports_dir = staging / "_reports"
    reports_dir.mkdir(parents=True)
    superseded = reports_dir / "scan_report.superseded.txt"
    superseded.write_text("not a report at all\n", encoding="utf-8")

    module.write_report(str(staging), [("docs/x.md", 3, "SECRET", "generic_key_assignment",
                                        "contents")])
    assert not superseded.exists(), (
        "a planted file at the sibling name survived a successful scan, so it goes on blocking "
        "every later preservation; the slot belongs to one report generation")

    module._write_refusal_report(str(staging), module.ScanRefused("report-path-unsafe 'c'"))
    assert "docs/x.md" in superseded.read_text(encoding="utf-8"), (
        "and after the plant is cleared the refusal must be able to keep the real findings")


def test_findings_that_cannot_be_preserved_are_not_destroyed(tmp_path: Path) -> None:
    """Publication gate, reproduced from the CLI: preservation failed silently and the replace
    went ahead anyway.

    A DIRECTORY at the sibling name cannot be unlinked by the clearing step and cannot be linked
    over, so every later refusal destroyed the only findings report while every failure on the way
    was swallowed. The two rules are not symmetric: a stale CLEAN beside an rc 2 falsely
    authorizes and must go, while a stale FINDINGS report says there are secrets here and is the
    conservative thing to leave. So findings are replaced only once they are safely kept.
    """
    driver = make_tool(tmp_path)
    module = import_driver(driver, "preserve_blocked")
    staging = tmp_path / "staging"
    reports_dir = staging / "_reports"
    reports_dir.mkdir(parents=True)
    rp = reports_dir / "scan_report.txt"
    findings = "SECRET\tgeneric_key_assignment\tcontents\tdocs/example.md:12\n"
    rp.write_text(findings, encoding="utf-8")
    # EVERY slot blocked by a populated directory: none can be unlinked, linked over, or mistaken
    # for a preserved copy. Round nine added alternates, so blocking only the first name no longer
    # makes the evidence unpreservable — it just moves it to the next slot, which is the point.
    for name in _superseded_names():
        d = reports_dir / name
        d.mkdir()
        (d / "somebody-elses-data.txt").write_text("not mine\n", encoding="utf-8")

    module._write_refusal_report(str(staging), module.ScanRefused("report-path-unsafe 'a'"))

    assert rp.read_text(encoding="utf-8") == findings, (
        "the findings were destroyed by a refusal that could not preserve them. Replacing is only "
        "safe once the evidence is kept; a report that says there are secrets is not a false "
        "authorization, so leaving it costs a reader nothing and losing it costs the evidence")


def test_an_unreadable_findings_report_is_still_preserved(tmp_path: Path) -> None:
    """A hard link needs no read permission on its source.

    Classifying the report before linking it made an unreadable findings report unpreservable —
    and therefore replaceable. Reproduced from the CLI at mode 000. The link now comes first.
    """
    if os.geteuid() == 0:
        pytest.skip("root can read a 000 file; this needs an unprivileged reader")
    driver = make_tool(tmp_path)
    module = import_driver(driver, "preserve_unreadable")
    staging = tmp_path / "staging"
    reports_dir = staging / "_reports"
    reports_dir.mkdir(parents=True)
    rp = reports_dir / "scan_report.txt"
    findings = "SECRET\tgeneric_key_assignment\tcontents\tdocs/example.md:12\n"
    rp.write_text(findings, encoding="utf-8")
    rp.chmod(0o000)
    try:
        try:
            rp.read_text(encoding="utf-8")
            pytest.skip("this filesystem does not enforce mode 000; the arm is not measurable")
        except PermissionError:
            pass

        module._write_refusal_report(str(staging), module.ScanRefused("report-path-unsafe 'a'"))

        superseded = reports_dir / "scan_report.superseded.txt"
        assert superseded.exists(), (
            "an unreadable findings report was not preserved. A hard link does not read its "
            "source, so a failed read must not be what decides whether evidence survives")
        superseded.chmod(0o600)
        assert superseded.read_text(encoding="utf-8") == findings, "and it must be the findings"
    finally:
        for leftover in (rp, reports_dir / "scan_report.superseded.txt"):
            try:
                leftover.chmod(0o600)
            except OSError:
                pass


def test_a_stale_clean_is_still_replaced_and_leaves_the_slot_free(tmp_path: Path) -> None:
    """The asymmetry, asserted from the other side.

    Making findings un-replaceable must not make a stale CLEAN un-replaceable too — that is the
    false authorization this whole writer exists to prevent. And a status line must not occupy the
    preservation slot, or the next real findings report cannot be kept.
    """
    driver = make_tool(tmp_path)
    module = import_driver(driver, "stale_clean_replaced")
    staging = tmp_path / "staging"
    reports_dir = staging / "_reports"
    reports_dir.mkdir(parents=True)
    rp = reports_dir / "scan_report.txt"
    rp.write_text("scan_gate: CLEAN\n", encoding="utf-8")

    module._write_refusal_report(str(staging), module.ScanRefused("report-path-unsafe 'a'"))

    assert rp.read_text(encoding="utf-8").startswith("scan_gate: REFUSED"), (
        "a stale CLEAN survived a refusal: it tells a reader this tree was scanned and passed, "
        "beside an exit code that says it was not")
    assert not (reports_dir / "scan_report.superseded.txt").exists(), (
        "a status line took the preservation slot, which the next findings report then cannot use")


def _superseded_names():
    """Every preservation slot name, mirroring _superseded_slots in the scanner."""
    return ["scan_report.superseded.txt"] + [
        "scan_report.superseded.%d.txt" % n for n in range(1, 8)]



# GROUP 21 — the ninth review round. Two independent gate legs, given no shared premise, both
# reproduced the same hole from the CLI: the "already preserved" check followed a symlink. One of
# them additionally found the fix for the eighth round had introduced a stale-CLEAN regression.


def test_a_symlink_at_the_slot_cannot_authorize_destroying_the_findings(tmp_path: Path) -> None:
    """The identity check asked os.stat, which FOLLOWS.

    Plant a symlink at the preservation slot pointing back at the report, and the inode
    comparison succeeded against the report's own inode — so the check answered "already
    preserved" when no second directory entry for that inode existed anywhere, and the caller
    destroyed the only copy. Reproduced independently by both gate legs, from the CLI, no race.

    Only a second directory entry for THIS inode counts: lstat rather than stat, st_dev carried
    alongside st_ino because inode numbers are unique only within a filesystem, and S_ISREG
    because a hard link is by definition a regular file.
    """
    driver = make_tool(tmp_path)
    module = import_driver(driver, "slot_symlink_identity")
    staging = tmp_path / "staging"
    reports_dir = staging / "_reports"
    reports_dir.mkdir(parents=True)
    rp = reports_dir / "scan_report.txt"
    findings = "SECRET\tgeneric_key_assignment\tcontents\tdocs/example.md:12\n"
    rp.write_text(findings, encoding="utf-8")
    # every slot a symlink at the report, so no link can be created and ONLY the identity
    # comparison decides the outcome
    for name in _superseded_names():
        os.symlink("scan_report.txt", reports_dir / name)

    module._write_refusal_report(str(staging), module.ScanRefused("report-path-unsafe 'a'"))

    assert rp.read_text(encoding="utf-8") == findings, (
        "a symlink at the preservation slot authorized destroying the findings. os.stat follows; "
        "the question is whether a second directory entry holds this inode, which only lstat "
        "plus (st_dev, st_ino) can answer")


def test_a_real_hard_link_still_authorizes_the_replace(tmp_path: Path) -> None:
    """The other direction, so the check is not merely refusing everything.

    A genuine hard link IS preservation, and the report may then be replaced. Without this arm the
    fix above is satisfied by a predicate that always answers "not preserved".
    """
    driver = make_tool(tmp_path)
    module = import_driver(driver, "slot_hardlink_identity")
    staging = tmp_path / "staging"
    reports_dir = staging / "_reports"
    reports_dir.mkdir(parents=True)
    rp = reports_dir / "scan_report.txt"
    findings = "SECRET\tgeneric_key_assignment\tcontents\tdocs/example.md:12\n"
    rp.write_text(findings, encoding="utf-8")
    names = _superseded_names()
    os.link(rp, reports_dir / names[0])          # genuinely preserved
    for name in names[1:]:
        os.symlink("scan_report.txt", reports_dir / name)

    module._write_refusal_report(str(staging), module.ScanRefused("report-path-unsafe 'a'"))

    assert rp.read_text(encoding="utf-8").startswith("scan_gate: REFUSED"), (
        "a genuine hard link is preservation, so the replace was safe and must have happened")
    assert (reports_dir / names[0]).read_text(encoding="utf-8") == findings, (
        "and the preserved copy must still hold the findings")


def test_an_occupied_slot_no_longer_denies_preservation(tmp_path: Path) -> None:
    """Gate review, round nine: the eighth round's fix left a stale CLEAN standing.

    A report the scanner cannot READ is treated as findings, which is the safe direction for
    evidence. Combined with "findings may not be replaced unless preserved", an occupied slot
    meant an unreadable stale CLEAN survived beside an rc 2 — a false authorization, measured at
    mode 0044 where the owner cannot read the file but another user can.

    One name can be occupied by something that cannot be removed. A set of them makes occupancy
    stop being a denial of preservation, which is what let the replace proceed again.
    """
    driver = make_tool(tmp_path)
    module = import_driver(driver, "occupied_slot")
    staging = tmp_path / "staging"
    reports_dir = staging / "_reports"
    reports_dir.mkdir(parents=True)
    rp = reports_dir / "scan_report.txt"
    rp.write_text("scan_gate: CLEAN\n", encoding="utf-8")
    squatted = reports_dir / _superseded_names()[0]
    squatted.mkdir()
    (squatted / "somebody-elses-data.txt").write_text("not mine\n", encoding="utf-8")
    if os.geteuid() != 0:
        rp.chmod(0o044)                 # owner cannot read it; another user still can
    try:
        module._write_refusal_report(str(staging), module.ScanRefused("invalid-wide-encoding 'b'"))
    finally:
        try:
            rp.chmod(0o644)
        except OSError:
            pass

    assert rp.read_text(encoding="utf-8").startswith("scan_gate: REFUSED"), (
        "a stale CLEAN survived beside an rc 2 because one preservation name was occupied by a "
        "directory that could not be removed. A reader who CAN read it is told this tree was "
        "scanned and passed, beside an exit code saying it was not")


def test_a_planted_clean_behind_a_symlinked_reports_directory_is_a_known_limitation(
        tmp_path: Path) -> None:
    """PINS A LIMITATION THAT IS NOT FIXED, so it stays visible instead of being rediscovered.

    The scanned tree is untrusted, so it can ship `_reports -> payload/` with a prewritten CLEAN
    behind it. The writer refuses to publish through the link, and a reader following the
    documented path is handed that planted CLEAN beside an rc 2. Gate review reproduced it from
    the CLI with no race.

    Removing the link was implemented and REVERTED: a symlinked _reports can be a deliberate
    setup, deleting it destroys that configuration, and it still would not close the class,
    because every defence available to the writer is writer-side and the exposure is reader-side.

    The real fix is to stop authorizing from a path inside the scanned tree. Until that is made,
    this arm asserts the limitation EXISTS. It is expected to fail, loudly, on the commit that
    finally fixes it — which is what a pinned limitation is for.
    """
    driver = make_tool(tmp_path)
    module = import_driver(driver, "reports_symlink_plant")
    staging = tmp_path / "staging"
    payload = staging / "payload"
    payload.mkdir(parents=True)
    planted = payload / "scan_report.txt"
    planted.write_text("scan_gate: CLEAN\n", encoding="utf-8")
    os.symlink("payload", staging / "_reports")

    module._write_refusal_report(str(staging), module.ScanRefused("report-path-unsafe '_reports'"))

    canonical = staging / "_reports" / "scan_report.txt"
    assert canonical.read_text(encoding="utf-8") == "scan_gate: CLEAN\n", (
        "the planted CLEAN is no longer readable through the symlinked _reports directory. If "
        "that is because the reader-side hole was closed, DELETE this arm and say so; it exists "
        "only to keep an unfixed limitation visible")
    assert planted.read_text(encoding="utf-8") == "scan_gate: CLEAN\n", (
        "and nothing was written through the link")


def test_a_platform_without_xattrs_does_not_break_the_refusal_writer(tmp_path: Path) -> None:
    """POSIX-ACL extended attributes are a LINUX API.

    Elsewhere os.getxattr does not merely fail, it does not EXIST — and AttributeError is not an
    OSError, so it escaped a function documented as raising only OSError, and escaped the refusal
    writer's "never raises" contract with it. Gate review found that a macOS or Windows adopter
    would have the refusal writer replace the very refusal it was called to report.

    The platform is simulated by giving THIS module copy an os whose three xattr names are
    absent; import_driver hands each test its own module, so nothing leaks between arms.
    """
    driver = make_tool(tmp_path)
    module = import_driver(driver, "no_xattr_platform")

    real_os = module.os
    absent = ("getxattr", "setxattr", "removexattr")

    class _NoXattrOS:
        def __getattr__(self, name):
            if name in absent:
                raise AttributeError(name)
            return getattr(real_os, name)

    staging = tmp_path / "staging"
    reports_dir = staging / "_reports"
    reports_dir.mkdir(parents=True)
    rp = reports_dir / "scan_report.txt"
    rp.write_text("scan_gate: CLEAN\n", encoding="utf-8")

    module.os = _NoXattrOS()
    module._XATTR_SUPPORTED = False       # what the probe would have found on that platform
    try:
        module._write_refusal_report(str(staging), module.ScanRefused("report-path-unsafe 'a'"))
    finally:
        module.os = real_os

    assert rp.read_text(encoding="utf-8").startswith("scan_gate: REFUSED"), (
        "on a platform without POSIX-ACL xattrs the refusal was not published. There is no ACL "
        "for this code to carry there, so it carries the mode instead — a mode-only fallback "
        "outside the POSIX-ACL domain, NOT a claim that the mode is the whole access policy on "
        "that platform")


def test_the_classification_reads_the_inode_it_preserved_not_the_name(tmp_path: Path) -> None:
    """A review leg's interleaving: link by name, then classify by name, is two different inodes.

    os.link and open both resolve a NAME. Between them, an os.replace onto scan_report.txt — a
    second scanner, or any mutator; there is no lock — swaps that name to a new inode while the
    link still holds the old one. The classification then describes the NEW bytes. If those are a
    status line, the code gives the slot back by unlinking the link, destroying the findings it
    had just successfully preserved.

    Classifying through the link removes the second resolution: it is our own name for the inode
    we kept, and nobody else is replacing it.

    The swap is injected at the link itself, so it lands exactly in the window and needs no
    concurrency to be deterministic.
    """
    driver = make_tool(tmp_path)
    module = import_driver(driver, "classify_through_link")
    staging = tmp_path / "staging"
    reports_dir = staging / "_reports"
    reports_dir.mkdir(parents=True)
    rp = reports_dir / "scan_report.txt"
    findings = "SECRET\tgeneric_key_assignment\tcontents\tdocs/example.md:12\n"
    rp.write_text(findings, encoding="utf-8")

    real_os = module.os

    class _SwapAtLink:
        """Proxies os, but after a successful link swaps report_path to a CLEAN inode."""

        def link(self, src, dst, *a, **kw):
            real_os.link(src, dst, *a, **kw)
            intruder = reports_dir / ".intruder"
            intruder.write_text("scan_gate: CLEAN\n", encoding="utf-8")
            real_os.replace(str(intruder), str(rp))

        def __getattr__(self, name):
            return getattr(real_os, name)

    module.os = _SwapAtLink()
    try:
        kept = preserve_superseded(module, reports_dir, rp.name)
    finally:
        module.os = real_os

    preserved = [reports_dir / n for n in _superseded_names()]
    surviving = [p for p in preserved if p.exists() and p.read_text(encoding="utf-8") == findings]
    assert surviving, (
        "the findings were preserved and then destroyed: the classification read the name, which "
        "another writer had swapped to a status line, so the slot was given back and the only "
        "remaining link to the findings inode was unlinked. Classify through the link instead")
    assert kept is True, (
        "and with the evidence genuinely preserved the caller may still replace the report")


def test_the_numbered_preservation_slots_are_a_reserved_namespace(tmp_path: Path) -> None:
    """PINS A DELETION THAT IS INTENDED, because it was not intended until it was documented.

    Widening the preservation slot from one name to eight turned seven ordinary filenames into
    scanner-owned ones. Gate review measured a pre-existing file at scan_report.superseded.1.txt
    destroyed by an ordinary clean scan — no race, no permission failure, nothing injected.

    A filename does not establish provenance, so the scanner cannot tell a user's archive from its
    own. The resolution is a stated reservation rather than a guess: STAGING_README.md lists the
    eight names as scanner-owned and says not to keep anything there. This arm holds the code and
    that promise together — if the cleanup is ever narrowed to establish ownership, this arm
    should fail and the documentation should change with it.
    """
    driver = make_tool(tmp_path)
    module = import_driver(driver, "reserved_namespace")
    staging = tmp_path / "staging"
    reports_dir = staging / "_reports"
    reports_dir.mkdir(parents=True)
    foreign = reports_dir / "scan_report.superseded.1.txt"
    foreign.write_text("an adopter's own archive\n", encoding="utf-8")

    module.write_report(str(staging), [])

    assert (reports_dir / "scan_report.txt").read_text(encoding="utf-8") == "scan_gate: CLEAN\n", (
        "the clean publication itself must still succeed")
    assert not foreign.exists(), (
        "the reserved-namespace contract changed: a file at a numbered slot survived a successful "
        "publication. If cleanup now establishes ownership, that is an improvement — update "
        "STAGING_README.md's reserved-filename list to match, then retire this arm")


def test_a_new_findings_report_is_never_other_readable(tmp_path: Path) -> None:
    """An independent review leg found this; the gate did not.

    A new report took its mode from what an ordinary create in that directory produces. That is
    correct when the directory carries a default ACL — an explicit statement about who may read
    here — and wrong as a bare fallback, because then the answer comes from the umask. Measured
    against the real scanner: 0644 at the ordinary login umask 0022, and 0666 inside a 0777
    directory at umask 0. Not an exotic configuration; the first one is the default.

    The report lists the CLASS, PATH and LINE of every secret found, so a world-readable one hands
    any local account a map to them. "Other" is therefore capped off unconditionally. Group access
    still follows the directory, because a shared group directory is a choice somebody made.

    The umask is set explicitly here: at the suite's own umask the bit may already be clear, and an
    arm that cannot observe the failure is not measuring the property.
    """
    if os.geteuid() == 0:
        pytest.skip("root ignores mode bits; this needs an unprivileged writer")
    driver = make_tool(tmp_path)
    module = import_driver(driver, "other_cap")
    previous = os.umask(0o000)            # the most permissive case, so the bit CAN appear
    try:
        staging = tmp_path / "staging"
        os.makedirs(staging)
        hits = [("docs/example.md", 12, "SECRET", "generic_key_assignment", "contents")]
        module.write_report(str(staging), hits)
        rp = staging / "_reports" / "scan_report.txt"
        mode = stat.S_IMODE(rp.stat().st_mode)
    finally:
        os.umask(previous)

    assert not mode & stat.S_IROTH, (
        f"a findings report was published mode {mode:04o}: readable by every account on the box, "
        "and it names the path and line of each secret found")
    assert not mode & stat.S_IWOTH, (
        f"a findings report was published mode {mode:04o}: writable by every account on the box, "
        "so anyone could replace it with a CLEAN line")
    assert mode & stat.S_IRUSR, (
        f"and it must still be readable by its owner, not merely locked down to {mode:04o}")


def test_an_acl_that_grants_other_is_stripped_rather_than_refused(tmp_path: Path) -> None:
    """SUPERSEDED SUBJECT: this arm asserted a REFUSAL. Stripping is strictly better.

    Round twelve refused the publish when the old report's ACL granted other, because installing
    that ACL restored the bits the mode cap removed. Round fifteen stopped installing ACLs at all,
    which turns a refusal into a narrowing: the adopter gets their report, nobody else gets access,
    and the availability cost of the refusal disappears.
    """
    if os.geteuid() == 0:
        pytest.skip("root ignores these checks; this needs an unprivileged writer")
    if shutil.which("setfacl") is None:
        pytest.skip("setfacl is not installed; the ACL contract cannot be measured here")
    driver = make_tool(tmp_path)
    module = import_driver(driver, "acl_grants_other")
    reports_dir = tmp_path / "_reports"
    reports_dir.mkdir()
    rp = reports_dir / "scan_report.txt"
    rp.write_text("scan_gate: CLEAN\n", encoding="utf-8")
    rp.chmod(0o664)
    applied = subprocess.run(["setfacl", "-m", f"u:{os.geteuid()}:r,o::r", "--", str(rp)],
                             capture_output=True, text=True)
    if applied.returncode != 0:
        pytest.skip(f"the filesystem refused an ACL entry: {applied.stderr.strip()[:80]}")
    if not stat.S_IMODE(rp.stat().st_mode) & stat.S_IROTH:
        pytest.skip("this filesystem did not grant other through the ACL; nothing to measure")

    staged = reports_dir / ".scan_report_staged"
    staged.write_text("scan_gate: REFUSED input-error\n", encoding="utf-8")
    staged.chmod(0o600)
    # The STAGED file carries the ACL here, because that is what the strip acts on. Setting one
    # only on the old report leaves this arm unable to fail.
    seeded = subprocess.run(["setfacl", "-m", f"u:{os.geteuid()}:r,o::r", "--", str(staged)],
                            capture_output=True, text=True)
    if seeded.returncode != 0:
        pytest.skip(f"the filesystem refused an ACL entry: {seeded.stderr.strip()[:80]}")
    assert _acl(staged) is not None, "CONTROL: the staged file carries an ACL to strip"

    install_policy(module, rp, staged)

    mode = stat.S_IMODE(staged.stat().st_mode)
    assert not mode & 0o077, (
        f"the staged report ended at {mode:04o}: an ACL granting other was allowed to widen it")
    assert _acl(staged) is None, "and the granting ACL must not have been carried onto it"


# 0644 and 0666 carry no other-EXECUTE, so a mutant clearing read and write only (& ~0o006) kept
# execute and survived this arm — measured by gate review: planted 0601 published 0601. Execute on
# a report is not itself a disclosure, but the fixture set decides which bits the assertion can
# actually see, and one that never sets a bit cannot prove that bit is capped.
@pytest.mark.parametrize("planted", [0o644, 0o666, 0o601, 0o602, 0o604, 0o607])
def test_a_planted_existing_report_cannot_widen_the_findings_it_is_replaced_by(
        tmp_path: Path, planted: int) -> None:
    """The cap on a NEW report was half a fix, and a review leg found the other half.

    Preserving the old report's mode exists because forcing a umask-derived one once widened an
    existing private report. But the "existing report" lives inside the tree being scanned, and
    that tree is untrusted: a committed _reports/scan_report.txt checks out 0644 at an ordinary
    umask, and preserving it faithfully republished the findings at 0644. Measured before the fix:
    planted 0644 -> published 0644, planted 0666 -> published 0666. The new-report cap never ran,
    because this is the existing-report arm.

    Narrow-only preservation keeps the property the earlier round protected — 0600 stays 0600,
    0660 keeps group write — and refuses only "other", only downward.
    """
    if os.geteuid() == 0:
        pytest.skip("root ignores mode bits; this needs an unprivileged writer")
    driver = make_tool(tmp_path)
    module = import_driver(driver, "planted_mode_%o" % planted)
    staging = tmp_path / "staging"
    reports_dir = staging / "_reports"
    reports_dir.mkdir(parents=True)
    rp = reports_dir / "scan_report.txt"
    rp.write_text("scan_gate: CLEAN\n", encoding="utf-8")
    rp.chmod(planted)

    module.write_report(str(staging), [("docs/example.md", 12, "SECRET",
                                        "generic_key_assignment", "contents")])

    mode = stat.S_IMODE(rp.stat().st_mode)
    # EVERY other bit, not just read — and the arithmetic in the previous version of this comment
    # was WRONG. It claimed 0666 & ~0o004 "still clears write". It does not: 0666 & ~0o004 is
    # 0662, which leaves other-WRITE set. That is exactly why the mutant escaped — the old
    # assertion looked only at the read bit and never saw the write bit that survived. A cap
    # asserted on the one bit a fixture happens to exercise is not a cap, and an explanation that
    # gets the arithmetic backwards hides the hole instead of recording it.
    assert not mode & 0o007, (
        f"a report planted at {planted:04o} caused the findings to be republished at {mode:04o}: "
        "the tree being scanned chose who may read or write the secrets found in it")
    assert mode & stat.S_IRUSR and mode & stat.S_IWUSR, (
        f"and the owner must keep read and write, not be locked out at {mode:04o}")


@pytest.mark.parametrize("acl,label", [("u::r,g::-,o::-", "owner-read-only"),
                                       ("u::-,g::-,o::-", "no-access")])
def test_a_directory_policy_cannot_publish_a_report_its_owner_cannot_read(
        tmp_path: Path, acl: str, label: str) -> None:
    """The cross-product the gate asked for, kept and turned around.

    It was built around _report_mode, a probe that measured what an ordinary create in the report
    directory produces and returned a GUESSED 0o600 when its unnamed O_TMPFILE attempt was
    unsupported. Round seventeen deleted the probe: under a constant published mode there is
    nothing left to discover, and a function whose docstring described a caller that no longer
    existed is the defect the previous two rounds were spent on.

    The CONFIGURATIONS it exercised are the valuable part and they stay. A directory whose default
    ACL grants the owner read only, or nothing at all, is exactly where the old intersection
    published findings at 0400 and 0000. Both now publish 0600 and both are readable.
    """
    if os.geteuid() == 0:
        pytest.skip("root ignores mode bits; this needs an unprivileged writer")
    if shutil.which("setfacl") is None:
        pytest.skip("setfacl is not installed; the default-ACL contract cannot be measured here")
    driver = make_tool(tmp_path)
    module = import_driver(driver, "locked_out_%s" % label)
    staging = tmp_path / "staging"
    reports_dir = staging / "_reports"
    reports_dir.mkdir(parents=True)
    applied = subprocess.run(["setfacl", "-d", "-m", acl, str(reports_dir)],
                             capture_output=True, text=True)
    if applied.returncode != 0:
        pytest.skip(f"the filesystem refused a default ACL: {applied.stderr.strip()[:80]}")

    reference = reports_dir / ".ordinary_reference"
    fd = os.open(str(reference), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    inherited = stat.S_IMODE(reference.stat().st_mode)
    reference.unlink()
    if inherited & 0o400 and inherited & 0o200:
        pytest.skip(f"this filesystem gave an ordinary create {inherited:04o} despite {acl!r}; "
                    "the owner is not locked out here, so there is nothing to measure")

    module.write_report(str(staging), [("docs/example.md", 12, "SECRET",
                                        "generic_key_assignment", "contents")])

    rp = reports_dir / "scan_report.txt"
    got = stat.S_IMODE(rp.stat().st_mode)
    assert got == 0o600, (
        f"a private create in this directory lands at {inherited:04o} and the findings report "
        f"came out at {got:04o}; the published mode is a constant, not a term intersected with "
        "whatever the directory would have handed an ordinary file")
    assert rp.read_text(encoding="utf-8"), (
        f"the findings report is not readable by the uid that wrote it. An ordinary create here "
        f"gives {inherited:04o} — this is the outcome the constant exists to prevent")



@pytest.mark.parametrize("umask_val", [0o022, 0o002, 0o000])
def test_the_report_directory_is_never_writable_by_anyone_else(tmp_path: Path,
                                                               umask_val: int) -> None:
    """Four rounds hardened the FILE and left the CONTAINER unconstrained.

    Directory write permission, not file mode, is what governs unlink and create. os.makedirs
    defaults to 0o777, so at umask 0 the report directory was created world-writable with an
    owner-only report inside it — and any local account could unlink that 0600 report and drop its
    own "scan_gate: CLEAN" at the same path. Measured before the fix: _reports 0777 at umask 0 and
    0775 at umask 0002, which is ordinary where per-user groups are configured.

    A file mode is only as good as the directory holding it, and every permission arm before this
    one asserted the file.
    """
    if os.geteuid() == 0:
        pytest.skip("root ignores mode bits; this needs an unprivileged writer")
    driver = make_tool(tmp_path)
    module = import_driver(driver, "reportdir_umask_%o" % umask_val)
    previous = os.umask(umask_val)
    try:
        staging = tmp_path / "staging"
        os.makedirs(staging)
        module.write_report(str(staging), [("docs/example.md", 12, "SECRET",
                                            "generic_key_assignment", "contents")])
        dmode = stat.S_IMODE((staging / "_reports").stat().st_mode)
    finally:
        os.umask(previous)

    assert not dmode & 0o022, (
        f"the report directory is {dmode:04o} at umask {umask_val:04o}: another account can unlink "
        "the owner-only report inside it and publish its own CLEAN at the same path, which makes "
        "the report's own mode irrelevant to the outcome it was hardened for")


def test_an_existing_permissive_report_directory_is_narrowed_before_publishing(
        tmp_path: Path) -> None:
    """Creation mode is only half of it: exist_ok=True leaves an existing directory alone.

    The scanned tree is untrusted and can ship its own `_reports` at 0777. Only the WRITE bits are
    removed, and only from group and other: this narrows who can FORGE the report, not who can
    find it, and it touches the scanner's own output directory rather than anything it was asked
    to scan.
    """
    if os.geteuid() == 0:
        pytest.skip("root ignores mode bits; this needs an unprivileged writer")
    driver = make_tool(tmp_path)
    module = import_driver(driver, "reportdir_existing")
    staging = tmp_path / "staging"
    reports_dir = staging / "_reports"
    reports_dir.mkdir(parents=True)
    reports_dir.chmod(0o777)

    module.write_report(str(staging), [("docs/example.md", 12, "SECRET",
                                        "generic_key_assignment", "contents")])

    dmode = stat.S_IMODE(reports_dir.stat().st_mode)
    assert not dmode & 0o022, (
        f"an existing report directory shipped at 0777 stayed {dmode:04o}: the publish went into a "
        "directory strangers can rewrite")
    assert dmode & 0o400, (
        f"and the owner must still be able to reach its own reports, not {dmode:04o}")


@pytest.mark.parametrize("planted", [0o660, 0o662, 0o670])
def test_a_planted_group_writable_report_cannot_keep_its_group_write(tmp_path: Path,
                                                                     planted: int) -> None:
    """The unclosed half of the planted-mode bug: other was capped, group was kept.

    An earlier comment said so explicitly — "a 0660 keeps group write". A planted 0660 is the same
    plant with a different audience: any member of the staging tree's group overwrites the
    published report with a CLEAN line after the scanner returns, and a reader sees CLEAN.

    Group READ is deliberately still preserved; only write and execute go. The demonstrated attack
    is a write, and dropping group read would additionally refuse every report carrying a
    group-granting ACL.
    """
    if os.geteuid() == 0:
        pytest.skip("root ignores mode bits; this needs an unprivileged writer")
    driver = make_tool(tmp_path)
    module = import_driver(driver, "planted_gw_%o" % planted)
    staging = tmp_path / "staging"
    reports_dir = staging / "_reports"
    reports_dir.mkdir(parents=True)
    rp = reports_dir / "scan_report.txt"
    rp.write_text("scan_gate: CLEAN\n", encoding="utf-8")
    rp.chmod(planted)

    module.write_report(str(staging), [("docs/example.md", 12, "SECRET",
                                        "generic_key_assignment", "contents")])

    mode = stat.S_IMODE(rp.stat().st_mode)
    assert not mode & stat.S_IWGRP, (
        f"a report planted at {planted:04o} published the findings at {mode:04o}: the scanned tree "
        "handed write access to its own report to a group, and a group member can replace it with "
        "a CLEAN line the moment the scanner returns")
    assert not mode & 0o007, f"and no other access, not {mode:04o}"


def test_the_refusal_path_hardens_the_report_directory_too(tmp_path: Path) -> None:
    """Round fourteen hardened the directory in write_report ONLY, and the gate caught it.

    The refusal writer has its own publish, and it is the path that runs when something is already
    wrong — so hardening the path that usually succeeds and not the one that runs on failure gets
    it exactly backwards. A refusal wrote an owner-only report into a directory group or other
    could still rewrite, which is the same unlink-and-replace with a different trigger.
    """
    if os.geteuid() == 0:
        pytest.skip("root ignores mode bits; this needs an unprivileged writer")
    driver = make_tool(tmp_path)
    module = import_driver(driver, "refusal_dir_harden")
    staging = tmp_path / "staging"
    reports_dir = staging / "_reports"
    reports_dir.mkdir(parents=True)
    rp = reports_dir / "scan_report.txt"
    rp.write_text("scan_gate: CLEAN\n", encoding="utf-8")
    reports_dir.chmod(0o777)

    module._write_refusal_report(str(staging), module.ScanRefused("invalid-wide-encoding 'b'"))

    dmode = stat.S_IMODE(reports_dir.stat().st_mode)
    assert not dmode & 0o022, (
        f"the refusal published into a directory at {dmode:04o}: anyone can unlink the report it "
        "just wrote and replace it with a CLEAN line")


@pytest.mark.parametrize("planted", [0o640, 0o644, 0o660])
def test_a_planted_report_cannot_hand_its_group_the_findings(tmp_path: Path,
                                                             planted: int) -> None:
    """The round-fifteen change, and it was UNPINNED until this arm existed.

    Round fourteen kept group READ on the replacement path, reasoning that the demonstrated attack
    was a write. Both review legs refused that independently: a planted 0640 hands the file's group
    the class, path and line of every secret found, and "there was an existing file" is not a
    sharing decision by anyone who matters when that file came out of the untrusted tree.

    The existing planted-mode arms assert other-access and group WRITE, so restoring the old
    `& ~0o037` cap left them all green — measured. A change nothing can fail on is not proven, and
    this is the arm that can.
    """
    if os.geteuid() == 0:
        pytest.skip("root ignores mode bits; this needs an unprivileged writer")
    driver = make_tool(tmp_path)
    module = import_driver(driver, "planted_gr_%o" % planted)
    staging = tmp_path / "staging"
    reports_dir = staging / "_reports"
    reports_dir.mkdir(parents=True)
    rp = reports_dir / "scan_report.txt"
    rp.write_text("scan_gate: CLEAN\n", encoding="utf-8")
    rp.chmod(planted)

    module.write_report(str(staging), [("docs/example.md", 12, "SECRET",
                                        "generic_key_assignment", "contents")])

    mode = stat.S_IMODE(rp.stat().st_mode)
    assert not mode & stat.S_IRGRP, (
        f"a report planted at {planted:04o} published the findings at {mode:04o}: the scanned tree "
        "selected a reader class for its own secrets, and every member of that group now has the "
        "path and line of each one")
    assert not mode & 0o007, f"and no other access, not {mode:04o}"
    assert mode & stat.S_IRUSR, f"while the owner must still be able to read it, not {mode:04o}"


@pytest.mark.parametrize("planted", [0o644, 0o640, 0o666])
def test_the_preserved_copy_is_narrowed_not_left_at_the_mode_it_had(tmp_path: Path,
                                                                    planted: int) -> None:
    """Review: "preservation keeps the leak the owner-only publish was about to close."

    The reserved slot is a SECOND published name inside the untrusted tree, and a hard link keeps
    the old inode's mode and ACL by definition — which is the point when preserving evidence and
    the problem when that evidence was published wide. A findings report at a planted 0644 is
    replaced owner-only at the canonical name while the preserved copy sits beside it still
    group- and other-readable, and os.replace would have dropped that inode entirely.

    This is NOT the closed finding about the replacement branch preserving a planted mode. That
    was the canonical file; this is the slot, and the refusal path is the one that keeps it.
    """
    if os.geteuid() == 0:
        pytest.skip("root ignores mode bits; this needs an unprivileged writer")
    driver = make_tool(tmp_path)
    module = import_driver(driver, "preserved_mode_%o" % planted)
    staging = tmp_path / "staging"
    reports_dir = staging / "_reports"
    reports_dir.mkdir(parents=True)
    rp = reports_dir / "scan_report.txt"
    findings = "SECRET\tgeneric_key_assignment\tcontents\tdocs/example.md:12\n"
    rp.write_text(findings, encoding="utf-8")
    rp.chmod(planted)

    module._write_refusal_report(str(staging), module.ScanRefused("report-path-unsafe 'a'"))

    kept = [reports_dir / n for n in _superseded_names() if (reports_dir / n).exists()]
    assert kept, "CONTROL: nothing was preserved, so there is no mode to assert"
    for k in kept:
        mode = stat.S_IMODE(k.stat().st_mode)
        assert not mode & 0o077, (
            f"the preserved copy {k.name} is {mode:04o}, carried from a report planted at "
            f"{planted:04o}. The canonical name was published owner-only and the findings stayed "
            "readable beside it under a second name this tool created")


def test_the_refusal_fallback_strips_an_inherited_acl_too(tmp_path: Path) -> None:
    """The fallback ASSIGNED 0600 and stopped, which is owner-only in the mode bits only.

    It runs when the ordinary policy install has already failed — the path that executes when
    something has gone wrong — and said nothing about an ACL the directory handed the staged file
    at creation. A report whose mode reads 0600 while an inherited entry still grants a named user
    is exactly the channel a mode cap cannot see.
    """
    if os.geteuid() == 0:
        pytest.skip("root ignores these checks; this needs an unprivileged writer")
    if shutil.which("setfacl") is None:
        pytest.skip("setfacl is not installed; the ACL contract cannot be measured here")
    driver = make_tool(tmp_path)
    module = import_driver(driver, "fallback_acl_strip")
    staging = tmp_path / "staging"
    reports_dir = staging / "_reports"
    reports_dir.mkdir(parents=True)
    rp = reports_dir / "scan_report.txt"
    rp.write_text("scan_gate: CLEAN\n", encoding="utf-8")

    applied = subprocess.run(["setfacl", "-d", "-m", f"u:{os.geteuid()}:r,g::r", str(reports_dir)],
                             capture_output=True, text=True)
    if applied.returncode != 0:
        pytest.skip(f"the filesystem refused a default ACL: {applied.stderr.strip()[:80]}")
    probe = reports_dir / ".inherit_probe"
    probe.write_text("x", encoding="utf-8")
    inherits = _acl(probe) is not None
    probe.unlink()
    if not inherits:
        pytest.skip("this filesystem does not hand a new file the directory default ACL")

    # Force the ordinary policy install to fail so the FALLBACK is what publishes.
    def refusing_policy(dirfd, src_name, dst_fd, dst_name):
        raise OSError(errno.EIO, "report-permission-preservation-failed")
    module._install_posix_acl_policy = refusing_policy

    module._write_refusal_report(str(staging), module.ScanRefused("invalid-wide-encoding 'b'"))

    assert rp.read_text(encoding="utf-8").startswith("scan_gate: REFUSED"), (
        "CONTROL: the fallback did not publish, so there is nothing to assert about it")
    assert _acl(rp) is None, (
        "the fallback published a report still carrying the directory's inherited ACL: its mode "
        "reads owner-only and its actual access does not")
    assert not stat.S_IMODE(rp.stat().st_mode) & 0o077, "and the mode must be owner-only too"


@pytest.mark.parametrize("dmode", [0o300, 0o700, 0o755])
def test_a_write_and_traverse_report_directory_still_publishes(tmp_path: Path,
                                                               dmode: int) -> None:
    """An availability regression the hardening introduced, caught by the gate with a control.

    A directory at 0300 grants WRITE and TRAVERSE but not READ. Its owner can create and traverse
    named entries there perfectly well — an O_RDONLY open of it simply fails. Round fourteen used
    O_RDONLY alone to anchor the hardening and turned that into a refusal, where the parent
    scanner published normally. Measured both ways before the fix.

    O_PATH opens such a directory without requiring read; its descriptor cannot be fchmod'd, so
    the mode is reached through /proc/self/fd. Both handles stay O_NOFOLLOW.
    """
    if os.geteuid() == 0:
        pytest.skip("root ignores directory permission bits; this needs an unprivileged writer")
    driver = make_tool(tmp_path)
    module = import_driver(driver, "wx_dir_%o" % dmode)
    staging = tmp_path / "staging"
    reports_dir = staging / "_reports"
    reports_dir.mkdir(parents=True)
    reports_dir.chmod(dmode)
    try:
        canary = reports_dir / ".canary"
        fd = os.open(str(canary), os.O_CREAT | os.O_WRONLY, 0o600)
        os.close(fd)
        os.unlink(str(canary))
    except OSError:
        reports_dir.chmod(0o700)
        pytest.skip(f"this filesystem does not allow creating in a {dmode:04o} directory")

    try:
        module.write_report(str(staging), [("docs/example.md", 12, "SECRET",
                                            "generic_key_assignment", "contents")])
        published = (reports_dir / "scan_report.txt").exists()
    finally:
        reports_dir.chmod(0o700)

    assert published, (
        f"a {dmode:04o} report directory refused the publish, but its owner can create there — "
        "the hardening turned a working configuration into a refusal, which is a stale-CLEAN "
        "consequence rather than a permission limit")


# =============================================================================================
# GROUP 22 — the seventeenth round. A policy expressed as an INTERSECTION cannot tell
# "narrower because the operator wants it narrower" from "narrower because the umask removed
# the owner's OWN bits", and the second one publishes an artifact nobody can open.
#
# The cold leg measured all of this on the box:
#
#   umask 0400 -> mkstemp gives 0200 -> `staged & 0600` publishes the findings at 0200. That
#   report carries the class, path and line of every secret found, and the operator who just
#   asked what the scanner found cannot read it. Python's own tempfile documentation calls
#   mkstemp "readable and writable only by the creating user ID"; under that umask it is false.
#
#   umask 0277 with no _reports -> makedirs(mode=0o700) yields 0500, and 0600/0700 yield
#   0100/0000 -> the next mkstemp raises EACCES -> the scan refuses a tree whose owner can
#   write it perfectly well. Round fourteen's availability regression again, moved from the
#   hardening path into the creation path.
#
# Removing an owner's own bits from a file that owner still owns buys NO confidentiality — the
# uid can restore them whenever it likes — so the intersection was paying an availability cost
# for nothing. ONE RULE replaces it: the report is published at exactly 0600, the report
# directory keeps owner rwx, and group and other are what the confidentiality argument was
# always actually about.
#
# The same round found the mode call reachable through a swapped pathname. Every other metadata
# call in the policy installer passes follow_symlinks=False; os.chmod on this platform cannot
# (os.chmod is not in os.supports_follow_symlinks, and follow_symlinks=False raises
# NotImplementedError, which is not an OSError and would escape the refusal writer). So the one
# call that could not refuse to follow was being made on a name the scanner had already stopped
# holding a descriptor for — a chmod gadget on any file this uid can chmod. It is made on the
# descriptor now, and the staged name is checked to still BE that descriptor's inode before the
# publish.
# =============================================================================================


class _SwapAfterWrite:
    """Wraps the writer object so an interleave fires the instant the body is written.

    The seam is ``os.fdopen``, which the publication path calls in every revision of this file,
    so an arm built on it measures the BEHAVIOUR on either side of the repair rather than a
    changed signature. A test that can only fail with a TypeError is not a proof.
    """

    def __init__(self, wrapped, swap):
        self._wrapped = wrapped
        self._swap = swap

    def __enter__(self):
        self._wrapped.__enter__()
        return self

    def write(self, data):
        return self._wrapped.write(data)

    def __exit__(self, *exc):
        result = self._wrapped.__exit__(*exc)
        self._swap()
        return result


@pytest.mark.parametrize("mask", [0o400, 0o200, 0o600])
def test_a_umask_that_masks_owner_bits_still_publishes_a_readable_report(
    tmp_path: Path, mask: int
) -> None:
    """REPAIRED: the published report is owner-readable whatever the umask masked.

    The report directory is pre-created 0700 so this arm measures the FILE rule alone; the
    directory rule has its own arm below.
    """
    driver = make_tool(tmp_path, name="tool_%o" % mask)
    module = import_driver(driver, "umask_owner_%o" % mask)
    staging = tmp_path / "staging"
    (staging / "_reports").mkdir(parents=True)
    (staging / "_reports").chmod(0o700)

    old = os.umask(mask)
    try:
        module.write_report(str(staging), [("docs/example.md", 12, "SECRET",
                                            "generic_key_assignment", "contents")])
    finally:
        os.umask(old)

    report = staging / REPORT_REL
    mode = stat.S_IMODE(report.stat().st_mode)
    assert mode == 0o600, (
        f"REPAIRED: umask {mask:04o} published the findings report at {mode:04o}; owner-only "
        "means the owner can READ it, and an unopenable report is not a published one"
    )
    assert report.read_text(encoding="utf-8"), (
        "REPAIRED: the report must be openable by the uid that just wrote it"
    )


@pytest.mark.parametrize("mask", [0o277, 0o600, 0o700])
def test_a_umask_that_masks_owner_bits_does_not_refuse_a_writable_tree(
    tmp_path: Path, mask: int
) -> None:
    """REPAIRED: creating the report directory under a restrictive umask must not refuse.

    A CONTROL creates a file directly in the staging tree under the same umask first, so a
    failure here is the publication path's and not the fixture's.
    """
    driver = make_tool(tmp_path, name="tool_%o" % mask)
    module = import_driver(driver, "umask_dir_%o" % mask)
    staging = tmp_path / "staging"
    staging.mkdir()

    old = os.umask(mask)
    try:
        control = staging / "control.txt"
        control_fd = os.open(str(control), os.O_CREAT | os.O_WRONLY, 0o600)
        os.close(control_fd)
        assert control.exists(), (
            f"CONTROL: umask {mask:04o} must still allow an ordinary create in the staging tree "
            "— if this fails the fixture is wrong, not the scanner"
        )
        module.write_report(str(staging), [])
    finally:
        os.umask(old)

    assert (staging / REPORT_REL).exists(), (
        f"REPAIRED: umask {mask:04o} made the publication path refuse a tree its owner can "
        "write; os.makedirs is umask-masked, so mode=0o700 never arrived as 0700"
    )
    dmode = stat.S_IMODE((staging / "_reports").stat().st_mode)
    assert dmode & 0o700 == 0o700, (
        f"REPAIRED: the report directory came out {dmode:04o}; the scanner cannot publish into "
        "a directory it cannot itself write, list and traverse"
    )
    assert not dmode & 0o022, (
        f"CONTROL: the report directory came out {dmode:04o}; restoring the owner's bits must "
        "not hand group or other the write that lets them forge a report"
    )
    # These fixtures deliberately create files and directories the umask stripped to 0000, which
    # the temp-directory cleaner cannot then remove — it leaves undeletable garbage under /tmp and
    # warns on every later run. Restoring the modes is the fixture's own mess to clear up.
    for child in sorted(staging.rglob("*"), reverse=True):
        child.chmod(0o700 if child.is_dir() else 0o600)


def test_the_policy_mode_call_cannot_be_redirected_onto_a_file_outside_the_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REPAIRED: a staged name swapped for a symlink must not mutate the symlink's target.

    Measured on the old path: os.chmod(dst, mode) with no follow_symlinks took a 0644 victim to
    0600 and only THEN raised, because the verifying stat read the symlink's own mode. The
    publish failed, the hits were destroyed, and a file outside the scanned tree was left
    permanently mode-mutated.
    """
    driver = make_tool(tmp_path)
    module = import_driver(driver, "chmod_gadget")
    staging = tmp_path / "staging"
    reports = staging / "_reports"
    reports.mkdir(parents=True)

    victim = tmp_path / "victim_outside_the_tree.txt"
    victim.write_text("not the scanner's file\n", encoding="utf-8")
    victim.chmod(0o644)

    def swap() -> None:
        staged = [p for p in reports.iterdir() if p.name.startswith(".scan_report_")]
        assert staged, "fixture: the staged temporary file was not found to swap"
        target = staged[0]
        target.unlink()
        target.symlink_to(victim)

    real_fdopen = os.fdopen

    def swapping_fdopen(*args, **kwargs):
        return _SwapAfterWrite(real_fdopen(*args, **kwargs), swap)

    monkeypatch.setattr(module.os, "fdopen", swapping_fdopen)

    try:
        module.write_report(str(staging), [("docs/example.md", 12, "SECRET",
                                            "generic_key_assignment", "contents")])
    except Exception:
        pass

    assert stat.S_IMODE(victim.stat().st_mode) == 0o644, (
        "REPAIRED: the policy mode call followed the swapped name and changed the mode of a "
        "file outside the scanned tree — a chmod gadget on anything this uid can chmod"
    )
    assert victim.read_text(encoding="utf-8") == "not the scanner's file\n", (
        "CONTROL: the victim's CONTENT was never in play; if this fires the fixture is wrong"
    )
    report = staging / REPORT_REL
    assert not report.is_symlink(), (
        "REPAIRED: the swapped name was published as the report, so the canonical report path "
        "is now a symlink pointing outside the tree"
    )


@pytest.mark.parametrize("mask", [0o277, 0o022])
def test_a_replacement_and_a_new_report_publish_the_same_mode(
    tmp_path: Path, mask: int
) -> None:
    """REPAIRED: ONE RULE means both branches land on the same number.

    The existing-report branch computed the staged inode's mode and then discarded it, so under
    umask 0277 a replacement published 0600 while a new report in the same directory under the
    same umask published 0400. Two branches, two rules, one docstring claiming otherwise.
    """
    driver = make_tool(tmp_path, name="tool_%o" % mask)
    module = import_driver(driver, "one_rule_%o" % mask)

    modes = {}
    for label in ("new", "replacement"):
        staging = tmp_path / ("staging_" + label)
        reports = staging / "_reports"
        reports.mkdir(parents=True)
        reports.chmod(0o700)
        if label == "replacement":
            existing = reports / "scan_report.txt"
            existing.write_text("scan_gate: CLEAN\n", encoding="utf-8")
            existing.chmod(0o600)

        old = os.umask(mask)
        try:
            module.write_report(str(staging), [("docs/example.md", 12, "SECRET",
                                                "generic_key_assignment", "contents")])
        finally:
            os.umask(old)
        modes[label] = stat.S_IMODE((reports / "scan_report.txt").stat().st_mode)

    assert modes["new"] == modes["replacement"], (
        f"REPAIRED: umask {mask:04o} published a new report at {modes['new']:04o} and a "
        f"replacement at {modes['replacement']:04o}; the docstring says ONE RULE, BOTH BRANCHES"
    )
    assert modes["new"] == 0o600, (
        f"REPAIRED: the one rule is 0600 and both branches published {modes['new']:04o}"
    )


# =============================================================================================
# GROUP 23 — the seventeenth round, second half. Preservation narrows the evidence it keeps,
# and it narrows it on BOTH the branch that links and the branch that finds it already linked.
#
# The gate leg ruled the second branch blocking and the cold leg ranked the ACL/chmod coupling
# MED; those are the two arms here, and they converged independently on the second one, which is
# the strongest signal this round's pairing produced.
#
#   A. The "already preserved by an earlier call" branch returned True without touching the
#      mode. A copy an earlier run left wide — or one whose narrowing failed that time — stayed
#      wide for every run afterwards, beside an owner-only publish, forever.
#
#   B. The ACL strip and the mode cap shared one try block, so a removexattr that failed for any
#      reason other than "there is no ACL here" skipped the chmod entirely and left the preserved
#      copy at exactly the mode it was planted with. The two are not alternatives: the strip
#      closes a channel the mode cannot express, the cap closes the one it can.
# =============================================================================================


def _fill_every_slot_but_the_first(module, reports_dir: Path, report_path: Path) -> Path:
    """Preserve the report into slot 0 and OCCUPY the rest, so the next call cannot link.

    Reaching the already-preserved branch needs every slot to refuse a link: slot 0 because it is
    already this inode (EEXIST), the rest because something else is sitting there. Without this
    the loop simply links into slot 1 and the branch under test never runs.
    """
    slots = list(module._superseded_slots(str(reports_dir)))
    os.link(str(report_path), slots[0])
    for other in slots[1:]:
        Path(other).write_text("occupied by something else\n", encoding="utf-8")
    return Path(slots[0])


def test_a_copy_found_already_preserved_is_narrowed_rather_than_left_wide(tmp_path: Path) -> None:
    """REPAIRED: the branch that finds the findings already kept must still narrow them.

    A hard link keeps the old inode's mode by definition, so a report preserved while it was wide
    stays wide — and this branch answered "already preserved, oldest wins" and returned without
    looking at the mode. The publish that follows is owner-only; the second name beside it was not.
    """
    if os.geteuid() == 0:
        pytest.skip("root ignores mode bits; this needs an unprivileged writer")
    driver = make_tool(tmp_path)
    module = import_driver(driver, "already_preserved_narrow")
    reports_dir = tmp_path / "_reports"
    reports_dir.mkdir()
    report_path = reports_dir / "scan_report.txt"
    report_path.write_text("aws\tkey\tassignment\tdocs/x.md:3\n", encoding="utf-8")
    report_path.chmod(0o644)

    kept = _fill_every_slot_but_the_first(module, reports_dir, report_path)
    assert stat.S_IMODE(kept.stat().st_mode) == 0o644, (
        "CONTROL: the preserved copy starts wide — it is one inode with the report, so if this "
        "is not 0644 the fixture never built the state the arm is about")

    assert preserve_superseded(module, reports_dir, report_path.name) is True, (
        "CONTROL: with the findings already preserved this must answer True; a False here means "
        "the arm measured a refusal path instead of the already-preserved branch")

    got = stat.S_IMODE(kept.stat().st_mode)
    assert not got & 0o077, (
        f"REPAIRED: the already-preserved copy is still {got:04o}. Preservation keeps the leak "
        "the owner-only publish was about to close, and this branch never narrowed it")


def test_a_failed_acl_strip_does_not_skip_the_mode_cap(tmp_path: Path, monkeypatch) -> None:
    """REPAIRED: the strip and the cap are independent, and a failed strip must not skip the cap.

    Both review legs reached this one from different directions. The strip closes a channel the
    mode bits cannot express; the cap closes the one they can. Sharing a try block made the second
    conditional on the first succeeding, so a filesystem that refuses removexattr for any reason
    other than "no ACL here" published a preserved copy at its planted mode.
    """
    if os.geteuid() == 0:
        pytest.skip("root ignores mode bits; this needs an unprivileged writer")
    driver = make_tool(tmp_path)
    module = import_driver(driver, "strip_fails_cap_runs")
    reports_dir = tmp_path / "_reports"
    reports_dir.mkdir()
    report_path = reports_dir / "scan_report.txt"
    report_path.write_text("aws\tkey\tassignment\tdocs/x.md:3\n", encoding="utf-8")
    report_path.chmod(0o644)

    refused: list[str] = []

    def refusing_removexattr(path, attribute, *args, **kwargs):
        refused.append(str(path))
        raise PermissionError(errno.EPERM, "removexattr refused (injected)")

    monkeypatch.setattr(module.os, "removexattr", refusing_removexattr)

    # RESCOPED IN ROUND TWENTY-SIX, and the property it was written for is the one asserted below.
    # This arm was authored against a version that answered True here, and it read that answer as
    # "the evidence was not destroyed". Those are different claims, and round twenty-six separated
    # them: a denied strip now forfeits the reserved name and DECLINES the replacement, so the
    # findings stay where they already were — at the canonical name, under their own inode, with
    # the mode cap applied. Nothing this arm was protecting is gone; the place to look for it
    # moved. What the arm must NOT do is assert the old answer, because that answer is what
    # authorized replacing a report whose retained copy carried a policy nobody installed.
    assert preserve_superseded(module, reports_dir, report_path.name) is False, (
        "REPAIRED: a denied strip still authorized the replacement, and the reserved name then "
        "stood for an access policy that was refused")

    if not refused:
        pytest.skip("this build never attempted an ACL strip; nothing was made to fail")

    assert report_path.read_text(encoding="utf-8") == "aws\tkey\tassignment\tdocs/x.md:3\n", (
        "CONTROL: the evidence must be intact — declining to preserve a SECOND name for an inode "
        "costs no bytes, and this is the assertion the original True was standing in for")
    got = stat.S_IMODE(report_path.stat().st_mode)
    assert not got & 0o077, (
        f"REPAIRED: the ACL strip failed and took the mode cap down with it — the findings are at "
        f"{got:04o}. The strip and the cap are independent; a report nobody should have been able "
        "to read stayed group- and other-readable because an unrelated call raised")
    for s in (Path(x) for x in module._superseded_slots(str(reports_dir))):
        if s.exists():
            assert s.stat().st_ino == report_path.stat().st_ino, (
                f"REPAIRED: {s.name} is a reserved name for an inode other than the report left "
                "standing")


# =============================================================================================
# GROUP 24 — the seventeenth round, third part. A publish that REFUSES must not take this scan's
# findings down with it.
#
# The cold leg reproduced this from the CLI. A FIFO at the canonical report name is not a
# symlink, so the path check admits it; the policy installer then refuses it as a non-regular
# file; and the failure path unlinked the staged temporary that held the secrets this scan had
# just found. `_write_refusal_report` runs next and receives only the EXCEPTION — it has no
# access to `hits` and cannot carry them — so it publishes `scan_gate: REFUSED input-error` over
# the top. The operator is left with a refusal naming a permission problem, and the evidence that
# there were real secrets in the tree is gone.
#
# Any other OSError out of the policy installer takes the same path: a foreign uid, a gid that
# cannot be preserved, a mode the filesystem will not verify.
#
# So a staged FINDINGS report that cannot be published is kept at a reserved name instead of
# deleted. A staged CLEAN is not: it carries no evidence, and leaving one beside a refusal would
# put a file saying CLEAN next to an rc 2, which is the false-authorization direction every other
# rule in this file exists to prevent.
# =============================================================================================


UNPUBLISHED_REL = Path("_reports") / "scan_report.unpublished.txt"


def test_findings_survive_a_policy_failure_instead_of_being_unlinked(tmp_path: Path) -> None:
    """REPAIRED: the hits this scan found outlive a publish that could not complete."""
    if os.geteuid() == 0:
        pytest.skip("root ignores mode bits; this needs an unprivileged writer")
    driver = make_tool(tmp_path)
    module = import_driver(driver, "quarantine_fifo")
    staging = tmp_path / "staging"
    reports = staging / "_reports"
    reports.mkdir(parents=True)
    fifo = reports / "scan_report.txt"
    try:
        os.mkfifo(str(fifo))
    except (OSError, AttributeError):
        pytest.skip("this platform cannot create a FIFO; the refusal state is not reachable here")

    with pytest.raises(Exception):
        module.write_report(str(staging), [("docs/example.md", 12, "SECRET",
                                            "generic_key_assignment", "contents")])

    kept = staging / UNPUBLISHED_REL
    assert kept.exists(), (
        "REPAIRED: the staged findings were unlinked on the way out of a failed publish. The "
        "refusal writer that runs next receives only the exception and cannot carry them, so "
        "this scan's evidence is gone and the caller sees a permission complaint instead")
    body = kept.read_text(encoding="utf-8")
    assert "docs/example.md:12" in body and "SECRET" in body, (
        f"the kept file is not the findings report — it holds {body!r}")
    mode = stat.S_IMODE(kept.stat().st_mode)
    assert mode == 0o600, (
        f"the kept findings landed at {mode:04o}; a report that could not be published is still a "
        "report naming secrets, and it does not get a wider audience than one that could")

    # CONTROL: the fixture really did drive the refusal path rather than some other failure.
    assert stat.S_ISFIFO(os.stat(str(fifo), follow_symlinks=False).st_mode), (
        "CONTROL: the FIFO is gone, so this arm measured a different failure than the one it "
        "was written for")


def test_a_staged_clean_is_not_kept_when_the_publish_fails(tmp_path: Path) -> None:
    """CONTROL-SHAPED REPAIR: only EVIDENCE is worth keeping past a refusal.

    A staged status line carries nothing, and a file saying CLEAN sitting beside an rc 2 is the
    false authorization this scanner exists to prevent. The asymmetry is the point: findings are
    kept, status lines are not.
    """
    driver = make_tool(tmp_path)
    module = import_driver(driver, "quarantine_clean")
    staging = tmp_path / "staging"
    reports = staging / "_reports"
    reports.mkdir(parents=True)
    try:
        os.mkfifo(str(reports / "scan_report.txt"))
    except (OSError, AttributeError):
        pytest.skip("this platform cannot create a FIFO; the refusal state is not reachable here")

    with pytest.raises(Exception):
        module.write_report(str(staging), [])

    assert not (staging / UNPUBLISHED_REL).exists(), (
        "a staged CLEAN was kept past the refusal. It is not evidence, and a file saying CLEAN "
        "beside an rc 2 tells a reader this tree passed")
    leftovers = [p.name for p in reports.iterdir() if p.name.startswith(".scan_report_")]
    assert not leftovers, (
        f"the staged temporary was left behind as {leftovers}; not keeping it means removing it")


def test_a_later_successful_publish_clears_the_unpublished_findings(tmp_path: Path) -> None:
    """The generation boundary applies to this name too, exactly as it does to the slots.

    A kept unpublished report belongs to the scan that could not publish it. Once a later scan
    HAS published, leaving it behind puts a stale findings file beside a current report, and the
    reader cannot tell which run either came from.
    """
    driver = make_tool(tmp_path)
    module = import_driver(driver, "quarantine_cleared")
    staging = tmp_path / "staging"
    reports = staging / "_reports"
    reports.mkdir(parents=True)
    stale = staging / UNPUBLISHED_REL
    stale.write_text("aws\tkey\tassignment\tdocs/old.md:1\n", encoding="utf-8")

    module.write_report(str(staging), [("docs/example.md", 12, "SECRET",
                                        "generic_key_assignment", "contents")])

    assert (staging / REPORT_REL).exists(), "CONTROL: this run must actually have published"
    assert not stale.exists(), (
        "a successful publish left the previous generation's unpublished findings in place; the "
        "reserved names belong to one report generation, and this is where that generation ends")


# =============================================================================================
# GROUP 25 — the eighteenth round. The directory was validated and then named again.
#
# Every earlier round anchored a FILE: the staged descriptor is held across the metadata install,
# the mode goes on with fchmod, the ACL strip reaches the inode through /proc/self/fd. None of it
# helped, because the DIRECTORY those names were resolved in was still a pathname. The gate leg
# reproduced the consequence and I reproduced it independently before changing anything:
#
#   harden `_reports`, then replace it with a symlink to a directory outside the scanned tree.
#   The findings were published OVER an external file the scanner does not own, and an external
#   `scan_report.superseded.txt` was deleted by the generation sweep on the way past. Every check
#   inside write_report still passed, because each one re-resolved the same substituted name.
#
# The gate's fix direction was explicit: hold the validated directory identity through creation,
# metadata installation, replacement, preservation and cleanup, and anchor operations to that
# identity — a late pathname recheck alone introduces another race window. That is what this
# round does. `_harden_report_dir` now RETURNS the descriptor it validated, and every name in the
# publication path is resolved relative to it.
#
# What is NOT claimed: that the window before os.replace is gone. renameat still takes names, and
# a writer who can create inside the report directory can still swap the staged name. What
# changed is who that writer can be — the hardening removes write from group and other, so the
# race needs the scanner's own uid, and a same-uid attacker owns the tree anyway.
# =============================================================================================


def test_a_directory_substituted_after_hardening_cannot_redirect_the_publish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REPAIRED: publication follows the validated DIRECTORY, not the name it was found under."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "dir_identity")
    staging = tmp_path / "staging"
    reports = staging / "_reports"
    reports.mkdir(parents=True)

    outside = tmp_path / "outside_the_tree"
    outside.mkdir()
    external = outside / "scan_report.txt"
    external.write_text("SOMEONE ELSE'S FILE\n", encoding="utf-8")
    external_slot = outside / "scan_report.superseded.txt"
    external_slot.write_text("SOMEONE ELSE'S PRESERVED FILE\n", encoding="utf-8")

    real_harden = module._harden_report_dir

    def harden_then_substitute(path, restore_owner=False, parent_fd=None):
        dirfd = real_harden(path, restore_owner, parent_fd)
        # The substitution lands in the window the gate identified: after validation, before a
        # single one of the publication path's names has been resolved.
        os.rename(str(reports), str(staging / "moved_aside"))
        os.symlink(str(outside), str(reports))
        return dirfd

    monkeypatch.setattr(module, "_harden_report_dir", harden_then_substitute)
    module.write_report(str(staging), [("docs/example.md", 12, "SECRET",
                                        "generic_key_assignment", "contents")])

    assert external.read_text(encoding="utf-8") == "SOMEONE ELSE'S FILE\n", (
        "REPAIRED: the findings were published OVER a file outside the scanned tree. The "
        "directory was validated and then named again, so every later name resolved through the "
        "substitution")
    assert external_slot.exists(), (
        "REPAIRED: the generation sweep deleted a file outside the scanned tree, because the "
        "superseded slot names were joined onto a path rather than resolved against the "
        "descriptor that was checked")
    published = staging / "moved_aside" / "scan_report.txt"
    assert published.exists(), (
        "the report did not land in the directory that was actually validated; anchoring must "
        "redirect the publish back to the real directory, not merely refuse")
    assert stat.S_IMODE(published.stat().st_mode) == 0o600, (
        "and it must still land under the one mode rule")


def test_the_refusal_fallback_sets_the_mode_through_the_descriptor(tmp_path: Path) -> None:
    """REPAIRED: the last-resort publish must not be the one pathname chmod left standing.

    The cold leg put it exactly: the fallback `os.chmod(tmp_path, 0o600)` was the chmod gadget the
    primary path had been rewritten to close, and the refusal writer's own identity check is typed
    as OSError — so CATCHING the primary path's refusal is what routes into the fallback. The two
    defects composed: win the race the primary path detects, and the recovery hands you a
    pathname chmod.
    """
    source = SCANNER.read_text(encoding="utf-8")
    # Anchored on a symbol that exists on BOTH sides of the change. An earlier draft indexed from
    # _publish_refusal, which round eighteen introduced — so on the previous commit the arm died
    # with "substring not found" instead of reporting the gadget. A test that can only fail
    # because a name is missing has not measured the behaviour it is named for.
    fallback = source[source.index("def _write_refusal_report"):]
    assert "os.chmod(tmp_path" not in fallback, (
        "the refusal writer still chmods a staged PATHNAME; on this platform os.chmod cannot "
        "decline to follow a symlink, so that call is reachable as a gadget")
    assert "os.fchmod(fd, _REPORT_MODE)" in fallback, (
        "the fallback must set the mode through the descriptor it already holds")
    assert "tempfile.mkstemp" not in fallback, (
        "the refusal writer still stages through mkstemp, which resolves its directory by NAME "
        "and so cannot be anchored to the validated descriptor")


# =============================================================================================
# GROUP 26 — two holes in the EDGES, both from the cold leg's review of the previous state.
#
#   F6. The refusal writer's contract is "never raises" — it is called to REPORT a failure and
#       must not displace it. Its guard catches (OSError, UnicodeError). But it calls str() on
#       the refusal object to classify it, and str() on an arbitrary exception can raise anything
#       at all. A ValueError out of __str__ escapes the one function in this file that is not
#       allowed to raise, and the original refusal is lost with it.
#
#   F8. os.makedirs applies its mode argument to the LAST component only. Every ancestor it
#       creates takes the default 0o777 masked by the umask, so at umask 0 the scanner creates a
#       WORLD-WRITABLE ancestor and then carefully puts an owner-only report directory inside it.
#       Anyone local can rename that ancestor. The hardening that removes group and other write
#       from _reports never looked one level up.
# =============================================================================================


class _RefusalWhoseStrRaises(Exception):
    """A refusal that cannot be rendered. str() on an exception runs arbitrary user code."""

    def __str__(self):
        raise ValueError("this refusal cannot be rendered")


def test_a_refusal_that_cannot_be_rendered_does_not_escape_the_writer(tmp_path: Path) -> None:
    """REPAIRED: the never-raises contract holds even when classification itself explodes."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "unrenderable_refusal")
    staging = tmp_path / "staging"
    reports = staging / "_reports"
    reports.mkdir(parents=True)
    rp = reports / "scan_report.txt"
    rp.write_text("scan_gate: CLEAN\n", encoding="utf-8")

    # CONTROL: the fixture really does raise where the writer will call it.
    with pytest.raises(ValueError):
        str(_RefusalWhoseStrRaises())

    module._write_refusal_report(str(staging), _RefusalWhoseStrRaises())

    assert rp.read_text(encoding="utf-8").startswith("scan_gate: REFUSED"), (
        "REPAIRED: a refusal whose str() raised escaped the writer, so the stale CLEAN survived "
        "beside the failure and the original refusal was displaced by a ValueError")


def test_creating_the_report_tree_does_not_leave_a_world_writable_ancestor(
    tmp_path: Path
) -> None:
    """REPAIRED: every directory the scanner creates is owner-only, not just the last one."""
    if os.geteuid() == 0:
        pytest.skip("root ignores mode bits; this needs an unprivileged writer")
    driver = make_tool(tmp_path)
    module = import_driver(driver, "ancestor_modes")
    # The staging tree does not exist yet, so the publication path has to build the chain.
    staging = tmp_path / "missing" / "deeper" / "staging"

    old = os.umask(0)
    try:
        module.write_report(str(staging), [])
    finally:
        os.umask(old)

    assert (staging / REPORT_REL).exists(), "CONTROL: nothing was published, so nothing to check"
    wide = []
    probe = staging / "_reports"
    while True:
        mode = stat.S_IMODE(probe.stat().st_mode)
        if mode & 0o022:
            wide.append(f"{probe.name}={mode:04o}")
        if probe == tmp_path or probe.parent == probe:
            break
        probe = probe.parent
    assert not wide, (
        f"the scanner created group- or other-writable directories at {wide}. os.makedirs gives "
        "its mode to the LAST component only; an ancestor anyone can write is an ancestor anyone "
        "can rename, with the owner-only report directory still sitting inside it")


# =============================================================================================
# GROUP 27 — the nineteenth round. Two findings from the publication gate, both measured there
# with an injected fault AND a no-injection control, and both reproduced here before the fix.
#
#   F1. `_preserve_superseded` caught EVERY OSError from its opening lstat and answered True,
#       under a comment reading "nothing there; nothing to lose". An EIO or a transient EACCES is
#       not evidence that the file is absent — it is evidence that we could not look. Answering
#       True authorizes the caller to replace the findings without preserving them. The gate
#       injected one EIO and one EACCES into that single call, left every other call real, and
#       watched the old findings disappear; its no-injection control kept them.
#
#   F5. My own regression from round seventeen. The hardening verifies the post-chmod mode
#       against a fixed 0700 even when restoration was NOT requested, so a pre-existing 0322
#       directory — which the hardening itself narrows to 0300 — is then refused for lacking
#       owner read. A directory ALREADY at 0300 skips that branch and publishes. The scanner
#       therefore accepted or refused the same effective directory depending on where it started.
# =============================================================================================


def test_an_unreadable_report_is_not_treated_as_an_absent_one(tmp_path: Path) -> None:
    """REPAIRED: failing to INSPECT the report is not a finding that it is gone."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "lstat_error_preserve")
    staging = tmp_path / "staging"
    reports = staging / "_reports"
    reports.mkdir(parents=True)
    rp = reports / "scan_report.txt"
    rp.write_text("aws\tkey\tassignment\tdocs/secret.md:4\n", encoding="utf-8")

    real_lstat = module.os.lstat
    fired: list[int] = []

    def lstat_that_cannot_look(path, *args, **kwargs):
        # Keyed on the CALLER, not on a call index. The refusal writer inspects this same name
        # twice before preservation ever runs — an existence check and a symlink check — so an
        # injection keyed on "the first lstat of scan_report.txt" lands on the existence check,
        # which returns early and leaves the findings intact for the wrong reason. The arm then
        # passes against the unfixed code. Measured: that is exactly what the first draft did.
        caller = sys._getframe(1).f_code.co_name
        if caller == "_preserve_superseded" and not fired:
            fired.append(errno.EIO)
            raise OSError(errno.EIO, "injected inspection failure")
        return real_lstat(path, *args, **kwargs)

    module.os.lstat = lstat_that_cannot_look
    try:
        module._write_refusal_report(str(staging), module.ScanRefused("fixture-refusal"))
    finally:
        module.os.lstat = real_lstat

    assert fired, (
        "CONTROL: the injection never fired, so this arm measured an ordinary run and would pass "
        "against an implementation that ignores inspection errors entirely")
    surviving = rp.read_text(encoding="utf-8") if rp.exists() else ""
    preserved = [p for p in reports.iterdir() if p.name.startswith("scan_report.superseded")]
    kept = "docs/secret.md:4" in surviving or any(
        "docs/secret.md:4" in p.read_text(encoding="utf-8") for p in preserved)
    assert kept, (
        "REPAIRED: an lstat that could not look answered 'nothing there; nothing to lose', and "
        "the findings were replaced by a refusal line without ever being preserved")


def test_a_no_injection_control_run_preserves_the_findings(tmp_path: Path) -> None:
    """CONTROL for the arm above: with nothing injected, the ordinary path keeps the evidence."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "lstat_error_control")
    staging = tmp_path / "staging"
    reports = staging / "_reports"
    reports.mkdir(parents=True)
    rp = reports / "scan_report.txt"
    rp.write_text("aws\tkey\tassignment\tdocs/secret.md:4\n", encoding="utf-8")

    module._write_refusal_report(str(staging), module.ScanRefused("fixture-refusal"))

    preserved = [p for p in reports.iterdir() if p.name.startswith("scan_report.superseded")]
    assert any("docs/secret.md:4" in p.read_text(encoding="utf-8") for p in preserved), (
        "CONTROL: the ordinary refusal path must preserve the findings it replaces; if this fails "
        "the fixture is wrong and the injected arm above proves nothing")


@pytest.mark.parametrize("start", [0o300, 0o322, 0o332, 0o700])
def test_a_usable_report_directory_is_not_refused_for_lacking_owner_read(
    tmp_path: Path, start: int
) -> None:
    """REPAIRED: the hardening must judge the mode it asked for, not a fixed 0700.

    0o300 already published before this round; 0o322 is the SAME effective directory once group
    and other write are removed, and it was refused. Whether the scanner accepted a directory
    depended on which side of its own narrowing it started.
    """
    if os.geteuid() == 0:
        pytest.skip("root ignores mode bits; this needs an unprivileged writer")
    driver = make_tool(tmp_path, name="tool_%o" % start)
    module = import_driver(driver, "usable_dir_%o" % start)
    staging = tmp_path / "staging"
    reports = staging / "_reports"
    reports.mkdir(parents=True)
    reports.chmod(start)
    try:
        canary = ".canary_probe"
        fd = os.open(str(reports / canary), os.O_CREAT | os.O_WRONLY, 0o600)
        os.close(fd)
        os.unlink(str(reports / canary))
    except OSError:
        reports.chmod(0o700)
        pytest.skip(f"this filesystem does not allow creating in a {start:04o} directory")

    try:
        module.write_report(str(staging), [("docs/example.md", 12, "SECRET",
                                            "generic_key_assignment", "contents")])
        published = (reports / "scan_report.txt").exists()
        final = stat.S_IMODE(reports.stat().st_mode)
    finally:
        reports.chmod(0o700)

    assert published, (
        f"a {start:04o} report directory was refused, but its owner can create there — the "
        "post-chmod check demanded owner read that restoration never asked for")
    assert not final & 0o022, (
        f"and the directory kept group or other write at {final:04o}, which is the one thing the "
        "hardening exists to remove")


# =============================================================================================
# GROUP 28 — the nineteenth round, quarantine. Round seventeen added a place to KEEP findings a
# publish could not complete. The gate found it destroys evidence in both directions.
#
#   F2a. The quarantine name can be BLOCKED — a populated directory sitting at it, which cannot
#        be removed and is not ours to remove. Quarantine then fails, and the caller's cleanup
#        unlinks the staged findings. The run that was supposed to be protected loses its hits
#        because somebody else's data was in the way.
#
#   F2b. Quarantine used a REPLACING rename onto one fixed name. An earlier run's kept findings
#        were silently overwritten by a later run's. A mechanism whose whole purpose is not
#        losing evidence was overwriting evidence.
#
#   F4.  The quarantined file got fchmod but no ACL strip, because the failure that sends us here
#        happens BEFORE the ordinary installer's strip. At 0600 the mask suppresses named entries,
#        so this is not an immediate read leak — but the retained evidence does not meet the same
#        ACL-free policy as the published report, and one chmod re-arms it.
# =============================================================================================


def _findings_anywhere(reports: Path, needle: str) -> bool:
    """True if any regular file under the report directory still carries the marker."""
    for path in reports.rglob("*"):
        if path.is_file():
            try:
                if needle in path.read_text(encoding="utf-8"):
                    return True
            except (OSError, UnicodeDecodeError):
                continue
    return False


def _fifo_at_report(reports: Path) -> None:
    try:
        os.mkfifo(str(reports / "scan_report.txt"))
    except (OSError, AttributeError):
        pytest.skip("this platform cannot create a FIFO; the refusal state is not reachable here")


NEW_HIT = [("docs/example.md", 12, "SECRET", "generic_key_assignment", "contents")]


def test_a_blocked_quarantine_name_does_not_cost_this_run_its_findings(tmp_path: Path) -> None:
    """REPAIRED: somebody else's data in the way must not cost us the evidence."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "quarantine_blocked")
    staging = tmp_path / "staging"
    reports = staging / "_reports"
    reports.mkdir(parents=True)
    _fifo_at_report(reports)
    blocker = reports / "scan_report.unpublished.txt"
    blocker.mkdir()
    (blocker / "keep").write_text("OPERATOR DATA\n", encoding="utf-8")

    with pytest.raises(Exception):
        module.write_report(str(staging), NEW_HIT)

    assert (blocker / "keep").read_text(encoding="utf-8") == "OPERATOR DATA\n", (
        "CONTROL: the blocking directory's contents must be untouched; this scanner does not "
        "remove a populated directory it did not create")
    assert _findings_anywhere(reports, "docs/example.md:12"), (
        "REPAIRED: the quarantine name was occupied, so the cleanup deleted the staged findings. "
        "A name being unavailable is not a reason to destroy the evidence it was going to hold")


def test_an_earlier_quarantined_report_is_not_replaced_by_a_later_one(tmp_path: Path) -> None:
    """REPAIRED: preserving THIS run's evidence must not destroy an earlier run's."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "quarantine_no_clobber")
    staging = tmp_path / "staging"
    reports = staging / "_reports"
    reports.mkdir(parents=True)
    earlier = reports / "scan_report.unpublished.txt"
    earlier.write_text("aws\tkey\tassignment\tdocs/older.md:1\n", encoding="utf-8")
    _fifo_at_report(reports)

    with pytest.raises(Exception):
        module.write_report(str(staging), NEW_HIT)

    assert _findings_anywhere(reports, "docs/older.md:1"), (
        "REPAIRED: an earlier run's quarantined findings were overwritten by this run's. A "
        "replacing rename onto one fixed name makes the evidence store destroy evidence")
    assert _findings_anywhere(reports, "docs/example.md:12"), (
        "and this run's findings must be kept too, under a name of their own")


def test_quarantined_findings_carry_no_inherited_acl(tmp_path: Path) -> None:
    """REPAIRED: retained evidence meets the same ACL-free policy as a published report."""
    if os.geteuid() == 0:
        pytest.skip("root ignores mode bits; this needs an unprivileged writer")
    if shutil.which("setfacl") is None:
        pytest.skip("setfacl is not installed; the ACL contract cannot be measured here")
    driver = make_tool(tmp_path)
    module = import_driver(driver, "quarantine_acl")
    staging = tmp_path / "staging"
    reports = staging / "_reports"
    reports.mkdir(parents=True)
    applied = subprocess.run(["setfacl", "-d", "-m", f"u:{os.geteuid()}:rw,o::r", str(reports)],
                             capture_output=True, text=True)
    if applied.returncode != 0:
        pytest.skip(f"the filesystem refused a default ACL: {applied.stderr.strip()[:80]}")
    probe = reports / ".inherit_probe"
    probe.write_text("x", encoding="utf-8")
    inherits = _acl(probe) is not None
    probe.unlink()
    if not inherits:
        pytest.skip("this filesystem does not hand a new file the directory default ACL")

    _fifo_at_report(reports)
    with pytest.raises(Exception):
        module.write_report(str(staging), NEW_HIT)

    kept = [p for p in reports.rglob("*")
            if p.is_file() and "docs/example.md:12" in p.read_text(encoding="utf-8")]
    assert kept, "CONTROL: nothing was retained, so there is no artifact to check"
    assert _acl(kept[0]) is None, (
        f"the retained findings at {kept[0].name} still carry the directory's inherited ACL: the "
        "failure that sends us here happens before the ordinary installer's strip, so quarantine "
        "has to do its own")


def test_creating_an_ancestor_cannot_change_the_mode_of_a_substituted_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REPAIRED: the last pathname pair in the publication path, found by both review legs.

    Creating the missing staging chain did pathname `mkdir` followed by pathname `chmod`. The gate
    injected a rename-and-symlink between those two calls; the chmod then landed on a directory
    OUTSIDE the supplied tree and publication followed it there. The cold leg reached the same
    helper from the other side — creating through an intermediate symlink. Each component is now
    created relative to the descriptor of the one above it and its mode set through a descriptor,
    so a name substituted underneath us is refused rather than followed.
    """
    if os.geteuid() == 0:
        pytest.skip("root ignores mode bits; this needs an unprivileged writer")
    driver = make_tool(tmp_path)
    module = import_driver(driver, "ancestor_swap")
    outside = tmp_path / "outside_the_tree"
    outside.mkdir()
    (outside / "sentinel").write_text("OUTSIDE DATA\n", encoding="utf-8")
    outside.chmod(0o755)
    staging = tmp_path / "absent" / "staging"

    real_mkdir = module.os.mkdir
    fired: list[str] = []

    def mkdir_then_substitute(name, mode=0o777, *args, **kwargs):
        # Keyed on the BASENAME and tolerant of both call shapes. The previous implementation
        # created components by full pathname with no dir_fd; this one creates a basename relative
        # to a held descriptor. An injection written against only the new shape never fires on the
        # old code, and the arm then fails on its own control instead of on the behaviour — which
        # is exactly what the first draft of this arm did.
        real_mkdir(name, mode, *args, **kwargs)
        if not fired and os.path.basename(str(name)) == "staging":
            fired.append(str(name))
            dir_fd = kwargs.get("dir_fd")
            moved = str(name) + "_moved"
            if dir_fd is None:
                os.rename(str(name), moved)
                os.symlink(str(outside), str(name))
            else:
                os.rename(str(name), moved, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
                os.symlink(str(outside), str(name), dir_fd=dir_fd)

    monkeypatch.setattr(module.os, "mkdir", mkdir_then_substitute)
    try:
        module.write_report(str(staging), [("docs/example.md", 12, "SECRET",
                                            "generic_key_assignment", "contents")])
    except Exception:
        pass

    assert fired, (
        "CONTROL: the substitution never fired, so this arm measured an ordinary run and proves "
        "nothing about what happens when the name changes underneath the helper")
    mode = stat.S_IMODE(outside.stat().st_mode)
    assert mode == 0o755, (
        f"REPAIRED: a directory outside the supplied tree was chmod'd to {mode:04o}. The helper "
        "created by name and then set the mode by name, so the substituted symlink was followed")
    assert not (outside / "_reports").exists(), (
        "REPAIRED: the report directory was created outside the supplied tree entirely")
    assert (outside / "sentinel").read_text(encoding="utf-8") == "OUTSIDE DATA\n", (
        "CONTROL: the outside directory's contents were never in play")


# =============================================================================================
# GROUP 29 — the twentieth round, from the cold leg's review of the nineteenth state.
#
#   #2 The symlink refusal fires ABOVE _stage_report, so the hits are never written anywhere and
#      the quarantine added for the FIFO case cannot see them. The leg put it exactly: "this is
#      the FIFO bug's sibling" — a FIFO is admitted, staged, failed and quarantined; a symlink is
#      refused earlier, and the evidence dies with the raise.
#
#   #5 `_narrow_kept_copy` did `_kept & 0o600` on a preserved copy. At a planted 0044 — other
#      readable, owner bits clear, and the owner is not "other" — that maps to 0000, so the only
#      copy of the findings becomes unreadable to its owner. Setting the report mode on the held
#      descriptor is both narrower for group and other AND readable by the owner.
#
#   #9 The classify and narrow opens have no O_NONBLOCK. Opening a FIFO for read blocks until a
#      writer appears, so a name that becomes a FIFO hangs the one function that must always
#      return. Hanging is worse than raising.
#
#   #3 The refusal writer's FALLBACK replaces without the staged-inode check the ordinary path
#      twelve lines above performs, under a comment explaining why that check exists.
# =============================================================================================


def test_a_symlinked_report_name_does_not_cost_this_scan_its_findings(tmp_path: Path) -> None:
    """REPAIRED: refusing the canonical name must not happen before the evidence is on disk."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "symlink_before_stage")
    staging = tmp_path / "staging"
    reports = staging / "_reports"
    reports.mkdir(parents=True)
    outside = tmp_path / "outside.txt"
    outside.write_text("scan_gate: CLEAN\n", encoding="utf-8")
    (reports / "scan_report.txt").symlink_to(outside)

    with pytest.raises(Exception):
        module.write_report(str(staging), NEW_HIT)

    assert _findings_anywhere(reports, "docs/example.md:12"), (
        "REPAIRED: a symlink at the report name was refused before the findings were staged, so "
        "this scan's hits existed only in the argument list and died with the raise. The refusal "
        "writer never receives them")
    assert outside.read_text(encoding="utf-8") == "scan_gate: CLEAN\n", (
        "CONTROL: the symlink's target must not have been written through")


@pytest.mark.parametrize("planted", [0o044, 0o004, 0o040, 0o000])
def test_a_preserved_copy_stays_readable_by_its_owner(tmp_path: Path, planted: int) -> None:
    """REPAIRED: narrowing must not map a planted mode onto one nobody can read.

    0o044 is the case that names the defect: `_kept & 0o600` is 0o000 because the owner is not
    "other". The only copy of the findings then needs an extra chmod before anyone can read it.
    """
    if os.geteuid() == 0:
        pytest.skip("root ignores mode bits; this needs an unprivileged writer")
    driver = make_tool(tmp_path, name="tool_%o" % planted)
    module = import_driver(driver, "narrow_readable_%o" % planted)
    reports = tmp_path / "_reports"
    reports.mkdir()
    rp = reports / "scan_report.txt"
    rp.write_text("aws\tkey\tassignment\tdocs/kept.md:9\n", encoding="utf-8")
    rp.chmod(planted)

    try:
        assert preserve_superseded(module, reports) is True, (
            "CONTROL: preservation must succeed here, or there is no kept copy to inspect")
        kept = [p for p in reports.iterdir() if p.name.startswith("scan_report.superseded")]
        assert kept, "CONTROL: nothing was preserved"
        mode = stat.S_IMODE(kept[0].stat().st_mode)
    finally:
        for p in reports.iterdir():
            try:
                p.chmod(0o600)
            except OSError:
                pass

    assert not mode & 0o077, (
        f"a copy planted at {planted:04o} was preserved at {mode:04o}, still open to group or other")
    assert mode & stat.S_IRUSR, (
        f"a copy planted at {planted:04o} was preserved at {mode:04o} — its OWNER cannot read the "
        "only surviving copy of the findings. Narrowing must not produce an unreadable artifact")


def test_narrowing_a_fifo_does_not_hang(tmp_path: Path) -> None:
    """REPAIRED: the one function that must always return must not block on a FIFO open.

    open(2): opening the read end of a FIFO blocks until the other end is opened. Without
    O_NONBLOCK a name that has become a FIFO stops the refusal writer forever, and a refusal that
    never returns is worse than one that raises.
    """
    import threading, time

    driver = make_tool(tmp_path)
    module = import_driver(driver, "fifo_no_hang")
    reports = tmp_path / "_reports"
    reports.mkdir()
    try:
        os.mkfifo(str(reports / "scan_report.superseded.txt"))
    except (OSError, AttributeError):
        pytest.skip("this platform cannot create a FIFO")

    dirfd = os.open(str(reports), os.O_RDONLY | os.O_DIRECTORY)
    done = threading.Event()

    def run():
        try:
            module._narrow_kept_copy(dirfd, "scan_report.superseded.txt")
        finally:
            done.set()

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    finished = done.wait(timeout=5.0)
    os.close(dirfd)
    assert finished, (
        "REPAIRED: narrowing blocked on a FIFO open with no writer. O_NONBLOCK does not appear in "
        "the publication path, so any name that becomes a FIFO hangs the refusal writer")


def test_the_refusal_fallback_checks_the_staged_inode_before_replacing(tmp_path: Path) -> None:
    """REPAIRED: the fallback publishes under the same identity rule as the ordinary path.

    The ordinary path checks the staged name is still the held inode and says why: renameat would
    otherwise publish a planted symlink under the canonical name. The fallback, twelve lines
    below, replaced without that check — and the fallback is the path that runs when something has
    already gone wrong.
    """
    source = SCANNER.read_text(encoding="utf-8")
    body = source[source.index("def _write_refusal_report"):]
    fallback = body[body.index("except (OSError, UnicodeError):"):]
    # RESCOPED IN ROUND THIRTY. This arm used to look for the identity check and the single
    # os.replace INSIDE the fallback's own text. Round thirty moved both into the one shared
    # publication path, `_replace_canonical_guarded`, precisely so that neither refusal branch
    # can omit them again — so the property is now: the fallback CALLS that path, and that path
    # carries the identity check and the module's only refusal replace.
    assert "_replace_canonical_guarded(" in fallback, (
        "REPAIRED: the refusal writer's fallback does not go through the shared publication "
        "path, so it can replace the canonical name without the checks that path carries")
    helper = source[source.index("def _replace_canonical_guarded"):]
    helper = helper[:helper.index("\ndef ")]
    assert "st_ino" in helper and helper.count("os.replace(") == 1, (
        "REPAIRED: the shared publication path must carry the staged-inode identity check and "
        "exactly one replace — it is the only place a refusal may replace the canonical name")
    assert fallback.count("os.replace(") == 0, (
        "REPAIRED: a refusal branch replaces the canonical name directly instead of through the "
        "shared path — the sibling-branch defect, back again")


# =============================================================================================
# GROUP 30 — the twenty-first round. The gate refuted a claim I made in round nineteen's own
# commit message, and it was right.
#
# I wrote that withholding restoration from a non-empty directory "confines any widening to
# something with nothing in it". It does not, for two reasons the gate measured:
#
#   a. `_makedirs_owner_only` caught FileExistsError, executed `pass` under a comment reading
#      "not ours to re-mode", and then opened and chmod'd the directory anyway. The comment did
#      not control execution. A competing mkdir left an operator's populated 0500 directory, and
#      the remaining code took it to 0700.
#
#   b. `_looks_freshly_created` fell back to `st_nlink <= 2` when it could not list the
#      directory. Measured here: a populated directory containing a regular file has st_nlink 2,
#      exactly like an empty one, because regular files do not add child-directory links. The
#      fallback cannot return the right answer — it is a check that cannot fail, applied to
#      authorize widening.
#
# The rule that replaces it: a failed inspection never authorizes widening, and only a component
# this call actually CREATED is re-moded.
# =============================================================================================


def test_a_populated_directory_is_never_widened_by_the_ancestor_helper(tmp_path: Path) -> None:
    """REPAIRED: losing the creation race must not hand us the right to re-mode what is there."""
    if os.geteuid() == 0:
        pytest.skip("root ignores mode bits; this needs an unprivileged writer")
    driver = make_tool(tmp_path)
    module = import_driver(driver, "eexist_no_widen")
    target = tmp_path / "absent" / "staging"

    real_mkdir = module.os.mkdir
    raced: list[str] = []

    def mkdir_losing_the_race(name, mode=0o777, *args, **kwargs):
        # Somebody else creates it first, populates it, and sets a mode of their own. Our mkdir
        # then raises FileExistsError — the branch whose comment claims it leaves things alone.
        if os.path.basename(str(name)) == "staging" and not raced:
            raced.append(str(name))
            dir_fd = kwargs.get("dir_fd")
            real_mkdir(name, 0o700, *args, **kwargs)
            opened = os.open(str(name), os.O_RDONLY | os.O_DIRECTORY, dir_fd=dir_fd)
            try:
                fd = os.open("operator_data.txt", os.O_CREAT | os.O_WRONLY, 0o600, dir_fd=opened)
                os.close(fd)
                os.chmod("operator_data.txt", 0o600, dir_fd=opened)
            finally:
                os.close(opened)
            os.chmod(str(name), 0o500, dir_fd=dir_fd)
            raise FileExistsError(errno.EEXIST, "somebody got there first")
        return real_mkdir(name, mode, *args, **kwargs)

    module.os.mkdir = mkdir_losing_the_race
    try:
        module._makedirs_owner_only(str(target))
    finally:
        module.os.mkdir = real_mkdir

    assert raced, "CONTROL: the race never fired, so nothing about FileExistsError was measured"
    mode = stat.S_IMODE(target.stat().st_mode)
    try:
        assert mode == 0o500, (
            f"REPAIRED: a directory this call did NOT create, already holding somebody's data, "
            f"was re-moded from 0500 to {mode:04o}. The FileExistsError branch said it was not "
            "ours to re-mode and then re-moded it anyway")
    finally:
        target.chmod(0o700)


def test_a_pre_existing_directory_is_never_widened_however_it_looks(tmp_path: Path) -> None:
    """REPAIRED: restoration follows CREATION, so what a directory looks like cannot authorize it.

    Round nineteen asked "does it look empty?" and answered with st_nlink when it could not list.
    Measured on this filesystem, a directory holding a regular file has st_nlink 2 exactly like an
    empty one, so the probe authorized the widening it was added to prevent. The question was
    wrong, not just its answer: what matters is whether THIS call created the directory, and a
    FileExistsError means it did not.
    """
    if os.geteuid() == 0:
        pytest.skip("root ignores mode bits; this needs an unprivileged writer")
    driver = make_tool(tmp_path)
    module = import_driver(driver, "preexisting_not_widened")
    staging = tmp_path / "staging"
    reports = staging / "_reports"
    reports.mkdir(parents=True)
    (reports / "operator_data.txt").write_text("OPERATOR DATA\n", encoding="utf-8")

    # CONTROL: the premise the refuted probe rested on, asserted rather than assumed.
    assert reports.stat().st_nlink <= 2, (
        "CONTROL: this filesystem gives a populated directory more than 2 links, so the link-count "
        "confusion this arm is about is not reachable here")

    reports.chmod(0o500)                   # readable and searchable, NOT writable
    try:
        try:
            module.write_report(str(staging), [])
        except Exception:
            pass                           # refusing an unusable directory is correct; widening is not
        mode = stat.S_IMODE(reports.stat().st_mode)
    finally:
        reports.chmod(0o700)

    assert mode == 0o500, (
        f"REPAIRED: a pre-existing directory holding somebody's data went from 0500 to {mode:04o}. "
        "This call did not create it, so its owner bits were never this tool's to restore")
    assert (reports / "operator_data.txt").read_text(encoding="utf-8") == "OPERATOR DATA\n", (
        "CONTROL: the operator's file must be untouched")


# =============================================================================================
# GROUP 31 — the twenty-second round. Two writers that did not agree about the same path, and
# round fourteen's availability case applied one level too shallow.
#
#   #1 O_NOFOLLOW APPLIES TO THE TRAILING COMPONENT ONLY. open(2) is explicit: "Symbolic links in
#      earlier components of the pathname will still be followed." write_report avoids that by
#      opening `staging` itself O_NOFOLLOW and working from that descriptor. The refusal writer
#      dropped the descriptor and re-walked the path, so a symlinked STAGING that write_report had
#      just refused was followed by the handler that runs on its refusal — and the report inside
#      the symlink's target was replaced. Split-brain between the two writers: anything
#      write_report refuses by holding a descriptor, the refusal writer will still name.
#
#   #2 The scan ROOT is opened O_RDONLY with no O_PATH fallback, so a 0300 staging directory —
#      write and search, no read — cannot publish at all, while a 0300 `_reports` publishes fine.
#      _open_dir_nofollow exists for exactly this case and was wired to the child, not the root.
# =============================================================================================


def test_the_refusal_writer_does_not_follow_a_staging_symlink_the_publisher_refused(
    tmp_path: Path
) -> None:
    """REPAIRED: both writers resolve the scan root the same way, or they disagree about safety."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "refusal_staging_symlink")
    real_root = tmp_path / "elsewhere"
    (real_root / "_reports").mkdir(parents=True)
    victim = real_root / "_reports" / "scan_report.txt"
    victim.write_text("scan_gate: CLEAN\n", encoding="utf-8")
    staging = tmp_path / "staging_link"
    staging.symlink_to(real_root)

    with pytest.raises(Exception) as caught:
        module.write_report(str(staging), NEW_HIT)
    # CONTROL: the publisher really does refuse this state — that is the premise of the finding.
    assert "report-path-unsafe" in str(caught.value), (
        f"CONTROL: expected the publisher to refuse a symlinked scan root, got {caught.value!r}")

    module._write_refusal_report(str(staging), caught.value)

    assert victim.read_text(encoding="utf-8") == "scan_gate: CLEAN\n", (
        "REPAIRED: the refusal writer followed a symlinked scan root that write_report had just "
        "refused, and replaced the report inside its target. O_NOFOLLOW covers the trailing "
        "component only; the earlier components were re-walked")


def test_a_write_and_search_scan_root_can_still_publish(tmp_path: Path) -> None:
    """REPAIRED: round fourteen's availability case, applied to the scan root and not just below it.

    A 0300 directory grants create and traverse but not read, so an O_RDONLY open of it fails. The
    O_PATH fallback exists for precisely that and was wired to `_reports` while the scan root kept
    the plain open.
    """
    if os.geteuid() == 0:
        pytest.skip("root ignores mode bits; this needs an unprivileged writer")
    driver = make_tool(tmp_path)
    module = import_driver(driver, "wx_scan_root")
    staging = tmp_path / "staging"
    staging.mkdir()
    staging.chmod(0o300)
    try:
        # CONTROL: the owner really can create in this directory, so a refusal is the scanner's
        # doing and not the filesystem's.
        probe = os.open(str(staging / ".control_probe"), os.O_CREAT | os.O_WRONLY, 0o600)
        os.close(probe)
        os.unlink(str(staging / ".control_probe"))
    except OSError:
        staging.chmod(0o700)
        pytest.skip("this filesystem does not allow creating in a 0300 directory")

    try:
        module.write_report(str(staging), NEW_HIT)
        published = (staging / REPORT_REL).exists()
        mode = stat.S_IMODE((staging / REPORT_REL).stat().st_mode) if published else None
    finally:
        staging.chmod(0o700)
        for child in staging.rglob("*"):
            try:
                child.chmod(0o700 if child.is_dir() else 0o600)
            except OSError:
                pass

    assert published, (
        "REPAIRED: a 0300 scan root was refused although its owner can create there. The O_PATH "
        "fallback was wired to the report directory and not to the root above it")
    assert mode == 0o600, f"and the report must still land under the one rule, not {mode:04o}"


def test_quarantine_does_not_link_through_a_symlink(tmp_path: Path) -> None:
    """REPAIRED: the two evidence paths agree. Preservation passes follow_symlinks=False; the
    quarantine link did not, so the two halves of the same guarantee disagreed."""
    source = SCANNER.read_text(encoding="utf-8")
    quarantine = source[source.index("def _quarantine_unpublished"):]
    quarantine = quarantine[:quarantine.index("\ndef ")]
    # RESCOPED IN ROUND THIRTY. This arm asserted `follow_symlinks=False` on the quarantine link
    # so that a staged NAME swapped for a symlink could not link its target into the evidence
    # store. Quarantine no longer links by name at all: it links the HELD INODE through the
    # descriptor directory, which has no source name to be a symlink. The property survives in a
    # stronger form — the link cannot attach anything the scanner does not hold.
    q = source[source.index("def _quarantine_unpublished"):]
    q = q[:q.index("\ndef ")]
    assert "_link_held_inode(" in q and "os.link(tmp_name" not in q, (
        "REPAIRED: quarantine links a NAME again. A staged name swapped for a symlink would link "
        "its target into the evidence store; linking the held inode makes that impossible")
    h = source[source.index("def _link_held_inode"):]
    h = h[:h.index("\ndef ")]
    assert "_PROC_FD_DIR" in h and "os.link(" in h and "follow_symlinks=True" in h, (
        "CONTROL: the descriptor-bound helper must link through the descriptor directory, which "
        "requires following that one symlink — the proc entry — and nothing else")



class _RefusalWhoseStrBlocks(Exception):
    """A refusal that never finishes rendering. Catching exceptions cannot interrupt it."""

    def __init__(self):
        super().__init__("blocking-refusal")
        import threading
        self.entered = threading.Event()
        self._never_set = threading.Event()

    def __str__(self):
        self.entered.set()
        self._never_set.wait()             # unset, with no setter anywhere
        return "unreachable"


def test_a_refusal_that_never_finishes_rendering_does_not_hang_the_writer(
    tmp_path: Path
) -> None:
    """REPAIRED: the refusal path renders no arbitrary object, so it cannot wait on one."""
    import threading, time

    driver = make_tool(tmp_path)
    module = import_driver(driver, "refusal_str_blocks")
    staging = tmp_path / "staging"
    reports = staging / "_reports"
    reports.mkdir(parents=True)
    rp = reports / "scan_report.txt"
    rp.write_text("scan_gate: CLEAN\n", encoding="utf-8")

    refusal = _RefusalWhoseStrBlocks()
    done = threading.Event()

    def run():
        try:
            module._write_refusal_report(str(staging), refusal)
        finally:
            done.set()

    threading.Thread(target=run, daemon=True).start()
    finished = done.wait(timeout=5.0)

    assert finished, (
        "REPAIRED: the refusal writer waited on a __str__ that never returns. Wrapping str() in "
        "`except Exception` cannot interrupt a callback that does not raise — the fix is to stop "
        "rendering arbitrary objects in this path")
    assert not refusal.entered.is_set(), (
        "the writer still called __str__ on the refusal object. A validated reason code carried "
        "from the failure needs no rendering at all")
    assert rp.read_text(encoding="utf-8").startswith("scan_gate: REFUSED"), (
        "and it must still publish a refusal, with a class, rather than skipping the publish")


def test_a_retained_report_whose_policy_failed_is_not_presented_as_compliant(
    tmp_path: Path
) -> None:
    """REPAIRED: bytes are preserved; a reserved name is not handed to a non-compliant file.

    The reserved quarantine names mean "retained evidence, published under the report policy". A
    file whose ACL strip was denied has not had that policy installed, so it does not get one of
    those names — its bytes stay at the staged name, which promises nothing.
    """
    driver = make_tool(tmp_path)
    module = import_driver(driver, "retained_policy_failed")
    staging = tmp_path / "staging"
    reports = staging / "_reports"
    reports.mkdir(parents=True)
    try:
        os.mkfifo(str(reports / "scan_report.txt"))
    except (OSError, AttributeError):
        pytest.skip("this platform cannot create a FIFO; the retention path is not reachable here")

    denied: list[str] = []

    def removexattr_denied(path, *args, **kwargs):
        denied.append(str(path))
        raise PermissionError(errno.EPERM, "strip denied (injected)")

    real_removexattr = module.os.removexattr
    module.os.removexattr = removexattr_denied
    try:
        with pytest.raises(Exception):
            module.write_report(str(staging), NEW_HIT)
    finally:
        module.os.removexattr = real_removexattr

    if not denied:
        pytest.skip("this build attempted no ACL strip; nothing was made to fail")

    assert _findings_anywhere(reports, "docs/example.md:12"), (
        "CONTROL: the bytes must survive regardless — preserving evidence outranks labelling it")
    reserved = [p.name for p in reports.iterdir()
                if p.name.startswith("scan_report.unpublished")
                and "docs/example.md:12" in p.read_text(encoding="utf-8")]
    assert not reserved, (
        f"REPAIRED: findings whose access policy could not be installed were linked into the "
        f"reserved name(s) {reserved} and reported as retained. A reserved name says the report "
        "policy is on the file; here it was denied and swallowed")


def test_the_refusal_docstring_does_not_describe_a_guard_it_no_longer_has(tmp_path: Path) -> None:
    """REPAIRED: the docstring said it catches only OSError/UnicodeError and lets a ValueError
    from __str__ propagate. Round eighteen changed both and left the paragraph standing."""
    source = SCANNER.read_text(encoding="utf-8")
    doc = source[source.index("def _write_refusal_report"):]
    doc = doc[:doc.index('"""', doc.index('"""') + 3)]
    # Asserted on the CLAIM, not on the token. The corrected paragraph legitimately names
    # ValueError while recording what it used to say, and an arm keyed on the bare word would
    # forbid the correction from explaining itself.
    assert "still propagates: a refusal whose __str__ raises" not in doc, (
        "the refusal writer's docstring still claims an unexpected type propagates and replaces "
        "the refusal being reported; the guard became `except Exception` two rounds ago")
    assert "scoped to the errors this can expect" not in doc, (
        "and it still describes an OSError/UnicodeError-only guard that the code no longer has")


# =============================================================================================
# GROUP 33 — the twenty-fourth round. The same defect, in a second helper, two rounds later.
#
# Round nineteen fixed `_preserve_superseded` treating an lstat error as "the file is absent",
# because failing to INSPECT something is not evidence about what it is. Round twenty-three then
# introduced `_staged_holds_evidence`, which converts an fstat error into False — and its caller
# reads False as "there is nothing here worth keeping" and UNLINKS a stage holding real findings.
#
# The gate's phrase for it is the one to keep: unknown metadata authorizes deletion. One injected
# EIO on the held descriptor, after an ACL-strip denial has already sent the staged findings down
# the retention path, and the evidence is gone — measured finding_copies_after_refusal=0 against
# a no-injection control that retained them.
#
# The rule, stated once so it is not rewritten a third time: a question this code cannot answer
# never authorizes destruction. `hits` being non-empty and the body having been written are facts
# already in hand; an fstat that fails changes neither.
# =============================================================================================


def test_an_unreadable_stage_is_kept_rather_than_deleted(tmp_path: Path) -> None:
    """REPAIRED: a failed size check must not be read as 'nothing worth keeping'."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "stage_fstat_eio")
    staging = tmp_path / "staging"
    reports = staging / "_reports"
    reports.mkdir(parents=True)
    try:
        os.mkfifo(str(reports / "scan_report.txt"))
    except (OSError, AttributeError):
        pytest.skip("this platform cannot create a FIFO; the retention path is not reachable here")

    real_removexattr = module.os.removexattr
    real_fstat = module.os.fstat
    denied: list[str] = []
    faults: list[int] = []

    def removexattr_denied(path, *args, **kwargs):
        denied.append(str(path))
        raise PermissionError(errno.EPERM, "strip denied (injected)")

    def fstat_one_eio(fd, *args, **kwargs):
        # Exactly one failure, and only after the strip has been denied — the moment the staged
        # findings are on the retention path and their size is about to decide their fate.
        if denied and not faults:
            faults.append(errno.EIO)
            raise OSError(errno.EIO, "injected metadata failure")
        return real_fstat(fd, *args, **kwargs)

    module.os.removexattr = removexattr_denied
    module.os.fstat = fstat_one_eio
    try:
        with pytest.raises(Exception):
            module.write_report(str(staging), NEW_HIT)
    finally:
        module.os.removexattr = real_removexattr
        module.os.fstat = real_fstat

    if not denied:
        pytest.skip("this build attempted no ACL strip; the retention path was not entered")
    assert faults, (
        "CONTROL: the metadata fault never fired, so this arm measured the no-injection case and "
        "would pass against code that deletes on an unanswerable question")
    assert _findings_anywhere(reports, "docs/example.md:12"), (
        "REPAIRED: one unanswerable fstat deleted the staged findings. Unknown metadata must "
        "never authorize destruction — `hits` was non-empty and the body was already written, "
        "and a failed size check changes neither of those facts")


def test_the_no_injection_control_retains_the_stage(tmp_path: Path) -> None:
    """CONTROL for the arm above: with only the strip denied, the findings are retained."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "stage_fstat_control")
    staging = tmp_path / "staging"
    reports = staging / "_reports"
    reports.mkdir(parents=True)
    try:
        os.mkfifo(str(reports / "scan_report.txt"))
    except (OSError, AttributeError):
        pytest.skip("this platform cannot create a FIFO")

    real_removexattr = module.os.removexattr
    denied: list[str] = []

    def removexattr_denied(path, *args, **kwargs):
        denied.append(str(path))
        raise PermissionError(errno.EPERM, "strip denied (injected)")

    module.os.removexattr = removexattr_denied
    try:
        with pytest.raises(Exception):
            module.write_report(str(staging), NEW_HIT)
    finally:
        module.os.removexattr = real_removexattr

    if not denied:
        pytest.skip("this build attempted no ACL strip")
    assert _findings_anywhere(reports, "docs/example.md:12"), (
        "CONTROL: with nothing but the strip denied the findings must survive; if this fails the "
        "fixture is wrong and the injected arm proves nothing")


# =============================================================================================
# GROUP 34 — the twenty-fifth round. A write that fails halfway, and a cleanup that removes what
# it managed to write.
#
# `_stage_report` unlinks its own staged file on any write error. That cleanup predates every
# retention rule this file has since grown: the caller never receives the descriptor, so the
# quarantine path added in round seventeen and the retain-the-stage rule added in round nineteen
# cannot see those bytes at all. A findings report interrupted partway through writing is deleted
# by the function that wrote it.
#
# THE HISTORY IS THE POINT. The cold leg traced this statically in round nineteen and said so —
# "examine _stage_report's own write-error cleanup separately... that write-error case was
# statically traced, not fault-injected here". It was a static observation with no reproduction
# attached, so it read as lower priority than the arms that came with measurements, and I did not
# follow it up. Five rounds later the gate fault-injected it: an EFBIG partway through the body,
# 128 bytes on disk, removed, finding_copies=0.
#
# A traced defect with no reproduction is still a defect. It is only cheaper to ignore.
# =============================================================================================


def test_a_partly_written_findings_report_is_not_deleted_by_its_own_writer(
    tmp_path: Path
) -> None:
    """REPAIRED: bytes that reached the disk survive the failure that stopped the rest."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "partial_write_kept")
    staging = tmp_path / "staging"
    reports = staging / "_reports"
    reports.mkdir(parents=True)

    real_fdopen = module.os.fdopen
    wrote: list[int] = []

    class _HalfWriter:
        """Writes part of the body, then fails the way a full disk or an RLIMIT_FSIZE does."""

        def __init__(self, wrapped):
            self._wrapped = wrapped

        def __enter__(self):
            self._wrapped.__enter__()
            return self

        def write(self, data):
            half = data[: max(1, len(data) // 2)]
            n = self._wrapped.write(half)
            self._wrapped.flush()
            wrote.append(len(half))
            raise OSError(errno.EFBIG, "injected write failure")

        def __exit__(self, *exc):
            return self._wrapped.__exit__(*exc)

    def half_writing_fdopen(*args, **kwargs):
        return _HalfWriter(real_fdopen(*args, **kwargs))

    module.os.fdopen = half_writing_fdopen
    try:
        with pytest.raises(Exception):
            module.write_report(str(staging), NEW_HIT)
    finally:
        module.os.fdopen = real_fdopen

    assert wrote and wrote[0] > 0, (
        "CONTROL: no bytes reached the disk before the injected failure, so this arm measured "
        "nothing about partial content")
    leftovers = [p for p in reports.iterdir()
                 if p.is_file() and p.name.startswith(".scan_report_")]
    assert leftovers, (
        "REPAIRED: the staged file was unlinked by the writer that had just put findings bytes "
        "in it. The caller never receives that descriptor, so no retention rule added since can "
        "see those bytes — the only chance to keep them is here")
    body = leftovers[0].read_text(encoding="utf-8")
    assert body, "and the retained stage must actually hold the bytes that were written"


def test_a_partly_written_status_line_is_still_cleaned_up(tmp_path: Path) -> None:
    """CONTROL-SHAPED: the asymmetry holds here too — a status line is not evidence.

    Keeping partial findings is worth a leftover file. Keeping half of `scan_gate: CLEAN` is not,
    and a stray fragment of a status line beside a refusal is the confusion every other rule in
    this file exists to prevent.
    """
    driver = make_tool(tmp_path)
    module = import_driver(driver, "partial_clean_removed")
    staging = tmp_path / "staging"
    reports = staging / "_reports"
    reports.mkdir(parents=True)

    real_fdopen = module.os.fdopen
    wrote: list[int] = []

    class _HalfWriter:
        def __init__(self, wrapped):
            self._wrapped = wrapped

        def __enter__(self):
            self._wrapped.__enter__()
            return self

        def write(self, data):
            half = data[: max(1, len(data) // 2)]
            self._wrapped.write(half)
            self._wrapped.flush()
            wrote.append(len(half))
            raise OSError(errno.EFBIG, "injected write failure")

        def __exit__(self, *exc):
            return self._wrapped.__exit__(*exc)

    module.os.fdopen = lambda *a, **k: _HalfWriter(real_fdopen(*a, **k))
    try:
        with pytest.raises(Exception):
            module.write_report(str(staging), [])
    finally:
        module.os.fdopen = real_fdopen

    assert wrote, "CONTROL: nothing was written, so the cleanup decision was never reached"
    leftovers = [p.name for p in reports.iterdir()
                 if p.is_file() and p.name.startswith(".scan_report_")]
    assert not leftovers, (
        f"a partial status line was left behind as {leftovers}; only evidence is worth keeping")


# =============================================================================================
# GROUP 35 — the twenty-sixth round. The rule quarantine learned, applied to preservation.
#
# Round twenty-three established it for the quarantine path: a RESERVED name means "retained
# evidence, carrying the report's access policy", so a file whose ACL strip was denied does not
# get one. Preservation was never brought in line. It links the old findings into
# scan_report.superseded.txt BEFORE installing the retained inode's policy, `_narrow_kept_copy`
# swallows an ACL-removal error and returns nothing, and the successful link then authorizes
# REPLACING the canonical report — while the copy standing in for it still carries an ACL.
#
# The fix costs nothing, because a preserved copy is a second NAME for the report's own inode. If
# the policy cannot be installed, the link is removed and the replacement is declined: the bytes
# are untouched at the canonical name, and no reserved name claims a compliance that was denied.
# That is the same shape as the occupied-slots case — publication is denied, evidence never is.
# =============================================================================================


def test_a_preserved_copy_whose_policy_failed_does_not_authorize_replacement(
    tmp_path: Path
) -> None:
    """REPAIRED: a reserved name is never given to a copy whose policy was denied."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "preserve_policy_denied")
    reports = tmp_path / "_reports"
    reports.mkdir()
    rp = reports / "scan_report.txt"
    rp.write_text("aws\tkey\tassignment\tdocs/kept.md:9\n", encoding="utf-8")

    real_removexattr = module.os.removexattr
    denied: list[str] = []

    def removexattr_denied(path, *args, **kwargs):
        denied.append(str(path))
        raise PermissionError(errno.EPERM, "strip denied (injected)")

    module.os.removexattr = removexattr_denied
    try:
        authorized = preserve_superseded(module, reports)
    finally:
        module.os.removexattr = real_removexattr

    if not denied:
        pytest.skip("this build attempted no ACL strip; nothing was made to fail")

    assert authorized is False, (
        "REPAIRED: preservation reported success while the retained copy's access policy had "
        "been denied, and that answer is what authorizes replacing the canonical report")
    assert rp.read_text(encoding="utf-8") == "aws\tkey\tassignment\tdocs/kept.md:9\n", (
        "CONTROL: the findings themselves must be untouched — a declined replacement leaves them "
        "standing at the canonical name, which is where they already were")
    # A RESERVED NAME MAY REMAIN, and it must be a second name for the report rather than a copy
    # standing in for one. Removing it is what this round's first shape did, and an arm below
    # reproduces the evidence loss that caused. What must NOT happen is the replacement.
    for p in reports.iterdir():
        if p.name.startswith("scan_report.superseded"):
            assert p.stat().st_ino == rp.stat().st_ino, (
                f"REPAIRED: {p.name} is a reserved name for a DIFFERENT inode than the report "
                "that was left standing — it is holding something it did not preserve")


def test_the_no_injection_control_authorizes_replacement(tmp_path: Path) -> None:
    """CONTROL: with nothing denied, preservation succeeds and answers True."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "preserve_policy_ok")
    reports = tmp_path / "_reports"
    reports.mkdir()
    rp = reports / "scan_report.txt"
    rp.write_text("aws\tkey\tassignment\tdocs/kept.md:9\n", encoding="utf-8")

    assert preserve_superseded(module, reports) is True, (
        "CONTROL: the ordinary path must preserve and authorize; if this fails the fixture is "
        "wrong and the injected arm above proves nothing")
    kept = [p for p in reports.iterdir() if p.name.startswith("scan_report.superseded")]
    assert kept and "docs/kept.md:9" in kept[0].read_text(encoding="utf-8"), (
        "CONTROL: and the preserved copy must actually hold the findings")


def test_the_forfeit_must_not_remove_the_last_name_for_the_findings(tmp_path: Path) -> None:
    """REPAIRED: giving back a reserved name must never be the act that destroys the evidence.

    Round twenty-six's first shape unlinked the reserved name when the policy was denied, and
    justified it in a comment: the link is a SECOND name for the report's own inode, so removing
    it removes no bytes. That sentence is true only while the canonical name still reaches that
    inode, and this module exists because the tree is hostile and a name can stop reaching an
    inode at any moment. The review leg aimed at exactly that sentence before its provider killed
    the run — it was testing "loss of that canonical link" — and the attack lands: unlink the
    report between the link and the forfeit, and the forfeit removes the last name for the
    findings. There is no race-free way to ask POSIX to remove a name ONLY IF it is not the last
    one, so the answer is not a better check. It is to not remove it: the replacement is declined,
    so the inode keeps standing at the canonical name where it already was, and an ACL on it is an
    ACL already on that report, not a channel this call opened.
    """
    driver = make_tool(tmp_path)
    module = import_driver(driver, "forfeit_not_last_name")
    reports = tmp_path / "_reports"
    reports.mkdir()
    rp = reports / "scan_report.txt"
    rp.write_text("aws\tkey\tassignment\tdocs/last.md:4\n", encoding="utf-8")

    real_removexattr = module.os.removexattr
    fired: list[str] = []

    def removexattr_that_also_takes_the_report(path, *args, **kwargs):
        # THE INTERLEAVING, injected where it actually occurs: this runs inside the narrowing,
        # which is called AFTER the link and BEFORE any decision about it. The canonical name
        # stops reaching the findings inode exactly there.
        if not fired:
            fired.append(str(path))
            try:
                os.unlink(rp)
            except OSError:
                pass
        raise PermissionError(errno.EPERM, "strip denied (injected)")

    module.os.removexattr = removexattr_that_also_takes_the_report
    try:
        authorized = preserve_superseded(module, reports)
    finally:
        module.os.removexattr = real_removexattr

    if not fired:
        pytest.skip("this build attempted no ACL strip; the interleaving never ran")

    assert authorized is False, (
        "CONTROL: a denied policy must not authorize the replacement, whatever else happened")
    survivors = [p for p in reports.iterdir()
                 if p.is_file() and "aws\tkey" in p.read_text(encoding="utf-8", errors="replace")]
    assert survivors, (
        "REPAIRED: the findings are gone. The canonical name was taken between the link and the "
        "forfeit, which made the reserved name the ONLY name for the inode — and the forfeit "
        "removed it. Declining to keep a name must never be the act that destroys the evidence")


# =============================================================================================
# GROUP 36 — the twenty-seventh round. Two destructive operations that acted on a name whose
# identity was established at an earlier instant.
#
# The gate returned five findings of one class: in a report directory another same-UID process can
# write to, every check-then-destroy is a race. Three of them cannot be closed by any check — the
# gate said so itself, and they are documented as a limit rather than papered over. These two can,
# because neither destructive operation was necessary in the form it had.
#
#   The refusal writer unlinked the canonical name when an lstat said symlink. It is the ONLY
#   reason that name is ever missing (the publish replaces it atomically either way), and a rename
#   landing a real findings file over the symlink between the lstat and the unlink made this
#   writer delete the findings. The unlink is removed, not guarded: os.replace does not follow a
#   symlink at the destination, so the policy installer's refusal simply routes the publish
#   through the fallback that was already there.
#
#   A successful publish swept every reserved name, on the stated assumption that anything there
#   belongs to an EARLIER generation. A concurrent writer that quarantines its findings DURING
#   this run leaves a file that assumption misreads, and the sweep deleted its only name. The
#   sweep now skips anything created after this run staged its own report. That is not a race-free
#   ownership proof and does not claim to be; it removes the case that needs no adversary at all.
# =============================================================================================


def test_the_refusal_writer_does_not_unlink_a_regular_report(tmp_path: Path) -> None:
    """REPAIRED: a stale symlink verdict must not authorize deleting a findings file."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "refusal_symlink_unlink")
    reports = tmp_path / "_reports"
    reports.mkdir()
    rp = reports / "scan_report.txt"
    rp.write_text("aws\tkey\tassignment\tdocs/raced.md:2\n", encoding="utf-8")

    real_lstat = module.os.lstat
    lied: list[str] = []

    class _SaysSymlink:
        """Every field of the real stat, with the mode reporting a symlink."""
        def __init__(self, real):
            self._real = real
        def __getattr__(self, name):
            return getattr(self._real, name)
        @property
        def st_mode(self):
            return (self._real.st_mode & ~stat.S_IFMT(self._real.st_mode)) | stat.S_IFLNK

    def lstat_that_lies_once(path, *args, **kwargs):
        real = real_lstat(path, *args, **kwargs)
        # KEYED ON THE CALLER, not on call ORDER. The first version keyed on "the first lstat of
        # this name" and was consumed by an existence probe earlier in the same function, so the
        # symlink check saw the truth and the arm passed while measuring nothing. That probe
        # discards the mode it reads, so lying to it changes no behaviour.
        if (isinstance(path, str) and path.endswith("scan_report.txt")
                and sys._getframe(1).f_code.co_name == "_publish_refusal"):
            # THE INTERLEAVING: the verdict is formed on a symlink that a concurrent rename has
            # already replaced with a real findings file by the time it is acted on.
            lied.append(path)
            return _SaysSymlink(real)
        return real

    module.os.lstat = lstat_that_lies_once
    try:
        module._write_refusal_report(str(tmp_path), module.ScanRefused("report-path-unsafe 'x'"))
    finally:
        module.os.lstat = real_lstat

    if not lied:
        assert False, "CONTROL: the injection never fired, so this arm measured nothing"
    assert _findings_anywhere(reports, "docs/raced.md:2"), (
        "REPAIRED: the refusal writer deleted a regular findings file because an lstat taken "
        "earlier had said the name was a symlink. The publish replaces that name atomically "
        "whether or not it is a symlink, so the unlink bought nothing and cost the findings")


def test_the_sweep_does_not_take_a_file_created_after_this_run_staged_its_own(
    tmp_path: Path
) -> None:
    """REPAIRED: 'anything reserved is an earlier generation' is false while others are writing."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "sweep_newer_quarantine")
    staging = tmp_path / "tree"
    reports = staging / "_reports"
    reports.mkdir(parents=True)

    real_replace = module.os.replace
    planted: list[Path] = []

    def replace_then_another_writer_quarantines(*args, **kwargs):
        result = real_replace(*args, **kwargs)
        if not planted:
            # A CONCURRENT WRITER that could not publish retains its findings under the
            # quarantine name — after this run staged its report, and before this run sweeps.
            q = reports / "scan_report.unpublished.txt"
            q.write_text("gcp\tkey\tassignment\tsrc/other.py:11\n", encoding="utf-8")
            planted.append(q)
        return result

    module.os.replace = replace_then_another_writer_quarantines
    try:
        module.write_report(str(staging), [("docs/mine.md", 3, "aws", "key", "assignment")])
    finally:
        module.os.replace = real_replace

    if not planted:
        assert False, "CONTROL: no publish happened, so the sweep was never reached"
    assert _findings_anywhere(reports, "src/other.py:11"), (
        "REPAIRED: the sweep deleted the only name of findings a concurrent writer had just "
        "retained. A reserved name is not proof of an earlier generation while another writer "
        "is still running")
    assert (reports / "scan_report.txt").read_text(encoding="utf-8").count("docs/mine.md") == 1, (
        "CONTROL: this run must still publish its own findings normally")


def test_the_sweep_still_takes_an_older_reserved_name(tmp_path: Path) -> None:
    """CONTROL: the guard must not disable the sweep it narrows."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "sweep_still_sweeps")
    staging = tmp_path / "tree"
    reports = staging / "_reports"
    reports.mkdir(parents=True)
    stale = reports / "scan_report.superseded.txt"
    stale.write_text("aws\tkey\tassignment\tdocs/old.md:1\n", encoding="utf-8")
    os.utime(stale, (1, 1))

    module.write_report(str(staging), [("docs/mine.md", 3, "aws", "key", "assignment")])

    assert not stale.exists(), (
        "CONTROL: a reserved name older than this run's staged report is exactly what the sweep "
        "is for — if this survives, the guard disabled the sweep instead of narrowing it")


def test_staged_findings_survive_the_staged_name_being_unlinked(tmp_path: Path) -> None:
    """REPAIRED: the last descriptor must not be closed on bytes no name reaches any more.

    A cold leg reproduced this one from the other side of the module. The staged findings are on
    disk and held open; something fails; a concurrent unlink takes the staged NAME. Quarantine
    compares that name to the held inode, finds they diverge, and answers "I did not take
    custody" — correctly. The caller then reads the evidence flag, leaves the name alone (there
    is none), and closes the descriptor in its finally. That close frees the inode, and the close
    is the destruction: the bytes were readable through the descriptor directory right up to it.

    The leg also measured the repair that does NOT work: os.link on /proc/self/fd/N is ENOENT
    once the link count reaches zero, so the inode cannot be given a new name. Copying the bytes
    out through a read of that same path does work, and is what this arm requires.
    """
    driver = make_tool(tmp_path)
    module = import_driver(driver, "staged_name_unlinked")
    staging = tmp_path / "tree"
    reports = staging / "_reports"
    reports.mkdir(parents=True)

    real_install = module._install_posix_acl_policy
    took: list[str] = []

    def install_after_a_concurrent_unlink(dirfd, src_name, dst_fd, dst_name):
        # THE INTERLEAVING: the staged name is taken while the policy install is in flight, and
        # the failure that follows routes this scan into quarantine with no name to hand it.
        if not took:
            try:
                os.unlink(str(reports / dst_name))
                took.append(dst_name)
            except OSError:
                pass
        raise OSError(errno.EIO, "policy install failed (injected)")

    module._install_posix_acl_policy = install_after_a_concurrent_unlink
    try:
        with pytest.raises(BaseException):
            module.write_report(str(staging), [("docs/held.md", 7, "aws", "key", "assignment")])
    finally:
        module._install_posix_acl_policy = real_install

    if not took:
        pytest.skip("the staged name was never taken; this arm measured nothing")
    assert _findings_anywhere(reports, "docs/held.md:7"), (
        "REPAIRED: the findings this scan wrote are gone. They were still readable through the "
        "held descriptor when quarantine gave up on them, and the descriptor was then closed. "
        "A function that cannot take custody by NAME must copy the bytes out before the last "
        "reference goes")


# =============================================================================================
# GROUP 37 — the twenty-eighth round. Preservation stops asking a NAME and holds the inode.
#
# Three legs across two providers converged on one sentence, independently and with no shared
# premise: this module moved every piece of metadata work onto held descriptors in rounds
# eighteen to twenty-three, and preservation was left asking a pathname whether the policy is on
# "the inode we actually hold". A name lookup is not a hold. The cold leg wrote the prescription
# out: after the link, open the slot O_PATH, require its fstat to equal the inode that was
# preserved, and do the narrowing and the classification THROUGH that descriptor.
#
# This does not make the path race-free and the limits section does not claim it does. What it
# removes is the whole family in which the module ACTS ON THE WRONG INODE — narrows a planted
# file and reports the policy installed, or reads a planted status line and releases findings it
# never preserved. Those are the sequences where a name swap converts this scanner into the thing
# that destroys the evidence.
# =============================================================================================


def test_a_slot_swapped_after_the_link_does_not_release_the_findings(tmp_path: Path) -> None:
    _A_MARK = "docs/swapped.md:5"
    """REPAIRED: classification must describe the inode that was preserved, not the name."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "slot_swapped_status_line")
    reports = tmp_path / "_reports"
    reports.mkdir()
    rp = reports / "scan_report.txt"
    rp.write_text("aws\tkey\tassignment\tdocs/swapped.md:5\n", encoding="utf-8")

    real_link = module.os.link
    swapped: list[str] = []

    def link_then_a_writer_replaces_the_slot(src, dst, *args, **kwargs):
        result = real_link(src, dst, *args, **kwargs)
        if not swapped:
            swapped.append(dst)
            # THE SWAP: the slot name now holds somebody else's STATUS LINE. Read by name, that
            # says "this is not findings, release the slot and authorize the replacement" — about
            # a file this scan never preserved.
            plant = reports / (str(dst) + ".plant")
            plant.write_text("scan_gate: CLEAN\n", encoding="utf-8")
            os.replace(plant, reports / str(dst))
        return result

    module.os.link = link_then_a_writer_replaces_the_slot
    try:
        authorized = preserve_superseded(module, reports)
    finally:
        module.os.link = real_link

    if not swapped:
        pytest.skip("no link was made, so no slot could be swapped")
    assert _findings_anywhere(reports, "docs/swapped.md:5"), (
        "REPAIRED: the findings are gone. A planted status line at the slot name was classified "
        "as THIS scan's preserved copy, the slot was released, and the replacement was authorized "
        "over the only remaining name for the real findings")
    _a_under_a_slot = [p.name for p in reports.iterdir() if p.name.startswith("scan_report.superseded")
                       and _A_MARK in p.read_text(encoding="utf-8", errors="replace")]
    # Since round forty-six the link is made from the held inode, a swapped slot is detected by the
    # helper and the NEXT slot is taken, so A is preserved and the replacement is rightly authorized.
    assert authorized is False or _a_under_a_slot, (
        "REPAIRED: the replacement was authorized on the strength of a file this scan never "
        "preserved. The slot no longer held the inode that was linked into it")


def test_a_slot_swapped_after_the_link_is_not_reported_as_narrowed(tmp_path: Path) -> None:
    _A_MARK = "docs/narrowed.md:8"
    """REPAIRED: narrowing a planted file must not count as installing policy on our inode."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "slot_swapped_narrow")
    reports = tmp_path / "_reports"
    reports.mkdir()
    rp = reports / "scan_report.txt"
    rp.write_text("aws\tkey\tassignment\tdocs/narrowed.md:8\n", encoding="utf-8")

    real_link = module.os.link
    swapped: list[str] = []

    def link_then_swap_in_other_findings(src, dst, *args, **kwargs):
        result = real_link(src, dst, *args, **kwargs)
        if not swapped:
            swapped.append(dst)
            plant = reports / (str(dst) + ".plant")
            plant.write_text("gcp\tkey\tassignment\tsrc/theirs.py:1\n", encoding="utf-8")
            os.replace(plant, reports / str(dst))
        return result

    module.os.link = link_then_swap_in_other_findings
    try:
        authorized = preserve_superseded(module, reports)
    finally:
        module.os.link = real_link

    if not swapped:
        pytest.skip("no link was made, so no slot could be swapped")
    _a_under_a_slot = [p.name for p in reports.iterdir() if p.name.startswith("scan_report.superseded")
                       and _A_MARK in p.read_text(encoding="utf-8", errors="replace")]
    # Since round forty-six the link is made from the held inode, a swapped slot is detected by the
    # helper and the NEXT slot is taken, so A is preserved and the replacement is rightly authorized.
    assert authorized is False or _a_under_a_slot, (
        "REPAIRED: the policy was installed on a planted file and reported as installed on the "
        "preserved inode, which then authorized replacing the report")
    assert _findings_anywhere(reports, "docs/narrowed.md:8"), (
        "CONTROL: and the findings must still be reachable")


def test_the_ordinary_preservation_path_is_unchanged(tmp_path: Path) -> None:
    """CONTROL: with nobody swapping anything, preservation still preserves and authorizes."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "held_copy_ordinary")
    reports = tmp_path / "_reports"
    reports.mkdir()
    rp = reports / "scan_report.txt"
    rp.write_text("aws\tkey\tassignment\tdocs/plain.md:2\n", encoding="utf-8")
    rp.chmod(0o644)

    assert preserve_superseded(module, reports) is True, (
        "CONTROL: the undisturbed path must still authorize the replacement — if this fails the "
        "anchoring broke preservation rather than anchoring it")
    kept = [p for p in reports.iterdir() if p.name.startswith("scan_report.superseded")]
    assert kept, "CONTROL: and the findings must actually be preserved"
    assert "docs/plain.md:2" in kept[0].read_text(encoding="utf-8")
    if os.geteuid() != 0:
        assert not stat.S_IMODE(kept[0].stat().st_mode) & 0o077, (
            "CONTROL: narrowing through the descriptor must still reach the mode")


# =============================================================================================
# GROUP 38 — the twenty-ninth round. The sibling branch, and a reserved name given out too early.
#
# Round twenty-eight anchored preservation to a held descriptor and anchored ONE of its two
# branches. The existing-slot branch — the one that runs when no new link could be made and an
# earlier call's copy is already sitting in a slot — still compared an lstat to the expected inode
# and then handed the NAME to a helper that opens it again. A leg reproduced the gap: it returned
# True after narrowing a different inode than the one it had checked. This is the same defect in a
# second place, two rounds later, which has now happened often enough in this file to be worth
# naming as a habit rather than an accident: when a fix anchors one branch, its sibling is where
# the same defect goes to live.
#
# The second arm is about a reserved name being granted before the bytes are all written. The
# copy-out path created the reserved name first and wrote into it, so a write error left a partial
# file under a name that means "retained evidence" — and the cleanup for that case is an unlink
# that is allowed to fail. The leg measured exactly that: a refused unlink left two bytes standing
# under the reserved name.
# =============================================================================================


def test_the_existing_slot_branch_narrows_the_inode_it_checked(tmp_path: Path) -> None:
    """REPAIRED: the sibling branch must hold the inode too, not re-resolve the name."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "existing_slot_identity")
    reports = tmp_path / "_reports"
    reports.mkdir()
    rp = reports / "scan_report.txt"
    rp.write_text("aws\tkey\tassignment\tdocs/mine.md:1\n", encoding="utf-8")

    # An earlier call already preserved this inode under the first slot: a real second name.
    slot = reports / "scan_report.superseded.txt"
    os.link(rp, slot)
    # Every remaining slot is occupied by a directory, so no NEW link can be made and the
    # function must take the existing-slot branch.
    for name in list(module._superseded_slot_names())[1:]:
        (reports / name).mkdir()

    real_lstat = module.os.lstat
    swapped: list[str] = []

    def lstat_then_swap_the_slot(path, *args, **kwargs):
        result = real_lstat(path, *args, **kwargs)
        if (not swapped and isinstance(path, str)
                and path.endswith("scan_report.superseded.txt")
                and sys._getframe(1).f_code.co_name == "_preserve_superseded"):
            # THE INTERLEAVING: identity is established, and the name then stops meaning it.
            swapped.append(path)
            other = reports / "planted.txt"
            other.write_text("gcp\tkey\tassignment\tsrc/theirs.py:9\n", encoding="utf-8")
            os.replace(other, slot)
        return result

    module.os.lstat = lstat_then_swap_the_slot
    try:
        authorized = preserve_superseded(module, reports)
    finally:
        module.os.lstat = real_lstat

    if not swapped:
        pytest.skip("the existing-slot branch was not reached; this arm measured nothing")
    assert authorized is False, (
        "REPAIRED: the replacement was authorized after the slot stopped holding the inode that "
        "was checked. The identity test and the narrowing were applied to two different files")


def test_a_partial_copy_never_occupies_a_reserved_name(tmp_path: Path) -> None:
    """REPAIRED: a reserved name is granted only once every byte is written."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "partial_copy_reserved")
    staging = tmp_path / "tree"
    reports = staging / "_reports"
    reports.mkdir(parents=True)

    real_write = module.os.write
    real_unlink = module.os.unlink
    broke: list[int] = []

    def write_that_fails_after_the_first_chunk(fd, data):
        if not broke:
            broke.append(fd)
            real_write(fd, data[:2])
            raise OSError(errno.EIO, "write failed mid-copy (injected)")
        return real_write(fd, data)

    def unlink_that_refuses(*args, **kwargs):
        # The cleanup is best effort by construction, so the arm makes it fail: what remains on
        # disk afterwards is the property under test, not the cleanup's own luck.
        raise PermissionError(errno.EACCES, "unlink refused (injected)")

    real_install = module._install_posix_acl_policy

    def install_after_taking_the_staged_name(dirfd, src_name, dst_fd, dst_name):
        # real_unlink, NOT os.unlink: the module-level patch below makes every unlink refuse, and
        # the first version of this arm used the patched one — so the staged name was never taken,
        # the copy-out never ran, and the arm skipped while appearing to be set up correctly.
        try:
            real_unlink(str(reports / dst_name))
        except OSError:
            pass
        raise OSError(errno.EIO, "policy install failed (injected)")

    module._install_posix_acl_policy = install_after_taking_the_staged_name
    module.os.write = write_that_fails_after_the_first_chunk
    module.os.unlink = unlink_that_refuses
    try:
        with pytest.raises(BaseException):
            module.write_report(str(staging), [("docs/held.md", 4, "aws", "key", "assignment")])
    finally:
        module.os.write = real_write
        module.os.unlink = real_unlink
        module._install_posix_acl_policy = real_install

    if not broke:
        pytest.skip("the copy-out path never wrote; this arm measured nothing")
    reserved = [p for p in reports.iterdir() if p.name.startswith("scan_report.unpublished")]
    for p in reserved:
        body = p.read_text(encoding="utf-8", errors="replace")
        assert body.endswith("\n") and "docs/held.md:4" in body, (
            f"REPAIRED: {p.name} is a reserved name holding a PARTIAL copy ({body!r}). A reserved "
            "name means retained evidence; it must not be granted until every byte is written, "
            "because the cleanup that would remove it is allowed to fail")


def test_copy_out_works_when_the_umask_stripped_owner_read(tmp_path: Path) -> None:
    """REPAIRED: the last-resort copy must not be defeated by the mode its own stage was created with.

    A cold leg found this one from the umask end. _stage_report creates at 0600 AND THAT IS
    UMASK-MASKED: at umask 0400 the staged inode is 0200, owner-write with no owner-read. The held
    descriptor is O_WRONLY. When the staged name diverges, the copy-out reopens the inode through
    the descriptor directory O_RDONLY — and that open is refused, because the inode has no read
    bit for anyone. The copy returns False, the caller closes the last reference, and the findings
    are gone. The non-diverged quarantine path already fchmods the source before it links; this
    path was added later and never did.
    """
    if os.geteuid() == 0:
        pytest.skip("root ignores mode bits; this needs an unprivileged writer")
    driver = make_tool(tmp_path)
    module = import_driver(driver, "copyout_umask")
    staging = tmp_path / "tree"
    reports = staging / "_reports"
    reports.mkdir(parents=True)

    real_unlink = module.os.unlink
    took: list[str] = []
    real_install = module._install_posix_acl_policy

    def install_after_taking_the_staged_name(dirfd, src_name, dst_fd, dst_name):
        if not took:
            took.append(dst_name)
            try:
                real_unlink(str(reports / dst_name))
            except OSError:
                pass
        raise OSError(errno.EIO, "policy install failed (injected)")

    module._install_posix_acl_policy = install_after_taking_the_staged_name
    previous_umask = os.umask(0o400)          # strips the OWNER READ bit from the staged file
    try:
        with pytest.raises(BaseException):
            module.write_report(str(staging), [("docs/umask.md", 5, "aws", "key", "assignment")])
    finally:
        os.umask(previous_umask)
        module._install_posix_acl_policy = real_install

    if not took:
        pytest.skip("the staged name was never taken; this arm measured nothing")
    assert _findings_anywhere(reports, "docs/umask.md:5"), (
        "REPAIRED: at umask 0400 the staged findings were created without owner read, the "
        "copy-out could not reopen them for reading, and the last descriptor was closed on the "
        "only copy. The mode has to be installed on the source before it is read back")


def test_the_sweep_keeps_a_reserved_name_whose_age_cannot_be_read(tmp_path: Path) -> None:
    """REPAIRED: a question this code cannot answer must not authorize destruction.

    The age guard added one round earlier skips a reserved name that is newer than this run's own
    staging. A cold leg pointed out that its except-OSError falls THROUGH to the unlink: when the
    lstat fails, the sweep does the exact thing the guard was added to prevent. That is the same
    inversion `_staged_holds_evidence` was rewritten to remove, still standing here.
    """
    driver = make_tool(tmp_path)
    module = import_driver(driver, "sweep_unknown_age")
    staging = tmp_path / "tree"
    reports = staging / "_reports"
    reports.mkdir(parents=True)

    real_lstat = module.os.lstat
    real_replace = module.os.replace
    planted: list[Path] = []

    def replace_then_a_concurrent_writer_quarantines(*args, **kwargs):
        result = real_replace(*args, **kwargs)
        if not planted:
            q = reports / "scan_report.unpublished.txt"
            q.write_text("gcp\tkey\tassignment\tsrc/theirs.py:3\n", encoding="utf-8")
            planted.append(q)
        return result

    def lstat_that_cannot_answer(path, *args, **kwargs):
        if (isinstance(path, str) and path.startswith("scan_report.unpublished")
                and sys._getframe(1).f_code.co_name == "write_report"):
            raise OSError(errno.EIO, "cannot stat (injected)")
        return real_lstat(path, *args, **kwargs)

    module.os.replace = replace_then_a_concurrent_writer_quarantines
    module.os.lstat = lstat_that_cannot_answer
    try:
        module.write_report(str(staging), [("docs/mine.md", 1, "aws", "key", "assignment")])
    finally:
        module.os.replace = real_replace
        module.os.lstat = real_lstat

    if not planted:
        assert False, "CONTROL: no publish happened, so the sweep was never reached"
    assert _findings_anywhere(reports, "src/theirs.py:3"), (
        "REPAIRED: the sweep could not read the reserved file's age and removed it anyway. The "
        "age check exists only to protect that file; on an unreadable answer it did the thing "
        "the check was added to prevent")


def test_quarantine_does_not_report_custody_of_a_substituted_link(tmp_path: Path) -> None:
    """REPAIRED: the identity check must bind the LINK, not merely precede it."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "quarantine_link_identity")
    staging = tmp_path / "tree"
    reports = staging / "_reports"
    reports.mkdir(parents=True)

    real_link = module.os.link
    swapped: list[str] = []
    real_install = module._install_posix_acl_policy

    def failing_install(dirfd, src_name, dst_fd, dst_name):
        raise OSError(errno.EIO, "policy install failed (injected)")

    def link_that_lands_on_something_else(src, dst, *args, **kwargs):
        # THE SUBSTITUTION: the staged name stops being our inode between the identity check and
        # the link, so the reserved name ends up describing a file this scan never staged.
        if not swapped and isinstance(dst, str) and dst.startswith("scan_report.unpublished"):
            swapped.append(dst)
            other = reports / "foreign.txt"
            other.write_text("gcp\tkey\tassignment\tsrc/foreign.py:2\n", encoding="utf-8")
            try:
                real_link(str(other), str(reports / dst))
                return None
            except OSError:
                pass
        return real_link(src, dst, *args, **kwargs)

    module._install_posix_acl_policy = failing_install
    module.os.link = link_that_lands_on_something_else
    try:
        with pytest.raises(BaseException):
            module.write_report(str(staging), [("docs/ours.md", 6, "aws", "key", "assignment")])
    finally:
        module.os.link = real_link
        module._install_posix_acl_policy = real_install

    if not swapped:
        pytest.skip("no quarantine link was attempted; this arm measured nothing")
    assert _findings_anywhere(reports, "docs/ours.md:6"), (
        "REPAIRED: quarantine answered that it had taken custody, the caller acted on that "
        "answer, and the reserved name held a file this scan never staged. Our findings are gone")


def test_a_slot_stolen_after_authorization_does_not_get_the_report_replaced(tmp_path: Path) -> None:
    """REPAIRED: the authorization must still hold at the moment it is spent.

    Preservation closes its descriptor and answers True. The caller then stages a refusal body and
    installs its policy — many syscalls — and only then replaces the canonical name. A cold leg
    pointed out that the answer is spent long after it was computed, and that stealing the slot in
    that window leaves the findings with no name at all. It also said what would close THIS window
    without pretending to close the last instruction: check, immediately before the replace, that
    the slot still holds the inode that was preserved.
    """
    driver = make_tool(tmp_path)
    module = import_driver(driver, "slot_stolen_after_auth")
    reports = tmp_path / "_reports"
    reports.mkdir()
    rp = reports / "scan_report.txt"
    rp.write_text("aws\tkey\tassignment\tdocs/spent.md:7\n", encoding="utf-8")

    real_stage = module._stage_report
    stolen: list[str] = []

    def stage_then_steal_the_slot(dirfd, body, evidence=False):
        result = real_stage(dirfd, body, evidence=evidence)
        if not stolen:
            stolen.append("yes")
            slot = reports / "scan_report.superseded.txt"
            if slot.exists():
                plant = reports / "plant.txt"
                plant.write_text("scan_gate: CLEAN\n", encoding="utf-8")
                os.replace(plant, slot)
        return result

    module._stage_report = stage_then_steal_the_slot
    try:
        module._write_refusal_report(str(tmp_path), module.ScanRefused("report-path-unsafe 'x'"))
    finally:
        module._stage_report = real_stage

    if not stolen:
        pytest.skip("no refusal was staged; this arm measured nothing")
    assert _findings_anywhere(reports, "docs/spent.md:7"), (
        "REPAIRED: the slot was taken between the authorization and the replace, so the preserved "
        "copy stopped being our inode — and the replace then dropped the canonical name, which "
        "was the last one the findings had")


# =============================================================================================
# GROUP 39 — the thirtieth round. Five evidence-retention failures, four of them in code the
# last three rounds wrote, and one more sibling branch.
#
# The pattern this file has now shown three times gets its own sentence: WHEN A FIX ANCHORS ONE
# BRANCH, ITS SIBLING IS WHERE THE DEFECT GOES TO LIVE. Round twenty-nine put the spent-authorization
# re-check before the ordinary refusal replace and not before the fallback one. The review leg's
# structural advice — put the shared preconditions in ONE publication path — is what this round
# does, so there is no second branch left to forget.
#
# The copy-out helper, added in round twenty-seven as the last-resort rescue for an inode with no
# names, turned out to lose that inode in three ways of its own: it deleted a completed recovery
# stage when every reserved name was occupied; it claimed custody of a reserved link it never
# confirmed was the stage it wrote; and quarantine's new mismatch branch, which relies on it,
# returned False while the held original had zero names left. Each is a case where the module
# knew it held the only copy and closed it anyway.
# =============================================================================================


def _refusal_with_slot_stolen_during(module, reports, hook_name):
    """Run the refusal writer with the preserved slot stolen inside HOOK_NAME. Returns nothing;
    the caller asserts on the tree. The steal is a status-line plant, which under the old code
    reads as 'nothing preserved here worth keeping'."""
    real = getattr(module, hook_name)
    fired: list[str] = []

    def hooked(*args, **kwargs):
        if not fired:
            fired.append(hook_name)
            slot = reports / "scan_report.superseded.txt"
            if slot.exists():
                plant = reports / "plant.txt"
                plant.write_text("scan_gate: CLEAN\n", encoding="utf-8")
                os.replace(plant, slot)
        return real(*args, **kwargs)

    setattr(module, hook_name, hooked)
    try:
        module._write_refusal_report(str(reports.parent), module.ScanRefused("report-path-unsafe 'x'"))
    finally:
        setattr(module, hook_name, real)
    return fired


def test_the_fallback_replace_also_rechecks_the_authorization(tmp_path: Path) -> None:
    """REPAIRED (B1): the sibling branch. The fallback publish must not spend a stale authorization."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "fallback_recheck")
    reports = tmp_path / "_reports"
    reports.mkdir()
    (reports / "scan_report.txt").write_text("aws\tkey\tassignment\tdocs/fb.md:1\n", encoding="utf-8")

    real_install = module._install_posix_acl_policy

    def install_that_steals_then_fails(dirfd, src_name, dst_fd, dst_name):
        # THE INTERLEAVING: the slot is taken during the ORDINARY path's policy install, which
        # then fails — routing the publish through the fallback, which had no re-check.
        slot = reports / "scan_report.superseded.txt"
        if slot.exists():
            plant = reports / "plant.txt"
            plant.write_text("scan_gate: CLEAN\n", encoding="utf-8")
            os.replace(plant, slot)
        raise OSError(errno.EPERM, "policy install failed (injected)")

    module._install_posix_acl_policy = install_that_steals_then_fails
    try:
        module._write_refusal_report(str(tmp_path), module.ScanRefused("report-path-unsafe 'x'"))
    finally:
        module._install_posix_acl_policy = real_install
    assert _findings_anywhere(reports, "docs/fb.md:1"), (
        "REPAIRED: the fallback replaced the canonical findings on the strength of an "
        "authorization whose slot had already been taken. The ordinary path re-checks; this one "
        "did not. Same defect, sibling branch, third time")


def test_copy_out_keeps_its_stage_when_every_reserved_name_is_taken(tmp_path: Path) -> None:
    """REPAIRED (B2): a completed recovery stage is never deleted for lack of a reserved name."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "copyout_slots_full")
    staging = tmp_path / "tree"
    reports = staging / "_reports"
    reports.mkdir(parents=True)
    for name in module._unpublished_slot_names():
        (reports / name).mkdir()          # every reserved name occupied by something unremovable

    real_unlink = module.os.unlink
    real_install = module._install_posix_acl_policy
    took: list[str] = []

    def install_after_taking_the_staged_name(dirfd, src_name, dst_fd, dst_name):
        if not took:
            took.append(dst_name)
            try: real_unlink(str(reports / dst_name))
            except OSError: pass
        raise OSError(errno.EIO, "policy install failed (injected)")

    module._install_posix_acl_policy = install_after_taking_the_staged_name
    try:
        with pytest.raises(BaseException):
            module.write_report(str(staging), [("docs/full.md", 2, "aws", "key", "assignment")])
    finally:
        module._install_posix_acl_policy = real_install
    if not took:
        pytest.skip("the staged name was never taken; this arm measured nothing")
    assert _findings_anywhere(reports, "docs/full.md:2"), (
        "REPAIRED: the copy-out wrote a complete recovery stage, found every reserved name "
        "occupied, and deleted the stage it had just written — then the last descriptor was "
        "closed. A completed stage under the scanner's own prefix promises nothing and must be "
        "KEPT when no reserved name will take it")


def test_copy_out_confirms_the_reserved_link_is_its_stage(tmp_path: Path) -> None:
    """REPAIRED (B3): the reserved name must be the inode that was written, not whatever is there."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "copyout_link_identity")
    staging = tmp_path / "tree"
    reports = staging / "_reports"
    reports.mkdir(parents=True)

    real_link = module.os.link
    real_unlink = module.os.unlink
    real_install = module._install_posix_acl_policy
    swapped: list[str] = []

    def install_after_taking_the_staged_name(dirfd, src_name, dst_fd, dst_name):
        try: real_unlink(str(reports / dst_name))
        except OSError: pass
        raise OSError(errno.EIO, "policy install failed (injected)")

    def link_that_substitutes_the_stage(src, dst, *args, **kwargs):
        # THE SUBSTITUTION, re-keyed in round thirty: the link is now made through the
        # descriptor directory, so the stage name no longer arrives as `src`. The writer takes
        # the stage NAME (unlink + decoy at that name) just before the link; the link then sees
        # an inode with no names and must fail rather than attach anything.
        if (not swapped and isinstance(dst, str) and dst.startswith("scan_report.unpublished")):
            for p in list(reports.iterdir()):
                if p.name.startswith(".scan_report_"):
                    swapped.append(p.name)
                    decoy = reports / "decoy.txt"
                    decoy.write_text("scan_gate: CLEAN\n", encoding="utf-8")
                    try: real_unlink(str(p))
                    except OSError: pass
                    os.replace(decoy, p)
        return real_link(src, dst, *args, **kwargs)

    module._install_posix_acl_policy = install_after_taking_the_staged_name
    module.os.link = link_that_substitutes_the_stage
    try:
        with pytest.raises(BaseException):
            module.write_report(str(staging), [("docs/decoy.md", 3, "aws", "key", "assignment")])
    finally:
        module.os.link = real_link
        module._install_posix_acl_policy = real_install
    if not swapped:
        pytest.skip("the copy-out never linked; this arm measured nothing")
    assert _findings_anywhere(reports, "docs/decoy.md:3"), (
        "REPAIRED: the copy-out linked whatever stood at its stage name into a reserved slot and "
        "answered True. The slot holds a decoy; the findings have no name. The link has to be "
        "confirmed against the descriptor that wrote the stage")


def test_quarantine_mismatch_still_rescues_the_held_original(tmp_path: Path) -> None:
    """REPAIRED (B4): divergence found after the link gets the same rescue as divergence before it."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "quarantine_mismatch_rescue")
    staging = tmp_path / "tree"
    reports = staging / "_reports"
    reports.mkdir(parents=True)

    real_link = module.os.link
    real_unlink = module.os.unlink
    real_install = module._install_posix_acl_policy
    swapped: list[str] = []

    def failing_install(dirfd, src_name, dst_fd, dst_name):
        raise OSError(errno.EIO, "policy install failed (injected)")

    def link_after_the_stage_was_replaced(src, dst, *args, **kwargs):
        # THE SUBSTITUTION, re-keyed in round thirty: the link is now made through the
        # descriptor directory, so the stage name no longer arrives as `src`. The writer takes
        # the stage NAME (unlink + decoy at that name) just before the link; the link then sees
        # an inode with no names and must fail rather than attach anything.
        if (not swapped and isinstance(dst, str) and dst.startswith("scan_report.unpublished")):
            for p in list(reports.iterdir()):
                if p.name.startswith(".scan_report_"):
                    swapped.append(p.name)
                    decoy = reports / "decoy.txt"
                    decoy.write_text("scan_gate: CLEAN\n", encoding="utf-8")
                    try: real_unlink(str(p))
                    except OSError: pass
                    os.replace(decoy, p)
        return real_link(src, dst, *args, **kwargs)

    module._install_posix_acl_policy = failing_install
    module.os.link = link_after_the_stage_was_replaced
    try:
        with pytest.raises(BaseException):
            module.write_report(str(staging), [("docs/rescue.md", 4, "aws", "key", "assignment")])
    finally:
        module.os.link = real_link
        module._install_posix_acl_policy = real_install
    if not swapped:
        pytest.skip("no quarantine link was attempted; this arm measured nothing")
    assert _findings_anywhere(reports, "docs/rescue.md:4"), (
        "REPAIRED: quarantine detected that the reserved link was not its inode, answered False, "
        "and left the held original — which by then had zero names — to be closed. The mismatch "
        "branch has to rescue through the descriptor, exactly as the pre-link divergence does")


def test_the_sweep_does_not_run_without_a_reference_timestamp(tmp_path: Path) -> None:
    """REPAIRED (B5): an unknown reference age must not authorize the sweep."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "sweep_no_reference")
    staging = tmp_path / "tree"
    reports = staging / "_reports"
    reports.mkdir(parents=True)

    real_fstat = module.os.fstat
    real_replace = module.os.replace
    planted: list[Path] = []
    denied: list[int] = []

    def fstat_that_fails_once_for_write_report(fd):
        if not denied and sys._getframe(1).f_code.co_name == "write_report":
            denied.append(fd)
            raise OSError(errno.EIO, "fstat failed (injected)")
        return real_fstat(fd)

    def replace_then_a_writer_quarantines(*args, **kwargs):
        r = real_replace(*args, **kwargs)
        if not planted:
            q = reports / "scan_report.unpublished.txt"
            q.write_text("gcp\tkey\tassignment\tsrc/noref.py:5\n", encoding="utf-8")
            planted.append(q)
        return r

    module.os.fstat = fstat_that_fails_once_for_write_report
    module.os.replace = replace_then_a_writer_quarantines
    try:
        module.write_report(str(staging), [("docs/mine.md", 1, "aws", "key", "assignment")])
    finally:
        module.os.fstat = real_fstat
        module.os.replace = real_replace
    if not denied:
        pytest.skip("the reference fstat was never reached; this arm measured nothing")
    assert _findings_anywhere(reports, "src/noref.py:5"), (
        "REPAIRED: with no reference timestamp the age guard short-circuited to 'not newer' and "
        "the sweep removed a concurrent writer's retained findings. No reference means no sweep")


# =============================================================================================
# GROUP 40 — the thirty-first round. Four defects in the round-thirty rescue path, found by the
# invariant leg on its fourth pass. All four are mine, and the first is the kind that should not
# survive a re-read: the bounded recursion never passed depth+1, so the bound was a comment.
# =============================================================================================


def test_the_rescue_recursion_actually_advances_its_depth(tmp_path: Path) -> None:
    """REPAIRED (F1): a bound that never advances is not a bound."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "rescue_depth")
    reports = tmp_path / "_reports"
    reports.mkdir()
    depths: list[int] = []
    real = module._copy_out_unpublished

    def recording(dirfd, fd, depth=0):
        depths.append(depth)
        return real(dirfd, fd, depth)
    module._copy_out_unpublished = recording

    real_link = module._link_held_inode
    def link_that_always_finds_no_names(fd, candidate, dirfd):
        # Every attempt: the stage has "lost its last name" — and, since round forty-nine, the
        # name really is taken, because the recursion now lives in the finally's re-ask and only
        # fires when the stage name no longer reaches the stage. Without an advancing depth this
        # recurses until Python gives up.
        decoy = reports / "decoy.txt"
        for victim in [p for p in reports.iterdir() if p.name.startswith(".scan_report_") and p.name != ".scan_report_src"]:
            decoy.write_text("scan_gate: CLEAN\n", encoding="utf-8")
            os.replace(decoy, victim)
        raise FileNotFoundError(errno.ENOENT, "no names (injected, every time)")
    module._link_held_inode = link_that_always_finds_no_names

    dirfd = os.open(str(reports), os.O_RDONLY | os.O_DIRECTORY)
    src = os.open(str(reports / ".scan_report_src"), os.O_CREAT | os.O_WRONLY, 0o600)
    os.write(src, b"aws\tkey\tassignment\tdocs/depth.md:1\n")
    try:
        result = module._copy_out_unpublished(dirfd, src)
    finally:
        module._copy_out_unpublished = real
        module._link_held_inode = real_link
        os.close(src); os.close(dirfd)
    assert not any(p.name.startswith("scan_report.unpublished") for p in reports.iterdir()), (
        "CONTROL: with every link refused, no reserved name may be taken (since round fifty the answer is\n"
        "True when a complete copy is KEPT, so the reserved names are what this control reads)")
    assert max(depths) >= 1 and len(depths) <= 3, (
        f"REPAIRED: depths seen were {depths}. The recursion must pass depth+1 and stop at the "
        "bound — the previous shape recursed with depth 0 every time")


def test_an_interrupt_mid_copy_keeps_the_partial_stage(tmp_path: Path) -> None:
    """REPAIRED (F2): retention is the default once a stage may hold bytes; cancellation keeps it."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "interrupt_keeps_stage")
    reports = tmp_path / "_reports"
    reports.mkdir()
    real_write = module.os.write
    calls: list[int] = []

    def write_then_interrupt(fd, data):
        calls.append(fd)
        if len(calls) == 1:
            real_write(fd, data[:3])
            raise KeyboardInterrupt
        return real_write(fd, data)
    module.os.write = write_then_interrupt

    dirfd = os.open(str(reports), os.O_RDONLY | os.O_DIRECTORY)
    src = os.open(str(reports / ".scan_report_src"), os.O_CREAT | os.O_WRONLY, 0o600)
    real_write(src, b"aws\tkey\tassignment\tdocs/int.md:2\n")
    try:
        with pytest.raises(KeyboardInterrupt):
            module._copy_out_unpublished(dirfd, src)
    finally:
        module.os.write = real_write
        os.close(src); os.close(dirfd)
    stages = [p for p in reports.iterdir() if p.name.startswith(".scan_report_") and p.name != ".scan_report_src"]
    assert stages and stages[0].stat().st_size >= 3, (
        "REPAIRED: a KeyboardInterrupt after three bytes were written propagated, and the finally "
        "deleted the recovery stage those bytes were in. Retention must be the default once a "
        "stage exists; cancellation keeps it")


def test_refusal_cleanup_does_not_unlink_a_stage_the_guard_rejected(tmp_path: Path) -> None:
    """REPAIRED (F3): the exception handler must not delete a name whose identity just failed."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "refusal_cleanup_foreign")
    reports = tmp_path / "_reports"
    reports.mkdir()
    (reports / "scan_report.txt").write_text("aws\tkey\tassignment\tdocs/old.md:1\n", encoding="utf-8")
    real_install = module._install_posix_acl_policy
    swapped: list[str] = []

    def install_that_substitutes_the_stage(dirfd, src_name, dst_fd, dst_name):
        # THE SUBSTITUTION: a FOREIGN findings file is put at the refusal's staged name. The
        # identity guard in the publication path will correctly refuse — and the cleanup that
        # follows must not delete the foreign file the guard just declined to touch.
        foreign = reports / "foreign.txt"
        foreign.write_text("gcp\tkey\tassignment\tsrc/foreign.py:7\n", encoding="utf-8")
        swapped.append(dst_name)
        os.replace(foreign, reports / dst_name)
        return real_install(dirfd, src_name, dst_fd, dst_name)
    module._install_posix_acl_policy = install_that_substitutes_the_stage
    try:
        module._write_refusal_report(str(tmp_path), module.ScanRefused("report-path-unsafe 'x'"))
    finally:
        module._install_posix_acl_policy = real_install
    if not swapped:
        pytest.skip("no refusal stage was created; this arm measured nothing")
    assert _findings_anywhere(reports, "src/foreign.py:7"), (
        "REPAIRED: the publication path detected that the staged name was not its inode and "
        "raised — and the exception handler then unlinked that name anyway, deleting a foreign "
        "findings file the guard had just refused to touch")


def test_an_interrupt_during_acquisition_does_not_leak_the_descriptor(tmp_path: Path) -> None:
    """REPAIRED (F4): the acquisition interval inside _open_held_copy is covered too."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "acquire_interrupt")
    reports = tmp_path / "_reports"
    reports.mkdir()
    rp = reports / "scan_report.txt"
    rp.write_text("x\n", encoding="utf-8")
    expect = os.lstat(rp)
    real_fstat = module.os.fstat
    opened: list[int] = []
    real_open = module.os.open

    def open_recording(*a, **k):
        fd = real_open(*a, **k); opened.append(fd); return fd
    def fstat_that_interrupts(fd):
        if fd in opened:
            raise KeyboardInterrupt
        return real_fstat(fd)
    module.os.open = open_recording
    module.os.fstat = fstat_that_interrupts
    dirfd = real_open(str(reports), os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(KeyboardInterrupt):
            module._open_held_copy(dirfd, "scan_report.txt", expect)
    finally:
        module.os.open = real_open
        module.os.fstat = real_fstat
        os.close(dirfd)
    leaked = [fd for fd in opened if fd != dirfd]
    still_open = []
    for fd in leaked:
        try:
            real_fstat(fd); still_open.append(fd)
        except OSError:
            pass
    for fd in still_open:
        os.close(fd)
    assert not still_open, (
        f"REPAIRED: descriptor(s) {still_open} were still open after an interrupt inside "
        "_open_held_copy. The caller never received them, so its own cleanup cannot close them")


def test_the_replace_is_refused_when_the_canonical_inode_changed_since_preservation(
    tmp_path: Path
) -> None:
    """REPAIRED: the authorization must name the inode it authorizes destroying.

    Preservation classifies and links report A. The refusal then stages its body and installs
    policy — many syscalls — and replaces the canonical NAME. If a different findings report B was
    put at that name in between, the replace destroys B, which nobody preserved. Two legs found
    this from different ends: one measured it and called it a wide limit, the other said it is
    closeable — record the canonical inode at preservation time and refuse the replace when the
    name no longer refers to it. The interval between that check and the rename remains the
    documented limit; the interval between preservation and the check no longer is.
    """
    driver = make_tool(tmp_path)
    module = import_driver(driver, "canonical_changed")
    reports = tmp_path / "_reports"
    reports.mkdir()
    (reports / "scan_report.txt").write_text("aws\tkey\tassignment\tdocs/A.md:1\n", encoding="utf-8")
    real_install = module._install_posix_acl_policy
    swapped: list[str] = []

    def install_that_substitutes_the_canonical(dirfd, src_name, dst_fd, dst_name):
        # THE INTERLEAVING: after A was preserved, a NEW findings report B lands at the canonical
        # name while the refusal body is being prepared.
        if not swapped:
            swapped.append("B")
            b = reports / "B.txt"
            b.write_text("gcp\tkey\tassignment\tsrc/B.py:2\n", encoding="utf-8")
            os.replace(b, reports / "scan_report.txt")
        return real_install(dirfd, src_name, dst_fd, dst_name)

    module._install_posix_acl_policy = install_that_substitutes_the_canonical
    try:
        module._write_refusal_report(str(tmp_path), module.ScanRefused("report-path-unsafe 'x'"))
    finally:
        module._install_posix_acl_policy = real_install
    if not swapped:
        pytest.skip("the refusal never reached policy install; this arm measured nothing")
    assert _findings_anywhere(reports, "docs/A.md:1"), "CONTROL: the preserved report A must survive"
    assert _findings_anywhere(reports, "src/B.py:2"), (
        "REPAIRED: the refusal replaced report B — a findings report that arrived at the canonical "
        "name after A was preserved. A guard for A cannot authorize deleting B; the replace must be "
        "refused when the canonical inode is no longer the one preservation saw")


# =============================================================================================
# GROUP 41 — the thirty-second round. Three findings from the invariant leg on f153122, and all
# three are the shape round thirty named: a name read or deleted after — or without — the
# identity check that its sibling branch already carries.
# =============================================================================================


def test_no_slot_classification_reads_the_inode_it_recorded(tmp_path: Path) -> None:
    """REPAIRED (N1): with no slot, the status verdict must come from the inode that was recorded.

    When every slot is taken, preservation classifies the canonical report by opening the NAME
    and reading it — never comparing what it opened with `previous`. Put a CLEAN status line at
    the name for exactly the duration of that open, then put findings A back: the verdict is
    "status line", nothing is preserved, the canonical guard passes because A is there again, and
    the replace destroys A's only name. No concurrent activity is needed after the swap; this is
    outside the documented check-to-rename interval.
    """
    driver = make_tool(tmp_path)
    module = import_driver(driver, "no_slot_aba")
    reports = tmp_path / "_reports"
    reports.mkdir()
    canonical = reports / "scan_report.txt"
    canonical.write_text("aws\tkey\tassignment\tdocs/A.md:1\n", encoding="utf-8")
    for slot in module._superseded_slot_names():
        (reports / slot).mkdir()              # every slot occupied: no link can succeed
    real_open = module.os.open
    swapped: list[str] = []

    def open_that_shows_a_status_line_then_restores_A(path, flags, *args, **kwargs):
        caller = sys._getframe(1).f_code.co_name
        if caller in ("_preserve_superseded", "_open_held_copy") and path == "scan_report.txt" and not swapped:
            swapped.append("S")
            aside = reports / "A.aside"
            os.rename(canonical, aside)
            canonical.write_bytes(module._STATUS_LINE_PREFIX + b" CLEAN\n")
            fd = real_open(path, flags, *args, **kwargs)
            os.replace(aside, canonical)      # A is back before the descriptor is even returned
            return fd
        return real_open(path, flags, *args, **kwargs)

    module.os.open = open_that_shows_a_status_line_then_restores_A
    try:
        module._write_refusal_report(str(tmp_path), module.ScanRefused("report-path-unsafe 'x'"))
    finally:
        module.os.open = real_open
    if not swapped:
        pytest.skip("the no-slot classification open never ran; this arm measured nothing")
    assert _findings_anywhere(reports, "docs/A.md:1"), (
        "REPAIRED: the no-slot classification read a status line from whatever was at the name, "
        "recorded findings report A as classified, and the refusal then replaced A on that "
        "verdict. Classification must read through a descriptor verified to be the recorded inode")


def test_a_successful_rescue_does_not_unlink_a_substituted_stage(tmp_path: Path) -> None:
    """REPAIRED (N2): the stage NAME is unlinked after publication without an identity check.

    The sibling of F3. `_copy_out_unpublished` links its completed stage to a reserved name,
    verifies THAT link, and then unlinks the stage by name in its finally. A findings report B
    put at the stage name between the two is deleted.
    """
    driver = make_tool(tmp_path)
    module = import_driver(driver, "rescue_stage_substituted")
    reports = tmp_path / "_reports"
    reports.mkdir()
    real_link = module._link_held_inode
    swapped: list[str] = []

    def link_then_substitute_the_stage(fd, candidate, dirfd):
        result = real_link(fd, candidate, dirfd)
        if not swapped:
            stages = [p for p in reports.iterdir()
                      if p.name.startswith(".scan_report_") and p.name != ".scan_report_src"]
            assert len(stages) == 1, stages
            swapped.append(stages[0].name)
            b = reports / "B.txt"
            b.write_text("gcp\tkey\tassignment\tsrc/B.py:2\n", encoding="utf-8")
            os.replace(b, stages[0])          # B's LAST name is now the rescue's stage name
        return result

    module._link_held_inode = link_then_substitute_the_stage
    dirfd = os.open(str(reports), os.O_RDONLY | os.O_DIRECTORY)
    src = os.open(str(reports / ".scan_report_src"), os.O_CREAT | os.O_WRONLY, 0o600)
    os.write(src, b"aws\tkey\tassignment\tdocs/int.md:2\n")
    os.close(src)
    src = os.open(str(reports / ".scan_report_src"), os.O_RDONLY)
    try:
        published = module._copy_out_unpublished(dirfd, src)
    finally:
        module._link_held_inode = real_link
        os.close(src); os.close(dirfd)
    if not swapped:
        pytest.skip("the rescue never linked; this arm measured nothing")
    assert published is True, "CONTROL: the rescue itself must have published"
    assert _findings_anywhere(reports, "docs/int.md:2"), "CONTROL: the rescued findings must survive"
    assert _findings_anywhere(reports, "src/B.py:2"), (
        "REPAIRED: after publishing its copy, the rescue unlinked the stage NAME, which by then "
        "was the only name of findings report B. Cleanup must verify the name is still the "
        "copied inode, through the still-open descriptor, or leave it")


def test_releasing_a_status_slot_checks_the_slot_is_still_the_status_inode(tmp_path: Path) -> None:
    """REPAIRED (N3): a link count on the held inode does not say what the slot NAME reaches.

    A status report with an extra hard link is preserved into a slot; the slot is then swapped
    for findings report B's last name while the prefix is being read. The held inode still has
    two names, so "another name reaches it" is true — of the status inode. The slot is unlinked
    by name and B is gone.
    """
    driver = make_tool(tmp_path)
    module = import_driver(driver, "status_slot_substituted")
    reports = tmp_path / "_reports"
    reports.mkdir()
    canonical = reports / "scan_report.txt"
    canonical.write_bytes(module._STATUS_LINE_PREFIX + b" CLEAN\n")
    os.link(canonical, reports / "alias.txt")     # a second name for the status inode
    slot = reports / next(iter(module._superseded_slot_names()))
    real_read = module._read_prefix_held
    swapped: list[str] = []

    def read_then_substitute_the_slot(fd, via_proc, count):
        result = real_read(fd, via_proc, count)
        if not swapped and slot.exists():
            swapped.append(slot.name)
            b = reports / "B.txt"
            b.write_text("gcp\tkey\tassignment\tsrc/B.py:2\n", encoding="utf-8")
            os.replace(b, slot)               # B's LAST name is now the slot
        return result

    module._read_prefix_held = read_then_substitute_the_slot
    try:
        preserve_superseded(module, reports)
    finally:
        module._read_prefix_held = real_read
    if not swapped:
        pytest.skip("the held-descriptor read never ran; this arm measured nothing")
    assert _findings_anywhere(reports, "src/B.py:2"), (
        "REPAIRED: the status-slot release unlinked the slot by name on the strength of the "
        "STATUS inode's link count; the slot had become findings report B's only name. The "
        "release must confirm the slot still refers to the held inode, or keep it")


# ---- the cold leg's three, on the same tree ------------------------------------------------

def test_the_staged_identity_check_is_the_syscall_before_the_replace(tmp_path: Path) -> None:
    """REPAIRED (cold leg #2): identity of the replace SOURCE is only useful in the syscall before it.

    Round thirty-one put two guards BETWEEN the staged-name identity check and the rename. Plant a
    wide-open file at the staged name during the second guard: the rename then publishes the
    planted entry under the canonical name, mode and all, and the last reference to the staged
    body is closed. The guards must run first and the identity check last.
    """
    driver = make_tool(tmp_path)
    module = import_driver(driver, "identity_adjacent_to_replace")
    reports = tmp_path / "_reports"
    reports.mkdir()
    canonical = reports / "scan_report.txt"
    canonical.write_text("aws\tkey\tassignment\tdocs/A.md:1\n", encoding="utf-8")
    real_check = module._canonical_still_classified
    swapped: list[str] = []

    def check_then_plant_at_the_stage(dirfd, guard):
        result = real_check(dirfd, guard)
        if not swapped:
            stages = [p for p in reports.iterdir() if p.name.startswith(".scan_report_")]
            assert len(stages) == 1, stages
            swapped.append(stages[0].name)
            planted = reports / "planted.txt"
            planted.write_text("PLANTED wide-open body\n", encoding="utf-8")
            planted.chmod(0o644)
            os.replace(planted, stages[0])    # the staged NAME is now a 0644 planted entry
        return result

    module._canonical_still_classified = check_then_plant_at_the_stage
    try:
        module._write_refusal_report(str(tmp_path), module.ScanRefused("report-path-unsafe 'x'"))
    finally:
        module._canonical_still_classified = real_check
    if not swapped:
        pytest.skip("the canonical guard never ran; this arm measured nothing")
    assert not canonical.is_symlink() and "PLANTED" not in canonical.read_text(encoding="utf-8"), (
        "REPAIRED: the replace renamed the PLANTED entry onto the canonical name — the staged "
        "identity was checked two syscalls too early")
    assert (os.stat(canonical).st_mode & 0o777) == 0o600, (
        "REPAIRED: the canonical name carries the planted mode, not the published one")


def test_a_clean_stage_failure_cleanup_does_not_unlink_a_substituted_name(tmp_path: Path) -> None:
    """REPAIRED (cold leg #4): the CLEAN twin of F3 — an identity-blind unlink of the staged name."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "clean_cleanup_foreign")
    staging = tmp_path / "staging"
    reports = staging / "_reports"
    reports.mkdir(parents=True)
    real_install = module._install_posix_acl_policy
    swapped: list[str] = []

    def install_that_substitutes_the_stage_then_fails(dirfd, src_name, dst_fd, dst_name):
        if not swapped and dst_name.startswith(".scan_report_"):
            swapped.append(dst_name)
            b = reports / "B.txt"
            b.write_text("gcp\tkey\tassignment\tsrc/B.py:2\n", encoding="utf-8")
            os.replace(b, reports / dst_name)   # B's LAST name is now the CLEAN stage's name
            raise OSError(5, "injected policy failure")
        return real_install(dirfd, src_name, dst_fd, dst_name)

    module._install_posix_acl_policy = install_that_substitutes_the_stage_then_fails
    try:
        with pytest.raises(Exception):
            module.write_report(str(staging), [])
    finally:
        module._install_posix_acl_policy = real_install
    if not swapped:
        pytest.skip("the policy install never ran on a stage; this arm measured nothing")
    assert _findings_anywhere(reports, "src/B.py:2"), (
        "REPAIRED: the CLEAN stage's failure cleanup unlinked the staged NAME, which had become "
        "findings report B's only name. Cleanup must be identity-checked like every other unlink")


def test_stage_write_failure_cleanup_does_not_unlink_a_substituted_name(tmp_path: Path) -> None:
    """REPAIRED (cold leg #4, sibling): `_stage_report`'s non-evidence cleanup unlinks by name."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "stage_cleanup_foreign")
    reports = tmp_path / "_reports"
    reports.mkdir()
    # The stage is written through os.fdopen's buffered handle, whose flush is the C write(2)
    # and never os.write — an earlier draft of this arm patched os.write and measured nothing.
    real_fdopen = module.os.fdopen
    swapped: list[str] = []

    def fdopen_that_substitutes_the_stage_then_fails(fd, *args, **kwargs):
        if not swapped:
            stages = [p for p in reports.iterdir() if p.name.startswith(".scan_report_")]
            if len(stages) == 1:
                swapped.append(stages[0].name)
                b = reports / "B.txt"
                b.write_text("gcp\tkey\tassignment\tsrc/B.py:2\n", encoding="utf-8")
                os.replace(b, stages[0])
                raise OSError(5, "injected write failure")
        return real_fdopen(fd, *args, **kwargs)

    module.os.fdopen = fdopen_that_substitutes_the_stage_then_fails
    dirfd = os.open(str(reports), os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(OSError):
            module._stage_report(dirfd, "scan_gate: CLEAN\n")
    finally:
        module.os.fdopen = real_fdopen
        os.close(dirfd)
    if not swapped:
        pytest.skip("the stage write never ran; this arm measured nothing")
    assert _findings_anywhere(reports, "src/B.py:2"), (
        "REPAIRED: a failed status-line stage was cleaned up by unlinking its NAME, which had "
        "become findings report B's only name")


def test_narrowing_reports_false_when_the_mode_did_not_land(tmp_path: Path) -> None:
    """REPAIRED (cold leg #6): the publish path verifies the mode landed; the narrowing must too."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "narrow_mode_verified")
    if not module._XATTR_SUPPORTED:
        pytest.skip("no xattr layer here: narrowing already answers False on this platform")
    wide = tmp_path / "wide.txt"
    wide.write_text("x\n", encoding="utf-8")
    wide.chmod(0o644)
    real_chmod = module.os.chmod

    def chmod_that_returns_without_effect(*args, **kwargs):
        return None

    fd = os.open(str(wide), os.O_RDONLY)
    module.os.chmod = chmod_that_returns_without_effect
    try:
        narrowed = module._narrow_held_copy(fd, False)
    finally:
        module.os.chmod = real_chmod
        os.close(fd)
    assert (os.stat(wide).st_mode & 0o777) == 0o644, "CONTROL: the no-op chmod must leave the file wide"
    assert narrowed is False, (
        "REPAIRED: a chmod that returned without taking effect was reported as a successful "
        "narrowing, which records a slot and authorizes the replace while the preserved copy stays wide")
    fd = os.open(str(wide), os.O_RDONLY)
    try:
        assert module._narrow_held_copy(fd, False) is True, "CONTROL: a real chmod narrows and says so"
    finally:
        os.close(fd)
    assert (os.stat(wide).st_mode & 0o777) == 0o600


# =============================================================================================
# GROUP 42 — the thirty-third round. The findings ledger (rounds 17–31, every leg output on
# disk) listed fourteen items with no fixing commit and no documented limit. Three are code.
# =============================================================================================


def test_the_refusal_writer_does_not_raise_on_a_staging_path_that_is_not_a_string(tmp_path: Path) -> None:
    """REPAIRED (ledger #8): "never raises" was true only once `staging` had survived os.path.join."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "refusal_none_staging")
    try:
        module._write_refusal_report(None, module.ScanRefused("report-path-unsafe 'x'"))
    except BaseException as exc:      # noqa: BLE001 — the contract is that NOTHING escapes
        pytest.fail(f"REPAIRED: the refusal writer raised {type(exc).__name__} on a non-string "
                    f"staging path, before its own guard was reached")


def test_the_refusal_fallback_does_not_publish_a_mode_it_did_not_verify(tmp_path: Path) -> None:
    """REPAIRED (ledger #10): the fallback fchmods and never looks; the ordinary path has looked since round 17."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "fallback_mode_verified")
    reports = tmp_path / "_reports"
    reports.mkdir()
    canonical = reports / "scan_report.txt"
    original = "aws\tkey\tassignment\tdocs/A.md:1\n"
    canonical.write_text(original, encoding="utf-8")
    real_install = module._install_posix_acl_policy
    real_fchmod = module.os.fchmod

    def install_that_fails(dirfd, src_name, dst_fd, dst_name):
        raise OSError(5, "injected: force the fallback")

    def fchmod_that_returns_without_effect(*args, **kwargs):
        return None

    module._install_posix_acl_policy = install_that_fails
    module.os.fchmod = fchmod_that_returns_without_effect
    old_umask = os.umask(0o400)           # the stage is created owner-unreadable (0200)
    try:
        module._write_refusal_report(str(tmp_path), module.ScanRefused("report-path-unsafe 'x'"))
    finally:
        os.umask(old_umask)
        module._install_posix_acl_policy = real_install
        module.os.fchmod = real_fchmod
    mode = os.stat(canonical).st_mode & 0o777
    assert mode & 0o400, (
        f"REPAIRED: the fallback published a refusal at mode {oct(mode)} — owner-unreadable. It "
        f"set the mode, never verified it landed, and published anyway")
    body = canonical.read_bytes()
    assert body == original.encode() or (body.startswith(b"scan_gate: REFUSED") and mode == 0o600), (
        f"REPAIRED: the canonical name holds neither the original nor a 0600 refusal (mode {oct(mode)})")


def test_a_kept_stage_is_left_owner_readable(tmp_path: Path) -> None:
    """REPAIRED (ledger #11/#12): a stage the scanner keeps as the only copy must be readable by its owner.

    The quarantine answers False when the strip is denied and the caller leaves the bytes at the
    staged name — at the CREATE mode, which under a umask that masks owner read is 0200. The
    evidence is kept and nobody can read it.
    """
    driver = make_tool(tmp_path)
    module = import_driver(driver, "kept_stage_readable")
    if not module._XATTR_SUPPORTED:
        pytest.skip("no xattr layer: the strip-denied path is not reachable here")
    staging = tmp_path / "staging"
    reports = staging / "_reports"
    reports.mkdir(parents=True)
    real_install = module._install_posix_acl_policy
    real_strip = module._strip_acl_by_fd

    def install_that_fails(dirfd, src_name, dst_fd, dst_name):
        raise OSError(5, "injected: force the failure path")

    def strip_that_is_denied(fd):
        raise OSError(errno.EPERM, "injected: strip denied")

    module._install_posix_acl_policy = install_that_fails
    module._strip_acl_by_fd = strip_that_is_denied
    old_umask = os.umask(0o400)
    try:
        with pytest.raises(Exception):
            module.write_report(str(staging), [("docs/k.md", 1, "SECRET", "generic_key_assignment",
                                                 "contents")])
    finally:
        os.umask(old_umask)
        module._install_posix_acl_policy = real_install
        module._strip_acl_by_fd = real_strip
    kept = [p for p in reports.iterdir() if p.name.startswith(".scan_report_")]
    if not kept:
        pytest.skip("no stage was kept; this arm measured nothing")
    modes = {p.name: oct(p.stat().st_mode & 0o777) for p in kept}
    assert all(p.stat().st_mode & 0o400 for p in kept), (
        f"REPAIRED: the kept stage is not owner-readable ({modes}); evidence kept as the only copy "
        f"must be left at a mode its owner can read")


# =============================================================================================
# GROUP 43 — the thirty-fourth round. The invariant leg's FIX-FORWARD on 0829b97: identity
# cleanup was put before the close (right), but not under a finally — a cancellation inside the
# helper leaks the descriptor at three sites. And two chmod sites still trust the return code.
# =============================================================================================


def _interrupting_remover(module, seen: list):
    def remove_that_is_interrupted(dirfd, name, fd):
        seen.append(fd)
        raise KeyboardInterrupt
    return remove_that_is_interrupted


def _assert_closed(fd: int, what: str) -> None:
    try:
        os.fstat(fd)
    except OSError as exc:
        assert exc.errno == errno.EBADF, exc
        return
    os.close(fd)                          # do not leak it into the rest of the suite
    pytest.fail(f"REPAIRED (F1): {what} — a cancellation inside the identity cleanup left the "
                f"descriptor open; the close must be in a finally")


def test_a_rescue_interrupted_in_cleanup_still_closes_its_stage_descriptor(tmp_path: Path) -> None:
    driver = make_tool(tmp_path)
    module = import_driver(driver, "rescue_cleanup_interrupt")
    reports = tmp_path / "_reports"
    reports.mkdir()
    real_remove = module._remove_own_stage
    seen: list[int] = []
    module._remove_own_stage = _interrupting_remover(module, seen)
    dirfd = os.open(str(reports), os.O_RDONLY | os.O_DIRECTORY)
    src = os.open(str(reports / ".scan_report_src"), os.O_CREAT | os.O_WRONLY, 0o600)
    os.write(src, b"aws\tkey\tassignment\tdocs/int.md:2\n"); os.close(src)
    src = os.open(str(reports / ".scan_report_src"), os.O_RDONLY)
    try:
        with pytest.raises(KeyboardInterrupt):
            module._copy_out_unpublished(dirfd, src)
    finally:
        module._remove_own_stage = real_remove
        os.close(src); os.close(dirfd)
    if not seen:
        pytest.skip("the rescue never reached its cleanup; this arm measured nothing")
    _assert_closed(seen[0], "the rescue's stage descriptor")


def test_a_stage_write_failure_interrupted_in_cleanup_still_closes_the_descriptor(tmp_path: Path) -> None:
    driver = make_tool(tmp_path)
    module = import_driver(driver, "stage_cleanup_interrupt")
    reports = tmp_path / "_reports"
    reports.mkdir()
    real_remove = module._remove_own_stage
    real_fdopen = module.os.fdopen
    seen: list[int] = []

    def fdopen_that_fails(fd, *args, **kwargs):
        raise OSError(5, "injected write failure")

    module._remove_own_stage = _interrupting_remover(module, seen)
    module.os.fdopen = fdopen_that_fails
    dirfd = os.open(str(reports), os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(KeyboardInterrupt):
            module._stage_report(dirfd, "scan_gate: CLEAN\n")
    finally:
        module._remove_own_stage = real_remove
        module.os.fdopen = real_fdopen
        os.close(dirfd)
    if not seen:
        pytest.skip("the stage cleanup never ran; this arm measured nothing")
    _assert_closed(seen[0], "the failed stage's descriptor")


def test_a_status_slot_release_interrupted_still_closes_the_held_descriptor(tmp_path: Path) -> None:
    driver = make_tool(tmp_path)
    module = import_driver(driver, "status_release_interrupt")
    reports = tmp_path / "_reports"
    reports.mkdir()
    (reports / "scan_report.txt").write_bytes(module._STATUS_LINE_PREFIX + b" CLEAN\n")
    real_remove = module._remove_own_stage
    seen: list[int] = []
    module._remove_own_stage = _interrupting_remover(module, seen)
    try:
        with pytest.raises(KeyboardInterrupt):
            preserve_superseded(module, reports)
    finally:
        module._remove_own_stage = real_remove
    if not seen:
        pytest.skip("the status-slot release never ran; this arm measured nothing")
    _assert_closed(seen[0], "the held slot descriptor")


def test_quarantine_declines_a_reserved_name_when_its_mode_did_not_land(tmp_path: Path) -> None:
    """REPAIRED: a reserved name asserts the report's policy; a chmod that did not take does not install it."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "quarantine_mode_verified")
    reports = tmp_path / "_reports"
    reports.mkdir()
    real_fchmod = module.os.fchmod
    old_umask = os.umask(0o400)
    try:
        fd = os.open(str(reports / ".scan_report_stage"), os.O_CREAT | os.O_RDWR, 0o600)  # lands 0200
    finally:
        os.umask(old_umask)
    os.write(fd, b"aws\tkey\tassignment\tdocs/q.md:1\n")
    dirfd = os.open(str(reports), os.O_RDONLY | os.O_DIRECTORY)
    module.os.fchmod = lambda *a, **k: None
    try:
        taken = module._quarantine_unpublished(dirfd, ".scan_report_stage", fd,
                                               [("docs/q.md", 1, "SECRET", "generic_key_assignment", "c")])
    finally:
        module.os.fchmod = real_fchmod
        os.close(fd); os.close(dirfd)
    # existence, not content: under this umask the file is 0200 and cannot be read back
    assert any(p.name == ".scan_report_stage" or p.name.startswith("scan_report.unpublished")
               for p in reports.iterdir()), "CONTROL: the bytes must survive under some name"
    assert taken is False and not any(p.name.startswith("scan_report.unpublished") for p in reports.iterdir()), (
        "REPAIRED: quarantine reserved a name for a stage whose mode it set and never verified — "
        "the retained copy is presented as carrying a policy that is not on it")


def test_rescue_declines_a_reserved_name_when_its_mode_did_not_land(tmp_path: Path) -> None:
    """REPAIRED: same as quarantine, in the rescue — the stage is kept, the reserved name is not taken."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "rescue_mode_verified")
    reports = tmp_path / "_reports"
    reports.mkdir()
    real_fchmod = module.os.fchmod
    src = os.open(str(reports / ".scan_report_src"), os.O_CREAT | os.O_WRONLY, 0o600)
    os.write(src, b"aws\tkey\tassignment\tdocs/int.md:2\n"); os.close(src)
    src = os.open(str(reports / ".scan_report_src"), os.O_RDONLY)
    dirfd = os.open(str(reports), os.O_RDONLY | os.O_DIRECTORY)
    module.os.fchmod = lambda *a, **k: None
    old_umask = os.umask(0o400)
    try:
        published = module._copy_out_unpublished(dirfd, src)
    finally:
        os.umask(old_umask)
        module.os.fchmod = real_fchmod
        os.close(src); os.close(dirfd)
    assert _findings_anywhere(reports, "docs/int.md:2"), "CONTROL: the copied bytes must survive somewhere"
    assert not any(p.name.startswith("scan_report.unpublished") for p in reports.iterdir()), (   # the answer is True since round fifty: a complete copy is KEPT
        "REPAIRED: the rescue reserved a name for a copy whose mode it set and never verified")


# =============================================================================================
# GROUP 44 — the thirty-fifth round. The cold leg's three on 0829b97: the scan arm round-trips
# arbitrary path bytes and the publication arm assumed UTF-8 text; a kept partial stage at the
# umask-masked create mode; and the sweep's age check followed by an unlink of the NAME.
# =============================================================================================


def test_a_hit_in_a_non_utf8_path_does_not_lose_every_finding(tmp_path: Path) -> None:
    """REPAIRED (cold #1): scan() stores paths with surrogateescape; the report must write them back."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "surrogate_path_hit")
    staging = tmp_path / "staging"
    reports = staging / "_reports"
    reports.mkdir(parents=True)
    hits = [("docs/\udcff.md", 1, "SECRET", "generic_key_assignment", "contents"),
            ("docs/plain.md", 2, "SECRET", "generic_key_assignment", "contents")]
    try:
        module.write_report(str(staging), hits)
    except UnicodeError as exc:
        pytest.fail(f"REPAIRED: the report writer raised {exc!r} on a hit whose path is not UTF-8 — "
                    f"this scan's findings, the plain ones included, landed nowhere")
    published = (reports / "scan_report.txt").read_bytes()
    assert b"docs/plain.md" in published and b"docs/\xff.md" in published, (
        "REPAIRED: the published report must carry both hits, the non-UTF-8 path as its original bytes")


def test_the_sweep_does_not_unlink_a_name_that_changed_after_it_was_aged(tmp_path: Path) -> None:
    """REPAIRED (cold #3): the age check answered for one inode; the unlink acted on the name."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "sweep_identity_bound")
    staging = tmp_path / "staging"
    reports = staging / "_reports"
    reports.mkdir(parents=True)
    old = reports / "scan_report.unpublished.txt"
    old.write_text("aws\tkey\tassignment\tdocs/old.md:1\n", encoding="utf-8")
    os.utime(old, ns=(1, 1))
    real_lstat, real_open = module.os.lstat, module.os.open
    swapped: list[str] = []

    def substitute_after_the_age_check(name):
        f = reports / "F.txt"
        f.write_text("gcp\tkey\tassignment\tsrc/F.py:2\n", encoding="utf-8")
        os.replace(f, reports / name)         # F's LAST name is now the aged reserved name
        swapped.append(name)

    def lstat_then_substitute(path, *args, **kwargs):
        st = real_lstat(path, *args, **kwargs)
        if (not swapped and isinstance(path, str) and path.startswith("scan_report.unpublished")
                and sys._getframe(1).f_code.co_name == "write_report"):
            substitute_after_the_age_check(path)
        return st

    def open_then_substitute(path, flags, *args, **kwargs):
        fd = real_open(path, flags, *args, **kwargs)
        if (not swapped and isinstance(path, str) and path.startswith("scan_report.unpublished")
                and flags & getattr(os, "O_PATH", 0)
                and sys._getframe(1).f_code.co_name == "write_report"):
            substitute_after_the_age_check(path)
        return fd

    module.os.lstat, module.os.open = lstat_then_substitute, open_then_substitute
    try:
        module.write_report(str(staging), [])   # a successful CLEAN publish runs the sweep
    finally:
        module.os.lstat, module.os.open = real_lstat, real_open
    if not swapped:
        pytest.skip("the sweep never aged the reserved name; this arm measured nothing")
    assert _findings_anywhere(reports, "src/F.py:2"), (
        "REPAIRED: the sweep aged one inode and unlinked the NAME, which by then was findings "
        "report F's only name. The unlink must be bound to the inode that was aged")


def test_a_kept_partial_stage_is_left_owner_readable(tmp_path: Path) -> None:
    """REPAIRED (cold #2, sequence A): the partial findings kept by `_stage_report` must be readable."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "partial_keep_readable")
    staging = tmp_path / "staging"
    reports = staging / "_reports"
    reports.mkdir(parents=True)
    real_fdopen = module.os.fdopen

    class _HalfWriter:
        def __init__(self, wrapped): self._wrapped = wrapped
        def __enter__(self): return self
        def __exit__(self, *exc): return self._wrapped.__exit__(*exc)
        def write(self, data):
            self._wrapped.write(data[: max(1, len(data) // 2)]); self._wrapped.flush()
            raise OSError(errno.EFBIG, "injected write failure")

    module.os.fdopen = lambda *a, **k: _HalfWriter(real_fdopen(*a, **k))
    old_umask = os.umask(0o400)
    try:
        with pytest.raises(Exception):
            module.write_report(str(staging), [("docs/k.md", 1, "SECRET", "generic_key_assignment", "contents")])
    finally:
        os.umask(old_umask)
        module.os.fdopen = real_fdopen
    kept = [p for p in reports.iterdir() if p.name.startswith(".scan_report_")]
    if not kept:
        pytest.skip("no partial stage was kept; this arm measured nothing")
    assert all(p.stat().st_mode & 0o400 for p in kept), (
        f"REPAIRED: the kept partial stage is owner-unreadable ({[oct(p.stat().st_mode & 0o777) for p in kept]})")


# =============================================================================================
# GROUP 45 — the thirty-sixth round. The invariant leg's FIX-FORWARD on 3c075f0: a mode check
# I put before the bytes were copied (an empty stage counted as retained evidence), and three
# more descriptor lifetimes not under a finally.
# =============================================================================================


def test_rescue_copies_the_bytes_before_deciding_about_the_reserved_name(tmp_path: Path) -> None:
    """REPAIRED (F1): declining the name is fine; declining before the copy leaves an EMPTY stage."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "rescue_bytes_first")
    reports = tmp_path / "_reports"
    reports.mkdir()
    real_fchmod = module.os.fchmod
    src = os.open(str(reports / ".scan_report_src"), os.O_CREAT | os.O_WRONLY, 0o600)
    os.write(src, b"aws\tkey\tassignment\tdocs/int.md:2\n"); os.close(src)
    src = os.open(str(reports / ".scan_report_src"), os.O_RDONLY)
    dirfd = os.open(str(reports), os.O_RDONLY | os.O_DIRECTORY)
    module.os.fchmod = lambda *a, **k: None
    old_umask = os.umask(0o400)
    try:
        published = module._copy_out_unpublished(dirfd, src)
    finally:
        os.umask(old_umask)
        module.os.fchmod = real_fchmod
        os.close(src); os.close(dirfd)
    assert not any(p.name.startswith("scan_report.unpublished") for p in reports.iterdir()), (
        "CONTROL: the reserved name must still be declined on a mode mismatch (the answer itself is True\n"
        "since round fifty — a complete copy is kept)")
    stages = [p for p in reports.iterdir() if p.name.startswith(".scan_report_") and p.name != ".scan_report_src"]
    assert stages and all(p.stat().st_size > 0 for p in stages), (
        f"REPAIRED: the rescue declined the reserved name BEFORE copying the bytes; the kept stage "
        f"is empty ({[(p.name, p.stat().st_size) for p in stages]}) — an empty destination counted as retained evidence")


def _fstat_that_cancels(module, target_fds: list, caller: str, seen: list):
    real_fstat = module.os.fstat
    def fstat_or_cancel(fd, *args, **kwargs):
        if fd in target_fds and not seen and sys._getframe(1).f_code.co_name == caller:
            seen.append(fd)
            raise KeyboardInterrupt
        return real_fstat(fd, *args, **kwargs)
    return real_fstat, fstat_or_cancel


def test_the_sweep_closes_its_entry_descriptor_when_the_age_read_is_cancelled(tmp_path: Path) -> None:
    """REPAIRED (F2): acquisition and aging must sit under the same finally as the removal."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "sweep_age_cancel")
    staging = tmp_path / "staging"
    reports = staging / "_reports"
    reports.mkdir(parents=True)
    old = reports / "scan_report.unpublished.txt"
    old.write_text("aws\tkey\tassignment\tdocs/old.md:1\n", encoding="utf-8")
    os.utime(old, ns=(1, 1))
    real_open = module.os.open
    opened: list[int] = []

    def open_and_record(path, flags, *args, **kwargs):
        fd = real_open(path, flags, *args, **kwargs)
        if (isinstance(path, str) and path.startswith("scan_report.") and flags & getattr(os, "O_PATH", 0)
                and sys._getframe(1).f_code.co_name == "write_report"):
            opened.append(fd)
        return fd

    seen: list[int] = []
    real_fstat, cancelling = _fstat_that_cancels(module, opened, "write_report", seen)
    module.os.open, module.os.fstat = open_and_record, cancelling
    try:
        with pytest.raises(KeyboardInterrupt):
            module.write_report(str(staging), [])
    finally:
        module.os.open, module.os.fstat = real_open, real_fstat
    if not seen:
        pytest.skip("the sweep never aged an entry; this arm measured nothing")
    _assert_closed(seen[0], "the sweep's entry descriptor (cancelled during the age read)")


def test_a_stage_write_failure_cancelled_in_the_evidence_read_still_closes_the_descriptor(tmp_path: Path) -> None:
    """REPAIRED (F3a): the evidence-size fstat sits before the cleanup's finally."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "stage_evidence_cancel")
    reports = tmp_path / "_reports"
    reports.mkdir()
    real_open, real_fdopen = module.os.open, module.os.fdopen
    opened: list[int] = []

    def open_and_record(path, flags, *args, **kwargs):
        fd = real_open(path, flags, *args, **kwargs)
        if sys._getframe(1).f_code.co_name == "_stage_report":
            opened.append(fd)
        return fd

    def fdopen_that_fails(fd, *args, **kwargs):
        raise OSError(5, "injected write failure")

    seen: list[int] = []
    real_fstat, cancelling = _fstat_that_cancels(module, opened, "_stage_report", seen)
    module.os.open, module.os.fdopen, module.os.fstat = open_and_record, fdopen_that_fails, cancelling
    dirfd = os.open(str(reports), os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(KeyboardInterrupt):
            module._stage_report(dirfd, "aws\tkey\tx\n", evidence=True)
    finally:
        module.os.open, module.os.fdopen, module.os.fstat = real_open, real_fdopen, real_fstat
        os.close(dirfd)
    if not seen:
        pytest.skip("the evidence-size read never ran; this arm measured nothing")
    _assert_closed(seen[0], "the failed stage's descriptor (cancelled during the evidence-size read)")


def test_a_publish_cancelled_in_the_reference_stamp_read_still_closes_the_stage(tmp_path: Path) -> None:
    """REPAIRED (F3b): the reference-stamp fstat runs before the ownership try/finally begins."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "stamp_cancel")
    staging = tmp_path / "staging"
    reports = staging / "_reports"
    reports.mkdir(parents=True)
    real_stage = module._stage_report
    staged: list[int] = []

    def stage_and_record(*args, **kwargs):
        fd, name = real_stage(*args, **kwargs)
        staged.append(fd)
        return fd, name

    seen: list[int] = []
    real_fstat, cancelling = _fstat_that_cancels(module, staged, "write_report", seen)
    module._stage_report, module.os.fstat = stage_and_record, cancelling
    try:
        with pytest.raises(KeyboardInterrupt):
            module.write_report(str(staging), [])
    finally:
        module._stage_report, module.os.fstat = real_stage, real_fstat
    if not seen:
        pytest.skip("the reference-stamp read never ran; this arm measured nothing")
    _assert_closed(seen[0], "the staged report descriptor (cancelled during the reference-stamp read)")


# =============================================================================================
# GROUP 46 — the thirty-seventh round. The cold leg's two on 3c075f0: the rescue read the whole
# source into memory before any stage existed, so a mid-read failure lost the last copy; and
# kept leftover stages were narrowed by mode alone, never stripped of an inherited ACL.
# =============================================================================================


def test_a_rescue_read_that_fails_midway_keeps_the_bytes_it_had(tmp_path: Path) -> None:
    """REPAIRED (cold #1): the retained stage must exist BEFORE the source is read; partial reads land in it."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "rescue_partial_read")
    reports = tmp_path / "_reports"
    reports.mkdir()
    body = (b"aws\tkey\tassignment\tdocs/first.md:1\n" * 3000)   # > one 64 KiB read
    src = os.open(str(reports / ".scan_report_src"), os.O_CREAT | os.O_WRONLY, 0o600)
    os.write(src, body); os.close(src)
    src = os.open(str(reports / ".scan_report_src"), os.O_RDONLY)
    real_read = module.os.read
    calls: list[int] = []

    def read_then_fail(fd, n):
        if sys._getframe(1).f_code.co_name == "_copy_out_unpublished":
            calls.append(fd)
            if len(calls) == 2:
                raise OSError(5, "injected read failure")
        return real_read(fd, n)

    module.os.read = read_then_fail
    dirfd = os.open(str(reports), os.O_RDONLY | os.O_DIRECTORY)
    try:
        published = module._copy_out_unpublished(dirfd, src)
    finally:
        module.os.read = real_read
        os.close(src); os.close(dirfd)
    if len(calls) < 2:
        pytest.skip("the rescue never reached its second read; this arm measured nothing")
    assert published is False, "CONTROL: a failed copy must not report custody"
    kept = [p for p in reports.iterdir() if p.name.startswith(".scan_report_") and p.name != ".scan_report_src"]
    assert kept and kept[0].stat().st_size >= 65536, (
        f"REPAIRED: the read failed after one chunk and the rescue returned with nothing on disk "
        f"({[(p.name, p.stat().st_size) for p in kept]}) — the bytes it already had were dropped with the stack")


def test_a_kept_partial_stage_has_its_acl_stripped(tmp_path: Path) -> None:
    """REPAIRED (cold #2a): the keep path narrows by mode alone; an inherited ACL survives on the leftover."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "partial_keep_strip")
    if not module._XATTR_SUPPORTED:
        pytest.skip("no xattr layer: no strip is possible here")
    staging = tmp_path / "staging"
    reports = staging / "_reports"
    reports.mkdir(parents=True)
    real_fdopen, real_strip = module.os.fdopen, module._strip_acl_by_fd
    stripped: list[int] = []
    opened: list[int] = []
    real_open = module.os.open

    def open_and_record(path, flags, *a, **k):
        fd = real_open(path, flags, *a, **k)
        if sys._getframe(1).f_code.co_name == "_stage_report":
            opened.append(fd)
        return fd

    class _HalfWriter:
        def __init__(self, wrapped): self._wrapped = wrapped
        def __enter__(self): return self
        def __exit__(self, *exc): return self._wrapped.__exit__(*exc)
        def write(self, data):
            self._wrapped.write(data[: max(1, len(data) // 2)]); self._wrapped.flush()
            raise OSError(errno.EFBIG, "injected write failure")

    module.os.open = open_and_record
    module.os.fdopen = lambda *a, **k: _HalfWriter(real_fdopen(*a, **k))
    module._strip_acl_by_fd = lambda fd: stripped.append(fd)
    try:
        with pytest.raises(Exception):
            module.write_report(str(staging), [("docs/k.md", 1, "SECRET", "generic_key_assignment", "contents")])
    finally:
        module.os.open, module.os.fdopen, module._strip_acl_by_fd = real_open, real_fdopen, real_strip
    if not opened:
        pytest.skip("no stage was created; this arm measured nothing")
    assert opened[-1] in stripped, (
        "REPAIRED: the kept partial stage was left with whatever ACL its directory gave it; the "
        "keep path must attempt the strip, as every other retained inode does")


def test_a_kept_stage_after_a_failed_quarantine_has_its_acl_stripped(tmp_path: Path) -> None:
    """REPAIRED (cold #2b): write_report's kept-stage branch fchmods and never strips."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "kept_stage_strip")
    if not module._XATTR_SUPPORTED:
        pytest.skip("no xattr layer: no strip is possible here")
    staging = tmp_path / "staging"
    reports = staging / "_reports"
    reports.mkdir(parents=True)
    real_install, real_quarantine, real_strip = module._install_posix_acl_policy, module._quarantine_unpublished, module._strip_acl_by_fd
    stripped: list[int] = []
    staged: list[int] = []

    def install_that_fails(dirfd, src_name, dst_fd, dst_name):
        staged.append(dst_fd)
        raise OSError(5, "injected policy failure")

    module._install_posix_acl_policy = install_that_fails
    module._quarantine_unpublished = lambda dirfd, tmp_name, fd, hits: False   # custody not taken
    module._strip_acl_by_fd = lambda fd: stripped.append(fd)
    try:
        with pytest.raises(Exception):
            module.write_report(str(staging), [("docs/k.md", 1, "SECRET", "generic_key_assignment", "contents")])
    finally:
        module._install_posix_acl_policy, module._quarantine_unpublished, module._strip_acl_by_fd = real_install, real_quarantine, real_strip
    if not staged:
        pytest.skip("the policy install never ran; this arm measured nothing")
    assert staged[0] in stripped, (
        "REPAIRED: the stage kept after a failed quarantine was fchmod'd and never stripped; the "
        "leftover keeps whatever ACL its directory gave it")


# =============================================================================================
# GROUP 47 — the thirty-eighth round. The invariant leg's FIX-FORWARD on 0c28c5e.
# =============================================================================================


def _rescue_source(reports: Path) -> int:
    src = os.open(str(reports / ".scan_report_src"), os.O_CREAT | os.O_WRONLY, 0o600)
    os.write(src, b"aws\tkey\tassignment\tdocs/int.md:2\n"); os.close(src)
    return os.open(str(reports / ".scan_report_src"), os.O_RDONLY)


def test_a_raising_stage_chmod_does_not_stop_the_rescue_copy(tmp_path: Path) -> None:
    """REPAIRED (F1): the stage's chmod sat inside the except that returns before the stream."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "rescue_chmod_raises")
    reports = tmp_path / "_reports"
    reports.mkdir()
    src = _rescue_source(reports)
    real_fchmod = module.os.fchmod

    def fchmod_that_raises_on_the_stage(fd, mode):
        if fd != src and sys._getframe(1).f_code.co_name == "_copy_out_unpublished":
            raise PermissionError(errno.EPERM, "injected chmod failure on the stage")
        return real_fchmod(fd, mode)

    os.unlink(reports / ".scan_report_src")          # the source's only name is gone: the rescue's case
    module.os.fchmod = fchmod_that_raises_on_the_stage
    dirfd = os.open(str(reports), os.O_RDONLY | os.O_DIRECTORY)
    try:
        module._copy_out_unpublished(dirfd, src)
    finally:
        module.os.fchmod = real_fchmod
        os.close(src); os.close(dirfd)
    assert any(p.stat().st_size > 0 for p in reports.iterdir() if p.name.startswith(".scan_report_")) \
        or _findings_anywhere(reports, "docs/int.md:2"), (
        "REPAIRED: a chmod that RAISED on the stage returned before the stream; the source was never "
        "copied, and the caller's close would have been the end of it")


def test_a_failed_held_identity_read_in_quarantine_still_reaches_the_rescue(tmp_path: Path) -> None:
    """REPAIRED (F2): `held = os.fstat(fd)` failing returned False without a rescue attempt."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "quarantine_fstat_fails")
    reports = tmp_path / "_reports"
    reports.mkdir()
    stage = reports / ".scan_report_stage"
    fd = os.open(str(stage), os.O_CREAT | os.O_RDWR, 0o600)
    os.write(fd, b"aws\tkey\tassignment\tdocs/q.md:1\n")
    os.unlink(stage)                                  # the source's only name is gone
    real_fstat = module.os.fstat
    seen: list[int] = []

    def fstat_that_fails_once_in_quarantine(f, *a, **k):
        if f == fd and not seen and sys._getframe(1).f_code.co_name == "_quarantine_unpublished":
            seen.append(f)
            raise OSError(errno.EIO, "injected identity read failure")
        return real_fstat(f, *a, **k)

    module.os.fstat = fstat_that_fails_once_in_quarantine
    dirfd = os.open(str(reports), os.O_RDONLY | os.O_DIRECTORY)
    try:
        module._quarantine_unpublished(dirfd, ".scan_report_stage", fd,
                                       [("docs/q.md", 1, "SECRET", "generic_key_assignment", "c")])
    finally:
        module.os.fstat = real_fstat
        os.close(fd); os.close(dirfd)
    if not seen:
        pytest.skip("the held-identity read never ran; this arm measured nothing")
    assert _findings_anywhere(reports, "docs/q.md:1"), (
        "REPAIRED: one failed metadata read on an already-unnamed source returned False with no "
        "rescue attempt; the close freed the last copy")


def test_the_sweep_leaves_an_entry_whose_ctime_equals_the_stage(tmp_path: Path) -> None:
    """REPAIRED (F3): 'older than' must exclude equality; equal ctimes happen on real filesystems."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "sweep_equal_ctime")
    staging = tmp_path / "staging"
    reports = staging / "_reports"
    reports.mkdir(parents=True)
    other = reports / "scan_report.unpublished.txt"
    other.write_text("gcp\tkey\tassignment\tsrc/other.py:2\n", encoding="utf-8")
    real_fstat = module.os.fstat
    stamp: list[int] = []
    faked: list[int] = []

    def fstat_with_an_equal_ctime_for_the_swept_entry(f, *a, **k):
        st = real_fstat(f, *a, **k)
        caller = sys._getframe(1).f_code.co_name
        if caller == "write_report":
            if not stamp:
                stamp.append(st.st_ctime_ns)          # the reference stamp, read first
                return st
            if stat.S_ISREG(st.st_mode) and st.st_size == other.stat().st_size and not faked:
                faked.append(f)
                fields = (st.st_mode, st.st_ino, st.st_dev, st.st_nlink, st.st_uid, st.st_gid, st.st_size,
                          st.st_atime, st.st_mtime, st.st_ctime)
                return os.stat_result(fields, {"st_atime_ns": st.st_atime_ns, "st_mtime_ns": st.st_mtime_ns,
                                               "st_ctime_ns": stamp[0]})
        return st

    module.os.fstat = fstat_with_an_equal_ctime_for_the_swept_entry
    try:
        module.write_report(str(staging), [])
    finally:
        module.os.fstat = real_fstat
    if not faked:
        pytest.skip("the sweep never read the entry's age; this arm measured nothing")
    assert _findings_anywhere(reports, "src/other.py:2"), (
        "REPAIRED: an entry whose ctime EQUALS the stage's was swept as 'older'; a concurrent "
        "writer's retained findings created in the same tick are deleted")


def test_the_rescue_strips_its_source_as_well_as_its_copy(tmp_path: Path) -> None:
    """REPAIRED (F4): the rescue fchmods its source and never strips it; a surviving alias keeps the ACL."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "rescue_source_strip")
    if not module._XATTR_SUPPORTED:
        pytest.skip("no xattr layer here")
    reports = tmp_path / "_reports"
    reports.mkdir()
    src = _rescue_source(reports)
    real_strip = module._strip_acl_by_fd
    stripped: list[int] = []
    module._strip_acl_by_fd = lambda fd: stripped.append(fd)
    dirfd = os.open(str(reports), os.O_RDONLY | os.O_DIRECTORY)
    try:
        module._copy_out_unpublished(dirfd, src)
    finally:
        module._strip_acl_by_fd = real_strip
        os.close(src); os.close(dirfd)
    assert src in stripped, (
        "REPAIRED: the rescue narrowed its source by mode alone; an alias of that inode left behind "
        "keeps whatever ACL its directory gave it")


def test_a_partial_stage_kept_through_a_cancelled_size_read_is_still_narrowed(tmp_path: Path) -> None:
    """REPAIRED (F4b): the narrowing must sit in cleanup that still runs when the size read is cancelled."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "partial_keep_cancel_narrow")
    if not module._XATTR_SUPPORTED:
        pytest.skip("no xattr layer here")
    reports = tmp_path / "_reports"
    reports.mkdir()
    real_open, real_fdopen, real_strip = module.os.open, module.os.fdopen, module._strip_acl_by_fd
    opened: list[int] = []
    stripped: list[int] = []

    def open_and_record(path, flags, *a, **k):
        fd = real_open(path, flags, *a, **k)
        if sys._getframe(1).f_code.co_name == "_stage_report":
            opened.append(fd)
        return fd

    class _HalfWriter:
        def __init__(self, wrapped): self._wrapped = wrapped
        def __enter__(self): return self
        def __exit__(self, *exc): return self._wrapped.__exit__(*exc)
        def write(self, data):
            self._wrapped.write(data[: max(1, len(data) // 2)]); self._wrapped.flush()
            raise OSError(errno.EFBIG, "injected write failure")

    seen: list[int] = []
    real_fstat, cancelling = _fstat_that_cancels(module, opened, "_stage_report", seen)
    module.os.open, module.os.fdopen, module.os.fstat = open_and_record, (lambda *a, **k: _HalfWriter(real_fdopen(*a, **k))), cancelling
    module._strip_acl_by_fd = lambda fd: stripped.append(fd)
    dirfd = os.open(str(reports), os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(KeyboardInterrupt):
            module._stage_report(dirfd, "aws\tkey\tassignment\tdocs/x.md:1\n", evidence=True)
    finally:
        module.os.open, module.os.fdopen, module.os.fstat, module._strip_acl_by_fd = real_open, real_fdopen, real_fstat, real_strip
        os.close(dirfd)
    if not seen:
        pytest.skip("the size read never ran; this arm measured nothing")
    _assert_closed(seen[0], "the partial stage's descriptor (cancelled during the size read)")
    assert seen[0] in stripped, (
        "REPAIRED: the partial stage kept through a cancelled size read was never narrowed — the "
        "narrowing sat after the read instead of in the cleanup that runs regardless")


# =============================================================================================
# GROUP 48 — the thirty-ninth round. The cold leg's three on 0c28c5e: a reserved name dropped
# by someone else after the link lets the stage removal take the LAST name; the rescue binds a
# reserved name when its strip was denied; and the primary stage is written before any policy.
# =============================================================================================


def test_quarantine_keeps_its_stage_when_the_reserved_name_is_gone_by_removal_time(tmp_path: Path) -> None:
    """REPAIRED (cold #1A): removing the stage must require that another name still reaches the inode."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "quarantine_last_name")
    reports = tmp_path / "_reports"
    reports.mkdir()
    fd = os.open(str(reports / ".scan_report_stage"), os.O_CREAT | os.O_RDWR, 0o600)
    os.write(fd, b"aws\tkey\tassignment\tdocs/q.md:1\n")
    real_link = module._link_held_inode
    swapped: list[str] = []

    def link_then_lose_the_reserved_name(f, candidate, dirfd):
        result = real_link(f, candidate, dirfd)
        if not swapped:
            swapped.append(candidate)
            os.unlink(reports / candidate)          # another process ends the reserved name
        return result

    module._link_held_inode = link_then_lose_the_reserved_name
    dirfd = os.open(str(reports), os.O_RDONLY | os.O_DIRECTORY)
    try:
        module._quarantine_unpublished(dirfd, ".scan_report_stage", fd,
                                       [("docs/q.md", 1, "SECRET", "generic_key_assignment", "c")])
    finally:
        module._link_held_inode = real_link
        os.close(fd); os.close(dirfd)
    if not swapped:
        pytest.skip("quarantine never linked; this arm measured nothing")
    assert _findings_anywhere(reports, "docs/q.md:1"), (
        "REPAIRED: the reserved name was gone by the time the stage was removed; the removal took "
        "the inode's LAST name and the close freed the findings")


def test_the_rescue_keeps_its_stage_when_the_reserved_name_is_gone_by_removal_time(tmp_path: Path) -> None:
    """REPAIRED (cold #1B): the same rule in the rescue's finally."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "rescue_last_name")
    reports = tmp_path / "_reports"
    reports.mkdir()
    src = _rescue_source(reports)
    os.unlink(reports / ".scan_report_src")
    real_link = module._link_held_inode
    swapped: list[str] = []

    def link_then_lose_the_reserved_name(f, candidate, dirfd):
        result = real_link(f, candidate, dirfd)
        if not swapped:
            swapped.append(candidate)
            os.unlink(reports / candidate)
        return result

    module._link_held_inode = link_then_lose_the_reserved_name
    dirfd = os.open(str(reports), os.O_RDONLY | os.O_DIRECTORY)
    try:
        module._copy_out_unpublished(dirfd, src)
    finally:
        module._link_held_inode = real_link
        os.close(src); os.close(dirfd)
    if not swapped:
        pytest.skip("the rescue never linked; this arm measured nothing")
    assert _findings_anywhere(reports, "docs/int.md:2"), (
        "REPAIRED: the rescue's copy lost its reserved name before the stage was removed; the "
        "removal took the copy's LAST name and nothing survived")


def test_the_rescue_declines_a_reserved_name_when_the_strip_is_denied(tmp_path: Path) -> None:
    """REPAIRED (cold #2): a reserved name asserts the policy; a denied strip means it is not on the file."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "rescue_strip_denied")
    if not module._XATTR_SUPPORTED:
        pytest.skip("no xattr layer here")
    reports = tmp_path / "_reports"
    reports.mkdir()
    src = _rescue_source(reports)
    os.unlink(reports / ".scan_report_src")
    real_strip = module._strip_acl_by_fd

    def strip_denied_on_the_stage(fd):
        if fd != src:
            raise PermissionError(errno.EPERM, "injected: strip denied")

    module._strip_acl_by_fd = strip_denied_on_the_stage
    dirfd = os.open(str(reports), os.O_RDONLY | os.O_DIRECTORY)
    try:
        published = module._copy_out_unpublished(dirfd, src)
    finally:
        module._strip_acl_by_fd = real_strip
        os.close(src); os.close(dirfd)
    assert _findings_anywhere(reports, "docs/int.md:2"), "CONTROL: the bytes must survive under some name"
    assert not any(p.name.startswith("scan_report.unpublished") for p in reports.iterdir()), (   # the answer is True since round fifty: a complete copy is KEPT
        "REPAIRED: the rescue bound a reserved name to a copy whose ACL strip was denied — the "
        "name asserts a policy that is not on the file; quarantine already declines in this case")


def test_the_primary_stage_is_narrowed_before_its_first_byte(tmp_path: Path) -> None:
    """REPAIRED (cold #3): policy-before-bytes was applied to the rescue copy and not to the primary write."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "stage_policy_first")
    if not module._XATTR_SUPPORTED:
        pytest.skip("no xattr layer here")
    reports = tmp_path / "_reports"
    reports.mkdir()
    real_strip, real_fdopen = module._strip_acl_by_fd, module.os.fdopen
    order: list[str] = []
    module._strip_acl_by_fd = lambda fd: order.append("strip")
    module.os.fdopen = lambda *a, **k: (order.append("write"), real_fdopen(*a, **k))[1]
    dirfd = os.open(str(reports), os.O_RDONLY | os.O_DIRECTORY)
    try:
        fd, name = module._stage_report(dirfd, "aws\tkey\tassignment\tdocs/x.md:1\n", evidence=True)
        os.close(fd)
    finally:
        module._strip_acl_by_fd, module.os.fdopen = real_strip, real_fdopen
        os.close(dirfd)
    assert "write" in order, "CONTROL: the stage must have been written"
    assert "strip" in order and order.index("strip") < order.index("write"), (
        f"REPAIRED: the primary stage was written before any strip or chmod ({order}); with a default "
        f"ACL on the directory the findings are group-readable under the temporary name until the "
        f"policy install")


def test_a_terms_file_with_no_terms_does_not_match_everything(tmp_path: Path) -> None:
    """REPAIRED (cold, unranked): an empty alternation matches the empty string at every position."""
    driver = make_tool(tmp_path)
    (driver.parent / "identity_terms.txt").write_text("# no terms yet\n\n", encoding="utf-8")
    module = import_driver(driver, "empty_terms")
    if not module._load_identity_terms.__code__.co_filename:
        pytest.skip("cannot locate the driver")
    if module._load_identity_terms():
        pytest.skip("the fixture did not yield an empty term list; this arm measured nothing")
    name, pattern = module.personal_patterns()[0]
    assert name == "owner-identity"
    assert pattern.search("nothing personal in this line") is None, (
        "REPAIRED: with no identity terms the owner-identity pattern is an empty alternation and "
        "matches every line — every file in the tree becomes a PERSONAL hit")


# =============================================================================================
# GROUP 49 — the fortieth round. An executed on-box review of 461db53 (33 schedules, one FAIL):
# quarantine's failure returns AFTER its identity check trusted the name; a decoy renamed onto
# the staged name during the failing call, plus the failure itself, left the findings unnamed.
# =============================================================================================


def _quarantine_with_decoy_during(module, reports: Path, patch_name: str, failing):
    """Run quarantine on staged findings; during `patch_name`'s call, rename a decoy onto the staged
    name and then let the call fail as `failing` says. Returns (quarantine result, fired)."""
    stage = reports / ".scan_report_stage"
    fd = os.open(str(stage), os.O_CREAT | os.O_RDWR, 0o600)
    os.write(fd, b"aws\tkey\tassignment\tdocs/q.md:1\n")
    real = getattr(module if patch_name.startswith("_") else module.os, patch_name)
    fired: list[str] = []
    calls: list[int] = []
    # fstat is called twice inside quarantine: the identity read BEFORE the check, and the mode
    # verify AFTER it. Only the second is the post-check window; firing on the first measures
    # a different, already-closed schedule.
    fire_on = 2 if patch_name == "fstat" else 1

    def decoy_then_fail(*args, **kwargs):
        if sys._getframe(1).f_code.co_name == "_quarantine_unpublished":
            calls.append(1)
        if not fired and len(calls) == fire_on and sys._getframe(1).f_code.co_name == "_quarantine_unpublished":
            fired.append(patch_name)
            decoy = reports / "decoy.txt"
            decoy.write_text("scan_gate: CLEAN\n", encoding="utf-8")
            os.replace(decoy, stage)                  # the staged NAME is now the decoy
            return failing(real, *args, **kwargs)
        return real(*args, **kwargs)

    target = module if patch_name.startswith("_") else module.os
    setattr(target, patch_name, decoy_then_fail)
    dirfd = os.open(str(reports), os.O_RDONLY | os.O_DIRECTORY)
    try:
        result = module._quarantine_unpublished(dirfd, ".scan_report_stage", fd,
                                                [("docs/q.md", 1, "SECRET", "generic_key_assignment", "c")])
    finally:
        setattr(target, patch_name, real)
        os.close(fd); os.close(dirfd)
    return result, fired


@pytest.mark.parametrize("patch_name,failing,label", [
    ("_strip_acl_by_fd", lambda real, *a, **k: (_ for _ in ()).throw(PermissionError(errno.EPERM, "strip denied")), "strip denied"),
    ("fchmod", lambda real, *a, **k: (_ for _ in ()).throw(PermissionError(errno.EPERM, "fchmod failed")), "fchmod failed"),
    ("fstat", lambda real, fd, *a, **k: os.stat_result((0o100644,) + tuple(real(fd, *a, **k))[1:10]), "mode verify failed"),
])
def test_quarantine_rescues_when_a_failure_after_its_identity_check_finds_the_name_taken(
        tmp_path: Path, patch_name, failing, label) -> None:
    """REPAIRED: a failure after the identity check must re-ask whether the name is still ours."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "quarantine_post_check_" + patch_name)
    if patch_name == "_strip_acl_by_fd" and not module._XATTR_SUPPORTED:
        pytest.skip("no xattr layer here")
    reports = tmp_path / "_reports"
    reports.mkdir()
    result, fired = _quarantine_with_decoy_during(module, reports, patch_name, failing)
    if not fired:
        pytest.skip(f"the {label} injection never fired inside quarantine; this arm measured nothing")
    assert _findings_anywhere(reports, "docs/q.md:1"), (
        f"REPAIRED ({label}): the staged name became a decoy during the failing call; quarantine "
        f"returned False trusting the name it had checked earlier, and the findings are under no name")


# =============================================================================================
# GROUP 50 — the forty-second round. Gate 37: an invariant-framed leg (Gemini, reading the module
# as a file) found the nlink rule of round 39 refusing to remove an EMPTY rescue stage, which has
# never been linked and so has exactly one name.
# =============================================================================================


def test_an_empty_rescue_stage_is_removed(tmp_path: Path) -> None:
    """REPAIRED: an empty stage holds nothing; identity alone should authorize removing its one name."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "empty_rescue_stage")
    reports = tmp_path / "_reports"
    reports.mkdir()
    src = os.open(str(reports / ".scan_report_src"), os.O_CREAT | os.O_RDONLY, 0o600)   # zero bytes
    dirfd = os.open(str(reports), os.O_RDONLY | os.O_DIRECTORY)
    try:
        published = module._copy_out_unpublished(dirfd, src)
    finally:
        os.close(src); os.close(dirfd)
    assert published is False, "CONTROL: nothing readable cannot be published"
    leftovers = [p.name for p in reports.iterdir() if p.name.startswith(".scan_report_") and p.name != ".scan_report_src"]
    assert not leftovers, (
        f"REPAIRED: the rescue read zero bytes, cleared keep_stage, and the finally's nlink rule then "
        f"refused to remove a stage that has only ever had one name: {leftovers} leaked")


def test_a_stale_clean_is_still_replaced_where_the_xattr_api_is_absent(tmp_path: Path) -> None:
    """CONTROL for a review claim: without the xattr API, the refusal still replaces a stale CLEAN.

    A gate-37 leg read `_narrow_held_copy` returning False on such platforms and concluded the
    refusal writer "always aborts" there. The status-line branch of preservation answers True
    before narrowing is consulted, so a stale CLEAN — the case the refusal exists for — is still
    replaced; only a FINDINGS report is left unreplaced there, which is the safe direction.
    """
    driver = make_tool(tmp_path)
    module = import_driver(driver, "no_xattr_refusal")
    reports = tmp_path / "_reports"
    reports.mkdir()
    canonical = reports / "scan_report.txt"
    canonical.write_bytes(module._STATUS_LINE_PREFIX + b"CLEAN\n")
    module._XATTR_SUPPORTED = False
    module._write_refusal_report(str(tmp_path), module.ScanRefused("report-path-unsafe 'x'"))
    assert canonical.read_bytes().startswith(b"scan_gate: REFUSED"), (
        "the refusal did not replace a stale CLEAN on a platform without the xattr API")


def test_a_cancellation_during_the_sweep_does_not_quarantine_the_published_report(tmp_path: Path) -> None:
    """REPAIRED (executed review, gate 37): after the replace the stage IS the report; the failure handler
    must not copy it out again under a reserved name."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "sweep_cancel_no_duplicate")
    staging = tmp_path / "staging"
    reports = staging / "_reports"
    reports.mkdir(parents=True)
    old = reports / "scan_report.superseded.txt"
    old.write_text("aws\tkey\tassignment\tdocs/old.md:1\n", encoding="utf-8")
    os.utime(old, ns=(1, 1))
    real_open = module.os.open
    fired: list[str] = []

    def open_that_cancels_the_sweep(path, flags, *a, **k):
        if (not fired and isinstance(path, str) and path.startswith("scan_report.superseded")
                and flags & getattr(os, "O_PATH", 0) and sys._getframe(1).f_code.co_name == "write_report"):
            fired.append(path)
            raise KeyboardInterrupt
        return real_open(path, flags, *a, **k)

    module.os.open = open_that_cancels_the_sweep
    try:
        with pytest.raises(KeyboardInterrupt):
            module.write_report(str(staging), [("docs/new.md", 2, "SECRET", "generic_key_assignment", "contents")])
    finally:
        module.os.open = real_open
    if not fired:
        pytest.skip("the sweep never opened the entry; this arm measured nothing")
    assert (reports / "scan_report.txt").read_text(encoding="utf-8").count("docs/new.md") == 1, "CONTROL: published"
    dupes = [p.name for p in reports.iterdir() if p.name.startswith("scan_report.unpublished")]
    assert not dupes, (
        f"REPAIRED: the report was already published when the sweep was cancelled; the handler "
        f"quarantined the renamed stage by descriptor and left a duplicate under {dupes}")


# ---- gate 37, the cold leg: the round-40 swap moved one function down -----------------------

def test_a_decoy_swapped_in_during_the_kept_stage_narrowing_does_not_free_the_findings(tmp_path: Path) -> None:
    """REPAIRED (cold #1): after `_false_or_rescue` says False, the caller narrows and closes; a swap
    during the narrowing must be caught before the close."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "narrow_then_swap")
    if not module._XATTR_SUPPORTED:
        pytest.skip("no xattr layer here")
    staging = tmp_path / "staging"
    reports = staging / "_reports"
    reports.mkdir(parents=True)
    real_install, real_strip, real_narrow = module._install_posix_acl_policy, module._strip_acl_by_fd, module._narrow_leftover
    swapped: list[str] = []

    def install_that_fails(dirfd, src_name, dst_fd, dst_name):
        raise OSError(5, "injected policy failure")

    def strip_denied(fd):
        raise PermissionError(errno.EPERM, "injected: strip denied")

    def narrow_then_swap_the_name(fd):
        real_narrow(fd)
        if not swapped and sys._getframe(1).f_code.co_name == "write_report":
            stages = [p for p in reports.iterdir() if p.name.startswith(".scan_report_")]
            if len(stages) == 1:
                swapped.append(stages[0].name)
                decoy = reports / "decoy.txt"
                decoy.write_text("scan_gate: CLEAN\n", encoding="utf-8")
                os.replace(decoy, stages[0])          # the kept NAME is now the decoy

    module._install_posix_acl_policy, module._strip_acl_by_fd, module._narrow_leftover = install_that_fails, strip_denied, narrow_then_swap_the_name
    try:
        with pytest.raises(Exception):
            module.write_report(str(staging), [("docs/k.md", 1, "SECRET", "generic_key_assignment", "contents")])
    finally:
        module._install_posix_acl_policy, module._strip_acl_by_fd, module._narrow_leftover = real_install, real_strip, real_narrow
    if not swapped:
        pytest.skip("the kept-stage narrowing never ran; this arm measured nothing")
    assert _findings_anywhere(reports, "docs/k.md:1"), (
        "REPAIRED: the name was swapped for a decoy during the kept-stage narrowing; the caller then "
        "closed the last reference and the findings are under no name")


def test_the_post_check_identity_helper_reads_the_held_side_first(tmp_path: Path) -> None:
    """REPAIRED (cold #1, sub-issue): a swap between the helper's two syscalls must be caught, so the
    name lookup has to be the LAST of the two."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "helper_order")
    reports = tmp_path / "_reports"
    reports.mkdir()
    stage = reports / ".scan_report_stage"
    fd = os.open(str(stage), os.O_CREAT | os.O_RDWR, 0o600)
    os.write(fd, b"aws\tkey\tassignment\tdocs/q.md:1\n")
    real_lstat, real_fstat = module.os.lstat, module.os.fstat
    calls: list[str] = []

    def swap_before_the_second_syscall():
        if len(calls) == 2:
            decoy = reports / "decoy.txt"
            decoy.write_text("scan_gate: CLEAN\n", encoding="utf-8")
            os.replace(decoy, stage)

    def lstat_counted(path, *a, **k):
        if sys._getframe(1).f_code.co_name == "_false_or_rescue":
            calls.append("lstat"); swap_before_the_second_syscall()
        return real_lstat(path, *a, **k)

    def fstat_counted(f, *a, **k):
        if sys._getframe(1).f_code.co_name == "_false_or_rescue":
            calls.append("fstat"); swap_before_the_second_syscall()
        return real_fstat(f, *a, **k)

    module.os.lstat, module.os.fstat = lstat_counted, fstat_counted
    dirfd = os.open(str(reports), os.O_RDONLY | os.O_DIRECTORY)
    try:
        answer = module._false_or_rescue(dirfd, ".scan_report_stage", fd)
    finally:
        module.os.lstat, module.os.fstat = real_lstat, real_fstat
        os.close(fd); os.close(dirfd)
    if len(calls) < 2:
        pytest.skip("the helper made fewer than two identity syscalls; this arm measured nothing")
    assert calls[-1] == "lstat", f"REPAIRED: the name lookup must be the last syscall, got {calls}"
    assert answer is True or _findings_anywhere(reports, "docs/q.md:1"), (
        "REPAIRED: a swap between the two identity syscalls was missed — the helper compared a stale "
        "name lookup and answered False")


# =============================================================================================
# GROUP 51 — the forty-third round. Gate 38's invariant leg (Gemini): with the ACL strip denied
# (or no xattr API at all), each refusal over the SAME findings inode took a fresh preservation
# slot — eight refusals of one report exhaust the capacity the README calls finite.
# =============================================================================================


def test_the_same_findings_inode_never_takes_a_second_slot(tmp_path: Path) -> None:
    """REPAIRED: a second name for an inode already in a slot gains nothing and costs a slot."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "one_slot_per_inode")
    if not module._XATTR_SUPPORTED:
        pytest.skip("no xattr layer here")
    reports = tmp_path / "_reports"
    reports.mkdir()
    (reports / "scan_report.txt").write_text("aws\tkey\tassignment\tdocs/A.md:1\n", encoding="utf-8")
    real_strip = module._strip_acl_by_fd

    def strip_denied(fd):
        raise PermissionError(errno.EPERM, "injected: strip denied")

    module._strip_acl_by_fd = strip_denied
    try:
        for _ in range(3):
            module._write_refusal_report(str(tmp_path), module.ScanRefused("report-path-unsafe 'x'"))
    finally:
        module._strip_acl_by_fd = real_strip
    slots = sorted(p.name for p in reports.iterdir() if p.name.startswith("scan_report.superseded"))
    assert _findings_anywhere(reports, "docs/A.md:1"), "CONTROL: the findings must survive the refusals"
    assert len(slots) <= 1, (
        f"REPAIRED: three refusals over ONE findings inode took {len(slots)} slots ({slots}); a "
        f"report whose policy cannot be installed must not consume the finite capacity once per refusal")
    # Since round fifty-three a report whose policy cannot be installed takes NO reserved name at
    # all (the inode is narrowed before it is linked, and a denied strip declines the link), so
    # the count here is zero; the arm pins "never a second slot", not "exactly one".


# =============================================================================================
# GROUP 52 — the forty-fourth round. Gate 38's cold leg (grok) on d7e4a3c, an executed on-box
# review of d7e4a3c, and gate 39's invariant leg (Gemini) on f69cfff: the stage writer's own
# keep branch narrowed and then closed without re-checking the name; the identity-unlink helper
# read the name before the held inode; a rescue whose first read failed left an empty stage
# under retention; and the post-publish sweep opened reserved names without O_NONBLOCK where
# O_PATH is absent.
# =============================================================================================


def test_a_swap_during_the_stage_writers_own_keep_narrowing_does_not_free_the_findings(tmp_path: Path) -> None:
    """REPAIRED (cold #1, gate 38): `_stage_report`'s handler is the only holder of a partial findings
    body; after it narrows the kept stage it must re-check the name before the close, as write_report does."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "stage_keep_swap")
    if module._PROC_FD_DIR is None:
        pytest.skip("no descriptor directory here; the rescue cannot run")
    reports = tmp_path / "_reports"
    reports.mkdir()
    real_fdopen, real_narrow = module.os.fdopen, module._narrow_leftover
    narrowed: list[int] = []
    swapped: list[str] = []

    class _HalfWriter:
        def __init__(self, wrapped): self._wrapped = wrapped
        def __enter__(self): return self
        def __exit__(self, *exc): return self._wrapped.__exit__(*exc)
        def write(self, data):
            self._wrapped.write(data[: max(1, len(data) // 2)]); self._wrapped.flush()
            raise OSError(errno.EFBIG, "injected write failure")

    def narrow_then_swap_on_the_keep_call(fd):
        real_narrow(fd)
        if sys._getframe(1).f_code.co_name == "_stage_report":
            narrowed.append(fd)
            if len(narrowed) == 2 and not swapped:          # 1st: before the first byte; 2nd: the keep branch
                stages = [p for p in reports.iterdir() if p.name.startswith(".scan_report_")]
                if len(stages) == 1:
                    swapped.append(stages[0].name)
                    decoy = reports / "decoy.txt"
                    decoy.write_text("scan_gate: CLEAN\n", encoding="utf-8")
                    os.replace(decoy, stages[0])          # the kept NAME is now the decoy

    module.os.fdopen = lambda *a, **k: _HalfWriter(real_fdopen(*a, **k))
    module._narrow_leftover = narrow_then_swap_on_the_keep_call
    dirfd = os.open(str(reports), os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(OSError):
            module._stage_report(dirfd, "aws\tkey\tassignment\tdocs/x.md:1\n" * 40, evidence=True)
    finally:
        module.os.fdopen, module._narrow_leftover = real_fdopen, real_narrow
        os.close(dirfd)
    if not swapped:
        pytest.skip("the keep-branch narrowing never ran; this arm measured nothing")
    assert _findings_anywhere(reports, "docs/x.md:1"), (
        "REPAIRED: the stage name was swapped for a decoy during the keep-branch narrowing and the "
        "handler then closed the last reference; the partial findings are under no name")


def test_the_identity_unlink_helper_reads_the_held_side_first(tmp_path: Path) -> None:
    """REPAIRED (cold #2, gate 38): `_remove_own_stage` compared a name lookup taken BEFORE the held
    fstat; a rename onto the name between the two removed a foreign findings inode's last name."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "unlink_helper_order")
    reports = tmp_path / "_reports"
    reports.mkdir()
    stage = reports / ".scan_report_stage"
    fd = os.open(str(stage), os.O_CREAT | os.O_RDWR, 0o600)               # OUR inode: empty
    foreign = reports / "scan_report.unpublished.txt"
    foreign.write_text("aws\tkey\tassignment\tdocs/q.md:1\n", encoding="utf-8")   # someone else's last name
    real_lstat, real_fstat = module.os.lstat, module.os.fstat
    calls: list[str] = []

    def swap_before_the_second_syscall():
        if len(calls) == 2:
            os.replace(foreign, stage)                 # the findings inode now lives at OUR stage name

    def lstat_counted(path, *a, **k):
        if sys._getframe(1).f_code.co_name == "_remove_own_stage":
            calls.append("lstat"); swap_before_the_second_syscall()
        return real_lstat(path, *a, **k)

    def fstat_counted(f, *a, **k):
        if sys._getframe(1).f_code.co_name == "_remove_own_stage":
            calls.append("fstat"); swap_before_the_second_syscall()
        return real_fstat(f, *a, **k)

    module.os.lstat, module.os.fstat = lstat_counted, fstat_counted
    dirfd = os.open(str(reports), os.O_RDONLY | os.O_DIRECTORY)
    try:
        module._remove_own_stage(dirfd, ".scan_report_stage", fd)
    finally:
        module.os.lstat, module.os.fstat = real_lstat, real_fstat
        os.close(fd); os.close(dirfd)
    if len(calls) < 2:
        pytest.skip("the helper made fewer than two identity syscalls; this arm measured nothing")
    assert calls[-1] == "lstat", f"REPAIRED: the name lookup must be the last syscall, got {calls}"
    assert _findings_anywhere(reports, "docs/q.md:1"), (
        "REPAIRED: a findings inode renamed onto the stage name between the helper's two identity "
        "syscalls lost its last name — the helper unlinked on a stale name lookup")


@pytest.mark.parametrize("failure", ["read_raises", "write_no_progress"])
def test_a_rescue_that_fails_before_its_first_byte_leaves_no_empty_stage(tmp_path: Path, failure: str) -> None:
    """REPAIRED (executed review, gate 38): a read that raises, or a write that makes no progress, before
    any byte reached the stage returned with retention on — an empty 0-byte stage stayed forever."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "rescue_first_" + failure)
    if module._PROC_FD_DIR is None:
        pytest.skip("no descriptor directory here; the rescue cannot run")
    reports = tmp_path / "_reports"
    reports.mkdir()
    src = os.open(str(reports / ".scan_report_src"), os.O_CREAT | os.O_WRONLY, 0o600)
    os.write(src, b"aws\tkey\tassignment\tdocs/first.md:1\n"); os.close(src)
    src = os.open(str(reports / ".scan_report_src"), os.O_RDONLY)
    real_read, real_write = module.os.read, module.os.write
    fired: list[str] = []

    def read_that_raises(fd, n):
        if sys._getframe(1).f_code.co_name == "_copy_out_unpublished" and not fired:
            fired.append("read"); raise OSError(5, "injected first-read failure")
        return real_read(fd, n)

    def write_no_progress(fd, data):
        if sys._getframe(1).f_code.co_name == "_copy_out_unpublished" and not fired:
            fired.append("write"); return 0
        return real_write(fd, data)

    if failure == "read_raises":
        module.os.read = read_that_raises
    else:
        module.os.write = write_no_progress
    dirfd = os.open(str(reports), os.O_RDONLY | os.O_DIRECTORY)
    try:
        published = module._copy_out_unpublished(dirfd, src)
    finally:
        module.os.read, module.os.write = real_read, real_write
        os.close(src); os.close(dirfd)
    if not fired:
        pytest.skip("the injected failure never fired; this arm measured nothing")
    assert published is False, "CONTROL: a copy that moved no bytes must not report custody"
    leftovers = [p.name for p in reports.iterdir() if p.name.startswith(".scan_report_") and p.name != ".scan_report_src"]
    assert not leftovers, (
        f"REPAIRED ({failure}): nothing reached the stage, yet it was kept under retention: {leftovers}")
    assert (reports / ".scan_report_src").read_bytes().startswith(b"aws"), "CONTROL: the source is untouched"


def test_the_sweep_does_not_block_on_a_fifo_where_o_path_is_absent(tmp_path: Path) -> None:
    """REPAIRED (invariant leg, gate 39): without O_PATH the sweep opened reserved names O_RDONLY, and
    a FIFO planted at one blocked the open until a writer appeared — the scanner hung after publishing."""
    import threading, time
    driver = make_tool(tmp_path)
    module = import_driver(driver, "sweep_fifo_no_opath")
    if not hasattr(os, "O_PATH") or not hasattr(os, "mkfifo"):
        pytest.skip("this arm simulates the absence of O_PATH on a platform that has it")
    staging = tmp_path / "staging"
    reports = staging / "_reports"
    reports.mkdir(parents=True)
    fifo = reports / next(iter(module._superseded_slot_names(include_unpublished=True)))
    os.mkfifo(str(fifo), 0o600)
    time.sleep(0.05)                                    # strictly older than this run's stage
    saved = os.O_PATH
    del os.O_PATH                                       # module.os IS os: the fallback branch runs
    outcome: list[object] = []

    def run():
        try:
            outcome.append(module.write_report(str(staging), [("docs/f.md", 1, "SECRET", "generic_key_assignment", "contents")]))
        except BaseException as exc:                    # noqa: BLE001 — recorded, asserted below
            outcome.append(exc)

    worker = threading.Thread(target=run, daemon=True)
    try:
        worker.start()
        worker.join(5.0)
        hung = worker.is_alive()
        if hung:
            try:
                unblock = os.open(str(fifo), os.O_WRONLY | os.O_NONBLOCK)   # let the blocked open return
                os.close(unblock)
            except OSError:
                pass
            worker.join(5.0)
    finally:
        os.O_PATH = saved
    assert not hung, "REPAIRED: the post-publish sweep blocked opening a FIFO at a reserved name (no O_PATH, no O_NONBLOCK)"
    assert outcome and not isinstance(outcome[0], BaseException), f"CONTROL: publication must succeed: {outcome}"
    assert (reports / "scan_report.txt").read_text(encoding="utf-8").find("docs/f.md:1") >= 0, "CONTROL: the report was published"


# =============================================================================================
# GROUP 53 — the forty-fifth round. Gate 40 on 4632326: the invariant leg (Gemini) found the
# round-44 re-check in `_stage_report` skipped by a cancellation inside the narrowing it follows;
# the executed review found the round-44 empty-stage release skipped by a cancellation before
# the rescue's first byte.
# =============================================================================================


def test_a_cancellation_inside_the_keep_narrowing_still_rescues_a_swapped_stage(tmp_path: Path) -> None:
    """REPAIRED (invariant leg, gate 40): the re-check after the narrowing must run in cleanup that a
    cancellation inside the narrowing cannot skip — otherwise the close frees a swapped stage."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "stage_keep_narrow_cancel")
    if module._PROC_FD_DIR is None:
        pytest.skip("no descriptor directory here; the rescue cannot run")
    reports = tmp_path / "_reports"
    reports.mkdir()
    real_fdopen, real_narrow = module.os.fdopen, module._narrow_leftover
    narrowed: list[int] = []
    swapped: list[str] = []

    class _HalfWriter:
        def __init__(self, wrapped): self._wrapped = wrapped
        def __enter__(self): return self
        def __exit__(self, *exc): return self._wrapped.__exit__(*exc)
        def write(self, data):
            self._wrapped.write(data[: max(1, len(data) // 2)]); self._wrapped.flush()
            raise OSError(errno.EFBIG, "injected write failure")

    def swap_then_cancel_on_the_keep_call(fd):
        real_narrow(fd)
        if sys._getframe(1).f_code.co_name == "_stage_report":
            narrowed.append(fd)
            if len(narrowed) == 2 and not swapped:
                stages = [p for p in reports.iterdir() if p.name.startswith(".scan_report_")]
                if len(stages) == 1:
                    swapped.append(stages[0].name)
                    decoy = reports / "decoy.txt"
                    decoy.write_text("scan_gate: CLEAN\n", encoding="utf-8")
                    os.replace(decoy, stages[0])          # the kept NAME is now the decoy
                    raise KeyboardInterrupt               # …and the narrowing is cancelled

    module.os.fdopen = lambda *a, **k: _HalfWriter(real_fdopen(*a, **k))
    module._narrow_leftover = swap_then_cancel_on_the_keep_call
    dirfd = os.open(str(reports), os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(KeyboardInterrupt):
            module._stage_report(dirfd, "aws\tkey\tassignment\tdocs/y.md:1\n" * 40, evidence=True)
    finally:
        module.os.fdopen, module._narrow_leftover = real_fdopen, real_narrow
        os.close(dirfd)
    if not swapped:
        pytest.skip("the keep-branch narrowing never ran; this arm measured nothing")
    assert _findings_anywhere(reports, "docs/y.md:1"), (
        "REPAIRED: a cancellation inside the keep-branch narrowing skipped the re-check; the handler "
        "closed the last reference to a swapped stage and the partial findings are under no name")


def test_a_cancellation_before_the_rescues_first_byte_leaves_no_empty_stage(tmp_path: Path) -> None:
    """REPAIRED (executed review, gate 40): retention-on-cancellation is for bytes; an empty stage is
    not evidence under a cancellation either, and is removed by identity."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "rescue_first_read_cancel")
    if module._PROC_FD_DIR is None:
        pytest.skip("no descriptor directory here; the rescue cannot run")
    reports = tmp_path / "_reports"
    reports.mkdir()
    src = os.open(str(reports / ".scan_report_src"), os.O_CREAT | os.O_WRONLY, 0o600)
    os.write(src, b"aws\tkey\tassignment\tdocs/first.md:1\n"); os.close(src)
    src = os.open(str(reports / ".scan_report_src"), os.O_RDONLY)
    real_read = module.os.read
    fired: list[str] = []

    def read_cancelled(fd, n):
        if sys._getframe(1).f_code.co_name == "_copy_out_unpublished" and not fired:
            fired.append("read"); raise KeyboardInterrupt
        return real_read(fd, n)

    module.os.read = read_cancelled
    dirfd = os.open(str(reports), os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(KeyboardInterrupt):
            module._copy_out_unpublished(dirfd, src)
    finally:
        module.os.read = real_read
        os.close(src); os.close(dirfd)
    if not fired:
        pytest.skip("the cancellation never fired; this arm measured nothing")
    leftovers = [p.name for p in reports.iterdir() if p.name.startswith(".scan_report_") and p.name != ".scan_report_src"]
    assert not leftovers, f"REPAIRED: nothing reached the stage, yet the cancellation kept it: {leftovers}"
    assert (reports / ".scan_report_src").read_bytes().startswith(b"aws"), "CONTROL: the source is untouched"


def test_a_cancellation_inside_the_callers_kept_stage_narrowing_still_rescues(tmp_path: Path) -> None:
    """REPAIRED (cold #1, gate 40): write_report's kept-stage branch has the same narrow-then-re-check pair;
    an interrupt inside the narrowing must not skip the re-check there either."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "caller_keep_narrow_cancel")
    if not module._XATTR_SUPPORTED or module._PROC_FD_DIR is None:
        pytest.skip("no xattr layer or descriptor directory here")
    staging = tmp_path / "staging"
    reports = staging / "_reports"
    reports.mkdir(parents=True)
    real_install, real_strip, real_narrow = module._install_posix_acl_policy, module._strip_acl_by_fd, module._narrow_leftover
    swapped: list[str] = []

    def install_that_fails(dirfd, src_name, dst_fd, dst_name):
        raise OSError(5, "injected policy failure")

    def strip_denied(fd):
        raise PermissionError(errno.EPERM, "injected: strip denied")

    def narrow_swap_then_cancel(fd):
        real_narrow(fd)
        if not swapped and sys._getframe(1).f_code.co_name == "write_report":
            stages = [p for p in reports.iterdir() if p.name.startswith(".scan_report_")]
            if len(stages) == 1:
                swapped.append(stages[0].name)
                decoy = reports / "decoy.txt"
                decoy.write_text("scan_gate: CLEAN\n", encoding="utf-8")
                os.replace(decoy, stages[0])          # the kept NAME is now the decoy
                raise KeyboardInterrupt               # …and the narrowing is cancelled

    module._install_posix_acl_policy, module._strip_acl_by_fd, module._narrow_leftover = install_that_fails, strip_denied, narrow_swap_then_cancel
    try:
        with pytest.raises(KeyboardInterrupt):
            module.write_report(str(staging), [("docs/z.md", 1, "SECRET", "generic_key_assignment", "contents")])
    finally:
        module._install_posix_acl_policy, module._strip_acl_by_fd, module._narrow_leftover = real_install, real_strip, real_narrow
    if not swapped:
        pytest.skip("the kept-stage narrowing never ran; this arm measured nothing")
    assert _findings_anywhere(reports, "docs/z.md:1"), (
        "REPAIRED: an interrupt inside the caller's kept-stage narrowing skipped the re-check; the close "
        "freed the last reference to a swapped stage and the findings are under no name")


def test_the_link_helper_confirms_custody_with_the_name_last(tmp_path: Path) -> None:
    """REPAIRED (cold #2, gate 40): `_link_held_inode` looked the new name up BEFORE the held fstat, so a
    decoy swapped onto the reserved name between the two passed as custody taken."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "link_helper_order")
    if module._PROC_FD_DIR is None:
        pytest.skip("no descriptor directory here")
    reports = tmp_path / "_reports"
    reports.mkdir()
    stage = reports / ".scan_report_stage"
    fd = os.open(str(stage), os.O_CREAT | os.O_WRONLY, 0o600)
    os.write(fd, b"aws\tkey\tassignment\tdocs/l.md:1\n")
    candidate = "scan_report.unpublished.txt"
    real_lstat, real_fstat = module.os.lstat, module.os.fstat
    calls: list[str] = []

    def swap_before_the_second_syscall():
        if len(calls) == 2:
            decoy = reports / "decoy.txt"
            decoy.write_text("scan_gate: CLEAN\n", encoding="utf-8")
            os.replace(decoy, reports / candidate)   # the reserved name now holds a decoy

    def lstat_counted(path, *a, **k):
        if sys._getframe(1).f_code.co_name == "_link_held_inode":
            calls.append("lstat"); swap_before_the_second_syscall()
        return real_lstat(path, *a, **k)

    def fstat_counted(f, *a, **k):
        if sys._getframe(1).f_code.co_name == "_link_held_inode":
            calls.append("fstat"); swap_before_the_second_syscall()
        return real_fstat(f, *a, **k)

    module.os.lstat, module.os.fstat = lstat_counted, fstat_counted
    dirfd = os.open(str(reports), os.O_RDONLY | os.O_DIRECTORY)
    custody: object = None
    try:
        try:
            custody = module._link_held_inode(fd, candidate, dirfd)
        except FileNotFoundError:
            custody = "declined"
    finally:
        module.os.lstat, module.os.fstat = real_lstat, real_fstat
        os.close(fd); os.close(dirfd)
    if len(calls) < 2:
        pytest.skip("the helper made fewer than two identity syscalls; this arm measured nothing")
    assert calls[-1] == "lstat", f"REPAIRED: the name lookup must be the last syscall, got {calls}"
    assert custody == "declined", (
        "REPAIRED: a decoy swapped onto the reserved name between the helper's two identity syscalls "
        "was reported as custody taken — the helper compared a stale name lookup")


# =============================================================================================
# GROUP 54 — the forty-sixth round. Gate 41's cold leg (grok) on e1c1404: preservation still
# linked the canonical NAME, so a report substituted between the lookup and the link took a
# reserved second name; a post-link confirmation error was read as "slot unusable" and the same
# inode took a second slot; and a write-only stage could not be read back by the rescue when the
# reopen through the descriptor directory was refused.
# =============================================================================================


def test_preservation_links_the_inode_it_recorded_not_whatever_the_name_holds(tmp_path: Path) -> None:
    """REPAIRED (cold #1, gate 41): a report substituted at the canonical name between preservation's
    lookup and its link must not get a reserved name; the recorded inode is what is preserved."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "preserve_links_inode")
    if module._PROC_FD_DIR is None:
        pytest.skip("no descriptor directory here")
    reports = tmp_path / "_reports"
    reports.mkdir()
    canonical = reports / "scan_report.txt"
    canonical.write_text("aws\tkey\tassignment\tdocs/A.md:1\n", encoding="utf-8")
    os.chmod(canonical, 0o600)
    real_link = module.os.link
    fired: list[str] = []

    def substitute_then_link(*a, **k):
        callers = {sys._getframe(1).f_code.co_name, sys._getframe(2).f_code.co_name}
        if "_preserve_superseded" in callers and not fired:      # by name, or via the link helper
            fired.append("link")
            decoy = reports / "decoy.txt"
            decoy.write_text("aws\tkey\tassignment\tdocs/B.md:1\n", encoding="utf-8")
            os.chmod(decoy, 0o644)
            os.replace(decoy, canonical)              # B now stands at the canonical name, wide
        return real_link(*a, **k)

    module.os.link = substitute_then_link
    dirfd = os.open(str(reports), os.O_RDONLY | os.O_DIRECTORY)
    try:
        module._preserve_superseded(dirfd, "scan_report.txt")
    finally:
        module.os.link = real_link
        os.close(dirfd)
    if not fired:
        pytest.skip("preservation never linked; this arm measured nothing")
    slots = [p for p in reports.iterdir() if p.name.startswith("scan_report.superseded")]
    b_named = [p.name for p in slots if "docs/B.md" in p.read_text(encoding="utf-8", errors="replace")]
    assert not b_named, (
        f"REPAIRED: the substituted report B took a reserved second name {b_named} (mode "
        f"{[oct(p.stat().st_mode & 0o777) for p in slots]}) — preservation linked the NAME, not the recorded inode")
    assert _findings_anywhere(reports, "docs/A.md:1"), "REPAIRED: the recorded report A was not preserved"


def test_an_unconfirmed_custody_never_takes_a_second_reserved_name(tmp_path: Path) -> None:
    """REPAIRED (cold #2, gate 41): a confirmation error AFTER a successful link means custody may have
    been taken; the caller must not move on to link the same inode into the next slot."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "unconfirmed_custody")
    if module._PROC_FD_DIR is None:
        pytest.skip("no descriptor directory here")
    reports = tmp_path / "_reports"
    reports.mkdir()
    src = os.open(str(reports / ".scan_report_src"), os.O_CREAT | os.O_RDWR, 0o600)
    os.write(src, b"aws\tkey\tassignment\tdocs/u.md:1\n")
    real_lstat = module.os.lstat
    fired: list[str] = []

    def lstat_eio_once(path, *a, **k):
        if sys._getframe(1).f_code.co_name == "_link_held_inode" and not fired:
            fired.append("lstat"); raise OSError(errno.EIO, "injected confirmation failure")
        return real_lstat(path, *a, **k)

    module.os.lstat = lstat_eio_once
    dirfd = os.open(str(reports), os.O_RDONLY | os.O_DIRECTORY)
    try:
        module._copy_out_unpublished(dirfd, src)
    finally:
        module.os.lstat = real_lstat
        os.close(src); os.close(dirfd)
    if not fired:
        pytest.skip("the post-link confirmation never ran; this arm measured nothing")
    by_inode: dict[tuple[int, int], list[str]] = {}
    for p in reports.iterdir():
        if p.name.startswith("scan_report.unpublished"):
            st = p.stat(); by_inode.setdefault((st.st_dev, st.st_ino), []).append(p.name)
    doubled = {k: v for k, v in by_inode.items() if len(v) >= 2}
    assert not doubled, (
        f"REPAIRED: one inode holds several reserved names {doubled} — a confirmation error after a "
        f"successful link was read as 'slot unusable' and the next slot was linked too")
    assert _findings_anywhere(reports, "docs/u.md:1"), "CONTROL: the findings must survive"


def test_a_rescue_reads_the_held_descriptor_when_the_reopen_is_refused(tmp_path: Path) -> None:
    """REPAIRED (cold #3, gate 41): a stage the rescue cannot reopen through the descriptor directory
    (a 0200 stage whose chmod did not stick) must still be copied from the descriptor it holds."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "rescue_pread_fallback")
    if module._PROC_FD_DIR is None:
        pytest.skip("no descriptor directory here")
    reports = tmp_path / "_reports"
    reports.mkdir()
    dirfd = os.open(str(reports), os.O_RDONLY | os.O_DIRECTORY)
    fd, name = module._stage_report(dirfd, "aws\tkey\tassignment\tdocs/r.md:1\n", evidence=True)
    os.unlink(str(reports / name))                    # the staged name has diverged; the fd is the last reference
    real_open = module.os.open
    refused: list[str] = []

    def refuse_the_proc_reopen(path, *a, **k):
        if sys._getframe(1).f_code.co_name == "_copy_out_unpublished" and str(path).startswith(module._PROC_FD_DIR):
            refused.append(str(path)); raise PermissionError(errno.EACCES, "injected: reopen refused (0200)")
        return real_open(path, *a, **k)

    module.os.open = refuse_the_proc_reopen
    try:
        module._copy_out_unpublished(dirfd, fd)
    finally:
        module.os.open = real_open
        os.close(fd); os.close(dirfd)
    if not refused:
        pytest.skip("the rescue never tried the reopen; this arm measured nothing")
    assert _findings_anywhere(reports, "docs/r.md:1"), (
        "REPAIRED: the reopen through the descriptor directory was refused and the rescue gave up; the "
        "held descriptor was the last reference and the close freed the findings")


# =============================================================================================
# GROUP 55 — the forty-seventh round. Gate 42's invariant leg (Gemini) on 4e0be0a: an unconfirmed
# custody made preservation return before its rescue block, so a recorded report whose name had
# been taken lost its last reference at the close.
# =============================================================================================


def test_an_unconfirmed_custody_still_rescues_a_recorded_report_whose_name_is_gone(tmp_path: Path) -> None:
    """REPAIRED (invariant leg, gate 42): the custody-unconfirmed exit of preservation must still run
    the held-descriptor rescue when the canonical name no longer reaches the recorded inode."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "preserve_unconfirmed_rescue")
    if module._PROC_FD_DIR is None:
        pytest.skip("no descriptor directory here")
    reports = tmp_path / "_reports"
    reports.mkdir()
    canonical = reports / "scan_report.txt"
    canonical.write_text("aws\tkey\tassignment\tdocs/P.md:1\n", encoding="utf-8")
    os.chmod(canonical, 0o600)
    real_lstat = module.os.lstat
    fired: list[str] = []

    def take_both_names_then_fail_the_confirmation(path, *a, **k):
        if (sys._getframe(1).f_code.co_name == "_link_held_inode"
                and sys._getframe(2).f_code.co_name == "_preserve_superseded" and not fired):
            fired.append("lstat")
            slot = reports / str(path)
            decoy = reports / "decoy.txt"
            decoy.write_text("scan_gate: CLEAN\n", encoding="utf-8")
            os.replace(decoy, slot)                   # the just-made link is taken by a decoy…
            decoy.write_text("scan_gate: CLEAN\n", encoding="utf-8")
            os.replace(decoy, canonical)              # …and so is the canonical name: the fd is the last reference
            raise OSError(errno.EIO, "injected confirmation failure")
        return real_lstat(path, *a, **k)

    module.os.lstat = take_both_names_then_fail_the_confirmation
    dirfd = os.open(str(reports), os.O_RDONLY | os.O_DIRECTORY)
    try:
        authorized = module._preserve_superseded(dirfd, "scan_report.txt")
    finally:
        module.os.lstat = real_lstat
        os.close(dirfd)
    if not fired:
        pytest.skip("the post-link confirmation never ran; this arm measured nothing")
    assert authorized is False, "CONTROL: an unconfirmed custody must not authorize the replacement"
    assert _findings_anywhere(reports, "docs/P.md:1"), (
        "REPAIRED: custody was unconfirmed, both names were taken, and preservation returned before its "
        "rescue block — the close freed the last reference to the recorded report")


def test_preservation_rescues_an_owner_unreadable_report_whose_name_is_taken(tmp_path: Path) -> None:
    """REPAIRED (executed review, gate 42): the copy-out narrows with fchmod, which a path-only descriptor
    refuses (EBADF), so a mode-000 report whose name was taken could not be reopened for reading and was
    freed at the close; the narrowing must fall back to chmod through the descriptor directory."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "preserve_unreadable_rescue")
    if module._PROC_FD_DIR is None or not hasattr(os, "O_PATH"):
        pytest.skip("no descriptor directory or O_PATH here")
    reports = tmp_path / "_reports"
    reports.mkdir()
    canonical = reports / "scan_report.txt"
    canonical.write_text("aws\tkey\tassignment\tdocs/U.md:1\n", encoding="utf-8")
    os.chmod(canonical, 0o000)                        # owner-unreadable, as preservation must tolerate
    real_link = module.os.link
    fired: list[str] = []

    def take_the_name_then_link(*a, **k):
        callers = {sys._getframe(1).f_code.co_name, sys._getframe(2).f_code.co_name}
        if "_preserve_superseded" in callers and not fired:
            fired.append("link")
            os.unlink(canonical)                      # the recorded inode now has no name: the held fd is its last reference
        return real_link(*a, **k)

    module.os.link = take_the_name_then_link
    dirfd = os.open(str(reports), os.O_RDONLY | os.O_DIRECTORY)
    try:
        authorized = module._preserve_superseded(dirfd, "scan_report.txt")
    finally:
        module.os.link = real_link
        os.close(dirfd)
        for p in reports.iterdir():
            try: os.chmod(p, 0o600)
            except OSError: pass
    if not fired:
        pytest.skip("preservation never linked; this arm measured nothing")
    assert authorized is False, "CONTROL: nothing preserved by link must not authorize the replacement"
    assert _findings_anywhere(reports, "docs/U.md:1"), (
        "REPAIRED: the recorded report was owner-unreadable and its name was taken; the rescue could not "
        "narrow the path-only descriptor (fchmod EBADF), the reopen was refused, and the close freed it")


def test_an_unconfirmed_custody_in_the_copy_out_still_rescues_a_diverged_stage(tmp_path: Path) -> None:
    """REPAIRED (inventory trace, gate 42): the copy-out's custody-unconfirmed exit kept its stage by
    retention alone; a stage whose name was taken meanwhile lost its last reference at the close."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "copyout_unconfirmed_rescue")
    if module._PROC_FD_DIR is None:
        pytest.skip("no descriptor directory here")
    reports = tmp_path / "_reports"
    reports.mkdir()
    src = os.open(str(reports / ".scan_report_src"), os.O_CREAT | os.O_RDWR, 0o600)
    os.write(src, b"aws\tkey\tassignment\tdocs/C.md:1\n")
    os.unlink(str(reports / ".scan_report_src"))      # the source has no name: the rescue is its last chance
    real_lstat = module.os.lstat
    fired: list[str] = []

    def take_both_names_then_fail(path, *a, **k):
        if (sys._getframe(1).f_code.co_name == "_link_held_inode"
                and sys._getframe(2).f_code.co_name == "_copy_out_unpublished" and not fired):
            fired.append("lstat")
            decoy = reports / "decoy.txt"
            for victim in [p for p in reports.iterdir() if p.name.startswith(".scan_report_")] + [reports / str(path)]:
                decoy.write_text("scan_gate: CLEAN\n", encoding="utf-8")
                os.replace(decoy, victim)             # the copy's stage name AND its reserved name are taken
            raise OSError(errno.EIO, "injected confirmation failure")
        return real_lstat(path, *a, **k)

    module.os.lstat = take_both_names_then_fail
    dirfd = os.open(str(reports), os.O_RDONLY | os.O_DIRECTORY)
    try:
        module._copy_out_unpublished(dirfd, src)
    finally:
        module.os.lstat = real_lstat
        os.close(src); os.close(dirfd)
    if not fired:
        pytest.skip("the post-link confirmation never ran; this arm measured nothing")
    assert _findings_anywhere(reports, "docs/C.md:1"), (
        "REPAIRED: custody was unconfirmed and both of the copy's names were taken; the copy-out returned "
        "on retention alone and the closes freed the last references to the findings")


# =============================================================================================
# GROUP 56 — the forty-eighth round. Gate 43's invariant leg (Gemini) on 407a89c: the round-47
# copy-out rescue re-entered through `_false_or_rescue`, which carried no depth, so a racer who
# keeps taking names could drive the rescue chain until descriptors ran out.
# =============================================================================================


def test_the_copy_out_rescue_chain_is_depth_bounded(tmp_path: Path) -> None:
    """REPAIRED (invariant leg, gate 43): a rescue that re-enters the copy-out must carry its depth;
    an unbounded chain leaks a stage and a descriptor per level until the process runs out."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "rescue_chain_depth")
    if module._PROC_FD_DIR is None:
        pytest.skip("no descriptor directory here")
    reports = tmp_path / "_reports"
    reports.mkdir()
    src = os.open(str(reports / ".scan_report_src"), os.O_CREAT | os.O_RDWR, 0o600)
    os.write(src, b"aws\tkey\tassignment\tdocs/D.md:1\n")
    os.unlink(str(reports / ".scan_report_src"))
    real_lstat, real_open = module.os.lstat, module.os.open
    stages_made: list[str] = []
    fired: list[str] = []

    def count_stage_opens(path, *a, **k):
        fd = real_open(path, *a, **k)
        if sys._getframe(1).f_code.co_name == "_copy_out_unpublished" and str(path).startswith(".scan_report_"):
            stages_made.append(str(path))
        return fd

    def take_every_name_and_fail_every_confirmation(path, *a, **k):
        if sys._getframe(1).f_code.co_name == "_link_held_inode":
            fired.append("lstat")
            decoy = reports / "decoy.txt"
            for victim in [p for p in reports.iterdir() if p.name.startswith(".scan_report_")] + [reports / str(path)]:
                decoy.write_text("scan_gate: CLEAN\n", encoding="utf-8")
                try: os.replace(decoy, victim)
                except OSError: pass
            raise OSError(errno.EIO, "injected confirmation failure")
        return real_lstat(path, *a, **k)

    module.os.lstat, module.os.open = take_every_name_and_fail_every_confirmation, count_stage_opens
    dirfd = os.open(str(reports), os.O_RDONLY | os.O_DIRECTORY)
    try:
        try:
            module._copy_out_unpublished(dirfd, src)
        except (RecursionError, OSError) as exc:            # the unbounded chain ends in one of these
            stages_made.append("EXHAUSTED:%s" % type(exc).__name__)
    finally:
        module.os.lstat, module.os.open = real_lstat, real_open
        os.close(src); os.close(dirfd)
    if not fired:
        pytest.skip("the post-link confirmation never ran; this arm measured nothing")
    assert len(stages_made) <= 3, (
        f"REPAIRED: the rescue chain made {len(stages_made)} stages before stopping "
        f"({stages_made[-1] if stages_made else '-'}); the re-entry carried no depth and the chain was unbounded")


def test_the_copy_outs_finally_re_asks_the_stage_name_before_the_close(tmp_path: Path) -> None:
    """REPAIRED (cold #1, gate 43): every retention exit of the copy-out closed its stage without asking
    whether the stage name still reached it; a strip denied plus a swapped stage name freed the copy."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "copyout_finally_reask")
    if not module._XATTR_SUPPORTED or module._PROC_FD_DIR is None:
        pytest.skip("no xattr layer or descriptor directory here")
    reports = tmp_path / "_reports"
    reports.mkdir()
    src = os.open(str(reports / ".scan_report_src"), os.O_CREAT | os.O_RDWR, 0o600)
    os.write(src, b"aws\tkey\tassignment\tdocs/S.md:1\n")
    os.unlink(str(reports / ".scan_report_src"))      # the source has no name: the copy is its last chance
    real_strip = module._strip_acl_by_fd
    fired: list[str] = []

    def deny_the_strip_and_take_the_stage_name(fd):
        if sys._getframe(1).f_code.co_name == "_copy_out_unpublished" and not fired:
            fired.append("strip")
            decoy = reports / "decoy.txt"
            for victim in [p for p in reports.iterdir() if p.name.startswith(".scan_report_")]:
                decoy.write_text("scan_gate: CLEAN\n", encoding="utf-8")
                os.replace(decoy, victim)             # the stage name is taken while the copy is being made
            raise PermissionError(errno.EPERM, "injected: strip denied")
        return real_strip(fd)

    module._strip_acl_by_fd = deny_the_strip_and_take_the_stage_name
    dirfd = os.open(str(reports), os.O_RDONLY | os.O_DIRECTORY)
    try:
        module._copy_out_unpublished(dirfd, src)
    finally:
        module._strip_acl_by_fd = real_strip
        os.close(src); os.close(dirfd)
    if not fired:
        pytest.skip("the stage strip never ran; this arm measured nothing")
    assert _findings_anywhere(reports, "docs/S.md:1"), (
        "REPAIRED: the strip was denied (a retention exit) and the stage name had been taken; the finally "
        "closed the stage without re-asking whether its name still reached it, and the copy was freed")


# =============================================================================================
# GROUP 57 — the forty-ninth round. Gate 44's invariant leg (Gemini) on e71e440: the copy-out's
# no-custody arm made its own nested copy and the round-48 finally then asked again, so a taken
# stage name produced two copies of the same findings.
# =============================================================================================


def test_a_taken_stage_name_after_a_refused_link_yields_exactly_one_copy(tmp_path: Path) -> None:
    """REPAIRED (invariant leg, gate 44): the no-custody arm must defer to the finally's single
    re-ask rather than copy on its own and then be copied again."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "single_rescue_copy")
    if module._PROC_FD_DIR is None:
        pytest.skip("no descriptor directory here")
    reports = tmp_path / "_reports"
    reports.mkdir()
    src = os.open(str(reports / ".scan_report_src"), os.O_CREAT | os.O_RDWR, 0o600)
    os.write(src, b"aws\tkey\tassignment\tdocs/E.md:1\n")
    os.unlink(str(reports / ".scan_report_src"))
    real_lstat = module.os.lstat
    fired: list[str] = []

    def take_the_candidate_and_the_stage_once(path, *a, **k):
        if (sys._getframe(1).f_code.co_name == "_link_held_inode"
                and sys._getframe(2).f_code.co_name == "_copy_out_unpublished" and not fired):
            fired.append("lstat")
            decoy = reports / "decoy.txt"
            for victim in [p for p in reports.iterdir() if p.name.startswith(".scan_report_")] + [reports / str(path)]:
                decoy.write_text("scan_gate: CLEAN\n", encoding="utf-8")
                os.replace(decoy, victim)             # the candidate AND the stage name are taken
        return real_lstat(path, *a, **k)              # …and the post-link check then sees a decoy: no custody

    module.os.lstat = take_the_candidate_and_the_stage_once
    dirfd = os.open(str(reports), os.O_RDONLY | os.O_DIRECTORY)
    try:
        module._copy_out_unpublished(dirfd, src)
    finally:
        module.os.lstat = real_lstat
        os.close(src); os.close(dirfd)
    if not fired:
        pytest.skip("the post-link check never ran; this arm measured nothing")
    names = [p for p in reports.iterdir() if "docs/E.md:1" in p.read_text(encoding="utf-8", errors="replace")]
    copies = sorted({(p.stat().st_dev, p.stat().st_ino) for p in names})   # names of ONE inode are one copy
    assert len(copies) >= 1, "CONTROL: the findings must survive"
    assert len(copies) == 1, (
        f"REPAIRED: one refused link plus one taken stage name produced {len(copies)} distinct copies "
        f"{sorted(p.name for p in names)}; the no-custody arm copied on its own and the finally's re-ask copied again")


@pytest.mark.parametrize("site", ["quarantine", "copy_out"])
def test_a_reserved_name_taken_before_the_stage_release_does_not_free_the_findings(tmp_path: Path, site: str) -> None:
    """REPAIRED (cold #1, gate 44): the release path read nlink >= 2 and then unlinked the stage four
    syscalls later; a reserved name removed in between made that unlink the last name, custody was
    reported taken, and the caller's close freed the findings."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "release_last_name_" + site)
    if module._PROC_FD_DIR is None:
        pytest.skip("no descriptor directory here")
    reports = tmp_path / "_reports"
    reports.mkdir()
    dirfd = os.open(str(reports), os.O_RDONLY | os.O_DIRECTORY)
    body = "aws\tkey\tassignment\tdocs/R.md:1\n"
    real_unlink = module.os.unlink
    fired: list[str] = []

    def take_the_reserved_names_then_unlink(path, *a, **k):
        if (sys._getframe(1).f_code.co_name == "_remove_own_stage"
                and sys._getframe(2).f_code.co_name == "_remove_stage_if_another_name_remains" and not fired):
            fired.append("unlink")
            for p in reports.iterdir():
                if p.name.startswith("scan_report.unpublished"):
                    os.unlink(p)                          # the racer ends the reserved name first
        return real_unlink(path, *a, **k)                 # …and the stage unlink now takes the LAST name

    module.os.unlink = take_the_reserved_names_then_unlink
    try:
        if site == "quarantine":
            fd, name = module._stage_report(dirfd, body, evidence=True)
            try:
                custody = module._quarantine_unpublished(dirfd, name, fd, [("docs/R.md", 1, "SECRET", "generic_key_assignment", "contents")])
            finally:
                os.close(fd)                              # write_report's close, on a True answer
        else:
            src = os.open(str(reports / ".scan_report_src"), os.O_CREAT | os.O_RDWR, 0o600)
            os.write(src, body.encode()); os.unlink(str(reports / ".scan_report_src"))
            try:
                custody = module._copy_out_unpublished(dirfd, src)
            finally:
                os.close(src)
    finally:
        module.os.unlink = real_unlink
        os.close(dirfd)
    if not fired:
        pytest.skip("the stage release never ran; this arm measured nothing")
    assert _findings_anywhere(reports, "docs/R.md:1"), (
        f"REPAIRED ({site}): the reserved name was ended between the nlink read and the stage unlink; the "
        f"unlink took the last name, custody was reported {custody!r}, and the close freed the findings")


# =============================================================================================
# GROUP 58 — the fiftieth round. Gate 45's invariant leg (Gemini) on 5b1a014: the copy-out
# answered False while keeping a complete stage, quarantine passed that answer up, and
# write_report's re-ask copied the same findings a second time.
# =============================================================================================


def test_a_complete_kept_copy_counts_as_custody_so_the_caller_does_not_copy_again(tmp_path: Path) -> None:
    """REPAIRED (invariant leg, gate 45): a copy-out that keeps a COMPLETE stage under the temporary prefix
    must answer True, or the caller's own re-ask makes a second copy of the same findings."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "kept_copy_is_custody")
    if not module._XATTR_SUPPORTED or module._PROC_FD_DIR is None:
        pytest.skip("no xattr layer or descriptor directory here")
    staging = tmp_path / "staging"
    reports = staging / "_reports"
    reports.mkdir(parents=True)
    for slot in module._unpublished_slot_names():
        (reports / slot).write_text("scan_gate: CLEAN\n", encoding="utf-8")   # every reserved name occupied
    real_install = module._install_posix_acl_policy
    swapped: list[str] = []

    def take_the_stage_name_then_fail(dirfd, src_name, dst_fd, dst_name):
        if not swapped:
            stages = [p for p in reports.iterdir() if p.name.startswith(".scan_report_")]
            if len(stages) == 1:
                swapped.append(stages[0].name)
                decoy = reports / "decoy.txt"
                decoy.write_text("scan_gate: CLEAN\n", encoding="utf-8")
                os.replace(decoy, stages[0])          # the staged name has diverged before quarantine looks
        raise OSError(5, "injected policy failure")

    module._install_posix_acl_policy = take_the_stage_name_then_fail
    try:
        with pytest.raises(Exception):
            module.write_report(str(staging), [("docs/T.md", 1, "SECRET", "generic_key_assignment", "contents")])
    finally:
        module._install_posix_acl_policy = real_install
    if not swapped:
        pytest.skip("the policy install never ran; this arm measured nothing")
    copies = sorted(p.name for p in reports.iterdir()
                    if "docs/T.md:1" in p.read_text(encoding="utf-8", errors="replace"))
    assert len(copies) >= 1, "CONTROL: the findings must survive"
    assert len(copies) == 1, (
        f"REPAIRED: a diverged stage with every reserved name occupied produced {len(copies)} kept copies "
        f"{copies}; the copy-out answered False over a complete kept stage and the caller copied again")


def test_quarantine_does_not_copy_a_stage_whose_name_is_intact_after_a_refused_link(tmp_path: Path) -> None:
    """REPAIRED (executed review, gate 45): quarantine's no-custody arm copied unconditionally; with the
    staged name intact that made two copies of the same findings (the stage and the copy)."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "quarantine_no_copy_when_intact")
    if module._PROC_FD_DIR is None:
        pytest.skip("no descriptor directory here")
    reports = tmp_path / "_reports"
    reports.mkdir()
    dirfd = os.open(str(reports), os.O_RDONLY | os.O_DIRECTORY)
    fd, name = module._stage_report(dirfd, "aws\tkey\tassignment\tdocs/Q.md:1\n", evidence=True)
    real_lstat = module.os.lstat
    fired: list[str] = []

    def swap_the_candidate_only(path, *a, **k):
        if (sys._getframe(1).f_code.co_name == "_link_held_inode"
                and sys._getframe(2).f_code.co_name == "_quarantine_unpublished" and not fired):
            fired.append("lstat")
            decoy = reports / "decoy.txt"
            decoy.write_text("scan_gate: CLEAN\n", encoding="utf-8")
            os.replace(decoy, reports / str(path))    # the reserved name is taken by a decoy; the STAGE keeps its name
        return real_lstat(path, *a, **k)

    module.os.lstat = swap_the_candidate_only
    try:
        module._quarantine_unpublished(dirfd, name, fd, [("docs/Q.md", 1, "SECRET", "generic_key_assignment", "contents")])
    finally:
        module.os.lstat = real_lstat
        os.close(fd); os.close(dirfd)
    if not fired:
        pytest.skip("the post-link check never ran; this arm measured nothing")
    copies = sorted(p.name for p in reports.iterdir()
                    if "docs/Q.md:1" in p.read_text(encoding="utf-8", errors="replace"))
    assert len(copies) >= 1, "CONTROL: the findings must survive"
    assert len(copies) == 1, (
        f"REPAIRED: a refused link with the staged name intact produced {len(copies)} copies {copies}; "
        f"quarantine copied without asking whether the stage still had its name")


def test_a_failed_link_count_read_after_the_release_unlink_still_copies_out(tmp_path: Path) -> None:
    """REPAIRED (executed review, gate 45): after the stage unlink a failed link-count read cannot mean
    'keep the name' — the name is gone; a copy must be attempted rather than letting the close decide."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "release_fstat_fault")
    if module._PROC_FD_DIR is None:
        pytest.skip("no descriptor directory here")
    reports = tmp_path / "_reports"
    reports.mkdir()
    dirfd = os.open(str(reports), os.O_RDONLY | os.O_DIRECTORY)
    fd, name = module._stage_report(dirfd, "aws\tkey\tassignment\tdocs/F.md:1\n", evidence=True)
    real_fstat, real_unlink = module.os.fstat, module.os.unlink
    calls: list[str] = []

    def end_the_reserved_name_then_unlink(path, *a, **k):
        if sys._getframe(1).f_code.co_name == "_remove_own_stage" and "unlink" not in calls:
            calls.append("unlink")
            for p in reports.iterdir():
                if p.name.startswith("scan_report.unpublished"):
                    os.unlink(p)                      # the racer ends the reserved name inside the window
        return real_unlink(path, *a, **k)

    def fail_the_post_check_fstat(f, *a, **k):
        if sys._getframe(1).f_code.co_name == "_remove_stage_if_another_name_remains" and "unlink" in calls and "fstat" not in calls:
            calls.append("fstat"); raise OSError(errno.EIO, "injected: post-unlink link-count read failed")
        return real_fstat(f, *a, **k)

    module.os.unlink, module.os.fstat = end_the_reserved_name_then_unlink, fail_the_post_check_fstat
    try:
        module._quarantine_unpublished(dirfd, name, fd, [("docs/F.md", 1, "SECRET", "generic_key_assignment", "contents")])
    finally:
        module.os.unlink, module.os.fstat = real_unlink, real_fstat
        os.close(fd); os.close(dirfd)
    if "fstat" not in calls:
        pytest.skip("the post-unlink link-count read never ran; this arm measured nothing")
    assert _findings_anywhere(reports, "docs/F.md:1"), (
        "REPAIRED: the post-unlink link-count read failed and was read as 'keep the name' after the name "
        "was already unlinked; no copy was attempted and the close freed the findings")


def test_the_nested_rescue_never_unlinks_its_own_stage(tmp_path: Path) -> None:
    """REPAIRED (cold #1, gate 45): the depth-one rescue released its stage after linking, so a racer's
    second act on the reserved name left the copy nameless with no further rescue; at depth one the
    module keeps the stage and takes no last name of its own."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "nested_rescue_keeps_stage")
    if module._PROC_FD_DIR is None:
        pytest.skip("no descriptor directory here")
    reports = tmp_path / "_reports"
    reports.mkdir()
    dirfd = os.open(str(reports), os.O_RDONLY | os.O_DIRECTORY)
    fd, name = module._stage_report(dirfd, "aws\tkey\tassignment\tdocs/N.md:1\n", evidence=True)
    real_unlink = module.os.unlink
    fired: list[str] = []

    def end_the_reserved_names_before_every_release(path, *a, **k):
        if (sys._getframe(1).f_code.co_name == "_remove_own_stage"
                and sys._getframe(2).f_code.co_name == "_remove_stage_if_another_name_remains"):
            fired.append(str(path))
            for p in reports.iterdir():
                if p.name.startswith("scan_report.unpublished"):
                    os.unlink(p)                      # the racer's act, once per release
        return real_unlink(path, *a, **k)

    module.os.unlink = end_the_reserved_names_before_every_release
    try:
        module._quarantine_unpublished(dirfd, name, fd, [("docs/N.md", 1, "SECRET", "generic_key_assignment", "contents")])
    finally:
        module.os.unlink = real_unlink
        os.close(fd); os.close(dirfd)
    if not fired:
        pytest.skip("no release ran; this arm measured nothing")
    assert _findings_anywhere(reports, "docs/N.md:1"), (
        f"REPAIRED: releases ran on {fired}; the depth-one rescue unlinked its own stage after its link and "
        f"the racer's second act left the copy nameless — the closes freed the findings")


def test_a_link_count_already_zero_on_entry_to_the_release_is_rescued(tmp_path: Path) -> None:
    """REPAIRED (cold #2, gate 45): both names taken before the release helper's first read left the
    held descriptor as the last reference; the helper skipped the case and the caller closed."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "release_nlink_zero_on_entry")
    if module._PROC_FD_DIR is None:
        pytest.skip("no descriptor directory here")
    reports = tmp_path / "_reports"
    reports.mkdir()
    dirfd = os.open(str(reports), os.O_RDONLY | os.O_DIRECTORY)
    fd, name = module._stage_report(dirfd, "aws\tkey\tassignment\tdocs/Z.md:1\n", evidence=True)
    real_fstat = module.os.fstat
    fired: list[str] = []

    def take_both_names_before_the_first_read(f, *a, **k):
        if sys._getframe(1).f_code.co_name == "_remove_stage_if_another_name_remains" and not fired:
            fired.append("fstat")
            for p in reports.iterdir():
                if p.name.startswith("scan_report.unpublished") or p.name == name:
                    os.unlink(p)                      # reserved name AND stage name: gone before the helper looks
        return real_fstat(f, *a, **k)

    module.os.fstat = take_both_names_before_the_first_read
    try:
        module._quarantine_unpublished(dirfd, name, fd, [("docs/Z.md", 1, "SECRET", "generic_key_assignment", "contents")])
    finally:
        module.os.fstat = real_fstat
        os.close(fd); os.close(dirfd)
    if not fired:
        pytest.skip("the release helper never read the link count; this arm measured nothing")
    assert _findings_anywhere(reports, "docs/Z.md:1"), (
        "REPAIRED: the link count was already zero when the release helper started; it skipped the case, "
        "custody was reported taken, and the close freed the findings")


# =============================================================================================
# GROUP 59 — the fifty-first round. Gate 46's inventory on 5850e01: a stage name taken while the
# copy streamed, plus no creatable name for the depth-one rescue, left the copy-out answering
# True over no copy, and write_report then skipped the re-ask that had rescued this corner
# from the original descriptor at 5b1a014.
# =============================================================================================


@pytest.mark.parametrize("mode", ["depth1_no_name", "depth1_name_taken", "release_nlink0_no_name"])
def test_the_copy_outs_answer_is_false_when_its_kept_stage_lost_its_name_and_the_rescue_failed(tmp_path: Path, mode: str) -> None:
    """REPAIRED (cold #1–3 and the inventory, gate 46): the answer must be decided after the cleanup — False
    whenever the copy's inode has no name left and no nested copy kept a named complete one — so that
    write_report's False handler still runs against the original descriptor it holds open."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "hollow_true_" + mode)
    if not module._XATTR_SUPPORTED or module._PROC_FD_DIR is None:
        pytest.skip("no xattr layer or descriptor directory here")
    staging = tmp_path / "staging"
    reports = staging / "_reports"
    reports.mkdir(parents=True)
    if mode != "release_nlink0_no_name":
        for slot in module._unpublished_slot_names():
            (reports / slot).write_text("scan_gate: CLEAN\n", encoding="utf-8")   # every reserved name occupied
    real_install, real_write, real_open, real_fstat = module._install_posix_acl_policy, module.os.write, module.os.open, module.os.fstat
    swapped: list[str] = []
    refused: list[str] = []

    def _decoy_over(target: Path) -> None:
        decoy = reports / ("decoy-%d.txt" % len(swapped))
        decoy.write_text("scan_gate: CLEAN\n", encoding="utf-8")
        os.replace(decoy, target)

    def install_fails(dirfd, src_name, dst_fd, dst_name):
        if mode != "release_nlink0_no_name" and not swapped:
            stages = [p for p in reports.iterdir() if p.name.startswith(".scan_report_")]
            if len(stages) == 1:
                swapped.append(stages[0].name); _decoy_over(stages[0])   # the primary stage diverges: quarantine copies out
        raise OSError(5, "injected policy failure")

    def write_hook(fd, data):
        f = sys._getframe(1)
        if f.f_code.co_name == "_copy_out_unpublished":
            d, stage = f.f_locals.get("depth", 0), f.f_locals.get("stage_name")
            want = {"depth1_no_name": 0, "depth1_name_taken": None, "release_nlink0_no_name": None}[mode]
            if stage and ((mode == "depth1_no_name" and d == 0 and len(swapped) == 1)
                          or (mode == "depth1_name_taken" and d in (0, 1) and stage not in swapped and len(swapped) <= 2)):
                swapped.append(stage); _decoy_over(reports / stage)   # the copy's own stage name is taken while it streams
        return real_write(fd, data)

    def open_hook(path, *a, **k):
        f = sys._getframe(1)
        if (mode in ("depth1_no_name", "release_nlink0_no_name") and f.f_code.co_name == "_copy_out_unpublished"
                and f.f_locals.get("depth", 0) >= 1 and str(path).startswith(".scan_report_")):
            refused.append(str(path)); raise OSError(errno.ENOSPC, "injected: no creatable name at depth one")
        return real_open(path, *a, **k)

    def fstat_hook(f_, *a, **k):
        f = sys._getframe(1)
        if mode == "release_nlink0_no_name" and f.f_code.co_name == "_remove_stage_if_another_name_remains" and not swapped:
            swapped.append("both")
            for p in list(reports.iterdir()):
                if p.name.startswith("scan_report.unpublished") or p.name.startswith(".scan_report_"):
                    os.unlink(p)                       # reserved name AND stage name gone before the helper's first read
        return real_fstat(f_, *a, **k)

    module._install_posix_acl_policy, module.os.write, module.os.open, module.os.fstat = install_fails, write_hook, open_hook, fstat_hook
    try:
        with pytest.raises(Exception):
            module.write_report(str(staging), [("docs/H.md", 1, "SECRET", "generic_key_assignment", "contents")])
    finally:
        module._install_posix_acl_policy, module.os.write, module.os.open, module.os.fstat = real_install, real_write, real_open, real_fstat
    needed = {"depth1_no_name": (2, 1), "depth1_name_taken": (3, 0), "release_nlink0_no_name": (1, 1)}[mode]
    if len(swapped) < needed[0] or len(refused) < needed[1]:
        pytest.skip(f"the schedule did not complete (swapped={swapped}, refused={len(refused)}); this arm measured nothing")
    assert _findings_anywhere(reports, "docs/H.md:1"), (
        f"REPAIRED ({mode}): the copy's inode had no name left and no nested copy kept one, yet the answer "
        f"reaching write_report was True; its False handler was skipped and the close freed the findings")


# =============================================================================================
# GROUP 60 — the fifty-second round. Gate 47's invariant leg on c3f5bb3: two answers given from
# no reading. A depth-one copy whose link landed closed with no check at all, answering True
# over an inode both of whose names a racer had taken; and the release helper answered "custody
# holds" when its first link-count read failed, where its own depth-one branch answers False.
# =============================================================================================


def test_a_depth_one_copy_whose_link_landed_and_then_lost_both_names_answers_false(tmp_path: Path) -> None:
    """REPAIRED (invariant leg #1, gate 47): the depth-one linked branch must still read the link count
    before its close — a copy with no name left answers False, and every caller above then retries
    from the original descriptor write_report still holds open."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "depth1_linked_hollow")
    if not module._XATTR_SUPPORTED or module._PROC_FD_DIR is None:
        pytest.skip("no xattr layer or descriptor directory here")
    staging = tmp_path / "staging"
    reports = staging / "_reports"
    reports.mkdir(parents=True)
    real_install, real_unlink, real_fstat = module._install_posix_acl_policy, module.os.unlink, module.os.fstat
    acts: list[str] = []

    def _decoy_over(target: Path) -> None:
        decoy = reports / ("decoy-%d.txt" % len(acts))
        decoy.write_text("scan_gate: CLEAN\n", encoding="utf-8")
        os.replace(decoy, target)

    def install_fails(dirfd, src_name, dst_fd, dst_name):
        if not acts:
            stages = [p for p in reports.iterdir() if p.name.startswith(".scan_report_")]
            if len(stages) == 1:
                acts.append("primary:" + stages[0].name); _decoy_over(stages[0])   # quarantine copies out at depth zero
        raise OSError(5, "injected policy failure")

    def unlink_hook(path, *a, **k):
        real_unlink(path, *a, **k)
        f1, f2 = sys._getframe(1), sys._getframe(2)
        if (f1.f_code.co_name == "_remove_own_stage" and f2.f_code.co_name == "_remove_stage_if_another_name_remains"
                and f2.f_locals.get("depth", 0) == 0 and "reserved0" not in "".join(acts)):
            acts.append("reserved0")
            for p in list(reports.iterdir()):
                if p.name.startswith("scan_report.unpublished"):
                    real_unlink(p)                     # the racer ends the depth-zero reserved name inside the release window

    def fstat_hook(f_, *a, **k):
        f = sys._getframe(1)
        if (f.f_code.co_name == "_copy_out_unpublished" and f.f_locals.get("depth") == 1
                and f.f_locals.get("keep_stage") is False and "both1" not in "".join(acts)):
            acts.append("both1")
            stage = f.f_locals.get("stage_name")
            for p in list(reports.iterdir()):
                if p.name.startswith("scan_report.unpublished") or (stage and p.name == stage):
                    real_unlink(p)                     # the depth-one stage name AND its reserved name, before the close
        return real_fstat(f_, *a, **k)

    module._install_posix_acl_policy, module.os.unlink, module.os.fstat = install_fails, unlink_hook, fstat_hook
    try:
        with pytest.raises(Exception):
            module.write_report(str(staging), [("docs/H.md", 1, "SECRET", "generic_key_assignment", "contents")])
    finally:
        module._install_posix_acl_policy, module.os.unlink, module.os.fstat = real_install, real_unlink, real_fstat
    if "both1" not in acts:
        pytest.skip(f"the schedule did not reach the depth-one linked close (acts={acts}); this arm measured nothing")
    assert _findings_anywhere(reports, "docs/H.md:1"), (
        "REPAIRED: the depth-one copy's link landed, both of its names were taken before its close, and it "
        "answered True with no reading; the release helper, quarantine and write_report all trusted it and the "
        f"close freed the findings (acts={acts})")


def test_a_release_pre_read_that_fails_does_not_answer_custody(tmp_path: Path) -> None:
    """REPAIRED (invariant leg #2, gate 47): a link-count read that fails before the release acts is not
    "custody holds" — the helper answers False, like its depth-one branch, and write_report's False
    handler re-asks the original descriptor instead of closing it over an inode that may have no name."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "release_preread_fails")
    if not module._XATTR_SUPPORTED or module._PROC_FD_DIR is None:
        pytest.skip("no xattr layer or descriptor directory here")
    staging = tmp_path / "staging"
    reports = staging / "_reports"
    reports.mkdir(parents=True)
    real_install, real_fstat = module._install_posix_acl_policy, module.os.fstat
    acts: list[str] = []

    def install_fails(dirfd, src_name, dst_fd, dst_name):
        raise OSError(5, "injected policy failure")     # the stage stays intact: quarantine links it

    def fstat_hook(f_, *a, **k):
        f = sys._getframe(1)
        if f.f_code.co_name == "_remove_stage_if_another_name_remains" and not acts:
            acts.append("preread")
            for p in list(reports.iterdir()):
                if p.name.startswith("scan_report.unpublished") or p.name.startswith(".scan_report_"):
                    os.unlink(p)                       # both names taken before the first read, and the read fails
            raise OSError(errno.EIO, "injected: the pre-read fails")
        return real_fstat(f_, *a, **k)

    module._install_posix_acl_policy, module.os.fstat = install_fails, fstat_hook
    try:
        with pytest.raises(Exception):
            module.write_report(str(staging), [("docs/H.md", 1, "SECRET", "generic_key_assignment", "contents")])
    finally:
        module._install_posix_acl_policy, module.os.fstat = real_install, real_fstat
    if not acts:
        pytest.skip("the release helper's pre-read was never reached; this arm measured nothing")
    assert _findings_anywhere(reports, "docs/H.md:1"), (
        "REPAIRED: the release helper's first link-count read failed and it answered True from nothing; "
        "quarantine and write_report trusted it and the close freed the findings")


# =============================================================================================
# GROUP 61 — the fifty-third round. Gate 48's cold leg on 2fb1625: preservation linked the
# canonical inode into a reserved name at whatever mode it had and narrowed it afterwards (a
# 0644 findings report was readable under a well-known second name for the window, and for
# good if the process died in it), kept the reserved name when the strip was then denied; and
# the release helper's failed first read answered False without the copy it exists to make.
# =============================================================================================


def _plant_wide_findings_report(module, reports: Path) -> None:
    (reports / "scan_report.txt").write_text("generic\tkey\tassignment\tdocs/W.md:1\n", encoding="utf-8")
    os.chmod(reports / "scan_report.txt", 0o644)


def test_preservation_narrows_the_held_inode_before_it_takes_a_reserved_name(tmp_path: Path) -> None:
    """REPAIRED (cold #3, gate 48): the reserved superseded name is created at 0600, never at the
    canonical inode's old mode — the narrowing runs on the held descriptor BEFORE the link."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "preserve_narrow_first")
    if not module._XATTR_SUPPORTED or module._PROC_FD_DIR is None:
        pytest.skip("no xattr layer or descriptor directory here")
    reports = tmp_path / "staging" / "_reports"
    reports.mkdir(parents=True)
    _plant_wide_findings_report(module, reports)
    real_link = module.os.link
    modes_at_link: list[int] = []

    def link_hook(src, dst, *a, **k):
        f = sys._getframe(1)
        if f.f_code.co_name == "_link_held_inode":
            modes_at_link.append(stat.S_IMODE(os.fstat(f.f_locals["fd"]).st_mode))
        return real_link(src, dst, *a, **k)

    module.os.link = link_hook
    dirfd = os.open(reports, os.O_RDONLY | os.O_DIRECTORY)
    try:
        answer = module._preserve_superseded(dirfd, "scan_report.txt")
    finally:
        module.os.link = real_link
        os.close(dirfd)
    if not modes_at_link:
        pytest.skip("preservation never linked; this arm measured nothing")
    assert answer is True
    assert all(m == 0o600 for m in modes_at_link), (
        f"REPAIRED: the reserved name was linked while the inode was at {[oct(m) for m in modes_at_link]}; "
        "a well-known second name carried the old mode for the window, and for good if the process died in it")
    slots = [p for p in reports.iterdir() if p.name.startswith("scan_report.superseded")]
    assert slots and all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in slots)


def test_preservation_takes_no_reserved_name_when_the_strip_is_denied(tmp_path: Path) -> None:
    """REPAIRED (cold #3 sibling, gate 48): a reserved name asserts the policy; with the strip
    denied the held inode gets no reserved name at all (as the copy-out already does), and the
    replacement is declined."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "preserve_strip_denied")
    if not module._XATTR_SUPPORTED or module._PROC_FD_DIR is None:
        pytest.skip("no xattr layer or descriptor directory here")
    reports = tmp_path / "staging" / "_reports"
    reports.mkdir(parents=True)
    _plant_wide_findings_report(module, reports)
    real_strip = module._strip_acl_by_fd
    denied: list[str] = []

    def strip_denied(fd):
        if sys._getframe(1).f_code.co_name == "_narrow_held_copy":
            denied.append("x"); raise OSError(errno.EACCES, "injected: strip denied")
        return real_strip(fd)

    module._strip_acl_by_fd = strip_denied
    dirfd = os.open(reports, os.O_RDONLY | os.O_DIRECTORY)
    try:
        answer = module._preserve_superseded(dirfd, "scan_report.txt")
    finally:
        module._strip_acl_by_fd = real_strip
        os.close(dirfd)
    if not denied:
        pytest.skip("the strip was never attempted; this arm measured nothing")
    assert answer is False
    slots = [p.name for p in reports.iterdir() if p.name.startswith("scan_report.superseded")]
    assert not slots, (
        f"REPAIRED: a reserved name {slots} was taken for an inode whose policy could not be installed; "
        "the name asserts a policy the inode does not carry")
    assert (reports / "scan_report.txt").exists()   # the findings stand where they were


def test_a_release_pre_read_that_fails_still_copies_the_bytes_out(tmp_path: Path) -> None:
    """REPAIRED (cold #2, gate 48): the release helper's failed first read is "cannot tell" like its
    post-unlink read — a copy is attempted through the descriptor, one level deep, instead of
    answering False and letting the copy-out's finally close a stage that may be nameless."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "release_preread_copies")
    if not module._XATTR_SUPPORTED or module._PROC_FD_DIR is None:
        pytest.skip("no xattr layer or descriptor directory here")
    reports = tmp_path / "staging" / "_reports"
    reports.mkdir(parents=True)
    src = reports / "source.txt"
    src.write_text("generic\tkey\tassignment\tdocs/H.md:1\n", encoding="utf-8")
    fd = os.open(src, os.O_RDWR)
    os.unlink(src)                                   # the descriptor is the last reference: the copy-out's case
    real_fstat = module.os.fstat
    acts: list[str] = []

    def fstat_hook(f_, *a, **k):
        f = sys._getframe(1)
        if f.f_code.co_name == "_remove_stage_if_another_name_remains" and not acts:
            acts.append("preread")
            for p in list(reports.iterdir()):
                if p.name.startswith("scan_report.unpublished") or p.name.startswith(".scan_report_"):
                    os.unlink(p)                       # both of the copy's names taken before the first read
            raise OSError(errno.EIO, "injected: the pre-read fails")
        return real_fstat(f_, *a, **k)

    module.os.fstat = fstat_hook
    dirfd = os.open(reports, os.O_RDONLY | os.O_DIRECTORY)
    try:
        answer = module._copy_out_unpublished(dirfd, fd, 0)
    finally:
        module.os.fstat = real_fstat
        os.close(dirfd); os.close(fd)
    if not acts:
        pytest.skip("the release helper's pre-read was never reached; this arm measured nothing")
    assert _findings_anywhere(reports, "docs/H.md:1"), (
        "REPAIRED: the helper's first read failed and it answered False without a copy; the copy-out's "
        "finally recorded a hollow copy and its close freed the only complete copy")
    assert answer is True


# =============================================================================================
# GROUP 62 — the fifty-fourth round. Gate 49's cold leg on 884e6c2: preservation asked the
# last-reference question only when NO slot was linked; after a confirmed link it closed the
# held canonical descriptor with no question, and both classification closes did the same —
# the one sibling in the module still closing a findings-bearing descriptor unasked.
# =============================================================================================


def _preserve_with_racer(tmp_path: Path, tag: str, occupy_slots: bool, hook: str):
    driver = make_tool(tmp_path)
    module = import_driver(driver, tag)
    if not module._XATTR_SUPPORTED or module._PROC_FD_DIR is None:
        pytest.skip("no xattr layer or descriptor directory here")
    reports = tmp_path / "staging" / "_reports"
    reports.mkdir(parents=True)
    (reports / "scan_report.txt").write_text("generic\tkey\tassignment\tdocs/W.md:1\n", encoding="utf-8")
    if occupy_slots:
        for slot in module._superseded_slot_names():
            (reports / slot).write_text("other\tkey\tassignment\tdocs/O.md:1\n", encoding="utf-8")
    acts: list[str] = []

    def _take_names(names):
        for p in list(reports.iterdir()):
            if p.name in names:
                os.unlink(p)
        acts.append("took:" + ",".join(sorted(names)))

    real_lstat, real_prefix = module.os.lstat, module._read_prefix_held

    def lstat_hook(path, *a, **k):
        r = real_lstat(path, *a, **k)
        f = sys._getframe(1)
        if hook == "after_link_confirmation" and f.f_code.co_name == "_link_held_inode" and not acts:
            _take_names({"scan_report.txt", str(path)})   # the racer's two acts, after the confirmation, before the close
        return r

    def prefix_hook(fd, via_proc, count):
        r = real_prefix(fd, via_proc, count)
        if hook in ("after_prefix_read_linked", "after_prefix_read_no_slot") and not acts:
            names = {"scan_report.txt"} | ({p.name for p in reports.iterdir() if p.name.startswith("scan_report.superseded")}
                                          if hook == "after_prefix_read_linked" else set())
            _take_names(names)                            # during the classification, before that descriptor's close
        return r

    module.os.lstat, module._read_prefix_held = lstat_hook, prefix_hook
    dirfd = os.open(reports, os.O_RDONLY | os.O_DIRECTORY)
    try:
        answer = module._preserve_superseded(dirfd, "scan_report.txt")
    finally:
        module.os.lstat, module._read_prefix_held = real_lstat, real_prefix
        os.close(dirfd)
    if not acts:
        pytest.skip(f"the racer never acted ({hook}); this arm measured nothing")
    return module, reports, answer, acts


@pytest.mark.parametrize("hook", ["after_link_confirmation", "after_prefix_read_linked", "after_prefix_read_no_slot"])
def test_preservation_asks_the_last_reference_question_before_every_findings_close(tmp_path: Path, hook: str) -> None:
    """REPAIRED (cold #1, gate 49): a findings-bearing descriptor preservation holds is copied out
    before its close when no name reaches the inode any more — after a confirmed link, after the
    classification through the slot, and after the classification reopen with no slot."""
    module, reports, answer, acts = _preserve_with_racer(tmp_path, "preserve_close_" + hook,
                                                         occupy_slots=(hook == "after_prefix_read_no_slot"), hook=hook)
    assert _findings_anywhere(reports, "docs/W.md:1"), (
        f"REPAIRED ({hook}): the names were taken while preservation held the last reference and its close "
        f"freed the previous report's findings (answer={answer}, acts={acts})")


def test_a_cancellation_inside_the_pre_link_narrowing_does_not_leak_the_held_descriptor(tmp_path: Path) -> None:
    """REPAIRED (inventory p.6, gate 49): the narrowing before the link runs under the same
    cancellation guard as the one after it — an interrupt inside it closes the held descriptor
    before propagating (884e6c2 leaked it; c3f5bb3 did not, having no pre-link narrowing)."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "preserve_prenarrow_kbi")
    if not module._XATTR_SUPPORTED or module._PROC_FD_DIR is None:
        pytest.skip("no xattr layer or descriptor directory here")
    reports = tmp_path / "staging" / "_reports"
    reports.mkdir(parents=True)
    (reports / "scan_report.txt").write_text("generic\tkey\tassignment\tdocs/W.md:1\n", encoding="utf-8")
    real_strip = module._strip_acl_by_fd
    fired: list[str] = []

    def strip_interrupted(fd):
        f1, f2 = sys._getframe(1), sys._getframe(2)
        if f1.f_code.co_name == "_narrow_held_copy" and f2.f_code.co_name == "_preserve_superseded" and not fired:
            fired.append("kbi"); raise KeyboardInterrupt()
        return real_strip(fd)

    def _held_report_fds() -> list[str]:
        out = []
        for n in os.listdir("/proc/self/fd"):
            try:
                target = os.readlink("/proc/self/fd/%s" % n)
            except OSError:
                continue
            if target.startswith(str(reports / "scan_report.txt")):
                out.append("%s -> %s" % (n, target))
        return out

    module._strip_acl_by_fd = strip_interrupted
    dirfd = os.open(reports, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(KeyboardInterrupt):
            module._preserve_superseded(dirfd, "scan_report.txt")
    finally:
        module._strip_acl_by_fd = real_strip
        os.close(dirfd)
    if not fired:
        pytest.skip("the pre-link narrowing was never reached; this arm measured nothing")
    leaked = _held_report_fds()
    assert not leaked, f"REPAIRED: the interrupt inside the pre-link narrowing left the held descriptor open: {leaked}"
    assert (reports / "scan_report.txt").exists()


# =============================================================================================
# GROUP 63 — the fifty-fifth round. Gate 50's invariant leg on 8dd9edd: the last-reference
# rescue after the link loop ran before classification, so a CLEAN whose names were taken was
# copied to a reserved unpublished name; the cancellation closes in preservation (the pre-link
# narrowing, the slot classification) and the already-preserved-slot close were still unasked.
# =============================================================================================


def _preserve_arm_setup(tmp_path: Path, tag: str, body: str):
    driver = make_tool(tmp_path)
    module = import_driver(driver, tag)
    if not module._XATTR_SUPPORTED or module._PROC_FD_DIR is None:
        pytest.skip("no xattr layer or descriptor directory here")
    reports = tmp_path / "staging" / "_reports"
    reports.mkdir(parents=True)
    (reports / "scan_report.txt").write_text(body, encoding="utf-8")
    return module, reports


def _take(reports: Path, names) -> None:
    for p in list(reports.iterdir()):
        if p.name in names:
            os.unlink(p)


def test_a_clean_whose_names_were_taken_is_never_copied_to_a_reserved_name(tmp_path: Path) -> None:
    """REPAIRED (invariant leg #4, gate 50): the last-reference rescue reads the prefix through the
    descriptor first — a status line is not evidence and never takes a reserved unpublished name."""
    module, reports = _preserve_arm_setup(tmp_path, "preserve_clean_not_copied", "scan_gate: CLEAN\n")
    real_lstat = module.os.lstat
    acts: list[str] = []

    def lstat_hook(path, *a, **k):
        r = real_lstat(path, *a, **k)
        if sys._getframe(1).f_code.co_name == "_link_held_inode" and not acts:
            acts.append("took"); _take(reports, {"scan_report.txt", str(path)})
        return r

    module.os.lstat = lstat_hook
    dirfd = os.open(reports, os.O_RDONLY | os.O_DIRECTORY)
    try:
        module._preserve_superseded(dirfd, "scan_report.txt")
    finally:
        module.os.lstat = real_lstat
        os.close(dirfd)
    if not acts:
        pytest.skip("the racer never acted; this arm measured nothing")
    reserved = [p.name for p in reports.iterdir() if p.name.startswith("scan_report.unpublished")]
    assert not reserved, f"REPAIRED: a CLEAN was copied to a reserved unpublished name {reserved} — a false authorization beside an exit status of 2"


@pytest.mark.parametrize("site", ["pre_link_narrowing", "slot_classification", "already_preserved_slot"])
def test_preservations_remaining_closes_ask_before_closing(tmp_path: Path, site: str) -> None:
    """REPAIRED (invariant leg #1–#3, gate 50): a cancellation inside the pre-link narrowing or the
    slot classification, and the already-preserved-slot cleanup, close a findings-bearing descriptor
    only after the last-reference question — the racer's acts before them are answered by a copy."""
    module, reports = _preserve_arm_setup(tmp_path, "preserve_close_" + site, "generic\tkey\tassignment\tdocs/W.md:1\n")
    if site == "already_preserved_slot":
        for slot in module._superseded_slot_names():
            (reports / slot).write_text("other\tkey\tassignment\tdocs/O.md:1\n", encoding="utf-8")
    real_strip, real_prefix = module._strip_acl_by_fd, module._read_prefix_held
    acts: list[str] = []
    strips: list[int] = []

    def strip_hook(fd):
        f1, f2 = sys._getframe(1), sys._getframe(2)
        if f1.f_code.co_name == "_narrow_held_copy" and f2.f_code.co_name == "_preserve_superseded":
            strips.append(fd)
            if site == "pre_link_narrowing" and len(strips) == 1:
                acts.append("took+kbi"); _take(reports, {"scan_report.txt"}); raise KeyboardInterrupt()
            if site == "already_preserved_slot" and len(strips) == 2:
                acts.append("took"); _take(reports, {"scan_report.txt"} | set(module._superseded_slot_names()))
        return real_strip(fd)

    def prefix_hook(fd, via_proc, count):
        if site == "slot_classification" and not acts:
            acts.append("took+kbi")
            _take(reports, {"scan_report.txt"} | {p.name for p in reports.iterdir() if p.name.startswith("scan_report.superseded")})
            raise KeyboardInterrupt()
        r = real_prefix(fd, via_proc, count)
        if site == "already_preserved_slot" and not acts:
            slot = next(iter(module._superseded_slot_names()))
            os.unlink(reports / slot); os.link(reports / "scan_report.txt", reports / slot)   # preserved meanwhile by another run
            acts.append("slot-appeared")
        return r

    module._strip_acl_by_fd, module._read_prefix_held = strip_hook, prefix_hook
    dirfd = os.open(reports, os.O_RDONLY | os.O_DIRECTORY)
    try:
        if site == "already_preserved_slot":
            module._preserve_superseded(dirfd, "scan_report.txt")
        else:
            with pytest.raises(KeyboardInterrupt):
                module._preserve_superseded(dirfd, "scan_report.txt")
    finally:
        module._strip_acl_by_fd, module._read_prefix_held = real_strip, real_prefix
        os.close(dirfd)
    if site == "already_preserved_slot" and "took" not in acts:
        pytest.skip(f"the already-preserved branch was not reached (acts={acts}); this arm measured nothing")
    if site != "already_preserved_slot" and not acts:
        pytest.skip("the injection never fired; this arm measured nothing")
    assert _findings_anywhere(reports, "docs/W.md:1"), (
        f"REPAIRED ({site}): the descriptor was closed unasked while it was the last reference; the findings are gone (acts={acts})")


def test_a_clean_whose_name_diverged_with_no_slot_is_never_copied_to_a_reserved_name(tmp_path: Path) -> None:
    """REPAIRED (executed review v-j, gate 50): the no-slot rescue in preservation asked only whether the
    canonical name still held the inode; a CLEAN whose name was taken was copied to a reserved
    unpublished name. The last-reference question there reads the prefix first, like the others."""
    module, reports = _preserve_arm_setup(tmp_path, "preserve_clean_noslot", "scan_gate: CLEAN\n")
    for slot in module._superseded_slot_names():
        (reports / slot).write_text("other\tkey\tassignment\tdocs/O.md:1\n", encoding="utf-8")   # no slot free
    real_link = module.os.link
    acts: list[str] = []

    def link_hook(src, dst, *a, **k):
        if sys._getframe(1).f_code.co_name == "_link_held_inode" and not acts:
            acts.append("took"); _take(reports, {"scan_report.txt"})   # the canonical name goes while the slots are tried
        return real_link(src, dst, *a, **k)

    module.os.link = link_hook
    dirfd = os.open(reports, os.O_RDONLY | os.O_DIRECTORY)
    try:
        module._preserve_superseded(dirfd, "scan_report.txt")
    finally:
        module.os.link = real_link
        os.close(dirfd)
    if not acts:
        pytest.skip("the racer never acted; this arm measured nothing")
    reserved = [p.name for p in reports.iterdir() if p.name.startswith("scan_report.unpublished")]
    assert not reserved, f"REPAIRED: a CLEAN whose name diverged was copied to a reserved unpublished name {reserved}"


def test_a_cancellation_inside_the_held_copy_opener_does_not_close_the_last_reference_unasked(tmp_path: Path) -> None:
    """REPAIRED (cold #2 fourth instance, gate 50): the opener's own cancellation guard, between the
    open and the return, asks the last-reference question before it closes what it just opened."""
    module, reports = _preserve_arm_setup(tmp_path, "opener_kbi", "generic\tkey\tassignment\tdocs/W.md:1\n")
    real_fstat = module.os.fstat
    acts: list[str] = []

    def fstat_hook(f_, *a, **k):
        if sys._getframe(1).f_code.co_name == "_open_held_copy" and not acts:
            acts.append("took+kbi"); _take(reports, {"scan_report.txt"}); raise KeyboardInterrupt()
        return real_fstat(f_, *a, **k)

    module.os.fstat = fstat_hook
    dirfd = os.open(reports, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(KeyboardInterrupt):
            module._preserve_superseded(dirfd, "scan_report.txt")
    finally:
        module.os.fstat = real_fstat
        os.close(dirfd)
    if not acts:
        pytest.skip("the opener's identity read was never reached; this arm measured nothing")
    assert _findings_anywhere(reports, "docs/W.md:1"), (
        "REPAIRED: the opener closed the descriptor it had just opened, unasked, while the name was already gone")


# =============================================================================================
# GROUP 64 — the fifty-sixth round. Gate 51's cold leg on f863349: the no-slot arm of
# preservation asked the last-reference question and then never closed the descriptor it had
# asked about, so the hold leaked to process exit and a name taken afterwards took the findings
# with it; the held-copy opener's successful return sat outside its own cancellation guard; and
# the staged rescue copied a status line to a reserved name (invariant leg, same gate).
# =============================================================================================


def _open_report_fds(reports: Path, name: str = "scan_report.txt") -> list:
    out = []
    for n in os.listdir("/proc/self/fd"):
        try:
            target = os.readlink("/proc/self/fd/%s" % n)
        except OSError:
            continue
        if target.startswith(str(reports / name)):
            out.append("%s -> %s" % (n, target))
    return out


def test_preservation_closes_the_held_canonical_when_no_slot_took_it(tmp_path: Path) -> None:
    """REPAIRED (cold #1, gate 51): the no-slot exit asked the last-reference question and left the
    descriptor open. A leaked hold is the last reference once the canonical name goes, and nothing
    runs on the way to process exit: the question and the close are one act at every exit."""
    module, reports = _preserve_arm_setup(tmp_path, "preserve_noslot_close", "generic\tkey\tassignment\tdocs/W.md:1\n")
    for slot in module._superseded_slot_names():
        (reports / slot).write_text("other\tkey\tassignment\tdocs/O.md:1\n", encoding="utf-8")   # every slot occupied
    before = _open_report_fds(reports)
    dirfd = os.open(reports, os.O_RDONLY | os.O_DIRECTORY)
    try:
        answer = module._preserve_superseded(dirfd, "scan_report.txt")
    finally:
        os.close(dirfd)
    leaked = [f for f in _open_report_fds(reports) if f not in before]
    assert not leaked, (
        f"REPAIRED: the no-slot exit left the held canonical descriptor open ({leaked}); it is the last "
        f"reference the moment the name goes, and no code runs before process exit (answer={answer})")


def test_the_held_copy_openers_success_return_is_inside_its_cancellation_guard(tmp_path: Path) -> None:
    """REPAIRED (cold #2, gate 51): the opener's comment owns the interval from the open to the RETURN,
    but the return statement sat outside the try, so a cancellation delivered between the identity
    check and the return closed nothing. Structural, because the window is one bytecode boundary
    that no injection can address: the return must be inside the guarded try."""
    driver = make_tool(tmp_path)
    source = Path(driver).read_text(encoding="utf-8")
    tree = ast.parse(source)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_open_held_copy")
    guarded = [t for t in ast.walk(fn) if isinstance(t, ast.Try)
               and any(isinstance(h.type, ast.Name) and h.type.id == "BaseException" for h in t.handlers)]
    assert guarded, "the opener has no BaseException guard at all"
    returns_of_fd = [r for r in ast.walk(fn) if isinstance(r, ast.Return)
                     and isinstance(r.value, ast.Tuple) and len(r.value.elts) == 2
                     and isinstance(r.value.elts[0], ast.Name) and r.value.elts[0].id == "fd"]
    assert returns_of_fd, "the opener no longer returns (fd, via_proc)"
    inside = [r for r in returns_of_fd
              if any(r in list(ast.walk(t)) for t in guarded)]
    assert len(inside) == len(returns_of_fd), (
        "REPAIRED: the opener's successful return sits outside the cancellation guard that claims to own "
        "the interval up to it; an interrupt there hands the caller nothing and closes nothing")


def test_the_staged_rescue_does_not_copy_a_status_line_to_a_reserved_name(tmp_path: Path) -> None:
    """REPAIRED (invariant leg, gate 51): the name-identity rescue on a staged file copied whatever it
    held. A staged CLEAN or REFUSED is not evidence — the README says a reserved name means retained
    evidence — so the rescue reads the prefix first, as the last-reference question already does."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "staged_rescue_status_line")
    if module._PROC_FD_DIR is None:
        pytest.skip("no descriptor directory here; the rescue cannot run")
    reports = tmp_path / "staging" / "_reports"
    reports.mkdir(parents=True)
    stage = reports / ".scan_report_probe"
    stage.write_text("scan_gate: CLEAN\n", encoding="utf-8")
    fd = os.open(stage, os.O_RDWR)
    decoy = reports / "decoy.txt"
    decoy.write_text("scan_gate: CLEAN\n", encoding="utf-8")
    os.replace(decoy, stage)                      # the staged name now reaches a different inode
    dirfd = os.open(reports, os.O_RDONLY | os.O_DIRECTORY)
    try:
        answer = module._false_or_rescue(dirfd, ".scan_report_probe", fd)
    finally:
        os.close(dirfd); os.close(fd)
    reserved = [p.name for p in reports.iterdir() if p.name.startswith("scan_report.unpublished")]
    assert not reserved, (
        f"REPAIRED: a staged status line was copied to a reserved name {reserved} — a file saying CLEAN "
        f"under a name that means retained evidence, beside an exit status of 2 (answer={answer})")


# =============================================================================================
# GROUP 65 — the fifty-seventh round. Gate 52's invariant leg on 212e683: quarantine's own
# diverged arm calls the copy-out directly, so the status-line test the staged rescue had just
# been given was bypassed at the one site that reaches it most often; and the copy-out's source
# descriptor was acquired outside the block whose finally closes it.
# =============================================================================================


def test_quarantine_does_not_copy_a_staged_status_line_to_a_reserved_name(tmp_path: Path) -> None:
    """REPAIRED (invariant leg, gate 52): quarantine's diverged arm reaches the copy-out without going
    through the name-identity rescue, so the status-line test added there did not cover it. A staged
    CLEAN or REFUSED whose name has diverged takes no reserved name by any route."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "quarantine_status_line")
    if module._PROC_FD_DIR is None:
        pytest.skip("no descriptor directory here; the rescue cannot run")
    reports = tmp_path / "staging" / "_reports"
    reports.mkdir(parents=True)
    stage = reports / ".scan_report_probe"
    stage.write_text("scan_gate: CLEAN\n", encoding="utf-8")
    fd = os.open(stage, os.O_RDWR)
    decoy = reports / "decoy.txt"
    decoy.write_text("scan_gate: CLEAN\n", encoding="utf-8")
    os.replace(decoy, stage)                       # the staged name reaches another inode now
    real_holds = module._staged_holds_evidence
    module._staged_holds_evidence = lambda f, h: True   # the conservative answer, as a failed fstat gives
    dirfd = os.open(reports, os.O_RDONLY | os.O_DIRECTORY)
    try:
        answer = module._quarantine_unpublished(dirfd, ".scan_report_probe", fd,
                                               [("docs/H.md", 1, "S", "generic_key_assignment", "c")])
    finally:
        module._staged_holds_evidence = real_holds
        os.close(dirfd); os.close(fd)
    reserved = [p.name for p in reports.iterdir() if p.name.startswith("scan_report.unpublished")]
    assert not reserved, (
        f"REPAIRED: quarantine copied a staged status line to a reserved name {reserved} — the namespace "
        f"that means retained evidence, holding a file that says this tree passed (answer={answer})")


def test_the_copy_outs_source_descriptor_is_acquired_inside_the_block_that_closes_it(tmp_path: Path) -> None:
    """REPAIRED (invariant leg, gate 52): the source reopen sat above the try whose finally closes it,
    so a cancellation in the gap leaked it to process exit. Structural, like the opener's return:
    the window is a statement boundary no injection can address."""
    driver = make_tool(tmp_path)
    tree = ast.parse(Path(driver).read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_copy_out_unpublished")
    closers = [t for t in ast.walk(fn) if isinstance(t, ast.Try)
               and any(isinstance(c, ast.Expr) and isinstance(c.value, ast.Call)
                       and getattr(c.value.func, "id", "") == "_close_quietly"
                       and any(getattr(a, "id", "") == "src" for a in c.value.args)
                       for c in ast.walk(t) if isinstance(c, ast.Expr))]
    assert closers, "the copy-out no longer closes its source descriptor in a finally"
    assigns = [a for a in ast.walk(fn) if isinstance(a, ast.Assign)
               and any(getattr(t_, "id", "") == "src" for t_ in a.targets)
               and isinstance(a.value, ast.Call) and getattr(a.value.func, "attr", "") == "open"]
    assert assigns, "the copy-out no longer opens a source descriptor"
    guarded = [a for a in assigns
               if any(a in list(ast.walk(t)) for t in closers)
               or any(a in list(ast.walk(t)) for t in ast.walk(fn)
                      if isinstance(t, ast.Try)
                      and any(isinstance(h.type, ast.Name) and h.type.id == "BaseException" for h in t.handlers))]
    assert len(guarded) == len(assigns), (
        "REPAIRED: the source descriptor is acquired outside every block that would close it; a "
        "cancellation between the open and the owning try leaks it to process exit")


def test_preservation_closes_the_classification_descriptor_for_a_status_line_too(tmp_path: Path) -> None:
    """REPAIRED (executed review, gate 52): the classification's finally both ASKS and CLOSES, and it was
    gated on the report not being a status line — so a CLEAN left the descriptor open. The question
    already lets a status line go on its own; the close must not be conditional on the answer."""
    module, reports = _preserve_arm_setup(tmp_path, "preserve_clean_classify_close", "scan_gate: CLEAN\n")
    for slot in module._superseded_slot_names():
        (reports / slot).write_text("other\tkey\tassignment\tdocs/O.md:1\n", encoding="utf-8")   # no slot free
    before = _open_report_fds(reports)
    dirfd = os.open(reports, os.O_RDONLY | os.O_DIRECTORY)
    try:
        answer = module._preserve_superseded(dirfd, "scan_report.txt")
    finally:
        os.close(dirfd)
    leaked = [f for f in _open_report_fds(reports) if f not in before]
    assert not leaked, (
        f"REPAIRED: the classification left its descriptor open for a status line ({leaked}); the close "
        f"was conditional on the classification's own answer (answer={answer})")


def test_an_unreadable_status_line_is_repaired_before_it_is_classified(tmp_path: Path) -> None:
    """REPAIRED (cold #1, gate 52): the last-reference question classified a path-only descriptor BEFORE
    anyone repaired owner-read, so a CLEAN at mode 000 read as unreadable, fell to the findings side,
    and the copy-out — which does repair owner-read — copied `scan_gate: CLEAN` into the namespace that
    means retained evidence. The repair happens first, so the classification reads what is there."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "unreadable_status_line")
    if module._PROC_FD_DIR is None or not getattr(os, "O_PATH", 0):
        pytest.skip("no descriptor directory or no O_PATH here")
    reports = tmp_path / "staging" / "_reports"
    reports.mkdir(parents=True)
    victim = reports / "scan_report.txt"
    victim.write_text("scan_gate: CLEAN\n", encoding="utf-8")
    os.chmod(victim, 0o000)
    fd = os.open(victim, os.O_PATH)
    os.unlink(victim)                                  # the descriptor is the last reference now
    dirfd = os.open(reports, os.O_RDONLY | os.O_DIRECTORY)
    try:
        module._rescue_before_close(dirfd, fd, True)
    finally:
        os.close(dirfd); os.close(fd)
    reserved = [p.name for p in reports.iterdir() if p.name.startswith("scan_report.unpublished")]
    assert not reserved, (
        f"REPAIRED: an unreadable status line was classified as findings and copied to a reserved name "
        f"{reserved}; the copy-out repaired owner-read that the classification never tried")


def test_the_opener_asks_before_closing_when_the_identity_read_itself_fails(tmp_path: Path) -> None:
    """REPAIRED (cold #3, gate 52): an identity read that FAILED closed the descriptor without asking. A
    question this code cannot answer never authorizes destruction — the same rule the staged-evidence
    helper is built on — so the last-reference question runs there too, at worst a duplicate."""
    module, reports = _preserve_arm_setup(tmp_path, "opener_identity_eio", "generic\tkey\tassignment\tdocs/W.md:1\n")
    expect = os.lstat(reports / "scan_report.txt")
    real_fstat = module.os.fstat
    acts: list[str] = []

    def fstat_hook(f_, *a, **k):
        if sys._getframe(1).f_code.co_name == "_open_held_copy" and not acts:
            acts.append("took+eio")
            _take(reports, {"scan_report.txt"})        # the only name goes, then the read fails
            raise OSError(errno.EIO, "injected: the identity read fails")
        return real_fstat(f_, *a, **k)

    module.os.fstat = fstat_hook
    dirfd = os.open(reports, os.O_RDONLY | os.O_DIRECTORY)
    try:
        got, _via = module._open_held_copy(dirfd, "scan_report.txt", expect)
    finally:
        module.os.fstat = real_fstat
        os.close(dirfd)
        if got is not None:
            os.close(got)
    if not acts:
        pytest.skip("the opener's identity read was never reached; this arm measured nothing")
    assert _findings_anywhere(reports, "docs/W.md:1"), (
        "REPAIRED: the identity read failed and the close ran unasked while that descriptor was the "
        "last reference; the previous report's findings are gone")


def test_a_status_line_is_never_left_parked_in_a_superseded_slot(tmp_path: Path) -> None:
    """REPAIRED (inventory, gate 52): the slot is linked before the report is classified, and the release
    of a status line's slot was refused whenever that slot had become the only name — the trade round
    twenty-six refused for EVIDENCE. A status line is not evidence, and a stale CLEAN under a reserved
    name beside an exit status of two is the very thing the reserved namespace must not say."""
    module, reports = _preserve_arm_setup(tmp_path, "status_line_parked", "scan_gate: CLEAN\n")
    real_prefix = module._read_prefix_held
    acts: list[str] = []

    def prefix_hook(fd, via_proc, count):
        r = real_prefix(fd, via_proc, count)
        if not acts:
            acts.append("took"); _take(reports, {"scan_report.txt"})   # the slot becomes the only name
        return r

    module._read_prefix_held = prefix_hook
    dirfd = os.open(reports, os.O_RDONLY | os.O_DIRECTORY)
    try:
        answer = module._preserve_superseded(dirfd, "scan_report.txt")
    finally:
        module._read_prefix_held = real_prefix
        os.close(dirfd)
    if not acts:
        pytest.skip("the classification read was never reached; this arm measured nothing")
    parked = [p.name for p in reports.iterdir()
              if p.name.startswith("scan_report.superseded")
              and p.read_bytes().startswith(b"scan_gate: ")]
    assert not parked, (
        f"REPAIRED: a status line is parked in a reserved slot {parked}; it says this tree passed, under "
        f"a name that means retained evidence, beside an exit status of two (answer={answer})")


# =============================================================================================
# GROUP 66 — the fifty-eighth round. Gate 53's invariant leg on aca6e8a: the slot descriptor is
# guarded only where it is USED, by two disjoint handlers, with plain statements between them
# and no handler over the whole of its life; and quarantine takes a reserved name for whatever
# the staged inode holds when its NAME never diverged — bytes overwritten in place included.
# =============================================================================================


def test_the_slot_descriptors_whole_life_is_under_one_handler(tmp_path: Path) -> None:
    """REPAIRED (invariant leg, gate 53): the descriptor held on the superseded slot was covered only
    inside the two blocks that use it. A cancellation between them — at a bare condition, where no
    call can be hooked — left it open, and it is the last reference once both names go. Structural,
    for the same reason the opener's return arm is: the window is a statement boundary."""
    driver = make_tool(tmp_path)
    tree = ast.parse(Path(driver).read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_preserve_superseded")
    acquisitions = [a for a in ast.walk(fn) if isinstance(a, ast.Assign)
                    and isinstance(a.value, ast.Call)
                    and getattr(a.value.func, "id", "") == "_open_held_copy"
                    and any(isinstance(t, ast.Tuple) and any(getattr(x, "id", "") == "held_fd" for x in t.elts)
                            for t in a.targets)]
    assert acquisitions, "preservation no longer acquires a slot descriptor named held_fd"

    def closes_held_fd(nodes) -> bool:
        for n in nodes:
            for c in ast.walk(n):
                if isinstance(c, ast.Call) and getattr(c.func, "id", "") in ("_close_quietly", "_rescue_then_close"):
                    if any(getattr(a, "id", "") == "held_fd" for a in c.args):
                        return True
        return False

    covered = []
    for a in acquisitions:
        enclosing = [t for t in ast.walk(fn) if isinstance(t, ast.Try) and a in list(ast.walk(t))
                     and (closes_held_fd(t.finalbody) or closes_held_fd(t.handlers))]
        covered.append(bool(enclosing))
    assert all(covered), (
        "REPAIRED: the slot descriptor is acquired outside any handler that closes it; the two blocks "
        "that guard it cover only the calls that use it, and the statements between them are unguarded")


def test_quarantine_takes_no_reserved_name_for_a_stage_overwritten_in_place(tmp_path: Path) -> None:
    """REPAIRED (invariant leg, gate 53): quarantine asked the status-line question only where the staged
    NAME had diverged. Bytes rewritten under the same inode leave the name intact, so a stage holding
    `scan_gate: CLEAN` was linked straight to a reserved name that means retained evidence."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "quarantine_inplace_overwrite")
    if not module._XATTR_SUPPORTED or module._PROC_FD_DIR is None:
        pytest.skip("no xattr layer or descriptor directory here")
    reports = tmp_path / "staging" / "_reports"
    reports.mkdir(parents=True)
    stage = reports / ".scan_report_probe"
    stage.write_text("generic\tkey\tassignment\tdocs/H.md:1\n", encoding="utf-8")
    fd = os.open(stage, os.O_RDWR)
    with open(stage, "wb") as f:                   # rewritten IN PLACE: the same inode, the same name
        f.write(b"scan_gate: CLEAN\n")
    dirfd = os.open(reports, os.O_RDONLY | os.O_DIRECTORY)
    try:
        answer = module._quarantine_unpublished(dirfd, ".scan_report_probe", fd,
                                               [("docs/H.md", 1, "S", "generic_key_assignment", "c")])
    finally:
        os.close(dirfd); os.close(fd)
    reserved = [p.name for p in reports.iterdir() if p.name.startswith("scan_report.unpublished")]
    assert not reserved, (
        f"REPAIRED: a stage whose bytes were rewritten in place took a reserved name {reserved} while "
        f"holding a status line; the name says retained evidence and the file says this tree passed "
        f"(answer={answer})")


def test_a_regular_inode_that_is_not_ours_is_left_exactly_as_it_was(tmp_path: Path) -> None:
    """ADJUDICATED (cold #1 against the inventory, gate 53): the cold leg asked for the last-reference
    rescue on an identity MISMATCH, reading it as the sibling of the failed-read arm. The inventory
    measured what that costs on that arm — a foreign file narrowed and its bytes published under a
    reserved name. "I cannot tell" is conservative toward copying; "this is not the inode we recorded"
    is conservative toward leaving it alone. The mismatch arm touches nothing."""
    module, reports = _preserve_arm_setup(tmp_path, "opener_not_ours", "generic\tkey\tassignment\tdocs/W.md:1\n")
    expect = os.lstat(reports / "scan_report.txt")
    other = reports / "other.txt"
    other.write_text("generic\tkey\tassignment\tdocs/B.md:1\n", encoding="utf-8")
    os.chmod(other, 0o400)
    os.replace(other, reports / "scan_report.txt")   # a DIFFERENT regular inode now holds the name
    before = stat.S_IMODE(os.lstat(reports / "scan_report.txt").st_mode)
    dirfd = os.open(reports, os.O_RDONLY | os.O_DIRECTORY)
    try:
        got, _via = module._open_held_copy(dirfd, "scan_report.txt", expect)
    finally:
        os.close(dirfd)
        if got is not None:
            os.close(got)
    assert got is None, "the opener handed back a descriptor for an inode it did not record"
    reserved = [p.name for p in reports.iterdir() if p.name.startswith("scan_report.unpublished")]
    assert not reserved, (
        f"ADJUDICATED: a foreign inode's bytes were published under a reserved name {reserved}; that name "
        f"asserts THIS scan's retained evidence")
    after = stat.S_IMODE(os.lstat(reports / "scan_report.txt").st_mode)
    assert after == before, (
        f"ADJUDICATED: a foreign inode's mode was changed from {oct(before)} to {oct(after)}; an inode this "
        f"scan did not record is not its to narrow")


def test_the_copy_outs_source_reopen_does_not_wait_on_a_peer(tmp_path: Path) -> None:
    """REPAIRED (cold #2, gate 53): the source reopen had no O_NONBLOCK, while the prefix read on the very
    same descriptor directory has carried it all along. The identity-failure arm added last round can
    hand the question an unverified descriptor, and opening the read end of a FIFO waits for a writer —
    inside a function documented never to block. The wait itself was measured on this box directly; this
    arm reads the flag, which is deterministic and cannot pass by measuring nothing."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "copyout_source_nonblock")
    if module._PROC_FD_DIR is None:
        pytest.skip("no descriptor directory here; the reopen cannot run")
    reports = tmp_path / "staging" / "_reports"
    reports.mkdir(parents=True)
    source = reports / "body.txt"
    source.write_text("generic\tkey\tassignment\tdocs/H.md:1\n", encoding="utf-8")
    fd = os.open(source, os.O_RDWR)
    real_open = module.os.open
    flags: list[int] = []

    def open_hook(path, *a, **k):
        if isinstance(path, str) and path.startswith(str(module._PROC_FD_DIR)) and a:
            flags.append(a[0])
        return real_open(path, *a, **k)

    module.os.open = open_hook
    dirfd = os.open(reports, os.O_RDONLY | os.O_DIRECTORY)
    try:
        module._copy_out_unpublished(dirfd, fd, 0)
    finally:
        module.os.open = real_open
        os.close(dirfd); os.close(fd)
    if not flags:
        pytest.skip("the source reopen never ran; this arm measured nothing")
    nonblock = getattr(os, "O_NONBLOCK", 0)
    assert all(f & nonblock for f in flags), (
        f"REPAIRED: the copy-out reopened its source without O_NONBLOCK (flags {[oct(f) for f in flags]}); "
        f"the read end of a FIFO waits for a writer, inside the path documented as never blocking")


# =============================================================================================
# GROUP 67 — the fifty-ninth round. Gate 54's invariant leg on 2dac6c6: the name-clearing that
# stops the whole-life handler closing a descriptor twice is a plain statement AFTER the rescue,
# so a rescue that raises skips it and the handler closes again.
# =============================================================================================


def test_no_descriptor_is_closed_twice_when_a_rescue_raises(tmp_path: Path) -> None:
    """REPAIRED (invariant leg, gate 54): the whole-life handler added last round is disarmed by clearing
    the local name after each early close — but as a plain statement, so a rescue that RAISES skips it
    and the handler closes the same descriptor again. The clearing belongs under a finally, the shape
    the held-copy opener was already given. A closed descriptor number is reused, so this counts closes
    of numbers not currently open rather than repeats of a number."""
    module, reports = _preserve_arm_setup(tmp_path, "double_close_on_raise", "generic\tkey\tassignment\tdocs/W.md:1\n")
    real_close, real_open, real_strip, real_fstat = module.os.close, module.os.open, module._strip_acl_by_fd, module.os.fstat
    live: set = set()
    stale: list[int] = []
    fired: list[str] = []
    narrowings: list[int] = []

    def open_hook(*a, **k):
        fd = real_open(*a, **k)
        live.add(fd)
        return fd

    def close_hook(fd):
        if fd not in live:
            stale.append(fd)              # closing a number nothing currently holds
        live.discard(fd)
        return real_close(fd)

    def strip_interrupted(fd):
        f1, f2 = sys._getframe(1), sys._getframe(2)
        if f1.f_code.co_name == "_narrow_held_copy" and f2.f_code.co_name == "_preserve_superseded":
            narrowings.append(fd)
            if len(narrowings) == 2 and not fired:
                fired.append("kbi")
                raise KeyboardInterrupt()
        return real_strip(fd)

    def fstat_raises_in_the_question(f_, *a, **k):
        if sys._getframe(1).f_code.co_name == "_rescue_before_close" and fired and "raised" not in fired:
            fired.append("raised")
            raise RuntimeError("injected: the question itself raises")
        return real_fstat(f_, *a, **k)

    for fd0 in list(os.listdir("/proc/self/fd")):
        try:
            live.add(int(fd0))
        except ValueError:
            pass
    module.os.close = close_hook
    module.os.open = open_hook
    module._strip_acl_by_fd = strip_interrupted
    module.os.fstat = fstat_raises_in_the_question
    dirfd = os.open(reports, os.O_RDONLY | os.O_DIRECTORY)
    live.add(dirfd)
    try:
        with pytest.raises(BaseException):
            module._preserve_superseded(dirfd, "scan_report.txt")
    finally:
        module.os.close = real_close
        module.os.open = real_open
        module._strip_acl_by_fd = real_strip
        module.os.fstat = real_fstat
        os.close(dirfd)
    if "raised" not in fired:
        pytest.skip("the question never raised during the unwinding (fired=%s, narrowings=%d); this arm "
                    "measured nothing" % (fired, len(narrowings)))
    assert not stale, (
        "REPAIRED: descriptor number(s) %s were closed while nothing held them — the rescue raised, the "
        "name was never cleared, and the whole-life handler closed again" % (stale,))


@pytest.mark.parametrize("shape", ["reports_is_a_symlink", "reports_is_a_regular_file", "reports_is_a_fifo"])
def test_findings_reach_the_operator_when_no_report_can_be_written(tmp_path: Path, capsys, shape: str) -> None:
    """REPAIRED (cold #1, gate 54): every raise above the stage happens while this run's findings exist
    only in the argument list. The refusal writer is handed the exception and never the hits, so a
    planted CLEAN behind an unusable `_reports` was all a reader saw beside an exit status of two.
    Staging first repaired this one name down; the directory name has nowhere to stage, so the
    findings go to the operator instead, which needs no filesystem at all."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "unwritten_" + shape)
    staging = tmp_path / "staging"
    staging.mkdir()
    target = staging / "_reports"
    if shape == "reports_is_a_symlink":
        (tmp_path / "payload").mkdir()
        os.symlink(tmp_path / "payload", target)
    elif shape == "reports_is_a_regular_file":
        target.write_text("not a directory\n", encoding="utf-8")
    else:
        os.mkfifo(target)
    hits = [("docs/H.md", 1, "SECRET", "generic_key_assignment", "k = '" + "AKIA" + "IOSFODNN7EXAMPLE" + "'"),
            ("docs/J.md", 7, "PERSONAL", "email_address", "someone@example.test")]
    capsys.readouterr()
    with pytest.raises((module.ScanRefused, OSError)):
        module.write_report(str(staging), hits)
    err = capsys.readouterr().err
    assert "docs/H.md:1" in err and "docs/J.md:7" in err, (
        f"REPAIRED ({shape}): the scanner refused before it could stage anything and the findings went "
        f"nowhere — not to a file, not to the operator (stderr was {err!r})")


def test_a_clean_run_that_cannot_write_says_nothing_extra(tmp_path: Path, capsys) -> None:
    """CONTROL for the arm above: the emission is for FINDINGS. A refusal with no hits must stay quiet,
    or the guarantee degrades into noise on every unusable report directory."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "unwritten_clean")
    staging = tmp_path / "staging"
    staging.mkdir()
    (tmp_path / "payload").mkdir()
    os.symlink(tmp_path / "payload", staging / "_reports")
    capsys.readouterr()
    with pytest.raises((module.ScanRefused, OSError)):
        module.write_report(str(staging), [])
    err = capsys.readouterr().err
    assert "hit(s) follow" not in err, f"a CLEAN refusal printed a findings banner: {err!r}"


# =============================================================================================
# GROUP 68 — the sixtieth round. The executed review on 2dac6c6 measured a fourth window of the
# same shape the round before closed at three: the status-line branch closes the slot descriptor
# and clears the name as two statements inside its finally, so a cancellation between them leaves
# the whole-life handler armed over a descriptor that is already closed.
# =============================================================================================


def test_no_descriptor_is_closed_twice_when_a_cancellation_lands_between_close_and_clear(tmp_path: Path) -> None:
    """REPAIRED (executed review, gate 54): the status-line branch's cleanup closes the slot descriptor and
    then clears the local name. A cancellation delivered between those two statements leaves the name
    set, and the whole-life handler closes a descriptor nothing holds. The other three sites were given
    a finally last round; this is the fourth."""
    module, reports = _preserve_arm_setup(tmp_path, "close_clear_window", "scan_gate: CLEAN\n")
    real_quietly, real_close, real_open = module._close_quietly, module.os.close, module.os.open
    live: set = set()
    stale: list = []
    fired: list = []

    def open_hook(*a, **k):
        fd = real_open(*a, **k)
        live.add(fd)
        return fd

    def close_hook(fd):
        if fd not in live:
            stale.append(fd)
        live.discard(fd)
        return real_close(fd)

    def quietly_hook(fd):
        caller = sys._getframe(1).f_code.co_name
        real_quietly(fd)
        if caller == "_preserve_superseded" and not fired:
            fired.append("kbi")           # delivered after the close, before the name is cleared
            raise KeyboardInterrupt()

    for fd0 in os.listdir("/proc/self/fd"):
        try:
            live.add(int(fd0))
        except ValueError:
            pass
    module.os.close, module.os.open, module._close_quietly = close_hook, open_hook, quietly_hook
    dirfd = os.open(reports, os.O_RDONLY | os.O_DIRECTORY)
    live.add(dirfd)
    try:
        try:
            module._preserve_superseded(dirfd, "scan_report.txt")
        except BaseException:
            pass
    finally:
        module.os.close, module.os.open, module._close_quietly = real_close, real_open, real_quietly
        os.close(dirfd)
    if not fired:
        pytest.skip("the cleanup close was never reached from preservation; this arm measured nothing")
    assert not stale, (
        "REPAIRED: descriptor number(s) %s were closed while nothing held them — a cancellation landed "
        "between the close and the name-clearing, and the whole-life handler closed again" % (stale,))


def test_findings_reach_the_operator_when_the_stage_itself_cannot_be_made(tmp_path: Path) -> None:
    """REPAIRED (cold #1, gate 55): the emission was wired only to the region that obtains the directory
    descriptor. A stage that cannot be created — a report directory with no write permission, no
    creatable name, no space — fails AFTER that guard with the findings still only in the argument
    list and nothing durable anywhere. Everything above the first durable byte must emit."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "stage_cannot_be_made")
    staging = tmp_path / "staging"
    reports = staging / "_reports"
    reports.mkdir(parents=True)
    os.chmod(reports, 0o500)                       # owner may read and traverse, not create
    hits = [("docs/H.md", 1, "SECRET", "generic_key_assignment", "k = '" + "AKIA" + "IOSFODNN7EXAMPLE" + "'")]
    capsys_err = io.StringIO()
    real_stderr = sys.stderr
    sys.stderr = capsys_err
    try:
        with pytest.raises((module.ScanRefused, OSError)):
            module.write_report(str(staging), hits)
    finally:
        sys.stderr = real_stderr
        os.chmod(reports, 0o700)
    err = capsys_err.getvalue()
    assert "docs/H.md:1" in err, (
        f"REPAIRED: the stage could not be created, nothing durable was written, and the findings went "
        f"nowhere — not to a file, not to the operator (stderr was {err!r})")


def test_a_swap_of_the_stage_name_during_the_rename_does_not_publish_and_then_free_the_findings(tmp_path: Path) -> None:
    """REPAIRED (cold #2, gate 55): the identity check sits before the rename, so a same-uid writer that
    renames a planted symlink onto the staged name in that window has the rename move the PLANT to the
    canonical name. The publish flag was set because the rename returned, the failure handler was then
    skipped, and the close freed a findings inode with no name left. The canonical name is asked again
    after the rename, and a landing that is not ours is not a publication."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "swap_during_rename")
    if not module._XATTR_SUPPORTED or module._PROC_FD_DIR is None:
        pytest.skip("no xattr layer or descriptor directory here")
    staging = tmp_path / "staging"
    reports = staging / "_reports"
    reports.mkdir(parents=True)
    real_replace = module.os.replace
    acts: list[str] = []

    def replace_hook(src, dst, *a, **k):
        if not acts and isinstance(src, str) and src.startswith(".scan_report_"):
            acts.append(src)
            plant = reports / "plant.link"
            os.symlink(tmp_path / "elsewhere", plant)
            real_replace(str(plant), str(reports / src))   # the staged NAME now reaches the plant
        return real_replace(src, dst, *a, **k)

    module.os.replace = replace_hook
    try:
        try:
            module.write_report(str(staging), [("docs/H.md", 1, "SECRET", "generic_key_assignment", "c")])
        except BaseException:
            pass
    finally:
        module.os.replace = real_replace
    if not acts:
        pytest.skip("the rename of the staged name was never reached; this arm measured nothing")
    assert _findings_anywhere(reports, "docs/H.md:1"), (
        f"REPAIRED: the rename moved a planted symlink to the canonical name, the run counted that as a "
        f"publication, and the close freed the only copy of the findings (acts={acts})")


def test_findings_are_never_written_into_a_stage_that_is_not_owner_only(tmp_path: Path) -> None:
    """REPAIRED (cold #3, gate 55): the narrowing before the write is best effort and was never verified,
    so where it did not stick — a report directory carrying a default ACL, or one left at 0755 whose
    group read the hardening keeps — the findings were written into a file group could open. A
    reserved name is refused in that state; the stage held the same bytes under no such rule. A mode
    that cannot be READ is not a wide mode and still proceeds, which is the rule that keeps an
    unreadable stage rather than deleting it."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "stage_not_owner_only")
    staging = tmp_path / "staging"
    reports = staging / "_reports"
    reports.mkdir(parents=True)
    real_narrow, real_fstat = module._narrow_leftover, module.os.fstat
    widened: list = []

    def narrow_that_does_not_stick(fd):
        real_narrow(fd)
        widened.append(fd)

    def fstat_reports_wide(f_, *a, **k):
        st = real_fstat(f_, *a, **k)
        if isinstance(f_, int) and f_ in widened and sys._getframe(1).f_code.co_name == "_stage_report":
            class _Wide:
                st_mode = (st.st_mode & ~0o777) | 0o640
                st_size, st_dev, st_ino, st_nlink = st.st_size, st.st_dev, st.st_ino, st.st_nlink
                st_ctime_ns = getattr(st, "st_ctime_ns", 0)
            return _Wide()
        return st

    module._narrow_leftover, module.os.fstat = narrow_that_does_not_stick, fstat_reports_wide
    dirfd = os.open(reports, os.O_RDONLY | os.O_DIRECTORY)
    try:
        try:
            module._stage_report(dirfd, "SECRET\tgeneric_key_assignment\tk\tdocs/H.md:1\n", evidence=True)
        except BaseException:
            pass
    finally:
        module._narrow_leftover, module.os.fstat = real_narrow, real_fstat
        os.close(dirfd)
    if not widened:
        pytest.skip("the stage narrowing was never reached; this arm measured nothing")
    leftovers = [p for p in reports.iterdir() if p.name.startswith(".scan_report_")]
    bodies = []
    for leftover in leftovers:
        try:
            bodies.append(leftover.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            pass
    assert not any("docs/H.md:1" in b for b in bodies), (
        "REPAIRED: findings were written into a stage whose mode read as wider than owner-only; %s hold "
        "them at whatever the directory gave" % ([p.name for p in leftovers],))


def test_a_cancellation_at_the_rescue_call_itself_still_closes_the_descriptor(tmp_path: Path) -> None:
    """REPAIRED (executed review, gate 55): a REGRESSION from the round before. Clearing the local name
    under a finally stops a double close, but a cancellation delivered AT the call — before the callee's
    own arms run — clears the name over a descriptor nobody closed, and the whole-life handler then
    sees None and never fires. The handler asks the descriptor itself whether it is still ours rather
    than trusting a name, which answers correctly whether the callee ran, did not run, or the number
    was reused."""
    module, reports = _preserve_arm_setup(tmp_path, "cancel_at_rescue_call", "generic\tkey\tassignment\tdocs/W.md:1\n")
    real_rescue = module._rescue_then_close
    fired: list[str] = []

    calls: list = []

    def rescue_interrupted(dirfd_, fd_, via_, depth=0):
        if sys._getframe(1).f_code.co_name == "_preserve_superseded":
            calls.append(fd_)
            # The FIRST call from this frame is the canonical descriptor's own cleanup, which is
            # the last line of defence and has nothing behind it by design. The SECOND is the
            # findings close, which the whole-life handler is there to catch.
            if len(calls) == 2 and not fired:
                fired.append("kbi")
                raise KeyboardInterrupt()  # delivered at the call, before the callee's own arms
        return real_rescue(dirfd_, fd_, via_, depth)

    module._rescue_then_close = rescue_interrupted
    dirfd = os.open(reports, os.O_RDONLY | os.O_DIRECTORY)
    try:
        try:
            module._preserve_superseded(dirfd, "scan_report.txt")
        except BaseException:
            pass
    finally:
        module._rescue_then_close = real_rescue
        os.close(dirfd)
    if not fired:
        pytest.skip("the findings close was never reached from preservation (calls=%d); this arm "
                    "measured nothing" % len(calls))
    leaked = _open_report_fds(reports) + _open_report_fds(reports, "scan_report.superseded.txt")
    assert not leaked, (
        f"REPAIRED: the cancellation landed at the rescue call, the name was cleared over a descriptor "
        f"nobody closed, and the handler never fired: {leaked}")


def test_the_emission_does_not_carry_the_matched_text(tmp_path: Path) -> None:
    """DECIDED (executed review, gate 55): the emission exists so a scan that found secrets cannot tell
    nobody. It does not need to carry the fifth field. The class, the pattern name and the path and
    line say what and where. The error stream is a descriptor this tool did not choose and cannot
    narrow, and an adversary picks the moment it is used, so that field stays out of it.

    THE PREMISE THIS ARM USED TO CARRY WAS FALSE, AND THE FIXTURE BELOW STILL CARRIES IT ON PURPOSE.
    The docstring said the fifth field "is the material". `scan()` never puts material there: it
    writes the literal "content" or "name", which is which ARM fired, and GROUP 79 below pins that
    domain against a real scan so this cannot drift. The fixture here hands in a secret-shaped fifth
    field anyway, because an arm asserting a field is withheld should feed the worst thing that
    field could ever hold — but it is a WORST CASE, not a description of production, and reading it
    as one is how a false sentence survived three rounds (guarantee inventory, 3adf105)."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "emission_no_surface")
    staging = tmp_path / "staging"
    staging.mkdir()
    (tmp_path / "payload").mkdir()
    os.symlink(tmp_path / "payload", staging / "_reports")
    secret = "AKIA" + "IOSFODNN7EXAMPLE" + "-do-not-print-me"
    buf = io.StringIO()
    real_stderr = sys.stderr
    sys.stderr = buf
    try:
        with pytest.raises((module.ScanRefused, OSError)):
            module.write_report(str(staging), [("docs/H.md", 1, "SECRET", "generic_key_assignment", secret)])
    finally:
        sys.stderr = real_stderr
    err = buf.getvalue()
    assert "docs/H.md:1" in err and "generic_key_assignment" in err, (
        f"the emission must still say what and where (stderr was {err!r})")
    assert secret not in err, (
        f"DECIDED: the emission carried the matched text itself onto a stream this tool did not choose "
        f"and cannot narrow (stderr was {err!r})")


def test_a_failure_after_the_stage_does_not_emit(tmp_path: Path) -> None:
    """PINNED (executed review, gate 55): nothing in the suite held the other half of the rule. Once the
    report is staged the bytes are on disk under a name this scanner controls, and the retention paths
    own them; printing as well would put findings on the error stream on every publish failure."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "quiet_after_stage")
    if not module._XATTR_SUPPORTED or module._PROC_FD_DIR is None:
        pytest.skip("no xattr layer or descriptor directory here")
    staging = tmp_path / "staging"
    (staging / "_reports").mkdir(parents=True)
    real_install = module._install_posix_acl_policy
    fired: list[str] = []

    def install_fails(dirfd, src_name, dst_fd, dst_name):
        fired.append("boom")
        raise OSError(errno.EIO, "injected: the publish fails after the stage")

    module._install_posix_acl_policy = install_fails
    buf = io.StringIO()
    real_stderr = sys.stderr
    sys.stderr = buf
    try:
        try:
            module.write_report(str(staging), [("docs/H.md", 1, "SECRET", "generic_key_assignment", "c")])
        except BaseException:
            pass
    finally:
        sys.stderr = real_stderr
        module._install_posix_acl_policy = real_install
    if not fired:
        pytest.skip("the publish never reached the policy install; this arm measured nothing")
    err = buf.getvalue()
    assert "hit(s)" not in err, (
        f"PINNED: a failure AFTER the stage printed findings to the error stream; the bytes were already "
        f"on disk under a name this scanner controls (stderr was {err!r})")
# GROUP 69 — the sixty-first round. `_cfd_still_ours` reads EVERY OSError from its identity
# fstat as "not ours". Both of preservation's cleanup handlers are gated on it, so an EIO or an
# EACCES on a descriptor that is STILL OPEN and is the last reference to a findings inode
# disarms the handler completely: no rescue, no close, and the findings are freed at process
# exit with nothing standing behind them.
# =============================================================================================


def test_a_cleanup_acts_when_the_identity_question_cannot_be_answered(tmp_path: Path) -> None:
    """REPAIRED: EBADF is an ANSWER — the callee closed it, and there is nothing to do. EIO and
    EACCES are not answers: the descriptor may still be open on the recorded inode, and this
    handler is the only thing left that can copy the bytes out before the close frees them. The
    rule this file is built on — a question this code cannot answer never authorizes destruction —
    has a second half here: it must not authorize INACTION either, because doing nothing over a
    last reference is the destruction, arriving at process exit instead of at a close."""
    module, reports = _preserve_arm_setup(tmp_path, "cfd_question_unanswerable",
                                          "generic\tkey\tassignment\tdocs/W.md:1\n")
    for slot in module._superseded_slot_names():
        (reports / slot).write_text("other\tkey\tassignment\tdocs/O.md:1\n", encoding="utf-8")   # no slot free
    real_strip, real_fstat = module._strip_acl_by_fd, module.os.fstat
    acts: list[str] = []
    asked: list[str] = []

    def strip_hook(fd):
        f1, f2 = sys._getframe(1), sys._getframe(2)
        if f1.f_code.co_name == "_narrow_held_copy" and f2.f_code.co_name == "_preserve_superseded" and not acts:
            acts.append("took")
            _take(reports, {"scan_report.txt"})   # the held descriptor is the last reference now
        return real_strip(fd)

    def fstat_cannot_answer(f_, *a, **k):
        if sys._getframe(1).f_code.co_name == "_cfd_still_ours":
            asked.append("eio")
            raise OSError(errno.EIO, "injected: the identity question cannot be answered")
        return real_fstat(f_, *a, **k)

    before = _open_report_fds(reports)
    module._strip_acl_by_fd, module.os.fstat = strip_hook, fstat_cannot_answer
    dirfd = os.open(reports, os.O_RDONLY | os.O_DIRECTORY)
    try:
        answer = module._preserve_superseded(dirfd, "scan_report.txt")
    finally:
        module._strip_acl_by_fd, module.os.fstat = real_strip, real_fstat
        os.close(dirfd)
    if not acts or not asked:
        pytest.skip("the narrowing or the cleanup question was never reached (acts=%s asked=%s); this "
                    "arm measured nothing" % (acts, asked))
    leaked = [f for f in _open_report_fds(reports) if f not in before]
    assert _findings_anywhere(reports, "docs/W.md:1"), (
        f"REPAIRED: the identity fstat failed with EIO, the handler read that as 'not ours', and the last "
        f"reference to the findings was neither rescued nor closed (answer={answer}, leaked={leaked})")
    assert not leaked, (
        f"REPAIRED: the handler did nothing on a question it could not answer and leaked the descriptor to "
        f"process exit ({leaked})")


# =============================================================================================
# GROUP 70 — the sixty-first round. Once a stage exists, write_report's post-stage handler never
# calls `_emit_unwritten_findings`, and the answer `_quarantine_unpublished` and `_false_or_rescue`
# return is DISCARDED. When the staged name has stopped reaching the findings inode AND the
# copy-out declines, the close in the finally is the last reference: nothing on disk, nothing on
# the error stream, and an exit status of 2 with no sign the tree held secrets.
# =============================================================================================


def test_a_retention_answer_of_false_after_the_stage_still_reaches_the_operator(tmp_path: Path) -> None:
    """REPAIRED (the other half of the post-stage rule): "the bytes are on disk" is what makes the
    emission unnecessary after a stage, and the retention path ANSWERS that question — False means
    no custody was taken. The handler threw the answer away, so the one case where the bytes are
    NOT on disk was indistinguishable from the ordinary one. The pin that a failure after the stage
    stays quiet is unchanged: it is quiet because custody was TAKEN, not because a stage existed."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "retention_false_emits")
    if not module._XATTR_SUPPORTED or module._PROC_FD_DIR is None:
        pytest.skip("no xattr layer or descriptor directory here")
    staging = tmp_path / "staging"
    reports = staging / "_reports"
    reports.mkdir(parents=True)
    real_install, real_copy = module._install_posix_acl_policy, module._copy_out_unpublished
    acts: list[str] = []
    declines: list[int] = []

    def install_fails(dirfd_, src_name, dst_fd, dst_name):
        acts.append(dst_name)
        decoy = reports / "decoy.txt"
        decoy.write_text("not the findings\n", encoding="utf-8")
        os.replace(str(decoy), str(reports / dst_name))   # the staged NAME reaches another inode
        raise OSError(errno.EIO, "injected: the publish fails after the stage")

    def copy_out_declines(dirfd_, fd_, depth=0):
        declines.append(depth)
        return False                       # the copy-out's stated limit: no name, or no readable source

    hits = [("docs/H.md", 1, "SECRET", "generic_key_assignment", "k = '" + "AKIA" + "IOSFODNN7EXAMPLE" + "'")]
    module._install_posix_acl_policy, module._copy_out_unpublished = install_fails, copy_out_declines
    buf = io.StringIO()
    real_stderr = sys.stderr
    sys.stderr = buf
    try:
        try:
            module.write_report(str(staging), hits)
        except BaseException:
            pass
    finally:
        sys.stderr = real_stderr
        module._install_posix_acl_policy, module._copy_out_unpublished = real_install, real_copy
    if not acts or len(declines) < 2:
        pytest.skip("the diverged-stage retention path was not reached (acts=%s declines=%s); this arm "
                    "measured nothing" % (acts, declines))
    if _findings_anywhere(reports, "docs/H.md:1"):
        pytest.skip("the findings survived on disk after all; this arm measured nothing")
    err = buf.getvalue()
    assert "docs/H.md:1" in err, (
        f"REPAIRED: the retention path answered False, the close freed the only copy of the findings, and "
        f"the discarded answer meant nothing reached the operator either (stderr was {err!r})")


# =============================================================================================
# GROUP 71 — the sixty-first round. Two plain assignments sit between the `except BaseException`
# that emits for a stage that could not be made and the `try:` whose handlers own the staged
# descriptor. A cancellation delivered at either one leaves the stage on disk with nobody asking
# whether to quarantine it under a reserved name, and no emission.
# =============================================================================================


def test_no_statement_sits_between_the_stage_call_and_the_block_that_owns_its_descriptor(tmp_path: Path) -> None:
    """REPAIRED: structural, for the reason the opener's-return and slot-descriptor arms already are —
    the window is a statement boundary, and an injection aimed at it can only interrupt one of the
    two handlers that bracket it, which measures the handler and not the gap. (This project discarded
    a cancellation arm for exactly that.) `_staged_ctime_ns` and `_published` are initialised from
    constants and depend on nothing the stage call produces, so they belong ABOVE it, where the
    region that emits still covers them and the gap closes to zero statements."""
    driver = make_tool(tmp_path)
    tree = ast.parse(Path(driver).read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "write_report")

    def _blocks(node):
        for field in ("body", "orelse", "finalbody"):
            seq = getattr(node, field, None)
            if isinstance(seq, list) and seq and all(isinstance(s, ast.stmt) for s in seq):
                yield seq
        for handler in getattr(node, "handlers", []):
            yield handler.body

    def _calls_stage(stmts) -> bool:
        return any(isinstance(c, ast.Call) and getattr(c.func, "id", "") == "_stage_report"
                   for s in stmts for c in ast.walk(s))

    def _stages(node) -> bool:
        """The INNERMOST try whose own body makes the stage call — not every ancestor of it."""
        if not isinstance(node, ast.Try) or not _calls_stage(node.body):
            return False
        for s in node.body:
            for d in ast.walk(s):
                if isinstance(d, ast.Try) and d is not node and _calls_stage(d.body):
                    return False
        return True

    gaps = []
    found = False
    for node in ast.walk(fn):
        for stmts in _blocks(node):
            for i, s in enumerate(stmts):
                if not _stages(s):
                    continue
                found = True
                j = next((k for k in range(i + 1, len(stmts)) if isinstance(stmts[k], ast.Try)), None)
                assert j is not None, "nothing owns the staged descriptor after the stage call"
                gaps.append([type(g).__name__ for g in stmts[i + 1:j]])
    assert found, "write_report no longer calls _stage_report inside a guarded block"
    assert all(not g for g in gaps), (
        "REPAIRED: %s statement(s) sit between the stage call's emitting handler and the block whose "
        "handlers own the staged descriptor (%s); a cancellation there leaves the stage on disk with "
        "nobody asking whether to quarantine it, and nothing on the error stream" % (
            sum(len(g) for g in gaps), gaps))


# =============================================================================================
# GROUP 72 — the sixty-first round. `_stage_report`'s pre-write check tests the staged mode with
# `!= 0o600`, an inequality rather than a wideness test. A stage that reads back NARROWER than
# owner-only — 0400, 0200, 0000 — is refused, which destroys this run's report file and pushes
# the whole hit list onto the uncontrolled error stream.
# =============================================================================================


def test_a_stage_narrower_than_owner_only_still_takes_the_findings(tmp_path: Path) -> None:
    """REPAIRED: the rule the comment states is "only a mode read and found WIDER refuses". 0400 is
    not wider than 0600 — it grants strictly less — and refusing it costs the run its report while
    sending the findings to a descriptor this tool did not choose and cannot narrow. The test is
    `mode & 0o077`, which is what "group and other can reach this" actually asks."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "stage_narrower_than_owner_only")
    reports = tmp_path / "staging" / "_reports"
    reports.mkdir(parents=True)
    real_fstat = module.os.fstat
    reads: list = []

    def fstat_reports_narrow(f_, *a, **k):
        st = real_fstat(f_, *a, **k)
        if sys._getframe(1).f_code.co_name == "_stage_report" and not reads:
            reads.append(f_)

            class _Narrow:
                st_mode = (st.st_mode & ~0o777) | 0o400
                st_size, st_dev, st_ino, st_nlink = st.st_size, st.st_dev, st.st_ino, st.st_nlink
                st_ctime_ns = getattr(st, "st_ctime_ns", 0)
            return _Narrow()
        return st

    body = "SECRET\tgeneric_key_assignment\tk\tdocs/H.md:1\n"
    module.os.fstat = fstat_reports_narrow
    dirfd = os.open(reports, os.O_RDONLY | os.O_DIRECTORY)
    fd = None
    refused = None
    try:
        try:
            fd, _name = module._stage_report(dirfd, body, evidence=True)
        except BaseException as exc:
            refused = exc
    finally:
        module.os.fstat = real_fstat
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        os.close(dirfd)
    if not reads:
        pytest.skip("the staged mode was never read; this arm measured nothing")
    bodies = []
    for leftover in reports.iterdir():
        if leftover.name.startswith(".scan_report_"):
            try:
                bodies.append(leftover.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                pass
    assert refused is None and any("docs/H.md:1" in b for b in bodies), (
        f"REPAIRED: a stage that read back at 0400 — narrower than owner-only, not wider — was refused "
        f"({refused!r}); the report was destroyed and the hit list is left with nowhere to go but the "
        f"error stream (leftover bodies {bodies!r})")


# =============================================================================================
# GROUP 73 WAS NOT PORTED. It was written, proven RED on the parent and proven satisfiable, and
# then adjudicated against: it pins a refusal to write findings into a stage whose POSIX ACL strip
# was DENIED. Implementing that refusal turns four arms red —
# `test_the_no_injection_control_retains_the_stage`,
# `test_a_retained_report_whose_policy_failed_is_not_presented_as_compliant`,
# `test_an_unreadable_stage_is_kept_rather_than_deleted` and
# `test_a_cancellation_inside_the_callers_kept_stage_narrowing_still_rescues` — each of which
# carries a CONTROL or REPAIRED marker bought by a past failure, and each of which is right. This
# file already holds that narrowness by PLACEMENT rather than by refusal: the bytes sit under the
# scanner's own temporary prefix, which promises nothing and claims nothing, and no reserved or
# canonical name is ever taken while the strip is denied. Porting GROUP 73 would have spent
# findings-never-lost to buy a guarantee the module already has. The reviewer's finding was real;
# the fix it implied was not the one to make.
# GROUP 74 — the sixty-first round. `_emit_unwritten_findings` ends in `except BaseException:
# pass`, so a KeyboardInterrupt or a SystemExit delivered while it writes is SWALLOWED. Its
# sibling `_write_refusal_report` documents the opposite policy in its own docstring — "a
# cancellation is not a refusal to report".
# =============================================================================================


@pytest.mark.parametrize("cancellation", [KeyboardInterrupt, SystemExit])
def test_the_emission_does_not_swallow_a_cancellation(tmp_path: Path, cancellation) -> None:
    """REPAIRED: "never raises" is about not DISPLACING the exception already on its way out — an
    OSError on the error stream must not become the failure the operator sees. A cancellation is a
    different thing: swallowing it turns an interrupt into a silently-continued run, which is the
    one behaviour the sibling writer's docstring already forbids in this file."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "emission_cancellation_" + cancellation.__name__)

    class _Interrupting(io.StringIO):
        def write(self, s):
            raise cancellation("injected: the operator interrupts the emission")

    hits = [("docs/H.md", 1, "SECRET", "generic_key_assignment", "k"),
            ("docs/J.md", 7, "PERSONAL", "email_address", "someone@example.test")]
    real_stderr = sys.stderr
    sys.stderr = _Interrupting()
    try:
        with pytest.raises(cancellation):
            module._emit_unwritten_findings(hits)
    finally:
        sys.stderr = real_stderr


# =============================================================================================
# GROUP 75 — the sixty-first round. write_report takes `_held = os.fstat(fd)` immediately before
# the rename and never consults `_held.st_mode`; only identity is checked, and only after the
# rename. So this scanner can report a successful publication of an inode that was not 0600 at
# the instant it landed at the canonical name.
# =============================================================================================


def test_a_publication_is_refused_when_the_held_inode_is_not_owner_only(tmp_path: Path) -> None:
    """REPAIRED: the descriptor is already in hand one syscall before the rename, and its mode is the
    single number the whole policy reduces to. The installer's own verify happens earlier and through
    a different call; reading the mode off the stat that is ALREADY taken for identity costs nothing
    and is the last moment at which a wide inode can be stopped from becoming the report."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "held_mode_before_replace")
    if not module._XATTR_SUPPORTED or module._PROC_FD_DIR is None:
        pytest.skip("no xattr layer or descriptor directory here")
    staging = tmp_path / "staging"
    reports = staging / "_reports"
    reports.mkdir(parents=True)
    real_fstat = module.os.fstat
    reads: list = []

    def fstat_reports_group_readable(f_, *a, **k):
        st = real_fstat(f_, *a, **k)
        if sys._getframe(1).f_code.co_name == "write_report":
            reads.append(f_)
            if len(reads) == 2:            # the first is the sweep's reference stamp; this is `_held`
                class _Wide:
                    st_mode = (st.st_mode & ~0o777) | 0o640
                    st_size, st_dev, st_ino, st_nlink = st.st_size, st.st_dev, st.st_ino, st.st_nlink
                    st_ctime_ns = getattr(st, "st_ctime_ns", 0)
                return _Wide()
        return st

    hits = [("docs/H.md", 1, "SECRET", "generic_key_assignment", "k = '" + "AKIA" + "IOSFODNN7EXAMPLE" + "'")]
    module.os.fstat = fstat_reports_group_readable
    raised = None
    try:
        try:
            module.write_report(str(staging), hits)
        except BaseException as exc:
            raised = exc
    finally:
        module.os.fstat = real_fstat
    if len(reads) < 2:
        pytest.skip("the held-side stat before the rename was never reached (reads=%d); this arm "
                    "measured nothing" % len(reads))
    published = reports / "scan_report.txt"
    assert not published.exists() and raised is not None, (
        f"REPAIRED: the held inode read as group-readable at the instant before the rename and the run "
        f"published it anyway, then reported success (raised={raised!r}); `_held.st_mode` has no reader")


# =============================================================================================
# GROUP 76 — the sixty-first round. The emission is keyed on an EXCEPTION rather than on whether
# the bytes landed. `_stage_report` has a path that KEEPS a non-empty findings leftover and then
# raises; the region above it then also prints the whole hit list to the error stream, so the
# same findings are published twice — once to a file this scanner controls, once to a descriptor
# it did not choose and cannot narrow.
# =============================================================================================


def test_a_kept_partial_stage_does_not_also_put_the_findings_on_the_error_stream(tmp_path: Path) -> None:
    """PINNED: the rule the post-stage silence already states, applied one frame lower. `_stage_report`'s
    own handler exists to KEEP a partial findings body — the gate fault-injected an EFBIG partway
    through one — and it re-raises afterwards, so the exception says nothing about whether bytes
    landed. The answer is a fact the handler holds and the caller does not; the emission belongs
    behind it, not behind "an exception came out of the stage call"."""
    driver = make_tool(tmp_path)
    module = import_driver(driver, "kept_partial_stage_quiet")
    if not module._XATTR_SUPPORTED or module._PROC_FD_DIR is None:
        pytest.skip("no xattr layer or descriptor directory here")
    staging = tmp_path / "staging"
    reports = staging / "_reports"
    reports.mkdir(parents=True)
    real_fdopen = module.os.fdopen
    partials: list = []

    class _PartialHandle:
        def __init__(self, fd):
            self.fd = fd

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def write(self, data):
            half = data[:len(data) // 2] or data[:1]
            os.write(self.fd, half)        # bytes reach the disk, and then the write fails
            partials.append(len(half))
            raise OSError(errno.EFBIG, "injected: the body did not fit")

    def fdopen_partial(fd, *a, **k):
        if sys._getframe(1).f_code.co_name == "_stage_report":
            return _PartialHandle(fd)
        return real_fdopen(fd, *a, **k)

    hits = [("docs/A.md", 1, "SECRET", "generic_key_assignment", "k1"),
            ("docs/B.md", 2, "SECRET", "generic_key_assignment", "k2"),
            ("docs/C.md", 3, "SECRET", "generic_key_assignment", "k3"),
            ("docs/D.md", 4, "SECRET", "generic_key_assignment", "k4")]
    module.os.fdopen = fdopen_partial
    buf = io.StringIO()
    real_stderr = sys.stderr
    sys.stderr = buf
    try:
        try:
            module.write_report(str(staging), hits)
        except BaseException:
            pass
    finally:
        sys.stderr = real_stderr
        module.os.fdopen = real_fdopen
    if not partials:
        pytest.skip("the staged body was never written; this arm measured nothing")
    kept = [p for p in reports.iterdir()
            if p.name.startswith(".scan_report_") and p.stat().st_size > 0]
    if not kept:
        pytest.skip("no non-empty leftover was kept; this arm measured nothing")
    err = buf.getvalue()
    assert "hit(s)" not in err, (
        f"PINNED: a non-empty findings leftover was KEPT at {[p.name for p in kept]} and the hit list was "
        f"printed to the error stream as well — the same findings published twice, the second time to a "
        f"descriptor this tool did not choose (stderr was {err!r})")


# =============================================================================================
# GROUP 77 — the sixty-first round. `self_test` does `_load_identity_terms()[0]`. A terms file
# that is LEGAL but holds only comments loads to an empty list, so `--self-test` dies with an
# IndexError traceback instead of this module's own refusal type — the one state the loader
# already documents (`_identity_terms` carries the comment about it) and does not refuse.
# =============================================================================================


def test_a_comments_only_terms_file_refuses_rather_than_raising_indexerror(tmp_path: Path) -> None:
    """REPAIRED: the missing-file case is a named ScanRefused with a five-line diagnostic; the
    comments-only case is the same missing input arriving through a file that exists, and it is
    already known to this module — `_identity_terms` compiles `(?!)` for it precisely because an
    empty alternation once made every line a PERSONAL hit. The self-test's mutation-2 arm cannot be
    built without a term, so it is the same refusal, not a traceback."""
    tool = tmp_path / "tool_comments_only"
    tool.mkdir()
    driver = tool / "scan_gate.py"
    shutil.copy(SCANNER, driver)
    (tool / "identity_terms.txt").write_text(
        "# every line here is a comment\n#\n#   and so is this one\n", encoding="utf8")
    module = import_driver(driver, "self_test_comments_only")
    assert module._load_identity_terms() == [], "CONTROL: the fixture terms file must load empty"
    raised = None
    try:
        module.self_test()
    except BaseException as exc:
        raised = exc
    assert isinstance(raised, module.ScanRefused), (
        f"REPAIRED: a legal comments-only terms file made --self-test die with {raised!r} instead of this "
        f"module's own refusal type; an IndexError traceback exits 1, not the documented 2, and names no "
        f"missing input")


# GROUP 78 — the sixty-first round, from the guarantee inventory rather than from a review. The
# module's "never blocks" invariant was verified all over the REFUSAL path — every open there
# carries O_PATH, O_NONBLOCK, O_DIRECTORY or O_EXCL, and several rounds were spent putting them
# there. The scan itself, which is the one part of this tool that reads the UNTRUSTED tree, used a
# plain blocking `open(path, "rb")`. A FIFO planted anywhere under the staging directory therefore
# made scan() wait for a writer and never return: the most attacker-reachable surface in the tool
# was the one surface the invariant did not actually cover, and the README's blocking statement is
# scoped to the refusal path, so nothing said otherwise. The arm is a real FIFO and a real timeout,
# with a regular file in the same place as the control — a hang arm with no control cannot tell a
# fixed scanner from a slow one.

def test_the_scan_does_not_wait_for_a_writer_on_a_planted_fifo(tmp_path: Path) -> None:
    driver = make_tool(tmp_path)
    staging = make_staging(tmp_path)
    (staging / "skills").mkdir(parents=True, exist_ok=True)
    (staging / "skills" / "plain.md").write_text("nothing here\n", encoding="utf8")

    runner = tmp_path / "run_scan.py"
    runner.write_text(
        "import importlib.util, sys\n"
        "spec = importlib.util.spec_from_file_location('sg', %r)\n"
        "m = importlib.util.module_from_spec(spec); sys.modules['sg'] = m\n"
        "spec.loader.exec_module(m)\n"
        "print('HITS', len(m.scan(%r)))\n" % (str(driver), str(staging)),
        encoding="utf8")

    def run_once() -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(runner)], capture_output=True, text=True,
                              encoding="utf8", errors="replace", env=clean_env(tmp_path),
                              timeout=30)

    # CONTROL FIRST: a regular file in that place must return, or a later timeout proves nothing.
    (staging / "skills" / "subject.md").write_text("ordinary\n", encoding="utf8")
    control = run_once()
    assert control.returncode == 0 and "HITS" in control.stdout, (
        "CONTROL: the scan must complete on a tree of regular files, else the arm below cannot "
        "distinguish a blocked scan from a broken one — got rc=%r stderr=%r"
        % (control.returncode, control.stderr[-400:]))

    (staging / "skills" / "subject.md").unlink()
    os.mkfifo(staging / "skills" / "subject.md")
    try:
        armed = run_once()
    except subprocess.TimeoutExpired:
        raise AssertionError(
            "REPAIRED: a FIFO planted in the scanned tree made scan() wait for a writer and the "
            "run never returned. The scan's own open is the one this module left blocking.")
    assert armed.returncode == 0 and "HITS" in armed.stdout, (
        "the scan must complete with a non-regular entry in the tree, skipping it — got rc=%r "
        "stderr=%r" % (armed.returncode, armed.stderr[-400:]))


# GROUP 79 — the sixty-first round, from the guarantee re-map. Two halves of this suite disagreed
# about what the fifth field of a hit tuple holds, and nothing compared them. One arm asserts
# `surface in ("content", "name")` against a real scan; the emission arm hands `write_report` a
# tuple whose fifth field is a secret and asserts it is withheld. Both pass. The second one cannot
# fail for the reason its docstring gave, because production never puts material in that field —
# so a sentence claiming it did survived in the module, in STAGING_README and in the arm's own
# docstring, and the emission was made less diagnostic than the success path to protect something
# that was not there. This arm is the comparison nobody was making: the domain of field five,
# measured on the real scanner over a planted tree, so the fixture's worst case stays a worst case
# and cannot quietly become a description of what the module does.

def test_the_fifth_field_of_every_hit_names_an_arm_and_never_the_material(tmp_path: Path) -> None:
    driver = make_tool(tmp_path)
    module = import_driver(driver, "hit_tuple_domain")
    staging = make_staging(tmp_path)
    (staging / "skills").mkdir(parents=True, exist_ok=True)
    secret = "AKIA" + "IOSFODNN7EXAMPLE"
    (staging / "skills" / "content_arm.md").write_text("k = '%s'\n" % secret, encoding="utf8")
    (staging / "skills" / ("notes-%s.md" % IDENTITY_TERM)).write_text("clean\n", encoding="utf8")

    hits = module.scan(str(staging))
    assert hits, ("CONTROL: the planted tree must produce hits, or the domain below is measured "
                  "over nothing")
    surfaces = {h[4] for h in hits}
    assert surfaces <= {"content", "name"}, (
        "the fifth field of a hit is which ARM fired, and these values are outside that domain: "
        "%r. If this module now carries matched material there, every sentence about the error "
        "stream withholding it has to be re-decided, and the emission's own comment with them."
        % sorted(surfaces - {"content", "name"}))
    assert not any(secret in str(h[4]) for h in hits), (
        "the fifth field carried the matched material itself; the emission drops that field but "
        "the report body prints it, so this would publish the secret into the report")
    assert len(surfaces) == 2, (
        "CONTROL: both arms must have fired, or this measured only one branch of the domain "
        "(saw %r)" % sorted(surfaces))
# GROUP 80 — the sixty-third round. `scan()` builds `hits` as a LOCAL LIST and the caller's name
# is bound only when the function returns normally. Every refusal raised partway through the walk
# — a malformed wide encoding, an unreadable input, a git failure on the file after the one that
# already matched — unwinds out of `scan()` and takes the findings collected so far with it.
# `main()` then hands the exception to `_write_refusal_report`, which receives no hits and cannot
# reconstruct them, so an operator whose tree holds a real secret AND one broken file is told
# "REFUSED invalid-wide-encoding" and nothing else. The findings existed; the scanner had them in
# memory; nothing published them. This is the same user-visible failure the module already
# repaired one frame lower (the post-stage emission, GROUP 76) and never repaired at the scan.
# =============================================================================================


def test_findings_collected_before_a_refusal_reach_the_operator(tmp_path: Path) -> None:
    """REPAIRED: a scan that matched and then refused must not report the refusal alone.

    CONTROL: the identical tree WITHOUT the refusing file exits 1 and reports the plant at the
    exact line, so the arm below is measuring a lost finding and not an undetectable one; a tree
    holding ONLY the refusing file exits 2 with no path line on the error stream, so the assertion
    cannot be satisfied by the refusal message happening to contain a path.
    REPAIRED: with both files present the run still exits 2, and the finding already collected
    reaches the operator — labelled as a partial scan, on the error stream, with the value itself
    still withheld.
    """
    driver = make_tool(tmp_path)
    plant = openai_plant()
    # git mode: `git ls-files` is sorted, so the matching file is READ BEFORE the refusing one.
    # An os.walk ordering would leave this arm measuring directory-entry order.
    control = make_staging(tmp_path, "control", git_repo=True)
    write(control / "docs" / "a_found.md", "probe " + plant + "\n")
    commit_all(tmp_path, control)
    proc = scan(tmp_path, driver, control)
    assert proc.returncode == 1, "CONTROL: the plant alone must block"
    assert hit("SECRET", "openai-style-key", "content", "docs/a_found.md", 1) in hits_for(
        control, "docs/a_found.md"), "CONTROL: the plant is detectable at that exact line"

    bare = make_staging(tmp_path, "bare", git_repo=True)
    write(bare / "docs" / "z_raises.md", b"\xff\xfe" + b"\x41")   # declared UTF-16 LE, odd length
    commit_all(tmp_path, bare)
    refused = scan(tmp_path, driver, bare)
    assert refused.returncode == 2, "CONTROL: a malformed declared-wide file must refuse"
    assert "docs/a_found.md:1" not in refused.stderr, (
        "CONTROL: with nothing found, no finding line may appear — otherwise the arm below could "
        "be satisfied by the refusal message rather than by the findings")

    armed = make_staging(tmp_path, "armed", git_repo=True)
    write(armed / "docs" / "a_found.md", "probe " + plant + "\n")
    write(armed / "docs" / "z_raises.md", b"\xff\xfe" + b"\x41")
    commit_all(tmp_path, armed)
    proc = scan(tmp_path, driver, armed)
    assert_values_absent([plant], armed, proc)
    assert proc.returncode == 2, "CONTROL: the refusal still governs the exit status"
    assert "docs/a_found.md:1" in proc.stderr, (
        "REPAIRED: the scan matched docs/a_found.md and then refused on a later file, and the "
        "finding vanished with the exception — `hits` is a local list, the caller's name is never "
        "bound, and `_write_refusal_report` is handed an exception carrying no findings. The "
        "operator got an exit status and a refusal class for a tree that really does hold a "
        "secret. stderr was %r" % proc.stderr[-600:])


# =============================================================================================
# GROUP 81 — the sixty-third round. TWO LOADER OPENS ON A PATH THIS TOOL DOES NOT CONTROL.
# `_load_identity_terms` and `_allowlist` each ask `os.path.isfile` and then hand the SAME PATH to
# a plain builtin `open`. Between the two calls the entry can become a named pipe, and the open of
# a FIFO's read end waits for a writer — forever, against an invariant that says this module never
# blocks, and before a single finding exists. GROUP 78 closed exactly this hole on the scan's
# content open and the two loaders were left as they were; `_allowlist`'s path is inside the
# UNTRUSTED tree, which is the surface that hole was closed for.
# The window is driven directly rather than raced: the module's own `os.path.isfile` answers True
# and creates the pipe on its way out, which is the same ordering an attacker gets for free.
# =============================================================================================


@pytest.mark.parametrize("loader", ["allowlist", "identity_terms"])
def test_a_loader_open_does_not_wait_for_a_writer_on_a_named_pipe(tmp_path: Path, loader: str) -> None:
    """REPAIRED: both loaders must answer with a non-regular entry in that position.

    CONTROL FIRST: the identical driver, with an ORDINARY FILE created in the same window, must
    return quickly — a hang arm with no control cannot tell a fixed loader from a broken harness.
    """
    driver = make_tool(tmp_path, name="tool_" + loader)
    staging = make_staging(tmp_path, "staging_" + loader)
    (staging / "_tools").mkdir(parents=True, exist_ok=True)
    target = ((staging / "_tools" / "scan_allow.tsv") if loader == "allowlist"
              else (driver.parent / "identity_terms.txt"))
    call = ("m._allowlist(%r)" % str(staging)) if loader == "allowlist" else "m._load_identity_terms()"

    runner = tmp_path / ("run_%s.py" % loader)
    runner.write_text(
        "import importlib.util, os, sys\n"
        "spec = importlib.util.spec_from_file_location('sg', %r)\n"
        "m = importlib.util.module_from_spec(spec); sys.modules['sg'] = m\n"
        "spec.loader.exec_module(m)\n"
        "TARGET = %r\n"
        "MODE = sys.argv[1]\n"
        "real_isfile = m.os.path.isfile\n"
        "def isfile(path):\n"
        "    answer = real_isfile(path)\n"
        "    if os.path.abspath(path) == TARGET:\n"
        "        if os.path.lexists(TARGET):\n"
        "            os.unlink(TARGET)\n"
        "        if MODE == 'fifo':\n"
        "            os.mkfifo(TARGET)\n"          # the window, taken between the two calls
        "        else:\n"
        "            fh = os.open(TARGET, os.O_CREAT | os.O_WRONLY, 0o600)\n"
        "            os.write(fh, b'# control\\n'); os.close(fh)\n"
        "        return True\n"
        "    return answer\n"
        "m.os.path.isfile = isfile\n"
        "try:\n"
        "    value = %s\n"
        "    print('RETURNED', type(value).__name__)\n"
        "except m.ScanRefused as exc:\n"
        "    print('REFUSED', exc.reason_class)\n"
        % (str(driver), str(target.resolve() if target.exists() else target), call),
        encoding="utf8")

    def run_once(mode: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(runner), mode], capture_output=True, text=True,
                              encoding="utf8", errors="replace", env=clean_env(tmp_path),
                              timeout=30)

    control = run_once("file")
    assert control.returncode == 0 and ("RETURNED" in control.stdout or "REFUSED" in control.stdout), (
        "CONTROL: with an ordinary file created in that window the loader must answer, else a "
        "timeout below proves nothing — rc=%r stdout=%r stderr=%r"
        % (control.returncode, control.stdout, control.stderr[-400:]))

    try:
        armed = run_once("fifo")
    except subprocess.TimeoutExpired:
        raise AssertionError(
            "REPAIRED: a named pipe created between `os.path.isfile` and the builtin `open` in "
            "%s made the call wait for a writer and it never returned. The scan's own content "
            "open was given O_NONBLOCK and a type check read from the DESCRIPTOR; these two were "
            "not, and one of them reads a path inside the scanned tree." % loader)
    assert armed.returncode == 0 and ("RETURNED" in armed.stdout or "REFUSED" in armed.stdout), (
        "the loader must answer with a non-regular entry in that position, treating it as absent "
        "— rc=%r stdout=%r stderr=%r" % (armed.returncode, armed.stdout, armed.stderr[-400:]))


# =============================================================================================
# GROUP 82 WAS NOT PORTED, AND THE REASON IS A MUTANT THIS SUITE ALREADY KILLS. It was written,
# proven red, and proven satisfiable by adding O_NOFOLLOW to the scan's content open and skipping
# a symlink on ELOOP. Its `dangling` case asserts that a dangling symlink must NOT refuse the scan.
# `test_report_and_read_failure[read-failure]` asserts the opposite, and its docstring names why:
# the broken behaviour it was built to kill is "an unreadable file was silently skipped (continue)
# and the scan reported CLEAN", and it lists the mutant by name — M-READ-ERROR-CONTINUE. Skipping a
# symlink on ELOOP IS that mutant, applied to a different entry type. A scan that quietly reads
# less than it was pointed at is how a dirty tree gets called clean, and that rule outranks the
# other half of this finding.
#
# The other half is real and is NOT closed: the walk still reads symlink TARGETS, so content from
# outside the tree can be reported under an in-tree path. Fixing THAT without reintroducing the
# mutant means telling an escaping link from a dangling one, which means resolving the target and
# binding the comparison to a descriptor rather than a path — more machinery than a round should
# improvise, and the two halves of the finding pull in opposite directions. It goes to the gate as
# a design question with that framing, which is what produced a usable answer for the ownership
# rule rather than a patch someone had to take back.
# GROUP 83 — the sixty-third round. `_git` CALLS `subprocess.run` WITH `capture_output` AND
# `check` AND NO TIMEOUT. Every selected-Git path in this module goes through it — the root
# probe, the index read, every blob read — so a git that does not exit (a filesystem that will
# not answer, an index lock held by another process, a `git` on PATH that hangs) parks the scan
# forever. The module's blocking invariant is stated absolutely and every open in the refusal
# path was given a flag to honour it; the one place this tool waits on ANOTHER PROCESS was never
# given the equivalent. `check=True` already converts a failure into this module's refusal, so
# the whole repair is a bound on the wait and the same conversion for the expiry.
# =============================================================================================


def test_a_git_that_does_not_exit_is_given_up_on(tmp_path: Path) -> None:
    """REPAIRED: `_git` must bound its wait.

    CONTROL FIRST: the same runner against the REAL git answers inside the same budget, so a
    timeout below is the module waiting and not the harness failing to observe a return.
    """
    driver = make_tool(tmp_path)
    staging = make_staging(tmp_path, git_repo=True)
    write(staging / "docs" / "x.md", "nothing here\n")
    commit_all(tmp_path, staging)

    runner = tmp_path / "run_git.py"
    runner.write_text(
        "import importlib.util, sys\n"
        "spec = importlib.util.spec_from_file_location('sg', %r)\n"
        "m = importlib.util.module_from_spec(spec); sys.modules['sg'] = m\n"
        "spec.loader.exec_module(m)\n"
        "try:\n"
        "    out = m._git(%r, ['rev-parse', '--show-toplevel'], 'git-root-error')\n"
        "    print('RETURNED', len(out))\n"
        "except m.ScanRefused as exc:\n"
        "    print('REFUSED', exc.reason_class)\n"
        "except BaseException as exc:\n"
        "    print('RAISED', type(exc).__name__)\n" % (str(driver), str(staging)),
        encoding="utf8")

    # A real hung child, not a patched `subprocess.run`: the module must be given up on by the
    # mechanism it would really use. `exec` so the hang IS the process git waits on.
    hang_dir = tmp_path / "hangshim"
    hang_dir.mkdir(exist_ok=True)
    hang = hang_dir / "git"
    hang.write_text("#!/bin/sh\nexec sleep 900\n", encoding="utf8")
    hang.chmod(hang.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    BUDGET = 20   # a git call that cannot be given up on inside this is indistinguishable from never

    def run_once(path_prefix: Path | None, tag: str):
        sink = tmp_path / ("git_%s.out" % tag)
        with open(sink, "wb") as handle:
            proc = subprocess.Popen([sys.executable, str(runner)], stdout=handle,
                                    stderr=subprocess.STDOUT, env=clean_env(tmp_path, path_prefix),
                                    start_new_session=True)
            try:
                code = proc.wait(timeout=BUDGET)
            except subprocess.TimeoutExpired:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                proc.wait(timeout=20)
                return None, sink.read_text(encoding="utf8", errors="replace")
        return code, sink.read_text(encoding="utf8", errors="replace")

    code, text = run_once(None, "control")
    assert code == 0 and ("RETURNED" in text or "REFUSED" in text), (
        "CONTROL: `_git` against the real git must answer inside %ds, else the arm below cannot "
        "tell a waiting module from a broken harness — rc=%r output=%r" % (BUDGET, code, text[-400:]))

    code, text = run_once(hang_dir, "armed")
    assert code is not None, (
        "REPAIRED: a git subprocess that never exits parked `_git` for the whole %d-second budget "
        "and had to be killed. `subprocess.run` is called with capture_output and check and no "
        "timeout, so every selected-Git path in this module — the root probe, the index read, "
        "every blob read — waits on another process without a bound, against an invariant this "
        "module states absolutely." % BUDGET)
    assert "REFUSED" in text, (
        "the expiry must arrive as this module's own refusal type, not as a raw TimeoutExpired: "
        "output was %r" % text[-400:])


# =============================================================================================
# GROUP 84 — the sixty-third round. `_emit_unwritten_findings` OPENS WITH "Never raises." and then
# re-raises KeyboardInterrupt and SystemExit — deliberately, and correctly: a cancellation is not
# a failure to print, and the function's own comment says so at length three lines below the
# handler. The sentence at the top was simply never updated, and it is the sentence a caller
# reads before deciding whether to wrap the call. Both callers sit on an already-unwinding path,
# where "never raises" is exactly the property being relied on.
# This arm is not a string search. It reads which exception types the CODE propagates out of the
# AST, proves on a live call that the propagation is real and that ordinary failures really are
# swallowed, and only then asks whether the docstring's claim names them. A rewording that drops
# the claim satisfies it; a rewording that keeps the claim and still hides the carve-out does not.
# =============================================================================================


def _claim_sentences(text: str) -> list[tuple[str, str]]:
    """Each sentence making a never-raises claim, paired with the sentence that follows it."""
    import re as _re
    flat = " ".join(text.split())
    parts = [p.strip() for p in _re.split(r"(?<=[.!?])\s+", flat) if p.strip()]
    claim = _re.compile(r"(?i)\bnever\s+rais")
    return [(parts[i], parts[i + 1] if i + 1 < len(parts) else "")
            for i in range(len(parts)) if claim.search(parts[i])]


def _propagating_types(func: ast.FunctionDef) -> set[str]:
    """Exception types an ``except`` in FUNC catches and then re-raises — the types that leave."""
    out = set()
    for node in ast.walk(func):
        if not isinstance(node, ast.ExceptHandler):
            continue
        reraises = any(isinstance(sub, ast.Raise) and (sub.exc is None or isinstance(sub.exc, ast.Name))
                       for sub in ast.walk(node))
        if not reraises:
            continue
        caught = node.type
        for piece in (caught.elts if isinstance(caught, ast.Tuple) else [caught]):
            if isinstance(piece, ast.Name):
                out.add(piece.id)
            elif isinstance(piece, ast.Attribute):
                out.add(piece.attr)
    return out


def test_the_never_raises_docstring_names_what_the_code_lets_through(tmp_path: Path) -> None:
    """REPAIRED: the sentence and the code must agree about what leaves this function.

    CONTROL: the AST reading is checked against a live call — an OSError raised by the write is
    swallowed (so the swallow branch is real and the probe can tell the difference), and a
    KeyboardInterrupt raised by the same write comes back out (so the propagation the AST reports
    is not a misreading of the tree).
    """
    driver = make_tool(tmp_path)
    module = import_driver(driver, "never_raises_claim")
    tree = ast.parse(SCANNER.read_text(encoding="utf8"))
    func = next(n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == "_emit_unwritten_findings")
    propagating = _propagating_types(func)

    hits = [("docs/A.md", 1, "SECRET", "generic-key-assign", "content")]

    class _Exploding:
        def __init__(self, exc):
            self.exc = exc

        def write(self, _data):
            raise self.exc

    def call_with(exc):
        real = sys.stderr
        sys.stderr = _Exploding(exc)
        try:
            module._emit_unwritten_findings(hits)
            return None
        except BaseException as out:
            return type(out).__name__
        finally:
            sys.stderr = real

    assert call_with(OSError(errno.EPIPE, "injected")) is None, (
        "CONTROL: an ordinary failure to print must be swallowed, else this arm cannot tell a "
        "function that lets everything through from one with a carve-out")
    escaped = call_with(KeyboardInterrupt())
    assert escaped == "KeyboardInterrupt", (
        "CONTROL: the cancellation carve-out must be real on a live call, not only in the tree "
        "(got %r)" % escaped)
    assert escaped in propagating, (
        "CONTROL: the AST reading must agree with the live call — it says %r leaves this function "
        "and the call showed %r" % (sorted(propagating), escaped))

    doc = func.body[0].value.value if (func.body and isinstance(func.body[0], ast.Expr)
                                       and isinstance(func.body[0].value, ast.Constant)) else ""
    claims = _claim_sentences(doc)
    unqualified = [(sentence, nxt) for sentence, nxt in claims
                   if not propagating <= {w.strip(".,;:()") for w in (sentence + " " + nxt).split()}]
    assert not unqualified, (
        "REPAIRED: `_emit_unwritten_findings` promises %r while its own code re-raises %s. The "
        "claim is the sentence a caller reads before deciding whether to wrap the call, and both "
        "callers are already unwinding when they make it. Either drop the claim or let it name "
        "what leaves: the handler and the sentence have to say the same thing."
        % (unqualified[0][0], ", ".join(sorted(propagating))))


# =============================================================================================
# GROUP 85 — the sixty-third round. THE STRUCTURAL LINT, and the arm that runs it over the module.
#
# `guard/fd_ownership_check.py` answers ONE question over the whole of `_tools/scan_gate.py`: does
# any STATEMENT stand between a descriptor acquisition and the `try` whose `finally` releases it?
# Nothing more. An earlier design for it tried to certify that a particular shape — slot preset to
# None, acquisition inside the owning try, release behind an `is not None` guard — CLOSES the leak
# class. An adversarial review refuted that before the file was finished, and the refutation is
# correct: `os.open` returns a raw integer, and a cancellation delivered between the syscall
# returning and the bytecode that binds the name leaves the slot still None, so the finally
# releases nothing. That is the original defect, inside the shape meant to prevent it, and no
# source check can see it — the reference and PEP 343 both say an interrupt can arrive between any
# two opcodes. So the checker prints that limit with every verdict rather than burying it in a
# comment, and this group pins the printing as hard as it pins the finding: a gate that reports
# success while a class it appears to cover is still open is the failure this suite exists for.
#
# The checker is a LINT, not a proof. A rejection means "rewrite this site into a known-good
# shape", never "this is proven buggy", and every site it cannot classify is reported as an
# explicit unsupported-shape entry rather than passed. The tests below are the half that makes it
# an instrument at all: each accepted shape is paired with the mutation that must be rejected,
# because a checker that cannot be SHOWN to reject is not a checker.
# =============================================================================================

FD_CHECKER = REPO / "guard" / "fd_ownership_check.py"


def fd_checker():
    """Import the lint the way an operator would run it: by path, from guard/."""
    spec = importlib.util.spec_from_file_location("fd_ownership_check_under_test", str(FD_CHECKER))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _lint(source: str, name: str = "fixture.py"):
    """Verdicts for a synthetic source, as {line: verdict}, plus the site list."""
    import textwrap
    module = fd_checker()
    sites = module.analyse(Path(name), source=textwrap.dedent(source).lstrip("\n"))
    return module, sites


# --- the accepted language: each shape, and the mutation of it that must be rejected ----------

ACCEPTED_SHAPES = {
    "acquire-then-own": """
        import os
        def f(path):
            fd = os.open(path, os.O_RDONLY)
            try:
                return os.read(fd, 10)
            finally:
                os.close(fd)
    """,
    "acquired-inside-owner": """
        import os
        def f(path):
            fd = None
            try:
                fd = os.open(path, os.O_RDONLY)
                return os.read(fd, 10)
            finally:
                if fd is not None:
                    os.close(fd)
    """,
    "with-item": """
        def f(path):
            with open(path) as handle:
                return handle.read()
    """,
    "transfer-by-return": """
        import os
        def f(path):
            return os.open(path, os.O_RDONLY)
    """,
    "immediate-release": """
        import os
        def f(path):
            os.close(os.open(path, os.O_RDONLY))
    """,
    # two descriptors, one per level: the isolated spelling, where a release that raises cannot
    # skip the other one
    "nested-owners": """
        import os
        def f(a, b):
            first = os.open(a, os.O_RDONLY)
            try:
                second = os.open(b, os.O_RDONLY)
                try:
                    return os.read(first, 1) + os.read(second, 1)
                finally:
                    os.close(second)
            finally:
                os.close(first)
    """,
}


@pytest.mark.parametrize("shape", sorted(ACCEPTED_SHAPES))
def test_the_fd_lint_accepts_its_own_accepted_language(shape: str) -> None:
    """CONTROL for every rejection below: the shapes the lint says it accepts must actually pass,
    or a rejection elsewhere says nothing about the shape and only that the lint rejects widely."""
    module, sites = _lint(ACCEPTED_SHAPES[shape])
    assert sites, "CONTROL: the %s fixture must contain an acquisition at all" % shape
    bad = [s for s in sites if not s.ok]
    assert not bad, "CONTROL: %s must be accepted, got %r" % (shape, bad)


REJECTED_SHAPES = {
    # ONE statement between the acquisition and its owner: the shape both real instances in
    # scan_gate.py take (an `if <slot> is None: return` standing in the gap).
    "gap-one": ("""
        import os
        def f(path):
            fd = os.open(path, os.O_RDONLY)
            if fd < 0:
                return None
            try:
                return os.read(fd, 10)
            finally:
                os.close(fd)
    """, "gap"),
    # SEVERAL. The gap is reported with its width so the diagnostic names the work at risk.
    "gap-three": ("""
        import os
        def f(path, log):
            fd = os.open(path, os.O_RDONLY)
            log.append(fd)
            size = os.fstat(fd).st_size
            if size == 0:
                return None
            try:
                return os.read(fd, 10)
            finally:
                os.close(fd)
    """, "gap"),
    # A finally that releases SOMETHING ELSE reads exactly like ownership at a glance.
    "different-slot": ("""
        import os
        def f(path, other):
            fd = os.open(path, os.O_RDONLY)
            try:
                return os.read(fd, 1)
            finally:
                os.close(other)
    """, "unowned"),
    # Guarded on whether the WORK succeeded rather than on whether the descriptor exists.
    "success-guard": ("""
        import os
        def f(path):
            ok = False
            fd = None
            try:
                fd = os.open(path, os.O_RDONLY)
                ok = True
            finally:
                if ok:
                    os.close(fd)
    """, "unsupported-shape"),
    # THE FALSY-ZERO TRAP. Descriptor 0 is a legal, successfully acquired descriptor and it is
    # falsy, so `if fd:` skips precisely the case the guard exists for.
    "truthiness-guard": ("""
        import os
        def f(path):
            fd = None
            try:
                fd = os.open(path, os.O_RDONLY)
            finally:
                if fd:
                    os.close(fd)
    """, "falsy-guard"),
    # TWO ACQUISITIONS SHARING ONE FINALLY. If the first release raises, the second never runs.
    "shared-finally": ("""
        import os
        def f(a, b):
            first = None
            second = None
            try:
                first = os.open(a, os.O_RDONLY)
                second = os.open(b, os.O_RDONLY)
            finally:
                if first is not None:
                    os.close(first)
                if second is not None:
                    os.close(second)
    """, "shared-finally"),
    # A CONTEXT-MANAGER FACTORY CALLED BEFORE THE CONTEXT IS ENTERED. The descriptor exists on the
    # assignment line; the guarantee starts on the next one.
    "factory-before-with": ("""
        def f(path):
            handle = open(path)
            with handle:
                return handle.read()
    """, "unowned"),
    # AN EXIT-STACK CALLBACK WHOSE ARGUMENT IS AN OPEN CALL: evaluated before the registration.
    "exit-stack-callback": ("""
        import contextlib, os
        def f(path):
            with contextlib.ExitStack() as stack:
                stack.callback(os.close, os.open(path, os.O_RDONLY))
    """, "unsupported-shape"),
    # A DECORATOR THAT PROMISES OWNERSHIP. The body is what runs, and the body is what is read.
    "decorator-promise": ("""
        import os
        def owns_descriptors(fn):
            return fn

        @owns_descriptors
        def f(path):
            fd = os.open(path, os.O_RDONLY)
            return os.read(fd, 1)
    """, "unowned"),
    # A RELEASE IN AN except HANDLER: the success path, which is the path that runs, leaks.
    "release-in-except": ("""
        import os
        def f(path):
            fd = os.open(path, os.O_RDONLY)
            try:
                return os.read(fd, 1)
            except OSError:
                os.close(fd)
                raise
    """, "unowned"),
}


@pytest.mark.parametrize("shape", sorted(REJECTED_SHAPES))
def test_the_fd_lint_rejects_the_shapes_it_says_it_rejects(shape: str) -> None:
    """A checker that cannot be shown to reject is not a checker. Each fixture is one shape the
    lint's own documentation calls out, and the verdict it must carry."""
    source, expected = REJECTED_SHAPES[shape]
    module, sites = _lint(source)
    assert sites, "CONTROL: the %s fixture must contain an acquisition at all" % shape
    verdicts = {s.verdict for s in sites}
    assert expected in verdicts, (
        "%s must be rejected as %r; the lint said %r (%r)" % (shape, expected, sorted(verdicts), sites))
    assert not any(s.ok and s.verdict == expected for s in sites), \
        "CONTROL: a rejected verdict must not also read as accepted"


def test_zero_statements_between_is_accepted_and_the_lint_says_what_that_leaves_open() -> None:
    """THE SCOPE LIMIT, PINNED. A gap of zero statements passes — the lint asks about statements
    and there are none. It does NOT follow that the descriptor is owned: the acquiring syscall
    returns before the name is bound, and a cancellation in between leaves the slot unset with the
    descriptor already allocated. No source check can see that window, so the checker prints the
    limit with every verdict. This arm fails if that sentence is ever dropped, which is the only
    thing standing between an honest lint and a gate that reports success over an open class."""
    module, sites = _lint(ACCEPTED_SHAPES["acquire-then-own"])
    assert [s.verdict for s in sites] == ["ok"], "zero statements between is inside the language"
    assert [s.gap for s in sites] == [0], "and the reported gap is zero"
    text = module.report(sites)
    assert module.COVERAGE_DISCLOSURE in text, (
        "the clean report must carry the coverage disclosure; without it a reader takes a pass "
        "for a proof that the descriptor is owned, and the syscall-to-binding window is exactly "
        "the class that stays open")
    assert "syscall" in module.COVERAGE_DISCLOSURE and "no source check" in module.COVERAGE_DISCLOSURE, (
        "the disclosure must name what it does not cover, not merely exist")


def test_a_borrowed_descriptor_is_not_counted_as_an_acquisition() -> None:
    """CONTROL on the site set itself: `os.fdopen(fd, closefd=False)` borrows a descriptor the
    caller still owns. Counting it would double-count the site that is already guarded, and the
    lint would then be satisfiable by guarding the borrow instead of the open."""
    module, sites = _lint("""
        import os
        def f(fd):
            with os.fdopen(fd, "rb", closefd=False) as handle:
                return handle.read()
    """)
    assert sites == [], "a borrow is not an acquisition (got %r)" % (sites,)


def test_no_statement_stands_between_an_acquisition_and_its_owner() -> None:
    """THE ARM. Every descriptor acquisition in `_tools/scan_gate.py` must be in the lint's
    accepted language: nothing standing between it and the try whose finally releases it, and no
    site the lint cannot classify left reading as a pass.

    CONTROL: the lint must find sites at all, must have accepted some of them, and — measured on
    THIS file, not on a fixture — must report a gap when one accepted site is pushed apart by a
    single statement. A clean verdict from an instrument that cannot go red over this very module
    is not a measurement.
    """
    module = fd_checker()
    source = SCANNER.read_text(encoding="utf8")
    sites = module.analyse(SCANNER, source=source)
    assert len(sites) > 10, "CONTROL: the lint must find the module's acquisition sites at all"
    accepted = [s for s in sites if s.ok]
    assert accepted, "CONTROL: at least one site must be inside the accepted language"

    # POSITIVE CONTROL ON THE REAL FILE: push one accepted acquisition one statement away from its
    # owner and the lint must say so, at that line, with the width.
    subject = next((s for s in accepted if s.shape == "acquire-then-own" and s.owner_lineno), None)
    assert subject is not None, "CONTROL: no acquire-then-own site to push apart"
    lines = source.splitlines(keepends=True)
    owner_line = lines[subject.owner_lineno - 1]
    indent = owner_line[:len(owner_line) - len(owner_line.lstrip())]
    pushed = lines[:subject.owner_lineno - 1] + [indent + "_wedge = 0\n"] + lines[subject.owner_lineno - 1:]
    moved = module.analyse(SCANNER, source="".join(pushed))
    wedged = [s for s in moved if s.lineno == subject.lineno]
    assert wedged and wedged[0].verdict == "gap" and wedged[0].gap == 1, (
        "CONTROL: one statement wedged above the owner at line %d must be reported as a gap of 1, "
        "else the clean verdict below comes from an instrument that cannot fail here (got %r)"
        % (subject.owner_lineno, wedged))

    # THE CLAIM THIS LINT CAN ACTUALLY MAKE: no GAP. A statement standing between an acquisition
    # and its owner is the defect four rounds repaired one site at a time, and it must be zero.
    gaps = [s for s in sites if s.verdict == "gap"]
    assert not gaps, (
        "a descriptor is acquired away from the block that owns it:\n%s" % module.report(sites))

    # AND THE SHAPES IT DECLINES TO READ ARE PINNED, NOT PASSED OVER. These are not defects; they
    # are shapes this lint conservatively refuses to call ownership — a `with` on a helper's return,
    # a helper that hands a descriptor to its caller on success and closes it on failure, a
    # directory open, and the self-test's bare opens. Left as a bare count they would drift, and a
    # count that only ever grows is how a baseline becomes a place to hide. Pinned as an exact set
    # instead: a NEW unclassifiable site fails this arm, and removing one is a deliberate edit here.
    unowned = sorted((s.function, s.callee) for s in sites if not s.ok)
    assert unowned == sorted([
        ("_allowlist", "_open_untrusted_text"),
        ("_load_identity_terms", "_open_untrusted_text"),
        ("_makedirs_owner_only", "_open_dir_nofollow"),
        ("_open_untrusted_text", "os.open"),
        ("self_test", "open"), ("self_test", "open"), ("self_test", "open"),
        ("self_test", "open"), ("self_test", "open"), ("self_test", "open"),
    ]), (
        "the set of acquisition shapes this lint cannot classify has CHANGED. If a new site "
        "appeared, put it in the accepted language rather than in this list. If one was repaired, "
        "take it out of this list in the same commit.\n%s" % module.report(sites))
