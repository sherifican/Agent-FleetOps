# 10 — Configure `fleet_tui`

`fleet_tui` is a file-reading Textual monitor, not an orchestrator. Its sources must remain headless and safe-default: absent or malformed state files produce empty or `n/a` panels rather than a crash. It does not require a GPU, model runner, cloud CLI, or a second box.

## Step 1 — create the isolated Python environment

Read `adopt-scratch/inventory.md`; do not choose a runner or endpoint here. From the repository root, create the environment and install the TUI's declared runtime plus development dependencies.

**ADOPTER COMMAND:**

```bash
python3 -m venv tui/.venv
./tui/.venv/bin/python -m pip install -e 'tui[dev]'
./tui/.venv/bin/python -c 'import fleet_tui; print("fleet_tui-import-ok")'
```

**VERIFY — expected output:**

```text
fleet_tui-import-ok
```

If `python3 -m venv` or dependency installation is unavailable, stop and show the error to the human. Do not replace the declared dependencies with guessed versions.

## Step 2 — choose the minimum configuration

`~/.fleet_tui/boxes.json` is optional. The shipped reader accepts either a top-level list or an object with `boxes`. Every configured box needs `name` and `kind` (`local` or `remote`). Optional local-file relay fields are `receipts_path`, `models_path`, `health_path`, `ledger_path`, `downloads_path`, and `throughput_path`. `device_labels` maps a device key to `badge`, `color`, and `power_cap_w`.

For the minimum single-box path, create no configuration file. The shipped `read_boxes()` implementation returns one usable box named `local` when the file is absent or malformed. Its local readers may show missing data as `n/a`; that is the intended degradation behavior.

**ADOPTER COMMAND:**

```bash
./tui/.venv/bin/python - <<'PY'
from pathlib import Path
from fleet_tui.sources.boxes import read_boxes
probe = Path('adopt-scratch/absent-boxes.json')
print(read_boxes(probe))
PY
```

**VERIFY — expected output:** a one-element representation whose box name is `local`.

## Step 3 — configure an observed multi-box relay only when needed

If the inventory and the human-approved plan identify locally available relay files, copy the shipped neutral schema as a starting point. Replace each example path only with a path observed on the adopter's host. A `remote` box is still file-only: it reads locally mounted or relayed files and does not create a network connection.

**ADOPTER COMMAND:**

```bash
mkdir -p "$HOME/.fleet_tui"
cp tui/docs/boxes.example.json "$HOME/.fleet_tui/boxes.json"
${EDITOR:-vi} "$HOME/.fleet_tui/boxes.json"
./tui/.venv/bin/python - <<'PY'
from fleet_tui.sources.boxes import read_boxes
for box in read_boxes():
    print(f'{box.name}\t{box.kind}\tlabels={len(box.device_labels)}')
PY
```

**VERIFY — expected output:** one line per valid configured box with `local` or `remote` in the second column. Invalid rows are ignored and an empty or malformed file falls back to one `local` box.

**HUMAN GATE:** show the proposed `boxes.json` diff before saving it if it introduces relay paths outside the repository or changes an existing operator configuration.

## Step 4 — run the hermetic acceptance suite

**ADOPTER COMMAND:**

```bash
cd tui && .venv/bin/python -m pytest -q
```

**VERIFY — expected output:** pytest exits `0`; this export's acceptance run reports `364 passed`. A different result is a blocker: retain the output and do not describe the TUI as verified.

## Step 5 — launch only after the acceptance run

**ADOPTER COMMAND:**

```bash
cd tui && ./run.sh
```

**VERIFY — expected outcome:** `MANUAL: in an interactive terminal, the monitor opens; missing state files render degraded cells rather than terminating the process. A noninteractive shell cannot confirm the rendered interface.`
