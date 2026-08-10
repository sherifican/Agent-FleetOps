---
name: task-dependency-sequencing
description: Run BEFORE starting any long/complex multi-step task (3+ interdependent steps, phased builds, or anything where execution order changes the outcome or where steps unlock each other). Produces a dependency map + the complementary order of operations up front — so steps run in the order that unlocks/sharpens each other, independent work overlaps wait-time, decisions follow their inputs, and pre-groundwork is surfaced before you're blocked mid-task. Invoke at task kickoff, before committing to an execution order.
---

# Task Dependency Sequencing

Before executing a complex multi-step task, **map the dependencies and derive the *complementary* order** — the sequence where each step unlocks or sharpens the next, nothing serializes that could parallelize, decisions follow their inputs, and you never discover mid-task that a prerequisite wasn't built. Do this FIRST, before any execution.

## When to use
- Any task with **3+ interdependent steps**, a phased build, or where order materially changes the outcome or cost.
- When several sub-tasks are on the table and "what's next" isn't obvious.
- Before committing to an execution order — this is pre-flight, not a post-mortem.

## The method (produce this BEFORE executing)

### 1. Enumerate the steps
List every sub-task plainly.

### 2. Build the dependency map — classify each link
For each step, ask what it **consumes** and **produces**, then classify:
- **HARD dependency** — B literally cannot start without A's output (A → B).
- **SOFT / informing** — A doesn't block B, but A's output makes B *cleaner, cheaper, or correct*. ← the biggest sequencing wins hide here and are the easiest to miss.
- **Independent** — no link either way → a candidate for parallelism.

### 3. Identify the roles
- **Groundwork** — defines the *method, metric, or input* a later step needs. Goes EARLY even if it looks minor. (e.g. a rule that defines how a downstream experiment must be scored — skip it and you measure the wrong thing.)
- **Gating unknown** — the high-value question whose answer decides a later step. Goes BEFORE the decision it gates.
- **Independent / cheap** — gates nothing; slot into the **wait-time of an expensive step** (free parallelism). Never serialize it.
- **Decision** — goes LAST among its inputs. Deciding before the input lands is a guess.

### 4. Derive the order (priority ladder)
1. Groundwork that defines metrics/inputs/methods for others.
2. The highest-value gating unknown (build its pre-groundwork, then run it).
3. Independent/cheap work overlapped into the gaps.
4. Decisions, once their inputs are in hand.

### 5. Name the pre-groundwork
For each step: what must be **built or prepared** before it can run? Surface it now — the failure mode is hitting a missing prerequisite mid-execution.

### 6. State the complementarity explicitly
For every ordering choice, say *why*: "A before B because A's output makes B's metric honest / removes B's blocker / lets B parallelize." **If you can't name the complementarity, the order is arbitrary** — re-examine it.

## Output
A short ordered plan: the sequence, each step's dependencies + pre-groundwork, what overlaps what, and a one-line rationale per ordering choice. Then execute in that order.

## Pitfalls
- **Serializing independent work** — the most common waste; overlap it into an expensive step's wait-time.
- **Deciding before the input** — making a call whose deciding data isn't in yet.
- **Skipping the metric-definer** — running a test/experiment before codifying how it'll be judged → you measure the wrong thing or re-commit a known flaw.
- **Unsurfaced pre-groundwork** — discovering mid-task that a step needs a prerequisite you didn't build.
- **Treating a soft dependency as independent** — missing that doing A first would have made B cleaner.

## Canonical pattern
`rule / metric-definer  →  build the gating experiment (its pre-groundwork)  →  run it (overlap cheap independent work in the wait gap)  →  make the decision with the result in hand.`
