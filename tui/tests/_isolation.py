"""Early pytest plugin: isolate settings before pytest imports readline.

Loaded by pyproject addopts before initial conftests. Conftest also imports it
so explicit root-level collection keeps Fleet TUI imports isolated.
"""
import os
from pathlib import Path
import sys
import tempfile


# Fixtures run after collection imports. Redirect the process environment first
# so every current and future import-time settings constant starts in scratch.
# Refuse an already-imported package rather than silently retaining live paths.
assert not any(name == "fleet_tui" or name.startswith("fleet_tui.") for name in sys.modules), (
    "fleet_tui imported before the TUI test HOME/XDG isolation"
)
# The user roots: the resolved HOME plus the resolved absolute entries of the
# XDG variables that are set. A relative entry is never a root, although
# fleet_tui/paths.py (_read_config) honours a relative XDG_CONFIG_HOME,
# resolved against the working directory; that directory counts only when it
# lies under a root. A per-user default directory not named by an absolute
# set variable (its variable unset, relative or set to another directory, e.g.
# ~/.config, and always ~/.local/bin, which has no XDG variable) is covered
# only through HOME: if it resolves outside HOME, itself or through a
# symlinked parent such as ~/.local, it is not a root, and paths under it are
# not reported.
_ORIGINAL_USER_ROOTS = tuple(dict.fromkeys(
    [Path.home().resolve()] + [
        Path(value).resolve()
        for key in ("XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME",
                    "XDG_STATE_HOME", "XDG_RUNTIME_DIR", "XDG_CONFIG_DIRS", "XDG_DATA_DIRS")
        for value in os.environ.get(key, "").split(os.pathsep)
        if value and os.path.isabs(value)
    ]
))
# The locations site.py derived the license helper's files from, taken before
# fleet_tui can rebind them.
_ORIGINAL_INTERPRETER_LOCATIONS = (
    ("sys._stdlib_dir", getattr(sys, "_stdlib_dir", None)),
    ("os.__file__", getattr(os, "__file__", None)),
)


# test_home_isolation.py pins the code of this whole module (its normalised
# AST) and the gate's run-time binding, because no finite input corpus can
# lock the gate below. A change here that changes the parsed code, docstrings
# included, fails that pin on purpose; comments, blank lines and spellings
# that parse the same (quote style, redundant parentheses) do not. The comment
# above the pin says how to update it.
def _exact_location(value, label, refused):
    """Return `value` only if it is an exact absolute str.

    Anything else is recorded in `refused` as an explicit finding and not
    used. The type test comes first, so only an exact str reaches
    os.path.isabs, which calls os.fspath and then str.startswith on it. No
    method of any other value is called: a str subclass could override the
    str methods os.path and pathlib call, and a non-str could run
    __fspath__. Only `label`, our own text, is formatted.
    """
    if type(value) is str and os.path.isabs(value):
        return value
    refused.append(f'{label} = <uninspectable exemption source>')
    return None


def _license_candidate_files(locations):
    """The license helper's absolute candidate files, resolved.

    Returns (files, refused findings). site.py builds the `license` helper
    from sys._stdlib_dir, or from the directory of os.__file__ when that is
    unset. Here LICENSE.txt and LICENSE are joined only to that directory's
    parent and the directory itself, then resolved. site.py also joins
    os.curdir; those relative candidates are omitted here because the scan's
    absolute-path gate skips them, so they are not exempted. The result is
    exact files, never a directory.
    """
    (stdlib_label, stdlib), (os_file_label, os_file) = locations
    refused = []
    if stdlib is None or (type(stdlib) is str and stdlib == ''):
        if os_file is None:
            return (), ()
        here = _exact_location(os_file, os_file_label, refused)
        label = os_file_label
        if here is not None:
            here = os.path.dirname(here)
    else:
        here = _exact_location(stdlib, stdlib_label, refused)
        label = stdlib_label
    if here is None:
        return (), tuple(refused)
    try:
        files = [Path(os.path.join(directory, name)).resolve()
                 for directory in (os.path.join(here, os.pardir), here)
                 for name in ('LICENSE.txt', 'LICENSE')]
    except (ValueError, OSError, RuntimeError):
        return (), (f'{label} = <uninspectable exemption source>',)
    return tuple(dict.fromkeys(files)), ()


