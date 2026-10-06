"""Shared pytest fixtures for the Fleet TUI suite."""
import copy
# Also cover callers that collect these tests without the TUI pytest config.
from ._isolation import _IMPORT_ENV, _ORIGINAL_USER_ROOTS, _TEST_ROOT


import pytest

from fleet_tui.widgets import anim
from fleet_tui.models import FleetBox, FocusState, HealthSnapshot


# CPython process audit events cover aliases imported before monkeypatching.
# os.py exec*l* -> execv/execve; spawn* -> fork/exec; popen -> Popen.
# pty.py fork -> forkpty (or fork); asyncio/unix_events.py -> Popen.
# subprocess.py audits Popen before posix_spawn or _posixsubprocess.fork_exec.
# Audit is active only for TUI tests, so combined guard collection stays usable.
import sys
import threading
import resource
from contextlib import contextmanager

_PROCESS_EVENTS = frozenset({'subprocess.Popen', 'os.system', 'os.exec', 'os.spawn',
                            'os.posix_spawn', 'os.fork', 'os.forkpty',
                            '_posixsubprocess.fork_exec', 'pty.spawn'})
_PROCESS_BLOCKS = None
_PROCESS_PERMIT = threading.local()
_PROCESS_LIMIT = None
_PROCESS_WINDOW_LOCK = threading.Lock()
_PROCESS_WINDOWS = 0
_PROCESS_GENERATION = object()
_PROCESS_WINDOWS_CLOSED = threading.Event()
_PROCESS_WINDOWS_CLOSED.set()


def _deny_process(event, args):
    if _PROCESS_BLOCKS is not None:
        _PROCESS_BLOCKS.append((event, repr(args)))
    raise FileNotFoundError('TUI test blocked process: ' + event + ' ' + repr(args))


def _current_permit():
    latch = getattr(_PROCESS_PERMIT, 'latch', None)
    if latch is not None and latch['generation'] is _PROCESS_GENERATION:
        return latch
    return None


def _exec_text(value):
    # One canonical representation: exact str/bytes -> filesystem bytes.
    # Reject subclasses before conversion so no caller overrides can run.
    import os
    if type(value) not in (str, bytes):
        raise ValueError('non-builtin exec text')
    return os.fsencode(value)


def _exec_vector(value):
    if type(value) not in (list, tuple):
        raise ValueError('non-builtin exec vector')
    return tuple(_exec_text(item) for item in value)


def _exec_env(value):
    # Mapping order does not determine exec behavior. Preserve exact key/value
    # bytes, split native K=V entries only once, and reject duplicate keys.
    if type(value) is dict:
        pairs = [(_exec_text(k), _exec_text(v)) for k, v in dict.items(value)]
    elif type(value) in (list, tuple):
        pairs = []
        for item in value:
            key, sep, val = _exec_text(item).partition(b'=')
            if not sep:
                raise ValueError('invalid exec environment')
            pairs.append((key, val))
    else:
        raise ValueError('non-builtin exec environment')
    if len({key for key, _ in pairs}) != len(pairs):
        raise ValueError('duplicate exec environment key')
    return tuple(sorted(pairs))


def _exec_binding(argv, executable, cwd, env):
    return {'argv': _exec_vector(argv), 'executable': _exec_text(executable),
            'executables': (_exec_text(executable),), 'cwd': _exec_text(cwd),
            'env': _exec_env(env)}


def _bound_process_event(event, args, latch):
    import os
    bound = latch['binding']
    if bound is None or type(args) is not tuple:
        return False
    try:
        if event == 'subprocess.Popen':
            executable, argv, cwd, env = args
            return (_exec_text(executable) == bound['executable']
                    and _exec_vector(argv) == bound['argv']
                    and _exec_text(cwd) == bound['cwd']
                    and _exec_env(env) == bound['env'])
        if event in {'os.posix_spawn', 'os.exec'}:
            executable, argv, env = args
            # These events inherit cwd; neither carries a cwd argument or has
            # a later audited stage. Check the actual cwd at admission instead.
            return (_exec_text(executable) == bound['executable']
                    and _exec_vector(argv) == bound['argv']
                    and _exec_env(env) == bound['env']
                    and os.getcwdb() == bound['cwd'])
        if event == 'fork_exec.call':
            # CPython 3.11/3.12: 23 args; 3.14: 22 (no allow_vfork).
            if len(args) not in (22, 23):
                return False
            argv, executables, cwd, env = args[0], args[1], args[4], args[5]
            if _exec_text(cwd) != bound['cwd']:
                return False
        else:  # CPython 3.14 native event: executable_list, argv, env_list.
            executables, argv, env = args
            # Its missing cwd is covered only by the active checked wrapper.
            if not latch['native_active'] or 'fork_exec.call' in latch['remaining']:
                return False
        return (_exec_vector(executables) == bound['executables']
                and _exec_vector(argv) == bound['argv']
                and _exec_env(env) == bound['env'])
    except (ValueError, TypeError, OSError):
        return False


