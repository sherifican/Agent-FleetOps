"""Pure reader for cloud-leg activity — which CLOUD models (codex / grok / kimi) are actively running a
fleet dispatch right now, and what they're working on. Rendered alongside the local models in the MODELS
panel. Dispatch-based (reliable + carries the 'what'): built from already-fetched dispatch.recent() data,
so it's pure with no extra I/O. Local legs (qwen/gemma/glm) are never matched."""

import os
import subprocess
import time

CLOUD_MARKERS = ("codex", "grok", "kimi", "claude", "agy")
SESSION_MARKERS = ("codex", "grok", "kimi")
# each cloud leg -> the exact process names (comm) its CLI may run as. `pgrep -x` needs the real exe
# name; the kimi CLI runs as 'kimi-code', codex/grok as themselves. (owner-reported 2026-07-08)
SESSION_PROCS = {
    "codex": ("codex",),
    "grok":  ("grok",),
    "kimi":  ("kimi", "kimi-code"),
    "antigravity": ("agy",),
}
_ext_cache = {"t": 0.0, "v": []}
_claude_cache = {"t": 0.0, "v": []}
_kimi_cache = {"t": 0.0, "v": []}
_codex_cache = {"t": 0.0, "v": []}


def is_cloud_leg(leg) -> bool:
    """True if a dispatch leg name denotes a CLOUD model (codex/grok/kimi and their variants)."""
    if not isinstance(leg, str) or not leg:
        return False
    s = leg.lower()
    return any(m in s for m in CLOUD_MARKERS)


def _parse_when(when):
    """Parse a dispatch 'when' stamp (YYYYMMDD-HHMMSS) to epoch seconds, or None."""
    try:
        return time.mktime(time.strptime(when, "%Y%m%d-%H%M%S"))
    except Exception:
        return None


def _claude_leg_display(leg: str) -> str:
    """A claude-* dispatch leg id -> 'Claude (<Model>)'. Maps the known legs to their model, else best-effort."""
    # mapping: "claude-opus" -> model "claude-opus-4-8"; "claude-sonnet" -> "claude-sonnet-5";
    # "claude-haiku" -> "claude-haiku-4-5". Then name = f"Claude ({_pretty_claude_model(model)})".
    # For an unknown "claude-<x>", use _pretty_claude_model("claude-<x>") inside the parens (never raise).

    # Extract model id from leg name
    if not leg.startswith("claude-"):
        return leg

    # Map leg names to model IDs
    leg_to_model = {
        "claude-opus": "claude-opus-4-8",
        "claude-sonnet": "claude-sonnet-5",
        "claude-haiku": "claude-haiku-4-5"
    }

    model_id = leg_to_model.get(leg, leg)

    # Get pretty model name
    pretty_model = _pretty_claude_model(model_id)

    return f"Claude ({pretty_model})"


def active_cloud_legs(dispatches) -> list:
    """Pure — from already-fetched dispatch.recent() dicts, return the RUNNING cloud legs as
    [{name, activity, started}] (activity = brief/tail = what it's doing). Never raises."""
    out = []
    for d in (dispatches or []):
        try:
            if not isinstance(d, dict) or not d.get("running"):
                continue
            leg = d.get("leg", "") or ""
            if not is_cloud_leg(leg):
                continue

            # Handle Claude dispatch legs specially
            name = leg
            if leg.startswith("claude"):
                name = _claude_leg_display(leg)

            out.append({
                "name": name,
                "activity": (d.get("brief") or d.get("tail") or "").strip(),
                "started": _parse_when(d.get("when", "")),
                "base": d.get("base"),            # dispatch base → lets the MODELS panel open the full output
            })
        except Exception:
            continue
    return out


