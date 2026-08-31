import os
"""Gate: the MODELS panel must say WHICH Kimi is running (K3 vs K2.7 Code) and in what MODE.

The kimi CLI selects K3 only via an explicit `-m k3`; with no `-m` it falls through to the config
default (kimi-code/kimi-for-coding = "K2.7 Code"). The owner has a standing directive to trial K3
specifically, so a generic "kimi (session)" row hides the one fact that matters. A headless fleet
dispatch (`kimi -p ...`, what the kimi-cli wrapper runs) must also not be labelled "interactive".

★ MEASURED PROCESS SHAPE (live, 2026-07-28) — the whole reason this is non-obvious:

    pid 1739966  comm=timeout    timeout 600 /home/…/.kimi-code/bin/kimi -m k3 -p "<prompt>" --output-format stream-json
    pid 1739968  comm=kimi-code  kimi-code

The process `pgrep -x kimi-code` matches carries NO flags — its whole argv is "kimi-code". The
`-m`/`-p` flags live on the launching wrapper, whose comm is not kimi-anything. Detection must scan
for the INVOCATION argv (a token whose basename is `kimi`), not just the matched pid's cmdline.
"""
from fleet_tui.sources import cloud_legs

KIMI_BIN = os.path.expanduser("~/.kimi-code/bin/kimi")


def _procs(table, monkeypatch):
    """Stub the single /proc seam with a hand-built {pid: argv} table."""
    monkeypatch.setattr(cloud_legs, "_kimi_cache", {"t": 0.0, "v": []}, raising=False)
    monkeypatch.setattr(cloud_legs, "_iter_proc_cmdlines",
                        lambda: iter(sorted(table.items())), raising=False)


# ---------- the pure composer ----------

def test_build_rows_names_the_model_and_mode():
    rows = cloud_legs.build_kimi_rows([
        {"pid": 11, "model": "K3", "mode": "dispatch", "raw_model": "k3"},
        {"pid": 12, "model": "K2.7 Code", "mode": "session", "raw_model": "kimi-code/kimi-for-coding"},
    ])
    acts = {r["name"]: r["activity"] for r in rows}
    assert "kimi K3" in acts, f"K3 must be named in the row, got {list(acts)}"
    assert "kimi K2.7 Code" in acts
    assert acts["kimi K3"] == "fleet dispatch"
    assert acts["kimi K2.7 Code"] == "interactive session"
    for r in rows:
        assert set(r) >= {"name", "activity", "started"}


def test_build_rows_is_pure_and_empty_safe():
    assert cloud_legs.build_kimi_rows([]) == []


# ---------- detection against the REAL process shape ----------

def test_real_shape_k3_dispatch_is_detected_from_the_wrapper_argv(monkeypatch):
    """The exact tree measured live: flags on the `timeout` wrapper, bare argv on kimi-code."""
    _procs({
        1739966: ["timeout", "600", KIMI_BIN, "-m", "k3", "-p", "prompt", "--output-format", "stream-json"],
        1739968: ["kimi-code"],
    }, monkeypatch)
    procs = cloud_legs.read_kimi_procs()
    assert len(procs) == 1, f"one invocation -> exactly one row, got {procs}"
    assert procs[0]["model"] == "K3"
    assert procs[0]["mode"] == "dispatch"
    assert procs[0]["pid"] == 1739966


def test_no_m_flag_falls_through_to_K2_7_default(monkeypatch):
    """Pre-patch kimi-cli passed no -m. That must NOT read as K3."""
    _procs({500: ["timeout", "600", KIMI_BIN, "-p", "prompt"]}, monkeypatch)
    procs = cloud_legs.read_kimi_procs()
    assert procs[0]["model"] == "K2.7 Code"
    assert procs[0]["mode"] == "dispatch"


def test_direct_invocation_without_a_wrapper(monkeypatch):
    _procs({501: [KIMI_BIN, "-m", "k3"]}, monkeypatch)
    procs = cloud_legs.read_kimi_procs()
    assert procs[0]["model"] == "K3"
    assert procs[0]["mode"] == "session"


