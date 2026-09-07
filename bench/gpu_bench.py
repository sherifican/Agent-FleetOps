#!/usr/bin/env python3
"""Read-only LLM inference benchmark + GPU telemetry harness (stdlib only)."""
import argparse, glob, hashlib, json, os, statistics, subprocess, sys, threading, time
import urllib.error, urllib.request

SAMPLES, TEL_ERR = [], []
LOCK, ABORT, STOP = threading.Lock(), threading.Event(), threading.Event()
NVQ = ("index,clocks.sm,clocks.mem,power.draw,power.limit,temperature.gpu,"
       "fan.speed,utilization.gpu,clocks_event_reasons.active")

def die(msg, code=1):
    print(msg, file=sys.stderr)
    raise SystemExit(code)

def _read(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read().strip()

def num(x):
    x = x.strip()
    if x.upper() in ("", "N/A", "[N/A]", "[NOT SUPPORTED]", "NOT ACTIVE"):
        return None
    try:
        return float(x)
    except ValueError:
        return x

def parse_dpm(text):
    """Active DPM MHz from the '*' line. Accepts 'S:' levels; None if no star."""
    for line in (text or "").splitlines():
        if not line.rstrip().endswith("*"):
            continue
        tok = line.replace("*", "").replace(":", " ")
        for a in ("Mhz", "MHz", "mhz"):
            tok = tok.replace(a, " ")
        nums = [float(p) for p in tok.split() if p.replace(".", "", 1).isdigit()]
        if nums:
            return nums[-1]
    return None

def or_reasons(vals):
    acc = 0
    for v in vals:
        s = "" if v is None else str(v).strip()
        if not s or s.upper() in ("N/A", "[N/A]", "NOT ACTIVE"):
            continue
        try:
            acc |= int(s, 16) if "x" in s.lower() else int(float(s))
        except ValueError:
            pass
    return hex(acc)

def row_max_temp(r):
    xs = ([r["temp"]] if r.get("temp") is not None else [])
    xs += [v for v in (r.get("temps") or {}).values() if v is not None]
    return max(xs) if xs else 0.0

def sample_nvidia(indexes):
    cmd = ["nvidia-smi", f"--query-gpu={NVQ}", "--format=csv,noheader,nounits"]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=8)
    except FileNotFoundError as e:
        raise RuntimeError("nvidia-smi not found") from e
    if p.returncode != 0:
        raise RuntimeError(p.stderr.strip() or "nvidia-smi failed")
    rows = []
    for line in p.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [x.strip() for x in line.split(",")]
        if len(parts) < 9:
            raise RuntimeError(f"bad nvidia-smi row: {line}")
        idx = int(float(parts[0]))
        if indexes is not None and idx not in indexes:
            continue
        def f(i):
            v = num(parts[i])
            return None if isinstance(v, str) else v
        rows.append({"backend": "nvidia", "index": idx, "sclk": f(1), "mclk": f(2),
                     "power": f(3), "power_limit": f(4), "temp": f(5), "fan": f(6),
                     "util": f(7), "clocks_event_reasons": parts[8]})
    if not rows:
        raise RuntimeError("nvidia-smi returned no matching GPUs")
    return rows

def _hwmon_clocks(hwmons):
    actual = {}
    for hw in hwmons:
        for fin in sorted(glob.glob(os.path.join(hw, "freq*_input"))):
            lp = fin.replace("_input", "_label")
            lab = _read(lp).strip().lower() if os.path.isfile(lp) else ""
            mhz = int(_read(fin)) / 1e6
            base = os.path.basename(fin)
            if lab == "sclk" or (lab not in ("sclk", "mclk") and base.startswith("freq1")):
                actual["sclk"] = mhz
            elif lab == "mclk" or (lab not in ("sclk", "mclk") and base.startswith("freq2")):
                actual["mclk"] = mhz
    return actual

def _amd_power_cap(device):
    for hw in sorted(glob.glob(os.path.join(device, "hwmon", "hwmon*"))):
        p = os.path.join(hw, "power1_cap")
        if os.path.isfile(p):
            return int(_read(p)) / 1e6
    return None

