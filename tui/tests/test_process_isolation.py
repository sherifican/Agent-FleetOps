"""Positive and negative controls for the shared process boundary."""
import shlex
import subprocess

import pytest

from ._isolation import _dispatch_fixture


def test_process_allowlist_checks_script_and_paths(tmp_path):
    base = str(tmp_path / 'fixture with spaces')
    brief, out, log, done = [base + suffix for suffix in ('.brief', '.out', '.log', '.done')]
    script = (f'true {shlex.quote(brief)} {shlex.quote(out)} '
              f'> {shlex.quote(log)} 2>&1; touch {shlex.quote(done)}')
    command = ['setsid', 'sh', '-c', script]
    assert _dispatch_fixture(command, tmp_path)
    assert not _dispatch_fixture(command, tmp_path / 'different-root')
    assert not _dispatch_fixture(command[:-1] + [script + '; true'], tmp_path)
    assert not _dispatch_fixture(['/bin/sh', '-c', script], tmp_path)
    assert not _dispatch_fixture(['setsid', 'sh', '-c', "'"], tmp_path)


def test_unmocked_processes_are_blocked(tmp_path, _isolate_processes):
    sentinel = tmp_path / 'unexpected-process'
    with pytest.raises(FileNotFoundError, match='TUI test blocked process'):
        subprocess.run(['/bin/sh', '-c', 'touch ' + shlex.quote(str(sentinel))])
    assert not sentinel.exists()
    assert len(_isolate_processes) == 1
    _isolate_processes.clear()


@pytest.mark.parametrize('module_name,reader,args,expected,command', [
    ('health', 'read_fleet_doctor', (), {}, 'fleet-doctor'),
    ('health', 'read_services', (['synthetic-fixture.service'],),
     {'synthetic-fixture.service': False}, 'systemctl'),
    ('health', 'read_gpu', (), [], 'nvidia-smi'),
    ('network', 'read_ip_addr', (), '', 'ip'),
    ('network', 'read_pc_reachable', (), False, 'ping'),
    ('network', 'read_gateway', (), False, 'systemctl'),
    ('network', 'read_cron_list', (), '', 'hermes'),
    ('modelstate', 'read_gpu_util', (), 0, 'nvidia-smi'),
    ('jobs', 'read_crontab', (), '', 'crontab'),
    ('cloud_legs', '_claude_cmdlines', (), [], 'pgrep'),
    ('codex_link', '_port_listening', (4500,), False, 'ss'),
    ('codex_link', '_readyz', (4500,), 0, 'curl'),
])
def test_source_readers_use_the_blocked_boundary(
        module_name, reader, args, expected, command, _isolate_processes, monkeypatch):
    import ast
    import importlib
    from fleet_tui.sources import cloud_legs, health, jobs, modelstate

    monkeypatch.setattr(health, '_cache', {})
    monkeypatch.setattr(jobs, '_crontab_cache', {})
    monkeypatch.setattr(modelstate, '_util_cache', {})
    monkeypatch.setattr(cloud_legs, '_claude_cache', {'t': 0, 'v': []})
    module = importlib.import_module('fleet_tui.sources.' + module_name)
    assert _isolate_processes == []
    assert getattr(module, reader)(*args) == expected
    assert len(_isolate_processes) == 1, (module_name, reader, _isolate_processes)
    event, arguments = _isolate_processes[0]
    assert event == 'subprocess.Popen'
    assert ast.literal_eval(arguments)[0] == command
    _isolate_processes.clear()


# The emergency audit observer makes baseline RED runs safe: any launch missed
# by the production fixture raises a DIFFERENT exception before a process starts.
import os
import sys
import asyncio
import pty
import posix

_ESCAPE_ARMED = False


def _escape_observer(event, args):
    if _ESCAPE_ARMED and event in {'subprocess.Popen', 'os.system', 'os.exec',
                                  'os.spawn', 'os.posix_spawn', 'os.fork',
                                  'os.forkpty', 'pty.spawn', '_posixsubprocess.fork_exec'}:
        raise RuntimeError('process escaped TUI boundary: ' + event)


sys.addaudithook(_escape_observer)


