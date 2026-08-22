#!/usr/bin/env python3
"""Fleet throughput composite via Vega-Lite + vl-convert. MEASURED DATA ONLY."""
import json, vl_convert as vlc

BG, FG, MUTED, GRID = "#12141a", "#e8eaf0", "#98a0b3", "#2a2f3a"
FAM = {"qwen": "#4cc9f0", "gemma": "#b5179e", "ornith": "#f7b801", "lfm": "#43e97b",
       "deepseek": "#f56565", "glm": "#9d8df1", "qwythos": "#8d99ae"}

peak = [
    ("LFM2.5-8B-A1B",         230.0, "lfm",      "5.2 GB · Q4_K_M · MoE A1B · decode · best of 2"),
    ("Ornith-1.0-35B-A3B",    199.9, "ornith",   "21 GB · Q4_K_M · MoE A3B · llama.cpp -ngl 24 · best of 4"),
    ("Ornith-1.0-35B",        115.0, "ornith",   "21 GB · Q4_K_M · MoE A3B · single run"),
    ("qwen3.6:35b-a3b",       114.43, "qwen",    "box-a · both-dgpu · Q4_K_M · n=1"),
    ("qwen3-coder:30b",       111.7, "qwen",     "box-b · dgpu-b · quant unknown · n=1"),
    ("gemma4:26b-a4b-it-qat", 105.2, "gemma",    "box-b · dgpu-b · q4_0-qat · n=2"),
    ("GLM-4.7-Flash",         105.0, "glm",      "18 GB · Q4_K_M · llama-server · best of 2"),
    ("GLM-4.7-Flash-GGUF",    105.0, "glm",      "box-a · both-dgpu · quant unknown · n=1"),
    ("Ornith-1.0-9B",          67.0, "ornith",   "5.6 GB · Q4_K_M · 9B dense · single run"),
    ("qwen3.8:27b-devicepinned", 56.4, "qwen",   "box-b · dgpu-b · device-pinned build · n=1"),
    ("gemma4:12b",             54.0, "gemma",    "7.6 GB · Q4_K_M · 11.9B dense · best of 3"),
    ("qwen3.8:27b",            46.0, "qwen",     "box-b · dgpu-b · quant unknown · n=1"),
    ("qwen3:14b",              42.5, "qwen",     "Q4_K_M · 14B · ollama · single run"),
    ("deepseek-r1:14b",        42.4, "deepseek", "9.0 GB · Q4_K_M · 14.8B · single run"),
    ("muse-glimmer",           33.8, "qwythos",  "box-b · dgpu-b · llama-server spec-decode · n=3"),
    ("gemma4:31b-it-qat",      28.25, "gemma",   "box-b · dgpu-b · q4_0-qat · n=2"),
    ("Qwythos-9B",             28.0, "qwythos",  "9B · decode, short context · best of 2"),
    ("gemma4:26b-a4b-it-bf16", 13.0, "gemma",    "box-b · igpu · bf16 · n=1 · dumped"),
    ("gemma4:31b-it-q8_0",      6.6, "gemma",    "box-b · igpu · q8_0 · n=1 · dumped"),
    ("gemma4:31b-it-bf16",      3.6, "gemma",    "box-b · igpu · bf16 · n=1 · dumped"),
]
p1 = [{"model": m, "tps": v, "family": f, "detail": d} for m, v, f, d in peak]

