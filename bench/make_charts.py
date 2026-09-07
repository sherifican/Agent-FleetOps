#!/usr/bin/env python3
"""Fleet throughput composite via Vega-Lite + vl-convert. MEASURED DATA ONLY."""
import json, math, vl_convert as vlc
import csv, os, re, sys, time

# Check every required source before the module-level silicon lookup or rendering.
SOURCE_DIR = os.path.dirname(os.path.abspath(__file__))
for source_name in ('local_model_throughput.csv', 'power_undervolt.csv'):
    if not os.path.isfile(os.path.join(SOURCE_DIR, source_name)):
        sys.stderr.write(f'make_charts: {source_name} missing; images not refreshed\n')
        sys.exit(2)


BG, FG, MUTED, GRID = "#12141a", "#e8eaf0", "#98a0b3", "#2a2f3a"
FAM = {"qwen": "#4cc9f0", "gemma": "#b5179e", "ornith": "#f7b801", "lfm": "#43e97b",
       "deepseek": "#f56565", "glm": "#9d8df1", "qwythos": "#8d99ae", "other": "#7f8794"}

peak = [
    ("LFM2.5-8B-A1B",         230.0, "lfm",      "5.2 GB · Q4_K_M · MoE A1B · decode · HIGH END of a measured 130-230 range"),
    # Ornith-1.0-35B-A3B 199.9 REMOVED 2026-08-23: that figure is llama.cpp `pp2048` = PROMPT
    # PROCESSING, not decode. It sat on this generation axis at ~2x the same model's real
    # decode row (115.0, below). The CSV rows are now metric=prefill; this chart is decode-only.
    ("Ornith-1.0-35B",        115.0, "ornith",   "21 GB · Q4_K_M · MoE A3B · single run"),
    ("qwen3.6:35b-a3b",       114.43, "qwen",    "box-a · both-dgpu · Q4_K_M · n=1"),
    ("qwen3-coder:30b",       135.5, "qwen",     "box-b · dgpu-b · quant unknown · n=2"),
    ("gemma4:26b-a4b-it-qat", 109.05, "gemma",   "box-b · dgpu-b · q4_0-qat · n=2"),
    ("GLM-4.7-Flash",         105.0, "glm",      "18 GB · quant unrecorded · vLLM/endpoint probe · n=1"),
    ("GLM-4.7-Flash-GGUF",    105.0, "glm",      "box-a · both-dgpu · quant unknown · n=1"),
    ("Ornith-1.0-9B",          67.0, "ornith",   "5.6 GB · Q4_K_M · 9B dense · single run"),
    ("qwen3.8:27b-devicepinned", 56.4, "qwen",   "box-b · dgpu-b · device-pinned build · n=1"),
    ("gemma4:12b",             54.0, "gemma",    "7.6 GB · Q4_K_M · 11.9B dense · max of 3 conditions"),
    ("qwen3.8:27b",            74.6, "qwen",     "box-b · dgpu-b · same-prompt device matrix · n=1"),
    ("qwen3.8-flash-next-stock", 22.96, "qwen",  "box-b · igpu · official 512-expert release · decode · n=2"),
    ("qwen3:14b",             42.49, "qwen",     "Q4_K_M · 14B · ollama · single run"),
    ("deepseek-r1:14b",       42.37, "deepseek", "9.0 GB · Q4_K_M · 14.8B · single run"),
    ("muse-glimmer",           33.8, "other",    "box-b · igpu · llama-server spec-decode · n=3"),
    ("gemma4:31b-it-qat",     28.35, "gemma",    "box-b · dgpu-b · q4_0-qat · n=2"),
    ("Qwythos-9B",             28.0, "qwythos",  "9B · decode · max of 2 context depths (short)"),
    ("gemma4:26b-a4b-it-bf16", 13.0, "gemma",    "box-b · igpu · bf16 · n=1 · dumped"),
    ("gemma4:31b-it-q8_0",      6.6, "gemma",    "box-b · igpu · q8_0 · n=1 · dumped"),
    ("gemma4:31b-it-bf16",      3.6, "gemma",    "box-b · igpu · bf16 · n=1 · dumped"),
]
# Sorted here rather than by hand: when a re-measurement changes a value, a hand-kept order
# silently stops matching the bars. (It did: qwen3-coder went 111.7 -> 135.5 and stayed 5th.)
peak = sorted(peak, key=lambda t: -t[1])

# ---------------------------------------------------------------- silicon labels
# Every bar states the GPU it actually ran on. The mapping is derived from the CSV rather than
# typed into the detail strings, because a hand-kept second copy of the device is exactly the
# drift this file's consistency gate exists to prevent.
#
# ⚠ device is a property of the RUN, not of the model. ollama places by free VRAM at load time,
# so the same model can land on one card or span two on different days — measured 2026-08-22:
# ornith:9b is 5.6 GB and fits one card, yet was split 5435/5315 MiB across both, while lfm:8b
# at 5.2 GB stayed on one. Read each row as "where this measurement ran".
ALIAS = {"Ornith-1.0-35B-A3B": "Ornith-1.0-35B-A3B-MoE-Q4", "qwen3.6:35b-a3b": "qwen3.6:35b-a3b-q4_K_M"}
GPU = {
    ("box-a", "dgpu-a"):    "1× RTX 5060 Ti 16GB",
    ("box-a", "both-dgpu"): "2× RTX 5060 Ti · 32GB",
    ("box-b", "dgpu-b"):    "Radeon AI PRO R9700 31GB",
    ("box-b", "igpu"):      "Radeon 8060S iGPU",
}


def _silicon():
    """model -> GPU string, taken from the CSV row that supplies its peak figure."""
    import csv, os
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "local_model_throughput.csv")
    best = {}
    for r in csv.DictReader(open(path)):
        if r["metric"] not in ("generation", "decode"):
            continue
        v = float(r["tok_per_sec"])
        if r["model"] not in best or v > best[r["model"]][0]:
            best[r["model"]] = (v, r)
    return {m: GPU.get((r["box"], r["device"]), "") for m, (v, r) in best.items()}


_SIL = _silicon()


def _detail(model, d):
    """Lead with the silicon; drop the old box·device prefix so it is not stated twice."""
    parts = [x.strip() for x in d.split("·")]
    parts = [x for x in parts if not x.startswith("box-") and x not in
             ("dgpu-a", "dgpu-b", "both-dgpu", "igpu")]
    gpu = _SIL.get(ALIAS.get(model, model), "")
    if not gpu:
        # Never silently print a model with no device: say it is unrecorded.
        gpu = "device unrecorded"
    return " · ".join([gpu] + parts)


p1 = [{"model": m, "tps": v, "family": f, "detail": _detail(m, d)} for m, v, f, d in peak]