def _process_plants():
    """Every public os exec/spawn form plus each higher-level launch family."""
    for module in (os, posix):
        for name in sorted(vars(module)):
            fn = getattr(module, name)
            if name.startswith('exec') and name in {'execl', 'execle', 'execlp', 'execlpe',
                                                    'execv', 'execve', 'execvp', 'execvpe'}:
                argv = ('/bin/true',) if name.startswith('execl') else (['/bin/true'],)
                args = ('/bin/true', *argv, *([{}] if name.endswith('e') else []))
            elif name in {'spawnl', 'spawnle', 'spawnlp', 'spawnlpe', 'spawnv', 'spawnve', 'spawnvp', 'spawnvpe'}:
                argv = ('/bin/true',) if name.startswith('spawnl') else (['/bin/true'],)
                args = (os.P_WAIT, '/bin/true', *argv, *([{}] if name.endswith('e') else []))
            elif name in {'posix_spawn', 'posix_spawnp'}:
                args = ('/bin/true', ['/bin/true'], {})
            elif name in {'system', 'popen'}:
                args = ('true',)
            elif name in {'fork', 'forkpty'}:
                args = ()
            else:
                continue
            yield pytest.param(fn, args, id=module.__name__ + '.' + name)
    for name in ('Popen', 'run', 'call', 'check_call', 'check_output'):
        # Resolve at call time, after the fixture installs the wrapper.
        yield pytest.param(lambda *a, n=name: getattr(subprocess, n)(*a), (['/bin/true'],), id='subprocess.' + name)
    yield pytest.param(lambda: pty.fork(), (), id='pty.fork')
    yield pytest.param(pty.spawn, (['/bin/true'],), id='pty.spawn')
    yield pytest.param(lambda: asyncio.run(asyncio.create_subprocess_exec('/bin/true')), (), id='asyncio.exec')
    yield pytest.param(lambda: asyncio.run(asyncio.create_subprocess_shell('true')), (), id='asyncio.shell')
    # A saved class alias bypasses monkeypatching; its audit event must still fire.
    yield pytest.param(subprocess.Popen, (['/bin/true'],), id='saved.Popen')


@pytest.mark.parametrize('reader,args', list(_process_plants()))
def test_planted_reader_hits_process_block(reader, args, _isolate_processes):
    global _ESCAPE_ARMED
    _ESCAPE_ARMED = True
    try:
        with pytest.raises(FileNotFoundError, match='TUI test blocked process'):
            reader(*args)
    finally:
        _ESCAPE_ARMED = False
    assert _isolate_processes, 'the block must record the planted call'
    _isolate_processes.clear()


@pytest.mark.parametrize('option', ['preexec_fn', 'cwd', 'env'])
def test_allowlisted_process_refuses_launch_overrides(tmp_path, option, _isolate_processes):
    base = str(tmp_path / 'fixture')
    script = (f'true {base}.brief {base}.out > {base}.log 2>&1; touch {base}.done')
    value = {'preexec_fn': lambda: None, 'cwd': str(tmp_path), 'env': {}}[option]
    global _ESCAPE_ARMED
    _ESCAPE_ARMED = True
    try:
        with pytest.raises(FileNotFoundError, match='TUI test blocked process'):
            subprocess.Popen(['setsid', 'sh', '-c', script], **{option: value})
    finally:
        _ESCAPE_ARMED = False
    assert _isolate_processes
    _isolate_processes.clear()


# Keep the actual native object from collection, before fixture monkeypatches.
import _posixsubprocess
_SAVED_FORK_EXEC = _posixsubprocess.fork_exec


@pytest.mark.parametrize('call_form', ['direct', 'dunder', 'partial'])
def test_saved_native_fork_exec_is_blocked_and_recorded(tmp_path, _isolate_processes, call_form):
    """A real signature and a harmless scratch marker make the baseline falsifiable."""
    from functools import partial
    call = {'direct': _SAVED_FORK_EXEC, 'dunder': _SAVED_FORK_EXEC.__call__,
            'partial': partial(_SAVED_FORK_EXEC)}[call_form]
    marker = os.fsencode(tmp_path / 'escaped-native-alias')
    r, w = os.pipe()
    args = ([b'/bin/touch', marker], [b'/bin/touch'], True, (w,), None, None,
            -1, -1, -1, -1, -1, -1, r, w, False, False, -1,
            None, None, None, -1, None)
    if sys.version_info < (3, 14):
        args += (True,)  # use_vfork was removed in 3.14
    try:
        for attempt in range(2):
            before = len(_isolate_processes)
            refused = False
            try:
                pid = call(*args)
            except OSError:
                refused = True  # native EAGAIN or the 3.14 audit-hook refusal
            else:
                os.waitpid(pid, 0)  # reap the harmless baseline escape
            if call_form == 'direct' or sys.version_info >= (3, 14):
                assert len(_isolate_processes) > before, (attempt, _isolate_processes)
            assert not os.path.exists(marker), 'saved native alias created the marker'
            assert refused, 'saved native alias returned a child PID'
    finally:
        os.close(r)
        os.close(w)
    _isolate_processes.clear()


