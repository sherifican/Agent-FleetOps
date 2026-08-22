"""Per-box serving throughput records. Identical model names are never merged across boxes."""
import json
from pathlib import Path

from fleet_tui.models import ThroughputRow


def read_throughput(boxes):
    out = {}
    for box in boxes or []:
        rows = {}
        try:
            path = getattr(box, "throughput_path", "")
            raw = json.loads(Path(path).read_text(encoding="utf-8")) if path else {}
            if not isinstance(raw, dict):
                raw = {}
            for name, value in raw.items():
                record = value if isinstance(value, dict) else {}
                try:
                    rate = float(record.get("tok_s", 0))
                except (TypeError, ValueError):
                    continue
                if rate > 0:
                    rows[str(name)] = ThroughputRow(str(name), rate, box.name, str(record.get("ts") or ""))
        except Exception:
            rows = {}
        out[getattr(box, "name", "local")] = rows
    return out