ab = [
    ("gemma4 31b→26b-a4b · 18-issue corroborate",  21.0, 100.0, "swap", "same task · 18/18 vs 16/18"),
    ("gemma4 31b→26b-a4b · 9.3K-tok artifact",     19.0,  90.0, "swap", "same task · quality parity"),
    ("gemma4 31b→26b-a4b · 5-seeded-bug review",   20.4,  80.6, "swap", "same task · 5/5 vs 4/5"),
    ("qwen3-coder:30b · spec-decode ngram-mod",    95.6, 108.1, "tune", "same model + same prompt"),
    ("Ornith-35B-A3B · MoE accel (-ngl 8) · PREFILL pp2048",       115.7, 114.1, "tune", "claimed win did NOT reproduce"),
    ("Ornith-35B-A3B · MoE accel (-ngl 24) · PREFILL",      199.9, 188.7, "tune", "claimed win did NOT reproduce"),
    ("qwen3.8:27b · 2×16GB over PCIe → one 32GB card", 21.8, 74.6, "place", "same prompt, same model · different device"),
]
p2 = []
for lab, b, a, grp, note in ab:
    d = (a - b) / b * 100
    p2.append({"change": lab, "before": b, "after": a, "delta": d, "group": grp, "note": note,
               "dlabel": f"{d:+.1f}%", "dir": "gain" if d > 0 else "loss",
               "lo": min(a, b), "hi": max(a, b)})

vram = [("qwen3.6:35b-a3b", 23.0, 25.1), ("deepseek-r1:14b", 9.0, 17.0), ("gemma4:12b", 7.6, 9.3)]
vram_order = [m for m, *_ in vram]
p3 = []
for m, w, v in vram:
    p3 += [{"model": m, "kind": "weights on disk", "gb": w, "lbl": f"{w:g} GB"},
           {"model": m, "kind": "VRAM occupied",   "gb": v, "lbl": f"{v:g} GB  (+{v-w:.1f}, {v/w:.2f}×)"}]

pos = [r["delta"] for r in p2 if r["delta"] > 0]
neg = [r["delta"] for r in p2 if r["delta"] <= 0]
swap = [r["delta"] for r in p2 if r["group"] == "swap"]
tune = [r["delta"] for r in p2 if r["group"] == "tune"]
place = [r["delta"] for r in p2 if r["group"] == "place"]
sub2 = (f"mean of the {len(pos)} that improved: +{sum(pos)/len(pos):.1f}%   ·   "
        f"mean of the {len(neg)} that did not: {sum(neg)/len(neg):.1f}%   ·   "
        f"model-swap group: +{sum(swap)/len(swap):.1f}%   ·   "
        f"placement (same prompt, different device): +{sum(place)/len(place):.1f}%   ·   "
        # `pos[-1]` reported the single winning tune row as if it were the class average.
        # The tune group carries its losers too (-1.4%, -5.6%), so the mean is computed over
        # every tune row rather than quoted from the best one; `len(tune)` is printed so the
        # figure cannot be read as a per-run guarantee.
        f"same-model tuning (mean of {len(tune)}): {sum(tune)/len(tune):+.1f}%")


