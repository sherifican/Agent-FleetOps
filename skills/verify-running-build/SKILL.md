---
name: verify-running-build
description: Verify that the code actually SERVING is the code you think you deployed — never infer it from a version string, a git log, or a successful copy. Invoke before asserting "the fix is live", after any deploy or restart, when a fix "did not work" but the tree looks correct, or when reviewing a service whose behavior contradicts its source. The procedure marker-diffs the deployed files AND proves the process reloaded, because each check is blind to the failure the other catches.
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
> for the rules, not universal figures. The two-check structure is the transferable part.

## Why a version string cannot do this job

A version constant, a `--version` flag, a build label in a health endpoint — all of them are **claims made
by the artifact about itself**. They are exactly as stale as the artifact. A build serving old code reports
the old version confidently, or worse, reports a new version because the label was bumped in a file that
did deploy while the logic that mattered did not.

The rule: **never accept a self-reported identity as proof of deployed content.** Diff the content.

## The two checks, and why you need both

Each is blind to the failure the other catches. Running only one is the common mistake.

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
  5b restarted   OK   — uptime=8s (process reloaded from the new files)
```

Prove this one red before you trust it: run it against the live process *before* the restart. In the
authors' deployment it read `uptime = 13356s` and correctly returned FAILED one minute before the deploy,
then `8s` and OK after. A check you have watched fail is a check you can believe.

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
- [ ] A failed gate triggers rollback automatically, not a warning.
- [ ] The receipt/log records what was actually deployed. Check its label — a receipt naming the wrong
      release is a stale clear you will read back later and believe.

## Related

- `guard/teeth_prover.py` — the general rule that every guard ships with a mutation making it go red.
- `skills/guard-target-correctness` — the companion failure: a guard that *can* fail but watches the wrong
  predicate. Marker discrimination is exactly that problem in this domain.
- `skills/honesty-stop-gate` — do not end a turn asserting live state you never measured.
