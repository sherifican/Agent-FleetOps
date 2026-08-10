"""Pure composer: correlate each IN-FLIGHT model/leg with the fleet dispatch it's running, so the MODELS
panel can reveal WHAT a busy model is working on (the dispatch title it was given + its brief).

No I/O — it joins already-fetched data (ModelStates + the cloud snapshot + dispatch.recent()). Never raises
→ []/safe defaults. Local busy models are matched to a RUNNING dispatch whose cmd targets them
(`fleet-model-dispatch <model>` carries the model tag); a busy model with no linked TUI dispatch is reported
HONESTLY (no fabricated title) — the gpu-busy flag is global, so a model can be busy without a TUI dispatch.
Sidecars (loaded models ending in " (:<port>)") are labeled as services, never as unlinked tasks.
"""

import re

_SERVICE_PORTS = {
    8336: "vision service",
    8090: "GLM service"
}

_PORT_RE = re.compile(r"\:(\d+)\)\s*$")


def _service_label(model_name):
    """If model_name ends in " (:<port>)", return the service label for that port; else None."""
    if not model_name:
        return None
    match = _PORT_RE.search(str(model_name))
    if not match:
        return None
    port = int(match.group(1))
    label = _SERVICE_PORTS.get(port, "llama-server service")
    return f"{label} (:{port})"


def _running(dispatches):
    return [d for d in (dispatches or []) if isinstance(d, dict) and d.get("running")]


def _match_local(model_name, running):
    """The running dispatch whose cmd targets this local model, or None. Match = the model tag appears in
    the dispatch cmd (`fleet-model-dispatch qwen3-coder:30b` → matches model `qwen3-coder:30b`)."""
    if not model_name:
        return None
    for d in running:
        if model_name in str(d.get("cmd", "") or ""):
            return d
    return None


def build_inflight(models, cloud, dispatches) -> list:
    """[{name, kind: 'local'|'cloud'|'service', title, brief, base}] — one entry per IN-FLIGHT model/leg, in panel
    order (local busy models first, then cloud legs). `title` = the dispatch label it was given (local
    unmatched → 'loaded · GPU active · no linked TUI dispatch'; cloud → its leg name). `base` = dispatch base (for
    'watch output'), or None. Never raises."""
    out = []
    running = _running(dispatches)
    # local: loaded + busy models, joined to their dispatch by cmd target
    for m in (models or []):
        try:
            if not (getattr(m, "loaded", False) and getattr(m, "busy", False)):
                continue
            name = getattr(m, "name", "?") or "?"
            svc = _service_label(name)
            if svc:
                # This is a sidecar/service — never matched to a dispatch
                out.append({"name": name, "kind": "service",
                            "title": svc, "brief": "", "base": None})
                continue
            d = _match_local(getattr(m, "name", ""), running)
            if d:
                out.append({"name": name, "kind": "local",
                            "title": str(d.get("leg") or d.get("cmd") or "?"),
                            "brief": str(d.get("brief") or "").strip(),
                            "base": d.get("base")})
            else:
                out.append({"name": name, "kind": "local",
                            "title": "loaded · GPU active · no linked TUI dispatch", "brief": "", "base": None})
        except Exception:
            continue
    # cloud: each leg from the snapshot already carries its activity(brief) + base (dispatch-derived legs)
    for c in (cloud or []):
        try:
            c = c or {}
            out.append({"name": str(c.get("name", "?") or "?"), "kind": "cloud",
                        "title": str(c.get("name", "?") or "?"),
                        "brief": str(c.get("activity") or "").strip(),
                        "base": c.get("base")})
        except Exception:
            continue
    return out
