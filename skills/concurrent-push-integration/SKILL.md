---
name: concurrent-push-integration
description: When a push to a shared branch is REJECTED (the remote advanced), integrate the incoming work — never force over it. Invoke when `git push` fails with non-fast-forward / "fetch first", whenever two or more writers (agents, or an agent and a human, or an automated export) share write access to one repo, or any time you are about to reach for `--force`. A rejected push is information — someone else pushed — not an obstacle to bulldoze.
license: MIT
---

# Concurrent Push Integration — a rejected push means integrate, not overwrite

When more than one writer can push to the same branch, `git push` will sometimes be rejected with
`! [rejected] ... (fetch first)` (non-fast-forward). This is not an error to force past — it is the remote
telling you **someone else advanced the ref while you were working.** Reaching for `git push --force` is the
destructive move: it replaces the remote's history with yours and silently discards whatever the other writer
pushed.

## The protocol

1. **Never `--force` to clear a rejection.** A rejection means the remote has commits you don't have. Forcing
   deletes them — in a multi-writer setup, that is another party's work.
2. **Fetch and read what landed.** `git fetch origin`, then `git log --oneline <your-base>..origin/main` and
   `git diff --stat <your-base>..origin/main`. Now you can tell a benign parallel edit from a real conflict.
3. **Check for overlap** with your own pending change — same files, same lines? No overlap → a clean rebase.
   Overlap → the rebase pauses on the conflicting hunk for you to resolve, keeping both intents.
4. **Rebase (or merge) your work on top** of the remote: `git rebase origin/main`. You are moving your commit
   to sit after theirs, not replacing theirs.
5. **Verify BOTH survived before pushing.** Grep the working tree for your change AND theirs. A rebase that
   silently dropped one side is worse than the rejection — do not trust "rebase succeeded", read the result.
6. **Push** — now a fast-forward.

## The one time force is legitimate — and how to make it safe

A *deliberate, authorized* rewrite of a commit you just pushed (e.g. correcting a malformed author email on
your own tip commit) is a real use of force. Even then:

- Use **`--force-with-lease`, never bare `--force`.** `--force-with-lease` refuses the push if the remote has
  moved since your last fetch — so if another writer slipped a commit in, the lease FAILS and stops you, which
  is precisely the signal to go integrate instead. Bare `--force` has no such guard and overwrites blind.
- A lease failure is not an obstacle either — it is the same "someone else pushed" signal. Fetch, integrate,
  and re-decide whether the rewrite is still needed.

## Why this matters more with agents than with people

Two humans on one repo coordinate socially ("I'm pushing now"). Two agents — or an agent and a human, or an
agent and an automated export — push on independent schedules with no shared clock, so collisions are normal,
not exceptional. The capability to push is shared, which makes **"who pushes" a coordination decision, not a
capability limit**; the integration discipline above is what makes shared write access safe instead of a
clobber lottery.

## Why this exists

Distilled from a live incident where **an agent and a human collaborator (the repository owner)** both held
push access to one repository. The agent's push was rejected mid-task because the human had pushed a one-line
change moments earlier. The agent fetched, confirmed the incoming edit did not overlap its own, rebased on top,
verified both changes survived in the tree, and pushed a fast-forward — no work lost on either side.

The agent-and-human case is the sharper one: a claim/driver lock can stop two *agents* from colliding, but it
**cannot bind a human collaborator's own hands** — so *recovery* after a collision, not just *prevention* of
one, is mandatory. (The incident was first written up with the wrong participant — "two agents" — which would
have taught the wrong prevention; corrected here, which is itself the argument for recording provenance.)
Generalized; identifiers removed.

**Related:** `specs/driver-lock-protocol.md` is the *prevention* half — claim a region before writing so two
agents don't collide. This skill is the *recovery* half — what to do once a collision has happened anyway,
which a lock cannot prevent when one writer sits outside its authority.
