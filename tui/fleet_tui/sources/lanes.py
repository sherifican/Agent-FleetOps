"""Union admission ledgers by box; no machine name has semantic meaning here."""
import json
from pathlib import Path

from fleet_tui.models import LaneState


def read_lanes(boxes):
    grouped = {}
    for box in boxes or []:
        path = getattr(box, "ledger_path", "")
        if not path:
            continue
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
            rows = raw.get("lanes", raw) if isinstance(raw, dict) else raw
            for row in rows if isinstance(rows, list) else []:
                if not isinstance(row, dict) or not row.get("lane"):
                    continue
                lane = grouped.setdefault(str(row["lane"]), LaneState(str(row["lane"])))
                count = max(0, int(row.get("admits", row.get("live", 0)) or 0))
                lane.live += count
                lane.admits_by_box[box.name] = count
        except Exception:
            continue
    return sorted(grouped.values(), key=lambda row: row.lane)
