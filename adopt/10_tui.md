# 10 — Configure `fleet_tui`

`fleet_tui` is a file-reading Textual monitor, not an orchestrator. Its sources must remain headless and safe-default: absent or malformed state files produce empty or `n/a` panels rather than a crash. It does not require a GPU, model runner, cloud CLI, or a second box.

## Step 1 — create the isolated Python environment

**PREFLIGHT — Debian/Ubuntu ship `venv` without `pip`.** On Debian-family systems `python3-venv` is a
separate package, and without it `python3 -m venv` succeeds but produces an environment with no `pip`,
so the next command fails with `No module named pip`. Check before building the environment:

**ADOPTER COMMAND:**

```bash
python3 -c 'import ensurepip; print("ensurepip-ok")'
```

**VERIFY — expected output:**

```text
ensurepip-ok
```

If instead you see `ModuleNotFoundError: No module named 'ensurepip'`, do NOT work around it by
guessing. Show the human this remedy and let them decide:

```text
Debian/Ubuntu:  sudo apt install python3-venv
Fedora/RHEL:    sudo dnf install python3-devel
Alternative (no install): python3 -m venv --system-site-packages tui/.venv
```

The alternative reuses system packages instead of installing into an isolated environment; note that
trade-off to the human rather than choosing it silently.


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
if [ -e "$HOME/.fleet_tui/boxes.json" ] || [ -L "$HOME/.fleet_tui/boxes.json" ]; then
    echo "$HOME/.fleet_tui/boxes.json exists; not overwriting" >&2
else
    cp tui/docs/boxes.example.json "$HOME/.fleet_tui/boxes.json"
fi
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

Run the TUI suite as a non-root Linux user without `CAP_SYS_ADMIN` or
`CAP_SYS_RESOURCE`. Setup refuses root or either capability with the exact failure:
`Failed: TUI process isolation requires non-root without CAP_SYS_ADMIN/SYS_RESOURCE`.
This refusal preserves the kernel process boundary; do not bypass it to run the suite.


**ADOPTER COMMAND:**

```bash
cd tui && .venv/bin/python -m pytest -q
```

**VERIFY — expected output:** pytest must exit `0` and report `1179 passed` on Python 3.14, or `1178 passed, 1 skipped` on Python 3.11/3.12 because Unicode array typecode w is unavailable. These versions describe the same 1179 cases. Baseline Python 3.13 skips the unwritten Unicode 15.1.0 census; Python 3.13 execution at this revision is unmeasured. Retain the actual output and interpreter's readline backend; a different result is a blocker, and collection alone does not verify the TUI.

## Step 5 — launch only after the acceptance run

**ADOPTER COMMAND:**

```bash
cd tui && ./run.sh
```

**VERIFY — expected outcome:** `MANUAL: in an interactive terminal, the monitor opens; missing state files render degraded cells rather than terminating the process. A noninteractive shell cannot confirm the rendered interface.`

External input setup is fail-closed: follow [Point the TUI at your fleet](../tui/README.md#point-the-tui-at-your-fleet), copy and edit `tui/paths.example.json`, or set `FLEET_TUI_<KEY>`; absent keys display `not configured: <key>`.

The canonical count above uses Python 3.14. Python 3.11/3.12 collect the same
cases and skip the unavailable Unicode array typecode w case. Baseline Python
3.13 skips the unwritten Unicode 15.1.0 census; native 3.13 collection for
this revision is unmeasured.


Passing startup scenarios remove their compact input and child pytest tree;
a passing startup-observation module then removes its remaining scratch.
A passing suite still leaves two `fleet-tui-startup-*` trees from the deliberate
initialization/finalizer storage-error fixtures; they preserve negative-test
evidence until a person removes them. To retain raw startup
evidence, pass `--keep-startup-artifacts` with a persistent destination.
Without `fleet-data-path`, also supply `--startup-artifacts-root`; the flag
alone refuses explicitly before allocating startup evidence. When installed, `fleet-data-path
test-scratch-kept <run>` supplies the kept output root; an explicit
`--startup-artifacts-root` must agree and must be outside the repository.
Failed scenarios and failed modules retain their evidence in unnumbered
`fleet-tui-startup-*` directories beside pytest basetemp, outside numbered
rotation and the isolation sibling sweeper. These remain until a person removes
them. Initialization and later storage errors retain the HOME sandbox too.
The sticky initialization veto is supplemented by a one-use helper permission
published before payload. Retention revokes that helper through its preallocated
pidfd without a filesystem write; normal cleanup revokes before removing anything,
and failed retirement consumes permission before its first deletion. Later
sweeps name the retained tree and cannot authorize another cleanup. Older
three/five-field records are retained without attempting to reinterpret them.
Consumers of receipt JSON must request
retention; ordinary passing runs keep receipt digests in memory.

On an earlier internal runtime/test freeze, a passing
Python 3.14.4 run sampled **532,606,976 B peak** and **569,344 B residue** at exit,
including deliberately retained storage-error test evidence. Total-footprint
sampling runs every five seconds. A separate one-second observer recorded the
dominant `structural-cells.json` at **409,190,559 logical bytes**
(409,194,496 allocated). Earlier passing samples on internal builds ranged from
142,360,576 B to 532,303,872 B; the smaller sample missed the brief large
structural file. These are sampled observations, never continuous maxima;
the structural coverage test was preserved. These figures describe that earlier
internal runtime/test revision. Subsequent revisions changed the test file and its
AGENTS.md contracts, including source-location controls, main-process scrub
controls and retirement-failure controls; the isolation runtime remained
byte-identical. The figures are historical measurements of that earlier internal freeze,
not a footprint measurement of the later tests.
The current footprint remains a
large reduction
versus 10,019,028,992 bytes for both at suite exit before the repair and harness cleanup.
Sampling was every five seconds, so this is a sampled peak, not a continuous
maximum. The release gate permits at most 2 GiB peak and 512 MiB residue after
a passing run. Reserve 3 GiB per ordinary run as an operating budget (the peak
and residue limits plus 512 MiB margin); this budget is not a measured minimum.
Budget at least 25 GiB for retained evidence or the pre-repair reproduction;
the small passing-run footprint does not bound failing or kept artifacts.
Run the footprint check in [Verify all](90_verify_all.md) and retain its actual
peak, residue, test summary and exit code. Explicit `--basetemp` skips pytest's
session-end base-directory removal and numbered-directory rotation; under the
`failed` policy, passing tests' `tmp_path` directories are still removed.
Remove only that run's tree after its receipt and any required archive are written.
