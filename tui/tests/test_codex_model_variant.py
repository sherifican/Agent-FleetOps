import os
"""Gate: codex rows must name the MODEL/PROFILE variant, not collapse to a flat 'codex'.

Owner-reported 2026-08-08: every running codex process rendered as `codex (session)`, so the panel
could not show that the gh-watch cron had moved from Sol-at-xhigh to Terra-at-high. The variant IS
the task type — Sol/xhigh is the fleet's most expensive configuration, Luna/medium is a cheap
lookup — so collapsing them throws away the signal the owner wants at a glance.

Every cmdline used below was READ FROM /proc on 2026-08-08, not invented:
    codex exec --sandbox workspace-write -m gpt-5.6-sol -c model_reasoning_effort=xhigh -o /path -
    codex exec --sandbox workspace-write -p terra
    codex exec --sandbox workspace-write -p terra -c model_reasoning_effort=xhigh   (codex-research)

CRITICAL: these tests patch _iter_proc_cmdlines with raising=True (the default). If that helper is
renamed, the suite FAILS LOUDLY rather than silently patching nothing and falling through to the
real process table — which would make every assertion below depend on whatever happens to be
running on the box at the time.
"""
from fleet_tui.sources import cloud_legs


def _procs(monkeypatch, *cmdlines):
    """Drive read_codex_procs()/codex_status() from fake argv lists, bypassing the 15s cache.

    The cache MUST be busted: a sibling test (or the app itself) can populate it with real data,
    which would make these assertions read live state instead of the stub — the order-dependent
    flake documented in the fleet-tui-dev skill.
    """
    rows = [(1000 + i, list(c)) for i, c in enumerate(cmdlines)]
    monkeypatch.setattr(cloud_legs, "_iter_proc_cmdlines", lambda: iter(rows))
    cloud_legs._codex_cache["t"] = 0.0
    return cloud_legs.codex_status()


def _raw(monkeypatch, *cmdlines):
    """Same, but returns the PROC records (pid/variant/via/effort/mode) rather than the display rows.

    codex_status() composes read_codex_procs() through build_codex_rows(), so it yields
    name/activity/started. Tests that assert on the parsed fields must read the earlier stage.
    """
    rows = [(1000 + i, list(c)) for i, c in enumerate(cmdlines)]
    monkeypatch.setattr(cloud_legs, "_iter_proc_cmdlines", lambda: iter(rows))
    cloud_legs._codex_cache["t"] = 0.0
    return cloud_legs.read_codex_procs()


SOL = ["codex", "exec", "--sandbox", "workspace-write", "-m", "gpt-5.6-sol",
       "-c", "model_reasoning_effort=xhigh", "-o", os.path.expanduser("~/.cache/gh_watch/verify_out.md.finalmsg"), "-"]
TERRA = ["codex", "exec", "--sandbox", "workspace-write", "-p", "terra"]
LUNA = ["codex", "exec", "--sandbox", "workspace-write", "-p", "luna"]
RESEARCH = ["codex", "exec", "--sandbox", "workspace-write", "-p", "terra",
            "-c", "model_reasoning_effort=xhigh"]


def _render(row):
    """Mirror how format_cloud_legs joins name + activity, so the test sees what the user sees."""
    name = str(row.get("name", "") or "")
    act = str(row.get("activity", "") or "")
    return f"{name} · {act}" if act else name


# ---------- the core ask: different models must read differently ----------

def test_sol_names_the_model(monkeypatch):
    """Catches: the flat 'codex (session)' row that started this — an -m model id must reach the name."""
    rows = _procs(monkeypatch, SOL)
    assert rows, "a running `codex exec -m ...` must produce a row"
    assert rows[0]["name"] == "codex Sol", rows[0]["name"]


def test_terra_names_the_profile(monkeypatch):
    """Catches: reading only -m and ignoring -p, which would blank every profile-based wrapper."""
    rows = _procs(monkeypatch, TERRA)
    assert rows and rows[0]["name"] == "codex Terra", rows


