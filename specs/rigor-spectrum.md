<!-- Hand this file to your AI agent with "adapt this rigor spectrum to my project" — it is a teaching doc, not a config. -->

# Rigor spectrum — same teeth, different rung count

> **guard COUNT scales with how much can act unread; guard QUALITY does not scale at all. Every check ships something that proves it can fail, or it does not ship.**

That is the whole law. The rest of this file is how to spend the count, and how not to fake the quality.

A number in this document is a timestamp of when someone counted, not a live measurement. Publish the **command** a reader runs on *their* tree; do not copy a count out of prose. Counts go stale in minutes.

**A note on two names used throughout — `REFUSED` and `CANNOT_CHECK`.** These are vendor-neutral *teaching* names for two stop conditions: a guard that actively refuses (a regenerated shrink it will not accept) and a guard that cannot run its measurement at all (a severed machine, a missing corpus). This public tree renders both as a single `UNMEASURED` / exit 2 — where `2` dominates `1 = violation` — rather than shipping a two-type hierarchy. The point that transfers is that neither situation is a pass; the specific token is local.

## The axis: can it act unread?

The weaker predictor is artifact complexity — how many files, how clever the code, how “serious” the project looks. The real question is:

**Can a wrong or absent artifact ACT on someone before a human reads it?**

An application with an auto-updater can: it installs bytes on every box before anyone opens them. A pattern library whose rules are **auto-loaded into agents** can too. A deleted or drifted rule silently changes behaviour on every box that loads it, with no runtime and no users in the usual sense. That is not a low-blast-radius artifact wearing a docs costume. It is the same failure mode with a longer fuse. Deletion is the ugly case: removing a rule makes every subsequent report look *cleaner*, so the routine checks (“is what is here correct?”) all stay green.

What scales with that blast radius is how many rungs you buy. What does not scale is whether the rungs have teeth. Ten unproven guards are worse than three proven ones: they produce a green report nobody can price. If a project cannot afford the teeth, it ships fewer checks, not softer ones.

The two-column comparison below extends the README split (pattern-library workflow vs user-facing app / updater / corpus), expanded so the shared class is visible. The columns are **not** “toy vs serious.” They are the same religion, different surfaces, different fuse.