def tps_axis(values, step=50):
    """An x-domain that always contains its data, with ticks every `step`.

    This was hardcoded to [0, 250]. A row measured at 355 tok/s then drew past the
    last labelled tick, into unlabelled space: the reader can see the point but
    cannot read its value. A hand-set axis bound is one more number that does not
    recompute when the data moves.
    """
    top = max(values)
    hi = max(int(math.ceil(top / step) * step), step)
    # A value landing exactly on the bound draws its marker half outside the plot,
    # which reads as clipped even though the number is in range. Give it one step.
    if hi - top < step * 0.05:
        hi += step
    # A domain that does not cover its data understates the size of what it plots,
    # so the chart must refuse to render rather than mislead.
    assert hi >= top, f"axis domain {hi} does not cover the largest value {top}"
    return {"domain": [0, hi]}, {"tickCount": hi // step}


AX = {"labelColor": FG, "titleColor": FG, "gridColor": GRID, "domainColor": GRID,
      "tickColor": GRID, "labelFontSize": 11, "titleFontSize": 12}
order = [m for m, *_ in peak]

ROWH1 = 34
PEAK_SCALE, PEAK_TICKS = tps_axis([r["tps"] for r in p1])
AB_SCALE, AB_TICKS = tps_axis([r["hi"] for r in p2] + [r["lo"] for r in p2])

p1_text = {
    "width": 400, "height": len(peak) * ROWH1,
    "data": {"values": p1},
    "encoding": {"y": {"field": "model", "type": "nominal", "sort": order, "title": None,
                       "axis": None, "scale": {"paddingInner": 0.22}}},
    "layer": [
        {"mark": {"type": "text", "align": "right", "x": 400, "dy": -6,
                  "fontSize": 12.5, "fontWeight": "bold", "color": FG, "limit": 398},
         "encoding": {"text": {"field": "model"}}},
        {"mark": {"type": "text", "align": "right", "x": 400, "dy": 8,
                  "fontSize": 8.6, "color": MUTED, "limit": 398},
         "encoding": {"text": {"field": "detail"}}},
    ],
}
p1_bars = {
    "width": 620, "height": len(peak) * ROWH1,
    "data": {"values": p1},
    "encoding": {"y": {"field": "model", "type": "nominal", "sort": order, "title": None,
                       "axis": None, "scale": {"paddingInner": 0.22}}},
    "layer": [
        {"mark": {"type": "bar", "height": 17, "cornerRadiusEnd": 3},
         "encoding": {"x": {"field": "tps", "type": "quantitative",
                            "title": "peak generation / decode  (tokens per second)",
                            "scale": PEAK_SCALE, "axis": PEAK_TICKS},
                      "color": {"field": "family", "type": "nominal",
                                "scale": {"domain": list(FAM), "range": [FAM[k] for k in FAM]},
                                "legend": {"title": "family", "labelColor": FG, "titleColor": FG,
                                           "orient": "bottom", "direction": "horizontal", "columns": 7,
                                           "offset": 16, "symbolSize": 110}}}},
        {"mark": {"type": "text", "align": "left", "dx": 6, "fontSize": 12.5,
                  "fontWeight": "bold", "color": FG},
         "encoding": {"x": {"field": "tps", "type": "quantitative"},
                      "text": {"field": "tps", "type": "quantitative", "format": ".1f"}}},
    ],
}
with open(os.path.join(SOURCE_DIR, 'local_model_throughput.csv')) as _source:
    _inventory = list(csv.DictReader(_source))
_inventory_count = len({r['model'] for r in _inventory})
_eligible_count = len({r['model'] for r in _inventory if r['metric'] in ('generation', 'decode')})
_peak_census = (f"Best recorded generation/decode rows: {len(peak)} plotted of {_inventory_count} tags "
                f"({_eligible_count} eligible; {_inventory_count - _eligible_count} prefill-only). "
                "Mixed serving stacks and boxes are directional operating data, not a controlled cross-vendor benchmark.")

panel1 = {
    "title": {"text": "Local model throughput — two-box operating log",
              "subtitle": [_peak_census,
                           "Each row names the GPU that measurement ran on. Placement is a property of the run, not the model:",
                           "ollama packs by free VRAM at load time, so a model that fits one card may still span two."],
              "anchor": "start", "color": FG, "fontSize": 19, "subtitleColor": MUTED,
              "subtitleFontSize": 11.5},
    "hconcat": [p1_text, p1_bars], "spacing": 14,
}

ROWH2 = 46
p2_text = {
    "width": 300, "height": len(p2) * ROWH2,
    "data": {"values": p2},
    "encoding": {"y": {"field": "change", "type": "nominal", "sort": [r["change"] for r in p2],
                       "title": None, "axis": None, "scale": {"paddingInner": 0.25}}},
    "layer": [
        {"mark": {"type": "text", "align": "right", "x": 300, "dy": -6,
                  "fontSize": 11, "color": FG, "limit": 298},
         "encoding": {"text": {"field": "change"}}},
        {"mark": {"type": "text", "align": "right", "x": 300, "dy": 8,
                  "fontSize": 8.6, "color": MUTED, "limit": 298},
         "encoding": {"text": {"field": "note"}}},
    ],
}
DIRSCALE = {"domain": ["gain", "loss"], "range": ["#43e97b", "#f56565"]}
p2_plot = {
    "width": 620, "height": len(p2) * ROWH2,
    "data": {"values": p2},
    "encoding": {"y": {"field": "change", "type": "nominal", "sort": [r["change"] for r in p2],
                       "title": None, "axis": None, "scale": {"paddingInner": 0.25}}},
    "layer": [
        {"mark": {"type": "rule", "size": 3, "color": "#3a4152"},
         "encoding": {"x": {"field": "lo", "type": "quantitative", "title": "tokens per second",
                            "scale": AB_SCALE, "axis": AB_TICKS},
                      "x2": {"field": "hi"}}},
        {"mark": {"type": "point", "filled": True, "size": 140, "color": "#6b7280"},
         "encoding": {"x": {"field": "before", "type": "quantitative"}}},
        {"mark": {"type": "point", "filled": True, "size": 200},
         "encoding": {"x": {"field": "after", "type": "quantitative"},
                      "color": {"field": "dir", "type": "nominal", "scale": DIRSCALE, "legend": None}}},
        {"mark": {"type": "text", "align": "left", "dx": 14, "fontSize": 13.5, "fontWeight": "bold"},
         "encoding": {"x": {"field": "hi", "type": "quantitative"}, "text": {"field": "dlabel"},
                      "color": {"field": "dir", "type": "nominal", "scale": DIRSCALE, "legend": None}}},
    ],
}
panel2 = {
    "title": {"text": "Before / after — every A/B measured here, same task on both sides",
              "subtitle": [sub2,
                           f"Grey dot = before. Coloured dot = after. {len(neg)} of {len(p2)} are negatives. "
        f"Rows marked PREFILL are prompt-processing, not decode — the two axes are not comparable."],
              "anchor": "start", "color": FG, "fontSize": 17, "subtitleColor": MUTED,
              "subtitleFontSize": 11.5},
    "hconcat": [p2_text, p2_plot], "spacing": 14,
}

panel3 = {
    "title": {"text": "Weights on disk vs VRAM actually occupied (box-a, both dGPUs summed)",
              "subtitle": ["Runtime overhead is KV cache + engine and does NOT scale with weight size — deepseek-r1:14b nearly doubles its 9 GB footprint.",
                           "VRAM measured only for the models in the ollama bake-off; the rest are unmeasured rather than zero."],
              "anchor": "start", "color": FG, "fontSize": 16, "subtitleColor": MUTED, "subtitleFontSize": 11},
    "width": 900, "height": 175,
    "data": {"values": p3},
    "encoding": {"y": {"field": "model", "type": "nominal", "title": None, "sort": vram_order,
                       "axis": {"labelFontSize": 12, "labelColor": FG}},
                 "yOffset": {"field": "kind"}},
    "layer": [
        {"mark": {"type": "bar", "height": 20},
         "encoding": {"x": {"field": "gb", "type": "quantitative", "title": "gibibytes",
                            "scale": {"domain": [0, 32]}, "axis": {"tickCount": 8}},
                      "color": {"field": "kind", "type": "nominal",
                                "scale": {"domain": ["weights on disk", "VRAM occupied"],
                                          "range": ["#4cc9f0", "#f7b801"]},
                                "legend": {"title": None, "labelColor": FG, "orient": "bottom-right",
                                           "direction": "horizontal"}}}},
        {"mark": {"type": "text", "align": "left", "dx": 6, "fontSize": 10.5, "color": FG},
         "encoding": {"x": {"field": "gb", "type": "quantitative"}, "text": {"field": "lbl"}}},
    ],
}

# ---------------------------------------------------------------- new two-box panels
# ★ Every pair here must compare the SAME metric. It did not: the box-a cells are
# metric=generation while the box-b cells were metric=decode, so a "different silicon"
# picture was really generation-vs-decode. Box-b now uses its generation rows.
p4 = [
    {"model": "gemma4:26b-a4b-it-qat", "side": "box-a / dgpu-a", "tps": 83.3, "label": "83.3  n=1"},
    {"model": "gemma4:26b-a4b-it-qat", "side": "box-b / dgpu-b", "tps": 105.2, "label": "105.2  n=2"},
    {"model": "gemma4:31b-it-qat", "side": "box-a / dgpu-a", "tps": 18.2, "label": "18.2  n=1"},
    {"model": "gemma4:31b-it-qat", "side": "box-b / dgpu-b", "tps": 28.25, "label": "28.25  n=2"},
    {"model": "qwen3-coder:30b", "side": "box-a / dgpu-a", "tps": 35.6, "label": "35.6  n=1 · ollama"},
    {"model": "qwen3-coder:30b", "side": "box-b / dgpu-b", "tps": 111.7, "label": "111.7  n=1"},
    {"model": "qwen3.8:27b", "side": "box-a / dgpu-a", "tps": 21.2, "label": "21.2  n=1"},
    {"model": "qwen3.8:27b", "side": "box-b / dgpu-b", "tps": 46.0, "label": "46.0  n=1"},
]
panel4 = {
    "title": {"text": "Cross-box throughput — identical model, different silicon",
              "subtitle": ["Grouped bars show the published CSV cell value, with box/device and n attached.",
                           "Different vendors and serving stacks make this an operating comparison, not a controlled benchmark.",
                           "Both sides of every pair are generation rows. Stacks still differ: the coder pair is an n=1 ollama cell against an n=1 box-b cell, and box-a also has 108.1 on llama.cpp."],
              "anchor": "start", "color": FG, "fontSize": 18, "subtitleColor": MUTED, "subtitleFontSize": 11.5},
    "width": 900, "height": 280, "data": {"values": p4},
    "encoding": {"x": {"field": "model", "type": "nominal", "title": None, "axis": {"labelAngle": 0, "labelLimit": 260, "labelFontSize": 11}}, "xOffset": {"field": "side"}},
    "layer": [
        {"mark": {"type": "bar", "cornerRadiusEnd": 3},
         "encoding": {"y": {"field": "tps", "type": "quantitative", "title": "tokens per second", "scale": {"zero": True}},
                      "color": {"field": "side", "type": "nominal", "scale": {"domain": ["box-a / dgpu-a", "box-b / dgpu-b"], "range": ["#4cc9f0", "#b5179e"]}, "legend": {"title": None, "orient": "bottom"}}}},
        {"mark": {"type": "text", "dy": -7, "fontSize": 10.5, "color": FG},
         "encoding": {"y": {"field": "tps", "type": "quantitative"}, "text": {"field": "label"}}},
    ],
}
p5 = [
    {"model": "qwen3-coder:30b", "device": "dgpu-b", "tps": 135.5, "label": "135.5  n=2", "ratio": "1.63×"},
    {"model": "qwen3-coder:30b", "device": "igpu", "tps": 83.0, "label": "83.0  n=2", "ratio": ""},
    {"model": "gemma4:26b-a4b-it-qat", "device": "dgpu-b", "tps": 109.05, "label": "109.05  n=2", "ratio": "1.63×"},
    {"model": "gemma4:26b-a4b-it-qat", "device": "igpu", "tps": 67.1, "label": "67.1  n=2", "ratio": ""},
    {"model": "qwen3.8:27b", "device": "dgpu-b", "tps": 54.35, "label": "54.35  n=2", "ratio": "2.30×"},
    {"model": "qwen3.8:27b", "device": "igpu", "tps": 23.6, "label": "23.6  n=2", "ratio": ""},
    {"model": "gemma4:31b-it-qat", "device": "dgpu-b", "tps": 28.35, "label": "28.35  n=2", "ratio": "2.36×"},
    {"model": "gemma4:31b-it-qat", "device": "igpu", "tps": 12.0, "label": "12.0  n=2", "ratio": ""},
]
panel5 = {
    "title": {"text": "Box-b device split — dGPU vs iGPU",
              "subtitle": ["Bold multiplier is dGPU / iGPU. Two reps per device, one harness, one sitting (2026-08-22):",
                           "same prompt, num_ctx pinned on BOTH devices, model unloaded between them.",
                           "Quality is a separate measurement: T1 8/8 except qwen3-coder:30b at 6/8."],
              "anchor": "start", "color": FG, "fontSize": 18, "subtitleColor": MUTED, "subtitleFontSize": 11.5},
    "width": 900, "height": 290, "data": {"values": p5},
    "encoding": {"x": {"field": "model", "type": "nominal", "title": None, "sort": {"field": "tps", "op": "max", "order": "descending"}, "axis": {"labelAngle": 0, "labelLimit": 300, "labelFontSize": 11}}, "xOffset": {"field": "device", "sort": ["dgpu-b", "igpu"]}},
    "layer": [
        {"mark": {"type": "bar", "cornerRadiusEnd": 3},
         "encoding": {"y": {"field": "tps", "type": "quantitative", "title": "tokens per second", "scale": {"zero": True}},
                      "color": {"field": "device", "type": "nominal", "scale": {"domain": ["dgpu-b", "igpu"], "range": ["#f7b801", "#43e97b"]}, "legend": {"title": None, "orient": "bottom"}}}},
        {"mark": {"type": "text", "dy": -7, "fontSize": 10.5, "color": FG},
         "encoding": {"y": {"field": "tps", "type": "quantitative"}, "text": {"field": "label"}}},
        {"transform": [{"filter": "datum.device === 'dgpu-b'"}], "mark": {"type": "text", "dy": -29, "fontSize": 11.5, "fontWeight": "bold", "color": MUTED},
         "encoding": {"y": {"field": "tps", "type": "quantitative"}, "text": {"field": "ratio"}}},
    ],
}


# ---------------------------------------------------------------- consistency gate
def _verify_against_csv():
    """The CSV is the record; this script is a view of it. Two copies of a number that can drift
    silently is exactly the defect this repo argues against — so fail loudly instead of rendering
    a stale chart."""
    import csv, os, sys, collections
    csv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "local_model_throughput.csv")
    if not os.path.isfile(csv_path):
        sys.stderr.write(f"make_charts: {csv_path} missing — refusing to render an unverifiable chart\n")
        sys.exit(2)
    best = collections.defaultdict(float)
    values = set()
    for r in csv.DictReader(open(csv_path)):
        if r["metric"] in ("generation", "decode"):
            best[r["model"]] = max(best[r["model"]], float(r["tok_per_sec"]))
            values.add((r["model"], float(r["tok_per_sec"])))
    alias = ALIAS
    bad = []
    for name, v, _fam, _detail in peak:
        key = alias.get(name, name)
        if key not in best:
            bad.append(f"{name}: not present in CSV"); continue
        if abs(best[key] - v) > 0.005:  # was 0.051 — wide enough to hide a rounded hand-pin
            bad.append(f"{name}: chart {v} vs CSV {best[key]}")
    # The before/after list was previously verified by NOTHING. Its axis is a generic
    # "tokens per second" that legitimately carries decode rows AND prefill A/B rows, so each
    # value must exist in the CSV and a prefill-sourced bar must say so in its own label.
    allvals = {}
    for r in csv.DictReader(open(csv_path)):
        allvals.setdefault(float(r["tok_per_sec"]), set()).add(r["metric"])
    for lab, b, a, _grp, _note in ab:
        for v in (b, a):
            metrics = allvals.get(v)
            if metrics is None:
                bad.append(f"before/after '{lab}': {v} is not in the CSV record")
            elif metrics == {"prefill"} and "prefill" not in lab.lower():
                bad.append(f"before/after '{lab}': {v} is a PREFILL row in the CSV but the label "
                           f"does not say so — it shares an axis with decode bars")
    for row in p4 + p5:
        if (row["model"], row["tps"]) not in values:
            bad.append(f"{row['model']}: chart {row['tps']} not present in CSV")
    # ★ The gate above compares the chart to the CSV — it CANNOT catch an error present in BOTH.
    # It did not: a pp2048 (prompt-processing) row carried metric=generation, so chart and CSV
    # agreed with each other and disagreed with reality. Verify the CLAIM, not just the copy:
    # a row whose own condition text names a prompt-processing measurement may not be charted
    # as generation/decode.
    import re as _re
    _PP = _re.compile(r"\bpp\d+\b|prompt[- ]processing|\bprefill\b", _re.I)
    for r in csv.DictReader(open(csv_path)):
        if r["metric"] in ("generation", "decode") and _PP.search(r["condition"] + " " + r["source"]):
            bad.append(f"{r['model']}: metric={r['metric']} but condition names a prompt-processing "
                       f"measurement ({r['condition'][:60]}...) — prefill is not decode")
    if bad:
        sys.stderr.write("make_charts: chart table DISAGREES with the CSV record:\n")
        for b in bad: sys.stderr.write(f"  - {b}\n")
        sys.exit(1)
    print(f"consistency gate: OK — {len(peak)} peak rows and {len(p4) + len(p5)} two-box rows match the CSV record")

