"""GROUP 69 — the executed adversarial review of 3adf105 (round sixty, gate fifty-five).

Four arms, all RED on 3adf105. Each one was proved bidirectional before it was reported: the
module was changed so the arm's target behaviour holds, the arm went GREEN, and the change was
reverted. The candidate change that turns each arm GREEN is named in the arm's docstring.

THE CANCELLATION INSTRUMENT AND ITS CONTROL. Two of these arms deliver a BaseException at a bare
statement — a window with no function call at its boundary, which the suite's existing arms
cannot reach by monkeypatching a helper. They use ``sys.settrace`` and raise from the line event.
That instrument is UNSOUND at one statement kind and it was controlled before use: an exception
raised from a trace function at a line whose statement is ``try:`` skips EVERY enclosing finally,
which is a property of the CPython tracer and not of the traced code. Injecting there manufactures
descriptor leaks that a second mechanism — a monkeypatched ``os.open`` raising at the same point —
does not reproduce. ``_inject_at`` therefore never fires on a ``try:`` line. Every other statement
kind was controlled and runs its enclosing cleanup normally: a plain statement, a statement inside
a ``finally``, a ``with`` header, a statement inside a ``with``, a ``for`` header, a statement
inside a ``for``.
"""
import ast
import importlib.util
import io
import linecache
import os
import shutil
import stat
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCANNER = REPO / "_tools" / "scan_gate.py"
IDENTITY_TERM = "synthetic" + "fixture" + "person"
NEEDLE = "docs/H.md:1"
HIT = ("docs/H.md", 1, "SECRET", "generic_key_assignment", "AK" + "IA" + "-synthetic-surface")


def _tool(tmp_path: Path, tag: str):
    """The scanner copied into its own directory with a synthetic terms file beside it, imported."""
    tool = tmp_path / "tool"
    tool.mkdir(exist_ok=True)
    driver = tool / "scan_gate.py"
    shutil.copy(SCANNER, driver)
    (tool / "identity_terms.txt").write_text(IDENTITY_TERM + "\n", encoding="utf8")
    spec = importlib.util.spec_from_file_location("scan_gate_r61_" + tag, str(driver))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, driver


def _reports_dir(tmp_path: Path) -> Path:
    reports = tmp_path / "staging" / "_reports"
    reports.mkdir(parents=True)
    return reports


def _findings_anywhere(reports: Path, needle: str = NEEDLE) -> bool:
    for path in reports.rglob("*"):
        if path.is_file():
            try:
                if needle in path.read_text(encoding="utf-8"):
                    return True
            except (OSError, UnicodeDecodeError):
                continue
    return False


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


def _close_all(fds) -> None:
    for entry in fds:
        try:
            os.close(int(entry.split(" ->")[0]))
        except (OSError, ValueError):
            pass


def _inject_at(driver: Path, func_name: str, lines, before=None):
    """A tracer that raises KeyboardInterrupt the first time FUNC_NAME reaches one of LINES.

    ``try:`` lines are never fired on — see the module docstring's instrument control. BEFORE, if
    given, runs immediately before the raise, which is how a same-uid writer is made to take a
    name inside the window rather than around it.
    """
    drv_abs = str(driver.resolve())
    fired: list = []

    def local_trace(frame, event, arg):
        if event != "line":
            return local_trace
        if linecache.getline(drv_abs, frame.f_lineno).strip().startswith("try:"):
            return local_trace          # NOT an injection point: the tracer skips enclosing finallys there
        if frame.f_lineno in lines and frame.f_code.co_name == func_name and not fired:
            fired.append(frame.f_lineno)
            if before is not None:
                before()
            raise KeyboardInterrupt()
        return local_trace

    def global_trace(frame, event, arg):
        return local_trace if frame.f_code.co_filename == drv_abs else None

    return global_trace, fired


def _statements_between_the_stage_and_its_owner(driver: Path):
    """The statements ``write_report`` executes after ``_stage_report`` returns and before the
    ``try:`` whose ``finally`` closes the descriptor it returned. Computed from the AST, so the
    arm asks about the SHAPE rather than about a line number that a later round will move."""
    tree = ast.parse(driver.read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "write_report")
    for node in ast.walk(fn):
        body = getattr(node, "body", None)
        if not isinstance(body, list):
            continue
        for i, stmt in enumerate(body):
            if not isinstance(stmt, ast.Try) or not stmt.body:
                continue
            first = stmt.body[0]
            if not (isinstance(first, ast.Assign) and isinstance(first.value, ast.Call)
                    and getattr(first.value.func, "id", None) == "_stage_report"):
                continue
            window = []
            for later in body[i + 1:]:
                if isinstance(later, ast.Try):
                    return stmt, window     # the owning block reached; the window is closed
                window.append(later)
            return stmt, window
    return None, None


