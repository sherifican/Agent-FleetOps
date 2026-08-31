"""Fleet-model runtime state — which models are LOADED (running, in VRAM) vs COLD on disk (idle).

Pure + headless: `build_model_states` does no I/O (unit-test target); the readers hit ollama and
safe-default to []. Loaded models come from /api/ps (with an idle-unload countdown from expires_at);
the rest of the pulled models (/api/tags) are the cold/idle set.
"""
from fleet_tui.models import ModelState, InstalledModel
import datetime
import json
import subprocess
import time
import urllib.request

OLLAMA = "http://localhost:11434"
BUSY_UTIL = 20          # GPU % above which a loaded model is treated as "in-flight"
_util_cache = {}        # tiny time-cache so nvidia-smi is not spawned every refresh
_sidecar_vram_cache = {}  # cache for sidecar VRAM readings


# Known llama-server sidecars — (port, label). Bonsai Ternary 27B is on-demand (`bonsai-serve`), so it's
# only reachable on :8100 while running → it appears here as a LOADED model (with VRAM + in-flight) whenever
# it's up, and simply drops off the list when stopped (read_sidecars safe-skips an unreachable port).
SIDECARS = [(8336, "gemma4-vision"), (8090, "glm-jinja"), (8100, "bonsai-ternary")]


def read_gpu_util() -> int:
    """Max GPU utilization % across cards (for 'in-flight' detection). Cached ~2s. Safe: 0 on error."""
    now = time.time()
    hit = _util_cache.get("u")
    if hit is not None and (now - hit[0]) < 2.0:
        return hit[1]
    val = 0
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"],
                           capture_output=True, text=True, timeout=5)
        vals = [int(x.strip()) for x in r.stdout.strip().splitlines() if x.strip().isdigit()]
        val = max(vals) if vals else 0
    except Exception:
        val = 0
    _util_cache["u"] = (now, val)
    return val


_sidecar_busy_cache = {}


def _sidecar_busy(port) -> bool:
    """True iff the sidecar on `port` has an ACTIVE inbound request — i.e. an ESTABLISHED TCP
    connection whose LOCAL (source) port is the sidecar port (a client mid-request holds the
    connection open during generation). Cached ~2s. Safe: False on any error.

    This REPLACES the old heuristic that marked a sidecar in-flight from GLOBAL gpu-busyness,
    which mislabeled an idle resident sidecar (e.g. gemma4-vision :8336) as 'in-flight' whenever
    ANYTHING else used the GPU — the sidecar's own port, not the GPU, is the honest signal."""
    now = time.time()
    hit = _sidecar_busy_cache.get(port)
    if hit is not None and (now - hit[0]) < 2.0:
        return hit[1]
    busy = False
    try:
        r = subprocess.run(["ss", "-tnH", "state", "established", f"( sport = :{port} )"],
                           capture_output=True, text=True, timeout=3)
        busy = bool(r.stdout.strip())
    except Exception:
        busy = False
    _sidecar_busy_cache[port] = (now, busy)
    return busy


def read_ps() -> list:
    """Loaded models (in VRAM) from ollama /api/ps. Safe: [] on any error."""
    try:
        r = urllib.request.urlopen(f"{OLLAMA}/api/ps", timeout=5)
        return json.loads(r.read().decode()).get("models", [])
    except Exception:
        return []


def read_tags() -> list:
    """All pulled model NAMES (on disk) from ollama /api/tags. Safe: [] on any error."""
    try:
        r = urllib.request.urlopen(f"{OLLAMA}/api/tags", timeout=5)
        return [m.get("name", "?") for m in json.loads(r.read().decode()).get("models", [])]
    except Exception:
        return []


def read_tags_full() -> list:
    """Raw pulled-model records (name + size + details) from ollama /api/tags. Safe: [] on any error."""
    try:
        r = urllib.request.urlopen(f"{OLLAMA}/api/tags", timeout=5)
        return json.loads(r.read().decode()).get("models", [])
    except Exception:
        return []


