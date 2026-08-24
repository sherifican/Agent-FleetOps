# 20 — Adopt the skills library

The `skills/` directory is a library of agent procedures. Do not assume a destination directory or skill-loader format: inspect the adopter's agent platform first, then copy only the selected skill directories and their referenced files as a unit.

## Step 1 — inventory the portable library

**ADOPTER COMMAND:**

```bash
find skills -mindepth 2 -maxdepth 2 -name SKILL.md -print | sort
rg -n '(^name:|^description:|scripts/|https?://|~/|/home/|C:\\)' skills/*/SKILL.md
```

**VERIFY — expected output:** a list of skill manifests and every portability-sensitive reference. The second command may print no path references; that is evidence only for the current manifest text, not for files it links to.

## Step 2 — classify before copying

Start with these host-agnostic procedure patterns: `eval-integrity`, `generate-review-fix-loop`, `model-routing-table`, `local-model-onboarding`, `agent-memory-ops`, and `shared-brain-bookkeeping`. `generate-review-fix-loop` supplies the smallest independent draft/review/repair pattern when a second vendor is unavailable. Their policy text transfers, but any linked scripts or repository paths must travel with the selected skill and be rechecked after installation.

Treat these as requiring host adaptation before activation: skills that name a runtime, endpoint, model roster, project-root path, executable, or a target agent's native skill directory. Replace those values only from `adopt-scratch/inventory.md` and the human-approved plan; never copy a reference host topology into the new host.

**ADOPTER COMMAND:**

```bash
test -s adopt-scratch/inventory.md && find skills -mindepth 2 -maxdepth 2 -name SKILL.md | wc -l
```

**VERIFY — expected output:** a nonzero manifest count after `inventory.md` is confirmed present.

## Step 3 — create a living routing table, not a remembered roster

Create the adopter's routing table in its durable agent-rule location. For each task class, record: capability, primary candidate, fallback, serving path, context/step limit, evidence command or evaluation artifact, date, and known failure mode. Record a negative result rather than silently retrying it later. Prefer the cheapest capable local model; use cloud work as an explicit escalation for hard or long work.

Before copying a routing table, fill the [budget-tier row](../README.md#why-the-routing-table-looks-like-that) that matches `adopt-scratch/inventory.md`.

**ADOPTER COMMAND:**

```bash
mkdir -p adopt-scratch
test -f skills/model-routing-table/SKILL.md && printf 'routing-method-source-present\n'
```

**VERIFY — expected output:**

```text
routing-method-source-present
```

## Step 4 — human-gate installation

Present the selected skill list, each destination path, any referenced scripts, and the diff to the human before copying files into the agent's active skill directory. Do not install shell hooks or alter a global agent configuration as a side effect of a skill copy.

**VERIFY — expected outcome:** `MANUAL: human approval is required before installation; after approval, inspect the destination manifests and their linked files.`
