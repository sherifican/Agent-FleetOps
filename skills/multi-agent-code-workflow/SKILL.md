---
name: multi-agent-code-workflow
description: Coordinate a guarded, independently reviewed, and functionally verified code change.
license: MIT
---
# Multi-Agent Code Workflow

## Overview
Use this workflow for a code change that will be shipped. It coordinates scope control, implementation, independent review, a real functional check, and evidence-backed reporting. It is deliberately a workflow, not a substitute for project-specific guardrails.

## Procedure
1. Read the repository instructions and identify protected areas, required approvals, backup rules, and any single-writer/driver-lock rule.
2. Scope the change and record the intended verification command before editing. If a protected area needs work, obtain the required approval and make the required backup first.
3. Generate and review the change with separate roles; use the generate-review-fix loop for that portion.
4. Run the task's actual test, build, or reproduction against the reviewed result. A review is not a functional check.
5. Audit every shipping claim against the live files and tool output. Report unrun checks, warnings, and limitations explicitly.

## Rules
- Keep one active writer for a source area unless the repository expressly supports concurrent edits.
- The generator must not be the only reviewer of its own change.
- Do not infer a pass from a plausible diff. Preserve the command output as evidence.
- Use a narrow change; do not add unrelated refactors while resolving a scoped request.

## Pitfalls
- Skipping the functional test because a reviewer approved the patch.
- Editing a protected function without its approval and backup procedure.
- Reporting a test result that was not run against the final revision.

## Verification checklist
- [ ] Project safeguards and source ownership were checked.
- [ ] The final revision received independent review.
- [ ] A relevant functional check was run and its output retained.
- [ ] The report distinguishes verified facts from unresolved gaps.