def _take_process_event(event, args):
    latch = _current_permit()
    if latch is None or event not in latch['remaining']:
        return False
    if event in {'subprocess.Popen', 'os.posix_spawn', '_posixsubprocess.fork_exec',
                 'fork_exec.call', 'os.exec'}:
        if not _bound_process_event(event, args, latch):
            return False
        if event in {'os.posix_spawn', '_posixsubprocess.fork_exec', 'fork_exec.call'}:
            if 'subprocess.Popen' in latch['remaining']:
                return False
    # Consume before returning to later hooks/finalizers on the permitted thread.
    try:
        latch['remaining'].remove(event)
    except KeyError:  # a callback may have consumed it while argv was copied
        return False
    if event in {'os.posix_spawn', '_posixsubprocess.fork_exec'}:
        latch['remaining'].difference_update(
            {'os.posix_spawn', '_posixsubprocess.fork_exec', 'fork_exec.call'})
    if event == 'fork_exec.call':
        # Choosing fork_exec also retires posix_spawn on Python versions where
        # the native fork_exec emits no audit event of its own.
        latch['remaining'].discard('os.posix_spawn')
    if event in {'os.fork', 'os.forkpty'}:
        latch['remaining'].difference_update({'os.fork', 'os.forkpty'})
    return True


def _process_audit(event, args):
    if (_PROCESS_BLOCKS is not None and event in _PROCESS_EVENTS
            and not _take_process_event(event, args)):
        _deny_process(event, args)


sys.addaudithook(_process_audit)


@contextmanager
def _permit_process(*events, binding=None):
    global _PROCESS_WINDOWS
    previous = getattr(_PROCESS_PERMIT, 'events', ())
    previous_latch = _current_permit()
    latch = {'binding': binding, 'remaining': set(events), 'native_active': False}
    # The limit belongs to the process; event permissions belong to the thread.
    # Never hold the lock across yield: Thread.start waits for the new thread.
    with _PROCESS_WINDOW_LOCK:
        generation = _PROCESS_GENERATION
        latch['generation'] = generation
        active = _PROCESS_LIMIT is not None
        if active:
            if _PROCESS_WINDOWS == 0:
                resource.setrlimit(resource.RLIMIT_NPROC, _PROCESS_LIMIT)
                _PROCESS_WINDOWS_CLOSED.clear()
            _PROCESS_WINDOWS += 1
    _PROCESS_PERMIT.events = events
    _PROCESS_PERMIT.latch = latch
    try:
        yield
    finally:
        with _PROCESS_WINDOW_LOCK:
            # A leaked context may unwind after teardown or during the next
            # test. It owns no count or permission in that fixture generation.
            if generation is _PROCESS_GENERATION:
                _PROCESS_PERMIT.events = previous
                _PROCESS_PERMIT.latch = previous_latch
            if active and generation is _PROCESS_GENERATION:
                _PROCESS_WINDOWS = max(0, _PROCESS_WINDOWS - 1)
                if _PROCESS_WINDOWS == 0 and _PROCESS_LIMIT is not None:
                    resource.setrlimit(resource.RLIMIT_NPROC, (0, _PROCESS_LIMIT[1]))
                    _PROCESS_WINDOWS_CLOSED.set()