_verify_against_csv()


# ---------------------------------------------------------------- power-study CSV and panel
POWER_COLUMNS = set('date box device model arch metric condition voltage_offset_mv power_cap_w n tok_per_sec tok_source watts_med watts_max temp_max_c temp_kind sclk_med_mhz sclk_min_mhz gen_tokens prompt_tok_per_sec gen_tok_per_sec hashes serving_stack notes source_kind power_scope'.split())
POWER_KEY = ('date', 'box', 'device', 'model', 'metric', 'condition')
NO_UNDERVOLT = 'undervolt: not yet measured'


def _power_error(message):
    sys.stderr.write(f'make_charts: power CSV invalid: {message}; images not refreshed\n')
    sys.exit(1)


def _read_power_csv():
    """Validate configuration summaries; plotting never reads the raw JSONL.

    The initial study has a fixed protocol (five measured short requests, 107
    sustained requests, three parity requests). Hash denominators also check n.
    Future accepted settings need explicit acceptance/evidence columns; a
    negative offset by itself cannot establish output stability.
    """
    try:
        with open(os.path.join(SOURCE_DIR, 'power_undervolt.csv'), newline='') as f:
            reader = csv.DictReader(f)
            if not POWER_COLUMNS.issubset(reader.fieldnames or []):
                raise ValueError('required columns missing')
            rows = list(reader)
        seen = set()
        for r in rows:
            if None in r or any(r.get(k) is None for k in POWER_COLUMNS):
                raise ValueError('ragged CSV row')
            key = tuple(r[k] for k in POWER_KEY)
            if key in seen:
                raise ValueError(f'duplicate configuration {key}')
            seen.add(key)
            if r['box'] not in ('box-a', 'box-b'):
                raise ValueError(f'unknown box in {key}')
            a = r['box'] == 'box-a'
            if r['device'] != ('both-dgpu' if a else 'dgpu-b'):
                raise ValueError(f'device does not identify measured placement: {key}')
            if r['power_scope'] != ('sum_two_cards' if a else 'single_board'):
                raise ValueError(f'wrong power scope: {key}')
            if r['temp_kind'] != ('nvidia-smi' if a else 'junction'):
                raise ValueError(f'wrong temperature sensor: {key}')
            if r['arch'] not in ('moe', 'dense') or r['metric'] not in ('decode', 'prefill'):
                raise ValueError(f'unknown architecture/metric: {key}')
            for field in ('tok_per_sec', 'prompt_tok_per_sec', 'gen_tok_per_sec',
                          'watts_med', 'watts_max', 'sclk_med_mhz', 'sclk_min_mhz',
                          'temp_max_c', 'power_cap_w'):
                r[field] = float(r[field])
                if not math.isfinite(r[field]) or r[field] <= 0:
                    raise ValueError(f'{field} must be finite and positive: {key}')
            for field in ('n', 'gen_tokens'):
                r[field] = int(r[field])
                if r[field] <= 0:
                    raise ValueError(f'{field} must be a positive integer: {key}')
            if r['watts_med'] > r['watts_max'] or r['sclk_min_mhz'] > r['sclk_med_mhz']:
                raise ValueError(f'median outside min/max: {key}')
            if not r['serving_stack'] or not r['source_kind'] or not r['notes']:
                raise ValueError(f'missing provenance: {key}')
            offset = None if r['voltage_offset_mv'] == '' else float(r['voltage_offset_mv'])
            if offset is not None and not math.isfinite(offset):
                raise ValueError(f'non-finite voltage offset: {key}')
            r['voltage_offset_mv'] = offset
            cond = r['condition']
            if cond.startswith('uv-'):
                if offset is None or offset >= 0 or cond != f'uv{offset:g}' or a:
                    raise ValueError(f'undervolt must name a negative Box B offset: {key}')
                if r.get('verdict') != 'accepted' or not all(r.get(k) for k in ('correctness_ref', 'hot_run_ref')):
                    raise ValueError(f'undervolt lacks setting-specific acceptance evidence: {key}')
            elif cond in ('stock', 'stock_sustain', 'overdrive_exposed'):
                if offset != (None if a else 0):
                    raise ValueError(f'wrong stock voltage metadata: {key}')
                expected_n = {'stock': 5, 'stock_sustain': 107, 'overdrive_exposed': 3}[cond]
                if r['n'] != expected_n:
                    raise ValueError(f'n differs from the recorded study protocol: {key}')
                if cond != 'stock' and (a or r['metric'] != 'decode' or r['arch'] != 'dense'):
                    raise ValueError(f'wrong sustain/parity identity: {key}')
            else:
                raise ValueError(f'unknown condition: {key}')
            if r['power_cap_w'] != (180 if a else 300):
                raise ValueError(f'wrong applied cap: {key}')
            prefill = r['metric'] == 'prefill'
            source = 'prompt' if prefill else 'gen'
            if r['tok_source'] != source or abs(r['tok_per_sec'] - r[source + '_tok_per_sec']) > 0.005:
                raise ValueError(f'metric/source/value disagreement: {key}')
            expected_gen = 32 if prefill else 512 if cond == 'stock_sustain' else 256
            if r['gen_tokens'] != expected_gen:
                raise ValueError(f'wrong requested generation length: {key}')
            if prefill:
                if r['hashes'] != 'n/a (nonce)':
                    raise ValueError(f'nonced prefill cannot claim a hash pass: {key}')
            else:
                if re.search(r'\bpp\d+\b|prompt[- ]processing|\bprefill\b', r['notes'], re.I):
                    raise ValueError(f'decode row describes prompt processing: {key}')
                hashes = re.fullmatch(r'(\d+)/(\d+)', r['hashes'])
                if not hashes or not (0 <= int(hashes[1]) <= int(hashes[2]) == r['n']):
                    raise ValueError(f'hash count disagrees with n: {key}')
        stock_cells = {(r['box'], r['arch'], r['metric']) for r in rows if r['condition'] == 'stock'}
        expected = {(b, a, m) for b in ('box-a', 'box-b') for a in ('moe', 'dense') for m in ('decode', 'prefill')}
        if stock_cells != expected or sum(r['condition'] == 'stock' for r in rows) != len(expected):
            raise ValueError('stock baseline must contain exactly the eight measured cells')
        return rows
    except FileNotFoundError:
        sys.stderr.write('make_charts: power_undervolt.csv missing; images not refreshed\n')
        sys.exit(2)
    except (ValueError, KeyError, TypeError, csv.Error) as e:
        _power_error(str(e))