# The settings scan's default license exemption: the four candidate files,
# joined and resolved here, before fleet_tui is imported, so neither the two
# locations nor os.path.join, os.pardir, os.path.dirname or Path.resolve as
# rebound by package import code can move it.
_ORIGINAL_LICENSE_FILES = _license_candidate_files(_ORIGINAL_INTERPRETER_LOCATIONS)
def _process_identity(pid, proc_root='/proc'):
    """Return Linux start ticks and boot ID; only an absent PID means dead."""
    proc = Path(proc_root)
    boot = (proc / 'sys/kernel/random/boot_id').read_text().strip()
    try:
        stat = (proc / str(pid) / 'stat').read_text()
    except FileNotFoundError:
        if (proc / str(pid)).exists():
            raise
        return None, boot
    # comm may contain spaces or ')'; the fields after its last ')' are stable.
    ticks = int(stat.rsplit(')', 1)[1].split()[19])
    if ticks <= 0:
        raise ValueError('invalid process start ticks')
    return ticks, boot


def _test_path_identity(info):
    """Compare replacement/modification fields; reads may update only atime."""
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _retirement_address(root):
    """Address retention without reading a possibly damaged owner record."""
    import hashlib
    return '\0fleet-tui-retire-' + hashlib.sha256(os.fsencode(str(root))).hexdigest()


def _retirement_pidfd_open(pid):
    """Use the kernel pidfd facility even on Python builds without its wrapper."""
    native = getattr(os, 'pidfd_open', None)
    if native is not None:
        return native(pid)
    import ctypes
    call = ctypes.CDLL(None, use_errno=True).pidfd_open
    call.argtypes = (ctypes.c_int, ctypes.c_uint)
    call.restype = ctypes.c_int
    result = call(pid, 0)
    if result < 0:
        number = ctypes.get_errno()
        raise OSError(number, os.strerror(number))
    return result


def _retirement_pidfd_signal(descriptor, number):
    """Signal only the pidfd's incarnation through Python or host libc."""
    import signal
    native = getattr(signal, 'pidfd_send_signal', None)
    if native is not None:
        return native(descriptor, number)
    import ctypes
    call = ctypes.CDLL(None, use_errno=True).pidfd_send_signal
    call.argtypes = (ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint)
    call.restype = ctypes.c_int
    result = call(descriptor, number, None, 0)
    if result < 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def _stop_retirement_guard(child, descriptor, pidfd=None):
    """Cancel through the private socket; observe peer channel closure.

    The cooperating helper consumes R and exits, including a sole double-fork
    descendant. A helper that fails first can exit with R unread; releasing
    its last endpoint then reports ECONNRESET once, followed by EOF. Closure
    is that EOF, read directly or after the reset; any other error or data
    propagates. EOF proves channel closure, not arbitrary holder process death.
    A stale/reaped direct-child PID is never used for signalling.
    """
    import posix
    import select
    try:
        if posix.write(descriptor, b'R') != 1:
            raise RuntimeError('short retirement cancellation write')
    except (BrokenPipeError, ConnectionResetError):
        pass  # The peer released its last endpoint; the EOF read below must confirm it.
    ready, _, _ = select.select([descriptor], [], [], 10)
    if not ready:
        raise RuntimeError('retirement cancellation did not observe peer exit')
    try:
        received = posix.read(descriptor, 1)
    except ConnectionResetError:
        # The reset and the peer's shutdown are recorded together, so EOF is
        # already readable; never wait for it.
        if not select.select([descriptor], [], [], 0)[0]:
            raise
        received = posix.read(descriptor, 1)
    if received != b'':
        raise RuntimeError('retirement cancellation did not observe peer exit')
    if pidfd is not None:
        try:
            os.waitid(os.P_PIDFD, pidfd, os.WEXITED)
        except ChildProcessError:
            pass  # Another reaper owns this exact incarnation's reap.
    else:
        try:
            os.waitpid(child, 0)
        except ChildProcessError:
            pass


