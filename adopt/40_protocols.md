# 40 — Stand up the operating protocols

This repository exports three complementary patterns: a driver lock for concurrent writers, a human-gated curation loop, and a verified system map. Start with local files and a manual human gate. Do not turn any pattern into a cron job, service, or shell hook without a reviewed plan and diff.

## Step 1 — establish a driver lock

Before an agent edits a shared file, it reads a repository-root `.driver_lock`. With no lock, it writes one identifying the agent, UTC session start, task, claimed files, scope, and notes. Use `whole_file` when isolation is uncertain; use `regions` only after the strict isolation test. Before each write, check for a halt sentinel, re-read the lock, and re-hash claimed regions when applicable. Release or hand off the lock when the editing session ends.

**ADOPTER COMMAND:**

```bash
test ! -e .driver_lock && test ! -e .driver_halt && printf 'lock-path-clear\n'
```

**VERIFY — expected output:**

```text
lock-path-clear
```

If either file exists, stop and inspect it; do not overwrite a live or stale claim without the designated project authority's approval.

## Step 2 — create a curation proposal lane

Use a local, version-controlled rule directory for the active rule base and a separate proposal area. The scratch directories below are a planning scaffold; after the human selects the active rule directory, put approved rule changes under that directory's version control. A watcher may write a visible trigger, but it must never apply a change. An audit agent proposes exact edits with dedup evidence; a human approves, revises, or rejects; an applier performs only one exact-match replacement; an independent reviewer compares intent with the resulting diff; then commit the accepted change and rebaseline the watcher.

**ADOPTER COMMAND:**

```bash
mkdir -p adopt-scratch/curation/{triggers,proposals,rejects}
find adopt-scratch/curation -maxdepth 1 -type d -print | sort
```

**VERIFY — expected output:** the three named subdirectories. This is a local scaffold only; it installs no watcher and performs no edits to the active rule base.

## Step 3 — start a verified system map

Document only command output actually observed on the adopter's host. Each useful map claim gets a probe that could refute it and a dated verification stamp. Mark an unprobeable decision `UNVERIFIABLE-BY-PROBE`; do not label it verified. When infrastructure changes, update the claim and its probe in the same reviewed change.

**ADOPTER COMMAND:**

```bash
mkdir -p adopt-scratch/system-map
printf '# Host observations\n\nUNVERIFIABLE-BY-PROBE: no claims entered yet.\n' > adopt-scratch/system-map/00-host.md
test -s adopt-scratch/system-map/00-host.md && printf 'map-seed-present\n'
```

**VERIFY — expected output:**

```text
map-seed-present
```

## Step 4 — human gate automation

Before proposing a scheduler, service definition, or shell hook, show the human: the inventory, exact files to create or change, command line, rollback path, and diff. Only after explicit approval may an agent apply that specific change; re-run the relevant probe afterward.

**VERIFY — expected outcome:** `MANUAL: approval and the post-change probe must be recorded by the adopter; there is no safe generic command that can prove human approval.`