power_rows = _read_power_csv()
p6 = [dict(r, label=f"{r['tok_per_sec']:g}  n={r['n']}") for r in power_rows
      if r['condition'] == 'stock' or r['condition'].startswith('uv-')]
sub6 = NO_UNDERVOLT if not any(r['voltage_offset_mv'] is not None and r['voltage_offset_mv'] < 0 for r in power_rows) else 'Measured settings with recorded acceptance evidence; absent cells remain unmeasured'


def _power_facet(box, metric):
    rows = [r for r in p6 if r['box'] == box and r['metric'] == metric]
    # Same metric shares its zero-based domain across boxes; decode and prefill do not.
    scale, ticks = tps_axis([r['tok_per_sec'] * 1.15 for r in p6 if r['metric'] == metric],
                            step=25 if metric == 'decode' else 1000)
    title = 'Decode (generated tok/s)' if metric == 'decode' else 'Prefill 8k (prompt tok/s)'
    return {
        'title': {'text': title, 'color': FG, 'fontSize': 14, 'anchor': 'start'},
        'width': 460, 'height': 245, 'data': {'values': rows},
        'encoding': {
            'x': {'field': 'model', 'type': 'nominal', 'title': None,
                  'sort': sorted({r['model'] for r in rows}),
                  'axis': {'labelAngle': 0, 'labelLimit': 225, 'labelFontSize': 10}},
            'xOffset': {'field': 'condition', 'sort': sorted({r['condition'] for r in rows})}},
        'layer': [
            {'mark': {'type': 'bar', 'cornerRadiusEnd': 3},
             'encoding': {'y': {'field': 'tok_per_sec', 'type': 'quantitative',
                                'title': title, 'scale': scale, 'axis': ticks},
                          'color': {'field': 'condition', 'type': 'nominal',
                                    'scale': {'domain': sorted({r['condition'] for r in p6}),
                                              'range': [MUTED, '#f7b801', '#4cc9f0', '#b5179e', '#43e97b']},
                                    'legend': {'title': 'condition', 'orient': 'bottom'}}}},
            {'mark': {'type': 'text', 'dy': -9, 'fontSize': 12, 'fontWeight': 'bold', 'color': FG},
             'encoding': {'y': {'field': 'tok_per_sec', 'type': 'quantitative'}, 'text': {'field': 'label'}}},
        ]}