def test_joined_model_flag_form(monkeypatch):
    _procs({502: [KIMI_BIN, "--model=k3", "--print", "x"]}, monkeypatch)
    procs = cloud_legs.read_kimi_procs()
    assert procs[0]["model"] == "K3"
    assert procs[0]["mode"] == "dispatch"


def test_unknown_model_id_is_preserved_not_guessed(monkeypatch):
    _procs({503: [KIMI_BIN, "-m", "k9-experimental", "-p", "x"]}, monkeypatch)
    assert cloud_legs.read_kimi_procs()[0]["model"] == "k9-experimental"


def test_bare_kimi_code_alone_still_reports_something(monkeypatch):
    """kimi is demonstrably up but how it started cannot be seen -> default model, session."""
    _procs({504: ["kimi-code"]}, monkeypatch)
    procs = cloud_legs.read_kimi_procs()
    assert len(procs) == 1, "must not report nothing while kimi is running"
    assert procs[0]["model"] == "K2.7 Code"
    assert procs[0]["mode"] == "session"


def test_no_kimi_running_reports_nothing(monkeypatch):
    _procs({900: ["bash", "-c", "sleep"], 901: ["python3", "app.py"]}, monkeypatch)
    assert cloud_legs.read_kimi_procs() == []


def test_two_concurrent_invocations_both_reported(monkeypatch):
    _procs({
        601: ["timeout", "600", KIMI_BIN, "-m", "k3", "-p", "a"],
        602: ["kimi-code"],
        603: [KIMI_BIN, "-p", "b"],
        604: ["kimi-code"],
    }, monkeypatch)
    procs = cloud_legs.read_kimi_procs()
    assert len(procs) == 2, f"two invocations, children must not double-count: {procs}"
    assert {p["model"] for p in procs} == {"K3", "K2.7 Code"}


# ---------- the substring trap that already bit this module once ----------

def test_path_containing_dash_p_is_not_a_dispatch(monkeypatch):
    """`--json-path` contains '-p' as a SUBSTRING. Token equality only, never `in`."""
    _procs({700: [KIMI_BIN, "--json-path", "/tmp/a-p.json"]}, monkeypatch)
    assert cloud_legs.read_kimi_procs()[0]["mode"] == "session"


def test_a_path_merely_mentioning_kimi_is_not_an_invocation(monkeypatch):
    """Only a token whose BASENAME is `kimi` counts — not any path containing the word."""
    _procs({701: ["less", os.path.expanduser("~/.kimi-code/README.md")],
            702: ["grep", "-r", "kimi", "~"]}, monkeypatch)
    assert cloud_legs.read_kimi_procs() == []


# ---------- never crash ----------

def test_total_failure_returns_empty_never_raises(monkeypatch):
    monkeypatch.setattr(cloud_legs, "_kimi_cache", {"t": 0.0, "v": []}, raising=False)

    def boom():
        raise OSError("/proc exploded")

    monkeypatch.setattr(cloud_legs, "_iter_proc_cmdlines", boom, raising=False)
    assert cloud_legs.read_kimi_procs() == []
    assert cloud_legs.kimi_status() == []


def test_seam_exists_and_is_safe_on_the_real_box():
    """The real seam must run without raising and yield (int, list) pairs."""
    got = list(cloud_legs._iter_proc_cmdlines())
    for pid, argv in got[:20]:
        assert isinstance(pid, int)
        assert isinstance(argv, list)


# ---------- constants the wiring depends on ----------

def test_default_model_constant_matches_the_real_cli_default():
    assert cloud_legs.KIMI_DEFAULT_MODEL_ID == "kimi-code/kimi-for-coding"
    assert cloud_legs.KIMI_MODEL_DISPLAY["k3"] == "K3"
    assert cloud_legs.KIMI_MODEL_DISPLAY["kimi-code/kimi-for-coding"] == "K2.7 Code"


def test_existing_cloud_leg_behaviour_untouched():
    assert cloud_legs.SESSION_PROCS["kimi"] == ("kimi", "kimi-code")
    assert "claude" not in cloud_legs.SESSION_PROCS
    assert cloud_legs.is_cloud_leg("kimi") is True
    assert cloud_legs.is_cloud_leg("qwen3-coder:30b") is False
