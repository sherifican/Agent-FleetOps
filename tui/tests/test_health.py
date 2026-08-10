"""Tests for the health source module."""

import os
import json
from unittest.mock import patch

import pytest

from fleet_tui.sources import health
from fleet_tui.sources.health import (
    build_snapshot,
    read_fleet_doctor,
    read_ollama_ps,
    read_services,
    read_reliability_tail,
    snapshot
)
from fleet_tui.models import HealthSnapshot


@pytest.fixture(autouse=True)
def _clear_health_cache():
    """The readers memoize results; clear between tests so the safety tests aren't served a cached value."""
    health._cache.clear()
    yield
    health._cache.clear()


def test_build_snapshot_with_data():
    """Test building a snapshot with real data."""
    # Load fixtures
    fixture_dir = os.path.join(os.path.dirname(__file__), "fixtures")
    doctor_path = os.path.join(fixture_dir, "fleet-doctor.json")
    ps_path = os.path.join(fixture_dir, "ps_loaded.json")
    
    with open(doctor_path, 'r') as f:
        doctor = json.load(f)
    
    with open(ps_path, 'r') as f:
        ps = json.load(f)
    
    # Test building snapshot
    snap = build_snapshot(
        doctor,
        ps["models"],
        {"hermes-gateway": True}
    )
    
    # Assertions
    assert len(snap.critical_caps) == 2
    for cap in snap.critical_caps:
        assert "cap" in cap
        assert "ok" in cap
        assert "detail" in cap
    
    assert len(snap.loaded) == 2
    qwen_model = next((m for m in snap.loaded if m.name == "qwen3-coder:30b"), None)
    assert qwen_model is not None
    assert abs(qwen_model.gb - 18.6) < 0.1
    
    assert snap.services == {"hermes-gateway": True}


def test_build_snapshot_empty():
    """Test building a snapshot with empty data."""
    snap = build_snapshot({}, [], {})
    
    # Should return a valid HealthSnapshot
    assert isinstance(snap, HealthSnapshot)
    assert snap.loaded == []
    assert snap.critical_caps == []
    assert snap.services == {}
    assert snap.vram_note == ""


def test_loaded_includes_sidecars():
    # HEALTH's loaded list must count llama-server sidecars (else 'loaded: none' while a
    # resident sidecar like gemma4-vision :8336 holds VRAM — the owner-spotted discrepancy).
    sidecars = [{"name": "gemma4-e4b-q4-k-m", "port": 8336, "gb": 0.3}]
    snap = build_snapshot({}, [{"name": "qwen3.6:35b", "size": 23e9}], {}, sidecars=sidecars)
    names = [m.name for m in snap.loaded]
    assert "qwen3.6:35b" in names                              # ollama model still there
    assert any("8336" in n and "gemma4" in n for n in names)   # sidecar now counted
    # backward-compat: no sidecars arg → ollama-only, unchanged
    assert build_snapshot({}, [], {}).loaded == []


def test_vram_note():
    """Test vram note generation."""
    # Test with big model
    snap = build_snapshot({}, [{"name":"big","size":20_000_000_000}], {})
    assert snap.vram_note != ""
    
    # Test with small model
    snap = build_snapshot({}, [{"name":"small","size":1_000_000_000}], {})
    assert snap.vram_note == ""


def test_reader_safety_ollama():
    """Test that ollama reader handles errors gracefully."""
    with patch('urllib.request.urlopen') as mock_urlopen:
        mock_urlopen.side_effect = Exception("Network error")
        result = read_ollama_ps()
        assert result == []


def test_reader_safety_fleet_doctor():
    """Test that fleet-doctor reader handles errors gracefully."""
    with patch('subprocess.run') as mock_run:
        mock_run.side_effect = Exception("Process error")
        result = read_fleet_doctor()
        assert result == {}


def test_read_reliability_tail():
    """Test reading reliability tail."""
    # Test with a real file
    fixture_dir = os.path.join(os.path.dirname(__file__), "fixtures")
    reliability_path = os.path.join(fixture_dir, "FLEET_HEALTH_latest.txt")
    
    # This should not raise an exception even if the file doesn't exist
    result = read_reliability_tail(reliability_path, 3)
    assert isinstance(result, str)


def test_read_services():
    """Test reading services."""
    # Test with default services
    result = read_services()
    assert isinstance(result, dict)
    
    # Test with custom services
    result = read_services(["hermes-gateway"])
    assert isinstance(result, dict)
    assert "hermes-gateway" in result


def test_snapshot():
    """Test the snapshot convenience function."""
    # This should not raise any exceptions
    snap = snapshot()
    assert isinstance(snap, HealthSnapshot)


def test_read_disk():
    health._cache.clear()
    d = health.read_disk("/")          # a path that always exists
    assert d and d["total_gb"] > 0 and d["free_gb"] >= 0
    # bad path → safe {} (never raises)
    health._cache.clear()
    assert health.read_disk("/no/such/path/xyz") == {}
    # snapshot carries the disk fields through
    snap = snapshot()
    assert hasattr(snap, "disk_free_gb") and hasattr(snap, "disk_total_gb")
