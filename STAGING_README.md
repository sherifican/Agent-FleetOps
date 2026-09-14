# OSS export staging — one-way curated export target
Purpose: explain how curated exports are checked before publication and which checks an adopter can run.
Pipeline: copy-in → sanitize.py (audit report per file) → wall_check.py (never-publish refusal, mutation-proven) → readme_guard.sh (refuses a tree that DELETED critical README content — the inverse question; deletion passes every other gate)
→ scan gate (secrets + personal data, zero-hit) → review against the export policy →
OWNER GATE per batch → push to the NEW public repo → fresh-clone verify + public CI green.
Rules: no .git is ever copied in; this tree's history begins at its own init; the private backup repo is
never a remote here. Reports land in _reports/.

## What the scan report is, and what it is not

**The EXIT STATUS is the authorization. The report is a diagnostic.** `_tools/scan_gate.py` exits
`0` clean, `1` findings, `2` refused — and `2` says the scan did not complete, so it says nothing
about whether the tree is safe. The shipped `guard/hooks/pre-push` gates on that exit status,
which is the correct channel.

`_reports/scan_report.txt` is written INSIDE the tree being scanned, and that tree is the
untrusted subject of the scan. A publication gate review demonstrated the consequences, from the
CLI and with no race:

  - A tree can ship `_reports` as a SYMLINK with a prewritten `scan_gate: CLEAN` behind it. The
    scanner correctly refuses to publish through the link, and the planted CLEAN stays readable
    at the canonical path beside an exit status of 2.
  - If the report directory is unwritable, or the previous report cannot be read AND cannot be
    preserved, the old report is left standing — which may be a stale CLEAN.

So: **never treat `scan_report.txt` as authorization on its own.** It may be planted, and it may
be stale after a failed run. Authorize on a successful scan of the snapshot you intend to publish,
read from that run's exit status. A guard arm pins this limitation so it stays visible; a passing
arm is evidence of awareness, not of mitigation.

## Who can read the report

**Every report this scanner publishes is `0600`** — owner read and write, nothing for group,
nothing for other. New report or replacement, whatever the umask masked, whatever stood at the
name before. The report names the class, path and line of every secret found, so it does not get
a default audience.

That rule arrived by correction, and the corrections are worth knowing because every one of them
shipped first:

* An early rule made a new report match whatever an ordinary create in that directory produces.
  Measured: `0644` at the ordinary login umask `0022`, and `0666` inside a `0777` directory at
  umask `0` — readable by every account on the box.
* A later rule preserved the mode of the report being REPLACED, so that replacing a report would
  not change who could read it. But the report being replaced lives inside the tree being scanned,
  and that tree is untrusted: a committed `_reports/scan_report.txt` checks out `0644`, and
  preserving it faithfully republished the findings at `0644`.
* Narrowing that preserved mode to "no other" kept group. A planted `0640` hands the file's group
  the class, path and line of every secret found, and "there was an existing file" is not a
  sharing decision by anyone who matters when that file came out of the untrusted tree.
* Intersecting with the staged file's own mode was meant to let a STRICTER local policy still win.
  Measured: the only thing that reliably flowed in through that intersection was the umask
  removing the OWNER's bits. At umask `0400` the findings published at `0200` — a report its
  reader cannot open, which looks to a human exactly like a scanner that found nothing. Removing
  an owner's own bits from a file that owner still owns enforces nothing, because the uid restores
  them whenever it likes.

So the mode is a constant rather than a negotiation. **A directory policy stricter than `0600` no
longer decides the report's mode.** That is a deliberate reversal of the previous rule, and what
it buys is that a default ACL which would lock the owner out cannot produce findings nobody can
read.

**A RETAINED findings file whose access policy could not be installed keeps its bytes and loses
its reserved name.** Both families of reserved name — `scan_report.unpublished.*` and
`scan_report.superseded.*` — mean "retained evidence, carrying the report's access policy". If the
ACL strip is denied, that policy is not on the file, and treating it as policy-bearing anyway
would report a compliance that was never installed — review measured exactly that. **What follows
differs between the two families, because they are holding different things.**

