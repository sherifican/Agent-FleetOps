"""Tests for the focus source module."""

import json
import os
import tempfile
from unittest import mock

import pytest

from fleet_tui.sources.focus import (
    is_on,
    read_state,
    turn_on,
    turn_off,
    _lock_path
)


@pytest.fixture(autouse=True)
def setup_lock_path(tmp_path, monkeypatch):
    """Set up a temporary lock file path for each test."""
    lock_file = tmp_path / "watchers.lock"
    monkeypatch.setenv("FLEET_WATCHERS_LOCK", str(lock_file))
    return lock_file


def test_absent_lock_file(setup_lock_path):
    """Test behavior when lock file is absent."""
    # Ensure the file doesn't exist
    assert not os.path.exists(setup_lock_path)
    
    # is_on should return False
    assert is_on() is False
    
    # read_state should return FocusState(on=False, scope="noisy")
    state = read_state()
    assert state.on is False
    assert state.scope == "noisy"


def test_turn_on(setup_lock_path):
    """Test turning on focus mode."""
    # Initially off
    assert is_on() is False
    
    # Turn on
    state = turn_on("noisy")
    
    # Should be on now
    assert is_on() is True
    
    # Check the returned state
    assert state.on is True
    assert state.scope == "noisy"
    assert state.by == "tui"
    assert state.since != ""
    
    # Verify file content
    with open(setup_lock_path, "r") as f:
        data = json.load(f)
        assert "since" in data
        assert data["by"] == "tui"
        assert data["scope"] == "noisy"


def test_turn_off(setup_lock_path):
    """Test turning off focus mode."""
    # Turn on first
    turn_on()
    
    # Should be on
    assert is_on() is True
    
    # Turn off
    turn_off()
    
    # Should be off now
    assert is_on() is False


def test_turn_off_already_absent(setup_lock_path):
    """Test that turning off when already absent doesn't raise."""
    # File doesn't exist yet
    assert not os.path.exists(setup_lock_path)
    
    # This should not raise
    turn_off()


def test_turn_on_idempotent(setup_lock_path):
    """Test that turning on multiple times is idempotent."""
    # Turn on first time
    state1 = turn_on("noisy")
    
    # Turn on second time
    state2 = turn_on("all")
    
    # Should still be on
    assert is_on() is True
    
    # Should have the latest values
    assert state2.scope == "all"
    assert state2.by == "tui"
    
    # File should contain the latest data
    with open(setup_lock_path, "r") as f:
        data = json.load(f)
        assert data["scope"] == "all"


def test_malformed_lock_file(setup_lock_path):
    """Test behavior with malformed lock file."""
    # Create a malformed file
    with open(setup_lock_path, "w") as f:
        f.write("garbage{")
    
    # is_on should return True (file exists)
    assert is_on() is True
    
    # read_state should not raise and should return on=True with default values
    state = read_state()
    assert state.on is True
    assert state.scope == "noisy"
    assert state.since == ""
    assert state.by == ""
