"""Gate for guard/comms_filing.py — an ANSWER must never sit in the ASK queue.

Three arms, because two of them alone prove nothing: a misfiled answer must be a VIOLATION, the
same file filed correctly must be CLEAN, and an absent root must be UNMEASURED rather than a
quiet pass. The third is the one that matters most here — this guard ships no default root, and a
check pointed at a guessed path reads "nothing misfiled", which looks exactly like success.
"""
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CHECK = os.path.join(REPO, "guard", "comms_filing.py")


def _tree(tmp_path, where):
    """Build <root>/outbound/{requests,replies}/ with one REPLY_ file in `where`."""
    root = tmp_path / "comms"
    # BOTH directions have to exist: the check reports an unreadable direction as UNMEASURED, and a
    # fixture missing one would make every arm return 2 — a state that looks like "the arms fired".
    for direction in ("outbound", "inbound"):
        for folder in ("requests", "replies"):
            (root / direction / folder).mkdir(parents=True, exist_ok=True)
    (root / "outbound" / where / "REPLY_to_a_question.md").write_text("an answer\n")
    return root


def _run(args, env=None):
    e = {k: v for k, v in os.environ.items() if k != "COMMS_ROOT"}
    e.update(env or {})
    return subprocess.run([sys.executable, CHECK] + args, capture_output=True, text=True, env=e)


def test_a_misfiled_answer_is_a_violation(tmp_path):
    r = _run(["--root", str(_tree(tmp_path, "requests"))])
    assert r.returncode == 1, f"expected 1 (misfiled) got {r.returncode}\n{r.stdout}{r.stderr}"
    assert "MISFILED" in r.stdout, r.stdout


def test_a_correctly_filed_answer_is_clean(tmp_path):
    r = _run(["--root", str(_tree(tmp_path, "replies"))])
    assert r.returncode == 0, f"expected 0 (clean) got {r.returncode}\n{r.stdout}{r.stderr}"


def test_an_absent_root_is_unmeasured_not_clean():
    r = _run([])
    assert r.returncode == 2, f"expected 2 (CANNOT_CHECK) got {r.returncode}\n{r.stdout}{r.stderr}"
    assert "CANNOT_CHECK" in r.stdout, r.stdout