def _pretty_claude_model(model_id: str) -> str:
    """Map a Claude model id to a short display name; return the input unchanged if unknown; "" -> ""."""
    if not model_id:
        return model_id

    # Exact ID mappings
    exact_mappings = {
        "claude-opus-4-8": "Opus 4.8",
        "claude-sonnet-5": "Sonnet 5",
        "claude-haiku-4-5": "Haiku 4.5"
    }

    if model_id in exact_mappings:
        return exact_mappings[model_id]

    # Fallback: look for opus/sonnet/haiku and title-case that word + version digits
    model_lower = model_id.lower()
    if "opus" in model_lower:
        return "Opus " + "".join(c for c in model_id.split("opus")[-1] if c.isdigit() or c == ".")
    elif "sonnet" in model_lower:
        return "Sonnet " + "".join(c for c in model_id.split("sonnet")[-1] if c.isdigit() or c == ".")
    elif "haiku" in model_lower:
        return "Haiku " + "".join(c for c in model_id.split("haiku")[-1] if c.isdigit() or c == ".")

    # Generic fallback for any claude-<family>-<version>. Returning the raw id here is
    # what produced "Claude (claude-fable-5)" — the vendor printed twice — the moment a
    # model family appeared that the hardcoded list above had never heard of. A new
    # family should render as a name, not as an id, without anyone editing this function.
    if model_lower.startswith("claude-"):
        bits = model_id[len("claude-"):].split("-")
        if bits and bits[0]:
            fam = bits[0].capitalize()
            ver = ".".join(b for b in bits[1:] if b.isdigit())
            return (fam + " " + ver).strip()

    # If no match, return the original ID
    return model_id


def _iter_proc_cmdlines():
    """Generator yielding (pid:int, argv:list[str]) for every readable process on the box.
    This is the ONLY place that touches /proc — all other logic is pure and testable.
    Never raises: skip any pid it cannot read and keep going."""
    try:
        for pid_dir in os.listdir("/proc"):
            if not pid_dir.isdigit():
                continue
            pid = int(pid_dir)
            try:
                with open(f"/proc/{pid}/cmdline", "rb") as f:
                    cmdline_bytes = f.read()
                    # Split on NUL bytes and filter out empty entries
                    argv = [arg for arg in cmdline_bytes.decode('utf-8', errors='ignore').split('\x00') if arg]
                    yield (pid, argv)
            except (OSError, IOError):
                # Skip unreadable processes
                continue
    except (OSError, IOError):
        # If we can't read /proc at all, return empty iterator
        return


def _claude_cmdlines() -> list:
    """Best-effort list of full command-line strings for every running `claude` process. Read via
    `pgrep -x claude` then /proc/<pid>/cmdline (NUL-separated -> spaces). Cached ~15s in a module cache
    (a subprocess — NEVER per refresh tick, same pattern as external_cloud_procs). Returns [] on any
    error. This is the ONLY function that does process I/O here (so tests monkeypatch it)."""
    now = time.time()
    if now - _claude_cache["t"] < 15.0:
        return _claude_cache["v"]

    out = []
    try:
        # Get PIDs of all claude processes
        r = subprocess.run(["pgrep", "-x", "claude"], capture_output=True, text=True, timeout=3)
        if r.returncode == 0 and r.stdout.strip():
            pids = r.stdout.strip().split('\n')
            for pid in pids:
                try:
                    # Read cmdline from /proc/<pid>/cmdline
                    with open(f"/proc/{pid}/cmdline", "rb") as f:
                        cmdline_bytes = f.read()
                        # NUL-separated -> spaces
                        cmdline = cmdline_bytes.decode('utf-8', errors='ignore').replace('\x00', ' ')
                        out.append(cmdline)
                except Exception:
                    continue
    except Exception:
        pass

    _claude_cache["t"] = now
    _claude_cache["v"] = out
    return out