A QUARANTINED file is the only copy of this scan's findings, and its reserved name would survive
beside a freshly published report and stand in for it. So it loses the name: the bytes stay at the
scanner's staged `.scan_report_` name, which promises nothing.

A SUPERSEDED file is a second NAME for the report that was about to be replaced, and there the
denial **declines the replacement** instead: the findings stay at `scan_report.txt`, where they
already were, and the refusal reaches the caller through the exit status. The reserved name is
**kept**, deliberately. Removing it was this round's first shape, and it is wrong for a reason
worth stating: a link is a second name only while the canonical name still reaches the inode, and
in a hostile tree it may stop doing so between the link and the removal — at which point giving
the name back destroys the findings. POSIX cannot express "remove this name only if it is not the
last one", so the fix is not a better check. Keeping it costs nothing, because the replacement was
declined: the inode goes on standing at the canonical name, so an ACL on the reserved name is an
ACL already on the report itself. After a successful scan publication the sweep attempts cleanup of
the older entries in both reserved families; newer entries, and entries whose age cannot be read, are
left.

Neither case costs a byte. Preserving evidence outranks labelling it; claiming a policy it does
not have does not.

The mode cap is applied to the inode either way. The cap and the strip have been independent since
the seventeenth round, and a denied strip must not take the cap with it — that is a separate
property from this one, and this rule does not weaken it.

**Any POSIX ACL on the published report is REMOVED, never carried** — whether inherited from the
directory or copied from the report being replaced. Be precise about what that buys, because an
earlier version of this note overstated it: a `chmod` writes the group bits into the ACL mask, so
at `0600` the mask is `---` and a retained named-user entry is already masked to nothing. The
strip matters because the mask is one `chmod` away from coming back — widening the file to `0640`
re-arms every entry, with no ACL visible in anything a reader is likely to inspect — and removing
the entries makes that impossible instead of merely currently harmless.

**The report DIRECTORY loses group and other write.** Directory write permission, not file mode,
is what governs unlink and create, so a world-writable `_reports/` lets any local account delete
an owner-only report and publish its own `CLEAN` at the same path. Group and other keep whatever
read and traverse they had: this narrows who can forge the report, not who can find it. If the
scanner CREATED the directory on this run it also restores the owner's own `rwx`, because `mkdir`
is umask-masked and a directory the scanner cannot write is one it cannot publish into at all. A
`_reports/` that already existed is left with the owner bits its operator gave it.

**The report directory is held OPEN, not looked up twice.** The scanner validates `_reports/`
once, keeps the descriptor, and resolves every later name — the staged file, the canonical report,
the preservation slots, the sweep — relative to that descriptor. Without this, replacing `_reports/`
with a symlink after it was checked redirected the whole publish: review reproduced a findings
report written OVER a file outside the scanned tree, with an outside file deleted by the cleanup
on the way past, while every check inside the scanner still passed because each one re-resolved
the substituted name.

What that does NOT promise: the final rename still takes names, so a writer who can create files
inside `_reports/` can still swap the staged name in the instant before it. The hardening above is
what bounds this — group and other lose write, so the race needs the scanner's own uid, and an
attacker with that already owns the tree.

There is deliberately no sharing opt-in. If you need a report readable by another account, copy it
out of the staging tree to a location you control, rather than asking the scanner to publish it
wider.

## Reserved filenames in `_reports/`

These names belong to the scanner, which SWEEPS them after a successful publication — those older
than the report that publication staged; a newer entry, or one whose age cannot be read, is left —
regardless of who wrote them:

    scan_report.txt
    scan_report.superseded.txt
    scan_report.superseded.1.txt  …  scan_report.superseded.7.txt
    scan_report.unpublished.txt
    scan_report.unpublished.1.txt  …  scan_report.unpublished.7.txt