panel6 = {
    'title': {'text': 'Power study — stock baseline', 'subtitle': [sub6,
              'Short-prompt decode and nonced prefill are separate measurements. Sustain and parity remain in the CSV.'],
              'anchor': 'start', 'fontSize': 19, 'color': FG, 'subtitleColor': MUTED, 'subtitleFontSize': 12},
    'vconcat': [
        {'title': {'text': 'Box A · 2 × RTX 5060 Ti · both-dgpu',
                   'subtitle': ['stock only; no tuning planned', '180 W cap per card; reported watts sum both cards'],
                   'anchor': 'start', 'fontSize': 16, 'color': FG, 'subtitleColor': MUTED},
         'hconcat': [_power_facet('box-a', 'decode'), _power_facet('box-a', 'prefill')], 'spacing': 35},
        {'title': {'text': 'Box B · Radeon AI PRO R9700 · dgpu-b',
                   'subtitle': ['300 W board cap · Vulkan (RADV GFX1201)', 'Different silicon and serving stack; an operating comparison'],
                   'anchor': 'start', 'fontSize': 16, 'color': FG, 'subtitleColor': MUTED},
         'hconcat': [_power_facet('box-b', 'decode'), _power_facet('box-b', 'prefill')], 'spacing': 35}],
    'spacing': 45, 'resolve': {'scale': {'y': 'independent'}}}


def _published_tolerance(text):
    """Half a unit in the last published digit.

    A cell written as `2114` cannot be compared to a raw median of 2113.615 with a fixed
    tolerance without either rejecting honest rounding or swallowing a wrong digit. The
    precision of the published string is the tolerance: `2114` admits +/-0.5, `106.4`
    admits +/-0.05, so 106.9 against a raw 106.43 still fails.
    """
    text = (text or '').strip()
    if '.' in text:
        return 0.5 * (10 ** -len(text.split('.')[1]))
    return 0.5


