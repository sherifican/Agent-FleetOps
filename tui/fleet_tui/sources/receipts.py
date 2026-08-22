"""Hermetic-friendly artifact-receipt reader for one or many configured boxes."""
import json
from pathlib import Path

from fleet_tui.models import ReceiptRow


def _status(rc, size):
    if str(rc) not in {"", "0"}:
        return "failed"
    if str(rc) == "0" and size == 0:
        return "empty"
    return "ok" if str(rc) == "0" else "unknown"


def read_receipts(path, box="local", limit=8):
    """Read JSONL receipts newest-first; bad rows are skipped and unavailable paths are empty."""
    try:
        rows = []
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            try:
                raw = json.loads(line)
                if not isinstance(raw, dict) or not raw.get("name"):
                    continue
                size = int(raw.get("bytes", raw.get("size", 0)) or 0)
                rc = str(raw.get("rc", "") or "")
                rows.append(ReceiptRow(str(raw["name"]), box=str(raw.get("box") or box),
                                       model=str(raw.get("model") or ""), rc=rc, bytes=max(0, size),
                                       ts=str(raw.get("ts") or ""), status=_status(rc, max(0, size))))
            except Exception:
                continue
        return sorted(rows, key=lambda row: row.ts, reverse=True)[:limit]
    except Exception:
        return []


def from_boxes(boxes, limit=8):
    rows = []
    for box in boxes or []:
        if getattr(box, "receipts_path", ""):
            rows.extend(read_receipts(box.receipts_path, box.name, limit))
    return sorted(rows, key=lambda row: row.ts, reverse=True)[:limit]
