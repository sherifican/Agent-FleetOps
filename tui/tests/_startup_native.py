"""Post-subject native snapshot at -p plugin import, before later pytest hooks."""
import sys
import types

_SUBJECT_IMPORTED = 'tests._isolation' in sys.modules
_READLINE_LOADED = 'readline' in sys.modules


def _snapshot():
    import ctypes
    import hashlib
    if not sys.platform.startswith('linux'):
        raise RuntimeError('native environment instrument requires Linux libc environ')
    address = ctypes.c_void_p.in_dll(ctypes.CDLL(None), 'environ').value
    vector = ctypes.cast(address, ctypes.POINTER(ctypes.c_char_p))
    result = {}
    digests = {}
    index = 0
    while vector[index] is not None:
        entry = vector[index].decode(sys.getfilesystemencoding(), 'surrogateescape')
        name, separator, value = entry.partition('=')
        if not separator or name in result:
            raise RuntimeError('native environment malformed or duplicate name: ' + name)
        if value not in digests:
            digests[value] = (len(value), hashlib.sha256(
                value.encode('utf-8', 'surrogateescape')).hexdigest())
        length, digest = digests[value]
        result[name] = {'length': length, 'sha256': digest}
        index += 1
    return result


# A narrowly targeted injected resolver is restored after subject import.
if hasattr(sys, '_startup_restore_resolver'):
    sys._startup_restore_resolver()


def _source_description(value):
    kind = type(value)
    tag = [kind.__module__, kind.__qualname__]
    reference = getattr(sys, '_startup_location_string_type', None)
    if reference is not None and kind is reference:
        tag = ['controlled', 'StartupLocationString']
        stored = {'str_subclass': True, 'text': str.__str__(value)}
    elif any(kind is canonical for canonical in (type(None), str, int)):
        stored = {'type': tag, 'value': value}
    elif kind is bytes:
        stored = {'type': tag, 'hex': value.hex()}
    else:
        stored = {'unsupported': True}
    return {'type': tag, 'exact_str': kind is str, 'none': value is None, 'value': stored}


_EARLY_ENV = _snapshot()
REPORT = {'subject_imported': _SUBJECT_IMPORTED,
          'subject_type_exact': type(sys.modules.get('tests._isolation')) is types.ModuleType,
          'os_environ_exact': type(__import__('os').environ) is __import__('os')._Environ,
          'subject_finished': type(sys.modules.get('tests._isolation')) is types.ModuleType and not sys.modules['tests._isolation'].__spec__._initializing,
          'readline_loaded': 'readline' in sys.modules,
          'env': _EARLY_ENV, 'duplicates_rejected': True,
          'location_sources': [_source_description(getattr(sys, '_stdlib_dir', None)),
                               _source_description(getattr(__import__('os'), '__file__', None))],
          'resolution_events': getattr(sys, '_startup_resolution_events', []),
          'probe_root': next((arg.partition('=')[2] for arg in sys.argv
                              if arg.startswith('--startup-probe-root=')), None)}


def pytest_addoption(parser):
    parser.addoption('--startup-probe-root', help='Independent absent root for startup probes')