def _verify_power_against_raw():
    """Re-derive every published cell from the retained per-run records and telemetry.

    Written after two wrong cells shipped past checks that only compared the CSV with the
    chart: the sustain temperature published the memory sensor as junction, and box-a
    wattage published a one-card median for a two-card box. Internal consistency cannot see
    either, because the CSV agreed with the chart and the chart agreed with the CSV.

    SCOPE: this detects transcription and aggregation errors against the retained records —
    the class that actually shipped here twice. It is not a forgery detector: an author who
    edits a record and its summary consistently defeats any check that lives in the same
    tree. What it does buy is that every published number has to survive being recomputed
    from the tokens, nanoseconds and telemetry samples the server and driver returned.

    Adversarial review then showed the first version of this check FAILED OPEN in several
    ways at once: it skipped wattage entirely when telemetry was missing, ignored the rate
    column it does not plot, accepted a scalar temperature against a named-sensor claim, and
    used tolerances loose enough to swallow a wrong digit. Absence of evidence is now an
    error, both rates are checked, and every tolerance is tight enough that only rounding
    fits inside it.
    """
    import statistics
    with open(os.path.join(SOURCE_DIR, 'power_undervolt.csv'), encoding='utf-8') as _fh:
        raw_text = {(row['condition'], row['box'], row['model'], row['metric']): row
                    for row in csv.DictReader(_fh)}
    checked = 0
    for r in _read_power_csv():
        text = raw_text[(r['condition'], r['box'], r['model'], r['metric'])]
        note = r['notes'] or ''
        if 'label=' not in note or '.jsonl' not in note:
            _power_error(f"row does not name its raw record: {r['condition']} {r['model']}")
        fname = note.split(';')[0].strip()
        label = note.split('label=')[1].split(';')[0].strip()
        path = os.path.join(SOURCE_DIR, fname)
        if not os.path.isfile(path):
            _power_error(f'named raw record is missing: {fname}')
        runs = []
        with open(path, encoding='utf-8') as fh:
            for line in fh:
                if line.strip():
                    rec = json.loads(line)
                    if rec.get('label') == label and not rec.get('warmup'):
                        runs.append(rec)
        if not runs:
            _power_error(f'no non-warmup runs for {label} in {fname}')
        if len(runs) != r['n']:
            _power_error(f"n disagrees with the raw record for {label}: csv {r['n']}, file {len(runs)}")

        # Identity: the row must describe the runs it points at.
        tags = {x.get('model') for x in runs}
        if len(tags) != 1:
            _power_error(f'{label}: the raw runs do not share one model tag: {sorted(tags)}')
        tag = tags.pop() or ''
        # A prefix test is the right direction (the box-b runs used a locally aliased tag,
        # `qwen3.8:27b-r9700`) and the wrong bound: it also accepts a truncated CSV name like
        # `gemma`. Require the raw tag to be the published name exactly, or that name followed
        # by a suffix that begins with a hyphen.
        ALIAS_SUFFIXES = ('-r9700',)  # the box-b tags that pin a model to the discrete card
        if not (tag == r['model'] or any(tag == r['model'] + suf for suf in ALIAS_SUFFIXES)):
            _power_error(f"{label}: csv model {r['model']!r} does not match the raw tag {tag!r}")
        t0 = min(x['t_start'] for x in runs)
        days = {time.strftime('%Y-%m-%d', time.localtime(t0)), time.strftime('%Y-%m-%d', time.gmtime(t0))}
        if r['date'] not in days:
            _power_error(f"{label}: csv date {r['date']} but the runs started on {sorted(days)} "
                         f"(local or UTC)")

        # A retained run that aborted is not evidence of a completed measurement.
        if any(x.get('aborted') for x in runs):
            _power_error(f'{label}: a retained request is marked aborted; it cannot back a published cell')
        # Two requests cannot occupy the same window: that is one measurement counted twice.
        windows = [(x['t_start'], x['t_end']) for x in runs]
        if len(set(windows)) != len(windows):
            _power_error(f'{label}: retained requests share a window, so one measurement is counted twice')
        # Rates are recomputed from the counts and durations the server returned, not read from
        # the rate fields. A rate field can be edited to match a wrong published cell; the tokens
        # and nanoseconds it was derived from would have to be edited consistently too.
        for count_k, dur_k, col in (('eval_count', 'eval_duration', 'gen_tok_per_sec'),
                                    ('prompt_eval_count', 'prompt_eval_duration', 'prompt_tok_per_sec')):
            if all(x.get(count_k) and x.get(dur_k) for x in runs):
                derived = statistics.median(x[count_k] / (x[dur_k] / 1e9) for x in runs)
                if abs(derived - r[col]) > max(_published_tolerance(text[col]), derived * 0.005):
                    _power_error(f'{label}: published {col}={r[col]} but {count_k}/{dur_k} give '
                                 f'{derived:.3f}')

        # BOTH rates, not only the one this row plots. A wrong number in the column the
        # chart ignores is still a published wrong number.
        for field, col in (('gen_toks_per_s', 'gen_tok_per_sec'),
                           ('prompt_toks_per_s', 'prompt_tok_per_sec')):
            raw = statistics.median(x[field] for x in runs)
            if abs(raw - r[col]) > _published_tolerance(text[col]):
                _power_error(f'{label}: published {col}={r[col]} but the raw median is {raw:.3f}')
        plotted = r['gen_tok_per_sec'] if r['tok_source'] == 'gen' else r['prompt_tok_per_sec']
        if abs(plotted - r['tok_per_sec']) > _published_tolerance(text['tok_per_sec']):
            _power_error(f'{label}: tok_per_sec does not equal the {r["tok_source"]} column')

        # Temperature: a maximum across CARDS is the hotter card and is legitimate; a maximum
        # across SENSORS is a different quantity and is not. A scalar cannot support a
        # named-sensor claim at all, so it is an error rather than a pass.
        # Temperature comes from the telemetry samples inside each window, not from the run
        # record's own temp_max summary: that field is the harness's aggregate, and an edited
        # summary would otherwise certify itself. The record's field is still checked for
        # shape, because a bare number cannot support a named-sensor claim at all.
        temps = []
        for x in runs:
            t = x['temp_max']
            if not isinstance(t, dict):
                _power_error(f'{label}: temperature is a bare number, so a {r["temp_kind"]} claim cannot be checked')
            if r['temp_kind'] == 'junction':
                if 'junction' not in t:
                    _power_error(f'{label}: raw record has no junction sensor')
                temps.append(t['junction'])
            elif r['temp_kind'] == 'nvidia-smi':
                if not t or not all(k.startswith('gpu') for k in t):
                    _power_error(f'{label}: nvidia record is not keyed by card: {sorted(t)}')
                temps.append(max(t.values()))
            else:
                _power_error(f'{label}: unknown temp_kind {r["temp_kind"]}')
        tel_path_early = os.path.join(SOURCE_DIR, fname + '.telemetry.jsonl')
        if os.path.isfile(tel_path_early):
            tsamp = []
            with open(tel_path_early, encoding='utf-8') as th:
                for line in th:
                    if not line.strip():
                        continue
                    smp = json.loads(line)
                    if not any(x['t_start'] <= smp['ts'] <= x['t_end'] for x in runs):
                        continue
                    if r['temp_kind'] == 'junction':
                        v = (smp.get('temps') or {}).get('junction')
                    else:
                        v = smp.get('temp')
                    if v is not None:
                        tsamp.append(v)
            if tsamp and max(tsamp) != r['temp_max_c']:
                _power_error(f'{label}: published {r["temp_max_c"]} C but the telemetry samples in '
                             f'these windows peak at {max(tsamp):g} C on {r["temp_kind"]}')
        if max(temps) != r['temp_max_c']:
            _power_error(f'{label}: published {r["temp_max_c"]} C as {r["temp_kind"]} but that '
                         f'sensor peaked at {max(temps):g} C')

        # Hashes: the CSV claims k/n agreement, so re-derive it. A nonced row claims none.
        shas = [x.get('response_sha') for x in runs]
        claim = (r.get('hashes') or '').strip()
        nonce_claim = claim.lower().startswith('n/a')
        if claim and '/' in claim and not nonce_claim:
            k_txt, n_txt = claim.split('/', 1)
            if not all(shas):
                _power_error(f'{label}: hashes claimed {claim} but a retained run has no response hash')
            agree = max(sum(1 for h in shas if h == cand) for cand in set(shas))
            if (int(k_txt), int(n_txt)) != (agree, len(shas)):
                _power_error(f'{label}: hashes claimed {claim} but the retained runs agree {agree}/{len(shas)}')
        elif claim and not nonce_claim:
            _power_error(f'{label}: unreadable hashes field {claim!r}')
        elif nonce_claim:
            # 'n/a (nonce)' is a claim about the METHOD: every retained request carried a nonce,
            # so its output legitimately differs and no agreement can be asserted. One flagged
            # run does not establish it for five, and differing hashes are a consequence, not
            # evidence. Require the flag on every run.
            flagged = sum(1 for x in runs if x.get('nonce'))
            if flagged != len(runs):
                _power_error(f'{label}: claims {claim!r} but only {flagged} of {len(runs)} retained '
                             f'requests carry the nonce flag')

        # Clocks: re-derive median and minimum over the active samples, same as the harness.
        # Watts: required, never skipped. Keep a timestamp when any device is active, sum the
        # whole device set at it. Missing telemetry is an error: the earlier version treated
        # it as nothing to check, which is how a wrong wattage could pass with the evidence
        # simply deleted.
        tel_path = os.path.join(SOURCE_DIR, fname + '.telemetry.jsonl')
        if not os.path.isfile(tel_path):
            _power_error(f'{label}: telemetry {os.path.basename(tel_path)} is missing, so the '
                         f'published wattage cannot be checked')
        by_ts = {}
        with open(tel_path, encoding='utf-8') as th:
            for line in th:
                if line.strip():
                    smp = json.loads(line)
                    by_ts.setdefault(smp['ts'], []).append(smp)
        if r['power_scope'] == 'sum_two_cards' if 'power_scope' in r else (r['box'] == 'box-a'):
            idx = {q.get('index') for rows_at in by_ts.values() for q in rows_at
                   if any(x['t_start'] <= rows_at[0]['ts'] <= x['t_end'] for x in runs)}
            if len({i for i in idx if i is not None}) < 2:
                _power_error(f'{label}: a two-card sum is published but the telemetry in these '
                             f'windows carries {sorted(i for i in idx if i is not None)}')
        per_run, peak = [], 0.0
        for x in runs:
            ps = []
            for ts, rows_at in by_ts.items():
                if not (x['t_start'] <= ts <= x['t_end']):
                    continue
                busy = [q for q in rows_at
                        if (q.get('util') if q.get('util') is not None else q.get('gpu_busy')) is not None
                        and (q.get('util') if q.get('util') is not None else q.get('gpu_busy')) >= 10]
                total = sum(q['power'] for q in rows_at if q.get('power') is not None)
                # The MEDIAN is over active timestamps; the MAXIMUM is over the whole window,
                # which is what the harness publishes. Taking the maximum over active
                # timestamps only lets a high sample at an idle timestamp hide inside the
                # window, so an understated peak would pass.
                peak = max(peak, total)
                if busy:
                    ps.append(total)
            if not ps:
                _power_error(f'{label}: no telemetry covers one of the retained requests, so its '
                             f'wattage is unverified')
            per_run.append(statistics.median(ps))
        # Clocks: per-run median first, then the median across runs — the same shape the
        # harness uses for every other statistic. Pooling every sample instead gives a
        # different number (2794 vs the published 2790 on box-a MoE decode), which would be
        # a false alarm, not a finding.
        per_run_sclk, per_run_min = [], []
        for x in runs:
            vals = []
            for ts, rows_at in by_ts.items():
                if not (x['t_start'] <= ts <= x['t_end']):
                    continue
                for q in rows_at:
                    u = q.get('util') if q.get('util') is not None else q.get('gpu_busy')
                    if u is not None and u >= 10 and q.get('sclk') is not None:
                        vals.append(q['sclk'])
            if vals:
                per_run_sclk.append(statistics.median(vals))
                per_run_min.append(min(vals))
        if not per_run_sclk:
            _power_error(f'{label}: no active telemetry sample carries a clock, so the published '
                         f'clocks cannot be checked')
        if abs(statistics.median(per_run_sclk) - r['sclk_med_mhz']) > _published_tolerance(text['sclk_med_mhz']):
            _power_error(f'{label}: published sclk median {r["sclk_med_mhz"]} but the retained samples '
                         f'give {statistics.median(per_run_sclk):.0f}')
        if abs(min(per_run_min) - r['sclk_min_mhz']) > _published_tolerance(text['sclk_min_mhz']):
            _power_error(f'{label}: published sclk minimum {r["sclk_min_mhz"]} but the retained samples '
                         f'give {min(per_run_min):.0f}')
        raw_w = statistics.median(per_run)
        if abs(raw_w - r['watts_med']) > _published_tolerance(text['watts_med']):
            _power_error(f'{label}: published {r["watts_med"]} W but the telemetry gives {raw_w:.1f} W '
                         f'when every device is summed at each active timestamp')
        if abs(peak - r['watts_max']) > _published_tolerance(text['watts_max']):
            _power_error(f'{label}: published max {r["watts_max"]} W but the telemetry peaks at {peak:.1f} W')
        # The parity row's published claim is that the output was IDENTICAL to stock after the
        # overdrive bit was exposed. That is a cross-file claim, so check it across files rather
        # than trusting the prose.
        if r['condition'] == 'overdrive_exposed':
            stock_sha = set()
            with open(os.path.join(SOURCE_DIR, 'boxb_v2.jsonl'), encoding='utf-8') as sh:
                for line in sh:
                    if line.strip():
                        rec = json.loads(line)
                        if rec.get('label') == 'B-W2-dense-decode' and not rec.get('warmup'):
                            stock_sha.add(rec.get('response_sha'))
            post = {x.get('response_sha') for x in runs}
            if not stock_sha or post != stock_sha:
                _power_error('the parity row claims the post-overdrive output is identical to stock, '
                             'but the retained hashes differ from the stock dense-decode run')
        checked += 1
    # Evidence substitution: two summaries must not rest on the same measured window. A
    # record that borrows another request's t_start/t_end would let one real measurement
    # certify two published rows.
    spans = []
    for row in _read_power_csv():
        note = row['notes'] or ''
        fname = note.split(';')[0].strip()
        label = note.split('label=')[1].split(';')[0].strip()
        with open(os.path.join(SOURCE_DIR, fname), encoding='utf-8') as fh:
            for line in fh:
                if line.strip():
                    rec = json.loads(line)
                    if rec.get('label') == label and not rec.get('warmup'):
                        spans.append((fname, label, rec['t_start'], rec['t_end']))
    for i, (f1, l1, a1, b1) in enumerate(spans):
        for f2, l2, a2, b2 in spans[i + 1:]:
            if f1 == f2 and l1 != l2 and a1 < b2 and a2 < b1:
                _power_error(f'{l1} and {l2} claim overlapping measurement windows in {f1}; one '
                             f'run cannot be evidence for two published summaries')
    conditions = {row['condition'] for row in _read_power_csv()}
    for required in ('stock_sustain', 'overdrive_exposed'):
        if required not in conditions:
            _power_error(f'the {required} row is missing; the documents describe ten rows and the '
                         f'sustain and parity records are part of the published evidence')
    if checked != 10:
        _power_error(f'expected ten published summaries, found {checked}')
    print(f'raw cross-check: OK — {checked} summaries re-derived from the retained records '
          f'(both rates, the named sensor, and both power statistics)')


