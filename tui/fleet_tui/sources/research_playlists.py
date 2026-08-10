"""Research Playlists — a thin panel over the owner's video-research pipeline source queues.

Reads the configured playlists (default: the "AI Stuff" YouTube playlist) and lets the owner request a
"check for new videos + stage them for the research team" with one click. The button does NOT run any
orchestration in the TUI (the TUI is not an orchestrator): `request_check()` writes a REQUEST intent to a
FILE under ~/.fleet_tui/research_requests/ (which surfaces to Claude, who runs the actual check→stage flow)
and stamps last_checked. Owner-initiated only; no shell injection (intent goes in a JSON file). Pure readers
+ one thin request-writer, mirroring sources/dispatch.py.
"""
import json
import os
import datetime

from fleet_tui.models import Playlist

CONFIG = os.path.expanduser("~/.fleet_tui/research_playlists.json")
STATE = os.path.expanduser("~/.fleet_tui/research_playlists_state.json")   # last-checked per playlist
REQUEST_DIR = os.path.expanduser("~/.fleet_tui/research_requests")         # under ~ (trusted root)


def _config_path():
    return os.environ.get("FLEET_RESEARCH_PLAYLISTS", CONFIG)


def _state_path():
    return os.environ.get("FLEET_RESEARCH_STATE", STATE)


def _request_dir():
    return os.environ.get("FLEET_RESEARCH_REQUEST_DIR", REQUEST_DIR)


def _read_state() -> dict:
    """Read the sidecar last-checked state. Safe default {} on any error; never raises."""
    try:
        with open(_state_path()) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError, TypeError):
        return {}


def read_playlists() -> list:
    """Read the configured playlists -> list[Playlist], merging last_checked/last_result from the state
    sidecar. Safe default [] on a missing/malformed config; NEVER raises (degrade to an empty panel)."""
    try:
        with open(_config_path()) as f:
            data = json.load(f)
        state = _read_state()
        out = []
        for p in (data.get("playlists", []) if isinstance(data, dict) else []):
            if not isinstance(p, dict):
                continue
            name = str(p.get("name", "")).strip()
            if not name:
                continue
            st = state.get(name, {}) if isinstance(state.get(name), dict) else {}
            out.append(Playlist(
                name=name,
                url=str(p.get("url", "")),
                kind=str(p.get("kind", "youtube")),
                last_checked=str(st.get("last_checked", "")),
                last_result=str(st.get("last_result", "")),
            ))
        return out
    except (json.JSONDecodeError, OSError, TypeError, AttributeError):
        return []


def snapshot() -> list:
    """Convenience: the current playlist records (list[Playlist])."""
    return read_playlists()


def _stamp_checked(name: str, ts: str) -> None:
    """Record last_checked for a playlist in the state sidecar. Best-effort; never raises."""
    try:
        sp = _state_path()
        st = _read_state()
        entry = st.get(name) if isinstance(st.get(name), dict) else {}
        entry["last_checked"] = ts
        st[name] = entry
        os.makedirs(os.path.dirname(sp), exist_ok=True)
        with open(sp, "w") as f:
            json.dump(st, f)
    except OSError:
        pass


def request_check(name: str, url: str = "") -> str:
    """Owner-initiated (button): write a 'check this playlist for new videos + stage them' REQUEST intent to
    a JSON file under the request dir, and stamp last_checked. Returns the request-file path (or "" on error).
    Does NOT run the check itself — the intent surfaces to Claude, who runs the check->stage flow.
    """
    try:
        rd = _request_dir()
        os.makedirs(rd, exist_ok=True)
        ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
        safe = "".join(c if c.isalnum() else "-" for c in name)[:40].strip("-") or "playlist"
        path = os.path.join(rd, f"{safe}_{ts.replace(':', '-')}.json")
        with open(path, "w") as f:
            json.dump({
                "playlist": name,
                "url": url,
                "requested": ts,
                "pending": True,
                "action": "check_playlist_and_stage",
            }, f)
        _stamp_checked(name, ts)
        return path
    except OSError:
        return ""


def pending_requests() -> list:
    """List pending check-requests (for a surfacing hook / Claude to process). Safe default []; never raises."""
    try:
        rd = _request_dir()
        out = []
        for fn in sorted(os.listdir(rd)):
            if not fn.endswith(".json"):
                continue
            try:
                with open(os.path.join(rd, fn)) as f:
                    d = json.load(f)
                if isinstance(d, dict) and d.get("pending"):
                    out.append(d)
            except (json.JSONDecodeError, OSError):
                continue
        return out
    except OSError:
        return []
