"""
Pure headless reader for WinClaude->Fleet passback files.
"""
import glob
import json
import os
import time

DOCS_GLOB = os.path.expanduser("~/Documents/PASSBACK_*.md")
PC_GLOB   = os.path.expanduser("~/pc-passback/PC_CLAUDE_*.md")
SEEN_FILE = os.path.expanduser("~/.fleet_tui/passback_seen.json")


def _load_seen():
    """Load seen state from JSON file. Return empty dict on any error."""
    try:
        with open(SEEN_FILE, "r") as f:
            obj = json.load(f)
        return obj if isinstance(obj, dict) else {}   # a valid non-dict JSON must not break seen.get()
    except Exception:
        return {}


def _save_seen(d):
    """Save seen state to JSON file. Swallow all errors."""
    try:
        os.makedirs(os.path.dirname(SEEN_FILE), exist_ok=True)
        with open(SEEN_FILE, "w") as f:
            json.dump(d, f)
    except Exception:
        pass


def _title(path):
    """Extract title from Markdown file. Return first heading or first line."""
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            # Read at most 4KB
            content = f.read(4096)
        
        # Find first Markdown heading
        for line in content.splitlines():
            if line.startswith("# "):
                return line[2:].strip()
            elif line.strip():
                return line.strip()
        
        # Fallback to basename without extension
        return os.path.splitext(os.path.basename(path))[0]
    except Exception:
        return os.path.splitext(os.path.basename(path))[0]


def _age(mtime, now=None):
    """Humanized age string."""
    if now is None:
        now = time.time()
    
    delta = now - mtime
    if delta < 60:
        return "just now"
    elif delta < 3600:
        return f"{int(delta // 60)}m ago"
    elif delta < 86400:
        return f"{int(delta // 3600)}h ago"
    else:
        return f"{int(delta // 86400)}d ago"


def list_passback():
    """List passback files newest-first with metadata."""
    try:
        # Collect all matching files
        docs_files = glob.glob(DOCS_GLOB)
        pc_files = glob.glob(PC_GLOB)
        
        # Deduplicate by absolute path
        all_paths = set(docs_files + pc_files)
        
        # Get stats for each file
        items = []
        seen = _load_seen()
        
        for path in all_paths:
            try:
                stat = os.stat(path)
                mtime = stat.st_mtime
                
                item = {
                    "path": path,
                    "name": os.path.basename(path),
                    "title": _title(path),
                    "mtime": mtime,
                    "age": _age(mtime),
                    "new": mtime > seen.get(path, 0)
                }
                items.append(item)
            except Exception:
                # Skip files that error on stat
                continue
        
        # Sort newest first
        items.sort(key=lambda x: x["mtime"], reverse=True)
        
        return items
    except Exception:
        return []


def new_count():
    """Return number of new items."""
    try:
        return sum(1 for item in list_passback() if item.get("new"))
    except Exception:
        return 0


def mark_seen(path):
    """Mark a single file as seen."""
    try:
        seen = _load_seen()
        try:
            stat = os.stat(path)
            mtime = stat.st_mtime
        except Exception:
            # File gone, mark with current time
            mtime = time.time()
        
        seen[path] = mtime
        _save_seen(seen)
    except Exception:
        pass


def mark_all_seen():
    """Mark all current items as seen."""
    try:
        items = list_passback()
        seen = _load_seen()
        for item in items:
            seen[item["path"]] = item["mtime"]
        _save_seen(seen)
    except Exception:
        pass
