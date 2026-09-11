# Local model throughput — a two-box operating log

**This is not a benchmark.** It is the throughput record this lab operates from, with sample sizes,
device labels, and serving stacks attached to the rows. The two boxes use different vendors and
serving stacks; this is not a controlled cross-vendor comparison.

## The boxes

| | box-a | box-b |
|---|---|---|
| Compute | two 16 GB consumer dGPUs (dual RTX 5060 Ti 16GB) | 32 GB workstation dGPU (Radeon AI PRO R9700) plus unified-memory iGPU (Strix Halo / Radeon 8060S integrated) |
| Memory / link context | throughput charts: 30 GiB DDR4-2933 (4 × 8 GB, mismatched); power study and chart 06 (2026-09-06 onward): 60.7 GiB DDR4-3200 (4 × 16 GB, matched). One dGPU on PCIe Gen4 x8 and one on Gen3 x4 throughout | power study: dGPU on PCIe x4 Gen4 (oculink, four-lane cable), board cap 300 W, Vulkan (RADV GFX1201), not ROCm; historical iGPU runs use unified memory |
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

The counts and sample-size limits above describe `local_model_throughput.csv`; the separate power study below has its own sample sizes and does not change that historical dataset.

Some rows are single-run. Others aggregate exact replicate values in `condition` and carry their cell
sample count in `n_runs_for_model`. The charts use the published CSV cell value, not an invented error
bar. This is an honestly bounded operating log, not a benchmark.

Three further constraints worth naming:

- **Runtime versions are not pinned per row.** `local_model_throughput.csv` carries a `serving_stack` column and
  no runtime version: of its 67 rows, roughly a third are ollama-family (`ollama`, `ollama-cuda`, `ollama-vulkan`),
  a third are `llama.cpp` or `llama-server`, and the rest carry no stack at all. The `ollama 0.33.2` statement
  belongs to the 2026-09-06 power study (`power_undervolt.csv` names it on every row) and to nothing else here.
  Upstream ollama 0.33.3 changed two things that bear on these numbers — GGUF-model-defined default sampler
  parameters are now honoured below Modelfile and request options, and llama.cpp was bumped — so a row measured
  after an upgrade is not comparable to one measured before it unless its runtime is recorded. New rows record
  the server's reported version; old rows are not back-filled with a guess.
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

## Power and undervolt

I measured stock behavior on 2026-09-06: Box A used both RTX 5060 Ti cards over CUDA, and Box B used the Radeon AI PRO R9700 over Vulkan. I used `gemma4:26b-a4b-it-qat` and `qwen3.8:27b`, with n=5 per box/model/workload after one discarded warm-up. Box B also has a 15-minute dense-decode run, n=107 requests. After exposing the overdrive controls that same evening, I repeated Box B dense decode at 0 mV and 300 W (n=3). I have not measured an undervolt. Box A stays stock; only Box B has a voltage ladder planned.

I keep this record in `power_undervolt.csv`, separate from the throughput history. Its ten rows are configuration summaries: eight `stock`, one `stock_sustain`, and one `overdrive_exposed`. Each carries device, sample size, median, power scope, sensor, provenance and hash-check status. I preserve prompt and generation rates separately: chart decode comes from short-prompt generation, chart prefill from the nonced long-prompt prompt rate. The sustain and post-reboot parity rows remain separate from those bars. Nonced runs have hash checking disabled; that is missing correctness evidence, not a pass. Full hashes and actual discrete-pinned model tags stay in the raw JSONL; the CSV uses canonical tags.

**Aggregation and corrections.** I exclude warm-ups, take the median of per-request rates, round prompt rates to whole tok/s and generation to one decimal, and round watts and clocks to whole units. Power is the median of per-request active-sample medians, with the maximum taken over whole request windows. Clock median is the median of each request's active-sample median, and minimum is the minimum active clock. Box A's clock samples pool both cards; its watts sum samples with the same timestamp, while `power_cap_w=180` is per card. Box B's cap is 300 W for one board. Temperatures use each named sensor's maximum over request windows, including inactive samples. `source_kind=raw_jsonl_aggregate` identifies retained raw provenance; each row's notes name the raw file and label. Study dates follow the operating log; JSONL timestamps are UTC.

