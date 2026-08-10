"""Tests for the dispatch-target registry (default + file override + allow-list)."""
import json
from fleet_tui.sources import targets


def test_default_registry():
    groups = targets.list_groups("/no/such/file.json")   # missing → built-in default
    names = [g["name"] for g in groups]
    assert "Cloud legs" in names and "Local models" in names and "Combos" in names and "Teams" in names
    # every target has an id + cmd
    for t in targets.all_targets("/no/such/file.json"):
        assert t.get("id") and t.get("cmd")


def test_allowed_cmds_is_the_allowlist():
    allowed = targets.allowed_cmds("/no/such/file.json")
    assert "grok-research" in allowed
    assert "fleet-model-dispatch qwen3-coder:30b" in allowed
    assert "combo-gencode-audit" in allowed
    assert "reconcile-dispatch" in allowed
    # a team target reuses codex-driver (router)
    assert "codex-driver" in allowed


def test_file_override(tmp_path):
    p = tmp_path / "targets.json"
    p.write_text(json.dumps({"groups": [{"name": "Mine", "targets": [{"id": "x", "cmd": "echo"}]}]}))
    groups = targets.list_groups(str(p))
    assert groups == [{"name": "Mine", "targets": [{"id": "x", "cmd": "echo"}]}]
    assert targets.allowed_cmds(str(p)) == {"echo"}
    # corrupt file → falls back to default (never raises)
    p.write_text("{ not json")
    assert any(g["name"] == "Cloud legs" for g in targets.list_groups(str(p)))