# =============================================================================================
# GROUP 69
# =============================================================================================


def test_a_failure_while_the_findings_body_is_built_still_reaches_the_operator(tmp_path: Path) -> None:
    """RED on 3adf105. The round wired the emission to the region that obtains the report directory
    and to the stage call, and its own commit calls that "every channel above the first durable
    byte". The BODY CONSTRUCTION sits between those two guards and is covered by neither: the join
    over the hits is above the first durable byte, and a failure there takes the findings with it —
    no file, no operator, nothing but an exit status.

    The instrument does not suppress the channel it measures. ``hits`` raises on its FIRST
    iteration, which is what the join does; the emission reads ``hits[:40]``, a slice and not an
    iteration, so an armed emission still prints. GREEN once the body construction is inside a
    guard that calls ``_emit_unwritten_findings``."""
    module, _driver = _tool(tmp_path, "body_build")
    reports = _reports_dir(tmp_path)

    class BoomOnFirstIteration(list):
        armed = True

        def __iter__(self):
            if self.armed:
                type(self).armed = False
                raise KeyboardInterrupt()   # a cancellation while the body is being joined
            return super().__iter__()

    hits = BoomOnFirstIteration([HIT])
    buf = io.StringIO()
    real_stderr = sys.stderr
    sys.stderr = buf
    try:
        with pytest.raises(BaseException):
            module.write_report(str(tmp_path / "staging"), hits)
    finally:
        sys.stderr = real_stderr
    err = buf.getvalue()
    if BoomOnFirstIteration.armed:
        pytest.skip("the body was never joined; this arm measured nothing")
    assert NEEDLE in err or _findings_anywhere(reports), (
        f"REPAIRED: the findings body could not be built, nothing durable was written, and the hits "
        f"went nowhere — not to a file, not to the operator (stderr was {err!r}, "
        f"_reports held {sorted(p.name for p in reports.iterdir())})")


def test_no_bare_statement_stands_between_the_stage_and_the_block_that_owns_it(tmp_path: Path) -> None:
    """RED on 3adf105. ``fd, tmp_name = _stage_report(...)`` returns a descriptor on a findings
    inode, and two assignments run before the ``try:`` whose ``finally`` closes it and whose
    handler quarantines it. A cancellation at either one leaves the descriptor with no owner: no
    close, no quarantine, no emission. With the staged name taken in the same window — the same-uid
    writer this module is built against — that descriptor is the last reference and the findings
    are gone.

    The window is found from the AST, not from a line number. GREEN once the two assignments are
    moved above the stage call, which leaves no statement between the acquisition and its owner."""
    module, driver = _tool(tmp_path, "stage_window")
    reports = _reports_dir(tmp_path)
    stage_try, window = _statements_between_the_stage_and_its_owner(driver)
    if stage_try is None:
        pytest.fail("the stage call could not be located in write_report; this arm measured nothing")
    if not window:
        return                            # nothing stands between the acquisition and its owner
    lines = {stmt.lineno for stmt in window}

    def take_the_staged_name():
        for path in reports.iterdir():
            if path.name.startswith(".scan_report_"):
                os.unlink(path)

    tracer, fired = _inject_at(driver, "write_report", lines, before=take_the_staged_name)
    buf = io.StringIO()
    real_stderr = sys.stderr
    sys.stderr = buf
    before_fds = _open_report_fds(reports, ".scan_report_")
    sys.settrace(tracer)
    try:
        try:
            module.write_report(str(tmp_path / "staging"), [HIT])
        except BaseException:
            pass
    finally:
        sys.settrace(None)
        sys.stderr = real_stderr
    leaked = [f for f in _open_report_fds(reports, ".scan_report_") if f not in before_fds]
    _close_all(leaked)
    if not fired:
        pytest.skip("the window was never reached; this arm measured nothing")
    assert _findings_anywhere(reports) or NEEDLE in buf.getvalue(), (
        f"REPAIRED: a cancellation at line(s) {fired} — between the stage and the block that owns "
        f"it — left the staged descriptor with no owner while its name was taken; the findings "
        f"reached no file and no operator (leaked {leaked}, _reports held "
        f"{sorted(p.name for p in reports.iterdir())})")


