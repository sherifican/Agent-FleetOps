---
name: verify-running-build
description: Check deployed files, process restart, and the serving PID's path and file-ordering evidence before asserting that a fix is live. For interpreted programs this does not prove which source bytes were loaded; it rejects a file modified after process start as CANNOT-PROVE.
license: MIT
---

# Verify the Running Build — the tree is not the deployment

Reviewing the source tree tells you what the code *should* do. It cannot tell you what is *running*. Those
two diverge constantly and silently, and the gap is invisible to every ordinary check: the repository is
clean, the tests pass, the commit is on the branch, and the wrong code is serving traffic.

In the authors' setup a service served a build **six revisions stale for over twelve hours** while the team
built, reviewed, and reasoned about revisions that were not deployed. Nothing was broken. Every status
surface was green. It was found only by diffing the deployed files directly.

> **Framing note:** the incidents and numbers here are from the authors' own deployment; they are evidence
> for the rules, not universal figures. The verification method is the transferable part.

## Why a version string cannot do this job

A version constant, a `--version` flag, a build label in a health endpoint — all of them are **claims made
by the artifact about itself**. They are exactly as stale as the artifact. A build serving old code reports
the old version confidently, or worse, reports a new version because the label was bumped in a file that
did deploy while the logic that mattered did not.

The rule: **never accept a self-reported identity as proof of deployed content.** Diff the content.

## The three checks, and why you need all of them

Each is blind to the failures the others catch. Running only some of them is the common mistake.

### Check 1 — marker-diff the deployed FILES

Pick markers that exist in the new build and **do not exist in the old one**, then count them in the
deployed files directly (not in your tree, not in the artifact you uploaded — the files on the target).

The critical property is **discrimination**. A marker present in both builds proves nothing. Before
trusting the gate, print the counts side by side and confirm each marker separates them:

```
  marker_a       tree=5  /3    deployed=0  /0    DISCRIMINATES
  marker_b       tree=3  /1    deployed=0  /0    DISCRIMINATES
  marker_c       tree=9  /5    deployed=9  /5    <- CANNOT DISCRIMINATE, drop it
```

**This is where a deploy gate rots.** A gate written for one release keeps passing for every later release
while checking only the markers of that first one. It is green because it is asking a question whose answer
stopped changing — a guard that cannot fail. Re-derive the markers for each batch you deploy.

**Beware the runtime-surface trap:** it is tempting to gate on "the health endpoint now contains field X".
Verify that the *old* build does not already return field X. In the authors' setup every field in a new
build's health payload was already present in the old one, so the intended gate could not have failed for
that release. The health surface simply did not expose anything new.

### Check 2 — prove the PROCESS reloaded

Marker-diffing the files is necessary and **not sufficient**. Files on disk can be entirely correct while
the old code keeps serving from memory, because the restart silently failed, restarted a different unit,
or the supervisor kept the old process alive. This is precisely how a stale build survives a deploy that
reports success at every step.

Check a monotonic runtime fact that must have reset — process uptime is the simplest:

```
  5a liveness    OK   — service responding
  5b restarted   OK   — uptime=8s (the observed process start reset)
```

Prove this one red before you trust it: run it against the live process *before* the restart. In the
authors' deployment it read `uptime = 13356s` and correctly returned FAILED one minute before the deploy,
then `8s` and OK after. A check you have watched fail is a check you can believe.

### Check 3 — check PID path identity and file ordering

Check 1 diffs files and Check 2 proves a process restarted. A restart of the wrong unit can still
pass Check 2 while a correct tree passes Check 1. Check 3 asks two narrower questions about the
serving PID:

1. **Path identity:** does the path resolved from the process's own identity equal the deployed
   path? For an interpreter, derive the script path from that PID's command line and working
   directory rather than trusting a caller-supplied service path.
2. **Ordering:** does the file's modification time predate the process start by more than a small
   uncertainty margin? Derive the boot-time reference once per guard run. A file modified after
   process start, or timestamped within the margin, is CANNOT-PROVE rather than bound.

Only after path and ordering hold may the current disk hash be compared with the expected deploy
hash. For a non-adversarial deployment, an unchanged pre-start file makes that disk comparison
useful. It still does **not** measure any of these three distinct objects directly:

- the source bytes an interpreter read;
- derived bytecode the interpreter may now execute;
- the process's current memory.

Do not substitute one for another. An external guard cannot recover which source bytes an
interpreter read merely by hashing the current file at its script path.

Modification time is forgeable. Archive extraction, a timestamp-preserving copy, or deliberate
timestamp restoration can make changed bytes present an older time. This arm guards against
accidental stale deploys and drift, not an adversary.

The reference implementation is **Linux-only**. It reads `/proc/<pid>/exe`, `cmdline`, `cwd`, and
`stat`; on other platforms, or when that kernel interface cannot be read, it returns CANNOT-CHECK.
No Windows mechanism is implemented.

Compiled binaries are a genuinely different case: the executable link and file-backed memory
mappings can identify the running image. A stronger claim must be scoped to that case and must
inspect those mappings. This interpreted-script arm does not do that.

Reference implementation and gate: `guard/verify_running_build_pid.py` and
`guard/tests/test_verify_running_build_pid.py`. The gate starts a process from a file, overwrites
that path in place with the intended deployment bytes, and requires CANNOT-PROVE. It also retains
an untouched positive control and a path-mismatch control. `--selftest` reaches BOUND, NOT-BOUND,
CANNOT-PROVE, and CANNOT-CHECK against a live child process.

## Fail closed, and roll back automatically

If any marker misses or the process did not restart, **the deploy has failed even though every command
returned success.** Restore the backup and restart. Do not report a partial deploy as done, and do not
leave a half-updated tree serving.

Take the backup *before* the copy, and print the exact one-line rollback command in the output so it is
available without reconstruction.

## Verification before you call it done

- [ ] Every marker was shown to **discriminate** old from new before the gate ran.
- [ ] Markers were counted on the **deployed** files, not the source tree or the upload.
- [ ] The process-reload check was **proven red** against the pre-restart process.
- [ ] The serving PID resolved the deployed path from its own identity.
- [ ] The deployed file predates process start beyond the uncertainty margin; otherwise the result
      is CANNOT-PROVE.
- [ ] Only after path and ordering held, the current disk hash matched the expected deploy hash.
- [ ] The report does not claim that path, mtime, or disk hash measured interpreted source bytes,
      derived bytecode, or current process memory.
- [ ] A failed gate triggers rollback automatically, not a warning.
- [ ] The receipt/log records what was actually deployed. Check its label — a receipt naming the wrong
      release is a stale clear you will read back later and believe.

## Related

- `guard/teeth_prover.py` — the general rule that every guard ships with a mutation making it go red.
- `guard/verify_running_build_pid.py` (+ its gate `guard/tests/test_verify_running_build_pid.py`) —
  the Linux PID path-and-ordering check, with an explicit CANNOT-PROVE state.
- `skills/guard-target-correctness` — the companion failure: a guard that *can* fail but watches the wrong
  predicate. Marker discrimination is exactly that problem in this domain.
- `skills/honesty-stop-gate` — do not end a turn asserting live state you never measured.
