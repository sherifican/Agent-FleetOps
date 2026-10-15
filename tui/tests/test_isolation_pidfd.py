"""The retirement guard's pidfd reap needs Linux's P_PIDFD idtype.

Some CPython builds omit ``os.P_PIDFD``. On Linux the idtype is 3; on FreeBSD
3 is ``P_SID`` (a session-id wait whose ECHILD the reap would swallow) and on
macOS it is undefined, so the fallback must apply only on Linux. These tests
start no process: the peer's endpoint is closed in-process and the wait is
recorded instead of performed.
"""
import os
import socket
import sys

import pytest

from . import _isolation

_PIDFD = 987654  # never opened; os.waitid is replaced, so it is only recorded


@pytest.fixture
def closed_peer():
    """A cancellation channel whose peer already released its endpoint."""
    ours, theirs = socket.socketpair()
    theirs.close()
    yield ours
    ours.close()


@pytest.fixture
def waits(monkeypatch):
    calls = []
    monkeypatch.setattr(os, 'waitid', lambda idtype, ident, options: calls.append((idtype, ident, options)))
    return calls


def test_the_reap_uses_the_real_constant_when_present(closed_peer, waits, monkeypatch):
    monkeypatch.setattr(os, 'P_PIDFD', 77, raising=False)  # a sentinel: the constant is read at call time
    _isolation._stop_retirement_guard(-1, closed_peer.fileno(), _PIDFD)
    assert waits == [(77, _PIDFD, os.WEXITED)]


def test_the_reap_falls_back_to_linux_p_pidfd_when_the_constant_is_missing(closed_peer, waits, monkeypatch):
    monkeypatch.delattr(os, 'P_PIDFD', raising=False)
    monkeypatch.setattr(sys, 'platform', 'linux')
    _isolation._stop_retirement_guard(-1, closed_peer.fileno(), _PIDFD)
    assert waits == [(3, _PIDFD, os.WEXITED)]


def test_the_reap_refuses_to_guess_the_idtype_off_linux(closed_peer, waits, monkeypatch):
    monkeypatch.delattr(os, 'P_PIDFD', raising=False)
    monkeypatch.setattr(sys, 'platform', 'freebsd14')
    with pytest.raises(RuntimeError, match='only Linux'):
        _isolation._stop_retirement_guard(-1, closed_peer.fileno(), _PIDFD)
    assert waits == []