def external_claude_workers() -> list:
    """From _claude_cmdlines(), return the running Claude WORKER legs (print-mode only) as
    [{name, activity, started}]. A cmdline is a worker iff it contains a '-p' or '--print' token
    (the orchestrator has neither -> excluded). Extract the model from a '--model <id>' token and
    surface it: name = "Claude (<Model>)" matching the rest of the cloud panel; activity = "worker"
    carries the single worker marker.
    started = None. Never raises -> [] on error."""
    try:
        workers = []
        for cmdline in _claude_cmdlines():
            parts = cmdline.split()
            # Worker iff -p / --print is a standalone TOKEN — NOT a substring: the interactive orchestrator's
            # cmdline carries flags like `--json-path`/`--spawned-by` that CONTAIN "-p" and must not match.
            if "-p" in parts or "--print" in parts:
                # Extract model from --model <id> token
                model = None
                for i, part in enumerate(parts):
                    if part == "--model" and i + 1 < len(parts):
                        model = parts[i + 1]
                        break

                # Format the name - capitalised model title alone, no parentheses or extra words
                if model:
                    name = f"Claude ({_pretty_claude_model(model)})"
                else:
                    name = "Claude"

                workers.append({
                    "name": name,
                    "activity": "worker",
                    "started": None
                })
        return workers
    except Exception:
        return []


# ---------- Codex model variant detection ----------

CODEX_PROFILE_DISPLAY = {
    "sol": "Sol",
    "terra": "Terra",
    "luna": "Luna"
}


def _is_codex_invocation(argv) -> bool:
    """True if an argv token list is a codex INVOCATION (not merely a mention of the word).

    A process counts when EITHER:
      (a) basename(argv[0]) is exactly 'codex', OR
      (b) some LATER token contains a path separator '/' AND its basename is exactly 'codex'
          (the wrapper case: `timeout 600 /home/…/.kimi-code/bin/kimi -m k3 -p …`).
    A bare token 'codex' that is NOT argv[0] is an ARGUMENT (e.g. `grep -r codex …`), never the program.
    """
    if not argv:
        return False
    try:
        if os.path.basename(argv[0]) == "codex":
            return True
        for token in argv[1:]:
            if "/" in token and os.path.basename(token) == "codex":
                return True
    except Exception:
        return False
    return False


def _codex_variant_from_argv(argv) -> tuple:
    """Extract variant information from codex command line.

    Return a tuple of (label, via):
      - If an exact "-m" or "--model" token is present and has a following value, take that value as the
        model id. Strip a leading "gpt-5.6-" prefix if present, then capitalise the remainder, so
        "gpt-5.6-sol" becomes "Sol". If nothing remains after stripping, use the raw id unchanged.
        Return (label, "model").
      - Else if an exact "-p" or "--profile" token is present with a following value, or a joined
        "--profile=<x>" / "-p=<x>" form, look the lowercased value up in CODEX_PROFILE_DISPLAY, falling
        back to the value capitalised. Return (label, "profile").
      - Else return ("", "default") — no variant information on the command line.
    Never raises.
    """
    raw = ""
    via = "default"
    
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok in ("-m", "--model") and i + 1 < len(argv):
            raw = argv[i + 1]
            # Strip leading "gpt-5.6-" prefix if present
            if raw.startswith("gpt-5.6-"):
                raw = raw[8:]
            # Capitalize the remainder
            if raw:
                raw = raw.capitalize()
            via = "model"
            break
        elif tok in ("-p", "--profile") and i + 1 < len(argv):
            raw = argv[i + 1]
            via = "profile"
            break
        elif tok.startswith("--profile="):
            raw = tok.split("=", 1)[1]
            via = "profile"
            break
        elif tok.startswith("-p="):
            raw = tok.split("=", 1)[1]
            via = "profile"
            break
        i += 1
    
    # If we found a profile, look it up or capitalize it
    if via == "profile" and raw:
        label = CODEX_PROFILE_DISPLAY.get(raw.lower(), raw.capitalize())
        return (label, via)
    
    # If we found a model, use it as the label
    if via == "model" and raw:
        return (raw, via)
    
    # No variant information
    return ("", via)


