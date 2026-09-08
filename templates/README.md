# Dispatch-harness templates

These are annotated, non-runnable starting points. Replace every `<...>` placeholder, review each capability granted to a worker, and test the selected CLI's options before adoption.

## `sandboxed-dispatch.sh.template`

Pattern: runs a worker under bubblewrap with a read-only system view, disposable home and temporary storage, one explicitly writable workspace bind, shared network, and parent-death cleanup.

**The defect this prevents:** a worker was accused of inventing results when it had accurately described a different sandbox filesystem view. Deliberately scope the workspace bind and state the sandbox boundary in the brief so the receiver knows what the worker can see.

**The read bind and the writable workspace bind are two different exposures.** The template's shipped read-only root bind makes the whole host READABLE to the worker; only the home directory is re-pointed. Therefore a brief scrubbed of identifiers does not scrub the files the worker can read: redaction of the brief reaches neither bind. That read bind is a decision to make for the destination, per task, not once for every task. A distribution-specific narrow alternative is written out in the template's trailing comments and is deliberately incomplete. The following paragraph already covers the writable workspace bind.

The separate writable workspace bind is the WRITE channel, and it is the one exposure the per-invocation isolation does not cover: the temporary storage and the sandbox home are fresh tmpfs on every run, but the workspace is host storage, and a workspace reused across runs is what makes an output unattributable — a file falling inside two runs' windows can only be recorded as belonging to neither. Give every run its own workspace directory.

## `dispatch-wrapper.sh.template`

Pattern: accepts a brief file and output file, logs start and end metadata with the explicit model and effort, applies a no-hang `timeout`, and retains every non-empty artifact. A nonzero worker exit with output writes a `.PARTIAL` marker and prints `PARTIAL — exists but UNVERIFIED`.

**The defect this prevents:** a worker report said failure while the artifact had landed on disk; retrying would have overwritten good work. The artifact is ground truth in both directions, and the marker forces inspection before retry. This occurred repeatedly on a reference fleet.

Every run also writes `<output>.receipt.json` beside the artifact, carrying seven fields — the artifact path, its byte length, its sha256, the worker's exit status, the lane, a UTC timestamp, and an `outcome` of `ok`, `partial`, `empty`, `usage` or `timeout` — so how a dispatch ended can be read without re-running it or reading a log by eye. It is written from a trap on every exit path the shell can trap; an untrappable kill leaves none, and the template says so rather than letting an absent receipt be read as evidence.

**Arm the watcher in the same turn as the dispatch.** A watcher armed later, or not at all, makes a human the polling mechanism. Tell the watcher which artifact to expect, and key its death test on process absence plus the absence of a valid artifact — a vocabulary of known failure strings is a supplement to that test, never the whole of it.

**A completion test evaluated while the worker may still be writing needs a different shape from one evaluated after it exits.** After exit, a size threshold is a fair basis for classifying the run — a good artifact ends the question, which is what the failure classifier does with it. Before exit it is not, because agents commonly write a plan or a placeholder first, so a watcher keyed on "the artifact is non-empty" fires on the plan. Tightening it to "the artifact contains the terminal marker" is not enough either, if the brief quoted a *previous* round's verdict as context and the worker echoed that quotation into its plan. The brief and the watcher are coupled: quoting a prior terminal marker into a brief can introduce the echo that defeats a loose pattern. Anchoring the marker to the start of a line fixed that in the reported run, and is still not a general contract, because a plan can quote a verdict at line start too. What holds better is a terminal record emitted by the supervising wrapper and bound to the run the watcher expects. The receipt above records the wrapper's outcome through its exit and signal handlers, so give each run a unique output path and have the watcher check that path; its absence stays inconclusive on the uncovered exit paths described above. Where free-form output is the only interface, treat the marker as a convention that narrows the accepted syntax, not as proof the run ended.

**Containment of a dispatch already in flight has to be arranged at launch.** The topology has to put the worker inside the group that gets recorded. Measured with one coreutils implementation (uutils coreutils 0.8.0): a wrapper started under `setsid` leads its own process group; a worker run under plain `timeout` lands in a DIFFERENT group, because that tool puts itself and its child in a new one; the same worker run under `timeout --foreground` stays in the wrapper's group. Whether a given implementation behaves this way is a property to CHECK on the tool at hand, not to assume. So start the wrapper as its own session or process-group leader, keep the timeout tool inside that group rather than around it, record that group id at launch, and — before killing — verify both that the recorded group still belongs to that dispatch and that the worker is actually a member of it. Where the topology cannot be arranged, record and verify the worker's actual group instead of assuming the wrapper's.

**Killing the inner model process instead is the wrapper's retry trigger.** A fallback lane resurrects the payload unless the wrapper dies first. Killing "the wrapper's group" without having made one targets whatever group the caller happens to be in. This paragraph is doctrine: no containment test ships beside it here.

**The defect this prevents:** two workers hung indefinitely during network stalls until a timeout bounded them. A dispatch without a ceiling can stall the whole fleet.

## `honesty-prepend.md.template`

Pattern: prepend a compact evidence contract to each brief: no invented results, confidence labels, explicit limitations, a self-check, and render-and-look for visual tasks.

**The defect this prevents:** workers without the prepend returned polished overclaims. The prepend encourages labeled evidence instead of unsupported "it works" language. It is prompt discipline, not a guarantee; the receiver still verifies the artifact and claims.

## `pinned-env-dispatch.sh.template`

Pattern: pin model and effort in the invocation, clear inherited tuning variables, and log the effective values.

**The defect this prevents:** an ambient effort variable silently raised every child dispatch to the most expensive tier, breaking a cost cap until detected. Pin settings per invocation and echo the effective values.

## `roster-check.sh.template`

Pattern: DERIVES the routed model tags from the routing document itself, in the same run, then fails when one of them is absent from the live roster. A committed one-tag-per-line list is optional and is treated as a cross-check: it must equal the freshly derived set. A failed or empty roster lookup, and an extractor that finds no tags, are CANNOT CHECK — never a pass and never a red.

**The defect this prevents:** a routing table is a living document and the roster moves underneath it; a row that outlives its model fails at dispatch time, unattended. This arm moves that failure to check time — but only if it reads the table. An earlier version trusted a separately maintained tag list, so the list rather than the table was the subject of the check: with a table naming two models, a list naming one, and a roster serving that one, the arm reported clean while the table still named the absent model. Gate: `guard/tests/test_roster_check.py` runs the template against a fake roster missing a model the TABLE names, and against a committed list that has drifted from the table, and requires the red in both.

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
