"""Configuration-only inventory of the fleet's boxes.

The source is deliberately file-only: a remote box is represented by paths that an owner has
mounted or relayed locally.  The monitor never opens a network connection merely to discover a box.
"""
from __future__ import annotations

import json
from pathlib import Path

from fleet_tui.models import DeviceLabel, FleetBox, ModelState

CONFIG_PATH = Path.home() / ".fleet_tui" / "boxes.json"


def _label(raw):
    if not isinstance(raw, dict):
        return DeviceLabel()
    try:
        cap = float(raw.get("power_cap_w", 0) or 0)
    except (TypeError, ValueError):
        cap = 0.0
    return DeviceLabel(str(raw.get("badge", "") or "")[:8],
                       str(raw.get("color", "") or "")[:32], max(0.0, cap))


def _box(raw):
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name", "") or "").strip()
    kind = str(raw.get("kind", "") or "").strip().lower()
    if not name or kind not in {"local", "remote"}:
        return None
    labels = raw.get("device_labels", {})
    labels = {str(key): _label(value) for key, value in labels.items()} if isinstance(labels, dict) else {}
    return FleetBox(name=name, kind=kind,
                    receipts_path=str(raw.get("receipts_path", "") or ""),
                    models_path=str(raw.get("models_path", "") or ""),
                    health_path=str(raw.get("health_path", "") or ""),
                    ledger_path=str(raw.get("ledger_path", "") or ""),
                    downloads_path=str(raw.get("downloads_path", "") or ""),
                    throughput_path=str(raw.get("throughput_path", "") or ""),
                    device_labels=labels)


def read_boxes(path=None):
    """Return configured boxes, or a useful zero-config single local box. Never raises."""
    try:
        raw = json.loads(Path(path or CONFIG_PATH).read_text(encoding="utf-8"))
        rows = raw.get("boxes", raw) if isinstance(raw, dict) else raw
        out = [_box(row) for row in rows] if isinstance(rows, list) else []
        out = [row for row in out if row is not None]
        return out or [FleetBox()]
    except Exception:
        return [FleetBox()]


def read_models(box, local_models=()):
    """Read a box's relayed model state, with local process-derived rows supplied by the caller.

    A relay is JSON data only.  Invalid rows are ignored so a partial remote file cannot take down
    the local monitor.  Sidecar state is kept as data rather than inferred from a service name.
    """
    if getattr(box, "kind", "local") == "local" and not getattr(box, "models_path", ""):
        return list(local_models or [])
    try:
        raw = json.loads(Path(box.models_path).read_text(encoding="utf-8"))
        rows = raw.get("models", raw) if isinstance(raw, dict) else raw
        out = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict) or not row.get("name"):
                continue
            state = str(row.get("state", "") or "").lower()
            if state not in {"", "busy", "idle", "asleep", "down"}:
                state = ""
            out.append(ModelState(name=str(row["name"]), loaded=bool(row.get("loaded", state in {"busy", "idle"})),
                                  gb=float(row.get("gb", 0) or 0), busy=bool(row.get("busy", state == "busy")),
                                  device=str(row.get("device", "") or ""), port=int(row.get("port", 0) or 0),
                                  state=state, wake_on_use=bool(row.get("wake_on_use", False))))
        return out
    except Exception:
        return []
