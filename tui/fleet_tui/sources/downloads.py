"""Read acquisition rows whose box attribution comes from the ledger, never a personal path heuristic."""
import json
from pathlib import Path

from fleet_tui.models import DownloadRow


def read_downloads(path, default_box="local", limit=8):
    try:
        rows = []
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            try:
                raw = json.loads(line)
                if not isinstance(raw, dict) or not raw.get("file"):
                    continue
                rows.append(DownloadRow(str(raw["file"]), str(raw.get("box") or default_box),
                                        str(raw.get("source") or ""), str(raw.get("agent") or ""),
                                        str(raw.get("kind") or ""), int(raw.get("ts", 0) or 0)))
            except Exception:
                continue
        return sorted(rows, key=lambda row: row.ts, reverse=True)[:limit]
    except Exception:
        return []


def from_boxes(boxes, limit=8):
    rows = []
    for box in boxes or []:
        if getattr(box, "downloads_path", ""):
            rows.extend(read_downloads(box.downloads_path, box.name, limit))
    return sorted(rows, key=lambda row: row.ts, reverse=True)[:limit]
