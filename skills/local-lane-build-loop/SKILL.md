---
name: local-lane-build-loop
description: Read BEFORE building features by dispatching a local coding model instead of writing the code yourself. Gives the build loop that makes a local lane safe and cheap — orchestrator-authored spec + test gate (never the implementer's own tests), bounded implementation by a local coding model, an audit-model pass treated as a surfacer-not-gate, and orchestrator adjudication + framework wiring — plus the test-hermeticity gotchas that bite any suite built this way.
---

# local-lane-build-loop — features via a test-gated local coding lane

A local coding model can build real features cheaply, but only inside a structure that makes its output
verifiable and its failure modes survivable. The loop below is the structure. The failure stories are the
evidence; keep them attached to the rules they paid for.

## The architecture that makes it locally-buildable
Split the target app along a strict one-way seam so a local model can own the data/logic layer in bounded
tasks without fighting the framework's event loop or runtime:

1. **Pure headless modules.** Read inputs (files, shell-outs, APIs) → return records. **Zero framework
   imports.** Unit-testable against fixtures with no live system. The pattern that works: small `read_*()`
   readers each wrapped in `try/except` returning a **distinguishable safe default** (never raise — and never collapse
   cannot-read into looks-empty: the returned record carries a status, `ok` / `empty` /
   `missing` / `permission` / `parse-error`, so a consumer can tell a VALID empty observation
   from a source it could not read; reference `guard/reader_record.py`, gated by
   `guard/tests/test_reader_record.py`) + a **pure `build_*()`
   composer** (no I/O) + a `status()` convenience that calls the readers → the composer.
2. **Dumb pure formatters.** Records → display strings. No I/O, no state.
3. **The framework app layer.** Layout, timers, bindings, modals, the paint loop. This is the part the
   local lane does NOT own — see the division of labor below.
4. **Frozen dataclass contracts.** Shared record types both sides compile against. Change a field → update
   every module that uses it.

Layers 1–2 are the local lane's territory; layer 3 is the orchestrator's; layer 4 is the contract between
them.

## The BUILD LOOP
1. **The orchestrator writes a tight spec + the test gate.** The test is **orchestrator-authored — never the
   local lane's own** — so it can't be gamed. This is the anti-cheatable-test rule: an implementer writing
   its own acceptance test will (even unintentionally) write the test its implementation passes.
2. **A local coding model writes the source** — dispatched through an AI-pair-programming edit CLI (e.g.
   aider), from inside the repo, handed the repo's agent docs + coding discipline + the spec + the test as
   read-only context, with an edit instruction of the form *"write the file COMPLETELY"*.
   ⚠ **Never put a fenced code block (triple-backtick) inside a file you pass as read-only spec/test
   context to an edit CLI.** Aider's fence detection sees the pre-existing backticks and silently bumps its
   expected fence to 4 backticks, so the model's normal 3-backtick output matches ZERO blocks → **it applies
   nothing, exits 0, prints no error.** Put INDENTED pseudocode in spec context files instead. (This cost
   two silent no-op dispatches on the reference setup before it was root-caused — the dangerous kind of
   failure, because every signal says "success".)
3. **Deterministic tests are the real gate.** The suite runs; green is the only acceptance signal from the
   lane. No "looks right" merges.
4. **A second local model's AUDIT is a SURFACER, not a gate.** On the reference setup the audit model
   **over-flags and sometimes confabulates specifics** (e.g. reporting "dead try/except in status()" when
   there is none). Use its findings as a list of places to look; **adjudicate every single finding against
   the actual code** before acting. Never let the audit model block or approve a merge directly.
5. **The orchestrator verifies end-to-end + does the thin framework wiring** (the app-layer glue is harder
   for the local lane than the pure logic).
   **Division that works: local lane = pure logic/formatter modules; orchestrator = spec + test gate +
   wiring + adjudication.** Long high-stakes SYNTHESIS docs (like this skill) are also orchestrator-authored:
   the lane truncates/confabulates on those; its sweet spot is bounded, test-gated code.
   **★ HARD RULE (an owner correction that stuck): the local coding lane — or a frontier leg — MUST write
   the source modules; the orchestrator does ONLY the spec, the test gate, the dispatch, the adjudication,
   and the framework wiring. Do NOT hand-write source logic yourself, even under rapid back-to-back feature
   requests.** The failure mode is momentum: the orchestrator starts writing the source modules directly "to
   be fast". Dispatching IS the job; hand-coding the source is the miss. The owner's words: "have the coding
   legs do the work, you just dispatch them."
6. **For a SMALL bounded piece, orchestrate the edit CLI DIRECTLY (the orchestrator runs the loop)** —
   don't wrap it in a background subagent. The wrapper adds failure surface (slow starts, transient API
   deaths mid-run) for little gain on a quick module. Reserve subagents for genuinely parallel or larger
   work.