The numbered names became scanner-owned when the preservation slot was widened from one name to
eight. Gate review measured an ordinary pre-existing file at `scan_report.superseded.1.txt` being
destroyed by a clean scan. A filename does not establish provenance, so the reservation is stated
here rather than assumed — do not keep anything you care about at these names.

**One narrow case writes a COPY rather than the file itself.** If the staged name stops naming
the staged inode — a concurrent unlink or replace — no name reaches those bytes any more, and the
descriptor this scanner holds is the last reference to them. Linking the inode back into the tree
is not possible at that point (measured: the descriptor-directory path returns `ENOENT` once the
link count is zero), so the bytes are READ through that descriptor and written to a fresh reserved
name. That copy is a different inode carrying the same findings. It is kept even if its ACL strip
is denied, which is the one place this tool reserves a name without having installed the policy on
it: the alternative is destroying the only remaining copy to avoid mislabelling it, and evidence
outranks labelling. The mode is set through the descriptor regardless, so such a file is at `0600`
with its ACL entries masked to nothing.

`scan_report.unpublished.txt` is where a FINDINGS report goes when the publish could not
complete — a FIFO or a foreign-owned file at the report name, a group that cannot be preserved, a
mode the filesystem will not verify. Those failures used to delete the staged findings, and the
refusal written next carries only an error class, never the hits, so the evidence that the tree
contained secrets was lost at exactly the moment it mattered. A staged CLEAN is NOT kept this way:
it is not evidence, and a file saying CLEAN beside an exit status of 2 tells a reader this tree
passed. The kept file is `0600` like any other report, and the next successful publication removes
it along with the preservation slots.

There are eight of those names for the same reason there are eight preservation slots. One name
can be OCCUPIED — by a populated directory that is not the scanner's to remove, or by an EARLIER
run's kept findings — and review measured both failures: an operator's directory at the name cost
a run its hits, and a replacing rename destroyed the previous run's. The names are now claimed
exclusively and never overwritten. If every one of them is unavailable, the staged temporary
holding the findings is LEFT IN PLACE under the scanner's reserved `.scan_report_` prefix rather
than deleted; a leftover temporary holding real evidence is better than no evidence.

A preserved copy is a HARD LINK to the report being replaced, not a duplicate of it, and the
scanner narrows that inode to owner-only — otherwise preservation would keep exactly the exposure
the owner-only publish was closing. Narrowing an inode narrows EVERY name for it. Two of those
names are the scanner's own and deliberate, but if you have hard-linked a findings report to
somewhere outside `_reports/`, that alias is narrowed too. So: this tool will restrict the
permissions of a file it did not create, if that file shares an inode with a findings report
inside the tree you asked it to scan. It only ever moves in the narrowing direction, one `chmod`
undoes it, and the alternative is republishing the findings to whoever holds the other name.

## What this scanner does NOT promise

Several rounds of adversarial review converged on a set of claims that were wider than anything in
this position can deliver. They are stated here as limits rather than quietly left as bugs,
because an overstated guarantee is worse than an absent one.

**A held descriptor pins an inode, not a place in the tree.** The scanner opens `_reports/`,
validates it, and performs every later operation against that descriptor — which stops the name
being re-resolved through a symlink. It does NOT stop the directory itself being renamed. Review
moved the hardened directory outside the supplied root between validation and publication and the
report was written into it, still owner-only, still the directory that was checked, now somewhere
else. **Precondition: the staging tree and its ancestors must not be writable by anyone you are
defending against.** Hardening `_reports/` to 0700 does nothing about a 0777 directory above it,
and this tool does not modify ancestors it was not asked to create.

**Findings are protected from a REFUSAL, not from a later clean scan.** A refusal never replaces a
findings report without preserving it first. But a SUCCESSFUL scan ends the generation: it
publishes its own result and sweeps the reserved names, including preserved findings from an
earlier run. Running the scanner again on a cleaned tree therefore discards the previous run's
evidence, deliberately — a report directory describes one scan, not a history. If you need the
history, copy it out.

