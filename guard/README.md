# guard/ — drift guards for a multi-stage research pipeline, mutation-proven

Adapted from a shipping desktop application's `_breaker/` verification stack. The transferable
part was the META-harness: machinery that keeps invariants honest, not the invariants themselves.

## The discipline, in one paragraph

A green check proves nothing until the check has been watched failing. So the **teeth-prover runs
first** and plants real defects to confirm every guard can go red (`HAS_TEETH` / `OVERBROAD` /
`VACUOUS` verdicts). Exit codes everywhere: `0` clean · `1` violation · `2` UNMEASURED — and
**2 dominates 1**, because a check that did not run can hide any number of violations beneath it.
Until 2026-08-03 the leg-liveness dry-run wrote fabricated ALIVE state and reported a PASS; the
staleness check could never fire. That defect is why the dry run now returns `2` and says so.

## What runs from a fresh clone (no private data needed)

| Layer | Command | Expectation |
|---|---|---|
| Teeth-prover | `python3 guard/teeth_prover.py` | 10 planted mutations; every guard proves it can fail |
| Contract agreement | `python3 guard/contract_agreement.py` | all four vocabulary surfaces agree (validator · addendum · rollup · preamble) |
| Guard unit gates | `pytest guard/tests/ -q` | 590 tests, hermetic (one is environment-gated: it skips without `PASSBACK_OUTBOX`; one is a strict xfail that records a known gap) |
| Documented counts | `python3 guard/doc_count_drift.py` | every count written into prose or the banner matches what it describes |
| Rendered banner | `python3 guard/banner_render.py` | the PNG keeps its transparent corners and was rendered from the SVG in the tree |
| Full runner | `guard/run_guards.sh` | the above in order; leg-liveness dry-run returns `2 = UNMEASURED` by design |

### Regenerating the banner

**Run `docs/render_banner.sh`. Do not render the banner by hand.**

The banner SVG has rounded corners, which survive into the PNG only if the render keeps a
transparent ground. A headless browser defaults to an opaque white page: it silently drops the
alpha channel and paints those corners white, which on a dark README reads as four white notches.
The output is still the right size, still the right picture, and still commits cleanly — nothing
about it looks wrong except the thing you were not looking at.

That happened twice. Both times the render was retyped from memory and
`--default-background-color=00000000` was the flag that went missing. The second time it survived a
positive control, because the control render carried the flag and the shipped render did not —
proving a renderer works is not the same as proving the command you shipped with works.

So the invocation lives in the script and the property is checked by the guard, which also records
which SVG the PNG came from. Edit the SVG without re-rendering and the guard goes red on staleness
rather than letting a stale image ship.

## Activating the publication hook

The tracked hook source in `guard/hooks/` is not active in a clone. Git does not run hooks from
the work tree, and nothing in the test suite installs one: the fresh-clone checks above run on
synthetic data so that they can pass anywhere. Activation is a separate procedure with two policy
inputs that never travel with a clone, and it has to be repeated in every clone that will push.

The steps below were run end to end in an isolated scratch repository (a fresh source repository,
a disposable bare target, synthetic identities); every quoted output line is one that run printed.

### 1. Prerequisites

The hook itself requires Bash 4+, Git, Python 3 and `tar` (its header says so). The recipe here
also uses `git rev-parse --path-format=absolute`, which needs Git 2.31 or later (the installer falls
back to joining the repository root with the relative path on older Git; the manual parity check in
step 5 does not). The installer uses `install`, `sha256sum` and `cmp`. All of these are taken from
`PATH`.

Two policy inputs are provisioned separately and stay private to the clone:

- `_tools/identity_terms.txt` — the terms the scanner treats as personal data. Gitignored.
- an approved-identity list — the emails a commit may carry. In this recipe that is the repo-local
  Git config key `fleetops.approvedIdentity`; the alternatives (a file or an environment variable)
  are described in step 3, because they take precedence over it.

Both fail closed: with either one missing, the installer refuses to install and an installed hook
refuses to push.

### 2. Provision the two policy inputs

Scanner identity terms:

    cp _tools/identity_terms.example.txt _tools/identity_terms.txt

Then edit the copy: one term per line, for each class the example file names (personal and account
names; machine nicknames and short hostnames, including any alias used in benchmark or log
annotations; LAN domain suffixes; personal email local-parts). The file must be nonempty after
comments. Never commit it — `.gitignore` already lists it, and `git status` must not show it. The
scanner's own self-test derives its planted identity from the first term in the file;
regex-special characters such as `-` or `.` are accepted in that first term.

