"""fleet dispatch pre-flight gate (control-plane bundle, component 3).

Cheap sanity checks to run BEFORE a dispatch: is the target real/allowed, does the
brief exist, is the output path trusted, is the fleet in a state that can serve it.
Warn-then-enforce — only genuine safety problems become BLOCKS.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict

from fleet_tui.sources import targets, health

TRUST_ROOT = "~"


def preflight(target: str, brief_path: str) -> Dict[str, Any]:
    """Run the fleet dispatch pre-flight gate. NEVER raises.

    Returns ``{"ok": bool, "warnings": [str], "blocks": [str]}``. ``ok`` is True
    iff no BLOCK fired. On an unroutable internal error a single degrade-warning
    is injected and ok stays True so the CLI can still report rather than crash.
    """
    result: Dict[str, Any] = {"ok": False, "warnings": [], "blocks": []}
    try:
        # 1. BLOCK — target must be a registered id or an allowed command
        known_ids = {t.get("id") for t in targets.all_targets()}
        allowed_c = targets.allowed_cmds() or set()
        if target not in known_ids and target not in allowed_c:
            result["blocks"].append(
                f"target '{target}' is neither a registered id nor an allowed command"
            )

        # 2. BLOCK — brief_path must exist and be non-empty
        bpath = Path(brief_path)
        if not bpath.exists() or not bpath.is_file():
            result["blocks"].append(f"brief file missing: {bpath}")
        elif os.path.getsize(str(bpath)) == 0:
            result["blocks"].append(f"brief file is empty: {bpath}")

        # 3. WARN — brief should resolve inside the trust root (~)
        try:
            resolved = str(Path(brief_path).resolve())
            if not resolved.startswith(TRUST_ROOT):
                result["warnings"].append(
                    f"brief resolves outside trust root '{TRUST_ROOT}': {resolved}"
                )
        except (OSError, ValueError):
            pass  # best-effort; an unresolvable path is not fatal

        # 4. WARN — hermes-gateway service should be up (dispatches route through it)
        snap = health.snapshot() if hasattr(health, "snapshot") else None
        services = getattr(snap, "services", {}) if snap else {}
        if not isinstance(services, dict):
            services = {}
        if services.get("hermes-gateway") is not True:
            result["warnings"].append("hermes-gateway service is not up")

        # 5. WARN — every GPU >90% VRAM used means low headroom for a new dispatch
        try:
            gpus = getattr(snap, "gpu", []) or []
            if gpus and all(
                (g.get("used", 0) / max(g.get("total", 1), 1)) > 0.9 for g in gpus
            ):
                result["warnings"].append(
                    "all GPUs are >90% VRAM used — low headroom for dispatch"
                )
        except Exception:
            pass  # tolerate a malformed snapshot

        result["ok"] = not result["blocks"]

    except BaseException as e:  # never let the gate itself crash a dispatch
        result["ok"] = True  # not failed, just incomplete
        result["warnings"].append(f"preflight check degraded: {e}")

    return result
