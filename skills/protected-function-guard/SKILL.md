---
name: protected-function-guard
description: Use before merging any patch that touches a project's small set of protected core functions, their private helpers, or their call sites — or any validation/test script whose model/pipeline config might have drifted from the current reference. Flags edits that NAME a protected function, its private helpers, or its call sites — the scope a text search can actually establish — plus config mistakes (wrong sample rate, bin count, class count, or class layout); removed calls, re-ordered call sequences, and wrappers that never name the function are surfaced as review prompts, not detected classes. Returns a structured VERDICT (Approved or Concern or Do not merge) with file:line evidence per finding; the verdict is STATIC — never the ship verdict, which belongs to the test-and-verify step. This is a read-only guardrail run by a local coding model and spot-verified by the orchestrator; it never modifies code. Invoke when the user says "guard this patch", "protected function check", "did this patch touch any core paths", "approve merge", or pastes a patch summary or changed-files list.
---

# Protected Function Guard

A guardrail over a project's protected core — the small set of critical functions that must never be edited without explicit owner approval and a backup. Same protected list, same audit procedure, every time; invocable from an agent CLI or auto-triggered via a file watcher. The orchestrator spot-verifies the verdict.

This is a guardrail, NOT an implementer. It flags risk + outputs a verdict; it does not modify code.

The protected-function concept generalizes to any project: pick the handful of functions whose silent breakage would corrupt everything downstream (a core parser, an event detector, a converter), write the list down, and require owner approval + a pre-edit backup for any change to them. This skill enforces that list.

## When to Use

Activates whenever:

1. **A patch summary or changed-files list is pasted** by an implementer (a local coding model, a frontier leg) or a plan agent.
2. **A merge is proposed** that touches the main application module's core regions or any validation/guard script.
3. **The user explicitly invokes** the guard (`/protected-function-guard`, `/guard`, `/protected-check`).
4. **A plan claims to change core behavior** — even if no patch is yet drafted, run the guard against the claimed scope.
5. **A file watcher detects an edit to the core module** with a diff hunk overlapping protected function line ranges (event-trigger; optional config).

DO NOT activate for:

- Patches that only touch UI text / labels / tooltips / help docs (route those to a string/UI lint pass instead)
- Edits to notes/memory/bookkeeping files (route those to the appropriate housekeeping check)
- Work on a *different major version* of the codebase if that version carries its own protected list (see Pitfalls 6)

## Protected Functions (example list)

Every project defines its own list. A typical list for a data-processing app might be:

- `parse_core()`
- `detect_events()`
- `detect_tempo_and_offset()`
- `build_artifact()`
- `prune_results()`

Any edit to the functions on YOUR project's list requires explicit owner approval and a validation plan.

Also flag:

- Direct edits to the function bodies
- Edits to private helpers called exclusively by these functions
- Changed call sites that re-order or skip required steps
- Added post-processing that effectively changes their behavior
- Removed validation guards or assertions

Of these, only edits that NAME a protected function or helper are what the text-search procedure
below can DETECT. The wrapper, call-order, post-processing, and removed-call classes are real
risks a grep cannot establish — treat them as prompts for the reviewer reading the full diff (or
add AST-level arms); a clean grep does not clear them.

## Pipeline Config Guard

Flag any validation or detector script using stale assumptions about the model/pipeline it exercises. Typical drift axes (define the current reference values once, in a spec doc, and keep the doc and the code comment next to the model spec in sync):

- Sample rate — e.g. a script assuming the legacy `22050` when the current model path uses `44100`
- Feature-bin count — e.g. 128 mel bins (legacy) vs 90 (current)
- Output class count — e.g. 6 classes (legacy) vs 5 (current)
- Class layout/order — anything other than the current model's documented layout
- Stale reports already marked unreliable in the project's detection/model notes

Reference values have ONE canonical owner — the spec doc. The inline comment in the core module next to the model's input spec POINTS at that owner instead of restating the values: two surfaces that must update together eventually will not, and a pointer cannot drift.

## Procedure

### Step 1 — Identify Changed Files

If the user pasted a patch summary, extract the changed-files list. Otherwise run (quote the repo path if it contains spaces; use `git -C` to avoid a `cd`):

```bash
git -C "<project-root>" diff --name-only HEAD~1 HEAD
git -C "<project-root>" status --short
```

### Step 2 — Search for Protected Function Touches

For each changed source file, search for the protected function names:

```bash
for fn in parse_core detect_events detect_tempo_and_offset build_artifact prune_results; do
  echo "--- $fn ---"
  grep -n "def $fn\b\|$fn(" <changed_file>
done
```

