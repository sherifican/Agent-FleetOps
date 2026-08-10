#!/usr/bin/env python3
"""envelope_tap.py — a logging reverse-proxy that captures the EXACT request a harness sends.

WHY THIS EXISTS. The harness review concluded that arm B is attributable only on lanes we instrument,
which made a third-party harness uninterpretable no matter how many trials it got. That was true while the
only options were "own the call site" or "give up". It stops being true the moment both harnesses let us
choose the endpoint — qwen-code reads OPENAI_BASE_URL, Hermes profiles carry base_url. So we do not
instrument the harness at all: we sit between it and the model.

★ THE PROPERTY THAT MAKES IT EVIDENCE: the tap is OUTSIDE THE BLAST RADIUS of the thing under test.
A harness cannot misreport what it sent, because it is not the one reporting. Same move as reading
/proc/<pid>/environ instead of a unit file, and as asking the recipient instead of the sender
([[feedback-control-must-be-verified]] ADDENDUM 4).

WHAT IT RECORDS, per call, one JSON line:
  ts · arm · model · method · path · the FULL request body · req_bytes · status · resp_bytes · ms
The body is stored WHOLE. Truncation happens at DISPLAY time only — capture-side truncation is
unrecoverable and a re-run returns a different answer rather than the same one completed.

WHAT IT DELIBERATELY DOES NOT DO: modify, retry, cache, or normalise anything. If it changed what arrives
upstream, every measurement taken through it would be about the tap. See --selftest, which proves
byte-identical forwarding by chaining two taps and comparing what each recorded.

  envelope_tap.py --port 11435 --upstream http://127.0.0.1:11434 --arm qwen-code --log runs.jsonl
  envelope_tap.py --selftest            # prove forwarding is byte-identical; no upstream needed
"""
import argparse, http.server, json, socketserver, sys, threading, time, urllib.error, urllib.request

ARGS = None


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    """Holds its OWN config. ⚠ The first cut read a shared module-level ARGS at request time, so two taps
    running at once — which is the INTENDED usage, one per arm — silently shared whichever config was set
    last. The selftest caught it by needing exactly that topology. A global is a shared mutable the moment
    you run two of anything."""
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, handler, cfg):
        self.cfg = cfg
        super().__init__(addr, handler)


