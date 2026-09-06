"""Gate for the receipt templates/dispatch-wrapper.sh.template writes.

The wrapper's contract used to end at the artifact: a reader who wanted to know how a dispatch
ended had to re-run it or read a log by eye. The receipt states the outcome once, in one place,
in a shape a machine can check — and a receipt is only worth that if it is written on EVERY exit
path, so the arms below drive one path each.

SEVEN FIELDS, asserted by name on every arm, because a receipt missing a field reads as a receipt:
artifact_path · bytes · sha256 · exit_status · lane · timestamp · outcome.

SIGKILL is uncovered by construction and is not tested here: an untrappable signal leaves no
receipt at all, which the template says out loud rather than leaving a reader to infer.
"""
import hashlib
import json
import os
import signal
import stat
import subprocess
import time

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TEMPLATE = os.path.join(REPO, "templates", "dispatch-wrapper.sh.template")
FIELDS = {"artifact_path", "bytes", "sha256", "exit_status", "lane", "timestamp", "outcome"}


def _wrapper(tmp_path, worker_body, replace_placeholders=True):
    """Materialise the template with a fake worker substituted for <MODEL_CLI>."""
    worker = tmp_path / "fake_worker.sh"
    worker.write_text(worker_body)
    worker.chmod(worker.stat().st_mode | stat.S_IEXEC)

    with open(TEMPLATE, encoding="utf-8") as fh:
        text = fh.read()
    if replace_placeholders:
        # Only the ASSIGNMENT lines. The template also names the placeholders in its
        # not-runnable check, and a blanket replace rewrites that check into a comparison of the
        # substituted value with itself — every arm then took the usage path with an outcome of
        # `usage`, which is what happened on the first run of this file.
        text = (text.replace("MODEL='<MODEL_ID>'", "MODEL='fake-model'")
                    .replace("EFFORT='<EFFORT_TIER>'", "EFFORT='high'")
                    .replace("MODEL_CLI='<MODEL_CLI>'", "MODEL_CLI='%s'" % worker))
    script = tmp_path / "wrapper.sh"
    script.write_text(text)
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


def _brief(tmp_path):
    b = tmp_path / "brief.md"
    b.write_text("a brief\n")
    return b


def _receipt(out_path):
    with open(str(out_path) + ".receipt.json", encoding="utf-8") as fh:
        return json.load(fh)


def _assert_shape(rec):
    assert set(rec) == FIELDS, f"receipt fields are {sorted(rec)}, want {sorted(FIELDS)}"


# The fake worker writes to stdout, which the wrapper redirects into the artifact.
WRITES_AND_EXITS_0 = '#!/bin/sh\nprintf "artifact bytes\\n"\nexit 0\n'
WRITES_AND_EXITS_3 = '#!/bin/sh\nprintf "partial bytes\\n"\nexit 3\n'
WRITES_NOTHING_EXITS_1 = "#!/bin/sh\nexit 1\n"
SLEEPS = "#!/bin/sh\nsleep 30\n"
# A SHORT sleeper for the signal arm. MEASURED here: the timeout tool puts itself and its child in
# a NEW process group, so a signal sent to the wrapper's group never reaches the worker, and bash
# defers a pending trap until its foreground child returns. A 30-second worker therefore makes the
# arm measure the timeout rather than the signal. Three seconds bounds the deferral without
# changing what is under test: the trap still runs, and it runs because of the signal.
SLEEPS_BRIEFLY = "#!/bin/sh\nsleep 3\n"


def _run(script, brief, out, env=None, timeout=60):
    e = dict(os.environ)
    e.update(env or {})
    return subprocess.run(["bash", str(script), str(brief), str(out)],
                          capture_output=True, text=True, env=e, timeout=timeout)