(Substitute your project's actual protected list for the example names.)

### Step 3 — Search for Config Red Flags

```bash
grep -n "22050\|128 mel\|6 classes\|sample_rate" <changed_files>
```

Compare any hits against the current reference values (e.g. 44100 / 90 / 5 in the example above).

### Step 4 — Compare Against Approved Plan

If the user pasted a plan summary or a `PLAN_*.md` exists:

- Verify each protected-function touch is in the plan's scope.
- Verify the owner approval marker is present (`OWNER-APPROVED` or `OWNER-LOCKED` near the protected-function section).
- Flag any touch NOT in the plan as a "drift" finding.

### Step 5 — Output Verdict

Use EXACTLY ONE of:

- `VERDICT: Approved` — no protected surfaces touched and no config risks found by this STATIC
  pass. Approved is never the ship verdict: the test suite and runtime verification run in their
  own step (see `skills/multi-agent-code-workflow`), and a merge needs both.
- `VERDICT: Concern` — something needs owner/driver attention but may be acceptable.
- `VERDICT: Do not merge` — protected function changed without approval, OR validation is measuring the wrong pipeline.

### Output Format

```markdown
## PROTECTED FUNCTION GUARD REPORT — (<timestamp>)

VERDICT: Approved | Concern | Do not merge

**PROTECTED SURFACES:**
- file:line — finding (e.g., `app/core.py:1247 — detect_events body modified, hop param changed from 441 to 512`)
- (or "none" if clean)

**PIPELINE CONFIG:**
- file:line — finding (e.g., `tools/audio_smoke.py:42 — sample_rate=22050 used, but the current model expects 44100`)
- (or "none" if clean)

**APPROVALS REQUIRED:**
- none — OR — exact approval needed (e.g., "owner must sign off on hop=512 change since this affects ALL existing conversions")

**NOTES:**
- concise explanation of the verdict reasoning

**Recommendation:** [MERGE / REQUEST OWNER REVIEW / REJECT MERGE]
```

## Pitfalls

1. **Wrapper edits count.** If a wrapper around `detect_events()` adds a pre-filter or post-filter that materially changes the output, that's a protected-function touch even though the `def detect_events` line wasn't edited. (Learned from real patches that "never touched the protected function" yet changed its results.)

2. **Call-site re-ordering counts.** If a caller skips the required `detect_events → detect_tempo_and_offset` order or inserts an intermediate step, that's a behavior change.

3. **Config drift in test scripts.** Tests that USE the wrong config (legacy sample rate / bin count / class count) produce false PASS/FAIL signals. Flag these even if the production path is correct — bad tests waste owner time chasing ghosts.

4. **Plan markers matter.** `OWNER-LOCKED` plans cannot be modified mid-flight. `OWNER-APPROVED` plans can be amended with owner consent. Untouched (no marker) plans need owner sign-off before merge.

5. **Don't approve "trivial" protected-function changes.** Even a one-line "fix" to `build_artifact()` is a protected-function touch. The rule is bright-line: any edit → owner approval, no exceptions for "obvious" changes.

6. **Different major versions may carry different protected lists.** If the project has a rewrite-in-progress with its own enforced rules, defer audits of that tree to those checks; this guard's list applies only to the version it was defined for.

7. **Don't run the test suite.** The guard is read-only static analysis. Runtime verification belongs to a separate ship-verification step or an owner-run smoke test — which is also why this guard's `Approved` can never stand in for that step's verdict.

## Verification

To test this skill works:

1. Run the guard against a pasted patch summary that touches a protected function (e.g. `detect_events()`).
2. Verify it returns `VERDICT: Do not merge` if no owner approval marker is present.
3. Verify it correctly identifies the file:line of the protected-function touch.
4. Verify it suggests the exact approval text needed.
5. Run against a benign patch (e.g., a README typo fix) → verify `VERDICT: Approved` and "none" in both PROTECTED SURFACES and PIPELINE CONFIG sections.

Manual fallback (no agent CLI): the orchestrator runs the same Procedure by hand directly against the repo. Both surfaces should produce equivalent output.

## Companion discipline

This guard works best alongside two standing rules in the project's persistent notes: a backup-before-edit discipline (never edit a protected function without a timestamped backup copy) and an owner-approval workflow (who approves, what marker they leave, how amendments work).

## Config knobs (agent-side)

```yaml
config:
  project_root:
    type: string
    default: "."
    description: Project root containing the protected core module
  strict_mode:
    type: bool
    default: true
    description: If true, ANY protected-function touch returns "Do not merge" without an owner-approval marker
  check_pipeline_config:
    type: bool
    default: true
    description: Run the config red-flag sweep on validation/test scripts
  defer_other_versions:
    type: bool
    default: true
    description: If true, skip trees governed by a different major version's own protected list
```
