---
name: shared-brain-bookkeeping
description: >
  Use when the task involves auditing, consolidating, updating, or garbage-collecting
  a project's shared brain files — the markdown knowledge/rule files that multiple
  agents read and write. Covers memory/feedback_*.md, brain/*.md, the agent roster
  table in the root instructions file, and any AGENTS.md files in subdirectories.
  Runs a structured audit script set and produces a diff-style report of proposed
  changes. Do NOT use for editing source code.
license: MIT
---

# Shared Brain-File Bookkeeping

A project's **shared brain** is the set of markdown knowledge/rule files — memory rules,
feedback notes, brain docs, the agent roster — that multiple agents read and write over
the life of the project. Left alone, these files go stale, duplicate each other, and
keep referencing retired agents and tools. This skill is the bookkeeping pass that
keeps the shared brain trustworthy.

## Portability note
The scripts are cross-platform; on Linux invoke them with `python3` (see the scripts'
comments for the one path-glob note). All paths below are relative to the project root.

## Invocation triggers
- Owner asks: "audit the brain files" or "which memory rules are stale?"
- Agent detects conflicting or duplicated rules during task execution.
- Quarterly maintenance window (owner discretion).

## Scope
Covers these paths relative to project root:
- `memory/feedback_*.md`
- `brain/*.md` (if directory exists)
- Root agent-instructions file (`CLAUDE.md` / `AGENTS.md` — agent roster table section only)
- `*/AGENTS.md` (recursively, 2 levels deep)

## Preconditions
1. Python 3.10+ available in environment.
2. `scripts/brain_inventory.py` exists alongside this SKILL.md.
3. Agent has read access to all memory paths.

## Procedure

### Step 1 — Inventory
Run `python3 scripts/brain_inventory.py --root . --output brain_inventory.json`
(pass the project dev tree as `--root`, e.g. `--root /path/to/project`, or `.` when
run from the project root).

This produces a JSON file with:
- `file_path`: relative path
- `last_modified`: ISO timestamp
- `word_count`: integer
- `heading_count`: number of H1/H2/H3
- `cross_reference_count`: how many other memory files mention this file's basename

### Step 2 — Staleness detection
Run `python3 scripts/staleness_detector.py --inventory brain_inventory.json --days 30 --output stale_report.json`.

Flag criteria:
- `cross_reference_count == 0` → STALE (no other inventoried file references this file's basename in its body)
- File frontmatter declares itself deprecated (`name` ends in `_SUPERSEDED_<date>`, OR `deprecated: true`, OR `superseded_by: <slug>`) → STALE
- File references an agent or tool that no longer exists (e.g., a retired model lane still cited post-migration without update) → STALE

(v1.1.0 note: previously this step also consumed `chat_log_index.md` for chat-side recency. That dependency was removed because the project did not maintain the chat-log-index file; the in-body cross-reference count is the sole orphan signal now. See Patch history below.)

### Step 3 — Duplication detection
Run `python3 scripts/duplication_detector.py --inventory brain_inventory.json --threshold 0.75 --output dup_report.json`.

Uses TF-IDF cosine similarity on file bodies. Threshold 0.75 means "75% semantic overlap."

### Step 4 — Consolidation plan
Read the three JSON reports and write `BRAIN_CONSOLIDATION_PLAN.md` containing:
- **Merge proposals:** pairs of files with >0.75 similarity, proposed merged path
- **Archive proposals:** stale files with no cross-references, proposed archive path
- **Update proposals:** files referencing deprecated agents/tools, proposed edits
- **Risk callouts:** any file marked CRITICAL that is also flagged stale (escalate to owner)

### Step 5 — Owner approval
Present `BRAIN_CONSOLIDATION_PLAN.md` to owner. The orchestrating agent spot-verifies each flag against the actual file on disk before propagating it (a confident "stale/duplicate" call can itself be stale — verify the artifact first). Do NOT execute deletions or moves without owner confirmation.

### Step 6 — Execution (post-approval)
Perform approved merges, moves, and edits. Update any `AGENTS.md` or root-instructions-file references that point to renamed files.

## Failure handling
- If Python dependencies (`scikit-learn` for TF-IDF) are missing, fall back to simple string-in-common-word matching and flag the lower-confidence result.
- If a file is locked or unreadable, log it and continue.

## Anti-patterns
- Do NOT delete files without owner approval.
- Do NOT merge CRITICAL-priority files into STANDARD-priority files (downgrades visibility).
- Do NOT run this Skill on source code or test files.
- Do NOT compress the report to save tokens — the no-compression discipline applies; full detail is required.

## Patch history

### v1.2.0 — 2026-05-27 (agent self-patch dispatch per the v1.2.0 patch brief)

- `staleness_detector.py`: `is_deprecated()` rewritten from content-substring heuristic to frontmatter-anchored detection (`name` ends in `_SUPERSEDED_<date>`, `deprecated: true`, or `superseded_by: <slug>`). Eliminates known v1.1.0 false positives: `project_release_versioning.md`, `feedback_context_via_keeper_agent.md`, `feedback_whats_new_panel_max_three_visible.md`. Resolves `OPEN_THREADS.md` item #2.
- `brain_inventory.py`: cross-reference computation now emits `explicit_reference_count` (count of wiki-style double-bracket slug link references) + `casual_reference_count` (count of bare-basename substring matches outside double-bracket links); `cross_reference_count` preserved as sum for backward compatibility. Resolves `OPEN_THREADS.md` item #8.
- `SKILL.md` §Procedure Step 2 staleness criteria list refreshed (drop the "first 10 lines" wording from the deprecation criterion to reflect the metadata-anchored check).

### v1.1.0 — 2026-05-26 (agent self-patch dispatch per the v1.1.0 patch brief)

- `staleness_detector.py`: stripped a still-active model lane from the `dead_agents` list (the lane stayed active through ~2026-06-15 per `feedback_cost_offloader_lane_through_jun15.md`)
- `brain_inventory.py`: extended memory globs to include `memory/reference_*.md` + `memory/project_*.md` (previously missed 16 files / ~18% of scope)
- `staleness_detector.py`: removed `chat_log_index.md` dependency (file not maintained in this project; in-body cross-reference counting is sufficient signal)
- `SKILL.md` §Procedure Step 2 updated to remove `--chat-log-index` flag from invocation example + reflect new orphan logic (cross-ref count alone, not AND'd with mtime cutoff)

### v1.0.0 — 2026-05-26 (initial deployment)

- Initial Skill deployment. See `BRAIN_CONSOLIDATION_PLAN.md` §6 for the 3 bugs that motivated v1.1.0.
