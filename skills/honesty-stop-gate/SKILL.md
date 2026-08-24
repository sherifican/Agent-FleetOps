---
name: honesty-stop-gate
description: Adapt the honesty stop gate (guard/honesty_stop_gate.py) to THIS user's stack — a Stop hook that blocks a turn asserting live state it never measured. Invoke when a user says "add the honesty gate", "wire up the stop hook", "adapt the honesty stop gate", or after copying guard/ into their project. The procedure forces the adopting AI to VERIFY every command it wires actually exists on the user's system (via --check-config), ASK about anything it cannot determine, keep private files and environments untouched, and prove the gate can fail end-to-end on the user's own box before calling it done.
license: MIT
---

# Honesty Stop Gate — adaptation skill

You are wiring `guard/honesty_stop_gate.py` into the user's agent so it blocks the agent from ending a turn on a live-state claim it never measured. The mechanism is fixed and correct; your whole job is the three config parameters — and the danger in that job is **building a stair to nowhere**: a check that reads as coverage and verifies nothing, because it points at a command the user's system does not have, or one that cannot fail. Read [`specs/honesty-stop-gate.md`](../../specs/honesty-stop-gate.md) first — including *"What it enforces, precisely"* (the gate confirms a probe RAN and named the subject; it does not read the probe's output). Do not reimplement the mechanism.

## The contract you operate under

1. **Verify everything you can.** Anything checkable about the user's system, you check — before you write it into config, not after. A command goes in `verification_commands` only after `--check-config` confirms its binary resolves on this box.
2. **Ask about anything you cannot.** When you cannot determine how the user names their jobs, which command observes a service's state, or whether the user's agent harness even fires Stop hooks, ASK — one concrete question with your best guess offered. Do not invent an answer.
3. **Keep private things private.** Do not read secrets, credential files, `.env` files, tokens, or private data to infer configuration. **This includes the user's conversation transcripts, logs, and history** — they are a tempting source for "how does this agent phrase claims" and they routinely contain private data. You need command names and subject vocabulary, not file *contents*; get those by asking the user for representative phrasings, or from non-secret places (process lists, service names, public scripts). If configuring correctly seems to require reading a private file or a past transcript, that is a signal to ASK, not to read.
4. **No stair to nowhere.** Every `verification_commands` entry must be a real, existing command that can fail. An unconditional `echo`, a bare `&`, a file-existence read (`ls`, `stat`) or a log read (`cat`/`tail` a `.log`) does not observe liveness and verifies nothing. `--check-config` enforces the existence half; you enforce the observes-liveness half by only listing process/service probes for running-claims.

## Procedure

### 1. Read the mechanism, then map the three parameters
Open `guard/honesty_gate.config.example.json`. You are producing a `honesty_gate.config.json` (same directory, or pointed to by `$HONESTY_GATE_CONFIG`) that overrides only `claim_patterns`, `verification_commands`, `subjects`, and optionally `non_subjects` / `verify_hint`. Leave `completion_pattern` alone unless the user's language for "finished" genuinely differs — and keep its linking-verb + clause-final requirement, or a bare "Done," will fire.

### 2. Discover the user's SUBJECTS (what live things do they claim state about?)
Ask, or infer from what is safely visible: what does this agent launch and then report on? Background jobs, CI runs, deploys, services, containers, other agents/legs, long builds. These become `subjects`. Prefer distinct single tokens (a service name) over generic compounds — "build" and "job" as separate subjects make "build job" demand both be verified. If the user has a naming convention (e.g. logs named `<subject>_run.log`), note it: the gate resolves a subject from a `*.log`/`*.json`/`*.txt` filename.

**If this agent launches nothing in the background** — it works synchronously, its output is always in the transcript — say so and do NOT ship a running-claim gate: there are no subjects to guard, and an empty `subjects` list is a degenerate config. Either scope the config to completion-type claims only, or tell the user the gate has nothing to watch on this stack. Do not manufacture subjects to have something to configure.

### 3. Discover and VERIFY the user's verification commands — the load-bearing step
For each way the user's agent could observe live state, find the actual command. Then **prove it exists on this box with `--check-config`, not by eye.**

- Is there a process supervisor? `systemctl status` / `systemctl is-active` / `supervisorctl status` / `pm2 list`.
- Containers/orchestration? `docker ps` / `kubectl get`.
- Process table? `pgrep` / `ps`.
- A job runner, task queue, or the agent harness's own status command? The exact binary/subcommand, and it must *observe* state (be able to return "not running"), not always succeed.
- Health endpoints? `curl .../health` — only if the endpoint is real.

Write your candidate list into `honesty_gate.config.json`, then run:

```
HONESTY_GATE_CONFIG=guard/honesty_gate.config.json python3 guard/honesty_stop_gate.py --check-config
```

It fails on any command whose binary does not resolve on this box (a stair to nowhere) and on any empty load-bearing list. **A candidate it flags is dropped, and you tell the user it was dropped and why** — silent omission reads as "covered everything." If, after dropping unresolved commands, `verification_commands` would be empty, STOP and tell the user: the gate cannot function without at least one real probe — surface the gap rather than shipping a gate that can never verify. Do not proceed past a red `--check-config`.

### 4. Write the CLAIMS' regexes conservatively
`claim_patterns` should cover how *this* agent phrases live-state claims (keep both running-type and completion-type families from the example). When unsure whether a phrase is a claim, leave it in — a false block is recoverable (run the check, delete, or label); a missed claim is the silent failure the gate exists to prevent. But do not add a pattern so broad it matches ordinary prose every turn — an always-firing gate gets disabled, which is the same as no gate.

### 5. Confirm the harness will actually run the hook
The hook's contract is Claude-Code-shaped: the harness must (a) invoke `Stop` hooks, (b) pass the turn's `transcript_path` on stdin, and (c) honor a `{"decision":"block"}` response. If any of those is untrue, the hook silently no-ops and the install *reads as done while the gate never fires*. Confirm the user's harness supports all three, or ASK. For Claude Code: add one `Stop` hook entry pointing at `guard/honesty_stop_gate.py`. **Show the exact settings diff and back up the settings file before writing**, and add only that one entry — do not modify settings you were not asked to.

### 6. Prove it can fail END-TO-END, on this box, with the user's own config
Two acceptance checks, both required:

1. `--check-config` is green (step 3) and `HONESTY_GATE_CONFIG=… --self-test` prints `SELF-TEST PASS` (the mechanism is intact under this config).
2. **A real trigger with a real subject.** The `--self-test` uses generic strings and only exercises `scan_turn` directly — it does *not* prove the harness fires the hook or that your subjects/commands work. So drive the hook the way the harness does: build a tiny transcript file naming one of the *user's* subjects with an unbacked claim, and feed it in —

   ```
   printf '{"type":"assistant","message":{"content":[{"type":"text","text":"The <their-subject> is still running."}]}}\n' > /tmp/ht.jsonl
   echo '{"transcript_path":"/tmp/ht.jsonl"}' | HONESTY_GATE_CONFIG=guard/honesty_gate.config.json python3 guard/honesty_stop_gate.py
   ```

   That must emit a `{"decision":"block", …}`. Then add a line running one of the user's *real* verification commands naming that subject (a `tool_use` + `tool_result` pair) before the claim, and confirm it goes silent. A gate you have not watched block a real unbacked claim and pass a real backed one on this machine is not yet trusted — this is the same law the rest of the guard ladder runs on ([rigor-spectrum](../../specs/rigor-spectrum.md)): no guard without a proof it can fail.

### 7. Report what you wired and what you skipped
Tell the user, in plain terms: the subjects you configured, the verification commands `--check-config` confirmed exist (and any you dropped and why), the harness-support facts you confirmed or the questions you still need answered, and the self-test / end-to-end teeth result. If you asked questions in steps 2–5 that are unanswered, the config is provisional — say so; do not present it as done.

## Acceptance
Done means: config written from verified facts (not guesses, not scraped from private history), `--check-config` green with every verification command's binary confirmed present, no private file or transcript read to produce it, the harness confirmed to fire Stop hooks with `transcript_path` and honor `decision:block`, the settings diff shown and backed up, `--self-test` green, and the gate observed to block a real unbacked claim and pass a real backed one **end-to-end** on the user's own machine. Anything you could not determine is surfaced as a question, not filled in silently. If the agent launches nothing in the background, the honest deliverable is saying so — not a degenerate config.
