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

## Reserved filenames in `_reports/`

These names belong to the scanner, which DELETES them after any successful publication regardless
of who wrote them:

    scan_report.txt
    scan_report.superseded.txt
    scan_report.superseded.1.txt  …  scan_report.superseded.7.txt

The numbered names became scanner-owned when the preservation slot was widened from one name to
eight. Gate review measured an ordinary pre-existing file at `scan_report.superseded.1.txt` being
destroyed by a clean scan. A filename does not establish provenance, so the reservation is stated
here rather than assumed — do not keep anything you care about at these names.

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