class Tap(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):        # keep stderr clean; the JSONL is the record
        pass

    def _proxy(self, method):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else b""

        url = self.server.cfg.upstream.rstrip("/") + self.path
        hdrs = {k: v for k, v in self.headers.items()
                if k.lower() not in ("host", "content-length", "connection")}
        req = urllib.request.Request(url, data=body if body else None, headers=hdrs, method=method)

        t0 = time.time()
        status, resp_bytes, err = 0, 0, None
        try:
            with urllib.request.urlopen(req, timeout=self.server.cfg.timeout) as up:
                status = up.status
                self.send_response(status)
                for k, v in up.headers.items():
                    if k.lower() not in ("transfer-encoding", "connection", "content-length"):
                        self.send_header(k, v)
                self.send_header("Connection", "close")
                self.end_headers()
                # Stream through unmodified — buffering would break SSE and change timing behaviour.
                while True:
                    chunk = up.read(8192)
                    if not chunk:
                        break
                    resp_bytes += len(chunk)
                    self.wfile.write(chunk)
                    self.wfile.flush()
        except urllib.error.HTTPError as e:
            status, err = e.code, str(e)
            payload = e.read()
            resp_bytes = len(payload)
            self.send_response(status); self.send_header("Connection", "close"); self.end_headers()
            self.wfile.write(payload)
        except Exception as e:                                   # noqa: BLE001
            err = f"{type(e).__name__}: {e}"
            self.send_response(502); self.send_header("Connection", "close"); self.end_headers()
        ms = int((time.time() - t0) * 1000)

        model, nmsg = None, None
        try:
            j = json.loads(body or b"{}")
            model = j.get("model")
            m = j.get("messages")
            nmsg = len(m) if isinstance(m, list) else None
        except Exception:
            pass

        rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "arm": self.server.cfg.arm,
               "model": model, "method": method, "path": self.path, "n_messages": nmsg,
               "req_bytes": len(body), "status": status, "resp_bytes": resp_bytes, "ms": ms,
               "error": err, "body": body.decode("utf-8", "replace")}
        with open(self.server.cfg.log, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")

    def do_POST(self):  self._proxy("POST")
    def do_GET(self):   self._proxy("GET")


def selftest():
    """Prove the tap forwards BYTE-IDENTICALLY, by chaining two taps and comparing what each recorded.

    If the tap mutated the body, tap A's record and tap B's record would differ — and every measurement
    taken through it would be about the tap rather than the harness. A tap that is not proven transparent
    is not an instrument.
    """
    import os, tempfile, urllib.request as u
    d = tempfile.mkdtemp()
    la, lb = os.path.join(d, "a.jsonl"), os.path.join(d, "b.jsonl")

    class Echo(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        def log_message(self, *a): pass
        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0); self.rfile.read(n)
            p = b'{"ok":true}'
            self.send_response(200); self.send_header("Content-Length", str(len(p)))
            self.send_header("Connection", "close"); self.end_headers(); self.wfile.write(p)

    cfg = lambda up, log, arm: argparse.Namespace(upstream=up, log=log, arm=arm, timeout=30)
    up = Server(("127.0.0.1", 0), Echo, cfg("", "", "echo")); upport = up.server_address[1]
    threading.Thread(target=up.serve_forever, daemon=True).start()

    # harness -> A -> B -> echo.  Two taps live AT ONCE, which is the real topology.
    sb = Server(("127.0.0.1", 0), Tap, cfg(f"http://127.0.0.1:{upport}", lb, "B")); bport = sb.server_address[1]
    threading.Thread(target=sb.serve_forever, daemon=True).start()
    sa = Server(("127.0.0.1", 0), Tap, cfg(f"http://127.0.0.1:{bport}", la, "A")); aport = sa.server_address[1]
    threading.Thread(target=sa.serve_forever, daemon=True).start()
    time.sleep(0.3)

    payload = json.dumps({"model": "probe",
                          "messages": [{"role": "user", "content": "\u03c0\u22483.14159 \u00fcn\u00efc\u00f8d\u00e9 " * 40}],
                          "temperature": 0}).encode()
    r = u.Request(f"http://127.0.0.1:{aport}/v1/chat/completions", data=payload,
                  headers={"Content-Type": "application/json"}, method="POST")
    with u.urlopen(r, timeout=30) as resp:
        resp.read()
    time.sleep(0.4)

    ra = [json.loads(l) for l in open(la, encoding="utf-8")]
    rb = [json.loads(l) for l in open(lb, encoding="utf-8")]
    print(f"  tap A recorded {len(ra)} call(s); tap B recorded {len(rb)}")
    if not ra or not rb:
        print("  \u26d4 a tap recorded nothing \u2014 cannot claim transparency"); return 2
    same_body = ra[0]["body"] == rb[0]["body"]
    same_sent = ra[0]["body"] == payload.decode()
    same_len  = ra[0]["req_bytes"] == len(payload)
    print(f"  {'OK  ' if same_body else 'FAIL'} A's body == B's body        (no mutation IN TRANSIT)")
    print(f"  {'OK  ' if same_sent else 'FAIL'} A's body == what was SENT   (no mutation ON CAPTURE)")
    print(f"  {'OK  ' if same_len  else 'FAIL'} byte count matches          ({ra[0]['req_bytes']} vs {len(payload)})")
    print(f"  {'OK  ' if ra[0]['arm']=='A' and rb[0]['arm']=='B' else 'FAIL'} arms labelled independently  (A={ra[0]['arm']} B={rb[0]['arm']})")
    ok = same_body and same_sent and same_len and ra[0]["arm"]=="A" and rb[0]["arm"]=="B"
    print("\nRESULT:", "TRANSPARENT \u2014 safe to measure through" if ok else "\u26d4 NOT transparent \u2014 do not measure through it")
    return 0 if ok else 1


def main():
    global ARGS
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=11435)
    ap.add_argument("--upstream", default="http://127.0.0.1:11434")
    ap.add_argument("--arm", default="unlabelled")
    ap.add_argument("--log", default="/tmp/envelope_tap.jsonl")
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("--selftest", action="store_true")
    ARGS = ap.parse_args()
    if ARGS.selftest:
        return selftest()
    srv = Server(("127.0.0.1", ARGS.port), Tap, ARGS)
    print(f"envelope-tap :{ARGS.port} -> {ARGS.upstream}  arm={ARGS.arm}  log={ARGS.log}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    sys.exit(main())
