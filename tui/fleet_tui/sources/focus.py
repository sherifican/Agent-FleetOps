"""Pure source module for reading/writing the focus mode semaphore."""

from fleet_tui.models import FocusState
import json
import os
import datetime


def _lock_path():
    return os.environ.get("FLEET_WATCHERS_LOCK", os.path.expanduser("~/.claude/curation/watchers.lock"))


def is_on() -> bool:
    """Check if the focus mode lock file exists."""
    return os.path.exists(_lock_path())


def read_state() -> FocusState:
    """Read the focus state from the lock file.
    
    Returns:
        FocusState: The current focus state. If lock file is absent, returns 
        FocusState(on=False, scope="noisy"). If lock file exists but is malformed,
        returns FocusState(on=True, scope="noisy", since="", by="") to avoid crashing.
    """
    if not is_on():
        return FocusState(on=False, scope="noisy")
    
    try:
        with open(_lock_path(), "r") as f:
            data = json.load(f)
        
        # Handle malformed JSON gracefully
        since = data.get("since", "")
        by = data.get("by", "")
        scope = data.get("scope", "noisy")
        
        return FocusState(on=True, since=since, by=by, scope=scope)
    except (json.JSONDecodeError, OSError):
        # If file exists but is malformed or unreadable, return default state
        return FocusState(on=True, scope="noisy", since="", by="")


def turn_on(scope="noisy", by="tui") -> FocusState:
    """Turn on focus mode by creating the lock file with JSON data.
    
    Args:
        scope: The scope of focus mode ("noisy" or "all")
        by: Who/what turned on focus mode
        
    Returns:
        FocusState: The newly created focus state
    """
    lock_path = _lock_path()
    since = datetime.datetime.now(datetime.timezone.utc).isoformat()
    
    data = {
        "since": since,
        "by": by,
        "scope": scope
    }
    
    with open(lock_path, "w") as f:
        json.dump(data, f)
    
    return read_state()


def turn_off() -> None:
    """Turn off focus mode by removing the lock file.
    
    Does not raise if the file doesn't exist (idempotent).
    """
    lock_path = _lock_path()
    try:
        os.remove(lock_path)
    except OSError:
        # File doesn't exist, which is fine
        pass