def _start_retirement_guard(root, owner_pid, owner_ticks, proc_root='/proc'):
    """Preallocate a one-use, memory-only retirement capability before payload.

    Revoke through its private cancellation socket and observe channel closure.
    A granted or lost helper cannot grant any later sweep. No retention
    decision requires a filesystem write, including a failed marker write.
    """
    import secrets
    import select
    import socket
    import struct
    cookie = secrets.token_hex(16)
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(_retirement_address(root))
    command_peer, command_owner = socket.socketpair()
    read_fd, write_fd = command_peer.detach(), command_owner.detach()
    ready_read, ready_write = os.pipe()
    child = None
    try:
        child = os.fork()
        if child == 0:
            try:
                os.close(write_fd)
                os.close(ready_read)
                # Redirect standard streams; unrelated inherited fds are not closed.
                null_fd = os.open('/dev/null', os.O_RDWR)
                for stream_fd in (0, 1, 2):
                    os.dup2(null_fd, stream_fd)
                if null_fd > 2:
                    os.close(null_fd)
                os.setsid()
                # SO_PEERCRED records the listening process, so listen in the
                # helper, after fork, before announcing readiness to its owner.
                listener.listen(1)
                # /proc/self always denotes this child, even when numeric procfs
                # entries belong to an ancestor PID namespace.
                child_ticks, _ = _process_identity('self')
                ready = f'A {child_ticks}\n'.encode('ascii')
                if os.write(ready_write, ready) != len(ready):
                    raise RuntimeError('short helper readiness pipe write')
                os.close(ready_write)
                while Path(root).is_dir():
                    readable, _, _ = select.select([listener] + ([] if read_fd is None else [read_fd]), [], [], 1)
                    if read_fd is not None and read_fd in readable:
                        command = os.read(read_fd, 1)
                        if command:
                            return_value = 0
                            break
                        # Owner exit closes the pipe. A SIGKILL permits a later
                        # request, but only after its recorded incarnation is dead.
                        os.close(read_fd)
                        read_fd = None
                    if listener in readable:
                        connection, _ = listener.accept()
                        with connection:
                            connection.settimeout(2)
                            peer = struct.unpack('3i', connection.getsockopt(
                                socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize('3i')))
                            request = connection.recv(8192)
                            if peer[1] == os.getuid() and request[:1] == b'R':
                                connection.sendall(b'RETAINED')
                                break
                            ticks, _ = _process_identity(owner_pid, proc_root)
                            # The pipe is drained before judging any request. A
                            # retained owner sent R before exiting, even if EOF and
                            # the request become readable together.
                            if read_fd is not None:
                                pending, _, _ = select.select([read_fd], [], [], 0)
                                if pending and os.read(read_fd, 1):
                                    break
                            if (peer[1] != os.getuid() or ticks == owner_ticks or
                                    request != os.fsencode(str(root)) + b'\0' + cookie.encode('ascii')):
                                continue
                            # Exit after this request even when send fails. The
                            # capability is consumed BEFORE the caller can remove.
                            connection.sendall(b'GRANT')
                            # Stay this live incarnation until the requester has read
                            # the reply and closed, so its pidfd check can bind it.
                            connection.recv(1)
                            break
                else:
                    return_value = 0
            except BaseException:
                return_value = 1
            finally:
                os._exit(locals().get('return_value', 0))
        listener.close()
        os.close(read_fd)
        read_fd = None
        os.close(ready_write)
        ready_write = None
        ready = os.read(ready_read, 128).decode('ascii').split()
        if len(ready) != 2 or ready[0] != 'A':
            raise RuntimeError('retirement helper failed before readiness')
        child_ticks = int(ready[1])
        if child_ticks <= 0:
            raise RuntimeError('retirement helper identity missing')
        pidfd = _retirement_pidfd_open(child)
        os.close(ready_read)
        ready_read = None
        return child, child_ticks, cookie, write_fd, pidfd
    except BaseException:
        # Nothing after fork may escape revocation, including closes/read/parse,
        # pidfd acquisition, and the final close before control publication.
        import posix
        # Release the parent's duplicate peer before waiting for channel EOF.
        for descriptor in (read_fd, ready_read, ready_write):
            if descriptor is not None:
                try:
                    posix.close(descriptor)
                except OSError as error:
                    if error.errno != 9:
                        raise
        if child is not None:
            _stop_retirement_guard(child, write_fd, locals().get('pidfd'))
        for descriptor in (write_fd, locals().get('pidfd')):
            if descriptor is not None:
                try:
                    posix.close(descriptor)
                except OSError as error:
                    if error.errno != 9:
                        raise
        raise


