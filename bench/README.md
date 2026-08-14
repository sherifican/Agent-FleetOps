# Local model throughput — an operating log

**This is not a benchmark.** It is the throughput record this lab actually operates from, published
with its sample sizes attached so you can see exactly how much weight it carries. Read the limits
section before quoting any number here.

## The hardware

| | |
|---|---|
| **GPU** | 2 × NVIDIA RTX 5060 Ti, **16 GB GDDR7 each (32 GB total)** · Blackwell, compute capability **12.0 (sm_120)** · driver 595.71.05, CUDA 13.2 |
| **CPU** | AMD Ryzen 7 5800XT — 8 cores / 16 threads, boost ~4.97 GHz |
| **RAM** | 32 GB DDR4 (30 GB usable) + 8 GB swap — 4 × 8 GB running at **2933 MT/s**. Deliberately mismatched: 3 × DDR4-3200 CL16 single-rank + 1 × DDR4-3000 CL15 dual-rank, so the controller settles below both kits' ratings. See the note below. |
| **Storage** | 2 TB internal NVMe (BIWIN NV7400), ~700 GB of it models and working state; 1 TB USB-attached NVMe SSD for backups |
| **OS** | Ubuntu 26.04 LTS, kernel 7.0 |
| **Serving stacks** | ollama · llama.cpp · llama-server |

Every figure below was produced on that machine. Nothing here is a vendor number or a projection.

**Minimum to reproduce.** VRAM is the binding constraint and it behaves as a cliff, not a slope —
a model fits or it does not. **One 16 GB card** reaches the `gemma4:26b-a4b-it-qat` tier (15 GB of
weights, 100 tok/s measured), which covers most of the useful lanes including code audit. **The
second card buys the 30–35B tier** — qwen3.6:35b-a3b is 23 GB of weights and occupies 25.1 GB
loaded, so it spans both cards. Budget for runtime overhead separately: it does not scale with
weight size (see the weights-vs-VRAM table — one 9 GB model occupies 17 GB loaded).

**A note on the RAM, since it is a real-world configuration rather than a clean one.** The four DIMMs
are a 3:1 kit mismatch *and* a rank mismatch — three sticks from a DDR4-3200 CL16 single-rank kit plus
one DDR4-3000 CL15 dual-rank stick — so all four negotiate down to **2933 MT/s**, under both kits'
rated speeds. Four DIMMs are also harder on a Ryzen memory controller than two, and mixing ranks
harder still. **It was left this way on purpose:** for GPU-resident inference the model weights and KV
cache live in VRAM, so system DRAM speed affects model *load* time and CPU-side orchestration, not
decode throughput. None of the tokens-per-second figures on this page would move meaningfully on a
matched 3200 kit. If you are building for this workload, **spend the budget on VRAM before RAM
speed.**

## The limits, stated first

| | |
|---|---|
| Models covered | **14** |
| Total measurements | **30** |
| Models with a **single** run | **7 of 14** |
| Largest sample for any model | **4** |
| Variance / confidence intervals | **none — not computable at these sample sizes** |

Seven of the fourteen models were measured **once**. Nothing here has more than four runs. That is
enough to make routing decisions on a box you own; it is **not** enough to publish as a benchmark,
and it is not offered as one. `n_runs_for_model` ships in the CSV so the sample size travels with
every row instead of living in a caption.

Three further constraints worth naming:

- **Mixed serving stacks.** A llama.cpp figure and an ollama figure are not a controlled comparison.
  The `condition` column names the stack for every row.
- **Peak, not mean.** The charts plot the best run per model. For a model measured once, "best" and
  "only" are the same number — which flatters the models measured more often.
- **Prefill and decode are separate rows.** The `metric` column distinguishes them. Sorting them
  together produces nonsense: LFM's prefill figures are ~50× its decode figures.

## What the data does support

- **Routing decisions beat flag-tuning on this hardware.** Swapping the audit lane from
  `gemma4:31b-it-qat` to `gemma4:26b-a4b-it-qat` averaged **+348%** across three separate tasks at
  quality parity — the smaller MoE model also found *more* seeded bugs (5/5 vs 4/5, 18/18 vs 16/18).
  Speculative decoding on the same model averaged **+13%**. Both are real; they are not the same
  size of lever.
- **Runtime footprint does not scale with weight size.** `deepseek-r1:14b` occupies **1.89×** its
  9 GB of weights once loaded, while `qwen3.6:35b-a3b` occupies **1.09×** its 23 GB. Sizing VRAM
  from model size alone will be badly wrong on the small model.
- **A published negative.** The MoE-offload accelerator build was **slower** than stock at both
  offload levels tested (−5.6% at `-ngl 24`, −1.4% at `-ngl 8`). A claimed 64% speed-up did not
  reproduce. It is on the chart in red because a result that only records wins is not a record.

## Files

| file | what it is |
|---|---|
| `local_model_throughput.csv` | the record — 30 measurements, each with metric, sample size, condition, and source |
| `01_peak_throughput.png` | peak decode per model, coloured by family, with weights · quantisation · architecture · run count |
| `02_before_after.png` | the six measured A/B pairs with deltas, including the two that went backwards |
| `03_weights_vs_vram.png` | weights on disk vs VRAM actually occupied |
| `make_charts.py` | regenerates all three images from the CSV |

## Reproducing

    python3 make_charts.py

`make_charts.py` carries a **consistency check**: it cross-references its plotting table against
`local_model_throughput.csv` and **exits non-zero if they disagree**. The CSV is the record; the
script is a view of it. Two copies of a number that can drift silently is the defect this repo
exists to argue against, so the check fails loudly rather than rendering a stale chart.

## Planned

Deepen the sample. Every model should carry enough runs to report a median and a spread rather than
a peak, and the single-run models should stop being single-run. Until that lands, treat these as
operating measurements — directional, honestly bounded, and reproducible on the hardware named above.
