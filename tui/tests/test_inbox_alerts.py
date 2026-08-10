"""Claude-authored gate for the v3.12 inbox alert-channel extension (spec_inbox_alerts.md).
Pure-source tests — no textual, no I/O beyond tmp fixtures."""
import json
from fleet_tui.sources import inbox


AUTO_TWO = (
    '{"ts": "2026-07-07 00:41", "job": "supply-chain-scan", "detail": "scanner FAILED rc=1"}\n'
    '{"ts": "2026-07-07 00:44", "job": "hermes-gateway", "detail": "GATEWAY DOWN"}\n'
)


def test_automation_item_parses_json_lines():
    it = inbox.automation_item(AUTO_TWO)
    assert it is not None
    assert it.source == "automation"
    assert it.priority == "crit"
    assert "2 automation failure(s)" == it.title
    assert "supply-chain-scan" in it.detail and "hermes-gateway" in it.detail
    assert it.age == "2026-07-07 00:41"
    assert "GATEWAY DOWN" in it.body


def test_automation_item_skips_garbage_lines_and_empty():
    assert inbox.automation_item("") is None
    assert inbox.automation_item("   \n") is None
    it = inbox.automation_item('not json\n' + AUTO_TWO.splitlines()[0] + "\n")
    assert it is not None and it.title.startswith("1 ")


def test_backup_and_supply_items():
    assert inbox.backup_item({}) is None
    assert inbox.supply_item({"pending": False}) is None
    b = inbox.backup_item({"pending": True, "detail": "secret in tracked file"})
    s = inbox.supply_item({"pending": True, "detail": "compromised pkg"})
    assert b.source == "backup" and b.priority == "crit" and "secret" in b.detail
    assert s.source == "supply" and s.priority == "crit" and "compromised" in s.detail


def test_hive_and_telegram_items():
    assert inbox.hive_item("") is None
    h = inbox.hive_item("[2026-07-07] HIVE drift:\n  - 02-stack.md STALE\n")
    assert h.source == "hive" and h.pending and "HIVE" in h.title
    assert inbox.telegram_item({}) is None
    t = inbox.telegram_item({"pending": True, "count": 3, "directed": 1})
    assert t.source == "telegram" and "3 message(s)" in t.detail


def test_build_inbox_order_crit_first():
    items = inbox.build_inbox(
        AUTO_TWO,                                  # automation (crit)
        {"pending": True, "detail": "bk"},         # backup (crit)
        {"pending": True, "detail": "sp"},         # supply (crit)
        {},                                        # dep
        {},                                        # curation
        "gh alert line",                           # github (crit)
        "hive drift",                              # hive
        "",                                        # rejects
        "",                                        # hf
        {"pending": True, "count": 1, "directed": 1},  # telegram
    )
    srcs = [i.source for i in items]
    assert srcs == ["automation", "github", "backup", "supply", "hive", "telegram"]


def test_ack_new_channels(tmp_path, monkeypatch):
    auto = tmp_path / "auto"; auto.write_text(AUTO_TWO)
    hive = tmp_path / "hive"; hive.write_text("drift")
    bk = tmp_path / "bk"; bk.write_text(json.dumps({"pending": True, "detail": "x"}))
    sp = tmp_path / "sp"; sp.write_text(json.dumps({"pending": True, "detail": "y"}))
    monkeypatch.setattr(inbox, "AUTOMATION_ALERT", str(auto))
    monkeypatch.setattr(inbox, "HIVE_ALERT", str(hive))
    monkeypatch.setattr(inbox, "BACKUP_ALERT", str(bk))
    monkeypatch.setattr(inbox, "SUPPLY_ALERT", str(sp))
    assert inbox.ack("automation") and auto.read_text() == ""
    assert inbox.ack("hive") and hive.read_text() == ""
    assert inbox.ack("backup") and json.loads(bk.read_text())["pending"] is False
    assert inbox.ack("supply") and json.loads(sp.read_text())["pending"] is False
    assert inbox.ack("telegram") is False   # Claude owns that trigger


def test_existing_readers_still_work():
    # the old channels must keep functioning through the rewrite
    assert inbox.github_item("boom").source == "github"
    assert inbox.dep_item({"pending": True, "updates": []}) is not None
    assert inbox.curation_item({"pending": True, "reasons": ["r"]}) is not None


def test_hf_item_is_clean_summary_not_raw_dump():
    """The HF digest must render as a clean model-name summary, never a raw JSON/markdown dump
    (regression: after the HF path fix, hf_item flooded the inbox with stray JSON, 2026-07-07)."""
    digest = (
        "## HF-WATCH 2026-07-02T17:12:33Z — 2 new models, 0 new papers\n"
        "### 🔔 SIGNAL — deepreinforce-ai/Ornith-1.0-397B\n"
        "```json\n{\n  \"needs_eval\": true,\n  \"lane\": \"coder\"\n}\n```\n"
        "### 🔔 SIGNAL — zai-org/GLM-5.2\n"
        "```json\n{\n  \"significance\": \"high\"\n}\n```\n"
        "## HF-WATCH 2026-07-07T07:00:01Z — 0 new models, 2 new papers\n"
    )
    it = inbox.hf_item(digest)
    assert it is not None and it.source == "hf"
    # model names present, raw json/markdown absent
    assert "Ornith-1.0-397B" in it.body and "GLM-5.2" in it.body
    assert "needs_eval" not in it.body and "```" not in it.body and "{" not in it.body
    assert "2 model(s) flagged" in it.detail
    assert inbox.hf_item("") is None
