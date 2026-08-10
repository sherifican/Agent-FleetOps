#!/usr/bin/env python3
"""
Append-only event log system.
"""

import os
import json
import time

EVENTS_PATH = os.path.expanduser("~/.fleet_tui/events.jsonl")


def log_event(kind: str, data: dict) -> None:
    """Append one JSON line to the event log."""
    try:
        os.makedirs(os.path.dirname(EVENTS_PATH), exist_ok=True)
        with open(EVENTS_PATH, "a") as f:
            event = {"ts": time.time(), "kind": kind, "data": data}
            f.write(json.dumps(event) + "\n")
    except Exception:
        # Never raises - logging must never break a caller
        pass


def read_events(limit: int = 50, kind: str = None) -> list:
    """Return the last `limit` events (newest-first), optionally filtered by `kind`."""
    try:
        if not os.path.exists(EVENTS_PATH):
            return []
        
        events = []
        with open(EVENTS_PATH, "r") as f:
            lines = f.readlines()
            # Read in reverse order to get newest first
            for line in reversed(lines[-limit:]):
                try:
                    event = json.loads(line.strip())
                    if kind is None or event.get("kind") == kind:
                        events.append(event)
                except Exception:
                    # Skip bad lines
                    continue
        
        return events
    except Exception:
        # Safe fallback
        return []