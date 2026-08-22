import json
import time

from fleet_tui.models import DownloadRow, FleetBox, ModelState, ReceiptRow, ThroughputRow
from fleet_tui.sources import bg_agents, boxes, downloads, lanes, receipts, throughput
from fleet_tui.widgets.format import _pad_markup, format_box_models, format_downloads, format_lanes, format_receipt_grid
from fleet_tui.widgets.power import power_cell


def test_boxes_default_is_one_local_box(tmp_path):
    assert boxes.read_boxes(tmp_path / "absent.json") == [FleetBox()]


def test_boxes_config_carries_device_labels(tmp_path):
    path = tmp_path / "boxes.json"
    path.write_text(json.dumps({"boxes": [{"name": "desk", "kind": "local", "device_labels": {"dgpu": {"badge": "D", "color": "purple", "power_cap_w": 240}}}, {"name": "rack", "kind": "remote"}]}))
    result = boxes.read_boxes(path)
    assert [box.name for box in result] == ["desk", "rack"]
    assert result[0].device_labels["dgpu"].badge == "D"


def test_receipts_have_two_columns_model_first_and_right_tail():
    left, right = FleetBox(name="desk"), FleetBox(name="rack", kind="remote")
    rows = [ReceiptRow("RESULT_sample-topic-one.md", "desk", "model-alpha", "0", 3 * 1024 * 1024, "2026-08-22", "ok"),
            ReceiptRow("RESULT_sample-topic-two.md", "rack", "model-beta", "0", 512, "2026-08-21", "ok")]
    rendered = format_receipt_grid(rows, [left, right])
    assert "desk RECEIPTS" in rendered and "rack RECEIPTS" in rendered
    assert rendered.index("model-alpha") < rendered.index("RESULT_sample-topic-one")
    assert "3.0MB" in rendered and "0.5KB" in rendered and "│" in rendered


def test_receipts_are_safe_and_sorted(tmp_path):
    path = tmp_path / "receipts.jsonl"
    path.write_text('{"name":"old","rc":0,"bytes":1,"ts":"2026-01-01"}\nnot json\n{"name":"new","rc":0,"bytes":2,"ts":"2026-02-01"}\n')
    assert [row.name for row in receipts.read_receipts(path)] == ["new", "old"]
    assert receipts.read_receipts(tmp_path / "missing") == []


def test_sidecars_show_config_badge_state_wake_and_serving_rate():
    box = FleetBox(name="desk", device_labels={"gpu": boxes.DeviceLabel("D", "purple")})
    row = ModelState("sidecar", loaded=True, device="gpu", port=8123, state="busy", wake_on_use=True)
    rate = ThroughputRow("sidecar", 22.5, "desk")
    rendered = format_box_models([box], {"desk": [row]}, {"desk": {"sidecar": rate}})
    assert "D" in rendered and ":8123" in rendered and "busy" in rendered and "wake on use" in rendered
    assert "22.5 tok/s" in rendered


def test_lanes_are_union_attributed_by_box(tmp_path):
    left = tmp_path / "left.json"
    right = tmp_path / "right.json"
    left.write_text('{"lanes":[{"lane":"coder","admits":2}]}')
    right.write_text('{"lanes":[{"lane":"coder","admits":3},{"lane":"review","admits":1}]}')
    state = lanes.read_lanes([FleetBox(name="desk", ledger_path=str(left)), FleetBox(name="rack", kind="remote", ledger_path=str(right))])
    rendered = format_lanes(state, [FleetBox(name="desk"), FleetBox(name="rack", kind="remote")])
    assert "coder 5" in rendered and "desk:2 +2D" in rendered and "rack:3 +3R" in rendered


def test_background_agent_uses_recorded_model_and_missing_is_safe(tmp_path):
    path = tmp_path / "agents.jsonl"
    path.write_text('{"key":"a","state":"running","ts":' + str(time.time()) + ',"model":"recorded-model","label":"task"}\n')
    assert bg_agents.read_bg_agents(path, ttl_s=9999999999)[0]["name"] == "recorded-model"
    assert bg_agents.read_bg_agents(tmp_path / "missing") == []


def test_throughput_stays_separate_per_box(tmp_path):
    one, two = tmp_path / "one.json", tmp_path / "two.json"
    one.write_text('{"same":{"tok_s":11}}')
    two.write_text('{"same":{"tok_s":22}}')
    data = throughput.read_throughput([FleetBox(name="desk", throughput_path=str(one)), FleetBox(name="rack", kind="remote", throughput_path=str(two))])
    assert data["desk"]["same"].tok_s == 11 and data["rack"]["same"].tok_s == 22


def test_download_rows_keep_explicit_box_attribution(tmp_path):
    path = tmp_path / "downloads.jsonl"
    path.write_text('{"file":"sample.bin","box":"rack","agent":"worker","ts":2}\n')
    rows = downloads.read_downloads(path, "desk")
    assert rows[0].box == "rack" and "rack" in format_downloads(rows)


def test_markup_exact_fit_preserves_color_and_power_uses_configured_cap():
    assert _pad_markup("abc", "[green]abc[/]", 3) == "[green]abc[/]"
    assert " [yellow]120.0W[/]" == power_cell(120, 240)
