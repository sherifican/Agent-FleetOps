#!/usr/bin/env python3
"""actionable_rollup.py — build the cross-video ACTIONABLE ITEMS rollup.

Owner ask (2026-07-30): "compile a list of potentially useful skills, workflows, repos, tools, and
anything else that stands out or has high relevance to your projects or system stack / fleet operations
into an actionable items list, organized by priority/relevance to a particular project or system
function / fleet operations optimization / upgrades."

Reads every actionable-items table the pipeline produces and emits ONE prioritized document grouped by
BUCKET (which configured project it upgrades) then PRIORITY then ACTION.

Two table formats are handled:
  v1 (vision-synthesis harvest, pre-2026-07-30, 5 cols): Item | What | Action | Why | [MM:SS]
  v2 (transcript legs + syntheses, 2026-07-30+,  7 cols): Item | What | Bucket | Action | Priority | Why | [MM:SS]
v1 rows have no Bucket/Priority — they are bucketed heuristically and marked so a human can re-triage.

Sources scanned (recursive):
  Research-fleet/video/ACTIONABLE_ITEMS_LEDGER.md   (the vision harvest ledger)
  Research-fleet/video/**/SYNTHESIS_*.md            (per-video vision syntheses)
  Research-fleet/research/results/RESULT_*.md       (transcript legs)
  Research-fleet/research/finals/FINAL_*.md         (reconciled finals — highest trust)

Usage: python3 actionable_rollup.py [--out <path>]
"""
import re, os, glob, argparse, collections

BASE = os.path.expanduser(os.environ.get("VIDEO_ROOT", "./video-research"))  # EDIT ME or set VIDEO_ROOT
OUT_DEFAULT = f"{BASE}/ACTIONABLE_ROLLUP.md"

BUCKETS = ["Flagship-App", "Local-Models", "Fleet-Ops", "Tooling-Infra", "Research-Pipeline", "Memory", "Unsorted"]
PRIOS = ["P0", "P1", "P2", "—"]
ACTIONS = ["GET", "ADOPT", "ADAPT", "VET", "EXPLORE", "WATCH", "REJECT"]
# trust order: a FINAL is reconciled + audited; a synthesis is one model; a single leg is one model unverified
SRC_TRUST = {"FINAL": 3, "SYNTHESIS": 2, "LEDGER": 2, "RESULT": 1}

# heuristic bucketing for v1 rows (no Bucket column). First match wins; deliberately conservative —
# anything that does not clearly match stays Unsorted so a human re-triages rather than trusting a guess.
BUCKET_HINTS = [
    # EDIT ME: keywords for YOUR flagship domain.
    ("Flagship-App", r"\b(photo|raw|lens|exif)\b"),
    ("Local-Models", r"\b(fine[- ]?tun|unsloth|lora|qlora|quantiz|gguf|vram|distill|train(ing)?|dataset|rtx|gpu memory"
                r"|glm-?\d|qwen|codestral|gemma|llama|mistral|deepseek|moe\b|\d+b\b|ollama|llama\.?cpp|vllm)\b"),
    ("Research-Pipeline", r"\b(transcript|caption|subtitle|whisper|frame|vision|screenshot|synthesis|hub card|playlist|ocr)\b"),
    ("Tooling-Infra", r"\b(mcp|cli|docker|container|backup|monitor|systemd|cron|proxy|server|self[- ]host|dashboard|obsidian|syncthing)\b"),
    ("Fleet-Ops", r"\b(agent|orchestrat|rout(e|ing)|context|token|memory|prompt|skill|sub[- ]?agent|harness|cost|usage|claude code|codex|workflow)\b"),
]


def norm(s):
    s = re.sub(r"[*`_\[\]]", "", s or "").strip()
    return re.sub(r"\s+", " ", s)


def keyify(s):
    return re.sub(r"[^a-z0-9]+", "", norm(s).lower())


