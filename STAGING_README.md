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

These names belong to the scanner, which DELETES them after any successful publication regardless
of who wrote them:

    scan_report.txt
    scan_report.superseded.txt
    scan_report.superseded.1.txt  …  scan_report.superseded.7.txt
    scan_report.unpublished.txt
    scan_report.unpublished.1.txt  …  scan_report.unpublished.7.txt

The numbered names became scanner-owned when the preservation slot was widened from one name to
eight. Gate review measured an ordinary pre-existing file at `scan_report.superseded.1.txt` being
destroyed by a clean scan. A filename does not establish provenance, so the reservation is stated
here rather than assumed — do not keep anything you care about at these names.

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
