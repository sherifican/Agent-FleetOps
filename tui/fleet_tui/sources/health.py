"""Pure readers for health status in the Fleet fleet."""

from fleet_tui.models import HealthSnapshot, LoadedModel
import glob
import json
import os
import re
import shutil
import subprocess
import time
import urllib.request

DEFAULT_SERVICES = ["hermes-gateway", "openrgb-server"]
BIG_MODEL_BYTES = 15_000_000_000
RELIABILITY_PATH = os.path.expanduser("~/pc-passback/FLEET_HEALTH_latest.txt")
DISK_PATH = "~"   # the partition holding models (~/.ollama) + logs; what fills up

# The UI refreshes ~every 3s, but `fleet-doctor --json` spawns nvidia-smi + systemctl + subprocess probes;
# hammering that every 3s is wasteful and can aggravate a busy GPU. Cache the heavy probes. (2026-07-02)
FLEET_DOCTOR_TTL = 30.0   # seconds
OLLAMA_PS_TTL = 5.0
GPU_TTL = 2.0             # nvidia-smi VRAM+temp+util — live-ish for the 1s refresh, still nvtop-level
SENSORS_TTL = 2.0         # hwmon temp FILE reads (cheap) — refresh ~every 2s
SERVICES_TTL = 4.0        # systemctl is-active (subprocess) — don't spawn it every 1s tick
_cache = {}               # key -> (timestamp, value); cleared by tests via _cache.clear()


def _cached(key, ttl, fn):
    """Return fn()'s value, memoized for `ttl` seconds. fn must be safe (never raise)."""
    now = time.time()
    hit = _cache.get(key)
    if hit is not None and (now - hit[0]) < ttl:
        return hit[1]
    val = fn()
    _cache[key] = (now, val)
    return val


def read_meminfo() -> dict:
    """Parse /proc/meminfo for RAM and swap usage. Cached ~4s.
    
    Returns exactly these keys, all floats except the percents which are ints:
        ram_used_gb     GiB currently in use
        ram_total_gb    GiB installed
        ram_pct         int 0..100, percent of RAM in use
        swap_used_gb    GiB of swap in use
        swap_total_gb   GiB of swap configured
        swap_pct        int 0..100, percent of swap in use
    
    Rules:
      - Parse the MemTotal:, MemAvailable:, SwapTotal: and SwapFree: lines. Values are in kB.
      - Used RAM is MemTotal - MemAvailable, NOT MemTotal - MemFree.
      - Convert kB to GiB by dividing by 1048576.
      - swap_pct is 0 when SwapTotal is 0 (a box with no swap is not at 0% pressure by accident — it just
        has none). Guard the divide.
      - Missing keys, unreadable file, or garbage values must yield the all-zero default, never an exception.
    """
    def _do():
        try:
            meminfo = {}
            with open("/proc/meminfo", "r") as f:
                for line in f:
                    if line.startswith(("MemTotal:", "MemAvailable:", "SwapTotal:", "SwapFree:")):
                        key, value = line.split(":", 1)
                        meminfo[key] = int(value.strip().split()[0])  # kB value
            
            # Calculate RAM values
            ram_total_kb = meminfo.get("MemTotal", 0)
            ram_available_kb = meminfo.get("MemAvailable", 0)
            ram_used_kb = ram_total_kb - ram_available_kb if ram_total_kb > 0 else 0
            
            ram_total_gb = ram_total_kb / 1048576.0
            ram_used_gb = ram_used_kb / 1048576.0
            ram_pct = int((ram_used_kb / ram_total_kb) * 100) if ram_total_kb > 0 else 0
            
            # Calculate swap values
            swap_total_kb = meminfo.get("SwapTotal", 0)
            swap_free_kb = meminfo.get("SwapFree", 0)
            swap_used_kb = swap_total_kb - swap_free_kb if swap_total_kb > 0 else 0
            
            swap_total_gb = swap_total_kb / 1048576.0
            swap_used_gb = swap_used_kb / 1048576.0
            swap_pct = int((swap_used_kb / swap_total_kb) * 100) if swap_total_kb > 0 else 0
            
            return {
                "ram_used_gb": ram_used_gb,
                "ram_total_gb": ram_total_gb,
                "ram_pct": ram_pct,
                "swap_used_gb": swap_used_gb,
                "swap_total_gb": swap_total_gb,
                "swap_pct": swap_pct
            }
        except Exception:
            # Return safe default on any error
            return {
                "ram_used_gb": 0.0,
                "ram_total_gb": 0.0,
                "ram_pct": 0,
                "swap_used_gb": 0.0,
                "swap_total_gb": 0.0,
                "swap_pct": 0
            }
    
    return _cached("meminfo", 4.0, _do)


def read_fleet_doctor() -> dict:
    """Read fleet-doctor output as JSON, return empty dict on any error. Cached ~30s (heavy probe)."""
    def _do():
        try:
            result = subprocess.run(
                ["fleet-doctor", "--json"],
                capture_output=True,
                text=True,
                timeout=30
            )
            return json.loads(result.stdout)
        except Exception:
            return {}
    return _cached("fleet_doctor", FLEET_DOCTOR_TTL, _do)


