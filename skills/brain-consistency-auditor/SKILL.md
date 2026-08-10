---
name: brain-consistency-auditor
description: Use when a project's shared knowledge/brain files have been edited (CHANGES_PENDING.md, VERSION_HISTORY.md, the project README for agents, the master PLAN file, memory/*.md, MEMORY.md). Cross-references that the brain layer is internally consistent — CHANGES_PENDING headings vs PLAN sections, VERSION_HISTORY monotonicity, MEMORY.md index completeness vs disk files, and dual-path parity when memory lives at two on-disk locations. Flags stale references (e.g., a CHANGES_PENDING entry titled for one patch version while its body retains the previous version's references — a class of bug seen on the reference setup during a patch ship), missing index entries for new memory rule files, version-sequence gaps. Invoke when the user says "audit brain files", "check brain consistency", "verify memory index", or after any brain-file edit session.
---

# Brain Consistency Auditor

Audits a project's shared knowledge layer ("brain files": changelogs, version history, a master plan, and a file-per-fact memory store with a hand-maintained index) for internal consistency after edits.

Closes the brain-file consistency gap surfaced repeatedly on the reference setup — a patch-release entry body retained pre-amendment references; the memory dir dual-path drifted (one path missing entries the other had); a VERSION_HISTORY entry was missed and caught only by a later release-janitor pass; the MEMORY.md index didn't track newly-added rule files until a manual sync. The brain layer lives under the project root and is audited by the local orchestrator agent.

## When to Use

Activates whenever:

1. **Brain file edited** — `CHANGES_PENDING.md`, `VERSION_HISTORY.md`, the project README for agents, the master `PLAN` file, `memory/*.md`, `MEMORY.md` modified in last N minutes (configurable; default 10 min)
2. **User explicitly invokes** via `/brain-audit` (or the equivalent skill-invocation in your agent framework)
3. **User asks** "audit brain files", "is the brain consistent", "check MEMORY.md vs disk"
4. **Post-ship trigger** — fires automatically after a slice's brain-bookkeeping step completes (paired with a ship-verification cron job or hook; see [[shared-brain-bookkeeping]])

