# 90 — End-to-end acceptance record

Run this only after the earlier documents have produced their artifacts. Read the actual output, fill the table, and show it to the human. `UNMEASURED`, a missing artifact, a failed command, or an unperformed manual check is not a clean result.

## Ordered acceptance run

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

**VERIFY — expected output:** inventory, skill-manifest, clear-lock, curation-scaffold, and system-map checks exit `0`; the box probe prints one `local` box; TUI pytest exits `0` and reports `364 passed` in this export; teeth-prover, contract agreement, and guard tests exit `0`; the default guard runner prints `UNMEASURED` and `guard-runner-exit=2` by design. Keep every literal output block.

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