def _request_retirement(root, fields):
    """Accept only a one-use reply sent by the recorded live helper incarnation.

    SO_PEERCRED names the process that called listen(), which also answers for
    any holder of an inherited listener. The reply's own SCM_CREDENTIALS name
    its sender, and a pidfd bound before the start-ticks check that is still
    unexited after the reply proves that PID was this incarnation throughout.
    """
    import select
    import socket
    import struct
    guardian, ticks = int(fields[5]), int(fields[6])
    pidfd = _retirement_pidfd_open(guardian)
    try:
        # Read independently of the owner-identity seam; the start ticks
        # disambiguate PID reuse and bind the pidfd to this incarnation.
        actual = (Path('/proc') / str(guardian) / 'stat').read_text()
        if int(actual.rsplit(')', 1)[1].split()[19]) != ticks:
            raise ValueError('retirement helper incarnation changed')
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(2)
            connection.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
            connection.connect(_retirement_address(root))
            peer = struct.unpack('3i', connection.getsockopt(
                socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize('3i')))
            if peer[:2] != (guardian, os.getuid()):
                raise ValueError('retirement helper peer identity differs')
            connection.sendall(os.fsencode(str(root)) + b'\0' + fields[7].encode('ascii'))
            reply, ancillary, _, _ = connection.recvmsg(6, socket.CMSG_SPACE(struct.calcsize('3i')))
            if reply != b'GRANT':
                raise ValueError('retirement permission refused')
            senders = [struct.unpack('3i', data[:struct.calcsize('3i')])[:2]
                       for level, kind, data in ancillary
                       if level == socket.SOL_SOCKET and kind == socket.SCM_CREDENTIALS]
            if senders != [(guardian, os.getuid())]:
                raise ValueError('retirement grant not sent by the recorded helper')
            if select.select([pidfd], [], [], 0)[0]:
                raise ValueError('retirement helper exited before its grant was bound')
    finally:
        os.close(pidfd)


def _veto_retirement(root):
    """Revoke on a sweep's storage error, without reading/writing that tree."""
    import socket
    import struct
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(2)
            connection.connect(_retirement_address(root))
            peer = struct.unpack('3i', connection.getsockopt(
                socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize('3i')))
            if peer[1] != os.getuid():
                raise ValueError('retention helper UID differs')
            connection.sendall(b'R')
            if connection.recv(9) != b'RETAINED':
                raise ValueError('retention veto not acknowledged')
    except (ConnectionRefusedError, ConnectionResetError, BrokenPipeError):
        pass  # The issuer is absent or has already consumed/exited its channel.


def _retain_test_directory():
    """Cancel this private helper channel; never write on a damaged filesystem."""
    import posix
    import signal
    global _RETIREMENT_CONTROL
    if os.getpid() != _TEST_OWNER_PID:
        return  # An inherited callback must not revoke its living parent's helper.
    child, ticks, cookie, descriptor, pidfd = _RETIREMENT_CONTROL
    if descriptor is None:
        return
    _stop_retirement_guard(child, descriptor, pidfd)
    _RETIREMENT_CONTROL = child, ticks, cookie, None, None
    posix.close(descriptor)
    posix.close(pidfd)


def _cleanup_test_directory(name, warn_message, ignore_errors=False, **kwargs):
    """Revoke before ordinary cleanup too: a failed cleanup cannot be retried."""
    import select
    if os.getpid() != _TEST_OWNER_PID:
        print('TUI scratch retained:', name, '- forked process does not own this tree', file=sys.stderr)
        return
    pidfd = _RETIREMENT_CONTROL[4]
    if pidfd is None or select.select([pidfd], [], [], 0)[0]:
        print('TUI scratch retained:', name, '- retirement helper already revoked or lost', file=sys.stderr)
        _retain_test_directory()
        return
    _retain_test_directory()
    tempfile.TemporaryDirectory._cleanup(name, warn_message, ignore_errors, **kwargs)