def sample_amd(device, card):
    hwmons = sorted(glob.glob(os.path.join(device, "hwmon", "hwmon*")))
    if not hwmons:
        raise FileNotFoundError(f"{device}/hwmon/hwmon*")
    power, fan, temps = None, None, {}
    for hw in hwmons:
        pa = os.path.join(hw, "power1_average")
        if os.path.isfile(pa):
            power = int(_read(pa)) / 1e6
        for tin in sorted(glob.glob(os.path.join(hw, "temp*_input"))):
            lp = tin.replace("_input", "_label")
            lab = _read(lp) if os.path.isfile(lp) else os.path.basename(tin)
            temps[lab] = int(_read(tin)) / 1000.0
        fp = os.path.join(hw, "fan1_input")
        if os.path.isfile(fp):
            fan = int(_read(fp))
    bp = os.path.join(device, "gpu_busy_percent")
    if power is None or not temps or not os.path.isfile(bp):
        raise FileNotFoundError(f"incomplete amd telemetry under {device}")
    clocks = _hwmon_clocks(hwmons)
    sclk_level = parse_dpm(_read(os.path.join(device, "pp_dpm_sclk")))
    mclk_level = parse_dpm(_read(os.path.join(device, "pp_dpm_mclk")))
    sclk_actual, mclk_actual = clocks.get("sclk"), clocks.get("mclk")
    return {"backend": "amd", "card": card, "power": power, "temps": temps, "fan": fan,
            "sclk_actual": sclk_actual, "sclk_level": sclk_level,
            "mclk_actual": mclk_actual, "mclk_level": mclk_level,
            "sclk": sclk_actual if sclk_actual is not None else sclk_level,
            "mclk": mclk_actual if mclk_actual is not None else mclk_level,
            "gpu_busy": int(float(_read(bp)))}

def _busy(s):
    v = s.get("util")
    return s.get("gpu_busy", s.get("gpu_busy_percent")) if v is None else v

def _clk(s, name):
    a = s.get(f"{name}_actual")
    return a if a is not None else s.get(name)

def window_stats(t0, t1, backend):
    with LOCK:
        win = [s for s in SAMPLES if t0 <= s.get("ts", 0) <= t1]
        if not win:
            win = [s for s in SAMPLES if (t0 - 1) <= s.get("ts", 0) <= (t1 + 1)]
    if not win:
        die("no telemetry samples in run window", 1)
    active = [s for s in win if (u := _busy(s)) is not None and u >= 10]
    def psum(rows):
        out = []
        for ts in dict.fromkeys(s["ts"] for s in rows):
            ps = [x["power"] for x in rows if x["ts"] == ts and x.get("power") is not None]
            if ps:
                out.append(sum(ps))
        return out
    # Aggregate power is a property of the BOX at an instant, not of one card. Filtering
    # per card and then summing drops a card's real draw whenever that card dips below the
    # activity threshold, so a two-card box reports a one-card number. Keep a TIMESTAMP when
    # any device is active, then sum every device present at it. Measured on the retained
    # 2026-09-06 box-a run: the MoE-prefill median read 134 W per-card-filtered and 187 W
    # summed over the pair, with both readings identical wherever both cards stayed active.
    act_ts = {s["ts"] for s in active}
    paired = [s for s in win if s["ts"] in act_ts]
    pall, pact = psum(win), psum(paired)
    sclk = [v for s in active if (v := _clk(s, "sclk")) is not None]
    mclk = [v for s in active if (v := _clk(s, "mclk")) is not None]
    fan = [s["fan"] for s in win if s.get("fan") is not None]
    tmax = {}
    for s in win:
        items = list((s.get("temps") or {}).items())
        if s.get("temp") is not None:
            items.append((f"gpu{s.get('index', 0)}", s["temp"]))
        for k, v in items:
            if v is not None:
                tmax[k] = v if k not in tmax else max(tmax[k], v)
    out = {"power_median": statistics.median(pact) if active and pact else None,
           "power_max": max(pall) if pall else None, "temp_max": tmax,
           "sclk_median": statistics.median(sclk) if active and sclk else None,
           "sclk_min": min(sclk) if active and sclk else None,
           "mclk_median": statistics.median(mclk) if active and mclk else None,
           "fan_max": max(fan) if fan else None,
           "active_samples": len(active), "window_samples": len(win)}
    if not active:
        out["telemetry_note"] = "no active samples"
    if backend == "nvidia":
        out["clocks_event_reasons"] = or_reasons(s.get("clocks_event_reasons") for s in win)
        pmg, umg = {}, {}
        for s in win:
            i = s.get("index")
            if i is None:
                continue
            if s.get("power") is not None:
                pmg[i] = s["power"] if i not in pmg else max(pmg[i], s["power"])
            if s.get("util") is not None:
                umg[i] = s["util"] if i not in umg else max(umg[i], s["util"])
        out["power_max_per_gpu"] = pmg
        out["util_max_per_gpu"] = umg
    return out

