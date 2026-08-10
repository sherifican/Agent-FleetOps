"""
Research launcher for fleet CLI.
"""

import os
import json
from typing import Dict, Any

from fleet_tui.sources import dispatch

RESEARCH_DIR = os.path.expanduser("~/pc-passback/Research-fleet")

def launch_research(slug: str, question: str) -> Dict[str, Any]:
    """
    Launch a research pipeline with the given slug and question.
    
    Returns a dict with:
    - 'brief_path': path to the brief file
    - 'grok_dispatch': dispatch base name for grok leg
    - 'codex_dispatch': dispatch base name for codex leg
    - 'stage': always 'launched'
    """
    # Create research directory
    os.makedirs(RESEARCH_DIR, exist_ok=True)
    
    # Write brief to file
    brief_path = os.path.join(RESEARCH_DIR, f"BRIEF_{slug}.md")
    with open(brief_path, 'w') as f:
        f.write(question)
    
    # Launch both legs
    grok_dispatch = dispatch.submit("grok-research", question, label=f"research:{slug}:grok")
    codex_dispatch = dispatch.submit("codex-fleet", question, label=f"research:{slug}:codex")
    
    return {
        'stage': 'launched',
        'brief_path': brief_path,
        'grok_dispatch': grok_dispatch,
        'codex_dispatch': codex_dispatch
    }
