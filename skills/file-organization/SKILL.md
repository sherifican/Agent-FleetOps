---
name: file-organization
description: Use WHENEVER creating, saving, or generating a new file (task output, research artifact, report, chart, log, dataset, brief, dispatch) inside a shared agent/human workspace. Decides the correct sub-folder + naming so files are organized for fast AI grep AND human browsing — never dumped loose at a directory root. Also governs how tooling must glob so the structure can deepen without breaking.
---

# File Organization — where every new file goes

Loose files at a directory root are forbidden. Every file you create lands in a typed sub-folder with a clear name. The structure serves **two consumers at once**:
- **AI grep:** PREDICTABLE locations (a `FINAL_*` report is always under `research/finals/`) → an agent greps the right place first try.
- **Human browse:** clear names, shallow trees, related things together → a person finds a thread by eye.

If both pull in different directions, keep the location predictable (AI) and the *name* descriptive (human). Never sacrifice predictability for cleverness.

## Decision procedure (before writing any file)
1. **Read the map first.** Check the nearest `AGENTS.md` (or equivalent context-map doc) and any `INDEX.md` for the directory's structure. Place into an existing folder if one fits.
2. **Classify by pipeline stage / type**, then drop it in the matching folder (example scheme below).
3. **Name it** descriptively: `STAGE_<slug>_<who-or-model>_<YYYY-MM-DD>.ext` where it helps grep (e.g. `RESULT_quant-test_<model>_2026-06-28.md`). Keep the stage prefix — it's critical for routing.
4. **If no folder fits**, create a new typed sub-folder (don't invent a one-off at root) and **update the map** (`AGENTS.md` + `INDEX.md`) so the new category is discoverable. Updating the map is part of creating the category, not optional.
5. **Heavy/cruft/superseded** (multi-MB logs, big zips, `*.bak`, raw machine dumps) → off the live tree to an archive location outside it, never left at a root.

## Example routing scheme (stage-based — adapt to your pipeline)
This is one working scheme for a research-workspace tree; treat it as a template, not a mandate.

| New file | Folder |
|---|---|
| `RESULT_*` / `FIX_*` (dispatch results) | `research/results/` |
| outgoing dispatch briefs (`BRIEF_*`, `DISPATCH_*`, per-model dispatches) | `research/dispatches/` |
| `RECONCILED_*` | `research/reconciled/` |
| `FINAL_*` (finalized reports) | `research/finals/` |
| `AUDIT_*` | `research/audits/` |
| `VERIFY_*` | `research/verify/` |
| `transcript*` | `research/transcripts/` |
| `SWEEP_*` (experiment sweeps) | `research/sweeps/` |
| specs/plans/notes `.md`/`.txt` (no stage prefix) | `research/notes/` |
| `*.html` / `*.svg` (visual reports, cards) | `reports/` |
| `*.json` data | `data/` |
| `*.log` run logs | `logs/` |
| **STAY AT ROOT (live anchors — never move):** `AGENTS.md`, `INDEX.md`, and any live ledger/digest files that other tooling addresses by fixed path | (root) |
| **KEEP AS-IS:** already-organized working dirs and repos | (don't reshuffle) |

A second tree used for human↔agent coordination can route by *kind* instead of stage — e.g. `coordination/` for handoffs/instructions/requests, `research-briefs/` for briefs, `setup-docs/` for setup documentation — with the same rule that loose files at the root are forbidden and designated anchor files stay.

## Exception classes — the ONLY files allowed loose at a root

The no-loose-files rule has exactly three exception classes, and each must stay checkable:

1. **Tool-required anchors** — files a tool, contract, or ecosystem addresses at a fixed root path
   (`pyproject.toml`, `package.json`, `LICENSE`, `README.md`, a contract doc other tooling reads by
   fixed path, VCS dotfiles).
2. **Runnable entry points named in the README** — a root script is legitimate only while the
   README names it; discoverability is the licence, and a root entry point the README never
   mentions is a stray whatever it does.
3. **Directories** — their internal organization is governed by the rules above, not the root rule.

Everything else at a root is a stray. Arm: `guard/org_lint.py` goes red on a stray root file
(gate: `guard/tests/test_org_lint.py`); run it in CI so an exception class cannot widen silently.

## TOOLING RULE (so the structure can deepen without breaking)
Any script that READS artifacts by pattern must use a **recursive glob** — `glob.glob(f"{BASE}/**/FINAL_*.md", recursive=True)`, not `{BASE}/FINAL_*.md`. This is what lets files live in sub-folders (or deeper, later) without breaking readers. (Learned during a reorg: several reader scripts had to be patched exactly this way.) When you MOVE files, run a path-chain audit: grep tooling for hardcoded old paths; fix any specific-file ref; verify generated HTML asset links still resolve.

## Propagation
Agents that can't auto-retrieve skills (e.g. small local models dispatched as sub-tasks) need the relevant routing rows PREPENDED into their task brief when the task produces files.