**Preservation capacity is finite.** There are eight preservation names and eight quarantine
names. If every one is occupied by something the scanner may not remove — a populated directory,
a file owned by somebody else — preservation fails, and the scanner then declines to replace the
findings rather than destroying them. That is the safe direction, but it means a planted set of
names can stop the report being updated. Occupancy is a denial of service against publication,
never a way to make the scanner destroy evidence.

**"Never raises" means never raises an error. It does not mean uninterruptible, and "never
blocks" is a bounded claim.** The refusal writer will not let its own failure displace the failure
it was called to report. It does not catch `KeyboardInterrupt` or `SystemExit`, and it should not:
a cancellation is not a refusal to report, and swallowing one would be a different defect.

On blocking, the precise statement: this path opens nothing that can wait for a peer — every open
that could meet a FIFO carries `O_NONBLOCK` or is `O_PATH` — and it renders no arbitrary object.
It used to call `str()` on the refusal it was handed, which review showed is unbounded: an
exception whose `__str__` never returns cannot be caught, because catching handles raising and
not waiting. Classification now reads a validated reason code the refusal carried from its own
raise site. What remains is ordinary synchronous filesystem latency, which no user-space tool can
promise away.

**A CONCURRENT WRITER IN THE REPORT DIRECTORY BEATS EVERY CHECK IN THIS TOOL.** This is the
widest limit here and the one that bounds several of the others, so read it before relying on any
promise above. The scanner checks a name and then acts on it — preserves, replaces, unlinks — and
between those two instants another process running as the same user can rename a different file
onto that name. Review reproduced five such schedules. The refusal can replace a canonical report
it never preserved, because the report it preserved was superseded after preservation and before
publication. A classification can describe one inode while the reserved name it was read through
holds another. Quarantine can establish custody by linking and then remove the source after that
custody has been moved out from under it. In each case the loss is caused by a rename the scanner
never sees, acting on a name the scanner believed it had just inspected.

**No amount of re-checking fixes this, and adding one would be the wrong repair.** Another `lstat`
before the destructive call only narrows the interval; the review that found these was explicit
that "adding one more lstat or deleting an uncertain reserved name would repeat the same failure
class". What a check CAN do is remove the cases that need no adversary, and two of those were
closed rather than documented: the refusal writer no longer unlinks the canonical name at all, and
a successful scan's sweep no longer removes a reserved file created after this run staged its own
report. What remains needs a writer who is actively substituting names.

Two more things were measured after that paragraph was written, and both belong here. First, the
scanner no longer links by name for an inode it HOLDS: every reserved name it creates from a held descriptor is made through the
descriptor directory (`linkat` on `/proc/self/fd/N`, the documented unprivileged form), which
attaches the inode the scanner holds or fails — it cannot attach something a writer put at the
name in between. That closes the "reserved name holds a decoy" family for those names. The one link still made by name is preservation's first link of the canonical name into a slot, which is then re-opened and compared with the inode preservation recorded; a slot that is not that inode declines the whole operation. It does not close
the replace: `os.replace` still acts on the canonical NAME. Review reproduced the gap that left:
preserve report A, substitute a new findings report B at the canonical name while the refusal is
being staged, and the refusal replaced B — a report the scanner never preserved. A guard for A
cannot authorize deleting B, so the guard now names what it authorizes: preservation records the
identity of the canonical inode it classified (or its confirmed absence), and the replace is
refused when the name no longer reaches that inode. That closes substitution BY NAME between
preservation and the re-check. It does not cover a writer that rewrites the SAME inode in place
after it was classified — an inode identity is not a content stamp — and it does not cover the
interval between the re-check and the rename itself, which is the same one-syscall gap as before.
Second, the one-syscall
primitive that would close the replace as well exists — `renameat2(2)` with `RENAME_EXCHANGE`,
which swaps two names atomically so the old inode is never nameless — and is not used here yet;
adopting it is a platform decision (Linux ≥ 3.15, filesystem support probed at runtime, a small
`ctypes` shim because `os.replace` exposes no flags) and is recorded as the next step rather than
claimed.

