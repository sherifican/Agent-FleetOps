"""Dispatch box — a THIN front-end over the EXISTING fleet dispatch scripts (grok-research / codex-fleet /
grok-code). It does NOT route or call models directly (the TUI is not an orchestrator): the owner types a
brief + picks a leg, the brief is written to a file and that leg's script is run in the background, capturing
output under ~/.fleet_tui/dispatches/. Owner-initiated only. Safe: brief goes in a FILE (no shell injection),
leg names are a fixed allow-list.
"""
import glob
import json
import os
import re
import shlex
import subprocess
import tempfile
import time

from fleet_tui.sources import targets

DISPATCH_DIR = os.path.expanduser("~/.fleet_tui/dispatches")   # under ~ (codex-fleet trusted-root)

# The meta-prompt Kimi (K2.7, `kimi-cli`, subscription) uses to reformat a rough brief into this repo's standard
# fleet-dispatch format + surface the relevant tools/files/paths. Honesty-bound: mark inferred items, never
# fabricate paths/facts. The whole thing (prompt + brief) goes to a temp file → `kimi-cli -f` (no argv/quoting
# issues on multi-line briefs).
KIMI_REVISE_PROMPT = """Reformat the ROUGH BRIEF below into this fleet's STANDARD dispatch-brief format.
Return ONLY the revised brief — no preamble, no closing remarks, no code fences.

Use exactly these sections (drop a section only if truly N/A):
**Task** — one tight sentence: the objective.
**Context** — the minimum background the model needs to act correctly.
**Relevant files & paths** — absolute paths referenced or clearly implied. Mark any you INFER (not stated) with a leading ⚠.
**Tools / commands** — fleet tools or shell commands that apply.
**Constraints & lessons** — guardrails, gotchas, must-nots.
**Output** — what to return and where.

Rules:
- Be concise; keep the owner's intent, tighten the wording.
- Surface tools/files/paths that are RELEVANT based on the brief.
- Flag anything ambiguous the owner should clarify with a leading ⚠ (own line under the relevant section).
- Do NOT invent paths, filenames, commands, or facts you can't justify from the brief. Uncertain → ⚠, don't guess silently.

--- ROUGH BRIEF ---
{brief}
"""

STALE_SECS = int(os.environ.get("FLEET_TUI_STALE_SECS", "1800"))


def revise_brief(brief: str, timeout: int = 180):
    """Pipe the owner's rough brief through Kimi to reformat into the standard brief + surface tools/files/paths.
    Synchronous (call from a worker thread). Returns the revised text, or None on empty/timeout/CLI error."""
    if not brief or not brief.strip():
        return None
    tf = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".kbrief", delete=False) as f:
            f.write(KIMI_REVISE_PROMPT.format(brief=brief))
            tf = f.name
        env = {**os.environ, "KIMI_CLI_TIMEOUT": str(timeout)}
        r = subprocess.run(["kimi-cli", "-f", tf], capture_output=True, text=True,
                           timeout=timeout + 15, env=env)
        out = (r.stdout or "").strip()
        return out or None
    except Exception:
        return None
    finally:
        if tf:
            try:
                os.unlink(tf)
            except Exception:
                pass

# allow-list: display name -> the existing script (each takes <brief-file> <output-file>)
LEGS = {
    "grok-research": "grok-research",   # web research (Argus / divergent)
    "codex-fleet": "codex-fleet",       # codex analysis (convergent)
    "grok-code": "grok-code",           # grok coding
    "codex-driver": "codex-driver",     # codex orchestrator (decomposes + delegates down)
}