DO NOT activate for:
- Brain files outside the project under audit (other projects' memory layers)
- Single-line edits that obviously can't break consistency (e.g., typo fix)
- Mid-edit state (only fire AFTER an edit session completes; race conditions otherwise)

## Procedure

### Step 1 — Inventory the Brain Layer

All paths are under the project root. Read the brain-file set + memory dir state:

```bash
cd <project-root>
# Brain-file headers + sizes (some bookkeeping files may live only on another machine — list whatever is present)
ls -la AGENT_README.md PLAN.md CHANGES_PENDING.md VERSION_HISTORY.md memory/MEMORY.md 2>/dev/null

# Memory rule files at both on-disk paths
ls memory/*.md | wc -l
ls <secondary-memory-path>/*.md | wc -l
```

NOTE (reference setup): on the machine being audited, the project root had the agent README and both memory dirs, but `CHANGES_PENDING.md`, `VERSION_HISTORY.md`, and the master plan were NOT present (they were ship-bookkeeping files kept on a different machine). Every check below SKIPs (reports `N/A — file absent`) rather than FAILs when its target file is missing, so the auditor degrades gracefully. If/when those files appear, the checks light up automatically.

### Step 2 — Run Six Consistency Checks

**Check 1 — VERSION_HISTORY.md monotonicity.** (Skip with `N/A` if `VERSION_HISTORY.md` is absent.) Extract all `### v<X>` headings; verify they're in strictly descending order; verify no version-numbers are skipped (e.g., -10 to -8 would skip -9). On the reference setup, a historical backfill stub closes one known gap; future gaps surface as WARN.

```bash
grep -nE "^### v[0-9]" VERSION_HISTORY.md | head -20
```

Compare adjacent entries; flag any non-monotonic-descending sequence + any skipped version segment.

**Check 2 — CHANGES_PENDING.md top entry body internal consistency.** (Skip with `N/A` if `CHANGES_PENDING.md` is absent.) For the TOP entry only (most recent ship breadcrumb), scan for stale references — pre-change terms appearing inside a post-change entry. Specifically look for:
- Old asset/component names inside entries about the newer versions that replaced them
- `TENTATIVE` flags inside entries with "Zero TENTATIVE remain" summary lines
- Pre-bump VERSION strings in post-bump entries
- Old function names in slices that renamed them

Flag each finding with line number + suggested correction.

**Check 3 — MEMORY.md index vs disk parity.** For each `feedback_*.md` / `project_*.md` / `reference_*.md` file in BOTH memory paths, confirm a corresponding entry in MEMORY.md exists with matching slug. Flag orphans (file exists, no index entry) AND broken entries (index points to non-existent file).

```bash
ls memory/feedback_*.md | xargs -I {} basename {} .md
# Compare against MEMORY.md entry slugs
```

**Check 4 — Memory dual-path parity (canonical vs secondary memory path).** On the reference setup the memory layer exists at two on-disk paths under the project root: the canonical `memory/` and a secondary path. (This replaced an earlier dual-write arrangement split across two machines/sync folders.) For each file that exists at BOTH paths, confirm the content matches; flag drift. CAVEAT: the two paths were NOT a strict byte-identical mirror on the reference setup (one held roughly twice as many files as the other, and the two used different slug conventions — underscored vs hyphenated). So treat "present at one path only" as INFORMATIONAL (Tier 3), not FAIL; treat genuine content drift on a same-named file as WARN. Confirm with the owner whether the secondary path is meant to be a full mirror or an intentional curated subset before flagging missing files as errors.

```bash
cd <project-root>
for f in memory/*.md; do
  b=$(basename "$f")
  alt="<secondary-memory-path>/$b"
  if [ ! -f "$alt" ]; then echo "ONLY_IN_memory (info): $b"; continue; fi
  if ! diff -q "$f" "$alt" >/dev/null 2>&1; then echo "DRIFT: $b"; fi
done
```

**Check 5 — PLAN section references inside CHANGES_PENDING/VERSION_HISTORY are valid.** (Skip with `N/A` if the master plan file is absent.) For every `Plan §<N.M>` or `§<N.M>` reference inside CHANGES_PENDING.md + VERSION_HISTORY.md, confirm that section actually exists in the plan file.

```bash
grep -oE "Plan §[0-9]+(\.[0-9]+)?|§[0-9]+(\.[0-9]+)?" CHANGES_PENDING.md VERSION_HISTORY.md | sort -u
# Cross-check each against grep -nE "^## §|^### §" PLAN.md
```

**Check 6 — Cross-file sequence-number monotonicity.** (Skip with `N/A` if `CHANGES_PENDING.md` is absent.) The reference project numbered its shipped work items ("One-hundred-and-thirteenth project slice" etc.). Verify the sequence numbers in CHANGES_PENDING.md descend monotonically + verify the top entry's number matches the last shipped number on record (cross-check against the latest ship breadcrumb the orchestrator has).

```bash
grep -oE "[Oo]ne-hundred-and-[a-z\-]+ project slice|[a-z]+ project slice" CHANGES_PENDING.md | head -10
```

### Step 3 — Output Consistency Report

Structured markdown:

```markdown
## BRAIN CONSISTENCY REPORT — <timestamp>

### Check 1 — VERSION_HISTORY monotonicity: PASS / WARN / FAIL
[per-finding bullets]

### Check 2 — CHANGES_PENDING top entry body: PASS / WARN / FAIL
[per-finding bullets with line numbers]

### Check 3 — MEMORY.md index parity: PASS / WARN / FAIL
[orphans, broken entries listed]

### Check 4 — Memory dual-path parity (canonical vs secondary): PASS / WARN / INFO
[drift cases = WARN; present-at-one-path-only = INFO; see caveat in Check 4]

### Check 5 — PLAN section references: PASS / WARN / FAIL
[invalid references listed with citing file:line]

### Check 6 — Sequence-number monotonicity: PASS / WARN / FAIL
[non-monotonic sequences listed]

**Overall verdict:** N PASS / M WARN / K FAIL out of 6 checks.

**Auto-fixable (Tier 1):** [items the auditor can fix automatically with low risk — e.g., add missing MEMORY.md index entry for an orphan rule file]
**Owner-approval-needed (Tier 2):** [items requiring owner judgment — e.g., resolve a content drift between the canonical and secondary copies of a rule]
**Surface-only (Tier 3):** [items the auditor flags but doesn't propose fixes for — e.g., complex monotonicity decisions]
```

### Step 4 — Auto-fix Tier 1 (Optional, Owner-configurable)

If `auto_fix_tier_1` config enabled, auto-execute Tier 1 fixes:
- Add missing MEMORY.md index entries (append at logical position)
- cp single-direction dual-path drift between the canonical and secondary memory paths (sync toward whichever copy is newer — but only when the secondary path is confirmed to be a mirror, not a curated subset; otherwise leave for owner)
- Backfill obvious version-sequence stubs (only if the source-of-truth file is identifiable)

ALWAYS log auto-fixes to `<project-root>/audits/brain_audit_autofixes_<date>.md` for owner review.

### Step 5 — Tier 2 + Tier 3 Surface

Tier 2 + Tier 3 findings get surfaced to the owner as a structured questionnaire (the orchestrator's ask-the-owner pattern) when invoked interactively, OR as a markdown report when invoked from cron.

## Pitfalls

1. **Don't auto-fix content drift between the canonical and secondary memory paths.** Picking which version "wins" requires owner judgment (one path may have intentional newer content, and the secondary path may be a curated subset rather than a mirror). Surface as Tier 2.

2. **Don't flag intentional version skips.** Some version segments are skipped legitimately (on the reference setup, one version was originally NOT supposed to have a VERSION_HISTORY entry per the project's version-suffix memory rule; it was backfilled as a stub later). Cross-reference against the suffix rule before flagging.

3. **Don't trigger during active brain-file edits.** Race condition: if a brain file is mid-edit, the auditor might fire on an inconsistent intermediate state. Configure cooldown: wait 5 min after last brain-file mtime before firing.

4. **MEMORY.md is one-line per entry.** Per the project's brain-file size-governance rule, entries should fit on one line ≤200 chars. Auto-fix shouldn't add multi-line entries.

5. **Don't change the dual-path arrangement without owner direction.** Whether the secondary memory path is a full mirror or an intentional curated subset is an owner decision; don't unilaterally reconcile the two.

6. **Plan §X.Y references can be valid even if §X.Y not at literal line-start.** Some plan sections are nested or appear in revision-history tables. Check by grepping the full plan file content, not just headers.

## Verification

To test:

1. Make a deliberate brain-file inconsistency (e.g., add an orphan `<project-root>/memory/feedback_test_orphan.md` without a MEMORY.md entry).
2. Invoke this skill — interactively via your agent framework's skill invocation, or as a dispatch to a local agent.
3. Verify the auditor flags the orphan in Check 3.
4. If auto-fix enabled, verify MEMORY.md gains the entry; if disabled, verify Tier 1 surface only.
5. Clean up: `rm <project-root>/memory/feedback_test_orphan.md` + remove the MEMORY.md entry.

Manual fallback (no agent framework): a standalone `python3 tools/brain_audit.py` script triggered by an agent post-edit hook OR a git pre-commit hook (runs on staged brain-file edits).

## Related memory rules (reference setup)

- The brain-file size-governance rule — sister rule on brain-file size ceilings + structural discipline
- The memory-index-update-required rule — sister rule on MEMORY.md update requirement when adding rules
- The orchestrator-executes-after-authorization discipline applies if auto-fix is enabled (the orchestrator executes the physical fix only after owner pre-authorization)
- A historical rules batch added several critical rules at once; the auditor verifies batch-added rules are indexed at both memory paths

## Config knobs

```yaml
config:
  project_root:
    type: string
    default: "<project-root>"
  secondary_memory_path:
    type: string
    default: "<project-root>/memory-secondary"
    description: Secondary memory path checked for dual-path parity against the canonical memory/. May be a curated subset, not a full mirror (see Check 4).
  auto_fix_tier_1:
    type: bool
    default: false
    description: If true, automatically apply Tier 1 fixes (MEMORY.md index adds, single-direction dual-write sync). Always logs to the audits dir.
  brain_edit_cooldown_minutes:
    type: int
    default: 5
    description: Skip audit if any brain file modified within last N minutes (avoids race with active edits).
  strict_monotonicity:
    type: bool
    default: false
    description: If true, ANY version-sequence skip is FAIL (zero tolerance); if false, skip if accompanied by stub-entry pattern.
```
