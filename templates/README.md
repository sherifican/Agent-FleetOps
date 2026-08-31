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

## `roster-check.sh.template`

Pattern: reads a one-tag-per-line routing-models file and a roster command, and fails when the routing table names a model the live roster does not serve; a failed or empty roster lookup is CANNOT CHECK, never a pass and never a red.

**The defect this prevents:** a routing table is a living document and the roster moves underneath it; a row that outlives its model fails at dispatch time, unattended. This arm moves that failure to check time. Gate: `guard/tests/test_roster_check.py` runs the template against a fake roster missing a routed model and requires the red.

## Video-research templates

- `video-research/_DISPATCH_PREAMBLE.md.template` — the shared transcript, verification, relevance, and standout method.
- `video-research/brief.md.template` — a per-video dispatch brief.
- `video-research/RESULT.md.template` — a contract-shaped research-leg result.
- `video-research/FINAL.md.template` — a reconcile skeleton with provenance.
- `video-research/reconcile.sh.template` — annotated orchestration guidance, not a runnable script.
- `video-research/known_ids.txt.example` — synthetic known-video input for `video_backlog_diff.py`.
- `video-research/new_videos.txt.example` — synthetic source-video input for `video_backlog_diff.py`.
- `video-research/SYNTHETIC.md` — fixture-data marker for the video-research examples.
- `video-hub/VIDEO_RESEARCH_HUB.html.template` — one self-contained synthetic card-hub reference.
- `video-hub/SYNTHETIC.md` — fixture-data marker for the video-hub example.
- `solo-rich-report.md.template` — the contract-shaped source template for a qualifying Solo-Rich Report.