_STOP = {"the", "a", "an", "of", "for", "and", "to", "in", "on", "via", "with", "pattern", "agent"}


def tokenkey(s):
    """Order-insensitive token key. Merges 'ACP (Agent Client Protocol)' with 'Agent Client Protocol (ACP)'
    — different legs name the same thing in different word orders. Only EXACT token-set matches merge;
    merely-similar names are reported as possible duplicates instead of being silently combined."""
    toks = {t for t in re.split(r"[^a-z0-9]+", norm(s).lower()) if t and t not in _STOP}
    return " ".join(sorted(toks))


def jaccard(a, b):
    sa, sb = set(a.split()), set(b.split())
    return len(sa & sb) / len(sa | sb) if (sa | sb) else 0.0


def guess_bucket(item, what, why):
    blob = f"{item} {what} {why}".lower()
    for b, pat in BUCKET_HINTS:
        if re.search(pat, blob):
            return b
    return "Unsorted"


def parse_tables(text, source_label, video_label, track_headings=False):
    """Yield dict rows from every markdown table in `text` that looks like an actionable-items table.

    track_headings is for the multi-video LEDGER only, where each video is its own heading. For a
    single-video RESULT/FINAL/SYNTHESIS the file already names the video, and heading-tracking actively
    HURTS: section titles like "§5 — ACTIONABLE ITEMS" or "PART B — ACTIONABLE ITEMS (merged)" become the
    "video" label, which is exactly the traceability the column exists to provide. (fix 2026-07-30)
    """
    rows = []
    lines = text.splitlines()
    i = 0
    cur_video = video_label
    # section-ish headings that are never a video name, matched ANYWHERE in the heading, not just the start
    SECTION_RE = re.compile(r"actionable|standout|part\s+[a-e]\b|§|open question|refuted|conflict|honesty|"
                            r"verdict|relevance|landscape|appendix|summary|method|source", re.I)
    while i < len(lines):
        ln = lines[i]
        if track_headings:
            h = re.match(r"^#{1,3}\s+(.*)$", ln)
            if h and not SECTION_RE.search(h.group(1)):
                cand = norm(h.group(1))
                if cand:
                    cur_video = cand
        if ln.lstrip().startswith("|") and i + 1 < len(lines) and re.match(r"^\s*\|[\s:\-|]+\|\s*$", lines[i + 1]):
            hdr = [norm(c) for c in ln.strip().strip("|").split("|")]
            hl = [c.lower() for c in hdr]
            # An actionable-items table needs an ITEM column AND an ACTION column. The §2 per-claim
            # VERIFICATION matrix also has an "Item"/"Entity" column, but its judgement column is a
            # VERDICT (✅ CORROBORATED / ❌ CONTRADICTED), not an action — without this second condition
            # the rollup swallows every verification row ("Stars", "License", "Latest commit") as if it
            # were an actionable item. (fix 2026-07-30, caught on the first real run.)
            if not any(c.startswith("item") for c in hl):
                i += 1
                continue
            if not any(c.startswith(("action", "suggested")) for c in hl):
                i += 1
                continue
            has_bucket = any(c.startswith("bucket") for c in hl)
            has_prio = any(c.startswith("prior") for c in hl)
            idx = {c: k for k, c in enumerate(hl)}

            def col(names, default=None):
                for n in names:
                    for c, k in idx.items():
                        if c.startswith(n):
                            return k
                return default

            ci, cw = col(["item"]), col(["what"])
            cb = col(["bucket"]) if has_bucket else None
            ca = col(["action", "suggested"])
            cp = col(["prior"]) if has_prio else None
            cy = col(["why"])
            ct = col(["[mm", "mm:ss", "ts", "time"])
            j = i + 2
            while j < len(lines) and lines[j].lstrip().startswith("|"):
                cells = [norm(c) for c in lines[j].strip().strip("|").split("|")]
                if len(cells) >= 3 and any(cells):
                    def g(k):
                        return cells[k] if k is not None and k < len(cells) else ""
                    item = g(ci)
                    if item and not re.match(r"^[:\- ]+$", item):
                        action = (g(ca) or "").upper()
                        action = next((a for a in ACTIONS if a in action), "")
                        # second guard: a row inside an accepted table whose action cell is not one of the
                        # vocabulary verbs is not an actionable item (it is a stray/verification row).
                        if not action:
                            j += 1
                            continue
                        prio = (g(cp) or "").upper()
                        prio = next((p for p in PRIOS if p in prio), "")
                        bucket = g(cb) if cb is not None else ""
                        bucket = next((b for b in BUCKETS if b.lower() == bucket.lower()), "")
                        inferred = False
                        if not bucket:
                            bucket = guess_bucket(item, g(cw), g(cy))
                            inferred = True
                        rows.append(dict(item=item, what=g(cw), bucket=bucket, bucket_inferred=inferred,
                                         action=action or "—", prio=prio or "—", why=g(cy), ts=g(ct),
                                         source=source_label, video=cur_video))
                j += 1
            i = j
            continue
        i += 1
    return rows


