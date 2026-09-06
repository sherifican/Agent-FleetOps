"""Caller for guard/envelope_tap.py, which is a REPORTER and not a guard.

It produces a record, never a verdict, so there is no red/green case to name. What CAN go wrong is
the record's shape: a field silently dropped or renamed makes every later analysis read a null as
an absence, and nothing would have said so. This gate drives the real tap over a real HTTP request
to a fake upstream and asserts the record it wrote — the fields it must carry, their types, and
that the request BODY is stored whole rather than truncated at capture time.
"""
import http.server
import importlib.util
import json
import pathlib
import threading
import urllib.request

MODULE_PATH = pathlib.Path(__file__).resolve().parent.parent / "envelope_tap.py"
_spec = importlib.util.spec_from_file_location("envelope_tap", MODULE_PATH)
envelope_tap = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(envelope_tap)

REQUIRED = {"ts", "arm", "model", "method", "path", "n_messages",
            "req_bytes", "status", "resp_bytes", "ms", "error", "body"}


class _Upstream(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(n)
        payload = b'{"ok": true}'
        self.send_response(200)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class _Cfg:
    def __init__(self, upstream, log, arm):
        self.upstream, self.log, self.arm, self.timeout = upstream, log, arm, 10


def _serve(handler, cfg=None):
    # The tap's Server takes its config as a constructor argument on purpose: a module-level global
    # was silently shared by two taps running at once, which is the intended topology.
    srv = (envelope_tap.Server(("127.0.0.1", 0), handler, cfg) if cfg
           else http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


def test_the_record_carries_every_field_and_the_whole_body(tmp_path):
    up_srv, up_port = _serve(_Upstream)
    log = tmp_path / "runs.jsonl"
    tap_srv, tap_port = _serve(
        envelope_tap.Tap, _Cfg(f"http://127.0.0.1:{up_port}", str(log), "test-arm"))
    try:
        body = json.dumps({"model": "some-model",
                           "messages": [{"role": "user", "content": "x" * 500}]}).encode()
        req = urllib.request.Request(f"http://127.0.0.1:{tap_port}/v1/chat/completions",
                                     data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            assert r.status == 200
    finally:
        tap_srv.shutdown()
        up_srv.shutdown()

    lines = [l for l in log.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(lines) == 1, lines
    rec = json.loads(lines[0])

    assert set(rec) == REQUIRED, f"record fields are {sorted(rec)}, want {sorted(REQUIRED)}"
    assert rec["arm"] == "test-arm"
    assert rec["model"] == "some-model"
    assert rec["method"] == "POST"
    assert rec["path"] == "/v1/chat/completions"
    assert rec["n_messages"] == 1
    assert rec["status"] == 200
    assert isinstance(rec["ms"], int) and rec["ms"] >= 0
    assert rec["req_bytes"] == len(body)
    # Stored WHOLE: truncation at capture time is unrecoverable, and a 500-character message is
    # exactly the kind of body a display-side truncation would have cut.
    assert rec["body"] == body.decode()


def test_it_reports_rather_than_deciding():
    """A reporter has no verdict to return. The record is the whole output."""
    assert not hasattr(envelope_tap, "check"), (
        "a function named check() would make this look like a verdict-producing guard")
    assert hasattr(envelope_tap, "selftest"), "the forwarding proof is still its own selftest"