def test_preservation_owns_the_held_canonical_from_the_open(tmp_path: Path) -> None:
    """RED on 3adf105. ``_cfd, _cvia = _open_held_copy(...)`` opens the canonical findings report,
    and ``if _cfd is None:`` runs before the ``try:`` whose ``finally`` asks ``_cfd_still_ours``
    and closes it. A cancellation at that guard leaks the descriptor — the same defect this round
    closed one statement lower and the round before closed at the narrowing, at the one statement
    above both of them. The module states the cost itself at that finally: the hold leaks to
    process exit and a name taken afterwards takes the findings with it.

    GREEN once the open is moved inside the block whose finally already asks and closes."""
    module, driver = _tool(tmp_path, "preserve_open_window")
    if not module._XATTR_SUPPORTED or module._PROC_FD_DIR is None:
        pytest.skip("no xattr layer or descriptor directory here")
    reports = _reports_dir(tmp_path)
    (reports / "scan_report.txt").write_text(
        "SECRET\tgeneric_key_assignment\tv\t%s\n" % NEEDLE, encoding="utf-8")
    os.chmod(reports / "scan_report.txt", 0o600)
    guard = [i + 1 for i, line in enumerate(driver.read_text(encoding="utf-8").splitlines())
             if line.strip() == "if _cfd is None:"]
    if not guard:
        pytest.skip("the held-canonical guard is not in this shape; this arm measured nothing")
    tracer, fired = _inject_at(driver, "_preserve_superseded", set(guard))
    before_fds = _open_report_fds(reports)
    dirfd = os.open(reports, os.O_RDONLY | os.O_DIRECTORY)
    sys.settrace(tracer)
    try:
        try:
            module._preserve_superseded(dirfd, "scan_report.txt")
        except BaseException:
            pass
    finally:
        sys.settrace(None)
        os.close(dirfd)
    leaked = [f for f in _open_report_fds(reports) if f not in before_fds]
    _close_all(leaked)
    if not fired:
        pytest.skip("the held-canonical guard was never reached; this arm measured nothing")
    assert not leaked, (
        f"REPAIRED: a cancellation at line(s) {fired} left the held canonical descriptor open with "
        f"nothing to close it ({leaked}); it is the last reference once the name goes")


def test_a_stage_mode_narrower_than_owner_only_is_not_refused(tmp_path: Path) -> None:
    """RED on 3adf105. The new stage gate reads the mode and refuses, and its own comment states
    the rule it enforces: "Only a mode read and found wider refuses." The code compares for
    INEQUALITY, so 0400 and 0200 — narrower than owner-only, the umask case ``_narrow_leftover``
    documents when its fchmod does not stick — refuse as hard as 0640 does. A narrower stage
    leaks nothing; refusing it costs the run a report that would have been published owner-only.

    Measured end to end with no fstat mock, denying fchmod only inside ``_narrow_leftover`` under
    ``umask(0o477)``: 3adf105 raises ScanRefused and writes no report, while its parent 8c2ca89
    publishes ``_reports/scan_report.txt`` at 0600 carrying the findings.

    GREEN once the test is for bits OUTSIDE owner-only:
    ``_mode_is_wide = bool(stat.S_IMODE(os.fstat(fd).st_mode) & ~_REPORT_MODE)``."""
    module, _driver = _tool(tmp_path, "stage_narrow_mode")
    reports = _reports_dir(tmp_path)
    real_narrow, real_fstat = module._narrow_leftover, module.os.fstat
    narrowed: list = []

    def narrow_that_does_not_stick(fd):
        real_narrow(fd)
        narrowed.append(fd)

    def fstat_reports_narrow(f_, *a, **k):
        st = real_fstat(f_, *a, **k)
        if isinstance(f_, int) and f_ in narrowed and sys._getframe(1).f_code.co_name == "_stage_report":
            class _Narrow:
                st_mode = (st.st_mode & ~0o777) | 0o400      # NARROWER than owner-only
                st_size, st_dev, st_ino, st_nlink = st.st_size, st.st_dev, st.st_ino, st.st_nlink
                st_ctime_ns = getattr(st, "st_ctime_ns", 0)
            return _Narrow()
        return st

    module._narrow_leftover, module.os.fstat = narrow_that_does_not_stick, fstat_reports_narrow
    dirfd = os.open(reports, os.O_RDONLY | os.O_DIRECTORY)
    refusal = None
    try:
        try:
            fd, _name = module._stage_report(
                dirfd, "SECRET\tgeneric_key_assignment\tv\t%s\n" % NEEDLE, evidence=True)
            os.close(fd)
        except module.ScanRefused as exc:
            refusal = str(exc)
        except BaseException as exc:      # noqa: BLE001 - reported, not swallowed
            refusal = "unexpected %r" % (exc,)
    finally:
        module._narrow_leftover, module.os.fstat = real_narrow, real_fstat
        os.close(dirfd)
    if not narrowed:
        pytest.skip("the stage narrowing was never reached; this arm measured nothing")
    assert refusal is None, (
        f"REPAIRED: a stage whose mode read 0o400 — NARROWER than owner-only, which leaks nothing — "
        f"was refused ({refusal!r}); the gate's own comment says only a mode found WIDER refuses")
    assert _findings_anywhere(reports), (
        "REPAIRED: the stage was not refused but the findings did not reach it either")