def collect():
    rows = []
    led = f"{BASE}/video/ACTIONABLE_ITEMS_LEDGER.md"
    if os.path.exists(led):
        # the ledger is the ONLY multi-video file -> heading-tracking is correct there and only there
        rows += parse_tables(open(led, encoding="utf-8", errors="replace").read(), "LEDGER",
                             "(vision ledger)", track_headings=True)
    for pat, lab in ((f"{BASE}/video/**/SYNTHESIS_*.md", "SYNTHESIS"),
                     (f"{BASE}/research/finals/**/FINAL_*.md", "FINAL"),
                     (f"{BASE}/research/results/**/RESULT_*.md", "RESULT")):
        for f in glob.glob(pat, recursive=True):
            # derive a readable video label from the filename, not from in-document headings
            b = os.path.basename(f)
            b = re.sub(r"^(RESULT|FINAL|SYNTHESIS)_", "", b)
            b = re.sub(r"\.md$", "", b)
            b = re.sub(r"_(kimi|grok|gemini36|flash|codex-terra|claude-opus48|antigravity)$", "", b)  # EDIT ME: your leg-name suffixes
            b = re.sub(r"_\d{4}-\d{2}-\d{2}$", "", b)
            rows += parse_tables(open(f, encoding="utf-8", errors="replace").read(), lab, b)
    return rows


def dedup(rows):
    """Merge rows describing the same item; keep the highest-trust description, union the sources."""
    by = collections.OrderedDict()
    for r in rows:
        k = tokenkey(r["item"])
        if not k:
            continue
        if k not in by:
            by[k] = dict(r, sources=set(), videos=set())
        cur = by[k]
        cur["sources"].add(r["source"])
        if r["video"]:
            cur["videos"].add(r["video"])
        # prefer the higher-trust source's fields; prefer an explicit bucket/priority over an inferred one
        if SRC_TRUST.get(r["source"], 0) > SRC_TRUST.get(cur["source"], 0):
            for f in ("what", "why", "action", "ts", "source"):
                if r[f] and r[f] != "—":
                    cur[f] = r[f]
        if not r["bucket_inferred"] and cur["bucket_inferred"]:
            cur["bucket"], cur["bucket_inferred"] = r["bucket"], False
        if r["prio"] != "—" and cur["prio"] == "—":
            cur["prio"] = r["prio"]
        if r["action"] != "—" and cur["action"] == "—":
            cur["action"] = r["action"]
    return list(by.values())