def _codex_effort_from_argv(argv) -> str:
    """Extract effort from codex command line.

    Return the effort ONLY if literally present. Accept both spellings seen in the wrappers:
      - a "-c" token whose following value starts with "model_reasoning_effort=" -> take the part after "="
      - a single joined token starting with "model_reasoning_effort=" -> same
    Strip surrounding double quotes from the value if present.
    Return "" when absent. Never invents a default. Never raises.
    """
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok == "-c" and i + 1 < len(argv):
            val = argv[i + 1]
            if val.startswith("model_reasoning_effort="):
                effort = val.split("=", 1)[1]
                # Strip surrounding double quotes if present
                if effort.startswith('"') and effort.endswith('"'):
                    effort = effort[1:-1]
                return effort
        elif tok.startswith("model_reasoning_effort="):
            effort = tok.split("=", 1)[1]
            # Strip surrounding double quotes if present
            if effort.startswith('"') and effort.endswith('"'):
                effort = effort[1:-1]
            return effort
        i += 1
    return ""


def _codex_mode_from_argv(argv) -> str:
    """Determine codex mode from command line.

    "dispatch" when an exact "exec" token appears among argv[1:], else "session".
    codex exec is the non-interactive fleet path; a plain `codex` is the owner's interactive session.
    Do NOT use -p for this. Never raises.
    """
    # Check if 'exec' appears anywhere after the first token (argv[0] is 'codex')
    for i in range(1, len(argv)):
        if argv[i] == "exec":
            return "dispatch"
    return "session"


def read_codex_procs() -> list:
    """Read running codex processes and return a list of dicts with variant info.
    Each dict has: {"pid": int, "variant": str, "via": str, "effort": str, "mode": str}
    Mode is exactly "dispatch" or "session".
    Variant is the display label from _codex_variant_from_argv().
    Effort is extracted from command line or "".
    One record per invocation; never raises."""
    
    now = time.time()
    if now - _codex_cache["t"] < 15.0:
        return _codex_cache["v"]

    try:
        procs = []
        for pid, argv in _iter_proc_cmdlines():
            try:
                if _is_codex_invocation(argv):
                    variant, via = _codex_variant_from_argv(argv)
                    effort = _codex_effort_from_argv(argv)
                    mode = _codex_mode_from_argv(argv)
                    procs.append({
                        "pid": pid,
                        "variant": variant,
                        "via": via,
                        "effort": effort,
                        "mode": mode
                    })
            except Exception:
                # Skip any single process that raises; return [] if the whole thing fails
                continue
        
        _codex_cache["t"] = now
        _codex_cache["v"] = procs
        return procs
    except Exception:
        # total failure (e.g. the /proc seam itself blew up) -> empty, never raise
        return []


def build_codex_rows(procs) -> list:
    """Pure composer turning codex proc dicts into display records.
    
    Each record is: {"name": "codex <variant>", "activity": "<fleet dispatch|interactive session>", "started": None}
    When effort is non-empty, append " · " + effort to activity.
    Dedupe identical (name, activity) pairs so two workers of the same variant collapse into one row.
    Preserve first-seen order."""
    rows = []
    seen = set()
    
    for p in procs:
        name = f"codex {p['variant']}".strip() if p["variant"] else "codex"
        activity = "fleet dispatch" if p["mode"] == "dispatch" else "interactive session"
        if p["effort"]:
            activity += f" · {p['effort']}"
        
        # Create a key for deduplication
        key = (name, activity)
        if key not in seen:
            rows.append({
                "name": name,
                "activity": activity,
                "started": None
            })
            seen.add(key)
    
    return rows


def codex_status() -> list:
    """Convenience that calls read_codex_procs() then build_codex_rows(). Never raises."""
    try:
        procs = read_codex_procs()
        return build_codex_rows(procs)
    except Exception:
        return []