## Gotchas that generalize (each bit a real suite)
- **A test that mounts the real app leaks cosmetic GLOBALS.** App startup applies user/cosmetic settings to
  module-global state (a color palette, active animation frames). If such a test sorts BEFORE a test that
  asserts the DEFAULT palette, it breaks it — order-dependent, invisible until a new test file lands in the
  wrong alphabetical slot. **Fix: an autouse fixture (e.g. in `conftest.py`) that snapshots + restores those
  globals around every test.** It makes the whole suite order-independent.
- **A module-level time-cache poisons monkeypatched tests (order-dependent flake).** A TTL cache (e.g. a
  15s resource-reading cache) populated by an integration test's LIVE call makes a LATER
  monkeypatched-to-throw test read the cached REAL value instead of exercising the stub → passes alone,
  fails in full-suite order. **Clear the cache at the top of any cache-BYPASSING test.**
- **A global `subprocess.Popen` monkeypatch also captures the app's OWN background subprocesses** (its
  refresh loop shells out during test pauses). Don't assert `launched[0]` — **FILTER the captured calls for
  your argv** (`[a for a in launched if a[:2] == ["my", "command"]]`), or the test flakes by race.
- **A click test whose OUTCOME depends on LIVE system state is non-hermetic — monkeypatch the function that
  decides the branch.** Mounting the real app runs real startup → real state reads → the UI reflects the
  ACTUAL machine (busy workers, running jobs). A test that branches on that state passes or fails depending
  on what happens to be running. (On the reference setup a live worker was busy, so a body-click opened the
  in-flight modal and broke the old "→ inventory" assertion.) Fix: stub the branch-deciding source so the
  test controls the branch.
- **Display-only cleaners touch the RENDERED string only — never mutate the real value in the source.**
  Pretty-printers (strip a long suffix for display, humanize an interval) exist for the screen; names,
  APIs, and matching logic depend on the real value. A cleaner that mutates upstream data turns a cosmetic
  tweak into a behavioral bug.
- **Click AND keyboard-nav handlers must index the SAME filtered/visible list the UI RENDERS — never the
  raw unfiltered data.** When a filter or search is active the rendered rows are a subset; mapping a
  click-row or a selection index back into the unfiltered data selects the WRONG row. Compute the selection
  against the exact list you painted (a shared "visible items" helper used by both the renderer and the
  handlers).
- **Fixed-width columns + markup:** compute padding on the PLAIN (markup-stripped) length, else invisible
  `[tags]` break column alignment — and a too-long cell that gets hard-truncated can silently drop its color
  markup and read as plain gray. Shorten the display string to fit; never assume styling survived.

## Local coder EDIT protocol (how the lane touches files)

- **Dense existing blocks: small byte-exact hunks.** Ask for the smallest search/replace-style hunk
  with its context stated exactly; an edit that does not match exactly once is refused, never
  fuzzy-applied.
- **A NEW file: one whole-file block.** Generation is safe where there is nothing to clobber; the
  whole-file form is the CREATE path, not the edit path.
- **Escalate after N artifact-verified failures.** When the same edit has failed N times — failures
  verified against the artifact, not taken from the lane's own report — hand the task to a
  tool-using builder instead of re-prompting. N is a PARAMETER measured on your own coder (the
  reference setup measured 3); re-measure it when the lane's model changes.
- **One coder per repository.** Two lanes editing one tree collide silently; the shipped default is
  a dirty-tree refusal at edit-pass start — see `specs/driver-lock-protocol.md` (shipped default)
  and `guard/one_writer_gate.py`.

## Working agreements that kept the lane honest
- **Never leave the suite red.** Every change stays green; a visible change gets verified by rendering, not
  just by tests. See [[eval-integrity]] for the audit discipline the gate itself deserves.
- **Update the changelog every shipped wave** — newest-first, one block per wave with `[feat]`/`[fix]`/
  `[qol]` bullets naming what shipped (and the failure a fix closes).
- **Bump a visible VERSION marker per shipped wave** if the app shows one. On the reference setup the marker
  silently drifted behind two shipped waves, so a relaunch couldn't visually confirm fresh code was running
  — the version marker is the TRIGGER to verify, never the proof: a marker is a claim the
  artifact makes about itself, exactly as stale as the artifact. On a bump, verify per
  [[verify-running-build]] (marker-diff the deployed files, prove the process reloaded, bind
  both to the serving PID). Bump it in the same commit as the wave so the trigger fires.
