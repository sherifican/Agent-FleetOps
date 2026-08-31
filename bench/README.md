# Local model throughput — a two-box operating log

**This is not a benchmark.** It is the throughput record this lab operates from, with sample sizes,
device labels, and serving stacks attached to the rows. The two boxes use different vendors and
serving stacks; this is not a controlled cross-vendor comparison.

## The boxes

| | box-a | box-b |
|---|---|---|
| Compute | two 16 GB consumer dGPUs | 32 GB workstation dGPU plus unified-memory iGPU |
| Memory / link context | 30 GiB DDR4-2933; one dGPU on PCIe Gen4 x8 and one on Gen3 x4 | unified-memory iGPU path; Vulkan serving stack |
| Published device labels | `dgpu-a`, `both-dgpu` | `dgpu-b`, `igpu` |

The generalized labels identify roles rather than hosts, vendors, or product names. `serving_stack`
is blank only where the retained source record does not identify one of the three published stack
names; blank is more honest than a reconstructed value.

## The limits, stated first

| | |
|---|---|
| Models covered | **22 model tags** |
| Total measurements | **67** |
| Cells with a **single** run | **many; every chart labels n=1** |
| Largest sample for any model | **4** |
| Variance / confidence intervals | **none — not computable at these sample sizes** |

Some rows are single-run. Others aggregate exact replicate values in `condition` and carry their cell
sample count in `n_runs_for_model`. The charts use the published CSV cell value, not an invented error
bar. This is an honestly bounded operating log, not a benchmark.

Three further constraints worth naming:

- **Mixed serving stacks and boxes.** `ollama`, `llama.cpp`, and `llama-server` are not interchangeable
  experimental conditions. Cross-box charts state box/device and sample size so the difference stays visible.
- **Published cell values.** For a model measured once, that value is labelled `n=1`; two-rep paired
  device rows report the stated cell mean and preserve both reps in `condition`.
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
| `local_model_throughput.csv` | the 55-row record, including box, device, quant, stack, quality, and verdict |
| `01_peak_throughput.png` | best recorded row per model, coloured by family, with box/device and run count |
| `02_before_after.png` | the six measured A/B pairs with deltas, including the two that went backwards |
| `03_weights_vs_vram.png` | weights on disk vs VRAM actually occupied |
| `04_cross_box.png` | identical model tags on box-a and box-b, grouped by box/device |
| `05_device_split.png` | box-b dGPU versus iGPU for the paired models, with ratios |
| `make_charts.py` | regenerates all five images from the CSV |

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