@pytest.fixture(autouse=True)
def _isolate_processes(monkeypatch, tmp_path, request):
    """Block process creation in unprivileged Linux TUI tests.

    Audit events record public calls; a profile observer records direct saved
    fork_exec calls even before CPython 3.14. RLIMIT_NPROC=0 also refuses native
    calls hidden inside C wrappers (which have neither event). Those refusals
    raise native EAGAIN and need not appear in the event record. Unconsumed
    recorded blocks fail teardown, even when the caller caught the exception.
    Intentional negative controls must assert on and explicitly clear the list.

    Only the exact scratch dispatch, one terminal PTY fork/exec and Python
    thread starts temporarily restore the incoming process limit. The limit is
    process-wide, with a locked shared count: it stays raised until the last
    overlapping window closes, then returns to soft zero. New thread run methods
    wait for that restoration before entering the target, including subclass run
    overrides. Later permit windows can still overlap an already running target.
    Threads created before
    setup also use the patched Thread.start, but on Python 3.11/3.12 they do not
    gain the profile observer. Their saved native aliases still face soft zero
    outside allow windows; public audit events remain covered in every thread.
    Dispatch binds one argv snapshot, resolved executable (also the singleton
    native executable list), exact cwd and sanitized environment before raising
    the limit. Exact str/bytes become filesystem bytes; exact list/tuple become
    tuples; exact dict or K=V vector environments become sorted byte pairs.
    Popen and fork_exec.call bind all four fields. Native fork_exec (3.14) binds
    argv/executables/env and requires the active cwd-checked wrapper. posix_spawn
    and PTY exec bind path/argv/env and check inherited cwd at admission. Each
    stage is consumed once before later hooks; mismatches are denied/recorded.

    Named limit — unaudited native starts and harness tampering: native code
    or raw syscalls that emit no audit event while RLIMIT_NPROC is raised are
    outside this boundary. So are tests calling _permit_process directly or
    replacing its private globals or deliberately changing limits/profiles.
    Executable bytes/inodes and filesystem changes after path resolution are
    not bound. For inherited cwd, concurrent chdir after the check is not bound.
    An identical start can consume the one allowance; caller identity is not
    authenticated. Tests must join threads before teardown. This is not a
    hostile-code sandbox. Root/capability bypasses
    are refused at setup rather than silently disabling the kernel boundary.
    """
    import os
    import pty
    import subprocess
    import _posixsubprocess
    from ._isolation import _dispatch_fixture

    global _PROCESS_BLOCKS, _PROCESS_LIMIT, _PROCESS_WINDOWS, _PROCESS_GENERATION
    with open('/proc/self/status') as status:
        capabilities = int(next(line.split()[1] for line in status
                                if line.startswith('CapEff:')), 16)
    if os.getuid() == 0 or os.geteuid() == 0 or capabilities & ((1 << 21) | (1 << 24)):
        pytest.fail('TUI process isolation requires non-root without CAP_SYS_ADMIN/SYS_RESOURCE')
    blocked = []
    real_popen, real_execve, real_fork = subprocess.Popen, os.execve, pty.fork
    real_fork_exec = _posixsubprocess.fork_exec
    original_limit = resource.getrlimit(resource.RLIMIT_NPROC)
    old_profile, old_thread_profile = sys.getprofile(), threading.getprofile()
    real_thread_start = threading.Thread.start

    def profile(previous):
        def observe(frame, event, arg):
            if (_PROCESS_BLOCKS is not None and event == 'c_call' and arg is real_fork_exec
                    and not ((_current_permit() or {}).get('native_active', False))):
                # Do not raise here: CPython disables a profile callback that
                # raises. The kernel limit refuses this and subsequent calls.
                _PROCESS_BLOCKS.append(('_posixsubprocess.fork_exec', 'saved native call outside this thread permission'))
            if previous is not None:
                previous(frame, event, arg)
        return observe

    def thread_start(self, *args, **kwargs):
        # Linux counts threads in RLIMIT_NPROC too. Textual/asyncio need threads.
        if self._started.is_set():
            return real_thread_start(self, *args, **kwargs)
        run = self.run
        had_run = 'run' in vars(self)
        saved_run = vars(self).get('run')

        def restore_run():
            if had_run:
                self.run = saved_run
            else:
                del self.run

        def gated_run():
            # An overlapping start/process window must close too. Recheck under
            # the transition lock in case another window cleared a signaled event.
            while True:
                _PROCESS_WINDOWS_CLOSED.wait()
                with _PROCESS_WINDOW_LOCK:
                    if _PROCESS_WINDOWS == 0:
                        break
            restore_run()
            return run()

        self.run = gated_run
        try:
            with _permit_process('thread.start'):
                return real_thread_start(self, *args, **kwargs)
        except BaseException:
            if not self._started.is_set():
                restore_run()
            raise

    terminal = (request.node.name == 'test_embedded_terminal_spawns_and_renders'
                and request.node.path.name == 'test_terminal.py')
    fork_used = child = False

    def popen(args, *positional, **kwargs):
        if type(args) not in (list, tuple):
            _deny_process('subprocess.Popen', args)
        # Snapshot caller storage once, before inspecting any element. Every
        # later check and the launch use this immutable copy. **kwargs is a
        # call-local dict; admitted option values are immutable exact builtins.
        args = tuple(args)
        # Unknown options fail closed; no preexec_fn/cwd/env can cross this seam.
        allowed = {'stdout', 'stderr', 'stdin', 'start_new_session', 'close_fds',
                   'bufsize'}
        if (positional or any(type(key) is not str for key in kwargs)
                or set(kwargs) - allowed
                or any(type(value) not in ((int, bool, type(None))
                       if key in {'close_fds', 'start_new_session'} else (int, type(None)))
                       for key, value in kwargs.items())
                or any(type(value) is not str for value in args)
                or not _dispatch_fixture(args, tmp_path)):
            _deny_process('subprocess.Popen', args)
        # No fileno, numeric dunder, codec lookup or argument conversion may
        # execute test code inside the permit. Text/encoding/errors are refused.
        cwd = str(tmp_path)
        # Use known system tools even if a test changed PATH or shell startup vars.
        # Only fixture essentials cross the boundary. Unknown loader, shell and
        # runtime knobs must not become executable behavior in an allowed child.
        env = {key: os.environ[key] for key in
               ('HOME', 'TMPDIR', 'LANG', 'LC_ALL', 'LC_CTYPE') if key in os.environ}
        env['PATH'] = '/usr/bin:/bin'
        import shutil
        executable = shutil.which(args[0], path=env['PATH'])
        if executable is None:
            _deny_process('subprocess.Popen', args)
        binding = _exec_binding(args, executable, cwd, env)
        with _permit_process('subprocess.Popen', 'os.posix_spawn',
                             '_posixsubprocess.fork_exec', 'fork_exec.call', binding=binding):
            return real_popen(args, executable=executable, cwd=cwd, env=env, **kwargs)

    def fork_exec(*args, **kwargs):
        # Also close the private primitive on Python versions predating its
        # native audit event. subprocess caches an alias at module import.
        if kwargs or not _take_process_event('fork_exec.call', args):
            _deny_process('_posixsubprocess.fork_exec', args)
        latch = _current_permit()
        latch['native_active'] = True
        try:
            return real_fork_exec(*args, **kwargs)
        finally:
            latch['native_active'] = False

    def fork():
        nonlocal fork_used, child
        if not terminal or fork_used:
            _deny_process('pty.fork', ())
        fork_used = True
        with _permit_process('os.fork', 'os.forkpty'):
            pid, fd = real_fork()
        child = pid == 0
        return pid, fd

    def execvp(file, args):
        if not child or file != '/bin/bash' or args != ['/bin/bash']:
            _deny_process('os.exec', (file, args))
        argv, env, cwd = ('/bin/bash',), dict(os.environ), os.getcwd()
        binding = _exec_binding(argv, '/bin/bash', cwd, env)
        with _permit_process('os.exec', binding=binding):
            return real_execve('/bin/bash', argv, env)

    try:
        _PROCESS_BLOCKS = blocked
        monkeypatch.setattr(subprocess, 'Popen', popen)
        monkeypatch.setattr(_posixsubprocess, 'fork_exec', fork_exec)
        monkeypatch.setattr(subprocess, '_fork_exec', fork_exec)
        monkeypatch.setattr(pty, 'fork', fork)
        monkeypatch.setattr(os, 'execvp', execvp)
        with _PROCESS_WINDOW_LOCK:
            _PROCESS_LIMIT = original_limit
            resource.setrlimit(resource.RLIMIT_NPROC, (0, original_limit[1]))
            monkeypatch.setattr(threading.Thread, 'start', thread_start)
        sys.setprofile(profile(old_profile))
        threading.setprofile(profile(old_thread_profile))
        yield blocked
    finally:
        # Attempt every restoration even if an earlier one fails. A test can
        # irreversibly lower its hard limit; clear fixture state before failing.
        errors = []
        for label, restore, value in (
                ('sys profile', sys.setprofile, old_profile),
                ('thread profile', threading.setprofile, old_thread_profile)):
            try:
                restore(value)
            except Exception as error:
                errors.append(f'{label}: {error}')
        with _PROCESS_WINDOW_LOCK:
            remaining = _PROCESS_WINDOWS
            try:
                current_hard = resource.getrlimit(resource.RLIMIT_NPROC)[1]
                if (current_hard != resource.RLIM_INFINITY
                        and (original_limit[1] == resource.RLIM_INFINITY
                             or current_hard < original_limit[1])):
                    errors.append('RLIMIT_NPROC hard limit was lowered; cannot restore original')
                    soft = original_limit[0]
                    if soft == resource.RLIM_INFINITY or soft > current_hard:
                        soft = current_hard
                    resource.setrlimit(resource.RLIMIT_NPROC, (soft, current_hard))
                else:
                    resource.setrlimit(resource.RLIMIT_NPROC, original_limit)
            except Exception as error:
                errors.append(f'RLIMIT_NPROC restore: {error}')
            finally:
                _PROCESS_LIMIT = None
                _PROCESS_GENERATION = object()
                _PROCESS_PERMIT.events = ()
                _PROCESS_PERMIT.latch = None
                _PROCESS_WINDOWS = 0
                _PROCESS_BLOCKS = None
                _PROCESS_WINDOWS_CLOSED.set()
        if remaining:
            errors.append(f'TUI process allow windows still active at teardown: {remaining}')
        if blocked:
            errors.append(f'TUI test left unconsumed process blocks: {blocked!r}')
        if errors:
            pytest.fail('TUI process isolation cleanup failed: ' + '; '.join(errors))


