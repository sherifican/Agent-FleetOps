---
name: peer-drop-watcher
description: Build a watcher that reliably catches when a PEER AGENT drops a file into a shared directory, so a human never has to relay "the other agent sent you something". Invoke when a user says "set a watcher on the other agent", "why do I keep having to tell you when a file lands", "watch the passback/inbox/drop folder", or when two agents exchange files and keep missing each other's messages. The procedure forces four properties most hand-written watchers lack — drain-before-arm, a shared definition of what counts as a message, self-echo exclusion, and a distinguishable dead-link state.
license: MIT
---

# Peer Drop Watcher — catch the peer's file without a human in the loop

Two agents exchanging files through a shared directory will each happily set watchers on their own
sub-agents and still fail to notice each other. The human ends up as the message bus, saying "the other one
sent you something" several times an hour. That is the problem this skill removes.

The mechanism is not hard. **Every failure here is in the four properties below, and a watcher missing any
one of them looks identical to a working watcher right up until it silently drops a message.**

> **Framing note:** the failure modes and measurements below come from the authors' own two-box setup
> (two agents, one shared directory over a local link). They are evidence for the rules they sit under,
> not universal constants. Your transport and paths will differ; the four properties do not.

## The contract you operate under

1. **Prove it red before you trust it green.** A watcher is a guard. Stage a real file into the drop
   directory and confirm the watcher fires; then confirm it stays quiet when nothing lands. If you cannot
   make it fire on demand, you have not built a watcher — see `guard/teeth_prover.py` for the general form
   of this rule.
2. **Ask about anything you cannot determine.** The drop path, which file types count as a message, and
   whether the peer writes bookkeeping files into the same tree are all things you must know exactly. Ask
   one concrete question with your best guess offered rather than assuming.

## The four properties

### 1. Drain before you arm — the baseline-reset bug

The default shape of a naive watcher is: record "now", then poll for anything newer. **Everything that
landed while the watcher was not running is now permanently invisible**, because arming moved the baseline
past it.

This is the single most expensive bug in this class, because it fires exactly when you are busy — you are
writing a long reply, the peer drops a file, you finish and re-arm, and the re-arm swallows it. In the
authors' setup this cost a full build cycle: an agent waited on a reply that had already arrived.

**The fix:** persist the mark to disk and, on arm, first check for anything newer than the *persisted*
mark. Report those immediately as a DRAINED set, then continue watching.

```
  === DRAINED (landed while not watching) ===
  2026-08-25 23:44   1975   <path>/NOTE_....md
```

Only advance the mark once a drop has actually been reported.

### 2. One shared definition of "a message"

If the watcher decides what counts as a peer message and some other tool (a preflight, a stop gate, an
inbox check) decides separately, they will drift — and because they often share a mark file, the drift is
invisible. In the authors' setup a review caught a watcher scanning three roots and six extensions while
the paired preflight scanned one root and four, with both writing the same mark.

**The fix:** extract the definition — roots, extensions, exclusions — into a single sourced file that every
tool reads. Not a copied constant in each. One file, sourced.

### 3. Exclude your own echo

A shared directory usually carries bookkeeping alongside payload: acknowledgement logs, receipt files,
ingest records, lock files. If the watcher counts those, it fires on **its own side's** writes and reports
a peer message that does not exist.

**The fix:** whitelist payload, do not blacklist noise. A whitelist fails closed (a new bookkeeping file is
ignored by default); a blacklist fails open (a new bookkeeping file fires a false alarm until someone
notices). Exclude acknowledgement and receipt subdirectories explicitly.

### 4. A dead link must not look like silence

"No files have arrived" and "I cannot reach the peer" produce the same output from a naive watcher: nothing.
That is the worst possible collision, because a broken link presents as a quiet peer and you wait
indefinitely on a message that can never come.

**The fix:** a tri-state exit. Something arrived / nothing arrived within the window / **CANNOT_CHECK**.
Give the unreachable case its own distinct exit code and say so out loud. `CANNOT_CHECK` is not silence,
and it is not a clear — see `skills/honesty-stop-gate` for the same distinction applied to turn-ending
claims.

## Process-liveness check — beware the platform trap

A watcher that has died is worse than no watcher, so tools that depend on one usually check it is alive.
The obvious implementation — search the process table for the watcher's command line — **is not portable**.
On some shells and platforms the process table does not expose command lines to that query at all, so the
check reports "watcher dead" on every single run regardless of truth.

That is a check that cannot pass, which is the mirror image of a check that cannot fail and just as useless.

**The fix:** have the watcher write a PID file on start and remove it on exit; check liveness by testing
that PID directly. Verify your liveness check on the actual target platform before shipping it.

## Verification before you call it done

Do not report a working watcher on the basis that you started it. Confirm all four:

- [ ] **Fires:** stage a file into the drop directory; the watcher reports it and exits/notifies.
- [ ] **Drains:** stop the watcher, stage a file, restart the watcher — the staged file is reported as
      DRAINED, not swallowed.
- [ ] **Ignores its own echo:** write a bookkeeping/ack file; the watcher stays quiet.
- [ ] **Distinguishes a dead link:** break the connection (or point it at an unreachable path); the watcher
      reports CANNOT_CHECK, not "nothing arrived".

## Relationship to the send side

This skill is the **receive** half. Its complement is `guard/passback_send_check.py`, which asks whether the
peer actually holds what you think you sent — evidence taken from the recipient, never from your own send
log. Together they close the loop: the sender confirms delivery, the receiver learns of arrival. Neither
substitutes for the other; a file can be confirmed-sent and still never noticed.
