"""Hermetic teeth tests for guard/passback_send_check.py.

Each arm runs the checker as a SUBPROCESS (it reads PASSBACK_OUTBOX and
PASSBACK_RECIPIENT_SHELL at import time) with a fake-recipient script that
either reports a hash or exits non-zero.  The fourth arm exercises the
shipped teeth script against a real outbox and opts out of the conftest's
environment isolation.
"""
import hashlib
import os
import stat
import subprocess
import sys

import pytest

# ABSOLUTE, resolved from this file. guard/tests/conftest.py moves the working directory to a
# disposable one for every test, so a repo-relative path here resolves under that temp dir and the
# subprocess dies with "can't open file" — an rc 2 that looks exactly like the checker reporting
# UNMEASURED. Measured on the first run of these arms.
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CHECKER = os.path.join(REPO, "guard", "passback_send_check.py")
SHIPPED_TEETH = os.path.join(REPO, "guard", "tests", "teeth_passback_send_check.py")


# ── helpers ───────────────────────────────────────────────────────────────────


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _make_fake_recipient(tmp_path, mode: str, filename: str, reported_hash: str) -> str:
    """Create an executable fake-recipient script.

    The checker invokes:  subprocess.run([PCSH, "<one opaque arg>"], ...)
    so the script receives one argument it must ignore.

    mode='report' → print  <filename>\\t<hash>  on stdout, exit 0
    mode='fail'   → exit 1 (non-zero ⇒ checker reports UNMEASURED)
    """
    script = tmp_path / "fake_recipient.sh"
    if mode == "report":
        body = f'#!/bin/sh\nprintf "{filename}\\t{reported_hash}\\n"\nexit 0\n'
    else:
        body = "#!/bin/sh\nexit 1\n"
    script.write_text(body)
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return str(script)


def _build_outbox(tmp_path, filename: str = "reply.md", content: bytes = b"hello peer\n"):
    """Create a minimal outbox directory with one file. Returns (outbox_dir, file_path)."""
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    f = outbox / filename
    f.write_bytes(content)
    return outbox, f


def _run_checker(outbox_dir, fake_recipient: str):
    """Run the checker subprocess. Returns (returncode, stdout, stderr)."""
    env = os.environ.copy()
    env["PASSBACK_OUTBOX"] = str(outbox_dir)
    env["PASSBACK_RECIPIENT_SHELL"] = fake_recipient
    r = subprocess.run(
        [sys.executable, CHECKER, "--quiet"],
        capture_output=True, text=True, env=env, timeout=30,
    )
    return r.returncode, r.stdout, r.stderr


# ── arms ──────────────────────────────────────────────────────────────────────


def test_a_diverged_file_is_a_violation(tmp_path):
    """M1: recipient holds different bytes → exit 1 (VIOLATION)."""
    outbox, f = _build_outbox(tmp_path)
    fake_hash = "0" * 64  # guaranteed different from the real hash
    fake = _make_fake_recipient(tmp_path, "report", "reply.md", fake_hash)
    rc, out, err = _run_checker(outbox, fake)
    assert rc == 1, (
        f"expected exit 1 (DIVERGED) but got {rc}\n"
        f"stdout: {out}\nstderr: {err}"
    )


def test_an_unreachable_recipient_is_unmeasured_not_clean(tmp_path):
    """M2: recipient unreachable (non-zero exit) → exit 2 (UNMEASURED), never 0."""
    outbox, _f = _build_outbox(tmp_path)
    fake = _make_fake_recipient(tmp_path, "fail", "reply.md", "")
    rc, out, err = _run_checker(outbox, fake)
    assert rc == 2, (
        f"expected exit 2 (UNMEASURED) but got {rc}\n"
        f"stdout: {out}\nstderr: {err}"
    )


def test_matching_hashes_are_clean(tmp_path):
    """Control: recipient reports the file's REAL sha256 → exit 0 (CLEAN)."""
    outbox, f = _build_outbox(tmp_path)
    real_hash = _sha256(str(f))
    fake = _make_fake_recipient(tmp_path, "report", "reply.md", real_hash)
    rc, out, err = _run_checker(outbox, fake)
    assert rc == 0, (
        f"expected exit 0 (CLEAN) but got {rc}\n"
        f"stdout: {out}\nstderr: {err}"
    )


@pytest.mark.no_env_isolation
def test_the_shipped_teeth_script_against_a_real_outbox():
    """Run the shipped teeth script against a REAL outbox (read-only).

    Skips (visibly) when PASSBACK_OUTBOX is not configured on this box.
    """
    outbox = os.environ.get("PASSBACK_OUTBOX")
    if not outbox:
        pytest.skip("PASSBACK_OUTBOX is not configured on this box")
    # The same precondition guard/run_guards.sh applies before it runs this script: the teeth
    # target has to be a reply this box has ALREADY sent. Measured why it matters — the runner's
    # own gate test points PASSBACK_OUTBOX at an empty temp directory, and against a synthetic
    # outbox the shipped script correctly reports NO TEETH, which is a fact about the fixture and
    # not a failure of this arm.
    target = os.path.join(outbox, "replies",
                          os.environ.get("PASSBACK_TEETH_TARGET", "REPLY_example.md"))
    if not os.path.isfile(target):
        pytest.skip("PASSBACK_OUTBOX is set but carries no teeth target under replies/ "
                    "(set PASSBACK_TEETH_TARGET to a reply this box has already sent)")
    r = subprocess.run(
        [sys.executable, SHIPPED_TEETH],
        capture_output=True, text=True, timeout=120,
    )
    assert r.returncode == 0, (
        f"shipped teeth script failed (exit {r.returncode})\n"
        f"stdout: {r.stdout}\nstderr: {r.stderr}"
    )