@pytest.fixture(autouse=True)
def _restore_anim_globals():
    """Keep every test hermetic against cosmetic pollution.

    Mounting the real FleetTUI (app.run_test()) runs on_mount, which loads the owner's cosmetics config
    and calls anim.set_colors()/set_style() — MUTATING the module-global palette (anim._colors, e.g.
    model->magenta) and spinner (anim._active_frames). Those globals feed widgets/format.py, so an
    app-mounting test that happens to sort BEFORE test_format would leak the custom palette and break
    format assertions that expect the defaults (this actually bit us when test_dispatch_presets was added).
    Snapshot + restore around each test so order never matters.
    """
    saved_colors = copy.deepcopy(anim._colors)
    saved_frames = list(anim._active_frames)
    saved_glow = anim._glow_on
    try:
        yield
    finally:
        anim._colors = copy.deepcopy(saved_colors)
        anim._active_frames = list(saved_frames)
        anim._glow_on = saved_glow


@pytest.fixture(autouse=True)
def _isolate_app_refresh(monkeypatch, request):
    """A mounted Textual app must not start live HTTP/subprocess reads during a test.

    Source tests call their readers directly; app tests need only a stable raw snapshot.  Patching the
    module attribute leaves a test's directly imported ``gather_data`` function available for its own
    explicit source seams while making every `run_test()` mount hermetic and quick to tear down.
    """
    # Headless source tests must not require the optional UI dependency.
    import importlib.util
    if importlib.util.find_spec("textual") is None:
        return
    import fleet_tui.app as app
    snapshot = {
        "jobs": [], "health": HealthSnapshot(), "models": [], "focus": FocusState(), "inbox": [],
        "dispatches": [], "util": 0, "network": {}, "alerts": [], "ops": [], "cloud": [],
        "posture": {}, "passback": [], "research_playlists": [], "boxes": [FleetBox()],
        "models_by_box": {"local": []}, "receipts": [], "throughput": {"local": {}},
        "lanes": [], "downloads": [], "bg_agents": [],
    }
    monkeypatch.setattr(app, "gather_data", lambda: snapshot)
    # The PTY is integration-tested in test_terminal.py.  Every other app test renders the pane hidden,
    # so for those tests avoid spawning a real shell that can outlive a test runner shutdown.
    if request.node.fspath.basename != "test_terminal.py":
        from fleet_tui.widgets.terminal import TerminalPane

        async def _no_terminal_process(self):
            return None

        monkeypatch.setattr(TerminalPane, "on_mount", _no_terminal_process)


@pytest.fixture(autouse=True)
def _isolate_inbox_triggers(monkeypatch):
    """Every inbox trigger constant points at a LIVE path on the host; a test that patches only
    some of them inherits the machine's real alert state (a live .automation_alert made
    test_list_inbox_integration order-dependent — the default-LIVE-path class, third sighting).
    Point them ALL at nonexistent paths; tests that want a trigger patch it explicitly."""
    from fleet_tui.sources import inbox as I
    for name in ("AUTOMATION_ALERT", "BACKUP_ALERT", "SUPPLY_ALERT", "DEP_TRIGGER",
                 "CURATION_TRIGGER", "GITHUB_ALERT", "HIVE_ALERT", "REJECTS",
                 "HF_DIGEST", "TELEGRAM_TRIGGER"):
        if hasattr(I, name):
            monkeypatch.setattr(I, name, "/nonexistent/" + name.lower(), raising=False)
