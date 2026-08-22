#!/usr/bin/env python3
"""jb-device-split-bench — measure decode tok/s for a model on BOTH GPUs of this box.

Why it exists: the 2026-08-22 gemma device-split cells were produced by an ad-hoc inline
command, so the numbers could not be reproduced or extended to other models. This is that
measurement, written down.

Method. ollama honours options.main_gpu PER REQUEST (verified 2026-08-21), so the same model
tag is driven onto device 0 (R9700 discrete) and device 1 (Strix Halo iGPU) with everything
else held constant. num_ctx is pinned for BOTH devices, not just the discrete one: KV cache is
a placement constraint, and letting it differ would make the comparison measure context size
as well as device. The model is unloaded between devices, otherwise the second request reuses
the resident copy on the FIRST device and silently reports that device twice — the failure
this whole benchmark exists to detect.

Reported figure is decode (eval) rate only: eval_count / eval_duration. Prompt-processing is
excluded because it is a different bottleneck and mixing them hides the decode difference.
"""
import argparse, json, subprocess, sys, time, urllib.request

API = "http://127.0.0.1:11434/api"
DEVICES = {0: "dgpu-b", 1: "igpu"}
PROMPT = ("Explain, in careful detail, how a write-ahead log keeps a database consistent "
          "across an unclean shutdown. Cover the ordering guarantees it relies on.")


def post(path, body, timeout):
    req = urllib.request.Request(f"{API}/{path}",
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def unload(model):
    subprocess.run(["ollama", "stop", model], capture_output=True)
    time.sleep(3)


def one_rep(model, device, num_ctx, num_predict, timeout):
    r = post("generate", {
        "model": model, "prompt": PROMPT, "stream": False,
        "options": {"main_gpu": device, "num_ctx": num_ctx,
                    "num_predict": num_predict, "temperature": 0},
    }, timeout)
    ec, ed = r.get("eval_count", 0), r.get("eval_duration", 0)
    if not ec or not ed:
        return None
    return round(ec / (ed / 1e9), 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("models", nargs="+")
    ap.add_argument("--reps", type=int, default=2)
    ap.add_argument("--num-ctx", type=int, default=32768)
    ap.add_argument("--num-predict", type=int, default=200)
    ap.add_argument("--timeout", type=int, default=900)
    a = ap.parse_args()

    out = {}
    for model in a.models:
        out[model] = {}
        for dev, label in DEVICES.items():
            unload(model)
            # Warm-up is DISCARDED: the first request pays model load from disk, which is not
            # decode rate. Including it would understate whichever device loaded cold.
            if one_rep(model, dev, a.num_ctx, a.num_predict, a.timeout) is None:
                print(f"{model}@{label}: WARMUP FAILED", flush=True); out[model][label] = None; continue
            reps = [one_rep(model, dev, a.num_ctx, a.num_predict, a.timeout) for _ in range(a.reps)]
            if any(r is None for r in reps):
                print(f"{model}@{label}: REP FAILED {reps}", flush=True); out[model][label] = None; continue
            mean = round(sum(reps) / len(reps), 2)
            out[model][label] = {"reps": reps, "mean": mean}
            print(f"{model}@{label}: decode {reps} tok/s | mean {mean}", flush=True)
        unload(model)
        c = out[model]
        if c.get("dgpu-b") and c.get("igpu"):
            print(f"{model}: RATIO dGPU/iGPU = "
                  f"{round(c['dgpu-b']['mean'] / c['igpu']['mean'], 2)}x", flush=True)
    print("JSON " + json.dumps(out), flush=True)


if __name__ == "__main__":
    sys.exit(main())