def test_luna_names_the_profile(monkeypatch):
    rows = _procs(monkeypatch, LUNA)
    assert rows and rows[0]["name"] == "codex Luna", rows


def test_two_variants_are_two_distinct_rows(monkeypatch):
    """THE point of the feature: Sol and Terra running at once must be individually visible.

    Catches: any implementation that dedupes on the bare program name, or that reports only the
    first match — both of which would re-collapse the panel into one indistinguishable row.
    """
    rows = _procs(monkeypatch, SOL, LUNA)
    names = sorted(r["name"] for r in rows)
    assert names == ["codex Luna", "codex Sol"], names


# ---------- the honesty rule: never report an effort that is not on the cmdline ----------

def test_effort_shown_when_literally_present(monkeypatch):
    rows = _procs(monkeypatch, SOL)
    assert "xhigh" in _render(rows[0]), _render(rows[0])


def test_effort_absent_is_not_invented(monkeypatch):
    """Catches the tempting 'terra means high' hardcode.

    `-p terra` proves the PROFILE is terra. The effort lives in a toml this reader never opens, and
    the toml can be edited. A row that prints an effort nobody observed is a confident lie, which is
    strictly worse than a row that stays quiet.
    """
    text = _render(_procs(monkeypatch, TERRA)[0]).lower()
    for guess in ("high", "medium", "xhigh", "low"):
        assert guess not in text, f"invented an unobserved effort {guess!r}: {text!r}"


def test_profile_is_not_claimed_to_be_a_model(monkeypatch):
    """Catches mapping 'terra' -> 'gpt-5.6-terra': the cmdline never said that."""
    text = _render(_procs(monkeypatch, TERRA)[0]).lower()
    assert "gpt-5.6" not in text, text


def test_research_shows_profile_and_override(monkeypatch):
    """codex-research = `-p terra` PLUS an xhigh override, so profile alone cannot identify it.

    Catches: stopping at the first recognised flag and never scanning for the effort override.
    """
    text = _render(_procs(monkeypatch, RESEARCH)[0])
    assert "Terra" in text and "xhigh" in text, text


# ---------- the codex-specific trap ----------

def test_dash_p_is_profile_not_print(monkeypatch):
    """In kimi and claude, `-p` means PRINT. In codex it means PROFILE and takes a value.

    Catches a copy-paste of the kimi mode logic: reading `-p` as print would consume 'terra' as a
    mode flag, losing the variant AND mislabelling the row.
    """
    recs = _raw(monkeypatch, TERRA)
    assert recs[0]["variant"] == "Terra", recs[0]
    assert "terra" not in str(recs[0].get("mode", "")).lower()


def test_exec_means_dispatch_bare_means_session(monkeypatch):
    """Catches: labelling an owner's interactive `codex` as a fleet dispatch, or vice versa."""
    disp = _raw(monkeypatch, TERRA)[0]
    sess = _raw(monkeypatch, ["codex"])[0]
    assert disp["mode"] == "dispatch", disp
    assert sess["mode"] == "session", sess
    assert "interactive" in _render(_procs(monkeypatch, ["codex"])[0]).lower()


def test_codex_as_an_argument_does_not_match(monkeypatch):
    """Catches substring matching: `grep -r codex .` is not a codex process.

    Same trap the kimi reader documents — a bare 'codex' token that is not argv[0] is an argument.
    """
    assert _procs(monkeypatch, ["grep", "-r", "codex", "."]) == []
    assert _procs(monkeypatch, ["rg", "codex"]) == []


def test_wrapper_path_form_matches(monkeypatch):
    """`timeout 600 /usr/local/bin/codex exec ...` — codex is the program, just not argv[0]."""
    rows = _procs(monkeypatch, ["timeout", "600", "/usr/local/bin/codex", "exec", "-p", "luna"])
    assert rows and rows[0]["name"] == "codex Luna", rows