def _retire_test_siblings(directory='/tmp', proc_root='/proc'):
    """Judge only namespace-qualified owners in this process's procfs PID view.

    Legacy or foreign-namespace records are retained, including dead runs.
    A procfs mounted in an ancestor namespace is not a local PID oracle.
    """
    import re
    import shutil
    import stat
    import uuid

    for sibling in Path(directory).glob('fleet-tui-tests-*'):
        try:
            info = sibling.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
                raise ValueError('not an owned directory')
            if info.st_mode & stat.S_ISVTX:
                raise ValueError('intentionally retained initialization evidence')
            owner = sibling / '.owner'
            owner_info = owner.lstat()
            if not stat.S_ISREG(owner_info.st_mode) or owner_info.st_uid != os.getuid():
                raise ValueError('not an owned regular owner file')
            raw = owner.read_text()
            fields = raw.split()
            if (len(fields) != 9 or fields[8] != 'v3' or
                    not re.fullmatch(r'[0-9a-f]{32}', fields[7]) or
                    any(not re.fullmatch(r'[1-9][0-9]*', fields[i]) for i in (0, 1, 3, 4, 5, 6))):
                raise ValueError('unparsable owner')
            pid, ticks = int(fields[0]), int(fields[1])
            boot = str(uuid.UUID(fields[2]))
            if boot != fields[2]:
                raise ValueError('noncanonical boot ID')
            proc = Path(proc_root)
            namespace = (proc / 'self/ns/pid').stat()
            if (int(fields[3]), int(fields[4])) != (namespace.st_dev, namespace.st_ino):
                raise ValueError('foreign PID namespace')
            # NStgid starts in the procfs mounter's namespace. Exactly one
            # value establishes that numeric entries use our namespace,
            # even if ancestor and local PIDs happen to have equal numbers.
            status = (proc / 'self/status').read_text().splitlines()
            ids = [line.split()[1:] for line in status if line.startswith('NStgid:')]
            if ids != [[str(os.getpid())]]:
                raise ValueError('procfs PID view is not local')
            actual_ticks, actual_boot = _process_identity(pid, proc_root)
            if boot != actual_boot:
                raise ValueError('different boot ID; identity not judged')
            dead = actual_ticks is None or ticks != actual_ticks
            # A replacement or modified owner invalidates the decision.
            if not dead:
                raise ValueError('owner still live')
            if (owner.read_text() != raw or
                    _test_path_identity(sibling.lstat()) != _test_path_identity(info) or
                    _test_path_identity(owner.lstat()) != _test_path_identity(owner_info)):
                raise ValueError('directory or owner changed before removal')
            _request_retirement(sibling, fields)
            if (owner.read_text() != raw or
                    _test_path_identity(sibling.lstat()) != _test_path_identity(info) or
                    _test_path_identity(owner.lstat()) != _test_path_identity(owner_info)):
                raise ValueError('directory or owner changed before removal')
            shutil.rmtree(sibling)
        except (OSError, ValueError, IndexError) as error:
            print('TUI scratch retained:', sibling, '-', str(error), file=sys.stderr)
            if isinstance(error, OSError) and error.errno in (5, 28, 30):
                _veto_retirement(sibling)
                raise
    for evidence in Path(directory).glob('fleet-tui-startup-*'):
        print('TUI scratch retained:', evidence, '- persistent startup evidence', file=sys.stderr)
    for parent in Path(directory).glob('pytest-of-*'):
        for evidence in parent.glob('fleet-tui-startup-*'):
            print('TUI scratch retained:', evidence, '- persistent startup evidence', file=sys.stderr)