Approved identities. Inspect what is already configured before adding anything:

    git config --local --get-all fleetops.approvedIdentity

Exit status 1 with no output means nothing is configured locally. Add the email that commits will
carry. This is a literal template: replace `<owner-email>` before running it, and do not add a value
the previous command already printed (`--add` appends; a duplicate adds nothing):

    git config --local --add fleetops.approvedIdentity '<owner-email>'

Matching is exact (`[ "$entry" = "$1" ]` in the hook): no case folding, no wildcards, no
`Name <email>` form — the bare email. Every commit in the push must satisfy it on three surfaces:
the author email, the committer email, and the email in any `Co-authored-by:` or `Signed-off-by:`
trailer. See what a commit actually carries with `git log -1 --format='%ae %ce'`. A GitHub handle
(`<owner-gh>`) is not automatically the right value; the noreply address GitHub offers is an email
like any other and has to be listed if commits carry it.

### 3. Where the hook reads identities, and why the installer refuses shadows

The hook (`guard/hooks/pre-push`) selects its identity source in this order:

1. the file named by `FLEETOPS_APPROVED_IDENTITIES`, when that variable is set;
2. otherwise the default file `_tools/approved_identities.txt`;
3. only when the selected file is absent or holds nothing but comments and whitespace,
   `git config --get-all fleetops.approvedIdentity`.

So a nonempty file silently overrides the config, with no change to the hook. The config route
activated here (`--pre-push-config` and `--check-pre-push-config`) therefore refuses, before
touching anything, when:

- `FLEETOPS_APPROVED_IDENTITIES` is nonempty (`refused: FLEETOPS_APPROVED_IDENTITIES is set ...`);
- `_tools/approved_identities.txt` exists in any form — a file, an empty file, a symlink, a
  dangling symlink (`refused: _tools/approved_identities.txt exists ...`);
- no nonblank `fleetops.approvedIdentity` is in the repository's `--local` config;
- any identity Git resolves for that key from any scope is absent from the repository's `--local`
  config (`... inherited from outside this repository's --local config ...`): every resolved
  identity must also be present locally; an inherited value already present locally is accepted.

The refusal names the category, never a value. When one of these fires and the file or variable is
not something this procedure created, do not delete or unset it to make the check pass: it is
someone's policy, possibly the one actually meant to govern this clone. Find out why it is there
and select the intended source; that decision is outside this recipe. The `.gitignore` entry for
`/_tools/approved_identities.txt` only keeps such a file out of commits — it neither provides a
policy nor removes one.

### 4. Inspect the effective hook path, then install pre-push only

Git may run hooks from somewhere other than `.git/hooks` (`core.hooksPath`, linked worktrees).
Ask Git, and look at what is already there:

    git config --show-origin --get core.hooksPath      # exit 1 and no output: unset
    resolved_hook=$(git rev-parse --path-format=absolute --git-path hooks/pre-push)
    ls -l "$resolved_hook"

A pre-push hook that already exists there with different content stays where it is: the installer
refuses (`refused: a different hook already exists at ...; rerun with --pre-push-config --replace
... Nothing was changed`). Decide what that hook is for before choosing between removing it
yourself and `--replace`, which first backs it up (bytes and mode) to a file in
`fleetops-hook-backups/` under the repository's Git common directory and prints two lines,
`backup: <path>` and `restore: install -m <mode> '<backup>' '<hook>'`, and only then installs. A
symlinked destination or hooks directory is refused outright. The installer never edits
`core.hooksPath`.

Install:

    bash guard/hooks/install.sh --pre-push-config          # add --replace only once the existing hook's disposition is decided

Success prints the selected source category, the effective path, and one line beginning
`installed <path> (guard/hooks/pre-push, mode 755, parity verified by sha256sum and cmp; identity
route: git-config)`. Exit status: 0 installed · 1 refused (nothing changed; stderr says why) · 2
usage error. Only `pre-push` is written. Running the installer with no arguments is the older route
and installs every hook in `guard/hooks/` (currently `commit-msg` and `pre-push`) after the same
git-config identity and scanner preflight. A failed preflight writes no hooks. Use the explicit
`--pre-push-config` route above to install only pre-push and preserve other existing hooks.

### 5. Check before every push

    bash guard/hooks/install.sh --check-pre-push-config