def cloud_snapshot(dispatches) -> list:
    """Active cloud legs = fleet DISPATCHES (dispatch-based, carries the 'what') + external interactive CLI
    SESSIONS (process-based, best-effort), deduped by base model name so a fleet dispatch isn't double-listed.
    Also includes Claude worker legs detected via external_claude_workers()."""
    legs = active_cloud_legs(dispatches)
    have = {(l.get("name") or "").lower().split()[0].split("-")[0] for l in legs}   # 'codex-fleet' -> 'codex'

    # Kimi gets MODEL-SPECIFIC rows ("kimi K3" / "kimi K2.7 Code") instead of the flat
    # "kimi (session)". Which variant is running is the whole point — K3 is only selected by an
    # explicit `-m k3`, so a generic row hides whether the K3 trial is actually exercising K3.
    # Degrades to the old generic row if detection finds nothing or raises (panel must never die).
    try:
        kimi_rows = kimi_status()
    except Exception:
        kimi_rows = []

    # Codex gets MODEL-SPECIFIC rows instead of the flat "codex (session)".
    # Degrades to the old generic row if detection finds nothing or raises (panel must never die).
    try:
        codex_rows = codex_status()
    except Exception:
        codex_rows = []

    # Add external cloud processes
    for p in external_cloud_procs():
        base = p["name"].split()[0]                  # 'codex (session)' -> 'codex'
        if base == "kimi" and kimi_rows:
            continue                                 # superseded by the model-specific rows below
        if base == "codex" and codex_rows:
            continue                                 # superseded by the model-specific rows below
        if base not in have:
            legs.append(p)
            have.add(base)

    for r in kimi_rows:
        if not any((l.get("name") or "") == r["name"] for l in legs):
            legs.append(r)

    for r in codex_rows:
        if not any((l.get("name") or "") == r["name"] for l in legs):
            legs.append(r)

    # Add Claude workers
    for worker in external_claude_workers():
        # Check if this worker is already present by name to avoid duplicates
        worker_name = worker["name"].lower()
        if not any(w["name"].lower() == worker_name for w in legs):
            legs.append(worker)

    return legs


# ---------- Kimi model variant detection ----------

KIMI_DEFAULT_MODEL_ID = "kimi-code/kimi-for-coding"
KIMI_MODEL_DISPLAY = {
    "k3": "K3",
    "kimi-code/kimi-for-coding": "K2.7 Code",
    "kimi-for-coding": "K2.7 Code",
}


def _is_kimi_invocation(argv) -> bool:
    """True if an argv token list is a kimi INVOCATION (not merely a mention of the word).

    A process counts when EITHER:
      (a) basename(argv[0]) is exactly 'kimi' or 'kimi-code', OR
      (b) some LATER token contains a path separator '/' AND its basename is exactly 'kimi'
          (the wrapper case: `timeout 600 /home/…/.kimi-code/bin/kimi -m k3 -p …`).
    A bare token 'kimi' that is NOT argv[0] is an ARGUMENT (e.g. `grep -r kimi …`), never the program.
    """
    if not argv:
        return False
    try:
        if os.path.basename(argv[0]) in ("kimi", "kimi-code"):
            return True
        for token in argv[1:]:
            if "/" in token and os.path.basename(token) == "kimi":
                return True
    except Exception:
        return False
    return False


def _is_bare_kimi_code(argv) -> bool:
    """True for the flag-less child the wrapper spawns: the entire argv is the single token 'kimi-code'.
    Such a process proves kimi is up but tells us nothing about how it was started."""
    return len(argv) == 1 and os.path.basename(argv[0]) == "kimi-code"


def _kimi_model_from_argv(argv) -> str:
    """Raw model id from the token list: the token after an exact '-m'/'--model', the joined
    '--model=<id>' form, else KIMI_DEFAULT_MODEL_ID. Token EQUALITY only, never substring."""
    raw = KIMI_DEFAULT_MODEL_ID
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok in ("-m", "--model") and i + 1 < len(argv):
            raw = argv[i + 1]
            break
        if tok.startswith("--model="):
            raw = tok.split("=", 1)[1]
            break
        i += 1
    return raw


