#!/usr/bin/env python3
"""stage_video_research.py — stage dispatch-ready briefs for the NEW playlist videos.

⚠ RUN `video_backlog_diff.py` FIRST TO PRODUCE THE INPUT LIST. Do NOT hand-roll the "is this already
researched?" check. A naive grep for `youtu.be/` in the hub misses the ~124 LEGACY auto-generated cards,
whose `data-source` is a Windows path (`C:\\…\\youtube_<ID>_FINAL.md`) — measured 2026-07-30: the naive
check resolved 86 ids where the correct one resolves 204. That gap re-staged an already-carded video and
silently OVERWROTE its card. Correct flow:

    python3 video_backlog_diff.py --out /path/new.txt
    VIDEO_NEWVIDS=/path/new.txt python3 stage_video_research.py

Writes: a shared _DISPATCH_PREAMBLE.md (FLEET_FEEDBACK §A + the video-research method), one brief
per video (specifics + transcript path), and STAGING_QUEUE.md (tiered dispatch list).
Transcripts: parses the fetched VTTs in _staging_transcripts/ -> clean transcript_<slug>_<id>.txt."""
import re, glob, os

BASE = "~/Fleet-PC-Passback/Research-fleet"
VTT_DIR = f"{BASE}/_staging_transcripts"
OUT = f"{BASE}/_staged_briefs"
FEEDBACK = "~/Fleet-PC-Passback/fleet-backbone-context/FLEET_FEEDBACK.md"
# input list ("<id>|<title>" per line). Was hard-coded to /tmp — shared by every parallel agent/job on the
# box, so two concurrent stagings clobber each other. Env-overridable now. (fix 2026-07-30)
NEWVIDS = os.environ.get("VIDEO_NEWVIDS", "/tmp/new_videos.txt")
os.makedirs(OUT, exist_ok=True)

# Owner's priority pin for THIS batch — was hard-coded to an old batch's id ("f61DCDwvFis") and silently
# misdirected dispatch on every later run. Now env-overridable and defaulted to the current batch's pin;
# set VIDEO_PRIORITY_PIN="" to disable. (fix 2026-07-30)
PRIORITY_PIN = os.environ.get("VIDEO_PRIORITY_PIN", "P1KpxzLVg7c")

# fleet-relevance pre-screen (orchestrator judgment from titles)
HIGH = {"f61DCDwvFis","L9QZ97y9Exg","xFEcAGB5kyg","uqNpKVpmajw","9gHcmhUDJfw",
        "mdPIjy-1Q6g","pDsTcrRVNc0","cV4zHxb-Q3k","g-qW8fQimyg",
        # 2026-06-23 new batch (fleet/Hermes/Gemma-local/memory-method/DeepSeek/cost):
        "DbeFq_uoaRs","Sb96po6S67k","YN05CyV_TpM","u6L9aedHqZc","zmrPY6S1FwY","K2BpNt3UBOQ",
        "NVkRkioBXQc","7zZy1QTvokM","TpEBYINwokA","ZCsPUsJUSf8","okdwcU-UC-w","ESELhY-G_9w",
        "mG4SmhWyeFA","n8rP6Ceskm4","mGYr9VqQnEI","Owv503rTqYY",
        # 2026-07-07 new playlist batch — clearly fleet-relevant (HomeLLM/Hermes/GLM/Ornith/MCP/RAG/A2A):
        "tC0Dv5qOcas","_Jdjq6pgIRg","wDpN2ORnqZk","VytSYCDhWQ0","GuTcle5edjk","tA7COD7l6o8",
        "SfP1YBO2tNo","bXn1BPYNHew","_X55fkwdC-Q","XbHeJL45USQ",
        # 2026-07-13 new batch — image-token MEMORY method (owner priority) + local-frontier + tiny function-calling:
        "Bbt8cEyzsTk","dElQ1atTSCI","tt9UJ0NiOzU",
        # 2026-07-28 new batch (77 videos). HIGH = bears on a CURRENT fleet decision, not merely interesting:
        #  local-model landscape we actually route to (GLM/Qwen/Kimi-K3/Colibri/local-coding),
        #  HomeLLM fine-tuning (Unsloth/Ollama FT), our own tools (Graphify), memory architecture
        #  (we run a Tier-2 brain), MCP (we run MCP servers), and cross-model routing.
        "SsUKTFSQoGM","T8v8Rxr8rMM","60lYsmAhoAg","ERCdFgTXNY4","A61WYw5-FLM","V6LmF7TuBmY",
        "uNfkHpNfXow","3uOOwUCfl9w","19xCOJxWU0A","2hMQ2k1JLA0","QfCpRTLSOB4","MsdZZ-HEUFo",
        "pTaSDVz0gok","HGPTUc7tEq4","LPDWUWP9SCk","U7fcJvVukHQ","zIiiu8hS1To","22iy2mDFiF8",
        "IwN-eK1s8og","3PbZ1h6buks","kDM2rBcvWh0","AQl5Q-0l7FQ",
        # 2026-07-30 new batch (4 videos). Buzz AI = OWNER PRIORITY, dispatch first.
        #  Buzz AI = a Claude-Code<->Codex interop tool (we run BOTH as fleet legs, so it bears directly on
        #  our cross-leg orchestration); token-limit/context method (we are context-bound on every long
        #  session); MCP servers for a home lab (we run an MCP registry + second-brain MCP).
        "P1KpxzLVg7c","Y8vAQ1FgNbM","NgAglRc_ccs"}