This repeats the step 3 preflight and then verifies the installed file, read-only: it must be a
regular file, executable, with the same `sha256sum` as `guard/hooks/pre-push`, and byte-identical
by `cmp -s`. Success ends with `parity: OK — <path> is executable and byte-identical to
guard/hooks/pre-push`; any failure is `refused: ...` with exit 1 and nothing repaired. The same
comparison by hand, in the clone being checked:

    resolved_hook=$(git rev-parse --path-format=absolute --git-path hooks/pre-push)
    sha256sum guard/hooks/pre-push "$resolved_hook"
    cmp -s guard/hooks/pre-push "$resolved_hook" && test -x "$resolved_hook" && echo "parity: identical and executable"

The `sha256sum` line prints two digests; that is a display, not a comparison, and two digests
with no compared result are not a parity check. The `cmp -s` line is the comparison, and `test -x`
is the check that Git will run the file at all. A one-byte drift in the installed copy and a
removed execute bit each make the check refuse.

The check describes one moment. Run it again after anything that could change what it examined:
the tracked hook changing (a pull that touches `guard/hooks/pre-push` leaves the installed copy
stale), the policy changing, `core.hooksPath` changing, a new shell or environment (a profile that
exports `FLEETOPS_APPROVED_IDENTITIES`), and before each push. The hook itself is unchanged by all
of this and keeps the file-first precedence from step 3: a `_tools/approved_identities.txt` created
after the check is selected by the very next push, and a push made without running the check gets
no warning. Passing this check does not establish that the config remains the source afterwards.

### 6. Rehearse in an isolated repository first

There is no rehearsal document under `docs/`; the rehearsal is small enough to describe here. Use an
isolated synthetic repository with a one-commit clean history and a disposable bare target — never
a real remote. From the package root, create that history with these two shell lines:

```bash
package=$PWD; rehearsal=$(mktemp -d); git init -b main "$rehearsal/source" && git init --bare "$rehearsal/target.git"
git -C "$package" archive HEAD | tar -x -C "$rehearsal/source"; cd "$rehearsal/source" && git config user.name Fixture && git config user.email fixture@example.invalid && git add . && git -c commit.gpgsign=false commit -m 'Synthetic clean baseline'
```

The archived tree must pass the scanner with the synthetic policy below. Push `main` to
`"$rehearsal/target.git"` for the following checks. A first push of the real clone to an empty target
selects all reachable history, not just this clean tree; the one-commit expectation applies only
to the synthetic repository. Expect these states:

- Provision synthetic inputs: a repo-local `fleetops.approvedIdentity` of `fixture@example.invalid`
  (also the commit author and committer), and a terms file whose first term
  does not occur in the clean content. Confirm that removing the config, and separately emptying
  the terms file, each make `--check-pre-push-config` refuse; then restore them.
- Install (step 4), check (step 5).
- Clean acceptance: an ordinary commit pushed to the empty bare target succeeds with exit 0, the
  target's ref advances, and the hook prints `pre-push gate: CLEAN — scanned 1 distinct selected
  commit tree(s) ...`. A no-op push (nothing to send) is not this control.
- Planted refusal: commit a file in an ordinary tracked path containing a key-shaped literal
  (assemble it at run time, e.g. `api_key = '<24 alphanumerics>'`), confirm
  `--check-pre-push-config` still passes (policy and parity are intact), then push. Expected:
  `PRE-PUSH BLOCKED: scanner rejected commit <sha>'s archived tree ...`, exit 1, the target's ref
  unchanged. `_tools/` is scanned, so a plant there also refuses. Do not plant under a directory
  the scanner skips (`_reports/`, `.git/`, caches — the list is in `_tools/scan_gate.py`'s
  docstring): such a plant is accepted, which proves nothing about the scanner. Drop the planted
  commit before the next clean push.
- Identity refusal: a commit whose author, or whose `Co-authored-by:` trailer, is
  `outsider@example.invalid` is refused with `PRE-PUSH BLOCKED: <sha> carries a non-approved ...`.

Two behaviours to know before the first real push:

- The hook refuses a shallow repository (`PRE-PUSH BLOCKED: shallow history cannot establish the
  commit range.`). Clone without `--depth`, or run `git fetch --unshallow` first.
- A first push to an empty remote has no destination tip to diff against, so the hook scans every
  commit reachable from the pushed ref, and every one of them must pass. A source tree whose older
  commits contain deliberate scanner literals — fixtures, examples — is refused even when HEAD is
  clean, and the refusal names the introducing commit. That refusal is correct: those bytes would
  be published. Do not waive it, disable the scanner, or bypass the hook. Whether a real
  repository's outgoing history is acceptable is a separate measurement on that repository; the
  rehearsal above does not establish it.

