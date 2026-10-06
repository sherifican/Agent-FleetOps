# 90 — End-to-end acceptance record

Run this only after the earlier documents have produced their artifacts. Read the actual output, fill the table, and show it to the human. `UNMEASURED`, a missing artifact, a failed command, or an unperformed manual check is not a clean result.

⚠ **This record is a SAMPLE, not full coverage — do not read a clean run as "everything adopted".**
The assertions below deliberately check a few cheap landmarks. They do **not** verify, among others:
`adopt-scratch/plan.md` (written in `00_inventory.md`); the `proposals` and `rejects` directories
alongside `triggers` (`40_protocols.md`); that skills were actually copied into your agent platform's
directory or that you authored a routing table in your own durable rule location (`20_skills.md`
steps 2-3 — the check here only proves the file exists in the cloned repo); a *configured*
`~/.fleet_tui/boxes.json` (only the absent-file fallback is exercised); or the publication hook being
installed (`30_guards.md` — run `bash guard/hooks/install.sh --check-pre-push-config` yourself).
Verify those by hand, or extend this file.

## Ordered acceptance run

Run the TUI suite as a non-root Linux user without `CAP_SYS_ADMIN` or
`CAP_SYS_RESOURCE`. Setup refuses root or either capability with the exact failure:
`Failed: TUI process isolation requires non-root without CAP_SYS_ADMIN/SYS_RESOURCE`.
This refusal preserves the kernel process boundary; do not bypass it to run the suite.


For `python3 -m pytest guard/tests/ -q`, use CPython 3.12 or newer with pytest
installed in that interpreter; the fetch-gate teeth require `sys.monitoring`.
Follow the [canonical C8 scope and invocation rules](../guard/README.md#fetch-gate-c8-scope-clarification);
retain the printed result together with the exit status.

**ADOPTER COMMAND:**

```bash
test -s adopt-scratch/inventory.md
test -f skills/model-routing-table/SKILL.md
test ! -e .driver_lock && test ! -e .driver_halt
test -d adopt-scratch/curation/triggers
test -s adopt-scratch/system-map/00-host.md
./tui/.venv/bin/python - <<'PY'
from pathlib import Path
from fleet_tui.sources.boxes import read_boxes
print(read_boxes(Path('adopt-scratch/absent-boxes.json')))
PY
cd tui && .venv/bin/python -m pytest -q
cd .. && python3 guard/teeth_prover.py
python3 guard/contract_agreement.py
python3 -m pytest guard/tests/ -q
guard/run_guards.sh; printf 'guard-runner-exit=%s\n' "$?"
```

**VERIFY — expected output:** inventory, skill-manifest, clear-lock, curation-scaffold, and system-map checks must exit `0`; the box probe must print one `local` box; TUI pytest must exit `0` and report `1179 passed` on Python 3.14 (Python 3.11/3.12: `1178 passed, 1 skipped`, the unavailable Unicode array w case; baseline Python 3.13 skips the unwritten Unicode 15.1.0 census and all versions describe the same 1179 cases; Python 3.13 execution at this revision is unmeasured); teeth-prover, contract agreement, and guard tests must exit `0`; the default guard runner prints `UNMEASURED` and `guard-runner-exit=2` by design. Keep every literal output block and the TUI interpreter's readline backend. These are required results, not a record of an acceptance run.

## Fill before reporting

| Component | Evidence command / manual check | Actual exit or observation | Verdict | Human shown? |
| --- | --- | --- | --- | --- |
| Host inventory | `test -s adopt-scratch/inventory.md` |  |  |  |
| Skills source | `test -f skills/model-routing-table/SKILL.md` |  |  |  |
| Protocol scaffold | lock, curation, and system-map checks |  |  |  |
| TUI no-config fallback | Python box probe |  |  |  |
| TUI suite | `cd tui && .venv/bin/python -m pytest -q` |  |  |  |
| Guard teeth | `python3 guard/teeth_prover.py` |  |  |  |
| Guard agreement | `python3 guard/contract_agreement.py` |  |  |  |
| Guard tests | `python3 -m pytest guard/tests/ -q` |  |  |  |
| Guard aggregate | `guard/run_guards.sh` |  | `UNMEASURED` unless approved liveness probe ran |  |
| TUI visual launch | interactive `./run.sh` |  | manual / not performed |  |
| Services, cron, hooks | plan and diffs |  | manual approval required |  |

Do not convert an `UNMEASURED` row into `clean`. If a human declines an optional automation, record it as deliberately unconfigured, not as a failed setup.

The canonical count above uses Python 3.14. Python 3.11/3.12 collect the same
cases and skip the unavailable Unicode array typecode w case. Baseline Python
3.13 skips the unwritten Unicode 15.1.0 census; native 3.13 collection for
this revision is unmeasured.


Run the TUI footprint check once in a fresh directory outside the checkout.
The historical passing Python 3.12 footprint gate on an earlier internal build
sampled 478,490,624 bytes peak and 8,192 bytes residue. README and `adopt/10_tui.md`
describe a later internal runtime/test freeze,
on Python 3.14.4: 532,606,976 B sampled peak and 569,344 B residue, including
deliberately retained storage-error evidence. These different revision, interpreter
and sampling observations are historical measurements, not conflicting bounds; the pre-repair command
sampled 10,019,028,992 bytes for both at suite exit before harness cleanup. These are five-second samples of pytest's temporary tree, not
continuous maxima or a bound on kept/failing evidence. Budget 3 GiB per
ordinary run from the gate limits plus margin, or at least 25 GiB when keeping
raw startup evidence or reproducing the pre-repair result.

The following Linux/GNU shell check uses the same 2 GiB peak and 512 MiB
residue thresholds. Set `CHECKOUT`, `TUI_PYTHON` and `TEST_SCRATCH` to the
checkout, its dependency-equipped interpreter and an external output directory.
It leaves its own logs and temporary tree for review; after retaining the
summary, exit code and log digest (and any required archive), remove only the
fresh run directory it printed. Do not clean existing test trees to pass.

```bash
(
set -u
run=$(mktemp -d "$TEST_SCRATCH/footprint.XXXXXX") || exit 2
printf 'footprint output: %s\n' "$run"
mkdir "$run/tmp" || exit 2
(while :; do du -s -B1 "$run/tmp" | cut -f1; sleep 5; done) > "$run/samples" &
sampler=$!
(cd "$CHECKOUT/tui" && TMPDIR="$run/tmp" PYTHONDONTWRITEBYTECODE=1 \
 "$TUI_PYTHON" -m pytest -q -p no:cacheprovider) > "$run/suite.log" 2>&1
suite_rc=$?
kill "$sampler"; wait "$sampler" 2>/dev/null
peak=$(sort -n "$run/samples" | tail -1)
residue=$(du -s -B1 "$run/tmp" | cut -f1)
printf 'suite rc=%s; sampled peak=%s B; residue=%s B\n' "$suite_rc" "$peak" "$residue"
tail -3 "$run/suite.log"
sha256sum "$run/suite.log"
[ "$suite_rc" -eq 0 ] || exit 2
[ "$peak" -le $((2<<30)) ] && [ "$residue" -le $((512<<20)) ] || exit 1
)
```

A nonzero suite exit is an invalid footprint result, even when residue is
small. A passing suite above either limit is a footprint refusal. Record
this check alongside the TUI suite receipt in the verification table.