def render(items):
    apos = {a: i for i, a in enumerate(ACTIONS)}
    ppos = {p: i for i, p in enumerate(PRIOS)}
    out = ["# ACTIONABLE ITEMS — cross-video rollup", "",
           "Everything the video-research pipeline surfaced that has real pull on the configured stack — skills, "
           "workflows, repos, tools, models, MCP servers, methods — **grouped by what it upgrades, then by "
           "priority**. Built by `actionable_rollup.py`; regenerate after every batch.", ""]
    tot = len(items)
    by_b = collections.Counter(i["bucket"] for i in items)
    by_p = collections.Counter(i["prio"] for i in items)
    out.append(f"**{tot} distinct items** · " + " · ".join(f"{b}: {by_b[b]}" for b in BUCKETS if by_b[b]))
    out.append("")
    out.append("**Priority:** " + " · ".join(f"{p}: {by_p[p]}" for p in PRIOS if by_p[p]))
    out.append("")
    out.append("> `⚠bucket` = the bucket was INFERRED from keywords (the row came from a pre-2026-07-30 table "
               "with no Bucket column) — re-triage before acting on it.  "
               "`src` = LEDGER/SYNTHESIS (vision) · RESULT (one unreconciled leg) · FINAL (reconciled + audited, "
               "highest trust). A single-leg RESULT row is **unverified** — confirm before adopting.")
    out.append("")
    for b in BUCKETS:
        sel = [i for i in items if i["bucket"] == b]
        if not sel:
            continue
        out.append(f"## {b}  ({len(sel)})")
        out.append("")
        for p in PRIOS:
            ps = [i for i in sel if i["prio"] == p]
            if not ps:
                continue
            ps.sort(key=lambda r: (apos.get(r["action"], 99), r["item"].lower()))
            label = {"P0": "P0 — bears on a decision we are making NOW",
                     "P1": "P1 — clear win, no urgency",
                     "P2": "P2 — interesting, speculative payoff",
                     "—": "Unprioritised (pre-2026-07-30 rows — triage these)"}[p]
            out.append(f"### {label}  ({len(ps)})")
            out.append("")
            out.append("| Item | Action | What it is | Why it matters to us | Source | Video |")
            out.append("|---|:---:|---|---|:---:|---|")
            for r in ps:
                flag = " ⚠bucket" if r["bucket_inferred"] else ""
                vids = "; ".join(sorted(r["videos"]))[:80]
                src = "/".join(sorted(r["sources"], key=lambda s: -SRC_TRUST.get(s, 0)))
                ts = f" `{r['ts']}`" if r["ts"] else ""
                out.append(f"| **{r['item']}**{flag} | {r['action']} | {r['what']} | {r['why']}{ts} | {src} | {vids} |")
            out.append("")
    # Near-duplicates: report, never silently merge. Different legs describing the same idea in different
    # words is SIGNAL (independent corroboration) — collapsing it automatically would hide that.
    keys = [(tokenkey(i["item"]), i["item"]) for i in items]
    near = []
    for x in range(len(keys)):
        for y in range(x + 1, len(keys)):
            j = jaccard(keys[x][0], keys[y][0])
            if j >= 0.5:
                near.append((round(j, 2), keys[x][1], keys[y][1]))
    if near:
        near.sort(reverse=True)
        out.append("## ⚠ Possible duplicates — review before acting")
        out.append("")
        out.append("Different legs naming the same idea differently. NOT merged automatically: independent "
                   "legs converging on one idea is corroborating evidence, and collapsing them would hide it. "
                   "Merge by hand once you have read both.")
        out.append("")
        out.append("| Overlap | Item A | Item B |")
        out.append("|:---:|---|---|")
        for j, a, b in near[:25]:
            out.append(f"| {j} | {a} | {b} |")
        out.append("")
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=OUT_DEFAULT)
    a = ap.parse_args()
    raw = collect()
    items = dedup(raw)
    open(a.out, "w", encoding="utf-8").write(render(items))
    print(f"rollup: {len(raw)} rows -> {len(items)} distinct items -> {a.out}")
    bc = collections.Counter(i["bucket"] for i in items)
    pc = collections.Counter(i["prio"] for i in items)
    print("  buckets:", dict(bc))
    print("  priority:", dict(pc))
