"""Fail-closed external fleet paths; the authors' instance is tui/paths.example.json.

Environment FLEET_TUI_<KEY> wins over paths.json in XDG_CONFIG_HOME/fleet_tui
(or ~/.config/fleet_tui). There are no shipped external defaults. Invalid or
absent values return None, including during headless imports. Sources resolve
constants at startup; restart after editing configuration. The TUI's own state
under ~/.fleet_tui/ and ~/.config/fleet_tui stays application-owned and unchanged.
OS interfaces such as /proc and /sys are not fleet-layout configuration.
"""
import json
import os
from pathlib import Path

KEYS = (
    'curation_dir', 'hermes_cron_dir', 'hermes_state_db', 'research_dir',
    'comms_inbound_glob', 'passback_docs_glob', 'gpu_forensics_log',
    'hive_alert', 'reliability_file', 'external_ssd_temp', 'disk_path',
    'screenshots_dir',
)

PANEL_KEYS = {
    'jobs': ('hermes_cron_dir',),
    'health': ('hermes_state_db', 'reliability_file', 'external_ssd_temp',
               'gpu_forensics_log', 'disk_path', 'curation_dir'),
    'posture': ('curation_dir',),
    'inbox': ('curation_dir', 'hive_alert', 'comms_inbound_glob', 'passback_docs_glob'),
    'research_playlists': ('curation_dir',),  # external notification script only
}


def _read_config():
    try:
        root = os.environ.get('XDG_CONFIG_HOME') or os.path.expanduser('~/.config')
        data = json.loads((Path(root) / 'fleet_tui' / 'paths.json').read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, RuntimeError):
        return {}


def resolve(key: str, *parts: str) -> str | None:
    """Resolve a known key and optional child components; unknown keys are bugs."""
    if key not in KEYS:
        raise KeyError(key)
    env = 'FLEET_TUI_' + key.upper()
    value = os.environ[env] if env in os.environ else _read_config().get(key)
    if not isinstance(value, str) or not value.strip() or '\x00' in value:
        return None
    try:
        return os.path.join(os.path.expanduser(value), *parts)
    except (OSError, ValueError, RuntimeError):
        return None


def missing(keys=KEYS) -> list[str]:
    return [key for key in keys if resolve(key) is None]


def notice(keys) -> str:
    """Accept missing key names (not paths or markup); return Textual markup."""
    return '\n'.join(f'[dim]not configured: {key}[/]' for key in keys if key in KEYS)


def panel_notices() -> dict[str, str]:
    absent = set(missing())
    return {panel: notice([key for key in keys if key in absent])
            for panel, keys in PANEL_KEYS.items()}