def test_arm1_ok(tmp_path):
    out = tmp_path / "art.md"
    r = _run(_wrapper(tmp_path, WRITES_AND_EXITS_0), _brief(tmp_path), out)
    rec = _receipt(out)
    _assert_shape(rec)
    assert rec["outcome"] == "ok", (rec, r.stdout, r.stderr)
    assert rec["exit_status"] == 0
    assert rec["sha256"] == hashlib.sha256(out.read_bytes()).hexdigest()
    assert rec["bytes"] == len(out.read_bytes())


def test_arm2_partial(tmp_path):
    out = tmp_path / "art.md"
    _run(_wrapper(tmp_path, WRITES_AND_EXITS_3), _brief(tmp_path), out)
    rec = _receipt(out)
    _assert_shape(rec)
    assert rec["outcome"] == "partial", rec
    assert rec["exit_status"] == 3
    assert rec["sha256"] == hashlib.sha256(out.read_bytes()).hexdigest()


def test_arm3_empty(tmp_path):
    out = tmp_path / "art.md"
    _run(_wrapper(tmp_path, WRITES_NOTHING_EXITS_1), _brief(tmp_path), out)
    rec = _receipt(out)
    _assert_shape(rec)
    assert rec["outcome"] == "empty", rec
    assert rec["bytes"] == 0
    assert rec["sha256"] is None, "an absent artifact has no hash; null says so, 0 bytes of nothing does not"


def test_arm4_usage_unreplaced_placeholder(tmp_path):
    """A usage failure WITH a valid output path — the case a receipt can cover.

    The template says the other usage failure (no arguments at all) cannot write one, because
    there is no path to derive it from; that limit is documented rather than tested around.
    """
    out = tmp_path / "art.md"
    r = _run(_wrapper(tmp_path, WRITES_AND_EXITS_0, replace_placeholders=False),
             _brief(tmp_path), out)
    assert r.returncode == 2
    rec = _receipt(out)
    _assert_shape(rec)
    assert rec["outcome"] == "usage", rec


def test_arm5_timeout(tmp_path):
    out = tmp_path / "art.md"
    _run(_wrapper(tmp_path, SLEEPS), _brief(tmp_path), out,
         env={"DISPATCH_TIMEOUT_SECONDS": "1"}, timeout=60)
    rec = _receipt(out)
    _assert_shape(rec)
    assert rec["outcome"] == "timeout", rec


def test_arm6_hostile_path_is_still_valid_json(tmp_path):
    out = tmp_path / 'a"quote\\and-backslash.md'
    _run(_wrapper(tmp_path, WRITES_AND_EXITS_0), _brief(tmp_path), out)
    rec = _receipt(out)          # json.load is the assertion: a broken encoder cannot parse
    _assert_shape(rec)
    assert rec["outcome"] == "ok", rec
    assert rec["sha256"] == hashlib.sha256(out.read_bytes()).hexdigest()
    assert 'quote' in rec["artifact_path"]


def test_arm7_sigterm_leaves_a_receipt(tmp_path):
    out = tmp_path / "art.md"
    script = _wrapper(tmp_path, SLEEPS_BRIEFLY)
    # The signal goes to the WRAPPER. Sending it to the process group does not reach the worker —
    # measured: the timeout tool makes its own group — and bash defers a pending trap until the
    # foreground child returns, which is why the fake worker here is a short one.
    proc = subprocess.Popen(["bash", str(script), str(_brief(tmp_path)), str(out)],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    receipt_path = str(out) + ".receipt.json"
    deadline = time.time() + 20
    # Wait for the wrapper to be far enough in to have installed its traps.
    while time.time() < deadline and not os.path.exists(str(out)):
        time.sleep(0.05)
    proc.send_signal(signal.SIGTERM)
    proc.wait(timeout=30)
    assert proc.returncode == 143, "the TERM handler must preserve the status as 128 + signal"
    assert os.path.exists(receipt_path), "SIGTERM must still leave a receipt"
    rec = _receipt(out)
    _assert_shape(rec)
    assert rec["exit_status"] == 143, rec
    assert rec["outcome"] in ("partial", "empty"), rec