# ---------- purity / robustness contracts ----------

def test_identical_invocations_collapse(monkeypatch):
    """Two workers of the same variant answer the same question — one row, not two."""
    rows = _procs(monkeypatch, SOL, list(SOL))
    assert len(rows) == 1, rows


def test_unknown_profile_still_renders(monkeypatch):
    """A profile nobody hardcoded must still show its name — the flat-row bug was exactly a
    hardcoded list failing to recognise something new."""
    rows = _procs(monkeypatch, ["codex", "exec", "-p", "vega"])
    assert rows and rows[0]["name"] == "codex Vega", rows


def test_no_variant_info_still_lists_codex(monkeypatch):
    rows = _procs(monkeypatch, ["codex", "exec"])
    assert rows and rows[0]["name"] == "codex", rows


def test_never_raises(monkeypatch):
    """Source purity contract: the panel degrades, it never dies."""
    def boom():
        raise OSError("proc table unreadable")
    monkeypatch.setattr(cloud_legs, "_iter_proc_cmdlines", boom)
    cloud_legs._codex_cache["t"] = 0.0
    assert cloud_legs.codex_status() == []


def test_build_rows_is_pure(monkeypatch):
    """build_codex_rows must be a pure composer — callable with no I/O and no cache."""
    out = cloud_legs.build_codex_rows([
        {"pid": 1, "variant": "Sol", "via": "model", "effort": "xhigh", "mode": "dispatch"},
    ])
    assert out[0]["name"] == "codex Sol"
    assert out[0]["started"] is None
    assert "xhigh" in out[0]["activity"]


# ---------- wiring: the specific rows must SUPERSEDE the generic one ----------

def test_snapshot_drops_generic_codex_row(monkeypatch):
    """Catches shipping both rows: `codex Sol` next to a stale `codex (session)` is worse than
    the original bug — it double-counts one process and still shows the uninformative label."""
    monkeypatch.setattr(cloud_legs, "codex_status",
                        lambda: [{"name": "codex Sol", "activity": "fleet dispatch · xhigh", "started": None}])
    monkeypatch.setattr(cloud_legs, "external_cloud_procs",
                        lambda: [{"name": "codex (session)", "activity": "interactive session", "started": None}])
    monkeypatch.setattr(cloud_legs, "kimi_status", lambda: [])
    monkeypatch.setattr(cloud_legs, "external_claude_workers", lambda: [])
    names = [r["name"] for r in cloud_legs.cloud_snapshot([])]
    assert "codex Sol" in names, names
    assert "codex (session)" not in names, names


def test_snapshot_keeps_generic_row_when_no_variant_data(monkeypatch):
    """The fallback must survive: if variant detection finds nothing, do not lose codex entirely."""
    monkeypatch.setattr(cloud_legs, "codex_status", lambda: [])
    monkeypatch.setattr(cloud_legs, "external_cloud_procs",
                        lambda: [{"name": "codex (session)", "activity": "interactive session", "started": None}])
    monkeypatch.setattr(cloud_legs, "kimi_status", lambda: [])
    monkeypatch.setattr(cloud_legs, "external_claude_workers", lambda: [])
    names = [r["name"] for r in cloud_legs.cloud_snapshot([])]
    assert "codex (session)" in names, names


def test_snapshot_survives_codex_status_blowing_up(monkeypatch):
    """Panel-must-never-die contract at the wiring seam, not just inside the reader."""
    def boom():
        raise RuntimeError("nope")
    monkeypatch.setattr(cloud_legs, "codex_status", boom)
    monkeypatch.setattr(cloud_legs, "external_cloud_procs", lambda: [])
    monkeypatch.setattr(cloud_legs, "kimi_status", lambda: [])
    monkeypatch.setattr(cloud_legs, "external_claude_workers", lambda: [])
    assert cloud_legs.cloud_snapshot([]) == []