def _post_gen(url, payload):
    req = urllib.request.Request(url, data=payload, method="POST",
                                 headers={"Content-Type": "application/json"})
    holder, start = {"resp": None, "body": None, "err": None}, time.time()
    def do():
        try:
            r = urllib.request.urlopen(req, timeout=1800)
            holder["resp"] = r
            holder["body"] = r.read()
        except urllib.error.HTTPError as e:
            holder["err"] = e
            try:
                holder["body"] = e.read()
            except Exception:
                pass
        except Exception as e:
            holder["err"] = e
    th = threading.Thread(target=do, daemon=True)
    th.start()
    aborted = False
    while th.is_alive():
        if ABORT.is_set():
            aborted = True
            try:
                holder["resp"].close()
            except Exception:
                pass
            break
        th.join(0.2)
    th.join(2)
    return start, time.time(), holder, aborted

def _parse_gen(body):
    if body is None:
        return None, "empty HTTP response"
    try:
        data = json.loads(body)
    except json.JSONDecodeError as e:
        return None, f"invalid JSON from ollama: {e}"
    if isinstance(data, dict) and "error" in data and "eval_duration" not in data:
        return None, f"ollama error: {data['error']}"
    if not isinstance(data, dict):
        return None, "invalid JSON from ollama: not an object"
    return data, None

def _think_rejected(err, body, perr):
    if isinstance(err, urllib.error.HTTPError) and 400 <= getattr(err, "code", 0) < 500:
        return True
    raw = body.decode("utf-8", "replace") if isinstance(body, (bytes, bytearray)) else (body or "")
    if "think" in raw.lower():
        return True
    return bool(perr and perr.startswith("ollama error:"))