ab = [
    ("gemma4 31b→26b-a4b · 18-issue corroborate",  21.0, 100.0, "swap", "same task · 18/18 vs 16/18"),
    ("gemma4 31b→26b-a4b · 9.3K-tok artifact",     19.0,  90.0, "swap", "same task · quality parity"),
    ("gemma4 31b→26b-a4b · 5-seeded-bug review",   20.4,  80.6, "swap", "same task · 5/5 vs 4/5"),
    ("qwen3-coder:30b · spec-decode ngram-mod",    95.6, 108.1, "tune", "same model + same prompt"),
    ("Ornith-35B-A3B · MoE accel (-ngl 8)",       115.7, 114.1, "tune", "claimed win did NOT reproduce"),
    ("Ornith-35B-A3B · MoE accel (-ngl 24)",      199.9, 188.7, "tune", "claimed win did NOT reproduce"),
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
sub2 = (f"mean of the {len(pos)} that improved: +{sum(pos)/len(pos):.1f}%   ·   "
        f"mean of the {len(neg)} that did not: {sum(neg)/len(neg):.1f}%   ·   "
        f"model-swap group: +{sum(swap)/len(swap):.1f}%   ·   same-model tuning: +{pos[-1]:.1f}%")

AX = {"labelColor": FG, "titleColor": FG, "gridColor": GRID, "domainColor": GRID,
      "tickColor": GRID, "labelFontSize": 11, "titleFontSize": 12}
order = [m for m, *_ in peak]

ROWH1 = 34
p1_text = {
    "width": 300, "height": len(peak) * ROWH1,
    "data": {"values": p1},
    "encoding": {"y": {"field": "model", "type": "nominal", "sort": order, "title": None,
                       "axis": None, "scale": {"paddingInner": 0.22}}},
    "layer": [
        {"mark": {"type": "text", "align": "right", "x": 300, "dy": -6,
                  "fontSize": 12.5, "fontWeight": "bold", "color": FG, "limit": 298},
         "encoding": {"text": {"field": "model"}}},
        {"mark": {"type": "text", "align": "right", "x": 300, "dy": 8,
                  "fontSize": 8.6, "color": MUTED, "limit": 298},
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
                            "scale": {"domain": [0, 250]}, "axis": {"tickCount": 6}},
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
panel1 = {
    "title": {"text": "Local model throughput — two-box operating log",
              "subtitle": ["Best recorded row per model. Mixed serving stacks and boxes are directional operating data, not a controlled cross-vendor benchmark.",
                           "Every single-run chart row states n=1; box and device sit under each model name."],
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
                            "scale": {"domain": [0, 250]}, "axis": {"tickCount": 6}},
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
    "title": {"text": "Before / after — every A/B measured here, same box, same task",
              "subtitle": [sub2,
                           "Grey dot = before. Coloured dot = after. Two of six are negatives: a claimed MoE-offload speed-up that did not reproduce."],
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
         "encoding": {"x": {"field": "gb", "type": "quantitative", "title": "gigabytes",
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
p4 = [
    {"model": "gemma4:26b-a4b-it-qat", "side": "box-a / dgpu-a", "tps": 83.3, "label": "83.3  n=1"},
    {"model": "gemma4:26b-a4b-it-qat", "side": "box-b / dgpu-b", "tps": 105.2, "label": "105.2  n=2"},
    {"model": "gemma4:31b-it-qat", "side": "box-a / dgpu-a", "tps": 18.2, "label": "18.2  n=1"},
    {"model": "gemma4:31b-it-qat", "side": "box-b / dgpu-b", "tps": 28.25, "label": "28.25  n=2"},
    {"model": "qwen3-coder:30b", "side": "box-a / dgpu-a", "tps": 35.6, "label": "35.6  n=1"},
    {"model": "qwen3-coder:30b", "side": "box-b / dgpu-b", "tps": 111.7, "label": "111.7  n=1"},
    {"model": "qwen3.8:27b", "side": "box-a / dgpu-a", "tps": 21.2, "label": "21.2  n=1"},
    {"model": "qwen3.8:27b", "side": "box-b / dgpu-b", "tps": 46.0, "label": "46.0  n=1"},
]
panel4 = {
    "title": {"text": "Cross-box throughput — identical model, different silicon",
              "subtitle": ["Grouped bars show the published CSV cell value, with box/device and n attached.",
                           "Different vendors and serving stacks make this an operating comparison, not a controlled benchmark."],
              "anchor": "start", "color": FG, "fontSize": 18, "subtitleColor": MUTED, "subtitleFontSize": 11.5},
    "width": 900, "height": 280, "data": {"values": p4},
    "encoding": {"x": {"field": "model", "type": "nominal", "title": None}, "xOffset": {"field": "side"}},
    "layer": [
        {"mark": {"type": "bar", "cornerRadiusEnd": 3},
         "encoding": {"y": {"field": "tps", "type": "quantitative", "title": "tokens per second", "scale": {"zero": True}},
                      "color": {"field": "side", "type": "nominal", "scale": {"domain": ["box-a / dgpu-a", "box-b / dgpu-b"], "range": ["#4cc9f0", "#b5179e"]}, "legend": {"title": None, "orient": "bottom"}}}},
        {"mark": {"type": "text", "dy": -7, "fontSize": 10.5, "color": FG},
         "encoding": {"y": {"field": "tps", "type": "quantitative"}, "text": {"field": "label"}}},
    ],
}
p5 = [
    {"model": "gemma4:26b-a4b-it-qat", "device": "dgpu-b", "tps": 105.2, "label": "105.2  n=2", "ratio": "1.6× dGPU / iGPU"},
    {"model": "gemma4:26b-a4b-it-qat", "device": "igpu", "tps": 66.25, "label": "66.25  n=2", "ratio": ""},
    {"model": "gemma4:31b-it-qat", "device": "dgpu-b", "tps": 28.25, "label": "28.25  n=2", "ratio": "2.4× dGPU / iGPU"},
    {"model": "gemma4:31b-it-qat", "device": "igpu", "tps": 12.0, "label": "12.0  n=2", "ratio": ""},
]
panel5 = {
    "title": {"text": "Box-b device split — dGPU vs iGPU",
              "subtitle": ["Cell means from two reps per device; ratio is dGPU-b / iGPU.",
                           "Functional-task quality was T1 8/8 for all four displayed cells."],
              "anchor": "start", "color": FG, "fontSize": 18, "subtitleColor": MUTED, "subtitleFontSize": 11.5},
    "width": 700, "height": 270, "data": {"values": p5},
    "encoding": {"x": {"field": "model", "type": "nominal", "title": None}, "xOffset": {"field": "device"}},
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
    alias = {"Ornith-1.0-35B-A3B": "Ornith-1.0-35B-A3B-MoE-Q4", "qwen3.6:35b-a3b": "qwen3.6:35b-a3b-q4_K_M"}
    bad = []
    for name, v, _fam, _detail in peak:
        key = alias.get(name, name)
        if key not in best:
            bad.append(f"{name}: not present in CSV"); continue
        if abs(best[key] - v) > 0.051:
            bad.append(f"{name}: chart {v} vs CSV {best[key]}")
    for row in p4 + p5:
        if (row["model"], row["tps"]) not in values:
            bad.append(f"{row['model']}: chart {row['tps']} not present in CSV")
    if bad:
        sys.stderr.write("make_charts: chart table DISAGREES with the CSV record:\n")
        for b in bad: sys.stderr.write(f"  - {b}\n")
        sys.exit(1)
    print(f"consistency gate: OK — {len(peak)} peak rows and {len(p4) + len(p5)} two-box rows match the CSV record")

_verify_against_csv()


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
                    ("05_device_split", panel5)]:
    spec = dict(COMMON); spec.update(panel)
    png = vlc.vegalite_to_png(json.dumps(spec), scale=2)
    open(OUT + name + ".png", "wb").write(png)
    print(f"  {name}.png  {len(png):,} bytes")