The retained runs report 65 prompt tokens for decode and 8796–8799 for the nominal 8k prefill. `gen_tokens` is the requested generation cap: short decode requests returned 256 tokens and prefill requests 32; the sustain requested 512 but returned 398 per request. The sustain's original 88 °C summary was the maximum across sensors: memory reached 88 °C, junction 87 °C. I use **87 °C junction** in the CSV and preserve 88 °C memory in notes. The post-reboot parity maximum is **63 °C junction**; its 3/3 full hashes are identical to stock. I retain transient board maxima above the configured cap rather than clipping them.

Running the harness exposed six defects that dry-run and review had missed: I initially hashed an empty string because thinking tokens arrived in a separate field, then used `think:false` and hashed thinking plus response; idle samples polluted minimum clocks, so I restricted median power and clock statistics to active samples; repeated prompts hit the serving cache and produced an invalid 109k tok/s “prefill” (that run was discarded before the retained set and is not among the published records), so I added a nonce and disabled hash comparison for those prompts; opening the output for write discarded earlier configurations, so I switched to append with one JSONL line per run; and an optional sysfs file caused a fatal error, so I recorded it as absent. The 109k reading is a rejected instrument result, not a throughput sample. A sixth was found in review, after the numbers were written: aggregate power filtered each CARD by its own activity and then summed what survived, so a two-card box reported a one-card figure whenever one card dipped below the threshold. The box-a MoE-prefill median read 134 W that way and 187 W when both cards are summed at every retained timestamp; the other three box-a configurations are unchanged, for two different reasons: MoE decode never had a single one-card timestamp among its retained samples, while dense decode (one) and dense prefill (seven) did, but too few to move the median. The retained per-run `power_median` fields still hold the old per-card numbers, so re-running `summarize()` over those files reproduces 134 W; the raw records are deliberately not rewritten, and every published power figure is re-derived from the telemetry instead. The harness now keeps a timestamp when any device is active and sums the whole set, the published cell is the corrected 187 W, and `make_charts.py` re-derives every published power figure from the retained telemetry so this class of error fails the build instead of reaching a reader. I keep these failures visible because a plausible number is not evidence that the instrument measured the intended work.

Chart 06 renders a stock-only preview labeled “undervolt: not yet measured,” with eight bars, n on each, and separate decode and prefill axes. The original five panels use `local_model_throughput.csv`; chart 06 reads only `power_undervolt.csv`. Future comparison rows must name distinct negative offsets at 300 W and supply `verdict=accepted`, `correctness_ref`, and `hot_run_ref` columns linking setting-specific review evidence. A negative offset alone is not acceptance. I will retain failed settings and regressions in the raw log; this CSV gate admits performance summaries only, so aborted attempts with missing rates stay in the raw record. I will review evidence and comparable controls before adding any accepted ladder summaries.

**Method publication.** I retain the original script and prompt filenames and the append-only result/telemetry schema. The harness carries two changes made after the runs: `--host` is required rather than defaulting to a local address, and aggregate power is now summed over the whole device set at each retained timestamp instead of per card (the sixth defect above). Everything else is the file that produced the records, and the published figures are re-derived from those records rather than from the harness's own summary line. The two recorded run scripts take `OLLAMA_ENDPOINT` instead of embedding an address. They retain their original staging layouts: `run_boxa.sh` expects sibling `h/`, `prompts/`, and `runs/` directories after changing to its parent; `run_boxb.sh` expects the harness and prompt basenames under `/tmp`. I do not run these scripts as part of chart reproduction. `prompts/long2k.txt` is retained method material but is not used by these stock configurations.