def run_once(host, model, prompt, gen):
    url = host.rstrip("/") + "/api/generate"
    base = {"model": model, "prompt": prompt, "stream": False,
            "options": {"temperature": 0, "seed": 0, "num_predict": gen},
            "keep_alive": "15m"}
    start, end, holder, aborted = _post_gen(url, json.dumps({**base, "think": False}).encode())
    data, perr = _parse_gen(holder["body"])
    think_flag = None
    if data is None and not aborted and _think_rejected(holder["err"], holder["body"], perr):
        start, end, holder, aborted = _post_gen(url, json.dumps(base).encode())
        think_flag = "unsupported"
        data, perr = _parse_gen(holder["body"])
    rec = {"t_start": start, "t_end": end, "wall_s": end - start,
           "t_start_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(start)),
           "t_end_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(end))}
    if think_flag:
        rec["think_flag"] = think_flag
    if aborted and data is None:
        rec["aborted"] = "temp"
        return rec
    if data is None:
        extra = ""
        err = holder["err"]
        if perr and perr != "empty HTTP response":
            die(perr, 1)
        if isinstance(err, urllib.error.HTTPError) and holder["body"]:
            extra = f" {holder['body'][:400]!r}"
        die(f"{err or perr or 'empty HTTP response'}{extra}", 1)
    thinking, text = data.get("thinking") or "", data.get("response") or ""
    pec, ped = data.get("prompt_eval_count"), data.get("prompt_eval_duration")
    ec, ed = data.get("eval_count"), data.get("eval_duration")
    rec.update({"prompt_eval_count": pec, "prompt_eval_duration": ped,
                "eval_count": ec, "eval_duration": ed,
                "prompt_toks_per_s": (pec / (ped / 1e9)) if pec is not None and ped else None,
                "gen_toks_per_s": (ec / (ed / 1e9)) if ec is not None and ed else None,
                "response_sha": hashlib.sha256((thinking + text).encode()).hexdigest(),
                "response_chars": len(text), "thinking_chars": len(thinking), "aborted": None})
    return rec

def _fmt_mm(vals, nd=2):
    if not vals:
        return "n/a"
    return f"{statistics.median(vals):.{nd}f} ({min(vals):.{nd}f}–{max(vals):.{nd}f})"

def _per_card_max(recs):
    pg = {}
    for r in recs:
        for k, v in (r.get("power_max_per_gpu") or {}).items():
            if v is None:
                continue
            ks = str(k)
            pg[ks] = v if ks not in pg else max(pg[ks], v)
    if not pg:
        return ""
    order = sorted(pg, key=lambda x: int(x) if str(x).lstrip("-").isdigit() else str(x))
    return " (max per card: " + "/".join(f"{pg[k]:.0f}" for k in order) + ")"

def summarize(runs, args):
    recs = [r for r in runs if not r.get("warmup")]
    def col(k):
        return [r[k] for r in recs if r.get(k) is not None]
    pmed, pmaxv, sm, sn = col("power_median"), col("power_max"), col("sclk_median"), col("sclk_min")
    w = f"{statistics.median(pmed):.1f}/{max(pmaxv):.1f}" if pmed and pmaxv else "n/a"
    w += _per_card_max(recs)
    # The summary's temperature is the maximum over whatever keys the backend reports, and
    # those keys mean different things: NVIDIA reports one per CARD (gpu0, gpu1), where the
    # maximum is the hotter card and is the number you want; AMD reports one per SENSOR
    # (edge, junction, mem), where the maximum can be the memory sensor while a reader
    # assumes junction. That happened: a 15-minute run summarised as 88 C had junction at 87
    # and memory at 88, and 88 was copied into a column labelled junction. So print WHICH
    # key produced the number instead of a bare figure, and keep the per-key values in the
    # JSONL, which is what any published figure should be derived from.
    tmax, anyt, tkey = 0.0, False, None
    for r in recs:
        td = r.get("temp_max") or {}
        items = td.items() if isinstance(td, dict) else [("temp", td)]
        for k, v in items:
            if v is not None and (not anyt or v > tmax):
                tmax, anyt, tkey = v, True, k
    mm, fm = col("mclk_median"), col("fan_max")
    hashes = [r.get("response_sha") for r in recs if r.get("response_sha")]
    nonce = bool(getattr(args, "nonce", False) or any(r.get("nonce") for r in recs))
    if nonce:
        hs = "n/a"
    else:
        hs = f"{sum(1 for h in hashes if h == hashes[0])}/{len(hashes)}" if hashes else "0/0"
    ab = any(r.get("aborted") == "temp" for r in runs) or ABORT.is_set()
    tshow = f"{tmax:.0f} ({tkey})" if anyt else "n/a"
    sclk = f"{statistics.median(sm):.0f}/{min(sn):.0f}" if sm and sn else "n/a"
    mclk = f"{statistics.median(mm):.0f}" if mm else "n/a"
    fan = f"{max(fm):.0f}" if fm else "n/a"
    line = (f"{args.label} · {args.model} · n={len(recs)} · prompt {_fmt_mm(col('prompt_toks_per_s'))} tok/s · "
            f"gen {_fmt_mm(col('gen_toks_per_s'))} tok/s · {w} W · {tshow} C · sclk {sclk} · "
            f"mclk {mclk} · fan {fan} · hash {hs} · aborted={'yes' if ab else 'no'}")
    if args.sustain_minutes and recs:
        t0, t1 = recs[0]["t_start"], recs[-1]["t_end"]
        first = [r for r in recs if r["t_start"] < t0 + 60]
        last = [r for r in recs if r["t_start"] >= t1 - 60]
        def medk(rs, k):
            xs = [r[k] for r in rs if r.get(k) is not None]
            return statistics.median(xs) if xs else None
        def f2(x, d=2):
            return "n/a" if x is None else f"{x:.{d}f}"
        line += (f" · first-minute gen {f2(medk(first, 'gen_toks_per_s'))} tok/s "
                 f"sclk {f2(medk(first, 'sclk_median'), 0)} · last-minute gen "
                 f"{f2(medk(last, 'gen_toks_per_s'))} tok/s sclk {f2(medk(last, 'sclk_median'), 0)}")
    return line

def parse_args(argv=None):
    p = argparse.ArgumentParser(prog="gpu_bench.py")
    p.add_argument("--backend", required=True, choices=("nvidia", "amd"))
    p.add_argument("--model", required=True); p.add_argument("--label", required=True)
    p.add_argument("--prompt-file", required=True)
    p.add_argument("--gen", type=int, default=256); p.add_argument("--repeats", type=int, default=5)
    p.add_argument("--warmup", type=int, default=1)
    p.add_argument("--host", required=True, help="ollama endpoint URL")
    p.add_argument("--gpu-index", default=None); p.add_argument("--sysfs-card", default=None)
    p.add_argument("--abort-temp", type=float, default=None)
    p.add_argument("--sustain-minutes", type=float, default=0)
    p.add_argument("--out", default="results.jsonl"); p.add_argument("--dry", action="store_true")
    p.add_argument("--nonce", action="store_true")
    args = p.parse_args(argv)
    if args.abort_temp is None:
        args.abort_temp = 90.0 if args.backend == "nvidia" else 100.0
    if args.gpu_index:
        try:
            args.gpu_index = [int(x) for x in args.gpu_index.split(",") if x.strip() != ""]
        except ValueError:
            die("invalid --gpu-index", 2)
    else:
        args.gpu_index = None
    return args

def main(argv=None):
    args = parse_args(argv)
    if args.dry:
        pf = args.prompt_file
        sha = hashlib.sha256(open(pf, "rb").read()).hexdigest() if os.path.isfile(pf) else "MISSING"
        print(f"plan backend={args.backend} model={args.model} label={args.label} prompt={pf} sha={sha} "
              f"gen={args.gen} repeats={args.repeats} warmup={args.warmup} host={args.host} "
              f"gpu-index={args.gpu_index} sysfs-card={args.sysfs_card} abort-temp={args.abort_temp} "
              f"sustain-minutes={args.sustain_minutes} nonce={args.nonce} out={args.out} "
              f"POST {args.host.rstrip('/')}/api/generate think=false num_predict={args.gen} keep_alive=15m")
        return 0
    if not os.path.isfile(args.prompt_file):
        die(f"prompt file not found: {args.prompt_file}", 2)
    prompt = open(args.prompt_file, encoding="utf-8").read()
    prompt_sha = hashlib.sha256(prompt.encode()).hexdigest()
    device = card = None
    try:
        if args.backend == "amd":
            cards = ([args.sysfs_card] if args.sysfs_card else
                     [p.split("/")[4] for p in sorted(glob.glob("/sys/class/drm/card*/device/pp_dpm_sclk"))])
            if not cards or not cards[0]:
                die("no amdgpu card with pp_dpm_sclk", 2)
            card, device = cards[0], f"/sys/class/drm/{cards[0]}/device"
            if not os.path.isfile(os.path.join(device, "pp_dpm_sclk")):
                die(f"missing telemetry file: {device}/pp_dpm_sclk", 2)
            sample_amd(device, card)
        else:
            sample_nvidia(args.gpu_index)
    except SystemExit:
        raise
    except Exception as e:
        die(str(e), 2)
    out_f = open(args.out, "a", encoding="utf-8")
    tel_f = open(args.out + ".telemetry.jsonl", "a", encoding="utf-8")
    def telem():
        hot = 0
        try:
            while not STOP.is_set():
                t0 = time.time()
                rows = (sample_nvidia(args.gpu_index) if args.backend == "nvidia"
                        else [sample_amd(device, card)])
                now, mt = time.time(), 0.0
                with LOCK:
                    for r in rows:
                        r["ts"] = now
                        SAMPLES.append(r)
                        tel_f.write(json.dumps(r) + "\n")
                        mt = max(mt, row_max_temp(r))
                    tel_f.flush()
                hot = hot + 1 if mt >= args.abort_temp else 0
                if hot > 15:
                    ABORT.set()
                STOP.wait(max(0.0, 1.0 - (time.time() - t0)))
        except Exception as e:
            TEL_ERR.append(str(e))
            print(f"telemetry error: {e}", file=sys.stderr)
            STOP.set()
    th = threading.Thread(target=telem, daemon=True)
    th.start()
    time.sleep(1.05)
    if TEL_ERR:
        STOP.set(); tel_f.close(); out_f.close(); die(TEL_ERR[0], 1)
    runs, first_hash, run_i, warm_ptps = [], None, 0, None
    try:
        def do_run(warmup):
            nonlocal first_hash, run_i, warm_ptps
            p = prompt
            if args.nonce:
                p = f"[bench-nonce {args.label} {run_i} {int(time.time() * 1000)}]\n{prompt}"
            rec = run_once(args.host, args.model, p, args.gen)
            rec.update({"warmup": warmup, "label": args.label, "model": args.model,
                        "backend": args.backend, "prompt_sha": prompt_sha, "gen": args.gen})
            if args.nonce:
                rec["nonce"] = True
            ptps = rec.get("prompt_toks_per_s")
            if warmup and warm_ptps is None and ptps is not None:
                warm_ptps = ptps
            rec["cache_suspect"] = bool(warm_ptps is not None and ptps is not None
                                        and ptps > 5 * warm_ptps)
            run_i += 1
            if args.backend == "amd":
                od = os.path.join(device, "pp_od_clk_voltage")
                rec["pp_od_clk_voltage"] = (open(od, encoding="utf-8", errors="replace").read()
                                             if os.path.exists(od) else "absent (overdrive disabled)")
                rec["power_cap_w"] = _amd_power_cap(device)
            rec.update(window_stats(rec["t_start"], rec["t_end"], args.backend))
            if not warmup and not args.nonce:
                sha = rec.get("response_sha")
                if first_hash is None:
                    first_hash = sha
                    rec["hash_mismatch"] = False
                else:
                    rec["hash_mismatch"] = bool(sha) and sha != first_hash
            out_f.write(json.dumps(rec) + "\n")
            out_f.flush()
            runs.append(rec)
        n_warm, i = max(0, args.warmup), 0
        while i < n_warm and not (ABORT.is_set() or TEL_ERR):
            do_run(True); i += 1
        if args.sustain_minutes > 0:
            tlim = time.time() + args.sustain_minutes * 60.0
            while time.time() < tlim and not (ABORT.is_set() or TEL_ERR):
                do_run(False)
        else:
            i, n = 0, max(0, args.repeats)
            while i < n and not (ABORT.is_set() or TEL_ERR):
                do_run(False); i += 1
    finally:
        STOP.set(); th.join(3); tel_f.close(); out_f.close()
    if TEL_ERR and not ABORT.is_set():
        die(TEL_ERR[0], 1)
    text = summarize(runs, args)
    print(text)
    with open(args.out + ".summary.md", "a", encoding="utf-8") as sf:
        sf.write(text + "\n")
    if ABORT.is_set() or any(r.get("aborted") == "temp" for r in runs):
        raise SystemExit(3)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
