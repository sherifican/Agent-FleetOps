# SPEC — modelstate.py: sidecars report REAL VRAM (not 0.0) + colorize by family

Edit fleet_tui/sources/modelstate.py (pure/headless, never raises). Gate = tests/test_modelstate_sidecar_vram.py + the existing tests/test_modelstate_sidecars.py + full suite.

## 1. `_sidecar_vram_mb(port) -> int`  (new reader; MB the llama-server on `port` uses on GPU, 0 on any error)
- Find the llama-server PID whose cmdline contains `--port <port>`: `pgrep -f "llama-server.*--port <port>"` (or scan /proc). Then read that pid's GPU memory from `nvidia-smi --query-compute-apps=pid,used_memory --format=csv,noheader,nounits` and return the MB for that pid. Cache ~15s (module cache, like _ext_cache). Return 0 on ANY error (never raise). Use `subprocess`.

## 2. `read_sidecars()` — attach `gb`
For each responding sidecar, call `_sidecar_vram_mb(port)` and add `"gb": round(mb/1024, 1)` to the record (0.0 if mb==0).

## 3. `build_model_states` — use the sidecar's gb
The sidecar ModelState should use `sc.get("gb", 0.0)` instead of the hardcoded `gb=0.0`.

Keep everything else (name `<id> (:<port>)`, loaded=True, busy=gpu_busy). Do NOT import textual. Do NOT change models.py.
