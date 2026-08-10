"""
Fleet mode management helper functions.
"""
import os

MODE_FILE = os.path.expanduser("~/.fleet_tui/fleet_mode")


def get_mode() -> str:
    """Get the current fleet mode, defaulting to 'normal'."""
    try:
        if os.path.exists(MODE_FILE):
            with open(MODE_FILE, 'r') as f:
                return f.read().strip()
        return "normal"
    except Exception:
        return "normal"


def set_mode(name: str) -> bool:
    """Set the fleet mode to name. Returns True on success, False on error."""
    valid_modes = {"normal", "focus", "night-research", "quiet", "emergency"}
    if name not in valid_modes:
        return False
    
    try:
        os.makedirs(os.path.dirname(MODE_FILE), exist_ok=True)
        with open(MODE_FILE, 'w') as f:
            f.write(name)
        return True
    except Exception:
        return False


def main():
    """CLI interface for modes - called directly from __main__.py"""
    import sys
    if len(sys.argv) == 1:
        print(get_mode())
    elif len(sys.argv) == 2:
        name = sys.argv[1]
        if set_mode(name):
            print(f"mode -> {name}")
        else:
            print(f"Invalid mode: {name}", file=sys.stderr)
            sys.exit(2)
    else:
        print("Usage: fleet mode [<name>]", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()