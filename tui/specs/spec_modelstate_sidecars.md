# SPEC — modelstate.py: surface llama-server SIDECARS as loaded local models

Edit `~/fleet_tui/fleet_tui/sources/modelstate.py` IN PLACE. Keep the module PURE/headless
(readers do I/O + safe-default to `[]`; the composer does NO I/O). NEVER import textual. Gate =
`tests/test_modelstate_sidecars.py` + the existing `tests/` (nothing else may regress).

## Bug (owner-reported 2026-07-11)
The models panel reads ONLY ollama (`/api/ps`). But local `llama-server` SIDECARS — the gemma4 vision snap
(`:8336`, model `gemma4-e4b-q4-k-m`) and the GLM `--jinja` sidecar (`:8090`) — are local models that use the
GPU and are NOT ollama models. So the owner saw sustained GPU utilization with "no local models loaded".
Fix: also detect the sidecars and show them as loaded models.

## 1. New reader — `read_sidecars() -> list`
Query the known llama-server sidecar endpoints (OpenAI-style `/v1/models`) and return a record per LOADED
(responding) sidecar. Safe: `[]` on ANY error (an asleep/unbound sidecar just isn't listed).

    SIDECARS = [(8336, "gemma4-vision"), (8090, "glm-jinja")]   # (port, label) — known llama-server sidecars

    def read_sidecars() -> list:
        out = []
        for port, label in SIDECARS:
            try:
                r = urllib.request.urlopen(f"http://localhost:{port}/v1/models", timeout=2)
                data = json.loads(r.read().decode()).get("data", [])
                for m in data:
                    mid = m.get("id") or label
                    out.append({"name": mid, "port": port})
            except Exception:
                continue          # sidecar down/asleep → skip, never raise
        return out

(Use a SHORT timeout — 2s — so a dead endpoint doesn't stall the refresh. Wrap each endpoint separately so
one down sidecar doesn't drop the others.)

## 2. Extend the PURE composer `build_model_states(...)`
Add a `sidecars` parameter (default `[]` — BACKWARD COMPATIBLE; existing callers/tests pass nothing and
must behave EXACTLY as before). Append the sidecars as LOADED `ModelState`s AFTER the ollama-loaded models
and BEFORE the cold on-disk models:

    def build_model_states(ps_models, tag_names, now_epoch, gpu_busy=False, sidecars=None):
        ...existing ollama-loaded loop (unchanged)...
        # NEW: sidecars are loaded local models too (llama-server, not ollama)
        for sc in (sidecars or []):
            nm = f"{sc.get('name','?')} (:{sc.get('port','?')})"   # port in the name → distinguishable from ollama
            states.append(ModelState(name=nm, loaded=True, gb=0.0, idle_in="", busy=gpu_busy))
        ...existing cold-model loop (unchanged)...

- `ModelState`'s other fields default (gb=0.0 acceptable — sidecar size isn't exposed by /v1/models; the
  point is to SHOW it's loaded + in-flight, resolving the "GPU busy, nothing shown" confusion).
- The sidecar name MUST contain both the model id and the `:port` so the owner sees which endpoint it is.

## 3. Wire it into `list_models()`
Pass the sidecar reader's output through:

    def list_models():
        return build_model_states(read_ps(), read_tags(), time.time(),
                                  gpu_busy=read_gpu_util() >= BUSY_UTIL, sidecars=read_sidecars())

## Constraints
- Pure composer stays I/O-free (sidecars come in as an arg). Readers safe-default to `[]`, never raise.
- Do NOT import textual. Do NOT change `models.py` (reuse `ModelState` as-is; its non-name fields default).
- Gate = `tests/test_modelstate_sidecars.py` — match it EXACTLY — plus the full existing suite stays green.