**Historical chart coverage.** The throughput CSV contains 67 data rows and 22 distinct tags: 21 have generation/decode rows and one is prefill-only (`Ornith-1.0-35B-A3B-MoE-Q4`). Chart 01's existing plotting list contains 20 tags; `qwen3.8-flash-next-pruned` is eligible but absent from that list. I preserve that plotting list and make the subtitle distinguish plotted coverage from the CSV census.


**Clocks.** Dates in `power_undervolt.csv` and in the prose are the local operating day (America/Los_Angeles). The raw JSONL keeps UTC timestamps, so a run listed here as 2026-09-06 carries a `t_start_iso` of 2026-09-07 in the file. Both stock sittings and the post-overdrive parity run happened on the evening of 2026-09-06 local.

## Files

| file | what it is |
|---|---|
| `local_model_throughput.csv` | the record with 67 data rows, including box, device, quant, stack, quality, and verdict |
| `01_peak_throughput.png` | best recorded row per model, coloured by family, with box/device and run count |
| `02_before_after.png` | the seven measured A/B pairs with deltas, including the two that went backwards |
| `03_weights_vs_vram.png` | weights on disk vs VRAM actually occupied |
| `04_cross_box.png` | identical model tags on box-a and box-b, grouped by box/device |
| `05_device_split.png` | box-b dGPU versus iGPU for the paired models, with ratios |
| `06_undervolt_vs_stock.png` | stock-only preview by box, model and workload; separate decode/prefill axes and n on every bar |
| `power_undervolt.csv` | ten power-study configuration summaries, including separate sustain and parity rows |
| `gpu_bench.py` | written-down method, stdlib only; endpoint supplied explicitly |
| `run_boxa.sh`, `run_boxb.sh` | recorded stock run scripts; original staging layout, explicit endpoint |
| `prompts/short.txt`, `prompts/long2k.txt`, `prompts/long8k.txt` | retained synthetic prompts |
| `boxa_v2.jsonl`, `boxb_v2.jsonl` | original stock requests, including warm-ups and Box B sustain |
| `boxa_v2.jsonl.telemetry.jsonl`, `boxb_v2.jsonl.telemetry.jsonl` | corresponding 1 Hz telemetry streams |
| `boxb_d1parity.jsonl`, `boxb_d1parity.jsonl.telemetry.jsonl` | post-reboot parity requests and telemetry |
| `make_charts.py` | regenerates all six images; throughput panels from `local_model_throughput.csv`, panel 06 from `power_undervolt.csv` |

## Reproducing

    python3 make_charts.py

`make_charts.py` carries a **consistency check**: it cross-references its plotting table against
`local_model_throughput.csv` and **exits non-zero if they disagree**. The CSV is the record; the
script is a view of it. Two copies of a number that can drift silently is the defect this repo
exists to argue against, so the check fails loudly rather than rendering a stale chart.

The same invocation validates `power_undervolt.csv` and renders the sixth image. Either missing source CSV exits 2 before any image is written. Invalid power data or chart/source disagreement exits 1; existing images are not refreshed on those failures. The previous script read the throughput CSV at module import and raised a traceback with exit 1 before reaching its documented missing-source exit 2; the existence checks now run before that read. Chart/data agreement cannot by itself establish that a workload was measured correctly.

## Planned

Deepen the sample. Every model should carry enough runs to report a median and a spread rather than
a peak, and the single-run models should stop being single-run. Until that lands, treat these as
operating measurements — directional, honestly bounded, and reproducible on the hardware named above.

I plan a Box B voltage ladder at −60/−80/−100/−120 mV and 300 W, with greedy golden-output checks, kernel-log monitoring, a junction-temperature abort rule and a long hot run before accepting a setting. A lower power-cap arm remains a possibility; neither arm has run. This study is separate from deepening the throughput history's samples.