def _write_test_owner(root, proc_root='/proc', control=None):
    """Create the first payload exclusively at mode 0600; retain partial writes."""
    # self resolves to the caller in any procfs view; getpid may instead name
    # an unrelated process when this procfs was mounted by an ancestor.
    ticks, boot = _process_identity('self', proc_root)
    if ticks is None:
        raise RuntimeError('current process identity missing')
    namespace = (Path(proc_root) / 'self/ns/pid').stat()
    child, child_ticks, cookie, _, _ = control or _RETIREMENT_CONTROL
    payload = (f'{os.getpid()} {ticks} {boot} {namespace.st_dev} {namespace.st_ino} '
               f'{child} {child_ticks} {cookie} v3\n').encode('ascii')
    descriptor = os.open(Path(root) / '.owner', os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        if os.write(descriptor, payload) != len(payload):
            raise OSError(5, 'short owner write', str(Path(root) / '.owner'))
    finally:
        os.close(descriptor)


def pytest_addoption(parser):
    parser.addoption('--keep-startup-artifacts', action='store_true',
                     help='Keep startup inputs and receipts for evidence review')
    parser.addoption('--startup-artifacts-root',
                     help='Kept output directory from fleet-data-path test-scratch-kept <run>')

# Capture helper discovery before the test PATH is reset; keep no environment copy.
sys._fleet_startup_artifact_helper = __import__('shutil').which('fleet-data-path')
_retire_test_siblings()
# Keep the owner alive for the process lifetime (including pytest teardown).
# /tmp avoids an inherited TMPDIR placing the sandbox inside the real HOME.
_TEST_OWNER_PID = os.getpid()
_TEST_DIRECTORY = tempfile.TemporaryDirectory(prefix="fleet-tui-tests-", dir="/tmp")
try:
    # Arm retention BEFORE the owner can become valid or any payload is made.
    # If this chmod fails, no owner exists and retirement already refuses it.
    Path(_TEST_DIRECTORY.name).chmod(0o1700)
    _RETIREMENT_CONTROL = _start_retirement_guard(
        _TEST_DIRECTORY.name, os.getpid(), _process_identity('self')[0])
    # Replace only this owner's callback, retaining TemporaryDirectory's own
    # warning and cleanup arguments. No shared class or service is changed.
    _finalizer_parts = _TEST_DIRECTORY._finalizer.detach()
    _TEST_DIRECTORY._finalizer = __import__('weakref').finalize(
        _TEST_DIRECTORY, _cleanup_test_directory, *_finalizer_parts[2], **_finalizer_parts[3])
    del _finalizer_parts
    _write_test_owner(_TEST_DIRECTORY.name)
except BaseException:
    # Preserve a partial owner payload on any identity/write failure.
    _TEST_DIRECTORY._finalizer.detach()
    if '_RETIREMENT_CONTROL' in globals():
        _retain_test_directory()
    raise
_TEST_ROOT = Path(_TEST_DIRECTORY.name)
_IMPORT_ENV = {}
try:
    for _key, _subdir in {
        "HOME": "home",
        "XDG_CONFIG_HOME": "config",
        "XDG_CACHE_HOME": "cache",
        "XDG_DATA_HOME": "data",
        "XDG_STATE_HOME": "state",
        "XDG_RUNTIME_DIR": "runtime",
        "XDG_CONFIG_DIRS": "config-dirs",
        "XDG_DATA_DIRS": "data-dirs",
    }.items():
        _directory = _TEST_ROOT / _subdir
        _directory.mkdir(mode=0o700)
        os.environ[_key] = str(_directory)
        _IMPORT_ENV[_key] = os.environ[_key]
    # Only a successfully initialized sandbox is eligible for dead-owner cleanup.
    _TEST_ROOT.chmod(0o700)

except BaseException:
    _TEST_DIRECTORY._finalizer.detach()
    _retain_test_directory()
    raise

# Environment overrides can point past HOME/XDG into live fleet state. Tests
# that need configuration must supply it explicitly, just like settings files.
for _key in tuple(os.environ):
    if _key.startswith("FLEET_"):
        del os.environ[_key]


# User-installed helpers can ignore HOME and read their installation's live
# settings (e.g. fleet-doctor / hermes). Keep them off PATH in source tests.
# The real PTY test still launches a system shell against the scratch HOME.
os.environ["PATH"] = os.defpath
os.environ["SHELL"] = "/bin/bash"
for _key in ("BASH_ENV", "ENV", "ZDOTDIR", "HISTFILE", "INPUTRC"):
    os.environ.pop(_key, None)


def _dispatch_fixture(args, scratch):
    """Allow only the tests' true + touch script, with every file in tmp_path.

    Allowing a shell name alone would permit arbitrary commands. Reconstruct
    the exact fixture grammar instead, including quoting and suffixes.
    """
    import shlex

    if not isinstance(args, (tuple, list)) or len(args) != 4:
        return False
    if list(args[:3]) != ['setsid', 'sh', '-c'] or not isinstance(args[3], str):
        return False
    try:
        words = shlex.split(args[3])
    except ValueError:
        return False
    if len(words) != 8 or words[0] != 'true':
        return False
    brief, out, log, done = (words[i] for i in (1, 2, 4, 7))
    base = brief.removesuffix('.brief')
    if [brief, out, log, done] != [base + suffix for suffix in ('.brief', '.out', '.log', '.done')]:
        return False
    if not all(Path(path).resolve().is_relative_to(scratch.resolve())
               for path in (brief, out, log, done)):
        return False
    expected = (f'true {shlex.quote(brief)} {shlex.quote(out)} '
                f'> {shlex.quote(log)} 2>&1; touch {shlex.quote(done)}')
    return args[3] == expected