def _sidecar_vram_mb(port) -> int:
    """Get the VRAM usage (MB) of the llama-server process on `port`, or 0 on any error.
    Caches results for ~15s to avoid hammering nvidia-smi."""
    now = time.time()
    hit = _sidecar_vram_cache.get(port)
    if hit is not None and (now - hit[0]) < 15.0:
        return hit[1]
    
    vram_mb = 0
    try:
        # Find the llama-server PID(s) serving this port (pgrep may return several).
        pgrep_cmd = ["pgrep", "-f", f"llama-server.*--port {port}"]
        result = subprocess.run(pgrep_cmd, capture_output=True, text=True, timeout=5)

        pids = {p.strip() for p in result.stdout.split("\n") if p.strip()} if result.returncode == 0 else set()
        if pids:
            # SUM every nvidia-smi compute-app row for the PID(s). A model spanning BOTH cards lists the
            # same pid can appear once per accelerator; sum every row to avoid undercounting.
            nvidia_cmd = ["nvidia-smi", "--query-compute-apps=pid,used_memory", "--format=csv,noheader,nounits"]
            result = subprocess.run(nvidia_cmd, capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                for line in result.stdout.strip().split("\n"):
                    if "," not in line:
                        continue
                    pid_str, mem_str = line.split(",", 1)
                    if pid_str.strip() in pids:
                        vram_mb += int(mem_str.strip())
    except Exception:
        pass  # Return 0 on any error
    
    _sidecar_vram_cache[port] = (now, vram_mb)
    return vram_mb


def read_sidecars() -> list:
    """Query the known llama-server sidecar endpoints (OpenAI-style /v1/models) and return a record per LOADED
    (responding) sidecar. Safe: [] on ANY error (an asleep/unbound sidecar just isn't listed)."""
    out = []
    for port, label in SIDECARS:
        try:
            r = urllib.request.urlopen(f"http://localhost:{port}/v1/models", timeout=2)
            data = json.loads(r.read().decode()).get("data", [])
            for m in data:
                mid = m.get("id") or label
                # Get VRAM usage for this sidecar
                gb = round(_sidecar_vram_mb(port) / 1024.0, 1)
                # in-flight = THIS sidecar's port has a live request (not global GPU busyness)
                out.append({"name": mid, "port": port, "gb": gb, "busy": _sidecar_busy(port)})
        except Exception:
            continue          # sidecar down/asleep → skip, never raise
    return out


def build_installed(tag_models: list) -> list:
    """PURE composer: raw /api/tags records → InstalledModel inventory, sorted by size (largest first).
    No I/O — the unit-test target. Missing fields degrade to safe defaults, never raise."""
    out = []
    for m in tag_models or []:
        det = m.get("details") or {}
        modified = str(m.get("modified_at", "") or "")[:10]   # date portion of the RFC3339 stamp
        out.append(InstalledModel(
            name=m.get("name", "?"),
            size_gb=round(m.get("size", 0) / 1e9, 1),
            family=str(det.get("family", "") or ""),
            quant=str(det.get("quantization_level", "") or ""),
            param_size=str(det.get("parameter_size", "") or ""),
            modified=modified,
        ))
    out.sort(key=lambda x: x.size_gb, reverse=True)
    return out


def installed_models() -> list:
    """Convenience: every model on disk (name + size + family/quant/params/modified), largest first."""
    return build_installed(read_tags_full())


def _idle_in(expires_at, now_epoch: float) -> str:
    """Human 'time until ollama idle-unloads it' from an RFC3339 expires_at; '' if past/unparseable."""
    if not expires_at:
        return ""
    try:
        exp = datetime.datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
        secs = exp.timestamp() - now_epoch
        if secs <= 0:
            return ""
        if secs < 90:
            return f"{int(secs)}s"
        if secs < 5400:
            return f"{int(secs / 60)}m"
        return f"{int(secs / 3600)}h"
    except Exception:
        return ""


def build_model_states(ps_models: list, tag_names: list, now_epoch: float, gpu_busy: bool = False, sidecars=None) -> list:
    """PURE composer: loaded models (from /api/ps) first, then the cold on-disk models.
    gpu_busy → loaded models are marked in-flight (busy) since the GPU is actively computing.
    
    New: sidecars parameter accepts a list of sidecar records from read_sidecars() and appends them
    as LOADED ModelStates after ollama-loaded models but before cold on-disk models."""
    states = []
    loaded_names = set()
    for m in ps_models:
        name = m.get("name", "?")
        loaded_names.add(name)
        states.append(ModelState(
            name=name,
            loaded=True,
            gb=round(m.get("size", 0) / 1e9, 1),
            idle_in=_idle_in(m.get("expires_at", ""), now_epoch),
            busy=gpu_busy,
        ))
    
    # NEW: sidecars are loaded local models too (llama-server, not ollama)
    for sc in (sidecars or []):
        nm = f"{sc.get('name','?')} (:{sc.get('port','?')})"   # port in the name → distinguishable from ollama
        gb = sc.get("gb", 0.0)  # Use the actual VRAM value from read_sidecars()
        # in-flight from the sidecar's OWN active request, NOT global gpu_busy (fixes the
        # gemma4-vision false-'in-flight' the owner spotted when the eval was using the GPUs).
        states.append(ModelState(name=nm, loaded=True, gb=gb, idle_in="", busy=bool(sc.get("busy", False))))
    
    for name in tag_names:
        if name not in loaded_names:
            states.append(ModelState(name=name, loaded=False))
    return states


def list_models() -> list:
    """Convenience: current fleet-model states (loaded + cold), with in-flight detection."""
    return build_model_states(read_ps(), read_tags(), time.time(),
                              gpu_busy=read_gpu_util() >= BUSY_UTIL, sidecars=read_sidecars())
