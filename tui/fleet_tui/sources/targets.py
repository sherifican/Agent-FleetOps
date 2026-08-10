"""Dispatch-target registry — the grouped set of things the dispatch box can fire (cloud legs / local
models / combos / teams). Each target maps to an EXISTING command (a leg, a fleet-side runner script, or
codex-driver-as-router for open-ended team work) — the TUI stays a thin runner, never an orchestrator.

Default lives in code (works out-of-the-box); the owner can override by dropping a JSON file at
~/.fleet_tui/dispatch_targets.json with the same shape. Commands are an ALLOW-LIST — dispatch.submit()
only runs a cmd that appears here, so nothing arbitrary can be injected.

A target = {id (button label), cmd (runnable, may include fixed args), desc (legend), prefix? (text prepended
to the brief — used to give codex-driver a team framing)}.
"""
import json
import os

CONFIG_PATH = os.path.expanduser("~/.fleet_tui/dispatch_targets.json")

DEFAULT = {
    "groups": [
        {"name": "Cloud legs", "targets": [
            {"id": "grok-research", "cmd": "grok-research", "desc": "web research · divergent"},
            {"id": "codex-fleet", "cmd": "codex-fleet", "desc": "codex convergent analysis"},
            {"id": "grok-code", "cmd": "grok-code", "desc": "grok coding"},
            {"id": "codex-driver", "cmd": "codex-driver", "desc": "orchestrator: decompose + delegate down"},
            {"id": "agy-pro", "cmd": "agy-pro", "desc": "Antigravity · Gemini 3.1 Pro · reasoning (verify)"},
            {"id": "agy-flash", "cmd": "agy-flash", "desc": "Antigravity · Gemini 3.5 Flash · cheap/fast"},
            {"id": "agy-research", "cmd": "agy-research", "desc": "Antigravity · Gemini · web/tools research"},
            {"id": "agy-gptoss", "cmd": "agy-gptoss", "desc": "Antigravity · GPT-OSS 120B · open-weights"},
            {"id": "agy-claude", "cmd": "agy-claude", "desc": "Antigravity · Claude Opus 4.6 · cross-check"},
        ]},
        {"name": "Claude legs (subscription)", "targets": [
            {"id": "claude-opus", "cmd": "claude-opus", "desc": "Opus 4.8 worker · complex/important"},
            {"id": "claude-sonnet", "cmd": "claude-sonnet", "desc": "Sonnet 5 worker · quick/medium (default)"},
        ]},
        {"name": "Local models", "targets": [
            {"id": "qwen3-coder", "cmd": "fleet-model-dispatch qwen3-coder:30b", "desc": "best code GEN"},
            {"id": "qwen3.6", "cmd": "fleet-model-dispatch qwen3.6:35b-a3b-q4_K_M", "desc": "best AUDIT"},
            {"id": "gemma4:31b", "cmd": "fleet-model-dispatch gemma4:31b-it-qat", "desc": "audit / general"},
            {"id": "gemma4:12b", "cmd": "fleet-model-dispatch gemma4:12b", "desc": "cheap sleeper"},
            {"id": "ornith:35b", "cmd": "fleet-model-dispatch ornith:35b", "desc": "AUTONOMOUS agentic coder"},
            {"id": "ornith:9b", "cmd": "fleet-model-dispatch ornith:9b", "desc": "agentic · GPU-light"},
            {"id": "glm-flash", "cmd": "fleet-model-dispatch glm-flash:latest", "desc": "tool-caller"},
        ]},
        {"name": "Combos", "targets": [
            {"id": "gen→audit", "cmd": "combo-gencode-audit", "desc": "qwen3-coder GEN → qwen3.6 AUDIT"},
            {"id": "3-way reconcile", "cmd": "reconcile-dispatch", "desc": "Claude→codex→grok rotation"},
        ]},
        {"name": "Teams", "targets": [
            {"id": "visual/vega", "cmd": "codex-driver",
             "prefix": "VISUAL/VEGA TEAM TASK — produce the chart/asset. Decompose + delegate down "
                       "(grok = polish/SDK · codex = data-depth · local models = Vega-Lite specs).\n\n",
             "desc": "router → codex-driver, team-framed"},
        ]},
    ]
}


def list_groups(path: str = CONFIG_PATH) -> list:
    """The registry groups — from the override file if present + valid, else the built-in default. Never raises."""
    try:
        d = json.load(open(path))
        if isinstance(d, dict) and isinstance(d.get("groups"), list) and d["groups"]:
            return d["groups"]
    except Exception:
        pass
    return DEFAULT["groups"]


def all_targets(path: str = CONFIG_PATH) -> list:
    """Flat list of every target across groups (index order matches the rendered buttons)."""
    return [t for g in list_groups(path) for t in g.get("targets", [])]


def allowed_cmds(path: str = CONFIG_PATH) -> set:
    """The set of runnable commands — the dispatch allow-list."""
    return {t.get("cmd", "") for t in all_targets(path) if t.get("cmd")}