LOW = {"PhVBCMPx4W4","6TK7gH920uI","ioJ0dCbeWTs","7w_tjX04BDY","j7xKK1odKs0","MwZq2_J_lSY","vuezTFo4kRE",
       "NnYLzGMk8Tg","TSl0RZK5Slo","mOBvnwM5iX4",
       # 2026-07-07 new batch — tangential to the fleet (a language intro / tmux tool / animator / web framework):
       "qy4iPvFFcV8","5GtkyPvuvbQ","z6Kj8vSCOpE","PgyggSRHY1o",
       # 2026-07-28 batch — general web/infra/careers/opinion with no fleet thread:
       "3Qc49WnQnSg","XJC5WB2Bwrc","I2mWnh66Bkg","WZoC1HA1vec","4Lmqvn_yz-c","1-hC_erTDwA",
       "VQHRUQDCh_Q","CXSvKcLovAk","NJpP5Z26g0w","cZqFaMlufDY","98JTsdLSzuc","W99Sm8wldQU",
       "Ja6p4j0aeCw","131yAOjxHHQ","f39MnczcJZA","1PXFAFMgdns","sUJI49dTAms","yW3zMV2rFo4",
       "uJblcC4lKYw","ro5FHh_voqk","Noo0NWD0gHU","oqjn7UyCWWA","MsQACpcuTkU","xJaMTo2YgO8"}
def tier(v): return "HIGH" if v in HIGH else ("LOW" if v in LOW else "MED")

def parse_vtt(path):
    txt = open(path, encoding="utf-8", errors="replace").read()
    out, last = [], ""
    for ln in txt.splitlines():
        ln = ln.strip()
        if not ln or ln == "WEBVTT" or "-->" in ln or ln.startswith(("Kind:", "Language:", "NOTE", "STYLE", "::")):
            continue
        ln = re.sub(r"<[^>]+>", "", ln); ln = ln.replace("&nbsp;", " ").strip()
        if not ln or ln == last:
            continue
        if out and (ln in out[-1] or out[-1] in ln):     # rolling-caption overlap
            if len(ln) > len(out[-1]): out[-1] = ln
            last = ln; continue
        out.append(ln); last = ln
    return "\n".join(out)

def slug(t): return re.sub(r"[^a-z0-9]+", "-", t.lower()).strip("-")[:45]

# 1) shared preamble (FLEET_FEEDBACK §A + the method)
fb = open(FEEDBACK, encoding="utf-8").read()
m = re.search(r"## §A.*?(?=\n## §B)", fb, re.S)
A = m.group(0).strip() if m else "(paste FLEET_FEEDBACK §A manually)"
preamble = f"""# DISPATCH PREAMBLE — prepend before EVERY video-research dispatch
*(shared across all staged briefs; same text per backbone)*

{A}

---

## METHOD — video-research-report (apply all 4 sections)
- **§1 TRANSCRIPT REPORT** — read the transcript ONCE (small text file). Condensed overview + EVERY load-bearing claim as a numbered list. Sanity-check the parse (lesson 6).
- **§2 PER-CLAIM VERIFICATION** — one ROW per named entity/number/repo/person (lesson 4); self-citation hard law, this-run URLs only (lesson 7); match search source to claim type + direct owner/repo fetch before any ❌ (lesson 10); cap live fetches at the ~10-15 LOAD-BEARING (lesson 14). Verdicts: ✅ CORROBORATED / ⚠️ PARTIAL / ⚠️ UNSUBSTANTIATED / ❌ CONTRADICTED (❌ is a HIGH bar).
- **§3 RELEVANCE** — FIRST emit the six `RELEVANCE: <Project> = <HIGH|MED|LOW|NONE>` verdict lines exactly as specified in the ACTIONABLE ADDENDUM (they are parsed mechanically; prose is not a rating, and `NONE` is a correct and expected answer). Then rate to each Fleet project — the SIX exact names, which are also the six §5 buckets: **HomeLLM** (local fine-tuning/inference on dual RTX 5060 Ti) · **ParaKit** (drum-charting: audio detection/MIDI/automation) · **Fleet-Ops** (orchestration / routing / cost / agent methodology) · **Tooling-Infra** (CLIs, MCP servers, dev tooling, monitoring, security) · **Research-Pipeline** (the video/research pipeline itself) · **Memory** (the Tier-2 brain: memory architecture, wiki-links, hygiene, retrieval). HIGH/MED/LOW/NONE + the specific thread + an action from the CLOSED vocabulary (GET/ADOPT/ADAPT/VET/EXPLORE/WATCH/REJECT). ⚠ `TRY` and `MONITOR` are NOT valid — this line previously prescribed them, which is why legs emitted them; use VET/EXPLORE and WATCH instead.
- **§4 STANDOUT + SKILLS** — standout YES/NO/MAYBE + theme bucket; list any INSTALLABLE skills (name · what · install command · license).
- **Cross-reference prior fleet reports** where this overlaps (lesson 9) — cite + spend fresh effort on what's NEW. **SHIP A PARTIAL** — always leave a written report at the OUTPUT path (lesson 14).
"""
open(f"{OUT}/_DISPATCH_PREAMBLE.md", "w").write(preamble)

