# Spec — RAM + swap monitor in the HEALTH panel

## Why

The owner's box hit "Device memory is nearly full. An application was forced to stop" on 2026-08-08.
The TUI shows CPU, GPU and SSD but has **no memory readout at all**, so the pressure was invisible
until the OS killed something.

Measured at the time of the incident:

    MemTotal      31733532 kB     (~30.3 GiB)
    MemAvailable  23884604 kB     (~22.8 GiB free -> RAM was HEALTHY)
    SwapTotal      8388604 kB
    SwapFree       1195656 kB     (~7.2 of 8.0 GiB USED -> swap nearly EXHAUSTED)

**A RAM-percentage-only monitor would have read "healthy" during the actual incident.** Swap exhaustion
was the real signal. Therefore this feature ships BOTH, and swap gets the more aggressive colour
thresholds. Do not drop the swap half.

## HARD constraint — there is NO RAM temperature on this box

The owner asked for a temperature tracker alongside usage. **This machine has no DIMM temperature
sensor.** `/sys/class/hwmon` exposes only `k10temp` (CPU), `nvme` (SSD) and two network adapters, and the
`jc42` SPD-temperature driver is not present.

So: **do NOT invent, estimate, derive, or placeholder a RAM temperature.** Do not reuse the CPU
temperature as a stand-in. There is no field for it in this spec. If a DIMM sensor ever appears it can be
added the same way `ssd_temp` was. A fabricated number on a monitoring panel is worse than a missing one,
because the owner would act on it.

## What to build in fleet_tui/sources/health.py

### read_meminfo() -> dict

Parses `/proc/meminfo`. Cached ~4s in the existing `_cached(key, ttl, fn)` helper already used by the
other readers in this module (see `read_disk` for the pattern). Reading a file is cheap, but the refresh
loop runs every 1s and every reader in that path must be cached.

Returns exactly these keys, all floats except the percents which are ints:

    ram_used_gb     GiB currently in use
    ram_total_gb    GiB installed
    ram_pct         int 0..100, percent of RAM in use
    swap_used_gb    GiB of swap in use
    swap_total_gb   GiB of swap configured
    swap_pct        int 0..100, percent of swap in use

Rules:
  - Parse the `MemTotal:`, `MemAvailable:`, `SwapTotal:` and `SwapFree:` lines. Values are in kB.
  - **Used RAM is `MemTotal - MemAvailable`, NOT `MemTotal - MemFree`.** MemFree excludes cache and
    buffers that the kernel will hand back on demand, so MemFree-based "used" massively overstates
    pressure on a box like this one, which routinely holds gigabytes of page cache.
  - Convert kB to GiB by dividing by 1048576.
  - `swap_pct` is 0 when `SwapTotal` is 0 (a box with no swap is not at 0% pressure by accident — it just
    has none). Guard the divide.
  - Missing keys, unreadable file, or garbage values must yield the all-zero default, never an exception.

### Safe default

    {"ram_used_gb": 0.0, "ram_total_gb": 0.0, "ram_pct": 0,
     "swap_used_gb": 0.0, "swap_total_gb": 0.0, "swap_pct": 0}

### Wire into snapshot()

`snapshot()` (or whichever convenience builds the HealthSnapshot in this module) must call
`read_meminfo()` and populate the new dataclass fields. Follow exactly how `read_disk` feeds
`disk_free_gb` / `disk_total_gb` today.

## What to add in fleet_tui/models.py

Six new fields on `HealthSnapshot`, all defaulting to 0 so every existing construction site keeps
working unchanged:

    ram_used_gb: float = 0.0
    ram_total_gb: float = 0.0
    ram_pct: int = 0
    swap_used_gb: float = 0.0
    swap_total_gb: float = 0.0
    swap_pct: int = 0

## What to add in fleet_tui/widgets/format.py

In `format_health`, directly AFTER the existing `disk:` line, add a RAM row and, when swap exists, a
swap row. Match the existing visual idiom exactly (see the `gpu0:` and `disk:` lines).

    ram: <used>/<total>GB <pct>%
    swap: <used>/<total>GB <pct>%

Formatting details:
  - Use one decimal for the GB figures, matching the gpu rows.
  - Only emit the ram row when `ram_total_gb` is non-zero. Only emit the swap row when `swap_total_gb`
    is non-zero.
  - Colour the USED figure only, exactly like the disk row colours its free figure.
  - RAM thresholds: red at 90 percent or above, yellow at 75 or above, else the healthy green constant
    already used in this module.
  - **SWAP thresholds are deliberately tighter: red at 75 or above, yellow at 50 or above.** Sustained
    swap use on this box means it is already thrashing; by the time swap is 90 percent full the OOM
    killer is imminent. This asymmetry is the point of the feature and must not be "tidied up" to match
    the RAM thresholds.
  - Reuse the module's existing colour helper if one fits; do not invent a second colour scheme.

## Contracts

  - `sources/health.py` stays pure and headless. ZERO textual import. No new subprocess: `/proc/meminfo`
    is a file read.
  - Nothing may raise. Every reader degrades to its safe default.
  - Do not change any existing reader, field, or format line. Additive only.

## The gate

`tests/test_ram_monitor.py` is Claude-authored and is the real gate. It feeds fixture text rather than
the live `/proc/meminfo`, so it is hermetic. Run the WHOLE suite, not just the new file:

    cd ~/fleet_tui && .venv/bin/python -m pytest -q