def test_allowlisted_child_receives_only_explicit_environment(tmp_path, monkeypatch):
    """Read the real child's environment while its approved redirection waits."""
    base = str(tmp_path / 'environment')
    script = (f'true {base}.brief {base}.out > {base}.log 2>&1; touch {base}.done')
    forbidden = ('LD_AUDIT', 'LD_PRELOAD', 'LD_LIBRARY_PATH', 'GCONV_PATH', 'PROCESS_GATE_UNKNOWN')
    for key in forbidden:
        monkeypatch.setenv(key, str(tmp_path / 'nonexistent-loader-fixture'))
    monkeypatch.setenv('TMPDIR', str(tmp_path))
    monkeypatch.setenv('LANG', 'C')
    monkeypatch.setenv('LC_ALL', 'C')
    monkeypatch.setenv('LC_CTYPE', 'C')
    os.mkfifo(base + '.log')
    child = subprocess.Popen(['setsid', 'sh', '-c', script], stderr=subprocess.DEVNULL)
    reader = None
    try:
        # The FIFO keeps the permitted shell alive without adding a command to
        # the allowlist. /proc reports the environment supplied at exec.
        # setsid execs sh after Popen's error pipe closes. During that exec,
        # /proc can briefly return no environment; wait for a nonempty sample.
        import time
        deadline = time.monotonic() + 5
        raw = b''
        while not raw and time.monotonic() < deadline:
            with open(f'/proc/{child.pid}/environ', 'rb') as environment:
                raw = environment.read()
            if not raw:
                time.sleep(0.001)
        assert raw, 'no child environment observed'
        actual = dict(item.split(b'=', 1) for item in raw.split(b'\0') if item)
        assert set(actual) == {b'HOME', b'TMPDIR', b'LANG', b'LC_ALL', b'LC_CTYPE', b'PATH'}, actual
        assert not {key for key in forbidden if os.fsencode(key) in actual}, actual
        assert actual[b'PATH'] == b'/usr/bin:/bin'
        assert actual[b'HOME'] == os.fsencode(os.environ['HOME'])
        assert actual[b'TMPDIR'] == os.fsencode(tmp_path)
        assert actual[b'LANG'] == b'C'
        reader = os.open(base + '.log', os.O_RDONLY | os.O_NONBLOCK)
        assert child.wait(timeout=5) == 0
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=5)
        if reader is not None:
            os.close(reader)