def _verify_power_csv():
    source = {tuple(r[k] for k in POWER_KEY): r for r in _read_power_csv()}
    expected = {k for k, r in source.items() if r['condition'] == 'stock' or r['condition'].startswith('uv-')}
    plotted = []
    for box in panel6['vconcat']:
        for facet in box['hconcat']:
            for r in facet['data']['values']:
                key = tuple(r[k] for k in POWER_KEY)
                if key not in expected or any(r[k] != source[key][k] for k in POWER_COLUMNS):
                    _power_error(f'plotted row differs from source: {key}')
                if r['label'] != f"{source[key]['tok_per_sec']:g}  n={source[key]['n']}":
                    _power_error(f'bar label differs from source: {key}')
                if ('prefill' in facet['title']['text'].lower()) != (r['metric'] == 'prefill'):
                    _power_error(f'facet names the wrong metric: {key}')
                plotted.append(key)
    if len(plotted) != len(expected) or set(plotted) != expected:
        _power_error('plot dropped or duplicated eligible rows')
    absent = not any(r['voltage_offset_mv'] is not None and r['voltage_offset_mv'] < 0 for r in source.values())
    if (NO_UNDERVOLT in ' '.join(panel6['title']['subtitle'])) != absent:
        _power_error('subtitle disagrees with measured voltage offsets')
    print(f'power consistency gate: OK — {len(source)} summaries; {len(plotted)} plotted bars; sustain/parity excluded; subtitle checked')


_verify_power_csv()
_verify_power_against_raw()


def _png_description(png, description):
    """Retain the human-readable subtitle as standard PNG Description metadata."""
    import struct, zlib
    data = b'Description\x00' + description.encode('latin-1', errors='replace')
    chunk = b'tEXt' + data
    # Insert after IHDR, before image data; pixel bytes are untouched.
    end = 8 + 12 + struct.unpack('>I', png[8:12])[0]
    return png[:end] + struct.pack('>I', len(data)) + chunk + struct.pack('>I', zlib.crc32(chunk)) + png[end:]


COMMON = {
    "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
    "background": BG,
    "config": {"view": {"stroke": None}, "axis": AX, "font": "DejaVu Sans", "padding": 22,
               "title": {"color": FG}, "legend": {"labelColor": FG, "titleColor": FG}},
}

import os
OUT = os.path.dirname(os.path.abspath(__file__)) + "/"
for name, panel in [("01_peak_throughput", panel1),
                    ("02_before_after", panel2),
                    ("03_weights_vs_vram", panel3),
                    ("04_cross_box", panel4),
                    ("05_device_split", panel5),
                    ("06_undervolt_vs_stock", panel6)]:
    spec = dict(COMMON); spec.update(panel)
    png = vlc.vegalite_to_png(json.dumps(spec), scale=2)
    if name == "06_undervolt_vs_stock":
        png = _png_description(png, sub6)
    open(OUT + name + ".png", "wb").write(png)
    print(f"  {name}.png  {len(png):,} bytes")