def read_ollama_ps() -> list:
    """Read ollama ps output as JSON, return empty list on any error. Cached ~5s."""
    def _do():
        try:
            response = urllib.request.urlopen("http://localhost:11434/api/ps", timeout=5)
            data = json.loads(response.read().decode())
            return data.get("models", [])
        except Exception:
            return []
    return _cached("ollama_ps", OLLAMA_PS_TTL, _do)


def read_services(names=None) -> dict:
    """Read systemctl status for given service names, return dict of name->active bool. Cached ~4s so the
    1s refresh doesn't spawn systemctl every tick."""
    if names is None:
        names = DEFAULT_SERVICES

    def _do():
        result = {}
        for name in names:
            try:
                process = subprocess.run(
                    ["systemctl", "--user", "is-active", name],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                result[name] = process.stdout.strip() == "active"
            except Exception:
                result[name] = False
        return result
    return _cached("services:" + ",".join(names), SERVICES_TTL, _do)


def read_gpu() -> list:
    """Per-card VRAM + temp via nvidia-smi. Cached ~30s (a probe, not every-3s). Safe: [] on any error."""
    def _do():
        try:
            r = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.used,memory.total,temperature.gpu,utilization.gpu",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=8)
            out = []
            for line in r.stdout.strip().splitlines():
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 3:
                    card = {"used": int(parts[0]), "total": int(parts[1]), "temp": int(parts[2])}
                    if len(parts) >= 4:
                        try:
                            card["util"] = int(parts[3])
                        except (ValueError, IndexError):
                            pass
                    out.append(card)
            return out
        except Exception:
            return []
    return _cached("gpu", GPU_TTL, _do)


def read_sensors() -> dict:
    """CPU (k10temp Tctl) + SSD (nvme Composite) temps from /sys/class/hwmon (no lm-sensors / sudo needed).
    Cached ~15s. Safe: returns {"cpu_temp":0,"ssd_temp":0} on any error (0 = unknown)."""
    def _do():
        out = {"cpu_temp": 0, "ssd_temp": 0, "ssd_ext_temp": 0}
        # external USB-NVMe SSD temp — written by the root cron (/etc/cron.d/fleet-ssd-temp); read sudo-free
        try:
            out["ssd_ext_temp"] = int(open(os.path.expanduser("~/.cache/ext_ssd_temp")).read().strip())
        except Exception:
            out["ssd_ext_temp"] = 0
        try:
            for h in glob.glob("/sys/class/hwmon/hwmon*"):
                try:
                    name = open(os.path.join(h, "name")).read().strip()
                except Exception:
                    continue
                if name not in ("k10temp", "coretemp", "nvme"):
                    continue
                for tf in glob.glob(os.path.join(h, "temp*_input")):
                    try:
                        label = ""
                        lf = tf[:-len("_input")] + "_label"
                        if os.path.exists(lf):
                            label = open(lf).read().strip()
                        val = int(open(tf).read().strip()) // 1000
                    except Exception:
                        continue
                    if name in ("k10temp", "coretemp") and label in ("Tctl", "Package id 0") and not out["cpu_temp"]:
                        out["cpu_temp"] = val
                    elif name == "nvme" and label == "Composite" and not out["ssd_temp"]:
                        out["ssd_temp"] = val
            return out
        except Exception:
            return out
    return _cached("sensors", SENSORS_TTL, _do)


def read_stability() -> dict:
    """System uptime + last GPU Xid error (from the gpu_forensics log). Cached ~15s. Safe: defaults on error."""
    def _do():
        out = {"uptime": "", "xid": "none"}
        try:
            secs = int(float(open("/proc/uptime").read().split()[0]))
            d, rem = divmod(secs, 86400)
            h = rem // 3600
            out["uptime"] = f"{d}d {h}h" if d else f"{h}h"
        except Exception:
            pass
        try:
            log = os.path.expanduser("~/gpu_forensics.log")
            if os.path.exists(log):
                hits = [ln for ln in open(log, errors="replace").read().splitlines()
                        if "xid" in ln.lower() or "gpu has fallen" in ln.lower()]
                out["xid"] = (hits[-1].split("KERR", 1)[-1].strip()[:48]) if hits else "none"
        except Exception:
            pass
        return out
    return _cached("stability", 15.0, _do)


def read_reliability_tail(path=RELIABILITY_PATH, n=6) -> str:
    """Compact reliability signal from FLEET_HEALTH_latest.txt — the EVENT counts (tool-errors, 402
    credit/rate-limit failures, loop-breaker), which are far higher-signal than the tiny per-window
    tool success-rate (which reads 100% off a single call). Empty string if unreadable."""
    try:
        text = open(path, errors="replace").read()

        def grab(label):
            m = re.search(re.escape(label) + r"\s+(\d+)", text)
            return int(m.group(1)) if m else None

        errs, c402 = grab("tool returned error"), grab("API/CREDIT failures (402)")
        lb, arg = grab("loop-breaker warns/blocks"), grab("arg-repairs / unrepairable")
        parts = []
        if c402:
            parts.append(f"{c402}×402")            # cloud credit / rate-limit (often the Kimi usage cap)
        if errs is not None:
            parts.append(f"{errs} tool-err")
        if lb:
            parts.append(f"⚠{lb} loop-break")
        if arg is not None:
            parts.append(f"{arg} arg-repair")
        return " · ".join(parts)
    except Exception:
        return ""


def read_disk(path: str = DISK_PATH) -> dict:
    """Free/total GB on the model+log partition. Cached 30s; safe {} default on error."""
    def _do():
        try:
            u = shutil.disk_usage(path)
            return {"free_gb": round(u.free / 1e9, 1), "total_gb": round(u.total / 1e9, 1)}
        except Exception:
            return {}
    return _cached(f"disk:{path}", 30.0, _do)


_cpu_prev = {"total": 0, "idle": 0}   # last /proc/stat sample, for the busy% delta


def read_cpu_util() -> int:
    """CPU busy % since the LAST call, from /proc/stat (aggregate 'cpu' line) — dependency-free, no
    subprocess (just reads a proc file), matching the GPU util % display. Returns None until it has two
    samples to diff, and on any error (0 = idle is a real value, so None = unknown). Safe: never raises."""
    try:
        with open("/proc/stat") as f:
            first = f.readline()
        if not first.startswith("cpu "):
            return None
        parts = [int(x) for x in first.split()[1:]]
        idle = parts[3] + (parts[4] if len(parts) > 4 else 0)   # idle + iowait
        total = sum(parts)
        pt, pi = _cpu_prev["total"], _cpu_prev["idle"]
        _cpu_prev["total"], _cpu_prev["idle"] = total, idle
        dt, di = total - pt, idle - pi
        if pt == 0 or dt <= 0:
            return None                       # first sample (or no elapsed jiffies) → unknown this tick
        return max(0, min(100, round(100.0 * (dt - di) / dt)))
    except Exception:
        return None


def build_snapshot(doctor: dict, ps: list, services: dict, reliability_tail: str = "", gpu: list = None,
                   sensors: dict = None, stability: dict = None, disk: dict = None,
                   cpu_util: int = None, sidecars: list = None, meminfo: dict = None) -> HealthSnapshot:
    """Compose a HealthSnapshot from parsed data. `sidecars` = llama-server sidecar records
    (modelstate.read_sidecars) — they're loaded local models too, so HEALTH's `loaded:` must
    count them. Without this, HEALTH shows 'loaded: none' while a resident sidecar (gemma4-vision
    :8336 / GLM :8090) is holding VRAM — the HEALTH↔MODELS discrepancy the owner spotted."""
    loaded = [LoadedModel(name=m.get("name", "?"), gb=round(m.get("size", 0) / 1e9, 1)) for m in ps]
    for sc in (sidecars or []):
        loaded.append(LoadedModel(name=f"{sc.get('name','?')} (:{sc.get('port','?')})", gb=sc.get("gb", 0.0)))

    big = sum(1 for m in ps if m.get("size", 0) > BIG_MODEL_BYTES)
    vram_note = f"{big} model(s) span both cards" if big else ""

    critical_caps = [
        {"cap": c.get("capability", ""), "ok": c.get("available"), "detail": c.get("detail", "")}
        for c in doctor.get("capabilities", [])
        if c.get("kind") == "CRITICAL"
    ]

    sensors = sensors or {}
    meminfo = meminfo or {}
    
    return HealthSnapshot(
        services=services,
        loaded=loaded,
        vram_note=vram_note,
        critical_caps=critical_caps,
        reliability_tail=reliability_tail,
        gpu=gpu or [],
        cpu_temp=sensors.get("cpu_temp", 0),
        cpu_util=cpu_util,
        ssd_temp=sensors.get("ssd_temp", 0),
        ssd_ext_temp=sensors.get("ssd_ext_temp", 0),
        uptime=(stability or {}).get("uptime", ""),
        xid=(stability or {}).get("xid", "none"),
        disk_free_gb=(disk or {}).get("free_gb", 0.0),
        disk_total_gb=(disk or {}).get("total_gb", 0.0),
        ram_used_gb=meminfo.get("ram_used_gb", 0.0),
        ram_total_gb=meminfo.get("ram_total_gb", 0.0),
        ram_pct=meminfo.get("ram_pct", 0),
        swap_used_gb=meminfo.get("swap_used_gb", 0.0),
        swap_total_gb=meminfo.get("swap_total_gb", 0.0),
        swap_pct=meminfo.get("swap_pct", 0)
    )


def snapshot() -> HealthSnapshot:
    """Convenience function to get a complete health snapshot."""
    from fleet_tui.sources import modelstate   # local import: sidecars for the loaded list
    return build_snapshot(
        read_fleet_doctor(),
        read_ollama_ps(),
        read_services(),
        read_reliability_tail(),
        read_gpu(),
        read_sensors(),
        read_stability(),
        read_disk(),
        read_cpu_util(),
        sidecars=modelstate.read_sidecars(),
        meminfo=read_meminfo()
    )
