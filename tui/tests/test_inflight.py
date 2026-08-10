"""Claude-authored gate: the in-flight model↔dispatch join that lets clicking an IN-FLIGHT model reveal
the dispatch title it's working on. Pure/headless — build_inflight takes already-fetched data.

Covers (v3.35): a REAL TUI-dispatch-linked local model gets a `base` (→ watch button); a persistent
llama-server SIDECAR (:8336/:8090) is labeled as the SERVICE it is (never "no linked TUI dispatch"); a
genuinely-untracked busy local model keeps an HONEST — and accurate — fallback; cloud legs pass through."""
from fleet_tui.sources import inflight
from fleet_tui.models import ModelState


def _disp(leg, cmd, running=True, brief="do the thing", base="20260711-x"):
    return {"leg": leg, "cmd": cmd, "running": running, "brief": brief, "base": base}


def test_local_busy_model_linked_to_its_dispatch():
    models = [ModelState(name="qwen3-coder:30b", loaded=True, busy=True, gb=19.0)]
    disp = [_disp("gen→audit", "fleet-model-dispatch qwen3-coder:30b")]
    r = inflight.build_inflight(models, [], disp)
    assert len(r) == 1
    assert r[0]["kind"] == "local" and r[0]["name"] == "qwen3-coder:30b"
    assert r[0]["title"] == "gen→audit"                 # the dispatch label it was given
    assert r[0]["brief"] == "do the thing"
    assert r[0]["base"] == "20260711-x"                 # non-None base → the row gets a ▶ watch button


def test_local_busy_model_with_no_matching_dispatch_is_HONEST():
    # busy from global GPU util but no TUI dispatch targets it → NEVER a fabricated title, no base
    models = [ModelState(name="gemma4:12b", loaded=True, busy=True)]
    disp = [_disp("codex-fleet", "codex-fleet")]          # a cloud dispatch, doesn't target gemma
    r = inflight.build_inflight(models, [], disp)
    assert len(r) == 1 and r[0]["base"] is None
    assert r[0]["kind"] == "local"
    assert "no linked" in r[0]["title"].lower()          # honest: not attributed to a TUI dispatch
    # copy must not imply a stalled/attributed TASK; `busy` is a GLOBAL gpu flag, so say so accurately
    assert r[0]["title"] != "busy — no linked TUI dispatch"


def test_vision_sidecar_is_labeled_a_SERVICE_not_no_linked():
    # the persistent gemma4-e4b vision sidecar (:8336) is ALWAYS a busy loaded ModelState when the GPU is
    # active — it is a SERVICE, never a stalled dispatch. It must NOT read "no linked TUI dispatch".
    models = [ModelState(name="gemma4-e4b-q4-k-m (:8336)", loaded=True, busy=True, gb=5.0)]
    r = inflight.build_inflight(models, [], [])
    assert len(r) == 1
    assert r[0]["kind"] == "service"
    assert r[0]["title"] == "vision service (:8336)"
    assert r[0]["base"] is None                           # a service has no dispatch to watch
    assert "no linked" not in r[0]["title"].lower()


def test_glm_sidecar_is_labeled_a_SERVICE():
    models = [ModelState(name="glm-4.7-flash (:8090)", loaded=True, busy=True)]
    r = inflight.build_inflight(models, [], [])
    assert r[0]["kind"] == "service" and r[0]["title"] == "GLM service (:8090)" and r[0]["base"] is None


def test_unknown_sidecar_port_gets_generic_service_label():
    models = [ModelState(name="mystery-model (:9999)", loaded=True, busy=True)]
    r = inflight.build_inflight(models, [], [])
    assert r[0]["kind"] == "service"
    assert r[0]["title"] == "llama-server service (:9999)" and r[0]["base"] is None


def test_a_finished_dispatch_does_not_count_as_in_flight_work():
    models = [ModelState(name="qwen3-coder:30b", loaded=True, busy=True)]
    disp = [_disp("gen→audit", "fleet-model-dispatch qwen3-coder:30b", running=False)]
    r = inflight.build_inflight(models, [], disp)
    assert r[0]["base"] is None                           # a done dispatch is not "what it's working on"


def test_loaded_but_not_busy_is_excluded():
    models = [ModelState(name="qwen3.6:35b", loaded=True, busy=False)]
    assert inflight.build_inflight(models, [], []) == []


def test_cloud_legs_pass_through_with_brief_and_base():
    cloud = [{"name": "codex-fleet", "activity": "audit the state layer", "base": "20260711-c", "started": None}]
    r = inflight.build_inflight([], cloud, [])
    assert len(r) == 1 and r[0]["kind"] == "cloud"
    assert r[0]["name"] == "codex-fleet" and r[0]["brief"] == "audit the state layer"
    assert r[0]["base"] == "20260711-c"                   # dispatch-derived cloud leg → watch button


def test_cloud_external_session_has_no_base():
    # an external CLI session / Claude worker carries no dispatch base → honest, no watch button
    cloud = [{"name": "codex (session)", "activity": "interactive session", "base": None}]
    r = inflight.build_inflight([], cloud, [])
    assert r[0]["kind"] == "cloud" and r[0]["base"] is None


def test_order_is_local_then_cloud():
    models = [ModelState(name="qwen3-coder:30b", loaded=True, busy=True)]
    disp = [_disp("gen", "fleet-model-dispatch qwen3-coder:30b")]
    cloud = [{"name": "grok-research", "activity": "web", "base": "b"}]
    r = inflight.build_inflight(models, cloud, disp)
    assert [e["kind"] for e in r] == ["local", "cloud"]


def test_service_and_task_coexist_in_order():
    # a real task-linked model AND the always-on vision sidecar → both listed, service distinguished
    models = [
        ModelState(name="qwen3-coder:30b", loaded=True, busy=True),
        ModelState(name="gemma4-e4b-q4-k-m (:8336)", loaded=True, busy=True),
    ]
    disp = [_disp("gen", "fleet-model-dispatch qwen3-coder:30b")]
    r = inflight.build_inflight(models, [], disp)
    kinds = [e["kind"] for e in r]
    assert kinds == ["local", "service"]
    assert r[0]["base"] == "20260711-x" and r[1]["base"] is None


def test_safe_on_junk_never_raises():
    assert inflight.build_inflight(None, None, None) == []
    # missing fields / wrong element types must not raise
    inflight.build_inflight([ModelState(name="x", loaded=True, busy=True)], [None, {}], [None, {"running": True}])
