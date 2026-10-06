"""Exercise fixture lifecycle failures without lowering the runner's hard limit."""
import resource
import sys
import threading
import subprocess

import pytest

from . import conftest as boundary

_SAVED_POPEN = subprocess.Popen


@pytest.fixture(autouse=True)
def _isolate_processes():
    # These tests invoke the real generator themselves with simulated OS state.
    # The separate lifecycle control below starts a thread under the real boundary.
    yield


@pytest.mark.parametrize('fault', ['setrlimit', 'sys-profile', 'thread-profile', 'lower-hard'])
def test_process_fixture_restores_after_failure(monkeypatch, tmp_path, request, fault):
    state = {'limit': (123, 456), 'sys': object(), 'thread': object()}
    original = state.copy()
    fired = []

    def update(key, value):
        if key == 'limit' and value[1] > state['limit'][1]:
            raise ValueError('not allowed to raise maximum limit')
        state[key] = value
        if not fired and fault == {'limit': 'setrlimit', 'sys': 'sys-profile',
                                   'thread': 'thread-profile'}[key]:
            fired.append(fault)
            raise RuntimeError('planted setup failure: ' + fault)

    monkeypatch.setattr(resource, 'getrlimit', lambda which: state['limit'])
    monkeypatch.setattr(resource, 'setrlimit', lambda which, value: update('limit', value))
    monkeypatch.setattr(sys, 'getprofile', lambda: state['sys'])
    monkeypatch.setattr(sys, 'setprofile', lambda value: update('sys', value))
    monkeypatch.setattr(threading, 'getprofile', lambda: state['thread'])
    monkeypatch.setattr(threading, 'setprofile', lambda value: update('thread', value))
    generator = boundary._isolate_processes.__wrapped__(monkeypatch, tmp_path, request)
    try:
        if fault == 'lower-hard':
            next(generator)
            state['limit'] = (0, 100)
            with pytest.raises(pytest.fail.Exception, match='hard limit.*lowered'):
                next(generator)
            assert state['limit'] == (100, 100)
        else:
            with pytest.raises(RuntimeError, match='planted setup failure'):
                next(generator)
            assert fired == [fault]
            assert state['limit'] == original['limit']
        assert state['sys'] is original['sys']
        assert state['thread'] is original['thread']
        assert boundary._PROCESS_BLOCKS is None
        assert boundary._PROCESS_LIMIT is None
        assert boundary._PROCESS_WINDOWS == 0
        assert boundary._PROCESS_WINDOWS_CLOSED.is_set()
    finally:
        # Preserve runner state even against a deliberately broken baseline.
        generator.close()
        boundary._PROCESS_BLOCKS = boundary._PROCESS_LIMIT = None
        boundary._PROCESS_WINDOWS = 0
        boundary._PROCESS_WINDOWS_CLOSED.set()


@pytest.mark.parametrize('overlap', [False, True])
def test_leaked_window_cannot_decrement_next_fixture(monkeypatch, tmp_path, request, overlap):
    from contextlib import nullcontext
    with monkeypatch.context() as first:
        generator = boundary._isolate_processes.__wrapped__(first, tmp_path, request)
        next(generator)
        stale = boundary._permit_process('thread.start')
        stale.__enter__()
        with pytest.raises(pytest.fail.Exception, match='allow windows still active'):
            next(generator)
    assert boundary._PROCESS_WINDOWS == 0
    with monkeypatch.context() as second:
        generator = boundary._isolate_processes.__wrapped__(second, tmp_path, request)
        next(generator)
        try:
            assert resource.getrlimit(resource.RLIMIT_NPROC)[0] == 0
            with boundary._permit_process('thread.start') if overlap else nullcontext():
                stale.__exit__(None, None, None)
                assert boundary._PROCESS_WINDOWS == int(overlap)
                if overlap:
                    assert boundary._PROCESS_PERMIT.events == ('thread.start',)
            assert boundary._PROCESS_WINDOWS == 0
            assert resource.getrlimit(resource.RLIMIT_NPROC)[0] == 0
            ran = []
            thread = threading.Thread(target=lambda: ran.append(True))
            thread.start()
            thread.join(timeout=5)
            assert ran == [True] and not thread.is_alive()
        finally:
            generator.close()


def test_worker_permission_expires_with_its_fixture(monkeypatch, tmp_path, request):
    entered, resume, finished = threading.Event(), threading.Event(), threading.Event()
    marker = tmp_path / 'stale-worker'
    argv = ('/bin/touch', str(marker))
    environment = {'PATH': '/usr/bin:/bin'}
    binding = boundary._exec_binding(argv, '/bin/touch', str(tmp_path), environment)
    result = []

    def worker():
        with boundary._permit_process('subprocess.Popen', 'os.posix_spawn',
                                      '_posixsubprocess.fork_exec', 'fork_exec.call', binding=binding):
            entered.set()
            if not resume.wait(10):
                result.append('timeout')
                finished.set()
                return
            try:
                _SAVED_POPEN(argv, executable='/bin/touch', cwd=str(tmp_path),
                             env=environment).wait(timeout=5)
            except FileNotFoundError:
                result.append('refused')
            finally:
                finished.set()

    with monkeypatch.context() as first:
        generator = boundary._isolate_processes.__wrapped__(first, tmp_path, request)
        next(generator)
        thread = threading.Thread(target=worker)
        thread.start()
        assert entered.wait(5)
        with pytest.raises(pytest.fail.Exception, match='allow windows still active'):
            next(generator)
    with monkeypatch.context() as second:
        generator = boundary._isolate_processes.__wrapped__(second, tmp_path, request)
        blocked = next(generator)
        try:
            with boundary._permit_process('thread.start'):
                resume.set()
                assert finished.wait(5)
            thread.join(timeout=5)
            assert not thread.is_alive()
            assert result == ['refused']
            assert not marker.exists()
            assert len(blocked) == 1 and blocked[0][0] == 'subprocess.Popen'
            blocked.clear()
        finally:
            resume.set()
            thread.join(timeout=5)
            generator.close()