### 7. Rollback

Restoring the hook and restoring the policy are two different actions.

- A hook that was replaced: run the `restore:` line the installer printed, verbatim; its shape is
  `install -m <mode> '<backup>' '<hook>'`. Then `sha256sum '<backup>' "$resolved_hook"` and
  `cmp -s` the two; the digests must match, and `--check-pre-push-config` must now refuse, because
  the restored file is not the tracked hook.
- A hook that did not exist before installation: confirm it is still the tracked copy, then remove
  only that file — `cmp -s guard/hooks/pre-push "$resolved_hook" && rm -- "$resolved_hook"`. Leave
  every other hook in the directory alone.
- Neither action touches `fleetops.approvedIdentity` or `_tools/identity_terms.txt`. If the policy
  was provisioned only for this activation, remove it as a separate, deliberate step
  (`git config --local --unset-all fleetops.approvedIdentity`; delete the terms file); if it was
  there before, leave it. Restoring a hook never restores a policy, and removing one does not
  remove the other.

## What fail-closes without private data — deliberately

`mutation_harness.py` sandboxes the code under test, applies surgical mutations, and asserts the
paired guard goes red (`KILLED` / `SURVIVED` / `ABSTAINED` — a guard that declined to assert
anything did not catch the bug). Its baseline check pins measured corpus sizes; without the private
measurement corpus it **aborts before mutating anything** — a harness that cannot reproduce the
clean baseline refuses to certify mutations against it. That refusal is the integrity rule, not a
missing feature. A synthetic public corpus is planned.

## Provenance note

During export, this directory's own gates caught the exporter twice: a sanitization pass made the
contract surfaces cwd-relative and the `isabs()` unit gate refused it; the mutation harness refused
its baseline in the corpus-less tree. Guards that police their own maintainers are the point.

The ref gate defaults to `refs/heads/main`; adopters publishing another branch can set `git config fleetops.publishRef refs/heads/release` before running `python3 _tools/ref_gate.py .`. Other local publishing refs still fail the gate.

For the honesty stop hook on a host with `ps` and `pgrep`, copy `guard/honesty_gate.config.minimal.example.json` to `guard/honesty_gate.config.json`, then run `python3 guard/honesty_stop_gate.py --check-config`; this process-only example inherits the claim and subject defaults and avoids optional service/container binaries. Adapt the config to the subjects and probes actually used on your host.

Both activation routes explicitly stop after a failed identity-config or scanner self-test preflight, even when shell errexit is disabled. The read-only check uses the same explicit refusal.

`fleetops.publishRef` is trimmed before validation. Unset, empty, and whitespace-only values use `refs/heads/main`. A nonblank value must start with `refs/` and pass `git check-ref-format`; a short name such as `main` or an invalid ref is refused with one line and status 1. Surrounding whitespace around a valid full ref is accepted. `refs/original/` remains reserved for refused rewrite leftovers and cannot be selected as a publishing ref.

The existing `HONESTY_GATE_CONFIG` environment variable selects an alternative settings file. `guard/tests/test_honesty_example_config.py` copies the shipped minimal example there and checks acceptance with only process probes available; removing that copy restores defaults and refuses the unresolved optional commands. The test controls command availability and does not probe live services.


## Instance policy and external archive paths

- `_tools/wall_check.py`: this file encodes the authors' policy; adopters replace the table at lines 22–36.
- `_tools/readme_guard.sh`: this file encodes the authors' policy; adopters replace the table at lines 8–12.
- `guard/leg_canary.py`: this file encodes the authors' policy; adopters replace the table at lines 44–49. Its `_default_runner` wrapper conventions and `main` cron PATH must be adapted to the same roster; the literal tilde PATH is retained as instance policy, not a portable setup.

`vision_ingest.py` takes external paths only from `VI_BACKUP_ROOT`, `VI_BACKUP_MOUNT`
and optional `VI_ALERT_SCRIPT`. Export edited values from the authors' instance in
`docs/vision_ingest.example.json`; that file is documentation, never auto-loaded.
Without both backup settings, archive prints `not configured` and leaves primary
files in place. The root must lie under the configured mount, which must be mounted.
Hash read-back verification and retention on corruption remain enforced. An unset
alert script disables that optional notification only; it does not change the verdict.