Also not a concurrency limit: **the access-policy guarantees are for POSIX-ACL filesystems with the
xattr API.** Where that API is absent the scanner cannot verify that a preserved copy carries no
ACL, so it answers "not verified": a stale CLEAN is still replaced by a refusal, but a FINDINGS
report is never replaced there — it is left standing, unnarrowed beyond its mode, which is the safe
direction. One preservation slot is used per distinct report by any one run, never more; two runs preserving the same report at the same moment can each take one, which is the concurrent same-UID writer limit above — what that costs is slot capacity, never the findings.

**The by-descriptor rescue needs a descriptor directory and a creatable temporary name.** When a staged or held name has stopped naming the bytes the scanner holds, the last resort copies them out through `/proc/self/fd/N` (or `/dev/fd/N`) into a fresh temporary name. On a system with neither directory, or when no temporary name can be created, that copy cannot be made, the function says so by answering False, and the close that follows frees the descriptor's inode. That is the one case in which bytes this scanner held are not on disk afterwards; it is stated here because two comments used to read as if the copy-out were unconditional.

Also not a concurrency limit: **a leftover whose ACL strip was denied keeps its inherited entries.**
Findings the scanner could not publish or reserve are left under the temporary `.scan_report_*`
prefix, narrowed as far as it can — mode 0600, so any inherited named-user or named-group entry is
masked to nothing. A strip that was DENIED cannot be redone here; those entries stay on the inode,
one `chmod` away from being live again. That is exactly why a reserved name (which asserts the
policy) is never taken in that state, and why the leftover claims nothing.

Third, and not a concurrency limit: **nothing here is fsync'd.** The stage is written, linked and
renamed with ordering guarantees only. A power loss or crash between the rename and the
filesystem's own commit can leave the previous report, an empty report, or no report — whatever
the filesystem's crash semantics give a rename-over. Durability of the report is not promised.

**Precondition, therefore: the report directory must not be writable by anyone you are defending
against, and no second writer should be publishing into it concurrently.** The scanner hardens
`_reports/` to `0700` on creation, which covers group and other. It cannot cover another process
running as YOU — a second scanner instance, a script tidying the tree, an editor writing through
a temporary file. Serialising those is the caller's job; a lock this tool took would be advisory
and a non-cooperating writer would ignore it.

**These gates are STAGING-side, not CI.** Public CI runs the hermetic suite, the guard layer,
`ref_gate.py` and `readme_guard.sh` only. `wall_check.py` and `scan_gate.py` run here, before a
batch is pushed — that is the point: a secret is caught before it lands, not after. `wall_check.py`
additionally reads the provenance ledger at `_reports/provenance.tsv`, which is gitignored and
deliberately NOT published (it records private source paths). So `wall_check.py` is present in a
clone but cannot pass from one — it is not a check a downstream user is expected to run.

Two scanners ship in this repo, and they do not have the same reach. guard/scrub_arm.py reads the
bytes of each file (UTF-8, then UTF-16 in both byte orders when NUL bytes are present, then
latin-1) and picks up the printable ASCII runs inside genuinely binary bytes; _tools/scan_gate.py
opens each file decoded as UTF-8 with decode errors ignored and has no byte view at all, so a
wide-encoded payload is invisible to it. On four of the five surfaces the two scanners agree:
contents: covered; filenames and paths: covered by the name arm, as a separate case from
contents; compressed payloads: NOT covered (bytes are read as stored); git history: NOT covered. They differ on binaries: covered for
guard/scrub_arm.py, as the printable ASCII runs inside them, NOT covered for _tools/scan_gate.py.
On git history neither scanner reaches: each scans one tree, so a value removed in a later commit
is still published by the earlier one. ref_gate.py is the instrument for that surface.
