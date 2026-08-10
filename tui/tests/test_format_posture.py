"""Gate for format_posture() — pure formatter, markup-safe, never raises on partial/missing input."""
from fleet_tui.widgets import format as fmt


FULL = {
    "backup": {"last": {"ts": "2026-07-07 00:54", "ok": True, "msg": "repos pushed"},
               "last_repos_ok": "2026-07-07 00:54", "last_mirror_ok": "2026-07-07 00:54",
               "last_abort": {"ts": "2026-07-05 04:24", "reason": "secret in a TRACKED file"},
               "alert_pending": False},
    "supply": {"ts": "2026-07-07 00:45", "alerts": 2, "install_hooks": 3, "new_since_last": 1,
               "alert_pending": True},
    "upstream": {"checked": "2026-07-06T15:47:03", "behind": 3,
                 "critical": [{"name": "open-second-brain", "local": "1.22.0", "latest": "1.24.0"},
                              {"name": "Hermes harness", "local": "2026.6.19", "latest": "2026.7.1"}]},
}


def test_full_renders_key_facts():
    out = fmt.format_posture(FULL)
    assert "2026-07-07 00:54" in out          # last good backup
    assert "last abort" in out and "secret in a TRACKED file" in out
    assert "alerts:2" in out
    assert "CRITICAL" in out
    assert "open-second-brain" in out and "1.22.0" in out and "1.24.0" in out


def test_clean_state_is_reassuring():
    clean = {
        "backup": {"last": {"ts": "2026-07-07 00:54", "ok": True, "msg": "repos pushed"},
                   "last_repos_ok": "2026-07-07 00:54", "last_mirror_ok": "2026-07-07 00:54",
                   "last_abort": None, "alert_pending": False},
        "supply": {"ts": "2026-07-07 00:45", "alerts": 0, "install_hooks": 3, "new_since_last": 0,
                   "alert_pending": False},
        "upstream": {"checked": "x", "behind": 0, "critical": []},
    }
    out = fmt.format_posture(clean)
    assert "all current" in out
    assert "abort" not in out                 # no abort line when there's no abort
    assert "ALERT pending" not in out


def test_empty_and_none_never_raise():
    assert isinstance(fmt.format_posture({}), str)
    assert isinstance(fmt.format_posture(None), str)
    # partial dicts (missing sub-keys) must not raise
    assert isinstance(fmt.format_posture({"backup": {}, "supply": {}, "upstream": {}}), str)


def test_pending_alert_surfaces():
    d = {"backup": {"last": None, "last_repos_ok": None, "last_mirror_ok": None,
                    "last_abort": None, "alert_pending": True},
         "supply": {"ts": None, "alerts": 0, "install_hooks": 0, "new_since_last": 0, "alert_pending": False},
         "upstream": {"checked": None, "behind": 0, "critical": []}}
    out = fmt.format_posture(d)
    assert "backup ALERT pending" in out
