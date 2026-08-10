"""Codex-PC-Link bridge status — is Fleet's SSH tunnel to the Windows box's codex app-server up?

Pure headless reader (ZERO textual import). It only READS status — it never opens/closes the tunnel.
The link is: an SSH tunnel forwards Fleet 127.0.0.1:<port> -> the Windows codex app-server, which serves
/readyz (HTTP 200 when up). So: UP = the local port is listening AND /readyz returns 200; DOWN = the port
is listening but the app-server isn't ready; OFF = the tunnel isn't up. Subprocess checks are CACHED
(~12s) because the TUI refresh loop is ~1s and must never hammer ss/curl (crash-hardening rule).
Config (optional): ~/.fleet_tui/codex_link.json  {"port": 4500, "host_label": "WinPC"}.
"""
import json
import os
import subprocess
import time

CONFIG = os.path.expanduser("~/.fleet_tui/codex_link.json")
_cache = {"t": 0.0, "v": None}


def _config_path():
    return os.environ.get("FLEET_CODEX_LINK_CONFIG", CONFIG)


def _read_config():
    """(port, host_label, enabled) from the config; safe defaults (4500, 'WinPC', True) on any error.
    enabled=False fully disables the bridge (no probing, omitted from the TUI). Never raises."""
    try:
        with open(_config_path()) as f:
            d = json.load(f)
        if isinstance(d, dict):
            return (int(d.get("port", 4500)), str(d.get("host_label", "WinPC")),
                    bool(d.get("enabled", True)))
    except (json.JSONDecodeError, OSError, TypeError, ValueError):
        pass
    return 4500, "WinPC", True


def _port_listening(port) -> bool:
    """Is 127.0.0.1:<port> a local LISTEN socket (the forwarded tunnel end)? False on any error."""
    try:
        r = subprocess.run(["ss", "-ltn"], capture_output=True, text=True, timeout=4)
        return f"127.0.0.1:{port}" in r.stdout
    except Exception:
        return False


def _readyz(port) -> int:
    """HTTP status of the app-server /readyz through the tunnel; 0 on any error."""
    try:
        r = subprocess.run(
            ["curl", "-s", "-m", "3", "-o", os.devnull, "-w", "%{http_code}",
             f"http://127.0.0.1:{port}/readyz"],
            capture_output=True, text=True, timeout=5)
        return int((r.stdout or "").strip() or 0)
    except Exception:
        return 0


def _compute() -> dict:
    port, label, enabled = _read_config()
    if not enabled:
        # Disabled by owner (codex remote only works desktop-client↔desktop-client; parked).
        # Short-circuit: no ss/curl probing, and the bridges formatter omits the segment entirely.
        return {"state": "disabled", "port": port, "host_label": label,
                "http_code": None, "detail": "disabled (set enabled:true in codex_link.json to re-enable)"}
    listening = _port_listening(port)
    code = _readyz(port) if listening else 0
    if code == 200:
        state = "up"
    elif listening:
        state = "down"
    else:
        state = "off"
    detail = {"up": "app-server reachable", "down": "tunnel up, app-server not ready",
              "off": "tunnel down"}[state]
    return {"state": state, "port": port, "host_label": label,
            "http_code": (code or None), "detail": detail}


def read_status(force: bool = False) -> dict:
    """Cached Codex-PC-Link status: {state: up|down|off, port, host_label, http_code, detail}.
    Safe default on any error; NEVER raises (the panel degrades to a muted cell)."""
    now = time.time()
    if not force and _cache["v"] is not None and now - _cache["t"] < 12:
        return _cache["v"]
    try:
        v = _compute()
    except Exception:
        v = {"state": "off", "port": 4500, "host_label": "WinPC", "http_code": None, "detail": "error"}
    _cache["t"] = now
    _cache["v"] = v
    return v
