"""Auto-summarize a dispatch output into sidecar files via a local ollama model.

Long dispatch logs are expensive for an orchestrator to read. This condenses one
into `<base>.summary.md` (a few lines) + `<base>.actions.json` (machine-readable
status/needs/artifacts) using a small fast local model (gemma4:12b). NEVER raises.
"""

import json
import os
import re
import urllib.request

DISPATCH_DIR = os.path.expanduser("~/.fleet_tui/dispatches")
OLLAMA_URL = "http://localhost:11434/api/chat"
SUMMARY_MODEL = "gemma4:12b"

_SUMMARIZE_PROMPT = (
    "Summarize the following dispatch output into a compact sidecar.\n\n"
    "Return ONLY valid JSON with these keys and no extra text:\n"
    '{\n'
    '  "summary_md": "<4-8 line markdown: what happened / blockers / key decisions / '
    'next action. Preserve numbers, paths, sizes>",\n'
    '  "status": "ok" or "blocked" or "partial",\n'
    '  "needs_owner": <true|false>,\n'
    '  "needs_feedback": <true|false>,\n'
    '  "artifacts": ["<absolute file paths mentioned in the output>"],\n'
    '  "suggested_next": "<one-line suggested next step>"\n'
    '}\n\n---OUTPUT STARTS HERE---\n{output}'
)


def summarize_dispatch(base_name: str, min_words: int = 300) -> dict:
    """Condense a dispatch output into sidecar summary + actions files.

    Reads `<DISPATCH_DIR>/<base_name>.out` (falls back to `.log`). If the text is
    shorter than ``min_words`` it returns without hitting Ollama. Otherwise it posts
    the text to gemma4:12b and writes `<base>.summary.md` + `<base>.actions.json`.
    Returns a result dict; NEVER raises.
    """
    try:
        out_path = _find_output(base_name)
        if out_path is None:
            return {"ok": False, "reason": "no output"}
        with open(out_path, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        if not text.strip():
            return {"ok": False, "reason": "no output"}

        if len(text.split()) < min_words:
            return {"ok": True, "skipped": "short output", "summary_path": None}

        # NB: .replace not .format — the prompt contains literal JSON braces { } that
        # str.format would misread as fields.
        prompt_text = _SUMMARIZE_PROMPT.replace("{output}", text[:16_000])  # cap length
        payload = json.dumps({
            "model": SUMMARY_MODEL,
            "messages": [{"role": "user", "content": prompt_text}],
            "stream": False,
        }).encode("utf-8")
        req = urllib.request.Request(
            OLLAMA_URL, data=payload,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(req, timeout=180) as resp:
            reply = json.loads(resp.read().decode("utf-8"))
        raw_reply = (reply.get("message") or {}).get("content", "")
        if not isinstance(raw_reply, str):
            return {"ok": False, "reason": "ollama returned non-string content"}

        parsed = _parse_ollama_reply(raw_reply)
        summary_txt = parsed.pop("summary_md", "")
        summary_path = os.path.join(DISPATCH_DIR, f"{base_name}.summary.md")
        actions_path = os.path.join(DISPATCH_DIR, f"{base_name}.actions.json")
        with open(summary_path, "w", encoding="utf-8") as fh:
            fh.write(summary_txt)
        with open(actions_path, "w", encoding="utf-8") as fh:
            json.dump(parsed, fh, indent=2)
        return {"ok": True, "summary_path": summary_path,
                "actions_path": actions_path, "status": parsed.get("status", "")}
    except Exception as e:
        return {"ok": False, "reason": str(e)}


def _find_output(base_name: str):
    """Return path to <base>.out or <base>.log (whichever exists first), else None."""
    for ext in ("out", "log"):
        p = os.path.join(DISPATCH_DIR, f"{base_name}.{ext}")
        if os.path.exists(p):
            return p
    return None


def _parse_ollama_reply(raw: str) -> dict:
    """Strip an optional ```json fence, parse, and merge into a safe fallback shape."""
    fallback = {
        "summary_md": raw.strip(),
        "status": "partial",
        "needs_owner": False,
        "needs_feedback": True,
        "artifacts": [],
        "suggested_next": "",
    }
    s = raw.strip()
    m = re.match(r"^```(?:json)?\s*\n(.*?)\n```\s*$", s, re.S)
    if m:
        s = m.group(1).strip()
    try:
        parsed = json.loads(s)
        if isinstance(parsed, dict):
            for k in fallback:
                fallback[k] = parsed.get(k, fallback[k])
    except Exception:
        pass
    return fallback