def _kimi_mode_from_argv(argv) -> str:
    """'dispatch' iff an exact '-p' or '--print' token is present, else 'session'. Never substring."""
    return "dispatch" if ("-p" in argv or "--print" in argv) else "session"


def read_kimi_procs() -> list:
    """Read running Kimi processes and return a list of dicts with model info.
    Each dict has: {"pid": int, "model": str, "mode": str, "raw_model": str}
    Mode is exactly "dispatch" or "session".
    Model is a display label from KIMI_MODEL_DISPLAY (unknown ids pass through unchanged).
    One record per invocation; a bare 'kimi-code' child never double-counts, but if it is the ONLY
    kimi process it still yields one default-model/session record. Never raises."""

    now = time.time()
    if now - _kimi_cache["t"] < 15.0:
        return _kimi_cache["v"]

    try:
        invocations = []
        bare_children = []
        for pid, argv in _iter_proc_cmdlines():
            try:
                if _is_bare_kimi_code(argv):
                    # flag-less child of a wrapper — fallback only, never its own row
                    bare_children.append((pid, argv))
                elif _is_kimi_invocation(argv):
                    invocations.append((pid, argv))
            except Exception:
                continue

        procs = []
        for pid, argv in invocations:
            try:
                raw_model = _kimi_model_from_argv(argv)
                model = KIMI_MODEL_DISPLAY.get(raw_model, raw_model)
                procs.append({
                    "pid": pid,
                    "model": model,
                    "mode": _kimi_mode_from_argv(argv),
                    "raw_model": raw_model,
                })
            except Exception:
                continue

        if not procs and bare_children:
            # kimi is demonstrably up but we cannot see how it started -> default model, session.
            pid = bare_children[0][0]
            procs.append({
                "pid": pid,
                "model": KIMI_MODEL_DISPLAY[KIMI_DEFAULT_MODEL_ID],
                "mode": "session",
                "raw_model": KIMI_DEFAULT_MODEL_ID,
            })

        _kimi_cache["t"] = now
        _kimi_cache["v"] = procs
        return procs
    except Exception:
        # total failure (e.g. the /proc seam itself blew up) -> empty, never raise
        return []


def build_kimi_rows(procs) -> list:
    """Pure composer turning kimi proc dicts into display records.
    Each record is: {"name": "kimi <MODEL>", "activity": "<fleet dispatch|interactive session>", "started": None}"""
    rows = []
    for p in procs:
        name = f"kimi {p['model']}"
        activity = "fleet dispatch" if p["mode"] == "dispatch" else "interactive session"
        rows.append({
            "name": name,
            "activity": activity,
            "started": None
        })
    return rows


def kimi_status() -> list:
    """Convenience that calls read_kimi_procs() then build_kimi_rows(). Never raises."""
    try:
        procs = read_kimi_procs()
        return build_kimi_rows(procs)
    except Exception:
        return []


def external_cloud_procs() -> list:
    """Best-effort: cloud CLI processes (codex/grok/kimi) running OUTSIDE the fleet dispatch system — the
    owner's own interactive sessions. Cached ~15s (a `pgrep` subprocess; NEVER per refresh tick). Returns
    [{name, activity, started}]. Matched by EXACT process name (`pgrep -x`) → no false positives; if a CLI
    runs under a different exe (node/python) it won't be caught — tune here once a real session's name is known."""
    now = time.time()
    if now - _ext_cache["t"] < 15.0:
        return _ext_cache["v"]
    out = []
    for label, names in SESSION_PROCS.items():         # iterate the new mapping
        matched = False                                 # avoid double-adding same label
        for name in names:                              # try each real name for this leg
            try:
                r = subprocess.run(["pgrep", "-x", name], capture_output=True, text=True, timeout=3)
                if r.returncode == 0 and r.stdout.strip():
                    matched = True
                    out.append({"name": f"{label} (session)", "activity": "interactive session", "started": None})
                    break                                   # one match is enough for this label
            except Exception:
                continue
    _ext_cache["t"] = now
    _ext_cache["v"] = out
    return out
