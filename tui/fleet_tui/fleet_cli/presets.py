"""
Preset management for fleet CLI.
"""

import json
import os
from typing import Dict, Any

PRESETS_PATH = os.path.expanduser("~/.fleet_tui/presets.json")

DEFAULT_PRESETS = {
    "gen-audit": {
        "cmd": "combo-gencode-audit",
        "prefix": "",
        "desc": "qwen3-coder GEN → qwen3.6 AUDIT"
    },
    "deep-audit": {
        "cmd": "fleet-model-dispatch qwen3.6:35b-a3b-q4_K_M",
        "prefix": "Audit the following rigorously:\n\n",
        "desc": "qwen3.6 deep audit"
    },
    "web-lookup": {
        "cmd": "fleet-model-dispatch gemma4:12b",
        "prefix": "Do a quick web_search and answer concisely:\n\n",
        "desc": "cheap web lookup"
    },
    "agentic-edit": {
        "cmd": "fleet-model-dispatch ornith:35b",
        "prefix": "",
        "desc": "Ornith autonomous file edit"
    }
}

def load_presets() -> Dict[str, Any]:
    """Load presets from file, or seed defaults if the file is absent."""
    try:
        with open(PRESETS_PATH, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        # Seed with defaults if the file doesn't exist or is invalid
        os.makedirs(os.path.dirname(PRESETS_PATH), exist_ok=True)
        with open(PRESETS_PATH, 'w') as f:
            json.dump(DEFAULT_PRESETS, f, indent=2)
        return DEFAULT_PRESETS
