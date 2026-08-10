"""Claude-authored gate for the Claude-worker-leg extension of sources/cloud_legs.py.
Pure/headless; the external-process reader is monkeypatched (no real processes). Never raises."""
from fleet_tui.sources import cloud_legs


def test_claude_dispatch_legs_are_cloud():
    # a claude-opus / claude-sonnet fleet DISPATCH must register as a cloud leg (dispatch-based path)
    assert cloud_legs.is_cloud_leg("claude-opus") is True
    assert cloud_legs.is_cloud_leg("claude-sonnet") is True
    # existing markers still work; local models still excluded
    assert cloud_legs.is_cloud_leg("codex-fleet") is True
    assert cloud_legs.is_cloud_leg("qwen3-coder") is False


def test_active_cloud_legs_shows_running_claude_dispatch():
    disp = [{"running": True, "leg": "claude-opus", "brief": "hard synthesis task", "when": "20260707-120000"}]
    legs = cloud_legs.active_cloud_legs(disp)
    assert len(legs) == 1
    assert legs[0]["name"] == "Claude (Opus 4.8)"   # v3.30: show the model tag, not the raw leg id
    assert "hard synthesis" in legs[0]["activity"]


def test_pretty_claude_model_maps_ids_to_short_names():
    assert cloud_legs._pretty_claude_model("claude-opus-4-8") == "Opus 4.8"
    assert cloud_legs._pretty_claude_model("claude-sonnet-5") == "Sonnet 5"
    assert cloud_legs._pretty_claude_model("claude-haiku-4-5") == "Haiku 4.5"
    # An unknown claude-<family>-<version> is still rendered as a NAME, not echoed as an id.
    # CHANGED 2026-08-08 (owner-reported): this line previously asserted the id came back
    # unchanged, which is precisely what printed "Claude (claude-fable-5) · worker" in the
    # MODELS panel — the vendor twice — as soon as a family appeared that the hardcoded
    # list above had never seen. The old assertion was pinning the defect in place.
    assert cloud_legs._pretty_claude_model("claude-mystery-9") == "Mystery 9"
    # Non-claude ids and empty input are still passed through untouched (never raises).
    assert cloud_legs._pretty_claude_model("gpt-5.6-sol") == "gpt-5.6-sol"
    assert cloud_legs._pretty_claude_model("") == ""


def test_external_claude_workers_detects_print_mode_with_model(monkeypatch):
    # monkeypatch the cmdline reader: one worker (claude -p --model sonnet), one INTERACTIVE (no -p)
    monkeypatch.setattr(cloud_legs, "_claude_cmdlines", lambda: [
        "claude -p some task here --model claude-sonnet-5 --output-format text",
        "claude",                                    # the interactive ORCHESTRATOR — must be excluded
    ])
    workers = cloud_legs.external_claude_workers()
    assert len(workers) == 1                          # only the -p worker, not the orchestrator
    w = workers[0]
    assert "Sonnet 5" in w["name"]                    # the model is surfaced
    assert w["activity"]                              # has some activity label


def test_external_claude_workers_default_model_when_unspecified(monkeypatch):
    monkeypatch.setattr(cloud_legs, "_claude_cmdlines", lambda: ["claude --print do a thing"])
    workers = cloud_legs.external_claude_workers()
    assert len(workers) == 1 and "claude" in workers[0]["name"].lower()   # still shown even w/o --model


def test_external_claude_workers_safe_on_error(monkeypatch):
    def boom(): raise RuntimeError("no /proc")
    monkeypatch.setattr(cloud_legs, "_claude_cmdlines", boom)
    assert cloud_legs.external_claude_workers() == []   # never raises


def test_cloud_snapshot_includes_claude_workers(monkeypatch):
    monkeypatch.setattr(cloud_legs, "_claude_cmdlines", lambda: ["claude -p x --model claude-opus-4-8"])
    monkeypatch.setattr(cloud_legs, "external_cloud_procs", lambda: [])   # isolate
    snap = cloud_legs.cloud_snapshot([])
    assert any("Opus 4.8" in l["name"] for l in snap)


def test_orchestrator_flags_are_not_false_positives(monkeypatch):
    """The interactive orchestrator's cmdline carries `--json-path`/`--permission-mode`/`--spawned-by`
    (all contain '-p' as a SUBSTRING). Token-based matching must NOT flag it as a worker (real bug 2026-07-07)."""
    monkeypatch.setattr(cloud_legs, "_claude_cmdlines", lambda: [
        "claude --json-path /x --permission-mode auto --spawned-by tui",   # orchestrator — NOT a worker
        "claude -p realtask --model claude-opus-4-8",                       # a real worker
    ])
    workers = cloud_legs.external_claude_workers()
    assert len(workers) == 1                       # only the real -p worker
    assert "Opus 4.8" in workers[0]["name"]


# ── v3.30: don't count the orchestrator as a session + show "Claude (model)" ──

def test_orchestrator_not_a_session_marker():
    # external_cloud_procs pgreps each SESSION marker as an interactive session. A bare `claude` process is
    # the ORCHESTRATOR, not a leg — so "claude" must NOT be in SESSION_MARKERS (else this session shows).
    assert "claude" not in cloud_legs.SESSION_MARKERS
    assert set(cloud_legs.SESSION_MARKERS) == {"codex", "grok", "kimi"}
    # but claude IS still a cloud marker for DISPATCH-leg detection
    assert "claude" in cloud_legs.CLOUD_MARKERS


def test_dispatch_claude_legs_display_as_Claude_model_tag():
    disp = [{"running": True, "leg": "claude-opus", "brief": "x", "when": "20260708-010000"},
            {"running": True, "leg": "claude-sonnet", "brief": "y", "when": "20260708-010000"}]
    names = [l["name"] for l in cloud_legs.active_cloud_legs(disp)]
    assert "Claude (Opus 4.8)" in names
    assert "Claude (Sonnet 5)" in names
    # non-claude legs are left untouched
    assert cloud_legs.active_cloud_legs(
        [{"running": True, "leg": "codex-fleet", "brief": "z", "when": "20260708-010000"}]
    )[0]["name"] == "codex-fleet"


def test_external_worker_uses_Claude_model_tag(monkeypatch):
    monkeypatch.setattr(cloud_legs, "_claude_cmdlines",
                        lambda: ["claude -p t --model claude-sonnet-5"])
    w = cloud_legs.external_claude_workers()
    assert len(w) == 1
    assert w[0]["name"].startswith("Claude (Sonnet 5)")   # "Claude (model)" tag, not "claude ... (worker)"