# 2) per-video briefs + parsed transcripts
rows = []
for line in open(NEWVIDS):
    line = line.strip()
    if not line or "|" not in line: continue
    vid, title = [x.strip() for x in line.split("|", 1)]
    sl = slug(title)
    tpath = f"{BASE}/transcript_{sl}_{vid}.txt"
    words = 0
    vtts = sorted(glob.glob(f"{VTT_DIR}/{vid}*.vtt"))
    if vtts:
        text = parse_vtt(vtts[0]); open(tpath, "w").write(text); words = len(text.split())
    brief = f"""# VIDEO-RESEARCH BRIEF — {title}
**Video:** {title}
**URL:** https://youtu.be/{vid}
**Transcript (fetched + parsed, {words} words):** {tpath}
**Fleet-relevance pre-screen:** {tier(vid)}

> BEFORE DISPATCH: prepend `_DISPATCH_PREAMBLE.md` (FLEET_FEEDBACK §A + the method).

## Task
Run the full video-research method (§1-§4 in the preamble) on this video, working from the parsed transcript above.
Focus §3 relevance on HomeLLM (local FT/inference), ParaKit (drum detection/MIDI), and the Fleet fleet (orchestration/cost/local-AI).

## Dispatch
- Full-leg roster: **kimi** (Hermes) + **grok** (owner desktop). Local models do NOT carry a full leg — use gemma4:31b-qat only for a SCOPED audit of the FINAL.
- OUTPUT: `{BASE}/RESULT_{sl}_<backbone>.md` (first line: `Generated by: <backbone>`)
- Then reconcile legs -> `FINAL_{sl}_<date>.md`; gemma-audit; build the visual edition + hub card.
"""
    open(f"{OUT}/{sl}_{vid}.md", "w").write(brief)
    rows.append((tier(vid), vid, title, sl, words))

# 3) master queue (tiered)
order = {"HIGH": 0, "MED": 1, "LOW": 2}
rows.sort(key=lambda r: (order[r[0]], r[2].lower()))
q = ["# STAGING QUEUE — NEW playlist videos (no report yet)", "",
     f"{len(rows)} videos staged. Dispatch HIGH first"
     + (f"; **{PRIORITY_PIN} is the owner's priority**." if PRIORITY_PIN else "."),
     "Full-leg roster = kimi + grok + agy-vresearch (Gemini 3.6 Flash) — parallel-safe, cloud; plus the "
     "free decomposed-local leg. Dispatch in waves of ~5-6. Prepend `_DISPATCH_PREAMBLE.md`.", "",
     "| Tier | Video | Brief | Words |", "|---|---|---|---|"]
for t, vid, title, sl, words in rows:
    star = " ⭐" if vid == PRIORITY_PIN else ""
    q.append(f"| {t}{star} | [{title}](https://youtu.be/{vid}) | `{sl}_{vid}.md` | {words} |")
open(f"{OUT}/STAGING_QUEUE.md", "w").write("\n".join(q) + "\n")
print(f"staged {len(rows)} briefs + transcripts -> {OUT}")
print("HIGH:", sum(1 for r in rows if r[0] == "HIGH"), "| MED:", sum(1 for r in rows if r[0] == "MED"),
      "| LOW:", sum(1 for r in rows if r[0] == "LOW"))
miss = [r[1] for r in rows if r[4] == 0]
print("transcripts parsed:", sum(1 for r in rows if r[4] > 0), "| empty:", miss)