def submit(cmd: str, brief: str, label: str = None, allowed=None):
    """Write the brief to a file and run `<cmd> <brief-file> <out>` in the background. `cmd` is a full
    command (may include fixed args, e.g. 'fleet-model-dispatch qwen3-coder:30b'); it is validated against
    `allowed` (the registry allow-list — targets.allowed_cmds() by default) so NOTHING arbitrary runs.
    `label` = display name. Returns the base path (or None on a disallowed cmd / empty brief)."""
    allowed = allowed if allowed is not None else targets.allowed_cmds()
    if cmd not in allowed or not brief.strip():
        return None
    try:
        os.makedirs(DISPATCH_DIR, exist_ok=True)
        ts = time.strftime("%Y%m%d-%H%M%S")
        slug = re.sub(r"[^A-Za-z0-9._-]+", "-", (label or cmd)).strip("-")[:40] or "dispatch"
        base = os.path.join(DISPATCH_DIR, f"{ts}-{slug}")
        brief_f, out_f, log_f, done_f, meta_f = (base + s for s in (".brief", ".out", ".log", ".done", ".meta"))
        with open(brief_f, "w") as f:
            f.write(brief)
        with open(meta_f, "w") as f:
            json.dump({"leg": label or cmd, "cmd": cmd, "brief": brief[:240], "started": ts}, f)
        # cmd is from the trusted allow-list (word-split by sh so its args survive); brief/out/log shlex-quoted
        full = (f"{cmd} {shlex.quote(brief_f)} {shlex.quote(out_f)} "
                f"> {shlex.quote(log_f)} 2>&1; touch {shlex.quote(done_f)}")
        subprocess.Popen(["setsid", "sh", "-c", full],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        # Add feedback debt gate - create a marker file indicating this dispatch owes feedback
        feedback_file = base + ".feedback_required"
        with open(feedback_file, "w") as f:
            json.dump({"leg": label or cmd, "cmd": cmd, "started": ts}, f)
        
        # Log the dispatch event
        try:
            from fleet_tui.fleet_cli import events
            events.log_event("dispatch", {"cmd": cmd, "label": label or cmd, "base": os.path.basename(base)})
        except Exception:
            # Never fails - logging must not break dispatch
            pass
            
        return base
    except Exception:
        return None


def _is_partial(base: str) -> bool:
    """True if a dispatch produced output but its worker exited rc!=0 — the wrappers (codex-fleet /
    grok-dispatch, H3 audit fix) drop a `<out>.PARTIAL` marker. The TUI passes `<base>.out` as the
    wrapper's OUT arg, so the marker is `<base>.out.PARTIAL`; also accept a bare `<base>.PARTIAL`.
    Presence IS the signal (an empty marker still counts) — never raises."""
    try:
        return os.path.exists(base + ".out.PARTIAL") or os.path.exists(base + ".PARTIAL")
    except Exception:
        return False


def full_output(base_name: str) -> dict:
    """The COMPLETE output of one dispatch (by its base name, from recent()['base']): the full .out (or
    .log) text + running status + the brief. Safe {} default. Lets the TUI open/watch a dispatch's result."""
    try:
        base = os.path.join(DISPATCH_DIR, base_name)
        text = ""
        for f in (base + ".out", base + ".log"):
            if os.path.exists(f):
                text = open(f, errors="replace").read()
                if text.strip():
                    break
        brief = ""
        if os.path.exists(base + ".brief"):
            brief = open(base + ".brief", errors="replace").read()
        # running only if the dispatch actually EXISTS and hasn't marked .done (else it's gone/unknown)
        exists = any(os.path.exists(base + s) for s in (".meta", ".brief", ".out", ".log"))
        running = exists and not os.path.exists(base + ".done")
        return {"text": text or "(no output yet)", "running": running, "brief": brief,
                "partial": _is_partial(base)}
    except Exception:
        return {"text": "(unavailable)", "running": False, "brief": "", "partial": False}


def recent(limit=8) -> list:
    """[{leg, brief, when, running, tail}] newest-first — the recent dispatches + their status/output tail."""
    out = []
    try:
        metas = sorted(glob.glob(os.path.join(DISPATCH_DIR, "*.meta")), key=os.path.getmtime, reverse=True)
        for m in metas[:limit]:
            base = m[:-len(".meta")]
            try:
                meta = json.load(open(m))
            except Exception:
                meta = {}
            tail = ""
            for f in (base + ".out", base + ".log"):
                if os.path.exists(f):
                    lines = open(f, errors="replace").read().splitlines()
                    if lines:
                        tail = "\n".join(ln.rstrip() for ln in lines[-8:]).strip()
                        break
            done = base + ".done"
            running = not os.path.exists(done)
            elapsed = None
            if not running:                                  # MEASURED wall-clock: .done mtime − brief mtime
                try:
                    elapsed = max(0, int(os.path.getmtime(done) - os.path.getmtime(base + ".brief")))
                except Exception:
                    elapsed = None
            
            # Compute age and stale status
            age = None
            stale = False
            if running:
                try:
                    start = os.path.getmtime(base + ".brief")
                    age = max(0, int(time.time() - start))
                    stale = age >= STALE_SECS
                except Exception:
                    age = None
                    stale = False
            else:
                # For done dispatches, use the already-computed elapsed value
                age = elapsed
                stale = False
            
            out.append({
                "leg": meta.get("leg", "?"),
                "cmd": meta.get("cmd", ""),          # the runnable (carries the target model for local legs)
                "brief": meta.get("brief", ""),
                "when": meta.get("started", ""),
                "running": running,
                "elapsed": elapsed,
                "base": os.path.basename(base),
                "tail": tail,
                "age": age,
                "stale": stale,
                "partial": _is_partial(base),
            })
    except Exception:
        pass
    return out


def clear_done() -> int:
    """Archive every FINISHED dispatch (its `.done` marker exists) out of the recents list — moves the
    whole file-set into DISPATCH_DIR/.archive/ so recent() (which globs DISPATCH_DIR/*.meta, non-recursive)
    no longer lists it. RUNNING dispatches are kept. Reversible (files are moved, not deleted). Returns the
    count cleared. Never raises."""
    n = 0
    try:
        arc = os.path.join(DISPATCH_DIR, ".archive")
        os.makedirs(arc, exist_ok=True)
        for meta in glob.glob(os.path.join(DISPATCH_DIR, "*.meta")):
            base = meta[:-len(".meta")]
            if not os.path.exists(base + ".done"):
                continue  # still running → keep it
            for ext in (".meta", ".brief", ".out", ".log", ".done", ".feedback_required"):
                f = base + ext
                if os.path.exists(f):
                    try:
                        os.replace(f, os.path.join(arc, os.path.basename(f)))
                    except Exception:
                        pass
            n += 1
    except Exception:
        pass
    return n


def open_feedback_debts() -> list:
    """Get list of open feedback debts. Glob DISPATCH_DIR/*.feedback_required, newest-first by mtime.
    Returns list of dicts with base, leg, started, output_ready (bool), and brief."""
    try:
        feedback_files = sorted(glob.glob(os.path.join(DISPATCH_DIR, "*.feedback_required")), key=os.path.getmtime, reverse=True)
        debts = []
        for f in feedback_files:
            base = os.path.basename(f)[:-len(".feedback_required")]  # Remove .feedback_required suffix
            try:
                with open(f, "r") as fd:
                    debt = json.load(fd)
                # Check if output is ready (i.e., if the .done file exists)  
                done_file = f"{DISPATCH_DIR}/{base}.done"
                output_ready = os.path.exists(done_file)
                
                # Get brief content for display
                brief_file = f"{DISPATCH_DIR}/{base}.brief"
                brief = ""
                if os.path.exists(brief_file):
                    try:
                        with open(brief_file, "r") as bf:
                            brief = bf.read()[:120]  # First 120 chars
                    except Exception:
                        pass
                debts.append({
                    "base": base,
                    "leg": debt.get("leg", "?"),
                    "started": debt.get("started", ""),
                    "output_ready": output_ready,
                    "brief": brief
                })
            except Exception:
                # If a feedback file can't be read, skip it rather than crash
                continue
        return debts
    except Exception:
        # Return empty list on any error (safe degredation)
        return []


def close_feedback(base_name: str) -> bool:
    """Close a feedback debt by removing the .feedback_required marker file.
    Returns True if file existed and was removed, False if it didn't exist."""
    try:
        feedback_file = os.path.join(DISPATCH_DIR, f"{base_name}.feedback_required")
        if os.path.exists(feedback_file):
            os.remove(feedback_file)
            return True
        return False
    except Exception:
        # If the file can't be removed, return False (no debt closed)
        return False