@pytest.mark.parametrize('starters', [1, 4], ids=['nested', 'four-concurrent'])
def test_thread_starters_restore_zero_and_refuse_saved_alias(
        starters, tmp_path, _isolate_processes):
    """Copied race/escape probes, with assertions and a bounded start window."""
    import resource
    import threading
    import time
    errors = []
    def starter():
        try:
            stop = time.monotonic() + 0.25
            while time.monotonic() < stop:
                leaf = threading.Thread(target=lambda: None)
                leaf.start()
                leaf.join()
        except BaseException as error:
            errors.append(error)
    threads = [threading.Thread(target=starter) for _ in range(starters)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert not any(thread.is_alive() for thread in threads)
    assert not errors, errors
    soft = resource.getrlimit(resource.RLIMIT_NPROC)[0]
    # Exercise the saved alias even if the limit assertion would already fail.
    test_saved_native_fork_exec_is_blocked_and_recorded(tmp_path, _isolate_processes, 'direct')
    assert soft == 0


def test_escape_after_nested_start(tmp_path, _isolate_processes, preexisting_starter):
    import resource
    import threading
    from .conftest import _permit_process
    entered, leave, done = threading.Event(), threading.Event(), threading.Event()
    errors = []
    def starter():
        try:
            # Keep the child's window open past its parent's return. This pins
            # the bad interleaving without relying on the OS thread scheduler.
            with _permit_process('thread.start'):
                entered.set()
                assert leave.wait(5)
                leaf = threading.Thread(target=lambda: None)
                leaf.start()
            leaf.join(timeout=5)
            assert not leaf.is_alive()
        except BaseException as error:
            errors.append(error)
    def task():
        try:
            starter()
        finally:
            done.set()
    with _permit_process('thread.start'):
        preexisting_starter.put(task)
        assert entered.wait(5)
    leave.set()
    assert done.wait(10)
    assert not errors, errors
    soft = resource.getrlimit(resource.RLIMIT_NPROC)[0]
    test_saved_native_fork_exec_is_blocked_and_recorded(tmp_path, _isolate_processes, 'direct')
    assert soft == 0


@pytest.fixture(scope='module')
def preexisting_starter():
    """Higher-scope setup runs before the per-test process boundary."""
    import threading
    import queue
    tasks = queue.Queue()
    def work():
        while (task := tasks.get()) is not None:
            task()
    thread = threading.Thread(target=work)
    thread.start()
    yield tasks
    tasks.put(None)
    thread.join(timeout=10)
    assert not thread.is_alive()


def test_thread_created_before_fixture_can_start_threads(
        preexisting_starter, tmp_path, _isolate_processes):
    import threading
    import resource
    done = threading.Event()
    errors = []
    def work():
        try:
            leaf = threading.Thread(target=lambda: None)
            leaf.start()
            leaf.join(timeout=5)
            assert not leaf.is_alive()
            assert resource.getrlimit(resource.RLIMIT_NPROC)[0] == 0
            # Pre-existing threads lack the pre-3.14 profile observer. Check
            # actual refusal here; event recording is asserted by other tests.
            marker = os.fsencode(tmp_path / 'preexisting-thread-escape')
            r, w = os.pipe()
            args = ([b'/bin/touch', marker], [b'/bin/touch'], True, (w,), None, None,
                    -1, -1, -1, -1, -1, -1, r, w, False, False, -1,
                    None, None, None, -1, None)
            if sys.version_info < (3, 14):
                args += (True,)
            try:
                with pytest.raises(OSError):
                    _SAVED_FORK_EXEC(*args)
            finally:
                os.close(r)
                os.close(w)
            assert not os.path.exists(marker)
        except BaseException as error:
            errors.append(error)
        finally:
            done.set()
    preexisting_starter.put(work)
    assert done.wait(10)
    assert not errors, errors
    assert resource.getrlimit(resource.RLIMIT_NPROC)[0] == 0
    if sys.version_info >= (3, 14):
        assert _isolate_processes
    _isolate_processes.clear()


@pytest.mark.parametrize('custom_run', [False, True], ids=['target', 'subclass-run'])
def test_started_thread_waits_for_zero_limit(
        tmp_path, monkeypatch, _isolate_processes, custom_run):
    """Force run lookup inside the parent's window; measure 200 native attempts."""
    import resource
    import threading
    from contextlib import contextmanager
    from . import conftest

    real_permit = conftest._permit_process
    looked_up, finished = threading.Event(), threading.Event()
    observed, forks, failures = [], [], []

    @contextmanager
    def held_permit(*events):
        with real_permit(*events):
            yield
            if events == ('thread.start',):
                assert looked_up.wait(5), 'child never reached run lookup'
                # Baseline runs its target here. The repair must hold it until
                # this context exits and restores zero. A timeout is not a pass:
                # the target's actual limit and fork result are asserted below.
                finished.wait(0.01)

    monkeypatch.setattr(conftest, '_permit_process', held_permit)
    marker = os.fsencode(tmp_path / 'started-thread-escape')
    r, w = os.pipe()
    args = ([b'/bin/touch', marker], [b'/bin/touch'], True, (w,), None, None,
            -1, -1, -1, -1, -1, -1, r, w, False, False, -1,
            None, None, None, -1, None)
    if sys.version_info < (3, 14):
        args += (True,)

    def target():
        try:
            observed.append(resource.getrlimit(resource.RLIMIT_NPROC)[0])
            try:
                pid = _SAVED_FORK_EXEC(*args)
            except OSError:
                pass
            else:
                forks.append(pid)
                os.waitpid(pid, 0)
        except BaseException as error:
            failures.append(error)
        finally:
            finished.set()

    class Probe(threading.Thread):
        def __getattribute__(self, name):
            if name == 'run' and threading.current_thread() is self:
                looked_up.set()
            return super().__getattribute__(name)

    class CustomProbe(Probe):
        def run(self):
            target()

    try:
        for _ in range(200):
            looked_up.clear()
            finished.clear()
            thread = CustomProbe() if custom_run else Probe(target=target)
            thread.start()
            thread.join(timeout=5)
            assert not thread.is_alive()
        print(f'target-soft-limits={sorted(set(observed))}; '
              f'attempts={len(observed)}; forks={len(forks)}; custom-run={custom_run}')
        assert not failures, failures
        assert len(observed) == 200
        assert observed == [0] * 200
        assert forks == []
        assert not os.path.exists(marker)
    finally:
        os.close(r)
        os.close(w)
        # On 3.11/3.12 profile records the attempts; 3.14 also audits them.
        assert _isolate_processes
        _isolate_processes.clear()


_SAVED_POPEN = subprocess.Popen


def test_dispatch_options_cannot_execute_user_code(tmp_path, _isolate_processes):
    marker = tmp_path / 'escape'
    calls = []
    def escape(self, *args):
        calls.append('executed')
        _SAVED_POPEN(['/bin/sh', '-c', 'touch ' + shlex.quote(str(marker))]).wait()
        return 1
    class Fileno:
        fileno = escape
    class Number(int):
        __bool__ = __int__ = __index__ = __eq__ = escape
    class Text(str):
        __str__ = __eq__ = __hash__ = escape
    base = str(tmp_path / 'fixture')
    script = f'true {base}.brief {base}.out > {base}.log 2>&1; touch {base}.done'
    command = ['setsid', 'sh', '-c', script]
    for key in ('stdin', 'stdout', 'stderr', 'bufsize', 'close_fds', 'start_new_session'):
        for value in (Fileno(), Number(1)):
            with pytest.raises(FileNotFoundError, match='TUI test blocked process'):
                subprocess.Popen(command, **{key: value})
            assert _isolate_processes
            _isolate_processes.clear()
            assert calls == [] and not marker.exists()
    for key in ('text', 'encoding', 'errors'):
        for value in (True, 'utf-8', Text('utf-8')):
            with pytest.raises(FileNotFoundError, match='TUI test blocked process'):
                subprocess.Popen(command, **{key: value})
            assert _isolate_processes
            _isolate_processes.clear()
            assert calls == [] and not marker.exists()
    child = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
    assert child.wait(timeout=10) == 0
    import time
    deadline = time.monotonic() + 5
    while not (tmp_path / 'fixture.done').exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert (tmp_path / 'fixture.done').exists()
    assert not marker.exists() and calls == []


def test_dispatch_snapshots_caller_argv_before_validation(tmp_path, monkeypatch):
    from . import _isolation
    base = str(tmp_path / 'snapshot')
    script = f'true {base}.brief {base}.out > {base}.log 2>&1; touch {base}.done'
    caller = ['setsid', 'sh', '-c', script]
    marker = tmp_path / 'changed-command'
    observed = []
    previous = sys.getprofile()

    def after_admission(frame, event, result):
        if previous is not None:
            previous(frame, event, result)
        if event == 'return' and frame.f_code is _isolation._dispatch_fixture.__code__:
            snapshot = frame.f_locals['args']
            observed.append((type(snapshot), tuple(snapshot)))
            caller[3] = 'touch ' + shlex.quote(str(marker))

    sys.setprofile(after_admission)
    try:
        child = subprocess.Popen(caller, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        assert child.wait(timeout=10) == 0
    finally:
        sys.setprofile(previous)
    assert observed == [(tuple, ('setsid', 'sh', '-c', script))]
    assert (tmp_path / 'snapshot.done').exists()
    assert not marker.exists()


_NESTED_CALLBACK = None


def _nested_dispatch_hook(event, args):
    global _NESTED_CALLBACK
    if event == 'subprocess.Popen' and _NESTED_CALLBACK is not None:
        callback, _NESTED_CALLBACK = _NESTED_CALLBACK, None
        callback()


# Registered after the boundary's hook, with no action outside an armed test.
# The saved alias exercises auditing independently of the monkeypatch wrapper.
import sys
sys.addaudithook(_nested_dispatch_hook)


@pytest.mark.parametrize('route', ['audit-other-argv', 'audit-same-argv', 'finalizer'])
def test_dispatch_latch_refuses_nested_spawn(tmp_path, monkeypatch, _isolate_processes, route):
    import gc
    from contextlib import contextmanager
    from . import conftest as boundary
    global _NESTED_CALLBACK
    base = str(tmp_path / 'latch')
    script = f'true {base}.brief {base}.out > {base}.log 2>&1; touch {base}.done'
    command = ['setsid', 'sh', '-c', script]
    marker = tmp_path / 'nested'
    nested = command if route == 'audit-same-argv' else ['/bin/touch', str(marker)]
    fired, errors = [], []

    def attempt():
        fired.append(True)
        try:
            _SAVED_POPEN(nested).wait(timeout=5)
        except FileNotFoundError as error:
            errors.append(str(error))

    class Finalizer:
        def __del__(self):
            attempt()

    real_permit = boundary._permit_process
    enabled = gc.isenabled()
    try:
        if route == 'finalizer':
            gc.disable()
            garbage = Finalizer()
            garbage.cycle = garbage
            del garbage

            @contextmanager
            def collect_in_window(*events, **kwargs):
                with real_permit(*events, **kwargs):
                    if 'subprocess.Popen' in events:
                        gc.collect()
                    yield
            monkeypatch.setattr(boundary, '_permit_process', collect_in_window)
        else:
            _NESTED_CALLBACK = attempt
        child = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        assert child.wait(timeout=10) == 0
    finally:
        _NESTED_CALLBACK = None
        if enabled:
            gc.enable()
    assert fired == [True]
    assert len(errors) == 1
    assert len(_isolate_processes) == 1
    assert _isolate_processes[0][0] == 'subprocess.Popen'
    _isolate_processes.clear()
    assert (tmp_path / 'latch.done').exists()
    assert not marker.exists()

    if route == 'audit-same-argv':
        # Same argv alone cannot admit a native start: this call has a different
        # environment and inherited cwd. Its rejection leaves the real launch.
        native = []
        def native_start():
            pid = os.posix_spawn('/usr/bin/setsid', command, {'PATH': '/usr/bin:/bin'})
            native.append(pid)
            os.waitpid(pid, 0)
        def refused_native_start():
            with pytest.raises(FileNotFoundError, match='TUI test blocked process'):
                native_start()
        _NESTED_CALLBACK = refused_native_start
        try:
            assert subprocess.Popen(command).wait(timeout=10) == 0
        finally:
            _NESTED_CALLBACK = None
        assert len(native) == 0
        assert len(_isolate_processes) == 1
        _isolate_processes.clear()

        # Conversely, a finalizer/profile callback after the wrapper chooses
        # fork_exec cannot spend a leftover posix_spawn allowance (3.11/3.12
        # do not audit the ensuing native fork_exec).
        previous = sys.getprofile()
        attempted, refused = [], []
        def after_native_choice(frame, event, arg):
            if previous is not None:
                previous(frame, event, arg)
            if event == 'c_call' and arg is _SAVED_FORK_EXEC and not attempted:
                attempted.append(True)
                try:
                    native_start()
                except FileNotFoundError:
                    refused.append(True)
        sys.setprofile(after_native_choice)
        try:
            child = subprocess.Popen(command)
            assert child.wait(timeout=10) == 0
        finally:
            sys.setprofile(previous)
        assert attempted == refused == [True]
        assert len(native) == 0
        assert len(_isolate_processes) == 1
        _isolate_processes.clear()


@pytest.mark.parametrize('option', ['stdin', 'stdout', 'stderr', 'bufsize'])
def test_dispatch_refuses_bool_outside_boolean_flags(tmp_path, option, _isolate_processes):
    base = str(tmp_path / 'boolean')
    script = f'true {base}.brief {base}.out > {base}.log 2>&1; touch {base}.done'
    for value in (True, False):
        with pytest.raises(FileNotFoundError, match='TUI test blocked process'):
            subprocess.Popen(['setsid', 'sh', '-c', script], **{option: value})
        assert len(_isolate_processes) == 1
        _isolate_processes.clear()
    assert not (tmp_path / 'boolean.done').exists()


_WINDOW_CALLBACK = None


def _window_open_hook(event, args):
    global _WINDOW_CALLBACK
    if event == 'open' and args[0] == os.devnull and _WINDOW_CALLBACK is not None:
        callback, _WINDOW_CALLBACK = _WINDOW_CALLBACK, None
        callback()


sys.addaudithook(_window_open_hook)


def _latch_command(tmp_path):
    base = str(tmp_path / 'bound')
    return ['setsid', 'sh', '-c',
            f'true {base}.brief {base}.out > {base}.log 2>&1; touch {base}.done']


@pytest.mark.parametrize('route', ['saved-popen-path', 'posix-spawn-path'])
def test_dispatch_binds_program_environment_and_cwd(tmp_path, _isolate_processes, route):
    import shlex
    global _WINDOW_CALLBACK, _NESTED_CALLBACK
    command = _latch_command(tmp_path)
    marker = tmp_path / 'escaped'
    evil = tmp_path / 'evil'
    evil.mkdir()
    program = evil / 'setsid'
    program.write_text(f'#!/bin/sh\n: > {shlex.quote(str(marker))}\n')
    program.chmod(0o755)
    refused = []

    def attempt():
        try:
            if route == 'saved-popen-path':
                _SAVED_POPEN(command, env={'PATH': str(evil)}).wait(timeout=5)
            else:
                pid = os.posix_spawn(str(program), command, {})
                os.waitpid(pid, 0)
        except FileNotFoundError:
            refused.append(True)

    if route == 'saved-popen-path':
        _WINDOW_CALLBACK = attempt
    else:
        _NESTED_CALLBACK = attempt
    try:
        process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        assert process.wait(timeout=10) == 0
    finally:
        _WINDOW_CALLBACK = _NESTED_CALLBACK = None
    assert not marker.exists()
    assert refused == [True]
    assert len(_isolate_processes) == 1
    event, detail = _isolate_processes[0]
    assert event == ('subprocess.Popen' if route == 'saved-popen-path' else 'os.posix_spawn')
    assert str(evil) in detail  # the recorded rejection belongs to the attacker
    _isolate_processes.clear()
    assert (tmp_path / 'bound.done').exists()


def test_dispatch_bound_call_shape_runs(tmp_path, _isolate_processes):
    import time
    process = subprocess.Popen(_latch_command(tmp_path), stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, start_new_session=True)
    assert process.wait(timeout=10) == 0
    # setsid may fork when start_new_session makes its caller a group leader.
    deadline = time.monotonic() + 5
    while not (tmp_path / 'bound.done').exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert (tmp_path / 'bound.done').exists()
    assert not _isolate_processes


def test_dispatch_identical_start_consumes_only_one_allowance(tmp_path, _isolate_processes):
    import shutil
    global _WINDOW_CALLBACK
    command = _latch_command(tmp_path)
    env = {key: os.environ[key] for key in
           ('HOME', 'TMPDIR', 'LANG', 'LC_ALL', 'LC_CTYPE') if key in os.environ}
    env['PATH'] = '/usr/bin:/bin'
    started, refused = [], []

    def attempt_twice():
        for _ in range(2):
            try:
                process = _SAVED_POPEN(command, executable=shutil.which('setsid', path=env['PATH']),
                                       cwd=str(tmp_path), env=dict(env))
                started.append(process.wait(timeout=10))
            except FileNotFoundError:
                refused.append(True)

    _WINDOW_CALLBACK = attempt_twice
    try:
        with pytest.raises(FileNotFoundError, match='TUI test blocked process'):
            subprocess.Popen(command, stdout=subprocess.DEVNULL)
    finally:
        _WINDOW_CALLBACK = None
    assert started == [0] and refused == [True]
    assert (tmp_path / 'bound.done').exists()
    # The second injected start and the original outer start are both denied.
    assert [event for event, _ in _isolate_processes] == ['subprocess.Popen'] * 2
    _isolate_processes.clear()