| | Pattern-library workflow | User-facing app, updater, or long-lived corpus |
|---|---|---|
| Can a wrong or absent artifact act unread? | **Yes**, if rules auto-load into agents (or any consumer that will not re-read them). A deleted or drifted rule changes every loader. No crash, no ticket, no user in the usual sense. | **Yes.** An updater can install the wrong bytes — or refuse to install anything — on every box before a human reads them. |
| Fuse | Longer. Damage is behavioral drift across sessions. | Shorter. A bad manifest is a fleet-wide install failure or a bad binary. |
| Blast-radius class | **Same class**, longer fuse. Not a docs costume. | Same class, shorter fuse. |
| Job of the guards | Prove a few exported patterns still have teeth. Refuse shrink on regenerated indexes and rollups. | The same base, then protect shipping artifacts, the update channel, and any corpus the product is scored against. |
| What scales | Rung **count**. Fewer surfaces → fewer checks (a handful of invariants, not a hundred). | Rung **count**. More surfaces (release, manifest, changelog, twin files, banners). |
| What does not scale | Quality. Every check proves it can fail, or it does not ship. | Same. |
| Add when | A narrowly scoped mutation case after a **named** incident. | Fund the extra rungs whose surfaces you actually have. Do not invent a hundred invariants by renaming someone else’s tests. |
| Acceptance | Prove the guard can fail. Reject unknowns (a `CANNOT_CHECK` is a stop, not a pass). Do not simulate an unshipped release system. | Rehearse the release path. Demonstrate the relevant failure controls, including rollback. |
| What you copy on day one | The baseline practices in [Adopter rule](#adopter-rule). | Those, **plus** the extra rungs for updater / public mirror / parsed changelog. |
| Stop rule | Stop after the applicable baseline — including teeth and shrink-refusal for auto-loading artifacts — until a named failure mode appears. Auto-load buys more rungs, not softer ones: reversibility and auto-load pick which *extra* surfaces you fund, never whether the baseline holds. | Same stop rule. More surfaces, not a different religion. Fund more rungs when users, self-updates, or irreversible corpus changes justify them. |

**Which extra rungs, not which class.** A previous draft of this spec asked three questions as a *class* check:

1. Could a bad change affect users, self-update a system, or alter a long-lived corpus?
2. Is the failure hard to reverse or expensive to detect after release?
3. Does this project have a measured history of high-cost regressions or cross-surface drift?

Those questions are still the right *surface picker*. They are the wrong *class* test. “Every answer is no” does **not** mean you are a lower blast-radius class if rules or patterns auto-load into agents. Auto-load already puts you in the can-act-unread class; you buy fewer rungs, not softer ones. Use the three questions, plus these, only to decide which families from the next section you actually need:

1. Do rules or patterns auto-load into agents (or any other consumer that will not re-read them)? → shrink-refusal and teeth are not optional because the repo “is just docs.”
2. Do you ship an updater that must not brick people, and must not stall every update on a prose edit? → two-stage release; classify-then-pin; a repair that does not use the update path.
3. Do you keep a public copy of a file you also run from somewhere else? → name the authoritative copy, or they will diverge at the worst moment.
4. Does something parse your changelog, version table, or banners? → test them as a format, not as prose.

Zero extra surfaces still means the baseline, with teeth. It does not mean “skip proving the checks can fail.”

## Choose your rung count

Eight protocols, adapted down from a production application (users, auto-updater, sha256-pinned shipped tree). Each paragraph is the **shape**. Copy the shape; do not copy a tool, a path, or a count. Spectrum tags:

- **universal as a principle** — the shape belongs on any repo whose artifacts can act unread; the *count* of instances is local.
- **worth anywhere with versioned artifacts** — buy it when you have history, mirrors, or a changelog something consumes.
- **app-project only** — the machinery is for updaters and shipped trees; the one-line principle still teaches.

### 1. Invariant suite + mutation needles

**Spectrum: universal as a principle** (shape universal, count is not).

Kills a check that cannot fail: guards that were green because they never ran, never bound, or asserted something trivially true. Each invariant asserts one property by **driving the code, or by parsing it (AST)** — never by searching its text. Each is paired with a **needle** — a one-line mutation that reintroduces the original bug — and the sweep asserts the guard goes red. A bind-check proves every needle binds exactly once; an aim-check proves it binds *where the guard actually looks*. A pattern library wants a handful of these, not a hundred. The transferable rule is the whole point of this file: **no guard without a proof it can fail.** Maintenance is real: a needle whose subject moves goes vacuous (see §7). If you cannot afford the teeth, ship fewer checks.

### 2. Rehearse the moving end; pin only the frozen end

**Spectrum: worth anywhere with versioned artifacts.**

Kills **silent narrowing** — a ship gate whose coverage shrinks while its verdict stays green. The classic shape: a matrix that still prints “every listed old version updates cleanly” while the entire *current* line went unrehearsed, because the recent end was pinned by hand and nobody moved the pin. Derive the recent end of any rehearsal or compatibility matrix from an **authoritative, versioned history** at run time; record the resolved range so the run is reproducible, and **fail closed (or fall back to a reviewed cache) when that history is unavailable, mutable, or rate-limited** — a derived endpoint is only as trustworthy as the source it reads, and an unavailable source must be a stop, not a silent pass. Pin only deliberately-historical anchors. A guard must fail if a pinned anchor has become recent — that is the shape of the bug returning. Low-maintenance when the history source is dependable; that is the point of deriving. Teaching unit: **derive the moving end, pin only the frozen end.**

### 3. Two-stage release (announcement last)

**Spectrum: app-project only.** Teach the principle; do not install the machinery on a repo with no updater.

Kills the window where people are told a version exists before its files do. Stage 1 ships files, changelog, and manifest, with version banners deliberately still old. Stage 2 ships the version banner alone, last, as the trigger. Between them, coherence is checked against the published tree for consecutive clean rounds. A pattern library has no updater and no trigger. The principle that transfers: **make the announcement the last irreversible step.**

### 4. Pin what will be committed, not what is on disk

**Spectrum: app-project only** at this depth — but the rule below is worth anywhere you publish. Transferable rule: **compare what will be committed, never what is on disk.**

Kills shipping a manifest that describes bytes other than the ones that will actually leave the tree — a hash-mismatch update failure for every user. Compare index bytes (the snapshot the next commit/publish will carry), not the working copy. Line-ending differences (CRLF vs LF) make working-copy comparisons silently wrong; the hasher must normalise identically in both places. A generator that refuses to write when required entries are missing, or when a version table disagrees with the version file, is a stop, not a warning. The same rule protects any **public mirror**: a check that reads your local worktree cannot see an edit made directly on the hosting site, so it passes while the published bytes have diverged — which is exactly the *detection* failure in the [case study](#case-study-a-prose-edit-that-halted-every-update) (the outage ran for a day with every local hash matching). See it before treating “pin everything” as the whole design.

### 5. Twin-file divergence

**Spectrum: worth anywhere with versioned artifacts** (specifically: anywhere you keep a public mirror).

Kills two copies of a release-critical file drifting, so the version you *run* at release time is not the version you *reviewed*. If a file exists twice, name which copy is authoritative at each moment, or it will diverge at the worst one. Cheap to check, easy to forget; in the project this was adapted from it has already diverged and been resynced. Wiring anything new into the public copy is a release-surface decision, not an edit. Do not invent a twin. If you already have one, name the authority.

### 6. Shrink-refusal on regenerated artifacts

**Spectrum: universal as a principle.** Highest teaching value of the eight, and it belongs on a pattern library *more* than on most apps — indexes and rollups are regenerated artifacts. Note the scope: this public tree ships teeth-prover, contract agreement, unit gates, liveness, a mutation harness, and transactional rollback, but **not yet a generic generated-artifact shrink-refusal component** — treat this section as an adopter pattern illustrated from the source system, not as a description of a guard already in this tree.

Kills a regenerated artifact coming back **smaller** with nothing going red. Every routine check asks *is what is here correct?* — and most of them validate only what remains, so they never see broad population loss: what is gone cannot be wrong. (Required-item and closure checks are the partial exception — they catch specific enumerated deletions. Add shrink-refusal for the loss they do not enumerate.) Real shapes: a lost changelog delimiter made an entire entry invisible to the in-app parser; a stale-copy generator run dropped the majority of a pinned manifest. Count the artifact’s **meaningful units**, not generic lines — a line count catches a missing manifest entry and misses a changelog whose separator vanished. The counter must mirror the consumer’s parser contract, so a header whose delimiter was lost counts as absent.

A `REFUSED` (the guard will not accept the shrink) and a `CANNOT_CHECK` (the guard could not measure) are separate stop types; neither subclasses the other, and both stop. Cannot-check-as-pass is exactly how a stale ship gate ships. An escape hatch that permits a shrink still logs the delta: a silent intentional shrink is indistinguishable from an accidental one six months later.

### 7. A needle whose subject moves cannot be hardcoded

**Spectrum: universal as a principle.**

Kills a needle that still binds but no longer aims — the guard’s teeth silently gone. The bind-check proves the needle exists; a second aim-check proves it sits inside the entry the guard actually reads. Moving needles are **derived** from the newest subject at load, selected through the guard’s *own* recognition, and they refuse to register at all if they cannot be made unique. In the source system this was a per-release manual chore that shipped vacuous needles; deriving the needle removed that recurring per-release cost. Sister of §2: derive the moving end, including the moving *test*.

### 8. Version surfaces tested as format, not as prose

**Spectrum: worth anywhere with versioned artifacts** (anywhere with a changelog something parses).

Kills a release whose changelog, banners, version table and version file disagree — and a “What’s New” surface that silently shows nothing. The newest changelog entry is the current version and has a real body; every heading in that entry has a body (a bodyless heading renders blank). A generator refuses to write when the newest table row is not the current version. The changelog is parsed by something, so test it as a **format**, not as prose. Do not point a sandboxed invariant at an absolute path outside the tree under test: that reddens the mutation sandbox and blames the product. If the files live in two trees, the gate lives with the generator that sees both, or you have two entry points (see lesson B).

## Cross-cutting lessons

These are not extra rungs. They are how rungs die, and they transfer even when you copy only the baseline.

### A. Recurrence trigger

*More than twice warrants an investigation; past three is a must review-and-patch — because something that fails that often is failure-prone even when it succeeds a lot.* The trigger is a **count**, not a feeling. In practice the felt count runs lower than the real one — in the source project the same drift class recurred twice more in the very day it was written up — which is exactly why a feeling is not the instrument. Cheapest, most portable item in the inventory; it needs no runtime.

### B. An assertion about the machine cannot live inside a sandbox that severs the machine

A check that needs the host (telemetry, a live service, a GPU, a network path) cannot be proven inside a harness that cuts those off. The tempting fix — make the check tolerate zero — makes it vacuous, which is the defect the guard existed to prevent. **Two entry points, not one loosened check:** one that runs where the machine is present, one that fail-closes as `CANNOT_CHECK` where it is not. Directly relevant to any telemetry or liveness half.

### C. The cheap observable is not the expensive property

A checker that measures a cheap proxy for an expensive fact will eventually certify the proxy and miss the fact. Demonstrated across:

| Cheap observable (the proxy) | Expensive property it was taken to prove | How it lies |
|---|---|---|
| Code predicate (“compared against −1”) | A miss is handled here | −1 is a *legal* index in some languages; a miss becomes “the last character.” The checker for the class contained the class. |
| Text match for a call name | The dangerous call is guarded | A regex for `.find(` also matches another language’s `.find` inside a string template. A regex lint would have *been* an instance of what it checks. **Parse the program (AST), do not search the text.** |
| Shell query that returns a count | The population you think you queried | A default flag silently narrows the population (tracked files skipped, a date resolved to “today at now”). Measured-zero and measured-nothing look the same. |
| Test fixture filename / layout | The fixture exercises the property | Periodic texture aliases; the “clean” control is not clean. The fixture did not have the property its name claimed. |
| Filename containing `pre-guard` | The guard **ran** | The name is a label someone typed. It is not an execution trace. |
| Delivery state `delivered` | The artifact was **read** (or acted on) | Delivered means left the sender. It does not mean a human or a consumer looked at it. |
| The local checkout’s hashes all match | The **published** bytes match their pin | The worktree is not the mirror. A release check that reads your local tree cannot see an edit made directly on the hosting site — the case-study outage ran for a day with every local gate green for exactly this reason. |
| A harness exited `0` | The harness **passed** | Judge a harness through a pipe and you read the pipe’s exit code, not the harness’s verdict — an `rc=2` that was the interpreter failing to find the file reads as a clean gate. |

A checker that cries wolf gets switched off, which is how a guard dies. Prefer a predicate that can go red on a known miss and stay quiet on a known handle — and when the proxy is all you can afford, name it as a proxy in the verdict, never as the property.

### D. A guard with no caller scores identically to a guard that never fires

A proven mechanism sitting unwired produces the same green as a missing mechanism. Ship the wiring, or ship a dated followup with a deadline. Never just the function. Worth teaching as: **ship the wiring or ship the followup, never just the mechanism.**

### E. Stage the subject with the guard

A new guard that reads a file the mutation sandbox does not carry will correctly refuse to vouch for what it cannot see — and the baseline goes red blaming the product. Stage the guard’s subject in the same commit as the guard. Second half, equally load-bearing: the harness’s own auto-diagnosis of “you forgot to stage it” can be wrong. It has confidently reported a staging gap when the file was staged and the real cause was environment-dependence. Stage the subject, *and* reproduce inside the sandbox before believing the harness’s hint.

## Case study: a prose edit that halted every update

A maintainer hand-edited one prose clause in a public README. That README was one file in a sha256-pinned update manifest covering the whole shipped tree (on the order of a thousand entries on that project — re-measure on yours; do not trust the number in this sentence). The main application had already downloaded and validated; then the updater reached the changed README, treated **any** entry mismatch as fatal, and aborted the whole staging transaction — discarding the validated download:

```text
staging failed — README.md (hash mismatch after download). Nothing was changed; your current install is untouched
```

A cosmetic documentation edit blocked every user’s update of the application. Integrity “worked”: the bytes on disk were not the bytes in the pin. The design was still wrong. The lessons below are shapes — and the first is load-bearing, because the obvious fix for it failed review twice in the source project before it held.

1. **Integrity pinning has a threat model, and prose is usually not the dangerous part of it.** The fix is to classify entries — but *classification follows the consumer and threat model, not the file’s prose-vs-code label.* Documentation can be security instructions, a licence/notice, or input another tool parses; when it is, the mismatch is load-bearing and stays fatal. Restrict “warn and skip” to documentation that is demonstrably non-executable and non-security/policy/contract-critical, surface a **visible stale-doc state** rather than swallowing it, and do not un-pin — un-pinning throws away the threat model to paper over a classification bug. Three failure modes turned up while implementing exactly this, all generalizable:
   - **A classifier is a set, and nobody checks the set.** The first cut answered “is this prose?” with a basename allowlist (`README`, `CHANGELOG`) and recognized 8 of 16 prose entries; an edit to any of the other eight would still have aborted every update. Every test passed because every test used a README — covering one member of a class is testing nothing, and the member you reach for first is the one the bug was reported against, i.e. the one already known to work. **Enumerate the class against what actually ships and assert the coverage count.**
   - **The policy has more enforcement sites than the one you are looking at.** The abort lived in the staging loop; two other sites also compared a file to its pin — a startup integrity scan that would mark the skipped doc `DAMAGED` on every launch, and a repair path that would count it `FAILED` forever. A one-time abort traded for a permanent nag is not a fix. **Count the enforcement sites before you fix one; a relaxation policy must live in one predicate every site consults**, or it drifts silently because each site is individually defensible.
   - **The relaxation must be driven by data the producer emits, not a heuristic the consumer guesses** — this is lesson C above, and the most concrete instance of it. “Is it named README” is a cheap observable standing in for “is this prose the application never reads as data.” The generator knows what it ships, so it marks documentation in the manifest and the consumer honours the mark — while keeping a *ceiling*: it may refuse a mark, never widen one, because the manifest is untrusted input. And a producer-emitted mark **cannot relax retroactively** — artifacts published before the mark existed do not carry it, so the consumer must treat *unmarked* as the old strict behaviour, or it silently relaxes every release already in the field.

2. **A guard on the update path must be repairable without the update path — and the repair must be consumable by the version already in the field.** Otherwise its failure mode is unrecoverable: the code fix only helps users who can update, and they cannot. So the immediate remedy has to be a **data-only** change (re-pin the hash) with no version bump that the already-released client can consume; the code fix is necessarily second. An out-of-band rebuild of the pin, a documented skip-class for docs, or a repair tool that does not go through the updater — something that still works when the updater is wedged. One integrity condition the availability requirement must not drop: **the recovery channel needs an independently trusted authentication/authorization path; it must not accept the compromised manifest as its sole authority** — otherwise the emergency path becomes how an attacker replaces the pin or widens a skip-class. Name who may invoke a skip-class and how the event is audited.

This is why §4 is app-project-only at depth, and why “pin everything, fatal on any miss” is not the high-rigor default. High rigor here is **classification** — of the right thing, in one place, driven by the producer — not more pins.

## Adopter rule

Copy these five baseline practices. Prove each can fail. Then **stop** until a named failure mode appears.

1. **Teeth.** Every check ships a planted mutation (or equivalent known-red) that makes it go red. Bind once; aim where the guard looks. If you cannot write the red, you have not written a check. A pattern library wants a handful, not a hundred.
2. **Unmeasured is not clean.** A `REFUSED` and a `CANNOT_CHECK` are separate stops; neither is a pass. A dry-run, a missing corpus, or a severed machine is a `CANNOT_CHECK`, louder than a violation. (This tree renders both as `UNMEASURED` / exit 2.)
3. **Derive the moving end.** Pin only what you meant to freeze. Applies to compatibility matrices, to needles whose subject moves, to generated charts and maps. A pin that you have to remember to slide is a pin that will silently narrow.
4. **Shrink-refusal.** Regenerated artifacts (indexes, rollups, manifests, parsed changelogs) refuse a materially smaller replacement unless an explicit override logs the delta. Count meaningful units, not lines.
5. **Recurrence is a count.** More than twice → investigate. Past three → review-and-patch. Do not wait for the feeling that “this keeps happening”; the feeling under-counts.

Then stop. Do not invent a hundred invariants by renaming someone else’s tests. When a named failure mode appears, take the matching extra rung from [Choose your rung count](#choose-your-rung-count):

| Named failure mode | Extra rung |
|---|---|
| Users are told a version exists before its files do | §3 two-stage; announcement last |
| An updater installs (or refuses) the wrong bytes | §4 pin the index; classify entries; repair without the channel |
| Public copy and run-copy of the same file disagree | §5 name the authority |
| Changelog / banners / version table silently diverge | §8 test as format |
| A needle still binds but the bug would now survive | §7 derive the moving needle |
| A green gate whose coverage shrank | §2 derive the moving end of the matrix |

If none of those failures have happened, the five baseline practices with proof-they-can-fail *are* the high-rigor posture for a pattern library. Adding rungs you cannot watch go red is how rigor becomes a green report nobody can price.
