# Dispatch-harness templates

These are annotated, non-runnable starting points. Replace every `<...>` placeholder, review each capability granted to a worker, and test the selected CLI's options before adoption.

## `sandboxed-dispatch.sh.template`

Pattern: runs a worker under bubblewrap with a read-only system view, disposable home and temporary storage, one explicitly writable workspace bind, shared network, and parent-death cleanup.

**The defect this prevents:** a worker was accused of inventing results when it had accurately described a different sandbox filesystem view. Deliberately scope the workspace bind and state the sandbox boundary in the brief so the receiver knows what the worker can see.

## `dispatch-wrapper.sh.template`

Pattern: accepts a brief file and output file, logs start and end metadata with the explicit model and effort, applies a no-hang `timeout`, and retains every non-empty artifact. A nonzero worker exit with output writes a `.PARTIAL` marker and prints `PARTIAL — exists but UNVERIFIED`.

**The defect this prevents:** a worker report said failure while the artifact had landed on disk; retrying would have overwritten good work. The artifact is ground truth in both directions, and the marker forces inspection before retry. This occurred repeatedly on a reference fleet.

**The defect this prevents:** two workers hung indefinitely during network stalls until a timeout bounded them. A dispatch without a ceiling can stall the whole fleet.

## `honesty-prepend.md.template`

Pattern: prepend a compact evidence contract to each brief: no invented results, confidence labels, explicit limitations, a self-check, and render-and-look for visual tasks.

**The defect this prevents:** workers without the prepend returned polished overclaims. The prepend encourages labeled evidence instead of unsupported "it works" language. It is prompt discipline, not a guarantee; the receiver still verifies the artifact and claims.

## `pinned-env-dispatch.sh.template`

Pattern: pin model and effort in the invocation, clear inherited tuning variables, and log the effective values.

**The defect this prevents:** an ambient effort variable silently raised every child dispatch to the most expensive tier, breaking a cost cap until detected. Pin settings per invocation and echo the effective values.
