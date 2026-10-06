"""Derived checks for settings paths, including modules not otherwise tested."""
import ast
import dis
import importlib
import importlib.machinery
import importlib.util
import inspect
from gc import get_referents
from collections import deque, defaultdict
from array import array, typecodes
from functools import partial, lru_cache, wraps
import hashlib
from itertools import product
import ntpath
import os
import sys
import unicodedata
import warnings
from pathlib import Path, PurePath, PurePosixPath, PureWindowsPath, PosixPath, WindowsPath
import pkgutil
import re
import string
from types import (GeneratorType, FunctionType, ModuleType, MappingProxyType, CellType,
                   GetSetDescriptorType, MemberDescriptorType, CodeType, BuiltinFunctionType)

import fleet_tui
import pytest

from . import _isolation, conftest


_FROM_SYS = object()
_FROM_SNAPSHOT = object()


# One exact-str rule for every exemption location; see _isolation.
_exact_location = _isolation._exact_location


def _source_metadata_dirs(package=fleet_tui, pycache_prefix=_FROM_SYS):
    """Directories holding the package's own source and its bytecode.

    Returns (directories, refused findings). Computed from the package's
    exact-str __file__ (read from its namespace by exact name) and
    sys.pycache_prefix, never from values read off scanned objects. With an
    absolute prefix, bytecode lives in a mirror of the absolute source
    directory below it (both the imported and the resolved spelling, since the
    interpreter builds it from the former). A relative prefix gives relative
    cache paths, which the detector never resolves, so it needs no mirror.
    """
    refused = []
    if pycache_prefix is _FROM_SYS:
        pycache_prefix = sys.pycache_prefix
    namespace = ModuleType.__dict__['__dict__'].__get__(package)
    package_file = _exact_location(
        next((child for name, child in dict.items(namespace)
              if type(name) is str and name == '__file__'), None),
        'package.__file__', refused)
    if package_file is None:
        return (), tuple(refused)
    spellings = dict.fromkeys([Path(os.path.dirname(package_file)),
                               Path(package_file).resolve().parent])
    dirs = [spelling.resolve() for spelling in spellings]
    if pycache_prefix is None or (type(pycache_prefix) is str
                                  and not os.path.isabs(pycache_prefix)):
        pass
    else:
        prefix = _exact_location(pycache_prefix, 'sys.pycache_prefix', refused)
        if prefix is not None:
            base = Path(prefix).resolve()
            dirs.extend((base / spelling.relative_to(spelling.anchor)).resolve()
                        for spelling in spellings)
    return tuple(dict.fromkeys(dirs)), tuple(refused)


def _license_files(locations=_FROM_SNAPSHOT):
    """The license helper's absolute candidate files, resolved.

    Returns (files, refused findings). By default this is the result the
    pre-import snapshot in tests/_isolation.py computed before fleet_tui was
    imported: the two locations site.py uses (sys._stdlib_dir, else the
    directory of os.__file__), joined with os.path.join and os.pardir and
    resolved with Path.resolve at that point. Package import code therefore
    cannot move the default by rebinding sys._stdlib_dir, os.__file__,
    os.pardir, os.path.join/dirname or Path.resolve. With
    explicit `locations` the same joins run now, for injected tests. The
    os.curdir candidates are relative: the scan's absolute-path gate skips
    them, so they are not exempted. The result is exact files, never a
    directory. Refused (non-exact-str or relative) locations give no files
    and an explicit uninspectable finding.
    """
    if locations is _FROM_SNAPSHOT:
        return _isolation._ORIGINAL_LICENSE_FILES
    return _isolation._license_candidate_files(locations)


# Taken once, before any scan: the source directories from the package
# location now, the license files as the pre-import snapshot computed them.
_SOURCE_METADATA_DIRS, _SOURCE_REFUSALS = _source_metadata_dirs()
_LICENSE_FILES, _LICENSE_REFUSALS = _license_files()
_EXEMPTION_REFUSALS = _SOURCE_REFUSALS + _LICENSE_REFUSALS


def _package_modules():
    modules = [fleet_tui]
    for info in pkgutil.walk_packages(fleet_tui.__path__, fleet_tui.__name__ + '.'):
        modules.append(importlib.import_module(info.name))
    return modules


def _settings_paths_in_roots(modules, roots, exempt=_SOURCE_METADATA_DIRS,
                             exempt_files=_LICENSE_FILES,
                             refused=_EXEMPTION_REFUSALS):
    """Inspect stored settings through eight edges without evaluating accessors.

    Include wrappers, closures, annotations, partials, properties and stored
    instance attributes. Never evaluate user descriptors, annotation expressions,
    function globals or imported module namespaces. Ancestor IDs break cycles.
    Exact stdlib paths are reconstructed from exact-str components read through
    PurePath's storage slots, using the matching pure flavour (the host's for
    exact PurePath/Path), whose constructor never refuses the host OS; invalid
    storage is explicitly uninspectable. The other OS's concrete class
    (WindowsPath on POSIX, PosixPath on Windows) is reported uninspectable
    without construction. Only absolute decoded paths are resolved: a
    non-absolute one (e.g. a Windows drive path on POSIX) gives no finding.
    str/bytes subclasses are decoded through base slots; arbitrary __fspath__
    and string subclass overrides are never called. Inspect byte buffers,
    byte/Unicode arrays and defaultdict factories without invoking factories.
    On CPython 3.11/3.12/3.13/3.14, inspect generator GC-visible stored references
    (fast locals and cells, the 3.11/3.12 locals dict, evaluation stack
    including a yield-from target, saved exception instance, names, function
    storage) plus the generator frame object's own GC referents (trace
    function; on 3.14, the extra-locals dict, legacy locals snapshot and
    retained overwritten fast-local values), with numeric labels. Extra-local
    detection is covered on 3.13 as well; its storage owner is not established
    by a passing control. GC referents can
    report the same value more than once. Never read f_locals: on 3.11/3.12
    that synchronizes a dict using potentially hostile keys, and on 3.13/3.14
    it returns a proxy. On 3.13, PEP 667 writes through to fast locals; an
    overwritten value has no retained storage to inspect. The generator's own
    frame and code are excluded by identity; code/globals and future yields
    are not traversed. This relies on
    CPython's GC traversal, not a portable named-locals API. Other interpreter
    implementations/versions report uninspectable generator state.
    Coroutine and async-generator frame locals are
    not inspected. Numeric arrays and paths computed only by user code remain
    outside scope, as do values beyond eight edges. While inspecting an instance,
    class-level attributes with exact str names are not traversed, except builtin
    storage descriptors. Non-str class entries are flagged and their values read.
    A stored class itself (including a str/bytes subclass) has its body traversed
    only when its __module__ is exact str equal to the inspected module name.
    A non-None non-exact-str class __module__ is uninspectable; its body is not
    read. An absent/None owner also skips the body, without a finding. Non-exact
    instance dictionaries are reported as uninspectable without calling their overrides. Builtin
    storage descriptors are read across the MRO, bypassing shadowing properties
    or other class attributes. Refusing descriptors or dictionary storage with
    no usable descriptor produce explicit uninspectable findings. Path
    subclasses are not decoded; on 3.11 their stored path can be missed.
    Stored names are compared or formatted only when their type is exactly str.
    Iterate namespace items instead of looking up names against unknown keys.
    Non-str keys get numbered labels and explicit uninspectable findings; their
    values are still inspected (including non-str class entries), without
    hashing, comparing, formatting or converting the keys themselves.
    General mapping keys are values under numeric labels, not attribute names.
    Read wrappers and containers through builtin slots; mapping proxies require
    GC-visible dict backing storage, otherwise report them as uninspectable.
    Named annotation limit on Python 3.14: when the builtin __annotate__ slot is
    non-None, neither lazy nor already realized __annotations__ are read. The
    public annotation descriptor may execute that callable; no public raw slot
    distinguishes the cached dictionary. Function __dict__ is still inspected.
    Named source-directory limit: a resolved path inside `exempt` (by default
    the package's own source directory and, with sys.pycache_prefix, its
    bytecode mirror, computed once from the package location) is not reported,
    whatever attribute holds it. It is keyed on the package's __file__, never
    __path__: it covers a regular package whose __file__ is an exact absolute
    str (that file's directory). A namespace package (__file__ None) gets no
    source directory and a refused finding; __path__ entries outside the
    __file__ directory are not exempt. For such a regular package it covers
    import metadata (__file__, __cached__, __spec__, __path__ and a
    source-file loader's path) when the checkout lives under a user root, and
    also any ordinary constant naming a file inside the package: shipped
    package data is not user settings. A zip import's archive path lies
    outside the source directory and is reported.
    Named license-file limit: `exempt_files` holds, by default, the license
    helper's absolute candidate files, which every module reaches through
    __builtins__; they lie inside the interpreter's installation, so they are
    under a user root when Python is installed under HOME (pyenv, uv, conda,
    asdf, a $HOME/.local prefix). Of its six strings the four absolute ones
    (LICENSE.txt and LICENSE in the stdlib directory and in its parent) are
    exempt as exact resolved files. tests/_isolation.py reads the two
    locations and joins and resolves the four files before fleet_tui is
    imported, so package import code rebinding sys._stdlib_dir, os.__file__,
    os.pardir, os.path.join/dirname or Path.resolve cannot move them. The
    snapshot does not cover code that runs before it (a .pth file,
    sitecustomize or usercustomize can rebind those names or locations
    before tests/_isolation.py reads them), this scan's own later operations
    (resolving and comparing scanned paths), the source directories above
    (computed when this module is imported), code that edits the snapshot
    module itself, or filesystem state: the stored files are Path.resolve
    results taken when they are computed, and every scanned absolute path
    is resolved before it is compared with them. If a candidate path is a
    symlink at that point, its target is the exempt file, and the
    candidate's own spelling, the target and every other path resolving to
    the target are not reported. A symlink made there later does not
    change the stored files, so paths resolving to its target are reported
    unless that target is itself a stored file.
    The two relative ones (./LICENSE.txt, ./LICENSE) are skipped by the
    absolute-path gate above, not exempted. The residual limit is the four
    candidates' resolved targets: a path resolving to exactly one of them is
    not reported, whatever attribute holds it and wherever the target lies.
    The only exempt directories are the package source directory and, with
    an absolute sys.pycache_prefix, its bytecode mirror below that prefix,
    wherever they lie: the source directory inside the installation when
    the checkout or the imported package lives there, the mirror wherever
    the prefix names (e.g. under ~/.cache). HOME, an XDG base directory, the
    installation and the venv's own tree (sys.prefix, sys.exec_prefix,
    wherever the venv lies, including inside the installation) are exempt
    only where one of those exemptions reaches into them: the package source
    directory (all of HOME when it resolves to HOME or above it), the
    bytecode mirror, and the four license targets: LICENSE.txt and LICENSE
    in the stdlib directory and in its parent, both taken after symlinks
    resolve, so a linked stdlib's parent is its target's parent. With the
    installation's prefix at HOME and the stdlib in $HOME/lib/python3.12,
    $HOME/lib/LICENSE.txt is not reported and $HOME/lib/settings.json is;
    with the stdlib named $HOME/lib64/python3.12 but resolving to
    $HOME/lib/python3.12 (through $HOME/lib64 -> lib, or through
    lib64/python3.12 -> ../lib/python3.12 in a real lib64 directory),
    $HOME/lib/LICENSE.txt is not reported, and in the second layout
    $HOME/lib64/LICENSE.txt is. Paths elsewhere under a root are
    reported under every name, including import-metadata names and
    __builtins__ entries. Named user-root limit: the package scan's roots
    (tests/_isolation.py's, taken before the redirect) are the resolved
    HOME and the resolved absolute entries of the XDG variables that are
    set. A relative XDG value is never a root, although fleet_tui/paths.py
    (_read_config) honours a relative XDG_CONFIG_HOME, resolved against the
    working directory, when it reads its config; that directory counts only
    when it lies under a root. A per-user default directory not named by an
    absolute set variable (its variable unset, relative or set to another
    directory, e.g. ~/.config without XDG_CONFIG_HOME, and always
    ~/.local/bin, which has no XDG variable) counts only through HOME: if it
    resolves outside HOME, itself or through a symlinked parent such as
    ~/.local, a path under it resolves outside every root and is not
    reported. Exemption locations are used only when they are
    exact absolute str; each refused one is listed in `refused` (by default
    those of the module-level computation) and reported as an uninspectable
    exemption source.
    """
    found = list(refused)

    def stored_name(name, index, label):
        if type(name) is str:
            return name
        safe = f'<non-str key #{index}>'
        found.append(f'{label}.{safe} = <uninspectable non-str key>')
        return safe

    def stored_attributes(value, label):
        children = []
        cls = type(value)
        # Invoke type's own descriptors, never metaclass attribute lookup.
        mro = type.__dict__['__mro__'].__get__(cls)
        dictionaries = []
        for base_index, base in enumerate(mro):
            dictionary = type.__dict__['__dict__'].__get__(base)
            entries = []
            for index, (name, descriptor) in enumerate(MappingProxyType.items(dictionary)):
                safe = stored_name(name, index, f'{label}.<class #{base_index}>')
                entries.append((safe, descriptor))
                if (type(name) is not str
                        and type(descriptor) is not MemberDescriptorType):
                    children.append((safe, descriptor))
            dictionaries.append(entries)
        inspected_dict = False
        for entries in dictionaries:
            descriptor = next((child for name, child in entries
                               if name == '__dict__'), None)
            if type(descriptor) is not GetSetDescriptorType:
                continue  # Names above are exact str; user keys are never compared.
            try:
                stored = descriptor.__get__(value, cls)
            except Exception:
                found.append(f'{label}.__dict__ = <uninspectable refusing descriptor>')
                continue
            inspected_dict = True
            if type(stored) is dict:
                children.extend((stored_name(name, index, label), child)
                                for index, (name, child) in enumerate(dict.items(stored)))
            else:
                found.append(f'{label}.__dict__ = <uninspectable non-exact dict>')
            break
        if not inspected_dict and type.__dict__['__dictoffset__'].__get__(cls):
            found.append(f'{label}.__dict__ = <uninspectable dictionary storage>')
        for entries in dictionaries:
            for name, descriptor in entries:
                if type(descriptor) is MemberDescriptorType:
                    try:
                        children.append((name, descriptor.__get__(value, cls)))
                    except AttributeError:
                        pass  # Genuine unset slots contain no stored value.
                    except Exception:
                        found.append(f'{label}.{name} = <uninspectable refusing descriptor>')
        return children

    def visit(value, label, module_name, depth=0, ancestors=frozenset()):
        if depth > 8 or id(value) in ancestors:
            return
        raw = None
        # Class-based checks avoid isinstance consulting an overridden __class__.
        if issubclass(type(value), str):
            raw = str.__str__(value)
        elif issubclass(type(value), bytes):
            raw = bytes.decode(value, sys.getfilesystemencoding(), sys.getfilesystemencodeerrors())
        elif any(type(value) is kind for kind in (Path, PurePath, PurePosixPath,
                                                PureWindowsPath, PosixPath, WindowsPath)):
            # Never stringify the original: even exact paths can retain hostile
            # str subclasses in _raw_paths (3.12+). Ignore all parsing caches.
            # The other OS's concrete class refuses construction on this host,
            # so it is reported without being decoded or constructed.
            if type(value) is (PosixPath if os.name == 'nt' else WindowsPath):
                found.append(f'{label} = <uninspectable foreign concrete path>')
                return
            # Decode through the matching pure flavour, which never refuses the
            # host OS; exact PurePath/Path use the host's pure flavour.
            host_pure = PureWindowsPath if os.name == 'nt' else PurePosixPath
            flavour = next(pure for kind, pure in (
                (PurePosixPath, PurePosixPath), (PosixPath, PurePosixPath),
                (PureWindowsPath, PureWindowsPath), (WindowsPath, PureWindowsPath),
                (PurePath, host_pure), (Path, host_pure)) if type(value) is kind)
            slot = PurePath.__dict__.get('_raw_paths', PurePath.__dict__.get('_parts'))
            if type(slot) is not MemberDescriptorType:
                found.append(f'{label} = <uninspectable pathlib storage>')
                return
            try:
                components = slot.__get__(value)
            except AttributeError:
                found.append(f'{label} = <uninspectable unset pathlib storage>')
                return
            if type(components) is not list:
                found.append(f'{label} = <uninspectable pathlib components>')
                return
            components = tuple(components)
            if any(type(part) is not str for part in components):
                found.append(f'{label} = <uninspectable pathlib components>')
                return
            raw = os.fsdecode(flavour(*components))
        elif type(value) is bytearray or type(value) is memoryview:
            try:
                buffer = bytes(value)
            except ValueError:  # A released memoryview has no readable buffer.
                found.append(f'{label} = <uninspectable released buffer>')
                return
            raw = os.fsdecode(buffer)
        elif type(value) is array:
            if value.typecode in ('b', 'B'):
                raw = os.fsdecode(value.tobytes())
            elif value.typecode in ('u', 'w'):
                raw = value.tounicode()
        if raw is not None:
            if os.path.isabs(raw):
                try:
                    resolved = Path(raw).resolve()
                # ValueError: embedded NUL. OSError: I/O failure. RuntimeError:
                # symlink loop on 3.11/3.12; 3.14 can resolve a loop instead.
                except (ValueError, OSError, RuntimeError):
                    found.append(f'{label} = <uninspectable path resolution>')
                else:
                    if (any(resolved.is_relative_to(root) for root in roots)
                            and not any(resolved.is_relative_to(source) for source in exempt)
                            and not any(resolved == location for location in exempt_files)):
                        found.append(f'{label} = {raw}')
            # Exact strings have no instance storage. Subclasses can hide a
            # second path in attributes, still read only through builtin slots.
            if type(value) is not str and type(value) is not bytes and issubclass(type(value), (str, bytes)):
                for name, child in stored_attributes(value, label):
                    visit(child, f'{label}.{name}', module_name, depth + 1, ancestors | {id(value)})
            return
        ancestors = ancestors | {id(value)}
        children = []
        if type(value) is FunctionType:
            children = [('__defaults__', value.__defaults__), ('__kwdefaults__', value.__kwdefaults__),
                        ('__closure__', value.__closure__)]
            # The descriptor is absent on 3.11/3.12: instance lookup there could
            # compare a malicious __dict__ key. See the named annotation limit.
            annotate = FunctionType.__dict__.get('__annotate__')
            if annotate is None or annotate.__get__(value) is None:
                children.append(('__annotations__', value.__annotations__))
            children.extend(dict.items(vars(value)))
        elif type(value) is GeneratorType:
            # CPython GC visits stored references without synchronizing f_locals
            # or performing mapping lookups against keys in the cached dict.
            if (sys.implementation.name == 'cpython'
                    and sys.version_info[:2] in ((3, 11), (3, 12), (3, 13), (3, 14))):
                frame = value.gi_frame
                metadata = (frame, value.gi_code)
                children = [(f'gi_referent[{i}]', child)
                            for i, child in enumerate(get_referents(value))
                            if not any(child is item for item in metadata)]
                # Read the frame object's trace function and stored referents.
                # On 3.14 these include extra-locals, legacy locals snapshots
                # and retained overwritten fast-local values. Detection on
                # 3.13 does not establish its storage owner. Use mapping labels.
                if frame is not None:
                    children.extend((f'gi_frame_referent[{i}]', child)
                                    for i, child in enumerate(get_referents(frame)))
            else:
                found.append(f'{label} = <uninspectable generator state on this interpreter>')
            children.append(('gi_yieldfrom', value.gi_yieldfrom))
        elif issubclass(type(value), partial):
            children = [(name, partial.__dict__[name].__get__(value))
                        for name in ('func', 'args', 'keywords')]
        elif type(value) is CellType:
            try:
                children = [('cell_contents', value.cell_contents)]
            except ValueError:  # empty closure cell
                pass
        elif issubclass(type(value), property):
            children = [(name, property.__dict__[name].__get__(value))
                        for name in ('fget', 'fset', 'fdel')]
        elif issubclass(type(value), (staticmethod, classmethod)):
            base = staticmethod if issubclass(type(value), staticmethod) else classmethod
            children = [('__func__', base.__dict__['__func__'].__get__(value))]
        elif value is type:
            pass  # type's __module__ is its builtin descriptor, not stored metadata.
        elif issubclass(type(value), type):
            namespace = type.__dict__['__dict__'].__get__(value)
            entries = list(MappingProxyType.items(namespace))
            owner = next((child for name, child in entries
                          if type(name) is str and name == '__module__'), None)
            if type(owner) is str and owner == module_name:
                children = entries
            elif owner is not None and type(owner) is not str:
                found.append(f'{label}.__module__ = <uninspectable non-str class module>')
        elif issubclass(type(value), dict) or type(value) is MappingProxyType:
            # A mappingproxy can wrap a user mapping. Only read dict storage;
            # calling the proxy's items method could delegate to that mapping.
            storage = value
            if type(value) is MappingProxyType:
                referents = get_referents(value)
                if len(referents) != 1 or not issubclass(type(referents[0]), dict):
                    found.append(f'{label} = <uninspectable mapping storage>')
                    return
                storage = referents[0]
            children = []
            for i, (key, child) in enumerate(dict.items(storage)):
                children.append((f'key[{i}]', key))
                children.append((f'value[{i}]', child))
            if issubclass(type(value), defaultdict):
                children.append(('default_factory', defaultdict.default_factory.__get__(value)))
        elif issubclass(type(value), (tuple, list, set, frozenset, deque)):
            base = next(kind for kind in (tuple, list, set, frozenset, deque)
                        if issubclass(type(value), kind))
            children = [(str(i), child) for i, child in enumerate(base.__iter__(value))]
        elif not issubclass(type(value), ModuleType):
            # Read storage through genuine descriptors, bypassing a property
            # named __dict__ and instance attribute lookup at this step.
            children.extend(stored_attributes(value, label))
        for index, (name, child) in enumerate(children):
            name = stored_name(name, index, label)
            visit(child, f'{label}.{name}', module_name, depth + 1, ancestors)

    for index, module in enumerate(modules):
        namespace = ModuleType.__dict__['__dict__'].__get__(module)
        entries = list(dict.items(namespace))
        module_name = next((child for name, child in entries
                            if type(name) is str and name == '__name__'), None)
        if type(module_name) is not str:
            module_name = f'<module #{index}>'
            found.append(f'{module_name}.__name__ = <uninspectable non-str module name>')
        for index, (name, value) in enumerate(entries):
            # Import metadata is exempted by location (see `exempt`), not name.
            name = stored_name(name, index, module_name)
            visit(value, f'{module_name}.{name}', module_name)
    return sorted(found)


def test_all_module_settings_paths_avoid_original_roots():
    # Fallback lets this same control run against the pre-isolation conftest:
    # it must fail on actual settings paths, not a missing fixture/attribute.
    roots = getattr(conftest, '_ORIGINAL_USER_ROOTS', (Path.home().resolve(),))
    probe = ModuleType('positive_control')
    probe.STRING_PATH = str(roots[0] / 'planted-settings')
    probe.PATHLIKE = roots[0] / 'planted-state'
    assert len(_settings_paths_in_roots([probe], roots)) == 2

    modules = _package_modules()
    assert len(modules) > 1, 'package discovery found no child modules'
    leaked = _settings_paths_in_roots(modules, roots)
    assert not leaked, 'Settings escaped the test session:\n' + '\n'.join(leaked)


def _checkout_root():
    # The checkout's own directory stands in for a user root holding it, as
    # when an adopter clones under HOME.
    return Path(fleet_tui.__file__).resolve().parents[2]


def test_package_scan_under_user_root_ignores_own_source_metadata():
    root = _checkout_root()
    source = Path(fleet_tui.__file__).resolve().parent
    assert source in _SOURCE_METADATA_DIRS and source.is_relative_to(root)
    modules = _package_modules()
    assert len(modules) > 1, 'package discovery found no child modules'
    # Positive control: without the exemption the same scan reports the
    # package's import metadata, so this test can observe a regression.
    assert _settings_paths_in_roots(modules, (root,), exempt=())
    assert _settings_paths_in_roots(modules, (root,)) == []


@pytest.mark.parametrize('kind', ['plain', 'beside-import-attrs', 'inside-path',
                                  'inside-spec', 'spec-value', 'inside-file',
                                  'non-exact-spec-key'])
def test_package_scan_under_user_root_still_reports_other_paths(kind):
    root = _checkout_root()
    planted = str(root / 'planted-settings')
    probe = ModuleType('positive_control')
    if kind == 'plain':
        probe.SETTINGS = planted
    elif kind == 'beside-import-attrs':
        probe.__spec__ = None
        probe.STATE = Path(planted)
    elif kind == 'inside-path':
        probe.__path__ = [planted]
    elif kind == 'inside-spec':
        probe.__spec__ = importlib.machinery.ModuleSpec('positive_control', None,
                                                        origin=planted)
    elif kind == 'spec-value':
        probe.__spec__ = planted
    elif kind == 'inside-file':
        probe.__file__ = planted
    else:
        class Key(str):
            pass
        # Assigning through an equal key would keep the existing exact-str
        # '__spec__' key, so remove it first to store a genuine subclass key.
        del vars(probe)['__spec__']
        vars(probe)[Key('__spec__')] = planted
    found = _settings_paths_in_roots([probe], (root,))
    assert [hit for hit in found if hit.endswith(f' = {planted}')], found
    if kind == 'non-exact-spec-key':
        assert any('<uninspectable non-str key>' in hit for hit in found), found


def test_package_source_constant_is_a_named_limit():
    # Documented limit: a path inside the package's own source directory is
    # exempt under every name, because shipped package data is not a setting.
    root = _checkout_root()
    probe = ModuleType('positive_control')
    probe.DATA = str(Path(fleet_tui.__file__).resolve().parent / 'shipped.json')
    probe.later = str(root / 'later')
    assert _settings_paths_in_roots([probe], (root,)) == [
        f'positive_control.later = {root / "later"}']


def test_pycache_prefix_bytecode_is_source_metadata(monkeypatch, tmp_path):
    root = tmp_path
    monkeypatch.setattr(sys, 'pycache_prefix', str(root / 'pycache'))
    exempt, refused = _source_metadata_dirs()
    assert refused == ()
    cached = importlib.util.cache_from_source(importlib.import_module(
        'fleet_tui.models').__file__)
    assert Path(cached).is_relative_to(root / 'pycache')
    probe = ModuleType('positive_control')
    probe.__cached__ = cached
    probe.later = str(root / 'later')
    assert _settings_paths_in_roots([probe], (root,), exempt=exempt) == [
        f'positive_control.later = {root / "later"}']
    # Without the prefix mirror the same bytecode path is reported.
    source_only = (Path(fleet_tui.__file__).resolve().parent,)
    assert _settings_paths_in_roots([probe], (root,), exempt=source_only) == [
        f'positive_control.__cached__ = {cached}',
        f'positive_control.later = {root / "later"}']


def _interpreter_root():
    # The running interpreter's own installation, wherever this host keeps it.
    # A user root is the installation itself, never its parent (which is / for
    # a system Python under /usr).
    return Path(sys.base_prefix).resolve()


def _license_helper(here):
    # The helper exactly as site.py builds it from a stdlib directory.
    return type(license)('license', 'text', ['LICENSE.txt', 'LICENSE'],
                         [os.path.join(here, os.pardir), here, os.curdir])


def _hit_paths(found):
    return {Path(hit.partition(' = ')[2]).resolve() for hit in found}


def test_package_scan_with_interpreter_installation_as_root():
    root = _interpreter_root()
    assert _LICENSE_FILES and _EXEMPTION_REFUSALS == ()
    modules = _package_modules()
    assert len(modules) > 1, 'package discovery found no child modules'
    # Positive control: without the license exemption the helper's candidate
    # files, reached through __builtins__, are reported, and they are exactly
    # the exempt files: nothing else in the installation is reached.
    unexempted = _settings_paths_in_roots(modules, (root,), exempt_files=())
    assert unexempted and all('.__builtins__.' in hit and '._Printer__filenames.' in hit
                              for hit in unexempted), unexempted
    assert _hit_paths(unexempted) == set(_LICENSE_FILES), unexempted
    assert _settings_paths_in_roots(modules, (root,)) == []
    # The installation itself is not exempt: other paths under it, including
    # beside the license files, are reported under ordinary and builtin names.
    probe = ModuleType('positive_control')
    probe.SETTINGS = str(root / 'planted-settings')
    beside = str(_LICENSE_FILES[0].parent / 'planted-beside-license')
    probe.__builtins__ = {'STATE': beside}
    assert _settings_paths_in_roots(modules + [probe], (root,)) == sorted([
        f'positive_control.SETTINGS = {root / "planted-settings"}',
        f'positive_control.__builtins__.value[0] = {beside}'])


@pytest.mark.parametrize('kind', ['plain', 'builtins-dict', 'builtins-license-helper'])
def test_interpreter_exemption_still_reports_paths_outside_installation(kind, tmp_path):
    root = tmp_path.resolve()
    assert not any(location.is_relative_to(root) for location in _LICENSE_FILES)
    planted = str(root / 'planted-settings')
    probe = ModuleType('positive_control')
    if kind == 'plain':
        probe.SETTINGS = planted
    elif kind == 'builtins-dict':
        probe.__builtins__ = {'SETTINGS': planted}
    else:
        # The license helper's own shape, pointed outside the installation:
        # exemption is by location, never by attribute or type.
        helper = type(license)('license', 'text', ['LICENSE'], [planted])
        probe.__builtins__ = {'license': helper}
    found = _settings_paths_in_roots([probe], (root,))
    assert len(found) == 1 and found[0].endswith(f' = {planted}' if kind != 'builtins-license-helper'
                                                 else f' = {planted}/LICENSE'), found
    if kind != 'plain':
        assert found[0].startswith('positive_control.__builtins__.value[0]'), found


def test_license_files_are_a_named_limit():
    # Documented limit: a path resolving to exactly one of the license
    # helper's candidate files is exempt under every name. A sibling sharing
    # the file name prefix, and a path below the file, are still reported.
    exact = _LICENSE_FILES[0]
    sibling = str(exact.parent / (exact.name + '-sibling'))
    below = str(exact / 'below')
    probe = ModuleType('positive_control')
    probe.INSIDE = str(exact)
    probe.__builtins__ = {'INSIDE': str(exact)}
    probe.SIBLING = sibling
    probe.BELOW = below
    root = exact.parent
    assert _settings_paths_in_roots([probe], (root,)) == [
        f'positive_control.BELOW = {below}',
        f'positive_control.SIBLING = {sibling}']
    # Without the license exemption the same planted paths are reported.
    assert _settings_paths_in_roots([probe], (root,), exempt_files=()) == [
        f'positive_control.BELOW = {below}',
        f'positive_control.INSIDE = {exact}',
        f'positive_control.SIBLING = {sibling}',
        f'positive_control.__builtins__.value[0] = {exact}']


@pytest.mark.parametrize('layout', ['home-dot-local', 'home', 'uv', 'pyenv', 'conda'])
def test_license_exemption_never_covers_user_directories(tmp_path, layout):
    # An interpreter installed with prefix $HOME/.local (or HOME itself) shares
    # its tree with the XDG data/state/bin defaults. Only the helper's own
    # files are exempt, so settings there and anywhere in HOME are reported.
    home = tmp_path.resolve() / 'home'
    prefix = {'home-dot-local': home / '.local', 'home': home,
              'uv': home / '.local' / 'share' / 'uv' / 'python' / 'cpython-3-linux',
              'pyenv': home / '.pyenv' / 'versions' / '3', 'conda': home / 'miniforge3'}[layout]
    here = str(prefix / 'lib' / 'python3')
    files, refused = _license_files((('sys._stdlib_dir', here), ('os.__file__', None)))
    assert refused == () and files == tuple(
        directory / name for directory in (prefix / 'lib', prefix / 'lib' / 'python3')
        for name in ('LICENSE.txt', 'LICENSE'))
    leaks = [home / 'settings.json', prefix / 'etc' / 'settings.json',
             Path(here) / 'site-packages' / 'settings.json'] + [
        home / relative / 'fleet_tui' / 'settings.json'
        for relative in ('.config', '.cache', '.local/share', '.local/state', '.local/bin')]
    probe = ModuleType('positive_control')
    probe.__builtins__ = {'license': _license_helper(here)}
    for index, leak in enumerate(leaks):
        setattr(probe, f'LEAK_{index}', str(leak))
    found = _settings_paths_in_roots([probe], (home,), exempt_files=files)
    assert found == sorted(f'positive_control.LEAK_{index} = {leak}'
                           for index, leak in enumerate(leaks))
    # Positive control: the same scan without the exemption also reports the
    # helper's four absolute candidates (its two relative ones never).
    unexempted = _settings_paths_in_roots([probe], (home,), exempt_files=())
    assert _hit_paths(unexempted) == set(leaks) | set(files), unexempted


def _check_venv_tree_is_reported(prefix, exec_prefix, base_prefix, version, exempt_files):
    # The venv's own tree (prefix, exec_prefix) and the installation's are
    # reported wherever the venv lies, including inside the installation
    # (a checkout under HOME when Python's prefix is HOME or ~/.local, a venv
    # under a conda base or a pyenv-virtualenv): only the exact license files
    # are exempt. Returns the planted paths.
    venv = tuple(dict.fromkeys(Path(location).resolve() for location in (prefix, exec_prefix)))
    installation = Path(base_prefix).resolve()
    names = ['planted-settings', 'pyvenv-state.json']
    planted = {location / name for location in venv for name in names}
    # Decided per location, on resolved paths: a venv location other than the
    # installation has no license exemption, so even license-named files in
    # its own lib are reported. Under the installation itself (no venv,
    # including when exec_prefix lies elsewhere, and whatever symlink or '..'
    # spelling names it) those names are the exempt files.
    license_owners = [location for location in venv if location != installation]
    planted |= {location / name for location in license_owners
                for name in ('lib/LICENSE.txt', f'lib/{version}/LICENSE')}
    # Also where this installation keeps its license files (lib64, a
    # free-threaded python3.14t stdlib), each at the same relative path.
    planted |= {location / exempt.relative_to(installation) for location in license_owners
                for exempt in exempt_files if exempt.is_relative_to(installation)}
    beside = tuple(dict.fromkeys(location.parent for location in exempt_files))
    planted.add(installation / 'planted-settings')
    planted |= {directory / 'planted-beside-license' for directory in beside}
    # The one thing this check needs: no planted path is, or resolves to, an
    # exempt file.
    shadowed = sorted(path for path in planted if path.resolve() in set(exempt_files))
    assert not shadowed, f'planted paths resolve to exempt files: {shadowed}'
    probe = ModuleType('positive_control')
    probe.__builtins__ = {'SETTINGS': tuple(sorted(map(str, planted)))}
    found = _settings_paths_in_roots([probe], venv + (installation,) + beside,
                                     exempt_files=exempt_files)
    # Hits are resolved; so are the plants (a venv's lib64 is a symlink to lib).
    assert _hit_paths(found) == {path.resolve() for path in planted}, found
    # Distinct spellings of one resolved path collapse above, so also compare
    # the reported spellings: each planted spelling is reported as planted.
    assert {hit.partition(' = ')[2] for hit in found} == {str(path) for path in planted}, found
    return planted


def test_venv_tree_is_not_exempt():
    _check_venv_tree_is_reported(
        sys.prefix, sys.exec_prefix, sys.base_prefix,
        f'python{sys.version_info[0]}.{sys.version_info[1]}', _LICENSE_FILES)


@pytest.mark.parametrize('layout', ['home', 'home-dot-local-src', 'venv-under-conda-base',
                                    'conda-env', 'pyenv-virtualenv', 'outside'])
def test_venv_tree_inside_installation_is_reported(tmp_path, layout):
    # Injected locations only: nothing is created. The venv lies inside the
    # installation prefix in every layout except the control ('outside').
    # 'venv-under-conda-base' is a venv created inside a conda base's envs
    # directory; a real conda env ('conda-env') is itself the installation:
    # prefix == exec_prefix == base_prefix == the env, with no venv.
    home = tmp_path.resolve() / 'home'
    prefix, venv = {
        'home': (home, home / 'co' / 'tui' / '.venv'),
        'home-dot-local-src': (home / '.local', home / '.local' / 'src' / 'co' / 'tui' / '.venv'),
        'venv-under-conda-base': (home / 'miniforge3', home / 'miniforge3' / 'envs' / 'tui'),
        'conda-env': (home / 'miniforge3' / 'envs' / 'tui', home / 'miniforge3' / 'envs' / 'tui'),
        'pyenv-virtualenv': (home / '.pyenv' / 'versions' / '3.12.0',
                             home / '.pyenv' / 'versions' / '3.12.0' / 'envs' / 'tui'),
        'outside': (home / '.local', home / 'co' / 'tui' / '.venv')}[layout]
    assert venv.is_relative_to(prefix) == (layout != 'outside')
    assert (venv == prefix) == (layout == 'conda-env')
    here = str(prefix / 'lib' / 'python3.12')
    files, refused = _license_files((('sys._stdlib_dir', here), ('os.__file__', None)))
    assert refused == () and len(files) == 4
    _check_venv_tree_is_reported(str(venv), str(venv), str(prefix), 'python3.12', files)
    # Positive control: exempting the venv tree or the installation hides a plant.
    for exempt in ((venv,), (prefix,)):
        probe = ModuleType('positive_control')
        probe.SETTINGS = str(venv / 'planted-settings')
        probe.STATE = str(prefix / 'planted-settings')
        assert len(_settings_paths_in_roots([probe], (home,), exempt_files=files)) == 2
        assert len(_settings_paths_in_roots([probe], (home,), exempt=exempt,
                                            exempt_files=files)) < 2


@pytest.mark.parametrize('layout', ['exec-prefix-split', 'venv-exec-prefix-split'])
def test_venv_tree_with_separate_exec_prefix_is_reported(tmp_path, layout):
    # Injected locations only: nothing is created. 'exec-prefix-split' is an
    # interpreter built with a separate --exec-prefix and used without a venv
    # (prefix == base_prefix, exec_prefix elsewhere); 'venv-exec-prefix-split'
    # is a venv whose exec_prefix differs from its prefix.
    root = tmp_path.resolve()
    installation = root / 'usr'
    prefix, exec_prefix = {
        'exec-prefix-split': (installation, installation / 'libexec'),
        'venv-exec-prefix-split': (root / 'venv', root / 'venv-exec')}[layout]
    here = str(installation / 'lib' / 'python3.12')
    files, refused = _license_files((('sys._stdlib_dir', here), ('os.__file__', None)))
    assert refused == () and len(files) == 4
    planted = _check_venv_tree_is_reported(str(prefix), str(exec_prefix), str(installation),
                                           'python3.12', files)
    # License-named plants go exactly under the venv locations that are not
    # the installation: under the installation those names are exempt files.
    owners = [location for location in (prefix, exec_prefix) if location != installation]
    assert {path for path in planted if path.name.startswith('LICENSE')} == {
        location / name for location in owners
        for name in ('lib/LICENSE.txt', 'lib/LICENSE', 'lib/python3.12/LICENSE.txt',
                     'lib/python3.12/LICENSE')}
    # Positive control: exempting prefix, exec_prefix or the installation
    # directory hides the plant below it.
    for location in (prefix, exec_prefix, installation):
        probe = ModuleType('positive_control')
        probe.SETTINGS = str(location / 'planted-settings')
        assert len(_settings_paths_in_roots([probe], (root,), exempt_files=files)) == 1
        assert _settings_paths_in_roots([probe], (root,), exempt=(location,),
                                        exempt_files=files) == []


@pytest.mark.parametrize('spelling', ['prefix-symlink', 'exec-prefix-symlink', 'both-symlink',
                                      'installation-symlink', 'prefix-dotdot',
                                      'exec-prefix-dotdot'])
def test_venv_locations_are_compared_with_the_installation_resolved(tmp_path, spelling):
    # prefix, exec_prefix or base_prefix spelled as a symlink to, or a '..'
    # spelling of, the installation: no venv. Compared as spelled they differ,
    # and license-named plants would land on the exempt files; compared
    # resolved they are the installation. Only the symlink a row names is
    # created.
    root = tmp_path.resolve()
    installation = root / 'usr'
    link = root / 'usr-link'
    if 'symlink' in spelling:
        link.symlink_to(installation, target_is_directory=True)
    dotdot = installation / 'bin' / os.pardir
    prefix, exec_prefix, base_prefix = {
        'prefix-symlink': (link, installation, installation),
        'exec-prefix-symlink': (installation, link, installation),
        'both-symlink': (link, link, installation),
        'installation-symlink': (installation, installation, link),
        'prefix-dotdot': (dotdot, installation, installation),
        'exec-prefix-dotdot': (installation, dotdot, installation)}[spelling]
    spellings = (prefix, exec_prefix)
    assert all(Path(location).resolve() == installation
               for location in spellings + (base_prefix,)), spelling
    assert any(Path(location) != Path(base_prefix) for location in spellings), spelling
    here = str(installation / 'lib' / 'python3.12')
    files, refused = _license_files((('sys._stdlib_dir', here), ('os.__file__', None)))
    assert refused == () and len(files) == 4
    planted = _check_venv_tree_is_reported(str(prefix), str(exec_prefix), str(base_prefix),
                                           'python3.12', files)
    assert not [path for path in planted if path.name.startswith('LICENSE')], sorted(planted)
    # Control: the license-named plants a venv location would get are exactly
    # the exempt files here, so a plant made from an unresolved spelling is
    # hidden by the scan.
    probe = ModuleType('positive_control')
    probe.SETTINGS = tuple(str(Path(location) / 'lib' / 'LICENSE.txt') for location in spellings)
    assert _settings_paths_in_roots([probe], (root,), exempt_files=files) == []
    assert len(_settings_paths_in_roots([probe], (root,), exempt_files=())) == 2


@pytest.mark.parametrize('layout', ['free-threaded-venv', 'free-threaded-exec-prefix-split',
                                    'lib64-venv', 'lib64-venv-with-link',
                                    'lib64-exec-prefix-split'])
def test_venv_license_names_follow_the_installation_layout(tmp_path, layout):
    # Injected locations only; only 'lib64-venv-with-link' creates something:
    # the venv's lib64 -> lib symlink that venv makes on 64-bit Linux. A
    # free-threaded build keeps its stdlib in lib/python3.14t, a platlibdir
    # lib64 build in lib64/python3.12; the license-named plants include the
    # exempt files' own paths relative to the installation, under every venv
    # location that is not the installation.
    root = tmp_path.resolve()
    installation = root / 'usr'
    stdlib, version = {'free-threaded': ('lib/python3.14t', 'python3.14'),
                       'lib64': ('lib64/python3.12', 'python3.12')}[
        'lib64' if layout.startswith('lib64') else 'free-threaded']
    prefix, exec_prefix = ((installation, installation / 'libexec')
                           if layout.endswith('-exec-prefix-split')
                           else (root / 'venv', root / 'venv'))
    if layout == 'lib64-venv-with-link':
        (prefix / 'lib').mkdir(parents=True)
        (prefix / 'lib64').symlink_to('lib', target_is_directory=True)
    here = str(installation / stdlib)
    files, refused = _license_files((('sys._stdlib_dir', here), ('os.__file__', None)))
    assert refused == () and len(files) == 4
    relative = {location.relative_to(installation) for location in files}
    assert relative == {Path(directory) / name for directory in (Path(stdlib).parent, stdlib)
                        for name in ('LICENSE.txt', 'LICENSE')}
    planted = _check_venv_tree_is_reported(str(prefix), str(exec_prefix), str(installation),
                                           version, files)
    owners = [location for location in dict.fromkeys((prefix, exec_prefix))
              if location != installation]
    fixed = {location / name for location in owners
             for name in ('lib/LICENSE.txt', f'lib/{version}/LICENSE')}
    mirrored = {location / path for location in owners for path in relative}
    assert {path for path in planted if path.name.startswith('LICENSE')} == fixed | mirrored
    # Control: the fixed spellings alone miss the license files in this
    # installation's stdlib directory.
    in_stdlib = {location / stdlib / name for location in owners
                 for name in ('LICENSE.txt', 'LICENSE')}
    assert owners and in_stdlib <= mirrored - fixed, sorted(in_stdlib & fixed)


def test_venv_tree_check_refuses_plants_resolving_to_exempt_files(tmp_path):
    # The check's precondition compares resolved plants: a venv whose lib is
    # a symlink to the installation's lib makes its license-named plants
    # resolve to the exempt files, though no spelling of them is one. The
    # precondition names those plants; compared as spelled, it would pass
    # them to the scan, which hides them.
    root = tmp_path.resolve()
    installation = root / 'usr'
    (installation / 'lib' / 'python3.12').mkdir(parents=True)
    venv = root / 'venv'
    venv.mkdir()
    (venv / 'lib').symlink_to(installation / 'lib', target_is_directory=True)
    here = str(installation / 'lib' / 'python3.12')
    files, refused = _license_files((('sys._stdlib_dir', here), ('os.__file__', None)))
    assert refused == () and len(files) == 4
    shadowed = sorted(venv / exempt.relative_to(installation) for exempt in files)
    assert all(path not in files and path.resolve() in files for path in shadowed), shadowed
    with pytest.raises(AssertionError) as raised:
        _check_venv_tree_is_reported(str(venv), str(venv), str(installation), 'python3.12', files)
    # The precondition's own message (pytest may append its explanation).
    assert str(raised.value).partition('\n')[0] == (
        f'planted paths resolve to exempt files: {shadowed}')
    # Control: the scan hides exactly those plants.
    probe = ModuleType('positive_control')
    probe.SETTINGS = tuple(map(str, shadowed))
    assert _settings_paths_in_roots([probe], (root,), exempt_files=files) == []
    assert len(_settings_paths_in_roots([probe], (root,), exempt_files=())) == 4


def test_license_exemption_resolves_its_locations(tmp_path):
    # The stdlib location is given through a symlinked spelling; the helper's
    # candidates are resolved before matching, so both spellings are exempt.
    real = tmp_path.resolve() / 'real'
    (real / 'lib' / 'python3').mkdir(parents=True)
    link = tmp_path.resolve() / 'link'
    link.symlink_to(real, target_is_directory=True)
    here = str(link / 'lib' / 'python3')
    files, refused = _license_files((('sys._stdlib_dir', here), ('os.__file__', None)))
    assert refused == () and all(location.is_relative_to(real) for location in files), files
    probe = ModuleType('positive_control')
    probe.__builtins__ = {'license': _license_helper(here)}
    probe.SETTINGS = str(link / 'lib' / 'settings.json')
    root = tmp_path.resolve()
    assert _settings_paths_in_roots([probe], (root,), exempt_files=files) == [
        f'positive_control.SETTINGS = {link / "lib" / "settings.json"}']
    # Control: unexempted, the four candidates are reported beside the setting.
    assert len(_settings_paths_in_roots([probe], (root,), exempt_files=())) == 5


def test_license_exemption_falls_back_to_the_os_module_location(tmp_path):
    # site.py uses os.__file__'s directory only when sys._stdlib_dir is unset.
    root = tmp_path.resolve()
    stdlib = root / 'stdlib' / 'lib' / 'python3'
    fallback = root / 'fallback' / 'lib' / 'python3'
    expected = lambda here: tuple(directory / name for directory in (here.parent, here)
                                  for name in ('LICENSE.txt', 'LICENSE'))
    for unset in (None, ''):
        assert _license_files((('sys._stdlib_dir', unset),
                               ('os.__file__', str(fallback / 'os.py')))) == (expected(fallback), ())
    # With both set the stdlib location wins, as in site.py; the other is not exempt.
    files, refused = _license_files((('sys._stdlib_dir', str(stdlib)),
                                     ('os.__file__', str(fallback / 'os.py'))))
    assert (files, refused) == (expected(stdlib), ())
    probe = ModuleType('positive_control')
    probe.__builtins__ = {'license': _license_helper(str(fallback))}
    assert _hit_paths(_settings_paths_in_roots([probe], (root,), exempt_files=files)) == set(
        expected(fallback))
    assert _license_files((('sys._stdlib_dir', None), ('os.__file__', None))) == ((), ())


def test_license_exemption_ignores_rebinding_after_import(monkeypatch, tmp_path):
    # The default is the four files the pre-import snapshot joined and resolved
    # before fleet_tui was imported, so import-time package code rebinding the
    # locations or the functions that build the paths cannot move it.
    snapshot = _isolation._ORIGINAL_INTERPRETER_LOCATIONS
    assert snapshot == (('sys._stdlib_dir', getattr(sys, '_stdlib_dir', None)),
                        ('os.__file__', getattr(os, '__file__', None)))
    assert _license_files() == _isolation._ORIGINAL_LICENSE_FILES == (
        _LICENSE_FILES, _LICENSE_REFUSALS)
    assert _license_files(snapshot) == _license_files()
    steered = str(tmp_path.resolve() / '.fleet_tui')
    real_join = os.path.join

    def steer_license_join(*parts):
        # Redirect only the license joins, leaving os.path usable elsewhere.
        if parts and type(parts[-1]) is str and parts[-1] in ('LICENSE.txt', 'LICENSE'):
            return real_join(steered, parts[-1])
        return real_join(*parts)

    for rebinding in ('locations', 'pardir', 'join'):
        with monkeypatch.context() as patch:
            if rebinding == 'locations':
                for name in ('base_prefix', 'base_exec_prefix', 'prefix', 'exec_prefix',
                             '_stdlib_dir'):
                    patch.setattr(sys, name, steered, raising=False)
                patch.setattr(os, '__file__', real_join(steered, 'os.py'))
            elif rebinding == 'pardir':
                # site.py's parent candidates come from os.path.join(here, os.pardir).
                patch.setattr(os, 'pardir', steered)
            else:
                patch.setattr(os.path, 'join', steer_license_join)
            assert _license_files() == (_LICENSE_FILES, _LICENSE_REFUSALS), rebinding
            # Executing this module again after the rebinding, as import-time
            # package code would precede it, keeps the same exemption.
            spec = importlib.util.spec_from_file_location(f'{__package__}._rebound_copy', __file__)
            copy = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(copy)
            assert (copy._LICENSE_FILES, copy._EXEMPTION_REFUSALS) == (
                _LICENSE_FILES, _EXEMPTION_REFUSALS), rebinding
            # Control: building the paths now, from the live values or even from
            # the snapshot's locations, would move them onto the planted file.
            live = (('sys._stdlib_dir', getattr(sys, '_stdlib_dir', None)),
                    ('os.__file__', os.__file__))
            moved = _license_files(live if rebinding == 'locations' else snapshot)[0]
            assert Path(real_join(steered, 'LICENSE')) in moved, (rebinding, moved)
    probe = ModuleType('positive_control')
    probe.LEAK = real_join(steered, 'boxes.json')
    probe.PLANTED = real_join(steered, 'LICENSE')
    assert _settings_paths_in_roots([probe], (tmp_path.resolve(),)) == [
        f'positive_control.LEAK = {probe.LEAK}',
        f'positive_control.PLANTED = {probe.PLANTED}']


def _recording_types(calls):
    class HostileStr(str):
        def __getattribute__(self, name):
            calls.append(name)
            return str.__getattribute__(self, name)

        def __fspath__(self):
            calls.append('__fspath__')
            return str.__str__(self)

    class PathLike:
        def __getattribute__(self, name):
            calls.append(name)
            return object.__getattribute__(self, name)

        def __fspath__(self):
            calls.append('__fspath__')
            return '/'

        def __bool__(self):
            calls.append('__bool__')
            return True

        def __eq__(self, other):
            calls.append('__eq__')
            return False

        __hash__ = None

    return HostileStr, PathLike


def test_exemption_sources_accept_exact_absolute_strings_only(tmp_path):
    calls = []
    HostileStr, PathLike = _recording_types(calls)
    # Control: the recorders observe the method calls os.path and pathlib make.
    os.path.abspath(HostileStr('/control'))
    Path(PathLike())
    assert '__fspath__' in calls and len(calls) > 1, calls
    calls.clear()
    package_file = str(tmp_path / 'scratch_pkg' / '__init__.py')
    root = tmp_path.resolve()
    probe = ModuleType('positive_control')
    probe.SETTINGS = str(root / 'planted-settings')

    # A non-exact __file__ on a scratch module object: refused, never used.
    scratch = ModuleType('scratch_pkg')
    scratch.__file__ = HostileStr(package_file)
    dirs, refused = _source_metadata_dirs(scratch, pycache_prefix=None)
    assert (dirs, refused) == ((), ('package.__file__ = <uninspectable exemption source>',))
    for value in (PathLike(), 'scratch_pkg/__init__.py', None):
        scratch.__file__ = value
        assert _source_metadata_dirs(scratch, pycache_prefix=None) == (
            (), ('package.__file__ = <uninspectable exemption source>',))

    # Non-exact prefixes: refused; the source directory is still exempt.
    scratch.__file__ = package_file
    source = Path(package_file).resolve().parent
    for prefix in (PathLike(), HostileStr(str(tmp_path / 'pycache')), b'/pycache'):
        assert _source_metadata_dirs(scratch, pycache_prefix=prefix) == (
            (source,), ('sys.pycache_prefix = <uninspectable exemption source>',))
    # An absent or relative prefix needs no mirror and is not refused.
    for prefix in (None, 'relative-pycache', ''):
        assert _source_metadata_dirs(scratch, pycache_prefix=prefix) == ((source,), ())

    # Non-exact license locations: refused, never used, no method called.
    stdlib_label, os_label = 'sys._stdlib_dir', 'os.__file__'
    for value in (HostileStr(str(tmp_path)), HostileStr(''), PathLike(), b'/stdlib', 'relative'):
        assert _license_files(((stdlib_label, value), (os_label, str(tmp_path / 'os.py')))) == (
            (), (f'{stdlib_label} = <uninspectable exemption source>',))
        assert _license_files(((stdlib_label, None), (os_label, value))) == (
            (), (f'{os_label} = <uninspectable exemption source>',))
    files, refused = _license_files(((stdlib_label, PathLike()), (os_label, None)))
    assert calls == []

    # The scan reports every refused exemption source as a finding.
    assert _settings_paths_in_roots([probe], (root,), exempt_files=files, refused=refused) == sorted([
        f'positive_control.SETTINGS = {root / "planted-settings"}', *refused])
    # Exact absolute strings are accepted and resolved.
    assert _license_files(((stdlib_label, str(tmp_path / 'lib' / 'python3')), (os_label, None))) == (
        tuple(directory / name for directory in (root / 'lib', root / 'lib' / 'python3')
              for name in ('LICENSE.txt', 'LICENSE')), ())
    assert calls == []


def test_scan_skips_relative_paths_from_any_working_directory(monkeypatch, tmp_path):
    # The scan resolves only absolute strings. Pin the working directory
    # inside the scan root, so relative strings, including the license
    # helper's ./LICENSE.txt and ./LICENSE, would resolve onto plants there if
    # the gate admitted them, whatever directory pytest runs from.
    root = tmp_path.resolve()
    cwd = root / 'cwd'
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    here = str(root / 'install' / 'lib' / 'python3')
    files, refused = _license_files((('sys._stdlib_dir', here), ('os.__file__', None)))
    assert refused == () and not any(location.is_relative_to(cwd) for location in files)
    probe = ModuleType('positive_control')
    probe.__builtins__ = {'license': _license_helper(here)}
    probe.RELATIVE = ('planted-settings', './planted-state', 'config/settings.json')
    probe.SETTINGS = str(cwd / 'planted-absolute')
    assert _settings_paths_in_roots([probe], (root,), exempt_files=files) == [
        f'positive_control.SETTINGS = {cwd / "planted-absolute"}']
    # Control: the relative values name paths inside the root from here.
    assert all((Path.cwd() / value).resolve().is_relative_to(root)
               for value in probe.RELATIVE + (os.path.join(os.curdir, 'LICENSE'),))


def _check_relative_locations_are_refused(home, stdlib_spellings, os_file_spellings,
                                          package_spellings):
    # Run with the working directory and HOME (in the environment) both at
    # `home`. Each spelling, taken as a location, would put $HOME/LICENSE
    # among the exempt files or make a directory in HOME the package source
    # directory. Each is refused instead, with its named finding, and the
    # plant there is still reported.
    assert Path.cwd() == home and os.path.expanduser('~') == str(home)
    assert os.path.expandvars('$HOME') == os.path.expandvars('${HOME}') == str(home)
    planted = str(home / 'LICENSE')
    finding = '{} = <uninspectable exemption source>'
    cases = [((('sys._stdlib_dir', value), ('os.__file__', None)), 'sys._stdlib_dir', value)
             for value in stdlib_spellings] + [
             ((('sys._stdlib_dir', None), ('os.__file__', value)), 'os.__file__',
              os.path.dirname(value)) for value in os_file_spellings]
    for locations, label, here in cases:
        files, refused = _license_files(locations)
        assert (files, refused) == ((), (finding.format(label),)), locations
        probe = ModuleType('positive_control')
        probe.PLANTED = planted
        assert _settings_paths_in_roots([probe], (home,), exempt_files=files,
                                        refused=refused) == sorted([
            f'positive_control.PLANTED = {planted}', finding.format(label)]), locations
        # Control: resolved from here (with a leading '~' and $HOME expanded
        # to HOME), the refused location names $HOME/LICENSE.
        here = os.path.expandvars(os.path.expanduser(here))
        assert Path(planted) in {Path(os.path.join(directory, 'LICENSE')).resolve()
                                 for directory in (os.path.join(here, os.pardir), here)}, locations
    source_plant = str(home / 'scratch_pkg' / 'settings.json')
    for value in package_spellings:
        scratch = ModuleType('scratch_pkg')
        scratch.__file__ = value
        dirs, refused = _source_metadata_dirs(scratch, pycache_prefix=None)
        assert (dirs, refused) == ((), (finding.format('package.__file__'),)), value
        probe = ModuleType('positive_control')
        probe.SETTINGS = source_plant
        assert _settings_paths_in_roots([probe], (home,), exempt=dirs, exempt_files=(),
                                        refused=refused) == sorted([
            f'positive_control.SETTINGS = {source_plant}',
            finding.format('package.__file__')]), value
        # Control: resolved from here (with a leading '~' and $HOME expanded
        # to HOME), the refused __file__'s directory holds the plant.
        directory = os.path.dirname(os.path.expandvars(os.path.expanduser(value)))
        assert Path(source_plant).is_relative_to(Path(directory).resolve()), value


_HOME_NAME = 'home'


def _chdir_home(monkeypatch, tmp_path):
    home = tmp_path.resolve() / _HOME_NAME
    home.mkdir()
    monkeypatch.chdir(home)
    monkeypatch.setenv('HOME', str(home))
    return home


_UP = os.pardir
_BACK = os.path.join(os.pardir, _HOME_NAME)
# Relative location spellings, read with the working directory and HOME
# (in the environment) both at the test's HOME: (sys._stdlib_dir,
# os.__file__, package __file__) spellings per class. Each class is also in
# the generated corpus below, which checks the gate's property over every
# spelling, not only these.
_RELATIVE_LOCATION_SPELLINGS = {
    # Bare, './'-anchored, '.' itself, or '..' back into HOME.
    'relative': (
        ('python3', './python3', '.', './', os.path.join(_BACK, 'python3')),
        ('python3/os.py', './python3/os.py', './os.py', os.path.join(_BACK, 'python3', 'os.py')),
        ('scratch_pkg/__init__.py', './scratch_pkg/__init__.py', './__init__.py',
         os.path.join(_BACK, 'scratch_pkg', '__init__.py'))),
    'trailing-dotdot': (
        (os.path.join('python3', _UP), os.path.join('python3', 'lib', _UP, _UP)),
        (os.path.join('python3', _UP), os.path.join('python3', 'os.py', _UP, _UP)),
        (os.path.join('scratch_pkg', _UP),)),
    'inner-dotdot': (
        (os.path.join('python3', _UP, 'python3'), os.path.join('python3', _UP, '')),
        (os.path.join('python3', _UP, 'os.py'), os.path.join('python3', _UP, 'python3', 'os.py')),
        (os.path.join('scratch_pkg', _UP, '__init__.py'),
         os.path.join('scratch_pkg', _UP, 'scratch_pkg', '__init__.py'))),
    'tilde': (
        ('~', '~/', os.path.join('~', 'python3'), os.path.join('~', _BACK, 'python3')),
        (os.path.join('~', 'os.py'), os.path.join('~', 'python3', 'os.py')),
        (os.path.join('~', '__init__.py'), os.path.join('~', 'scratch_pkg', '__init__.py'))),
    'dot-segment': (
        (os.path.join('python3', os.curdir), os.path.join('python3', os.curdir, '')),
        (os.path.join('python3', os.curdir, 'os.py'),),
        (os.path.join('scratch_pkg', os.curdir, '__init__.py'),)),
    'variable': (
        ('$HOME', '${HOME}', os.path.join('$HOME', 'python3')),
        (os.path.join('$HOME', 'os.py'), os.path.join('${HOME}', 'python3', 'os.py')),
        (os.path.join('$HOME', 'scratch_pkg', '__init__.py'),
         os.path.join('${HOME}', 'scratch_pkg', '__init__.py'))),
}
# What puts each spelling in its class.
_RELATIVE_LOCATION_CLASSES = {
    'relative': lambda value: not value.startswith(('~', '$')),
    'trailing-dotdot': lambda value: value.endswith(_UP),
    'inner-dotdot': lambda value: not value.endswith(_UP) and os.sep + _UP in value,
    'tilde': lambda value: value.startswith('~'),
    'dot-segment': lambda value: (value.endswith(os.sep + os.curdir)
                                  or os.sep + os.curdir + os.sep in value),
    'variable': lambda value: value.startswith('$'),
}


def _check_spelling_class(monkeypatch, tmp_path, spelling):
    home = _chdir_home(monkeypatch, tmp_path)
    stdlib, os_file, package = _RELATIVE_LOCATION_SPELLINGS[spelling]
    assert all(map(_RELATIVE_LOCATION_CLASSES[spelling], stdlib + os_file + package)), spelling
    _check_relative_locations_are_refused(home, stdlib, os_file, package)


def test_relative_exemption_locations_are_refused_from_any_working_directory(
        monkeypatch, tmp_path):
    # A relative location would resolve against the working directory. With
    # the working directory at HOME: bare, './'-anchored, '.' itself, or '..'
    # back into HOME.
    _check_spelling_class(monkeypatch, tmp_path, 'relative')


@pytest.mark.parametrize('spelling', ['trailing-dotdot', 'inner-dotdot', 'tilde'])
def test_dotdot_and_tilde_exemption_locations_are_refused(monkeypatch, tmp_path, spelling):
    # Relative too: a location that ends in '..', has '..' inside it, or
    # starts with '~'. Neither the gate nor Path.resolve expands '~', so it
    # names a directory called '~' below the working directory; HOME in the
    # environment is the working directory as well, so expanded it lands
    # there too.
    _check_spelling_class(monkeypatch, tmp_path, spelling)


@pytest.mark.parametrize('spelling', ['dot-segment', 'variable'])
def test_dot_segment_and_variable_exemption_locations_are_refused(monkeypatch, tmp_path,
                                                                  spelling):
    # Relative too: a location ending in '/.' or holding '/./' (for example
    # sys._stdlib_dir 'python3/.', whose parent candidate is $HOME/LICENSE),
    # or starting with '$'. Neither the gate nor Path.resolve expands a
    # variable, so '$HOME' names a directory called '$HOME' below the working
    # directory; expanded, it is the working directory too.
    _check_spelling_class(monkeypatch, tmp_path, spelling)


# The exemption-source gate's property, over a generated corpus rather than a
# list of spellings: every exact absolute str is returned unchanged with no
# finding; every other value (a str that is not absolute, a str subclass, a
# non-str) is refused with its named finding. The oracle for "absolute" is
# POSIX's rule written out, a str starting with '/', never os.path.isabs (the
# gate's own predicate). The suite is POSIX-only: conftest imports resource.
# This is a behavioural check over the corpus classes stated below, each
# character class generated whole from its definition. It does not lock the
# gate (the pin further down does): a wrong gate keyed on an input property
# the corpus does not list passes it, for example a literal the corpus does
# not contain, a length above the longest value that holds the character it
# tests, or working-directory state.
class _AbsoluteStrSubclass(str):
    """Not an exact str, whatever its value: always refused."""


class _BytesSubclass(bytes):
    """Not exact bytes, whatever its value: always refused."""


class _PathLikeValue:
    """An os.PathLike that is not a pathlib path: refused, never fspath'd."""

    def __init__(self, path):
        self.path = path

    def __fspath__(self):
        return self.path

    def __eq__(self, other):
        return type(other) is type(self) and (type(other.path), other.path) == (type(self.path), self.path)

    def __hash__(self):
        return hash((type(self.path), self.path))

    def __repr__(self):
        return f'_PathLikeValue({self.path!r})'


# Every string of length 0 to 4 over these ten characters (11,111 strings).
_CORPUS_ALPHABET = ('.', '/', '~', '$', '\\', 'a', ' ', ':', '{', '\t')
# One to three segments joined by separators, each value also with a leading
# and with a trailing space.
_CORPUS_SEGMENTS = ('', '.', '..', '~', '~root', '$HOME', '${HOME}', '%HOME%', 'python3',
                    'os.py', '__init__.py', 'C:', ' ')
_CORPUS_SEPARATORS = ('/', '//', '/./', '\\')
_CORPUS_SCHEMES = ('file:///abs', 'file://host/abs', 'https://host/abs',
                   'FILE:///abs', 'File:///abs', 'custom:/abs')
# Character classes, each generated whole from its definition over every code
# point, range(0x110000), with the running interpreter's Unicode database,
# never sampled: every c with c.isspace(); category Cc (C0, DEL and C1);
# category Cf; invisible and blank characters by name (a whole word SPACE,
# BLANK, FILLER, INVISIBLE or INHERENT, or VARIATION SELECTOR, GRAPHEME JOINER
# or ZERO WIDTH); slash look-alikes (every c whose NFKC form contains '/', and
# every c whose name contains SOLIDUS or SLASH); quotation marks (categories
# Pi and Pf, and names containing QUOTATION or APOSTROPHE); and astral code
# points (U+10000, U+1F600, U+E0001, U+10FFFF and, for each astral plane that
# has an assigned code point, one not in Cn, the plane's first and last code
# point and its first and last assigned one: planes 1, 2, 3, 14, 15 and 16).
# The database differs between interpreters (Unicode 14.0.0 on 3.11, 15.0.0 on
# 3.12, 16.0.0 on 3.14), so the census below pins each of these classes per
# database version.
_INVISIBLE_NAME = re.compile(r'\b(?:SPACE|BLANK|FILLER|INVISIBLE|INHERENT)\b'
                             r'|VARIATION SELECTOR|GRAPHEME JOINER|ZERO WIDTH')


@lru_cache(maxsize=None)
def _unicode_classes():
    classes = {name: [] for name in ('whitespace', 'controls', 'format', 'invisible', 'slash',
                                     'quotation')}
    assigned = defaultdict(list)
    for code in range(0x110000):
        char = chr(code)
        category = unicodedata.category(char)
        name = unicodedata.name(char, '')
        if char.isspace():
            classes['whitespace'].append(char)
        if category == 'Cc':
            classes['controls'].append(char)
        if category == 'Cf':
            classes['format'].append(char)
        if _INVISIBLE_NAME.search(name):
            classes['invisible'].append(char)
        if '/' in unicodedata.normalize('NFKC', char) or 'SOLIDUS' in name or 'SLASH' in name:
            classes['slash'].append(char)
        if category in ('Pi', 'Pf') or 'QUOTATION' in name or 'APOSTROPHE' in name:
            classes['quotation'].append(char)
        if code > 0xFFFF and category != 'Cn':
            assigned[code >> 16].append(code)
    astral = {0x10000, 0x1F600, 0xE0001, 0x10FFFF}
    for plane, codes in assigned.items():
        astral.update((plane << 16, plane << 16 | 0xFFFF, codes[0], codes[-1]))
    classes['astral'] = [chr(code) for code in sorted(astral)]
    return {name: tuple(chars) for name, chars in classes.items()}


# Classes that do not depend on the database: ASCII punctuation; every
# surrogate (U+D800-U+DFFF), placed only at the start; the four surrogate
# range endpoints with U+DC80 and U+DCFF (os.fsdecode's surrogateescape
# range), placed everywhere; and three sampled characters, not a class, that
# planted rows below use (an accented letter, a combining accent, a fullwidth
# letter).
_CORPUS_PUNCTUATION = tuple(string.punctuation)
_CORPUS_SURROGATES = tuple(map(chr, range(0xD800, 0xE000)))
_CORPUS_SURROGATE_ENDPOINTS = ('\ud800', '\udbff', '\udc00', '\udfff', '\udc80', '\udcff')
_CORPUS_SAMPLED = ('\xe9', '\u0301', '\uff41')


@lru_cache(maxsize=None)
def _character_classes():
    return {**_unicode_classes(), 'punctuation': _CORPUS_PUNCTUATION,
            'surrogate endpoints': _CORPUS_SURROGATE_ENDPOINTS, 'sampled': _CORPUS_SAMPLED}


@lru_cache(maxsize=None)
def _placed_characters():
    """The union of the character classes, each character once."""
    return tuple(dict.fromkeys(char for chars in _character_classes().values() for char in chars))


# Long values: relative segments of seven letters, longer than 300
# characters, and the same after a leading '/'; the longest ones are longer
# than every length boundary below. Each middle index falls inside a segment
# ('abc' before it, 'defg' after).
_LONG_RELATIVE = ('abcdefg/' * 42)[:-1]
_LONG_ABSOLUTE = '/' + _LONG_RELATIVE
_LONG_MIDDLE = 171
_LONGEST_RELATIVE = ('abcdefg/' * 1025)[:-1]
_LONGEST_ABSOLUTE = '/' + _LONGEST_RELATIVE
_LONGEST_MIDDLE = 4099
# Each placed character c becomes prefix + c + suffix for each pair: at the
# start (alone, before a relative rest, before an absolute rest), right after
# a leading '/' (alone, before more), inside a relative and an absolute
# segment, after an inner '/', and at the end of a relative and of an absolute
# value; then at the start of, right after the leading '/' of, after an inner
# '/' of, inside a segment of, and at the end of the long relative and
# absolute values; and in the longest values at the start (before the
# absolute one) and inside a segment of each.
_CORPUS_PLACEMENTS = (('', ''), ('', 'a'), ('', '/a'), ('/', ''), ('/', 'a'), ('a', 'a'),
                      ('/a', 'a'), ('a/', ''), ('/a/', 'a'), ('a', ''), ('/a', ''),
                      ('', _LONG_RELATIVE), ('', _LONG_ABSOLUTE), ('/', _LONG_RELATIVE),
                      (_LONG_RELATIVE[:8], _LONG_RELATIVE[8:]),
                      (_LONG_ABSOLUTE[:9], _LONG_ABSOLUTE[9:]),
                      (_LONG_RELATIVE[:_LONG_MIDDLE], _LONG_RELATIVE[_LONG_MIDDLE:]),
                      (_LONG_ABSOLUTE[:_LONG_MIDDLE + 1], _LONG_ABSOLUTE[_LONG_MIDDLE + 1:]),
                      (_LONG_RELATIVE, ''), (_LONG_ABSOLUTE, ''), ('', _LONGEST_ABSOLUTE),
                      (_LONGEST_RELATIVE[:_LONGEST_MIDDLE], _LONGEST_RELATIVE[_LONGEST_MIDDLE:]),
                      (_LONGEST_ABSOLUTE[:_LONGEST_MIDDLE + 1],
                       _LONGEST_ABSOLUTE[_LONGEST_MIDDLE + 1:]))
# Every surrogate at the start: alone, before a relative and an absolute rest,
# and right after a leading '/' (alone, before more).
_LEADING_PLACEMENTS = (('', ''), ('', 'a'), ('', '/a'), ('/', ''), ('/', 'a'))
# Long values, each as written and with a leading '/': longer than 300
# characters, slash runs of 5 to 8 at the start and inside, relative prefixes
# longer than four characters, and a component longer than 255 characters.
_CORPUS_LONG = ('a' * 301, 'a/' * 151,
                *('/' * run + 'a' for run in range(5, 9)),
                *('a' + '/' * run + 'a' for run in range(5, 9)),
                './x/y/z/w', '../../../a', 'x/y/z/w/v', 'a/' + 'b' * 256 + '/c')
# Length boundaries: at each length an absolute and a relative value, each as
# one component and as seven-letter segments.
_CORPUS_LENGTHS = (255, 256, 1023, 1024, 4095, 4096, 4097, 8192)


def _length_values(length):
    return ('/' + 'a' * (length - 1), ('/abcdefg' * 1024)[:length],
            'a' * length, ('abcdefg/' * 1024)[:length])


# Values that are not an exact str, over absolute and relative contents where
# they have any: one or more instances of each builtin type a caller could
# pass, a str and a bytes subclass, pathlib's pure and concrete paths and
# another os.PathLike (over str and bytes). Each is refused, whatever it holds.
_CORPUS_NON_STR = (_AbsoluteStrSubclass('/a'), _AbsoluteStrSubclass('a'), None, True, False, 0, 1,
                   0.0, 1.5, 0j, b'/a', b'a', _BytesSubclass(b'/a'), _BytesSubclass(b'a'),
                   bytearray(b'/a'), bytearray(b'a'), memoryview(b'/a'), memoryview(b'a'),
                   ('/a',), ('a',), ['/a'], ['a'], {'/a': '/a'}, {'a': 'a'}, {'/a'}, {'a'},
                   frozenset({'/a'}), frozenset({'a'}), PurePosixPath('/a'), PurePosixPath('a'),
                   PureWindowsPath('/a'), PureWindowsPath('a'), Path('/a'), Path('a'),
                   _PathLikeValue('/a'), _PathLikeValue('a'), _PathLikeValue(b'/a'),
                   _PathLikeValue(b'a'))


@lru_cache(maxsize=None)
def _exhaustive_corpus():
    return tuple(''.join(chars) for length in range(5)
                 for chars in product(_CORPUS_ALPHABET, repeat=length))


@lru_cache(maxsize=None)
def _grammar_corpus():
    values = []
    for count in (1, 2, 3):
        for segments in product(_CORPUS_SEGMENTS, repeat=count):
            for separators in product(_CORPUS_SEPARATORS, repeat=count - 1):
                value = segments[0] + ''.join(map(str.__add__, separators, segments[1:]))
                values.extend((value, ' ' + value, value + ' '))
    return tuple(dict.fromkeys(values))


@lru_cache(maxsize=None)
def _class_corpus():
    placed = [prefix + char + suffix for char in _placed_characters()
              for prefix, suffix in _CORPUS_PLACEMENTS]
    leading = [prefix + char + suffix for char in _CORPUS_SURROGATES
               for prefix, suffix in _LEADING_PLACEMENTS]
    long_values = [spelling for value in _CORPUS_LONG for spelling in (value, '/' + value)]
    lengths = [value for length in _CORPUS_LENGTHS for value in _length_values(length)]
    return tuple(dict.fromkeys(placed + leading + long_values + lengths))


@lru_cache(maxsize=None)
def _gate_corpus():
    spelled = [value for groups in _RELATIVE_LOCATION_SPELLINGS.values()
               for group in groups for value in group] + ['relative']
    strings = tuple(dict.fromkeys(_exhaustive_corpus() + _grammar_corpus() + _class_corpus()
                                  + tuple(spelled) + _CORPUS_SCHEMES))
    # Never deduplicated against the strings: a subclass equals its value.
    return strings + _CORPUS_NON_STR


def _is_absolute(value):
    """The oracle: an exact str starting with '/' (POSIX)."""
    return type(value) is str and value[:1] == '/'


_GATE_LABEL = 'corpus.location'
_REFUSE_ACCEPTED = 'must-refuse value accepted'
_REFUSE_UNREPORTED = 'must-refuse value without its finding'
_ABSOLUTE_CHANGED = 'absolute value not returned unchanged'
_ABSOLUTE_REPORTED = 'absolute value with a finding'


def _gate_violations(gate, corpus):
    """(value, reason) for each value where `gate` breaks the property."""
    finding = f'{_GATE_LABEL} = <uninspectable exemption source>'
    violations = []
    for value in corpus:
        refused = []
        try:
            result = gate(value, _GATE_LABEL, refused)
        except Exception as error:
            violations.append((value, f'raised {type(error).__name__}'))
            continue
        if _is_absolute(value):
            if result is not value:
                violations.append((value, _ABSOLUTE_CHANGED))
            if refused != []:
                violations.append((value, _ABSOLUTE_REPORTED))
        else:
            if result is not None:
                violations.append((value, _REFUSE_ACCEPTED))
            if refused != [finding]:
                violations.append((value, _REFUSE_UNREPORTED))
    return violations


def _call_counting_values(calls):
    """A str subclass and an os.PathLike that record calls made on them.

    __getattribute__ records each attribute looked up on an instance
    (startswith, strip, ...). Special methods that builtins call through the
    type bypass it, so those listed below (repr, str, format, len, indexing,
    iteration, in, comparisons, hash, +, *, %, bool, fspath) record their own
    calls; one not listed is not seen.
    """
    def recorded(name, call):
        def method(self, *args):
            calls.append(name)
            return call(self, *args)
        return method

    class CountingStr(str):
        def __getattribute__(self, name):
            calls.append(name)
            return str.__getattribute__(self, name)

        def __fspath__(self):
            calls.append('__fspath__')
            return str.__str__(self)

        def __bool__(self):
            calls.append('__bool__')
            return str.__len__(self) > 0

    class CountingPathLike:
        def __init__(self, path):
            self.path = path

        def __getattribute__(self, name):
            calls.append(name)
            return object.__getattribute__(self, name)

        def __fspath__(self):
            calls.append('__fspath__')
            return object.__getattribute__(self, 'path')

        def __bool__(self):
            calls.append('__bool__')
            return True

    for name in ('__repr__', '__str__', '__format__', '__len__', '__getitem__', '__iter__',
                 '__contains__', '__eq__', '__ne__', '__lt__', '__le__', '__gt__', '__ge__',
                 '__hash__', '__add__', '__mul__', '__rmul__', '__mod__'):
        setattr(CountingStr, name, recorded(name, getattr(str, name)))
    for name in ('__repr__', '__str__', '__format__', '__eq__', '__ne__', '__lt__', '__le__',
                 '__gt__', '__ge__', '__hash__'):
        setattr(CountingPathLike, name, recorded(name, getattr(object, name)))
    return CountingStr, CountingPathLike


# The gate's specification, written out: the type partition (each over an
# absolute content) and the POSIX prefix partition of exact str values, each
# row with the return value and the `refused` list the gate must give. The
# str subclass and os.PathLike rows record the calls made on the value
# (attribute lookups and the special methods _call_counting_values lists) and
# require none. A behavioural table, not a lock: see the pin below.
_SPEC_LABEL = 'spec.location'
_SPEC_FINDING = 'spec.location = <uninspectable exemption source>'
_GATE_SPECIFICATION = {
    'type-exact-str': (lambda types: '/a', '/a', []),
    'type-str-subclass': (lambda types: types[0]('/a'), None, [_SPEC_FINDING]),
    'type-bytes': (lambda types: b'/a', None, [_SPEC_FINDING]),
    'type-bytearray': (lambda types: bytearray(b'/a'), None, [_SPEC_FINDING]),
    'type-pathlike-over-str': (lambda types: types[1]('/a'), None, [_SPEC_FINDING]),
    'type-pathlike-over-bytes': (lambda types: types[1](b'/a'), None, [_SPEC_FINDING]),
    'type-none': (lambda types: None, None, [_SPEC_FINDING]),
    'prefix-empty': (lambda types: '', None, [_SPEC_FINDING]),
    'prefix-slash': (lambda types: '/', '/', []),
    'prefix-double-slash': (lambda types: '//', '//', []),
    'prefix-slash-a': (lambda types: '/a', '/a', []),
    'prefix-a': (lambda types: 'a', None, [_SPEC_FINDING]),
    'prefix-a-slash': (lambda types: 'a/', None, [_SPEC_FINDING]),
    'prefix-space-slash-a': (lambda types: ' /a', None, [_SPEC_FINDING]),
    'prefix-dot-slash-a': (lambda types: './a', None, [_SPEC_FINDING]),
    'scheme-file-absolute': (lambda types: 'file:///abs', None, [_SPEC_FINDING]),
    'scheme-file-host': (lambda types: 'file://host/abs', None, [_SPEC_FINDING]),
    'scheme-https': (lambda types: 'https://host/abs', None, [_SPEC_FINDING]),
    'scheme-file-upper': (lambda types: 'FILE:///abs', None, [_SPEC_FINDING]),
    'scheme-file-mixed': (lambda types: 'File:///abs', None, [_SPEC_FINDING]),
    'scheme-custom': (lambda types: 'custom:/abs', None, [_SPEC_FINDING]),
}


@pytest.mark.parametrize('row', list(_GATE_SPECIFICATION))
def test_exemption_gate_specification_table(row):
    calls = []
    types = _call_counting_values(calls)
    make, expected_return, expected_refused = _GATE_SPECIFICATION[row]
    value = make(types)
    refused = []
    result = _isolation._exact_location(value, _SPEC_LABEL, refused)
    made = list(calls)
    assert made == [], (row, made)
    assert refused == expected_refused, row
    if expected_return is None:
        assert result is None, row
    else:
        assert type(result) is str and result == expected_return and result is value, row
    # Control: the recorders see the calls os.path.isabs, os.fspath and repr make.
    os.path.isabs(types[0]('/a'))
    os.fspath(types[1]('/a'))
    repr(types[0]('a'))
    assert {'startswith', '__fspath__', '__repr__'} <= set(calls), calls


def test_exemption_gate_property_over_generated_corpus():
    corpus = _gate_corpus()
    assert os.path.__name__ == 'posixpath'  # The oracle is POSIX's rule.
    # The oracle only looks at the first character: a slash look-alike or a
    # lone surrogate there is relative; after a leading '/', either is absolute.
    assert [_is_absolute(value) for value in ('\u2215a', '\uff0fa', '\ud800', '/\u2215', '/\ud800')] == [
        False, False, False, True, True]
    assert {True, False} == set(map(_is_absolute, corpus))
    # Looked up now, so a changed _isolation._exact_location is what is checked.
    assert _gate_violations(_isolation._exact_location, corpus) == []


def _class_checksum(chars):
    """sha256 of the sorted code points, upper-case hex joined by commas."""
    return hashlib.sha256(','.join('%X' % ord(char) for char in sorted(chars)).encode()).hexdigest()


# Each class's size and checksum, written out: the Unicode classes per database
# version (this interpreter's is looked up; a version with no written census,
# such as Unicode 15.1.0 on 3.13, skips with a reason naming it), the others
# once.
_WRITTEN_UNICODE_CLASS_CENSUS = {
    '14.0.0': {
        'whitespace': (29, 'f147c4a81b1376103c9c425c2fcddec815fb08ef828b031d83cd4f58555c9fde'),
        'controls': (65, '127663b014d40adffd447d1a7b2bc25fb1b6e96f771f7cc35ec77d9656b7a6af'),
        'format': (163, '3d6b02f16eac05307cea3495813240beac2273c1698042a52275445873ef9f5c'),
        'invisible': (303, 'c099cc5574f80acf87d8fbf45e6f43421550bc82737d16cc5049be1e696ae1ac'),
        'slash': (51, 'aa0adcf42384c9b2fba6328a7c8b2dfa4844dfa38d9954b54d12af04b54f3028'),
        'quotation': (51, 'da03b2552a28f6f48f203cb73b5bb6f19fa5f81691abe43719d8af17acf2e678'),
        'astral': (20, '82bafd83e3c77e42640b5906e83380d9feb8f9b9bcfeeaf853f46b72ccfb43ad'),
    },
    '15.0.0': {
        'whitespace': (29, 'f147c4a81b1376103c9c425c2fcddec815fb08ef828b031d83cd4f58555c9fde'),
        'controls': (65, '127663b014d40adffd447d1a7b2bc25fb1b6e96f771f7cc35ec77d9656b7a6af'),
        'format': (170, 'fedc94e75fd491dddff1a9c2f428247de73ea991fc13681e030ef3f49f958868'),
        'invisible': (306, '6366c5832a5c97cfcffb689e779a83d6d86f6d9957558a6fe0b603d91c7faa83'),
        'slash': (51, 'aa0adcf42384c9b2fba6328a7c8b2dfa4844dfa38d9954b54d12af04b54f3028'),
        'quotation': (51, 'da03b2552a28f6f48f203cb73b5bb6f19fa5f81691abe43719d8af17acf2e678'),
        'astral': (20, 'e02586dd3c869c785cd20e2c5df5d956e01dcb2c471ef766750f3f61a49b1d3b'),
    },
    '16.0.0': {
        'whitespace': (29, 'f147c4a81b1376103c9c425c2fcddec815fb08ef828b031d83cd4f58555c9fde'),
        'controls': (65, '127663b014d40adffd447d1a7b2bc25fb1b6e96f771f7cc35ec77d9656b7a6af'),
        'format': (170, 'fedc94e75fd491dddff1a9c2f428247de73ea991fc13681e030ef3f49f958868'),
        'invisible': (306, '6366c5832a5c97cfcffb689e779a83d6d86f6d9957558a6fe0b603d91c7faa83'),
        'slash': (51, 'aa0adcf42384c9b2fba6328a7c8b2dfa4844dfa38d9954b54d12af04b54f3028'),
        'quotation': (51, 'da03b2552a28f6f48f203cb73b5bb6f19fa5f81691abe43719d8af17acf2e678'),
        'astral': (20, 'e02586dd3c869c785cd20e2c5df5d956e01dcb2c471ef766750f3f61a49b1d3b'),
    },
}
_WRITTEN_CLASS_CENSUS = {
    'punctuation': (32, '79c6899abf25ae8878574d641ab416520f8236ee2bfd7c9481e57cdf3fe90c80'),
    'surrogate endpoints': (6, 'e4029ca8833cf34ba5a83ec1ae951d93c98c52ced7937ff386e49254b905f127'),
    'sampled': (3, '258e1b200cc387dbf942001a49705e42611b76b768b39b7aa4ca4ac10a5aae0e'),
}
# Members the planted rows and the property depend on, in every database.
_REQUIRED_CLASS_MEMBERS = {
    'whitespace': ' \t\n\x0b\x0c\r\x1c\x1d\x1e\x1f\x85\xa0\u1680\u2000\u200a\u2028\u2029'
                  '\u202f\u205f\u3000',
    'controls': '\x00\t\n\x1f\x7f\x80\x85\x9f',
    'format': '\xad\u200b\u200e\u2060\ufeff\U000e0001\U000e007f',
    'invisible': ' \xa0\u115f\u200b\u2800\u3164\ufe0f\ufeff\U000e0100',
    'slash': '/\\\u2044\u2215\u29f8\u2afd\uff0f',
    'quotation': '"\'\xab\u2018\u201c\uff02',
    'astral': '\U00010000\U0001f600\U000e0001\U0010ffff',
    'punctuation': '!"#$%&\'()*+,-./:;<=>?@[\\]^_`{|}~',
    'surrogate endpoints': '\ud800\udbff\udc00\udfff\udc80\udcff',
    'sampled': '\xe9\u0301\uff41',
}


def _check_unicode_class_census(version):
    written = _WRITTEN_UNICODE_CLASS_CENSUS.get(version)
    if written is None:
        pytest.skip(f'no written class census for Unicode {version}: '
                    'its generated Unicode classes are not pinned')
    generated = _character_classes()
    assert {name: (len(generated[name]), _class_checksum(generated[name]))
            for name in written} == written


def test_exemption_gate_unicode_classes_match_written_census():
    # The Unicode classes by size and checksum, written out per database
    # version, never rebuilt from the generator: a dropped, substituted or
    # duplicated member, or a class definition change that changes the
    # members, fails it.
    _check_unicode_class_census(unicodedata.unidata_version)


def test_unicode_class_census_skips_an_unwritten_version():
    # Unicode 15.1.0 (Python 3.13, allowed by requires-python) has no written
    # census: it skips, naming the version, rather than failing.
    assert '15.1.0' not in _WRITTEN_UNICODE_CLASS_CENSUS
    with pytest.raises(pytest.skip.Exception, match=r'Unicode 15\.1\.0'):
        _check_unicode_class_census('15.1.0')


def test_exemption_gate_character_classes_match_written_census():
    # The classes that do not depend on the database, by size and checksum,
    # the class names, the members the planted rows depend on, the
    # surrogates, and each class character in each placement.
    generated = _character_classes()
    unicode_names = {name for census in _WRITTEN_UNICODE_CLASS_CENSUS.values() for name in census}
    assert set(generated) == unicode_names | set(_WRITTEN_CLASS_CENSUS)
    assert {name: (len(generated[name]), _class_checksum(generated[name]))
            for name in _WRITTEN_CLASS_CENSUS} == _WRITTEN_CLASS_CENSUS
    for name, members in _REQUIRED_CLASS_MEMBERS.items():
        assert set(members) <= set(generated[name]), name
    assert (len(_CORPUS_SURROGATES), _class_checksum(_CORPUS_SURROGATES)) == (
        2048, '784d53ad35e488f453086339514cedebf103abab3d20921fe8c5f32eeaa2a954')
    # Every class character is in every placement, every surrogate at the start.
    strings = set(_class_corpus())
    assert {prefix + char + suffix for chars in generated.values() for char in chars
            for prefix, suffix in _CORPUS_PLACEMENTS} <= strings
    assert {prefix + chr(code) + suffix for code in range(0xD800, 0xE000)
            for prefix, suffix in (('', ''), ('', 'a'), ('', '/a'), ('/', ''), ('/', 'a'))} <= strings


def test_exemption_gate_corpus_matches_written_out_sets():
    # A census of the other generator inputs against sets written out here,
    # never rebuilt from the tuples: a dropped, substituted or duplicated
    # member fails it. These inputs are compared by size and membership, so
    # reordering one does not fail it; the non-str values at the end are
    # compared as a list, in order, so reordering them does.
    long_relative, longest_relative = ('abcdefg/' * 42)[:-1], ('abcdefg/' * 1025)[:-1]
    long_absolute, longest_absolute = '/' + long_relative, '/' + longest_relative
    written = {
        '_CORPUS_ALPHABET': {'.', '/', '~', '$', '\\', 'a', ' ', ':', '{', '\t'},
        '_CORPUS_SEGMENTS': {'', '.', '..', '~', '~root', '$HOME', '${HOME}', '%HOME%',
                             'python3', 'os.py', '__init__.py', 'C:', ' '},
        '_CORPUS_SEPARATORS': {'/', '//', '/./', '\\'},
        '_CORPUS_SCHEMES': {'file:///abs', 'file://host/abs', 'https://host/abs',
                           'FILE:///abs', 'File:///abs', 'custom:/abs'},
        '_CORPUS_PLACEMENTS': {('', ''), ('', 'a'), ('', '/a'), ('/', ''), ('/', 'a'), ('a', 'a'),
                               ('/a', 'a'), ('a/', ''), ('/a/', 'a'), ('a', ''), ('/a', ''),
                               ('', long_relative), ('', long_absolute), ('/', long_relative),
                               (long_relative[:8], long_relative[8:]),
                               (long_absolute[:9], long_absolute[9:]),
                               (long_relative[:171], long_relative[171:]),
                               (long_absolute[:172], long_absolute[172:]),
                               (long_relative, ''), (long_absolute, ''), ('', longest_absolute),
                               (longest_relative[:4099], longest_relative[4099:]),
                               (longest_absolute[:4100], longest_absolute[4100:])},
        '_LEADING_PLACEMENTS': {('', ''), ('', 'a'), ('', '/a'), ('/', ''), ('/', 'a')},
        '_CORPUS_LONG': {'a' * 301, 'a/' * 151, '/////a', '//////a', '///////a', '////////a',
                         'a/////a', 'a//////a', 'a///////a', 'a////////a', './x/y/z/w',
                         '../../../a', 'x/y/z/w/v', 'a/' + 'b' * 256 + '/c'},
        '_CORPUS_LENGTHS': {255, 256, 1023, 1024, 4095, 4096, 4097, 8192},
    }
    for name, members in written.items():
        generated = globals()[name]
        assert (len(generated), set(generated)) == (len(members), members), name
    # Each middle index is inside a segment; the long values are longer than
    # 300 characters, the longest longer than every length boundary.
    assert long_relative[168:171] == 'abc' and long_relative[171:175] == 'defg'
    assert longest_relative[4096:4099] == 'abc' and longest_relative[4099:4103] == 'defg'
    assert (len(long_relative), len(longest_relative)) == (335, 8199)
    corpus = _gate_corpus()
    strings = {value for value in corpus if type(value) is str}
    # The exhaustive part is the product over the written-out alphabet.
    exhaustive = {''.join(chars) for length in range(5)
                  for chars in product('./~$\\a :{\t', repeat=length)}
    assert len(exhaustive) == 11111 and set(_exhaustive_corpus()) == exhaustive <= strings
    assert len(_exhaustive_corpus()) == 11111
    # Every written-out segment is a value, every long value is there both
    # ways, and every length boundary as absolute and relative values.
    assert written['_CORPUS_SEGMENTS'] <= strings
    assert written['_CORPUS_SCHEMES'] <= strings
    assert {spelling for value in written['_CORPUS_LONG']
            for spelling in (value, '/' + value)} <= strings
    lengths = {('/' + 'a' * (length - 1), ('/abcdefg' * 1024)[:length], 'a' * length,
                ('abcdefg/' * 1024)[:length]) for length in written['_CORPUS_LENGTHS']}
    assert {value for values in lengths for value in values} <= strings
    assert {len(value) for values in lengths for value in values} == written['_CORPUS_LENGTHS']
    # The non-str values, by exact type and content, in order.
    assert [(type(value), value) for value in corpus if type(value) is not str] == [
        (_AbsoluteStrSubclass, '/a'), (_AbsoluteStrSubclass, 'a'), (type(None), None),
        (bool, True), (bool, False), (int, 0), (int, 1), (float, 0.0), (float, 1.5),
        (complex, 0j), (bytes, b'/a'), (bytes, b'a'), (_BytesSubclass, b'/a'),
        (_BytesSubclass, b'a'), (bytearray, b'/a'), (bytearray, b'a'), (memoryview, b'/a'),
        (memoryview, b'a'), (tuple, ('/a',)), (tuple, ('a',)), (list, ['/a']), (list, ['a']),
        (dict, {'/a': '/a'}), (dict, {'a': 'a'}), (set, {'/a'}), (set, {'a'}),
        (frozenset, frozenset({'/a'})), (frozenset, frozenset({'a'})),
        (PurePosixPath, PurePosixPath('/a')), (PurePosixPath, PurePosixPath('a')),
        (PureWindowsPath, PureWindowsPath('/a')), (PureWindowsPath, PureWindowsPath('a')),
        (PosixPath, Path('/a')), (PosixPath, Path('a')),
        (_PathLikeValue, _PathLikeValue('/a')), (_PathLikeValue, _PathLikeValue('a')),
        (_PathLikeValue, _PathLikeValue(b'/a')), (_PathLikeValue, _PathLikeValue(b'a'))]


def test_corpus_oracle_does_not_use_os_path_isabs(monkeypatch):
    # The corpus check's oracle is independent of os.path.isabs: widen that
    # predicate itself (to a leading '$') and the unchanged gate breaks the
    # property on the corpus, only by accepting, without a finding, values it
    # must refuse.
    real_isabs = os.path.isabs
    monkeypatch.setattr(os.path, 'isabs', lambda path: real_isabs(path) or (
        type(path) is str and path.startswith('$')))
    violations = _gate_violations(_isolation._exact_location, _gate_corpus())
    monkeypatch.undo()
    assert ('$HOME', _REFUSE_ACCEPTED) in violations
    assert ('${HOME}', _REFUSE_ACCEPTED) in violations
    assert {reason for _, reason in violations} == {_REFUSE_ACCEPTED, _REFUSE_UNREPORTED}


def _planted_gate(accepts, result=lambda value: value, finding_on_accept=False,
                  finding_on_refuse=True):
    def gate(value, label, refused):
        if accepts(value):
            if finding_on_accept:
                refused.append(f'{label} = <uninspectable exemption source>')
            return result(value)
        if finding_on_refuse:
            refused.append(f'{label} = <uninspectable exemption source>')
        return None
    return gate


def _exact_str(value):
    return type(value) is str


# Wrong gates, each with a witness written here by hand (so the expected
# failure does not come from the checker), the reason it must be caught and
# any further witnesses for the same reason.
# Widenings admit a relative spelling; narrowings change or refuse an
# absolute one. isabs(normpath(value)) is not here: normpath never makes a
# relative str absolute, so that gate is equivalent to the shipped one.
_PLANTED_GATES = {
    'endswith-slash-dot': (_planted_gate(
        lambda v: _exact_str(v) and (os.path.isabs(v) or v.endswith('/.'))),
        'python3/.', _REFUSE_ACCEPTED),
    'inner-slash-dot-slash': (_planted_gate(
        lambda v: _exact_str(v) and (os.path.isabs(v) or '/./' in v)),
        'python3/./os.py', _REFUSE_ACCEPTED),
    'inner-double-slash': (_planted_gate(
        lambda v: _exact_str(v) and (os.path.isabs(v) or '//' in v[1:])),
        'a//a', _REFUSE_ACCEPTED),
    'strip': (_planted_gate(lambda v: _exact_str(v) and os.path.isabs(v.strip())),
              ' /a', _REFUSE_ACCEPTED),
    'lstrip': (_planted_gate(lambda v: _exact_str(v) and os.path.isabs(v.lstrip())),
               '\t/a', _REFUSE_ACCEPTED),
    'surrounding-space': (_planted_gate(
        lambda v: _exact_str(v) and (os.path.isabs(v) or v != v.strip())),
        'a ', _REFUSE_ACCEPTED),
    'expandvars': (_planted_gate(lambda v: _exact_str(v) and os.path.isabs(os.path.expandvars(v))),
                   '$HOME', _REFUSE_ACCEPTED, '${HOME}'),
    'startswith-dollar': (_planted_gate(
        lambda v: _exact_str(v) and (os.path.isabs(v) or v.startswith('$'))),
        '$HOME', _REFUSE_ACCEPTED),
    'startswith-backslash': (_planted_gate(
        lambda v: _exact_str(v) and (os.path.isabs(v) or v.startswith('\\'))),
        '\\python3', _REFUSE_ACCEPTED),
    'ntpath-isabs': (_planted_gate(
        lambda v: _exact_str(v) and (os.path.isabs(v) or ntpath.isabs(v))),
        'C:\\python3', _REFUSE_ACCEPTED),
    'empty': (_planted_gate(lambda v: _exact_str(v) and (os.path.isabs(v) or v == '')),
              '', _REFUSE_ACCEPTED),
    'startswith-dot-slash': (_planted_gate(
        lambda v: _exact_str(v) and (os.path.isabs(v) or v.startswith('./'))),
        './a', _REFUSE_ACCEPTED),
    'startswith-dot': (_planted_gate(
        lambda v: _exact_str(v) and (os.path.isabs(v) or v.startswith('.'))),
        '.', _REFUSE_ACCEPTED),
    'relative-with-separator': (_planted_gate(
        lambda v: _exact_str(v) and (os.path.isabs(v) or os.sep in v)),
        'a/a', _REFUSE_ACCEPTED),
    'endswith-dotdot': (_planted_gate(
        lambda v: _exact_str(v) and (os.path.isabs(v) or v.endswith('..'))),
        'python3/..', _REFUSE_ACCEPTED),
    'inner-dotdot': (_planted_gate(
        lambda v: _exact_str(v) and (os.path.isabs(v) or '/..' in v)),
        'python3/../os.py', _REFUSE_ACCEPTED),
    'expanduser': (_planted_gate(lambda v: _exact_str(v) and os.path.isabs(os.path.expanduser(v))),
                   '~/a', _REFUSE_ACCEPTED),
    'startswith-tilde': (_planted_gate(
        lambda v: _exact_str(v) and (os.path.isabs(v) or v.startswith('~'))),
        '~', _REFUSE_ACCEPTED),
    'tilde-user': (_planted_gate(
        lambda v: _exact_str(v) and (os.path.isabs(v) or (v[:1] == '~' and v[1:2] not in ('', '/')))),
        '~root', _REFUSE_ACCEPTED),
    'endswith-slash': (_planted_gate(
        lambda v: _exact_str(v) and (os.path.isabs(v) or v.endswith('/'))),
        'a/', _REFUSE_ACCEPTED),
    'abspath': (_planted_gate(lambda v: _exact_str(v) and os.path.isabs(os.path.abspath(v))),
                'a', _REFUSE_ACCEPTED),
    'percent-variable': (_planted_gate(
        lambda v: _exact_str(v) and (os.path.isabs(v) or v.startswith('%'))),
        '%HOME%', _REFUSE_ACCEPTED),
    'lstrip-line-controls': (_planted_gate(
        lambda v: _exact_str(v) and os.path.isabs(v.lstrip('\n\r\v\f'))),
        '\n/a', _REFUSE_ACCEPTED, '\r/a', '\v/a', '\f/a'),
    'lstrip-nul': (_planted_gate(lambda v: _exact_str(v) and os.path.isabs(v.lstrip('\x00'))),
                   '\x00/a', _REFUSE_ACCEPTED),
    'lstrip-vt-ff-nbsp': (_planted_gate(
        lambda v: _exact_str(v) and os.path.isabs(v.lstrip('\x0b\x0c\xa0'))),
        '\xa0/a', _REFUSE_ACCEPTED, '\x0b/a', '\x0c/a'),
    'startswith-nul': (_planted_gate(
        lambda v: _exact_str(v) and (os.path.isabs(v) or v.startswith('\x00'))),
        '\x00', _REFUSE_ACCEPTED),
    'startswith-newline': (_planted_gate(
        lambda v: _exact_str(v) and (os.path.isabs(v) or v.startswith('\n'))),
        '\n', _REFUSE_ACCEPTED),
    'division-slash': (_planted_gate(
        lambda v: _exact_str(v) and (os.path.isabs(v) or v[:1] == '\u2215')),
        '\u2215', _REFUSE_ACCEPTED),
    'slash-look-alikes': (_planted_gate(
        lambda v: _exact_str(v) and (os.path.isabs(v) or v.startswith(('\u2215', '\uff0f')))),
        '\uff0fa', _REFUSE_ACCEPTED, '\u2215a'),
    'nfkc': (_planted_gate(
        lambda v: _exact_str(v) and os.path.isabs(unicodedata.normalize('NFKC', v))),
        '\uff0f/a', _REFUSE_ACCEPTED, '\uff0fa'),
    'long-value': (_planted_gate(
        lambda v: _exact_str(v) and (os.path.isabs(v) or len(v) > 300)),
        'a' * 301, _REFUSE_ACCEPTED, 'a/' * 151),
    'bytearray': (_planted_gate(
        lambda v: (_exact_str(v) and os.path.isabs(v)) or (type(v) is bytearray and v[:1] == b'/')),
        bytearray(b'/a'), _REFUSE_ACCEPTED),
    'str-subclass': (_planted_gate(lambda v: isinstance(v, str) and os.path.isabs(v)),
                     _AbsoluteStrSubclass('/a'), _REFUSE_ACCEPTED),
    'silent-refusal': (_planted_gate(lambda v: _exact_str(v) and os.path.isabs(v),
                                     finding_on_refuse=False),
                       'a', _REFUSE_UNREPORTED),
    'normpath-result': (_planted_gate(lambda v: _exact_str(v) and os.path.isabs(v),
                                      result=os.path.normpath),
                        '/./a', _ABSOLUTE_CHANGED),
    'refuse-double-slash': (_planted_gate(
        lambda v: _exact_str(v) and os.path.isabs(v) and not v.startswith('//')),
        '//a', _ABSOLUTE_CHANGED),
    'finding-on-accept': (_planted_gate(lambda v: _exact_str(v) and os.path.isabs(v),
                                        finding_on_accept=True),
                          '/a', _ABSOLUTE_REPORTED),
    'ascii-only': (_planted_gate(lambda v: _exact_str(v) and os.path.isabs(v) and v.isascii()),
                   '/\xe9', _ABSOLUTE_CHANGED, '/\ud800'),
    'refuse-slash-run': (_planted_gate(
        lambda v: _exact_str(v) and os.path.isabs(v) and not v.startswith('/////')),
        '/////a', _ABSOLUTE_CHANGED),
    'refuse-nul': (_planted_gate(
        lambda v: _exact_str(v) and os.path.isabs(v) and '\x00' not in v),
        '/\x00', _ABSOLUTE_CHANGED),
    'refuse-newline': (_planted_gate(
        lambda v: _exact_str(v) and os.path.isabs(v) and '\n' not in v),
        '/a\n', _ABSOLUTE_CHANGED),
    'refuse-long': (_planted_gate(
        lambda v: _exact_str(v) and os.path.isabs(v) and len(v) < 200),
        '/' + 'a' * 301, _ABSOLUTE_CHANGED),
    'refuse-leading-e-acute': (_planted_gate(
        lambda v: _exact_str(v) and os.path.isabs(v) and not v.startswith('/\xe9')),
        '/\xe9', _ABSOLUTE_CHANGED),
    'refuse-dotdot-chain': (_planted_gate(
        lambda v: _exact_str(v) and os.path.isabs(v) and '/../../' not in v),
        '/../../../a', _ABSOLUTE_CHANGED),
    'rstrip-newline-result': (_planted_gate(lambda v: _exact_str(v) and os.path.isabs(v),
                                            result=lambda v: v.rstrip('\n')),
                              '/a\n', _ABSOLUTE_CHANGED),
    'nfc-result': (_planted_gate(lambda v: _exact_str(v) and os.path.isabs(v),
                                 result=lambda v: unicodedata.normalize('NFC', v)),
                   '/a\u0301', _ABSOLUTE_CHANGED),
    # One per generated class: a character outside the sampled ones.
    'lstrip-ideographic-space': (_planted_gate(
        lambda v: _exact_str(v) and os.path.isabs(v.lstrip('\u3000'))),
        '\u3000/a', _REFUSE_ACCEPTED),
    'lstrip-c1-control': (_planted_gate(lambda v: _exact_str(v) and os.path.isabs(v.lstrip('\x9f'))),
                          '\x9f/a', _REFUSE_ACCEPTED),
    'lstrip-byte-order-mark': (_planted_gate(
        lambda v: _exact_str(v) and os.path.isabs(v.lstrip('\ufeff'))),
        '\ufeff/a', _REFUSE_ACCEPTED),
    'lstrip-variation-selector': (_planted_gate(
        lambda v: _exact_str(v) and os.path.isabs(v.lstrip('\ufe0f'))),
        '\ufe0f/a', _REFUSE_ACCEPTED),
    'double-solidus': (_planted_gate(
        lambda v: _exact_str(v) and (os.path.isabs(v) or v.startswith('\u2afd'))),
        '\u2afd', _REFUSE_ACCEPTED, '\u2afda'),
    'startswith-at': (_planted_gate(
        lambda v: _exact_str(v) and (os.path.isabs(v) or v.startswith('@'))),
        '@a', _REFUSE_ACCEPTED),
    'lstrip-quotation-marks': (_planted_gate(
        lambda v: _exact_str(v) and os.path.isabs(v.lstrip('\u201c\u201d'))),
        '\u201c/a', _REFUSE_ACCEPTED),
    # A surrogate from inside the range (not an endpoint), checked at the
    # start of the value only: a leading check.
    'leading-inner-surrogate': (_planted_gate(
        lambda v: _exact_str(v) and (os.path.isabs(v) or v[:1] == '\udb7f')),
        '\udb7f', _REFUSE_ACCEPTED, '\udb7fa', '\udb7f/a'),
    'lstrip-language-tag': (_planted_gate(
        lambda v: _exact_str(v) and os.path.isabs(v.lstrip('\U000e0001'))),
        '\U000e0001/a', _REFUSE_ACCEPTED),
    'refuse-last-code-point': (_planted_gate(
        lambda v: _exact_str(v) and os.path.isabs(v) and '\U0010ffff' not in v),
        '/\U0010ffff', _ABSOLUTE_CHANGED),
    'tuple': (_planted_gate(
        lambda v: (_exact_str(v) and os.path.isabs(v)) or (type(v) is tuple and v[:1] == ('/a',))),
        ('/a',), _REFUSE_ACCEPTED),
    'relative-posix-path': (_planted_gate(
        lambda v: (_exact_str(v) and os.path.isabs(v))
        or (type(v) is PosixPath and not os.path.isabs(os.fspath(v)))),
        Path('a'), _REFUSE_ACCEPTED),
    'other-path-like': (_planted_gate(
        lambda v: (_exact_str(v) and os.path.isabs(v))
        or (hasattr(type(v), '__fspath__') and not isinstance(v, PurePath))),
        _PathLikeValue('/a'), _REFUSE_ACCEPTED, _PathLikeValue(b'a')),
    # Pairings of a character with a placement and a length.
    'refuse-newline-past-three': (_planted_gate(
        lambda v: _exact_str(v) and os.path.isabs(v) and not (len(v) > 3 and '\n' in v)),
        '/a\na', _ABSOLUTE_CHANGED),
    'refuse-long-non-ascii': (_planted_gate(
        lambda v: _exact_str(v) and os.path.isabs(v) and not (len(v) > 300 and not v.isascii())),
        '/\xe9' + ('abcdefg/' * 42)[:-1], _ABSOLUTE_CHANGED),
    'lstrip-long': (_planted_gate(
        lambda v: _exact_str(v) and (os.path.isabs(v) or (len(v) > 300 and os.path.isabs(v.lstrip())))),
        ' /' + ('abcdefg/' * 42)[:-1], _REFUSE_ACCEPTED),
    'long-leading-space': (_planted_gate(
        lambda v: _exact_str(v) and (os.path.isabs(v) or (len(v) > 300 and v[:1].isspace()))),
        ' ' + ('abcdefg/' * 42)[:-1], _REFUSE_ACCEPTED),
    'refuse-long-trailing-space': (_planted_gate(
        lambda v: _exact_str(v) and os.path.isabs(v) and not (len(v) > 300 and v[-1:].isspace())),
        '/' + ('abcdefg/' * 42)[:-1] + ' ', _ABSOLUTE_CHANGED),
    'refuse-longest-with-tab': (_planted_gate(
        lambda v: _exact_str(v) and os.path.isabs(v) and not (len(v) > 1000 and '\t' in v)),
        '/' + ('abcdefg/' * 513)[:-5] + '\t' + ('defg/' + 'abcdefg/' * 512)[:-1], _ABSOLUTE_CHANGED),
    'refuse-from-4096': (_planted_gate(
        lambda v: _exact_str(v) and os.path.isabs(v) and len(v) < 4096),
        '/' + 'a' * 4095, _ABSOLUTE_CHANGED, ('/abcdefg' * 512)),
    'accept-over-1000': (_planted_gate(
        lambda v: _exact_str(v) and (os.path.isabs(v) or len(v) > 1000)),
        'a' * 1023, _REFUSE_ACCEPTED),
    'long-component': (_planted_gate(
        lambda v: _exact_str(v) and (os.path.isabs(v) or ('/' in v and max(map(len, v.split('/'))) > 255))),
        'a/' + 'b' * 256 + '/c', _REFUSE_ACCEPTED),
}


@pytest.mark.parametrize('planted', list(_PLANTED_GATES))
def test_planted_wrong_exemption_gates_break_the_property(planted):
    gate, witness, reason, *more_witnesses = _PLANTED_GATES[planted]
    corpus = _gate_corpus()
    violations = _gate_violations(gate, corpus)
    for witness in (witness, *more_witnesses):
        same = lambda value: type(value) is type(witness) and value == witness
        assert any(map(same, corpus)), witness
        assert any(same(value) and why == reason
                   for value, why in violations), (planted, witness, reason)


@pytest.mark.parametrize('site', ['sys._stdlib_dir', 'os.__file__', 'package.__file__'])
def test_generated_relative_locations_are_refused_at_each_call_site(monkeypatch, tmp_path, site):
    # The grammar and character-class parts of the corpus through the real
    # call sites, with the working directory and HOME at the test's HOME:
    # every non-absolute value gives no files or directories and exactly its
    # named finding, with one exception. An empty sys._stdlib_dir is not a
    # location: as in site.py it selects os.__file__ (None here), so it gives
    # nothing and no finding, and that site skips it.
    _chdir_home(monkeypatch, tmp_path)
    expected = ((), (f'{site} = <uninspectable exemption source>',))
    scratch = ModuleType('scratch_pkg')
    checked, wrong = 0, []
    for value in _grammar_corpus() + _class_corpus():
        if _is_absolute(value):
            continue
        if site == 'sys._stdlib_dir':
            if value == '':
                assert _license_files(((site, value), ('os.__file__', None))) == ((), ())
                continue
            result = _license_files(((site, value), ('os.__file__', None)))
        elif site == 'os.__file__':
            result = _license_files((('sys._stdlib_dir', None), (site, value)))
        else:
            scratch.__file__ = value
            result = _source_metadata_dirs(scratch, pycache_prefix=None)
        checked += 1
        if result != expected:
            wrong.append((value, result))
    assert checked > 10000 and wrong == []


# The exemption gate's pin compares _isolation.py's normalised AST with the
# written form and sha256 below, and its compiled top-level functions with
# source compiled by this interpreter across the eight _CODE_FIELDS. These
# detect an honest edit that changes the normalised form or a compiled function;
# a function-body .pyc disagreeing with source is red in the binding comparison.
# Comments, blank lines, redundant parentheses and quote style that preserve the
# AST are ignored. Docstring text and whitespace are content, not layout.
# To edit _isolation.py deliberately, run `python -m pytest
# tests/test_home_isolation.py -k isolation_module_code_is_pinned` from tui/,
# review its diff, then update the written form and sha256 in the same commit.
# The callee check detects a parent-only change of posixpath.isabs/_get_sep:
# globals must be posixpath's, and (co_code, co_consts, co_names, co_varnames)
# must equal an -I -S child of sys.executable started at module setup, after
# conftest import. It trusts sys.executable and cannot see identical-bytecode
# replacements. The named globals and license snapshot also have comparisons.
# Not checked: C-level changes (the interpreter, posix, the str type), or
# patches of objects these comparisons do not name. Also outside these checks:
# module-class overrides (type(os), type(posixpath)), the callees'
# __builtins__, module-level .pyc constants versus source, swaps undone before
# assertions, or a forged sys.executable. Examples outside scope include an
# os.__class__ property, posixpath.__class__.__getattribute__, conftest rebinding
# isabs with a forged executable, a collection-time gate swap restored before
# assertions, and a planted bytecode cache under __pycache__.
# An adversary running first and also rewriting these checks defeats any
# in-process check; that case is for review. The interpreter environment is
# assumed trusted, outside both this suite and diff review. Illustrative, not
# closed: site-packages (sitecustomize, usercustomize, .pth), external plugins
# via -p/PYTEST_PLUGINS/PYTEST_ADDOPTS, wrappers on PATH, and bytecode caches
# (__pycache__ is gitignored by tui/.gitignore:1). Run at optimization level
# zero, without -O or -OO. The explicit pytest.fail refusal enforces the
# supported compilation mode. Pytest-rewritten assertions remain active
# under plain -O; -OO strips docstrings.
def _normalised_ast(node):
    """An AST node as text: its type and its non-attribute fields.

    No line or column numbers. Empty lists and None are dropped (a Constant's
    value is kept, None included), so the form does not depend on how ast
    spells empty fields: 3.12 has type_params=[], 3.11 has no such field and
    3.14's ast.dump omits empty fields such as posonlyargs=[].
    """
    if isinstance(node, list):
        return '[' + ', '.join(map(_normalised_ast, node)) + ']'
    if not isinstance(node, ast.AST):
        return repr(node)
    fields = [f'{name}={_normalised_ast(value)}' for name, value in ast.iter_fields(node)
              if not (isinstance(value, list) and value == [])
              and (value is not None or (isinstance(node, ast.Constant) and name == 'value'))]
    return f'{type(node).__name__}({", ".join(fields)})'


def _isolation_source():
    """_isolation.py's text and AST, read from the loaded module's own file."""
    data = Path(_isolation.__file__).read_bytes()
    return importlib.util.decode_source(data), compile(
        data, _isolation.__file__, 'exec', flags=ast.PyCF_ONLY_AST,
        dont_inherit=True, optimize=0)


def _isolation_module_form(tree):
    """The normalised AST of the module, one entry per top-level statement."""
    return tuple(_normalised_ast(statement) for statement in tree.body)


def _pin_literal(form):
    """`form` as the Python source of _ISOLATION_MODULE_PIN's value."""
    lines = ['(']
    for statement in form:
        pieces = [statement[start:start + 84] for start in range(0, len(statement), 84)]
        lines.extend(f'    {piece!r}' for piece in pieces)
        lines[-1] += ','
    return '\n'.join(lines + [')'])


def _pin_sha256(form):
    return hashlib.sha256('\n'.join(form).encode()).hexdigest()


# The normalised AST of _isolation.py, one string per top-level statement,
# identical on unoptimized CPython 3.11 through 3.14 in the baseline receipts.
# Generated by _pin_literal, with hand-wrapped source lines; see above.
_ISOLATION_MODULE_PIN_SHA256 = '0fbc47345b4065378cfec685f063fe41a6f843512597db3b7b60b95bac10e836'
_ISOLATION_MODULE_PIN = (
    "Expr(value=Constant(value='Early pytest plugin: isolate settings before pytest impor"
    'ts readline.\\n\\nLoaded by pyproject addopts before initial conftests. Conftest also '
    "imports it\\nso explicit root-level collection keeps Fleet TUI imports isolated.\\n'))",
    "Import(names=[alias(name='os')])",
    "ImportFrom(module='pathlib', names=[alias(name='Path')], level=0)",
    "Import(names=[alias(name='sys')])",
    "Import(names=[alias(name='tempfile')])",
    "Assert(test=UnaryOp(op=Not(), operand=Call(func=Name(id='any', ctx=Load()), args=[Ge"
    "neratorExp(elt=BoolOp(op=Or(), values=[Compare(left=Name(id='name', ctx=Load()), ops"
    "=[Eq()], comparators=[Constant(value='fleet_tui')]), Call(func=Attribute(value=Name("
    "id='name', ctx=Load()), attr='startswith', ctx=Load()), args=[Constant(value='fleet_"
    "tui.')])]), generators=[comprehension(target=Name(id='name', ctx=Store()), iter=Attr"
    "ibute(value=Name(id='sys', ctx=Load()), attr='modules', ctx=Load()), is_async=0)])])"
    "), msg=Constant(value='fleet_tui imported before the TUI test HOME/XDG isolation'))",
    "Assign(targets=[Name(id='_ORIGINAL_USER_ROOTS', ctx=Store())], value=Call(func=Name("
    "id='tuple', ctx=Load()), args=[Call(func=Attribute(value=Name(id='dict', ctx=Load())"
    ", attr='fromkeys', ctx=Load()), args=[BinOp(left=List(elts=[Call(func=Attribute(valu"
    "e=Call(func=Attribute(value=Name(id='Path', ctx=Load()), attr='home', ctx=Load())), "
    "attr='resolve', ctx=Load()))], ctx=Load()), op=Add(), right=ListComp(elt=Call(func=A"
    "ttribute(value=Call(func=Name(id='Path', ctx=Load()), args=[Name(id='value', ctx=Loa"
    "d())]), attr='resolve', ctx=Load())), generators=[comprehension(target=Name(id='key'"
    ", ctx=Store()), iter=Tuple(elts=[Constant(value='XDG_CONFIG_HOME'), Constant(value='"
    "XDG_CACHE_HOME'), Constant(value='XDG_DATA_HOME'), Constant(value='XDG_STATE_HOME'),"
    " Constant(value='XDG_RUNTIME_DIR'), Constant(value='XDG_CONFIG_DIRS'), Constant(valu"
    "e='XDG_DATA_DIRS')], ctx=Load()), is_async=0), comprehension(target=Name(id='value',"
    ' ctx=Store()), iter=Call(func=Attribute(value=Call(func=Attribute(value=Attribute(va'
    "lue=Name(id='os', ctx=Load()), attr='environ', ctx=Load()), attr='get', ctx=Load()),"
    " args=[Name(id='key', ctx=Load()), Constant(value='')]), attr='split', ctx=Load()), "
    "args=[Attribute(value=Name(id='os', ctx=Load()), attr='pathsep', ctx=Load())]), ifs="
    "[BoolOp(op=And(), values=[Name(id='value', ctx=Load()), Call(func=Attribute(value=At"
    "tribute(value=Name(id='os', ctx=Load()), attr='path', ctx=Load()), attr='isabs', ctx"
    "=Load()), args=[Name(id='value', ctx=Load())])])], is_async=0)]))])]))",
    "Assign(targets=[Name(id='_ORIGINAL_INTERPRETER_LOCATIONS', ctx=Store())], value=Tupl"
    "e(elts=[Tuple(elts=[Constant(value='sys._stdlib_dir'), Call(func=Name(id='getattr', "
    "ctx=Load()), args=[Name(id='sys', ctx=Load()), Constant(value='_stdlib_dir'), Consta"
    "nt(value=None)])], ctx=Load()), Tuple(elts=[Constant(value='os.__file__'), Call(func"
    "=Name(id='getattr', ctx=Load()), args=[Name(id='os', ctx=Load()), Constant(value='__"
    "file__'), Constant(value=None)])], ctx=Load())], ctx=Load()))",
    "FunctionDef(name='_exact_location', args=arguments(args=[arg(arg='value'), arg(arg='"
    "label'), arg(arg='refused')]), body=[Expr(value=Constant(value='Return `value` only "
    'if it is an exact absolute str.\\n\\n    Anything else is recorded in `refused` as an '
    'explicit finding and not\\n    used. The type test comes first, so only an exact str '
    'reaches\\n    os.path.isabs, which calls os.fspath and then str.startswith on it. No\\'
    'n    method of any other value is called: a str subclass could override the\\n    str'
    ' methods os.path and pathlib call, and a non-str could run\\n    __fspath__. Only `la'
    "bel`, our own text, is formatted.\\n    ')), If(test=BoolOp(op=And(), values=[Compare"
    "(left=Call(func=Name(id='type', ctx=Load()), args=[Name(id='value', ctx=Load())]), o"
    "ps=[Is()], comparators=[Name(id='str', ctx=Load())]), Call(func=Attribute(value=Attr"
    "ibute(value=Name(id='os', ctx=Load()), attr='path', ctx=Load()), attr='isabs', ctx=L"
    "oad()), args=[Name(id='value', ctx=Load())])]), body=[Return(value=Name(id='value', "
    "ctx=Load()))]), Expr(value=Call(func=Attribute(value=Name(id='refused', ctx=Load()),"
    " attr='append', ctx=Load()), args=[JoinedStr(values=[FormattedValue(value=Name(id='l"
    "abel', ctx=Load()), conversion=-1), Constant(value=' = <uninspectable exemption sour"
    "ce>')])])), Return(value=Constant(value=None))])",
    "FunctionDef(name='_license_candidate_files', args=arguments(args=[arg(arg='locations"
    '\')]), body=[Expr(value=Constant(value="The license helper\'s absolute candidate files'
    ', resolved.\\n\\n    Returns (files, refused findings). site.py builds the `license` h'
    'elper\\n    from sys._stdlib_dir, or from the directory of os.__file__ when that is\\n'
    "    unset. Here LICENSE.txt and LICENSE are joined only to that directory's\\n    par"
    'ent and the directory itself, then resolved. site.py also joins\\n    os.curdir; thos'
    "e relative candidates are omitted here because the scan's\\n    absolute-path gate sk"
    'ips them, so they are not exempted. The result is\\n    exact files, never a director'
    'y.\\n    ")), Assign(targets=[Tuple(elts=[Tuple(elts=[Name(id=\'stdlib_label\', ctx=Sto'
    "re()), Name(id='stdlib', ctx=Store())], ctx=Store()), Tuple(elts=[Name(id='os_file_l"
    "abel', ctx=Store()), Name(id='os_file', ctx=Store())], ctx=Store())], ctx=Store())],"
    " value=Name(id='locations', ctx=Load())), Assign(targets=[Name(id='refused', ctx=Sto"
    're())], value=List(ctx=Load())), If(test=BoolOp(op=Or(), values=[Compare(left=Name(i'
    "d='stdlib', ctx=Load()), ops=[Is()], comparators=[Constant(value=None)]), BoolOp(op="
    "And(), values=[Compare(left=Call(func=Name(id='type', ctx=Load()), args=[Name(id='st"
    "dlib', ctx=Load())]), ops=[Is()], comparators=[Name(id='str', ctx=Load())]), Compare"
    "(left=Name(id='stdlib', ctx=Load()), ops=[Eq()], comparators=[Constant(value='')])])"
    "]), body=[If(test=Compare(left=Name(id='os_file', ctx=Load()), ops=[Is()], comparato"
    'rs=[Constant(value=None)]), body=[Return(value=Tuple(elts=[Tuple(ctx=Load()), Tuple('
    "ctx=Load())], ctx=Load()))]), Assign(targets=[Name(id='here', ctx=Store())], value=C"
    "all(func=Name(id='_exact_location', ctx=Load()), args=[Name(id='os_file', ctx=Load()"
    "), Name(id='os_file_label', ctx=Load()), Name(id='refused', ctx=Load())])), Assign(t"
    "argets=[Name(id='label', ctx=Store())], value=Name(id='os_file_label', ctx=Load())),"
    " If(test=Compare(left=Name(id='here', ctx=Load()), ops=[IsNot()], comparators=[Const"
    "ant(value=None)]), body=[Assign(targets=[Name(id='here', ctx=Store())], value=Call(f"
    "unc=Attribute(value=Attribute(value=Name(id='os', ctx=Load()), attr='path', ctx=Load"
    "()), attr='dirname', ctx=Load()), args=[Name(id='here', ctx=Load())]))])], orelse=[A"
    "ssign(targets=[Name(id='here', ctx=Store())], value=Call(func=Name(id='_exact_locati"
    "on', ctx=Load()), args=[Name(id='stdlib', ctx=Load()), Name(id='stdlib_label', ctx=L"
    "oad()), Name(id='refused', ctx=Load())])), Assign(targets=[Name(id='label', ctx=Stor"
    "e())], value=Name(id='stdlib_label', ctx=Load()))]), If(test=Compare(left=Name(id='h"
    "ere', ctx=Load()), ops=[Is()], comparators=[Constant(value=None)]), body=[Return(val"
    "ue=Tuple(elts=[Tuple(ctx=Load()), Call(func=Name(id='tuple', ctx=Load()), args=[Name"
    "(id='refused', ctx=Load())])], ctx=Load()))]), Try(body=[Assign(targets=[Name(id='fi"
    "les', ctx=Store())], value=ListComp(elt=Call(func=Attribute(value=Call(func=Name(id="
    "'Path', ctx=Load()), args=[Call(func=Attribute(value=Attribute(value=Name(id='os', c"
    "tx=Load()), attr='path', ctx=Load()), attr='join', ctx=Load()), args=[Name(id='direc"
    "tory', ctx=Load()), Name(id='name', ctx=Load())])]), attr='resolve', ctx=Load())), g"
    "enerators=[comprehension(target=Name(id='directory', ctx=Store()), iter=Tuple(elts=["
    "Call(func=Attribute(value=Attribute(value=Name(id='os', ctx=Load()), attr='path', ct"
    "x=Load()), attr='join', ctx=Load()), args=[Name(id='here', ctx=Load()), Attribute(va"
    "lue=Name(id='os', ctx=Load()), attr='pardir', ctx=Load())]), Name(id='here', ctx=Loa"
    "d())], ctx=Load()), is_async=0), comprehension(target=Name(id='name', ctx=Store()), "
    "iter=Tuple(elts=[Constant(value='LICENSE.txt'), Constant(value='LICENSE')], ctx=Load"
    "()), is_async=0)]))], handlers=[ExceptHandler(type=Tuple(elts=[Name(id='ValueError',"
    " ctx=Load()), Name(id='OSError', ctx=Load()), Name(id='RuntimeError', ctx=Load())], "
    'ctx=Load()), body=[Return(value=Tuple(elts=[Tuple(ctx=Load()), Tuple(elts=[JoinedStr'
    "(values=[FormattedValue(value=Name(id='label', ctx=Load()), conversion=-1), Constant"
    "(value=' = <uninspectable exemption source>')])], ctx=Load())], ctx=Load()))])]), Re"
    "turn(value=Tuple(elts=[Call(func=Name(id='tuple', ctx=Load()), args=[Call(func=Attri"
    "bute(value=Name(id='dict', ctx=Load()), attr='fromkeys', ctx=Load()), args=[Name(id="
    "'files', ctx=Load())])]), Tuple(ctx=Load())], ctx=Load()))])",
    "Assign(targets=[Name(id='_ORIGINAL_LICENSE_FILES', ctx=Store())], value=Call(func=Na"
    "me(id='_license_candidate_files', ctx=Load()), args=[Name(id='_ORIGINAL_INTERPRETER_"
    "LOCATIONS', ctx=Load())]))",
    "FunctionDef(name='_process_identity', args=arguments(args=[arg(arg='pid'), arg(arg='"
    "proc_root')], defaults=[Constant(value='/proc')]), body=[Expr(value=Constant(value='"
    "Return Linux start ticks and boot ID; only an absent PID means dead.')), Assign(targ"
    "ets=[Name(id='proc', ctx=Store())], value=Call(func=Name(id='Path', ctx=Load()), arg"
    "s=[Name(id='proc_root', ctx=Load())])), Assign(targets=[Name(id='boot', ctx=Store())"
    "], value=Call(func=Attribute(value=Call(func=Attribute(value=BinOp(left=Name(id='pro"
    "c', ctx=Load()), op=Div(), right=Constant(value='sys/kernel/random/boot_id')), attr="
    "'read_text', ctx=Load())), attr='strip', ctx=Load()))), Try(body=[Assign(targets=[Na"
    "me(id='stat', ctx=Store())], value=Call(func=Attribute(value=BinOp(left=BinOp(left=N"
    "ame(id='proc', ctx=Load()), op=Div(), right=Call(func=Name(id='str', ctx=Load()), ar"
    "gs=[Name(id='pid', ctx=Load())])), op=Div(), right=Constant(value='stat')), attr='re"
    "ad_text', ctx=Load())))], handlers=[ExceptHandler(type=Name(id='FileNotFoundError', "
    "ctx=Load()), body=[If(test=Call(func=Attribute(value=BinOp(left=Name(id='proc', ctx="
    "Load()), op=Div(), right=Call(func=Name(id='str', ctx=Load()), args=[Name(id='pid', "
    "ctx=Load())])), attr='exists', ctx=Load())), body=[Raise()]), Return(value=Tuple(elt"
    "s=[Constant(value=None), Name(id='boot', ctx=Load())], ctx=Load()))])]), Assign(targ"
    "ets=[Name(id='ticks', ctx=Store())], value=Call(func=Name(id='int', ctx=Load()), arg"
    's=[Subscript(value=Call(func=Attribute(value=Subscript(value=Call(func=Attribute(val'
    "ue=Name(id='stat', ctx=Load()), attr='rsplit', ctx=Load()), args=[Constant(value=')'"
    "), Constant(value=1)]), slice=Constant(value=1), ctx=Load()), attr='split', ctx=Load"
    "())), slice=Constant(value=19), ctx=Load())])), If(test=Compare(left=Name(id='ticks'"
    ', ctx=Load()), ops=[LtE()], comparators=[Constant(value=0)]), body=[Raise(exc=Call(f'
    "unc=Name(id='ValueError', ctx=Load()), args=[Constant(value='invalid process start t"
    "icks')]))]), Return(value=Tuple(elts=[Name(id='ticks', ctx=Load()), Name(id='boot', "
    'ctx=Load())], ctx=Load()))])',
    "FunctionDef(name='_test_path_identity', args=arguments(args=[arg(arg='info')]), body"
    "=[Expr(value=Constant(value='Compare replacement/modification fields; reads may upda"
    "te only atime.')), Return(value=Tuple(elts=[Attribute(value=Name(id='info', ctx=Load"
    "()), attr='st_dev', ctx=Load()), Attribute(value=Name(id='info', ctx=Load()), attr='"
    "st_ino', ctx=Load()), Attribute(value=Name(id='info', ctx=Load()), attr='st_mode', c"
    "tx=Load()), Attribute(value=Name(id='info', ctx=Load()), attr='st_uid', ctx=Load()),"
    " Attribute(value=Name(id='info', ctx=Load()), attr='st_gid', ctx=Load()), Attribute("
    "value=Name(id='info', ctx=Load()), attr='st_size', ctx=Load()), Attribute(value=Name"
    "(id='info', ctx=Load()), attr='st_mtime_ns', ctx=Load()), Attribute(value=Name(id='i"
    "nfo', ctx=Load()), attr='st_ctime_ns', ctx=Load())], ctx=Load()))])",
    "FunctionDef(name='_retirement_address', args=arguments(args=[arg(arg='root')]), body"
    "=[Expr(value=Constant(value='Address retention without reading a possibly damaged ow"
    "ner record.')), Import(names=[alias(name='hashlib')]), Return(value=BinOp(left=Const"
    "ant(value='\\x00fleet-tui-retire-'), op=Add(), right=Call(func=Attribute(value=Call(f"
    "unc=Attribute(value=Name(id='hashlib', ctx=Load()), attr='sha256', ctx=Load()), args"
    "=[Call(func=Attribute(value=Name(id='os', ctx=Load()), attr='fsencode', ctx=Load()),"
    " args=[Call(func=Name(id='str', ctx=Load()), args=[Name(id='root', ctx=Load())])])])"
    ", attr='hexdigest', ctx=Load()))))])",
    "FunctionDef(name='_retirement_pidfd_open', args=arguments(args=[arg(arg='pid')]), bo"
    "dy=[Expr(value=Constant(value='Use the kernel pidfd facility even on Python builds w"
    "ithout its wrapper.')), Assign(targets=[Name(id='native', ctx=Store())], value=Call("
    "func=Name(id='getattr', ctx=Load()), args=[Name(id='os', ctx=Load()), Constant(value"
    "='pidfd_open'), Constant(value=None)])), If(test=Compare(left=Name(id='native', ctx="
    'Load()), ops=[IsNot()], comparators=[Constant(value=None)]), body=[Return(value=Call'
    "(func=Name(id='native', ctx=Load()), args=[Name(id='pid', ctx=Load())]))]), Import(n"
    "ames=[alias(name='ctypes')]), Assign(targets=[Name(id='call', ctx=Store())], value=A"
    "ttribute(value=Call(func=Attribute(value=Name(id='ctypes', ctx=Load()), attr='CDLL',"
    " ctx=Load()), args=[Constant(value=None)], keywords=[keyword(arg='use_errno', value="
    "Constant(value=True))]), attr='pidfd_open', ctx=Load())), Assign(targets=[Attribute("
    "value=Name(id='call', ctx=Load()), attr='argtypes', ctx=Store())], value=Tuple(elts="
    "[Attribute(value=Name(id='ctypes', ctx=Load()), attr='c_int', ctx=Load()), Attribute"
    "(value=Name(id='ctypes', ctx=Load()), attr='c_uint', ctx=Load())], ctx=Load())), Ass"
    "ign(targets=[Attribute(value=Name(id='call', ctx=Load()), attr='restype', ctx=Store("
    "))], value=Attribute(value=Name(id='ctypes', ctx=Load()), attr='c_int', ctx=Load()))"
    ", Assign(targets=[Name(id='result', ctx=Store())], value=Call(func=Name(id='call', c"
    "tx=Load()), args=[Name(id='pid', ctx=Load()), Constant(value=0)])), If(test=Compare("
    "left=Name(id='result', ctx=Load()), ops=[Lt()], comparators=[Constant(value=0)]), bo"
    "dy=[Assign(targets=[Name(id='number', ctx=Store())], value=Call(func=Attribute(value"
    "=Name(id='ctypes', ctx=Load()), attr='get_errno', ctx=Load()))), Raise(exc=Call(func"
    "=Name(id='OSError', ctx=Load()), args=[Name(id='number', ctx=Load()), Call(func=Attr"
    "ibute(value=Name(id='os', ctx=Load()), attr='strerror', ctx=Load()), args=[Name(id='"
    "number', ctx=Load())])]))]), Return(value=Name(id='result', ctx=Load()))])",
    "FunctionDef(name='_retirement_pidfd_signal', args=arguments(args=[arg(arg='descripto"
    'r\'), arg(arg=\'number\')]), body=[Expr(value=Constant(value="Signal only the pidfd\'s i'
    'ncarnation through Python or host libc.")), Import(names=[alias(name=\'signal\')]), As'
    "sign(targets=[Name(id='native', ctx=Store())], value=Call(func=Name(id='getattr', ct"
    "x=Load()), args=[Name(id='signal', ctx=Load()), Constant(value='pidfd_send_signal'),"
    " Constant(value=None)])), If(test=Compare(left=Name(id='native', ctx=Load()), ops=[I"
    "sNot()], comparators=[Constant(value=None)]), body=[Return(value=Call(func=Name(id='"
    "native', ctx=Load()), args=[Name(id='descriptor', ctx=Load()), Name(id='number', ctx"
    "=Load())]))]), Import(names=[alias(name='ctypes')]), Assign(targets=[Name(id='call',"
    " ctx=Store())], value=Attribute(value=Call(func=Attribute(value=Name(id='ctypes', ct"
    "x=Load()), attr='CDLL', ctx=Load()), args=[Constant(value=None)], keywords=[keyword("
    "arg='use_errno', value=Constant(value=True))]), attr='pidfd_send_signal', ctx=Load()"
    ")), Assign(targets=[Attribute(value=Name(id='call', ctx=Load()), attr='argtypes', ct"
    "x=Store())], value=Tuple(elts=[Attribute(value=Name(id='ctypes', ctx=Load()), attr='"
    "c_int', ctx=Load()), Attribute(value=Name(id='ctypes', ctx=Load()), attr='c_int', ct"
    "x=Load()), Attribute(value=Name(id='ctypes', ctx=Load()), attr='c_void_p', ctx=Load("
    ")), Attribute(value=Name(id='ctypes', ctx=Load()), attr='c_uint', ctx=Load())], ctx="
    "Load())), Assign(targets=[Attribute(value=Name(id='call', ctx=Load()), attr='restype"
    "', ctx=Store())], value=Attribute(value=Name(id='ctypes', ctx=Load()), attr='c_int',"
    " ctx=Load())), Assign(targets=[Name(id='result', ctx=Store())], value=Call(func=Name"
    "(id='call', ctx=Load()), args=[Name(id='descriptor', ctx=Load()), Name(id='number', "
    'ctx=Load()), Constant(value=None), Constant(value=0)])), If(test=Compare(left=Name(i'
    "d='result', ctx=Load()), ops=[Lt()], comparators=[Constant(value=0)]), body=[Assign("
    "targets=[Name(id='error', ctx=Store())], value=Call(func=Attribute(value=Name(id='ct"
    "ypes', ctx=Load()), attr='get_errno', ctx=Load()))), Raise(exc=Call(func=Name(id='OS"
    "Error', ctx=Load()), args=[Name(id='error', ctx=Load()), Call(func=Attribute(value=N"
    "ame(id='os', ctx=Load()), attr='strerror', ctx=Load()), args=[Name(id='error', ctx=L"
    'oad())])]))])])',
    "FunctionDef(name='_stop_retirement_guard', args=arguments(args=[arg(arg='child'), ar"
    "g(arg='descriptor'), arg(arg='pidfd')], defaults=[Constant(value=None)]), body=[Expr"
    "(value=Constant(value='Cancel through the private socket; observe peer channel closu"
    're.\\n\\n    The cooperating helper consumes R and exits, including a sole double-fork'
    '\\n    descendant. A helper that fails first can exit with R unread; releasing\\n    i'
    'ts last endpoint then reports ECONNRESET once, followed by EOF. Closure\\n    is that'
    ' EOF, read directly or after the reset; any other error or data\\n    propagates. EOF'
    ' proves channel closure, not arbitrary holder process death.\\n    A stale/reaped dir'
    "ect-child PID is never used for signalling.\\n    ')), Import(names=[alias(name='posi"
    "x')]), Import(names=[alias(name='select')]), Try(body=[If(test=Compare(left=Call(fun"
    "c=Attribute(value=Name(id='posix', ctx=Load()), attr='write', ctx=Load()), args=[Nam"
    "e(id='descriptor', ctx=Load()), Constant(value=b'R')]), ops=[NotEq()], comparators=["
    "Constant(value=1)]), body=[Raise(exc=Call(func=Name(id='RuntimeError', ctx=Load()), "
    "args=[Constant(value='short retirement cancellation write')]))])], handlers=[ExceptH"
    "andler(type=Tuple(elts=[Name(id='BrokenPipeError', ctx=Load()), Name(id='ConnectionR"
    "esetError', ctx=Load())], ctx=Load()), body=[Pass()])]), Assign(targets=[Tuple(elts="
    "[Name(id='ready', ctx=Store()), Name(id='_', ctx=Store()), Name(id='_', ctx=Store())"
    "], ctx=Store())], value=Call(func=Attribute(value=Name(id='select', ctx=Load()), att"
    "r='select', ctx=Load()), args=[List(elts=[Name(id='descriptor', ctx=Load())], ctx=Lo"
    'ad()), List(ctx=Load()), List(ctx=Load()), Constant(value=10)])), If(test=UnaryOp(op'
    "=Not(), operand=Name(id='ready', ctx=Load())), body=[Raise(exc=Call(func=Name(id='Ru"
    "ntimeError', ctx=Load()), args=[Constant(value='retirement cancellation did not obse"
    "rve peer exit')]))]), Try(body=[Assign(targets=[Name(id='received', ctx=Store())], v"
    "alue=Call(func=Attribute(value=Name(id='posix', ctx=Load()), attr='read', ctx=Load()"
    "), args=[Name(id='descriptor', ctx=Load()), Constant(value=1)]))], handlers=[ExceptH"
    "andler(type=Name(id='ConnectionResetError', ctx=Load()), body=[If(test=UnaryOp(op=No"
    "t(), operand=Subscript(value=Call(func=Attribute(value=Name(id='select', ctx=Load())"
    ", attr='select', ctx=Load()), args=[List(elts=[Name(id='descriptor', ctx=Load())], c"
    'tx=Load()), List(ctx=Load()), List(ctx=Load()), Constant(value=0)]), slice=Constant('
    "value=0), ctx=Load())), body=[Raise()]), Assign(targets=[Name(id='received', ctx=Sto"
    "re())], value=Call(func=Attribute(value=Name(id='posix', ctx=Load()), attr='read', c"
    "tx=Load()), args=[Name(id='descriptor', ctx=Load()), Constant(value=1)]))])]), If(te"
    "st=Compare(left=Name(id='received', ctx=Load()), ops=[NotEq()], comparators=[Constan"
    "t(value=b'')]), body=[Raise(exc=Call(func=Name(id='RuntimeError', ctx=Load()), args="
    "[Constant(value='retirement cancellation did not observe peer exit')]))]), If(test=C"
    "ompare(left=Name(id='pidfd', ctx=Load()), ops=[IsNot()], comparators=[Constant(value"
    "=None)]), body=[Try(body=[Expr(value=Call(func=Attribute(value=Name(id='os', ctx=Loa"
    "d()), attr='waitid', ctx=Load()), args=[Attribute(value=Name(id='os', ctx=Load()), a"
    "ttr='P_PIDFD', ctx=Load()), Name(id='pidfd', ctx=Load()), Attribute(value=Name(id='o"
    "s', ctx=Load()), attr='WEXITED', ctx=Load())]))], handlers=[ExceptHandler(type=Name("
    "id='ChildProcessError', ctx=Load()), body=[Pass()])])], orelse=[Try(body=[Expr(value"
    "=Call(func=Attribute(value=Name(id='os', ctx=Load()), attr='waitpid', ctx=Load()), a"
    "rgs=[Name(id='child', ctx=Load()), Constant(value=0)]))], handlers=[ExceptHandler(ty"
    "pe=Name(id='ChildProcessError', ctx=Load()), body=[Pass()])])])])",
    "FunctionDef(name='_start_retirement_guard', args=arguments(args=[arg(arg='root'), ar"
    "g(arg='owner_pid'), arg(arg='owner_ticks'), arg(arg='proc_root')], defaults=[Constan"
    "t(value='/proc')]), body=[Expr(value=Constant(value='Preallocate a one-use, memory-o"
    'nly retirement capability before payload.\\n\\n    Revoke through its private cancella'
    'tion socket and observe channel closure.\\n    A granted or lost helper cannot grant '
    'any later sweep. No retention\\n    decision requires a filesystem write, including a'
    " failed marker write.\\n    ')), Import(names=[alias(name='secrets')]), Import(names="
    "[alias(name='select')]), Import(names=[alias(name='socket')]), Import(names=[alias(n"
    "ame='struct')]), Assign(targets=[Name(id='cookie', ctx=Store())], value=Call(func=At"
    "tribute(value=Name(id='secrets', ctx=Load()), attr='token_hex', ctx=Load()), args=[C"
    "onstant(value=16)])), Assign(targets=[Name(id='listener', ctx=Store())], value=Call("
    "func=Attribute(value=Name(id='socket', ctx=Load()), attr='socket', ctx=Load()), args"
    "=[Attribute(value=Name(id='socket', ctx=Load()), attr='AF_UNIX', ctx=Load()), Attrib"
    "ute(value=Name(id='socket', ctx=Load()), attr='SOCK_STREAM', ctx=Load())])), Expr(va"
    "lue=Call(func=Attribute(value=Name(id='listener', ctx=Load()), attr='bind', ctx=Load"
    "()), args=[Call(func=Name(id='_retirement_address', ctx=Load()), args=[Name(id='root"
    "', ctx=Load())])])), Assign(targets=[Tuple(elts=[Name(id='command_peer', ctx=Store()"
    "), Name(id='command_owner', ctx=Store())], ctx=Store())], value=Call(func=Attribute("
    "value=Name(id='socket', ctx=Load()), attr='socketpair', ctx=Load()))), Assign(target"
    "s=[Tuple(elts=[Name(id='read_fd', ctx=Store()), Name(id='write_fd', ctx=Store())], c"
    "tx=Store())], value=Tuple(elts=[Call(func=Attribute(value=Name(id='command_peer', ct"
    "x=Load()), attr='detach', ctx=Load())), Call(func=Attribute(value=Name(id='command_o"
    "wner', ctx=Load()), attr='detach', ctx=Load()))], ctx=Load())), Assign(targets=[Tupl"
    "e(elts=[Name(id='ready_read', ctx=Store()), Name(id='ready_write', ctx=Store())], ct"
    "x=Store())], value=Call(func=Attribute(value=Name(id='os', ctx=Load()), attr='pipe',"
    " ctx=Load()))), Assign(targets=[Name(id='child', ctx=Store())], value=Constant(value"
    "=None)), Try(body=[Assign(targets=[Name(id='child', ctx=Store())], value=Call(func=A"
    "ttribute(value=Name(id='os', ctx=Load()), attr='fork', ctx=Load()))), If(test=Compar"
    "e(left=Name(id='child', ctx=Load()), ops=[Eq()], comparators=[Constant(value=0)]), b"
    "ody=[Try(body=[Expr(value=Call(func=Attribute(value=Name(id='os', ctx=Load()), attr="
    "'close', ctx=Load()), args=[Name(id='write_fd', ctx=Load())])), Expr(value=Call(func"
    "=Attribute(value=Name(id='os', ctx=Load()), attr='close', ctx=Load()), args=[Name(id"
    "='ready_read', ctx=Load())])), Assign(targets=[Name(id='null_fd', ctx=Store())], val"
    "ue=Call(func=Attribute(value=Name(id='os', ctx=Load()), attr='open', ctx=Load()), ar"
    "gs=[Constant(value='/dev/null'), Attribute(value=Name(id='os', ctx=Load()), attr='O_"
    "RDWR', ctx=Load())])), For(target=Name(id='stream_fd', ctx=Store()), iter=Tuple(elts"
    '=[Constant(value=0), Constant(value=1), Constant(value=2)], ctx=Load()), body=[Expr('
    "value=Call(func=Attribute(value=Name(id='os', ctx=Load()), attr='dup2', ctx=Load()),"
    " args=[Name(id='null_fd', ctx=Load()), Name(id='stream_fd', ctx=Load())]))]), If(tes"
    "t=Compare(left=Name(id='null_fd', ctx=Load()), ops=[Gt()], comparators=[Constant(val"
    "ue=2)]), body=[Expr(value=Call(func=Attribute(value=Name(id='os', ctx=Load()), attr="
    "'close', ctx=Load()), args=[Name(id='null_fd', ctx=Load())]))]), Expr(value=Call(fun"
    "c=Attribute(value=Name(id='os', ctx=Load()), attr='setsid', ctx=Load()))), Expr(valu"
    "e=Call(func=Attribute(value=Name(id='listener', ctx=Load()), attr='listen', ctx=Load"
    "()), args=[Constant(value=1)])), Assign(targets=[Tuple(elts=[Name(id='child_ticks', "
    "ctx=Store()), Name(id='_', ctx=Store())], ctx=Store())], value=Call(func=Name(id='_p"
    "rocess_identity', ctx=Load()), args=[Constant(value='self')])), Assign(targets=[Name"
    "(id='ready', ctx=Store())], value=Call(func=Attribute(value=JoinedStr(values=[Consta"
    "nt(value='A '), FormattedValue(value=Name(id='child_ticks', ctx=Load()), conversion="
    "-1), Constant(value='\\n')]), attr='encode', ctx=Load()), args=[Constant(value='ascii"
    "')])), If(test=Compare(left=Call(func=Attribute(value=Name(id='os', ctx=Load()), att"
    "r='write', ctx=Load()), args=[Name(id='ready_write', ctx=Load()), Name(id='ready', c"
    "tx=Load())]), ops=[NotEq()], comparators=[Call(func=Name(id='len', ctx=Load()), args"
    "=[Name(id='ready', ctx=Load())])]), body=[Raise(exc=Call(func=Name(id='RuntimeError'"
    ", ctx=Load()), args=[Constant(value='short helper readiness pipe write')]))]), Expr("
    "value=Call(func=Attribute(value=Name(id='os', ctx=Load()), attr='close', ctx=Load())"
    ", args=[Name(id='ready_write', ctx=Load())])), While(test=Call(func=Attribute(value="
    "Call(func=Name(id='Path', ctx=Load()), args=[Name(id='root', ctx=Load())]), attr='is"
    "_dir', ctx=Load())), body=[Assign(targets=[Tuple(elts=[Name(id='readable', ctx=Store"
    "()), Name(id='_', ctx=Store()), Name(id='_', ctx=Store())], ctx=Store())], value=Cal"
    "l(func=Attribute(value=Name(id='select', ctx=Load()), attr='select', ctx=Load()), ar"
    "gs=[BinOp(left=List(elts=[Name(id='listener', ctx=Load())], ctx=Load()), op=Add(), r"
    "ight=IfExp(test=Compare(left=Name(id='read_fd', ctx=Load()), ops=[Is()], comparators"
    "=[Constant(value=None)]), body=List(ctx=Load()), orelse=List(elts=[Name(id='read_fd'"
    ', ctx=Load())], ctx=Load()))), List(ctx=Load()), List(ctx=Load()), Constant(value=1)'
    "])), If(test=BoolOp(op=And(), values=[Compare(left=Name(id='read_fd', ctx=Load()), o"
    "ps=[IsNot()], comparators=[Constant(value=None)]), Compare(left=Name(id='read_fd', c"
    "tx=Load()), ops=[In()], comparators=[Name(id='readable', ctx=Load())])]), body=[Assi"
    "gn(targets=[Name(id='command', ctx=Store())], value=Call(func=Attribute(value=Name(i"
    "d='os', ctx=Load()), attr='read', ctx=Load()), args=[Name(id='read_fd', ctx=Load()),"
    " Constant(value=1)])), If(test=Name(id='command', ctx=Load()), body=[Assign(targets="
    "[Name(id='return_value', ctx=Store())], value=Constant(value=0)), Break()]), Expr(va"
    "lue=Call(func=Attribute(value=Name(id='os', ctx=Load()), attr='close', ctx=Load()), "
    "args=[Name(id='read_fd', ctx=Load())])), Assign(targets=[Name(id='read_fd', ctx=Stor"
    "e())], value=Constant(value=None))]), If(test=Compare(left=Name(id='listener', ctx=L"
    "oad()), ops=[In()], comparators=[Name(id='readable', ctx=Load())]), body=[Assign(tar"
    "gets=[Tuple(elts=[Name(id='connection', ctx=Store()), Name(id='_', ctx=Store())], ct"
    "x=Store())], value=Call(func=Attribute(value=Name(id='listener', ctx=Load()), attr='"
    "accept', ctx=Load()))), With(items=[withitem(context_expr=Name(id='connection', ctx="
    "Load()))], body=[Expr(value=Call(func=Attribute(value=Name(id='connection', ctx=Load"
    "()), attr='settimeout', ctx=Load()), args=[Constant(value=2)])), Assign(targets=[Nam"
    "e(id='peer', ctx=Store())], value=Call(func=Attribute(value=Name(id='struct', ctx=Lo"
    "ad()), attr='unpack', ctx=Load()), args=[Constant(value='3i'), Call(func=Attribute(v"
    "alue=Name(id='connection', ctx=Load()), attr='getsockopt', ctx=Load()), args=[Attrib"
    "ute(value=Name(id='socket', ctx=Load()), attr='SOL_SOCKET', ctx=Load()), Attribute(v"
    "alue=Name(id='socket', ctx=Load()), attr='SO_PEERCRED', ctx=Load()), Call(func=Attri"
    "bute(value=Name(id='struct', ctx=Load()), attr='calcsize', ctx=Load()), args=[Consta"
    "nt(value='3i')])])])), Assign(targets=[Name(id='request', ctx=Store())], value=Call("
    "func=Attribute(value=Name(id='connection', ctx=Load()), attr='recv', ctx=Load()), ar"
    'gs=[Constant(value=8192)])), If(test=BoolOp(op=And(), values=[Compare(left=Subscript'
    "(value=Name(id='peer', ctx=Load()), slice=Constant(value=1), ctx=Load()), ops=[Eq()]"
    ", comparators=[Call(func=Attribute(value=Name(id='os', ctx=Load()), attr='getuid', c"
    "tx=Load()))]), Compare(left=Subscript(value=Name(id='request', ctx=Load()), slice=Sl"
    'ice(upper=Constant(value=1)), ctx=Load()), ops=[Eq()], comparators=[Constant(value=b'
    "'R')])]), body=[Expr(value=Call(func=Attribute(value=Name(id='connection', ctx=Load("
    ")), attr='sendall', ctx=Load()), args=[Constant(value=b'RETAINED')])), Break()]), As"
    "sign(targets=[Tuple(elts=[Name(id='ticks', ctx=Store()), Name(id='_', ctx=Store())],"
    " ctx=Store())], value=Call(func=Name(id='_process_identity', ctx=Load()), args=[Name"
    "(id='owner_pid', ctx=Load()), Name(id='proc_root', ctx=Load())])), If(test=Compare(l"
    "eft=Name(id='read_fd', ctx=Load()), ops=[IsNot()], comparators=[Constant(value=None)"
    "]), body=[Assign(targets=[Tuple(elts=[Name(id='pending', ctx=Store()), Name(id='_', "
    "ctx=Store()), Name(id='_', ctx=Store())], ctx=Store())], value=Call(func=Attribute(v"
    "alue=Name(id='select', ctx=Load()), attr='select', ctx=Load()), args=[List(elts=[Nam"
    "e(id='read_fd', ctx=Load())], ctx=Load()), List(ctx=Load()), List(ctx=Load()), Const"
    "ant(value=0)])), If(test=BoolOp(op=And(), values=[Name(id='pending', ctx=Load()), Ca"
    "ll(func=Attribute(value=Name(id='os', ctx=Load()), attr='read', ctx=Load()), args=[N"
    "ame(id='read_fd', ctx=Load()), Constant(value=1)])]), body=[Break()])]), If(test=Boo"
    "lOp(op=Or(), values=[Compare(left=Subscript(value=Name(id='peer', ctx=Load()), slice"
    '=Constant(value=1), ctx=Load()), ops=[NotEq()], comparators=[Call(func=Attribute(val'
    "ue=Name(id='os', ctx=Load()), attr='getuid', ctx=Load()))]), Compare(left=Name(id='t"
    "icks', ctx=Load()), ops=[Eq()], comparators=[Name(id='owner_ticks', ctx=Load())]), C"
    "ompare(left=Name(id='request', ctx=Load()), ops=[NotEq()], comparators=[BinOp(left=B"
    "inOp(left=Call(func=Attribute(value=Name(id='os', ctx=Load()), attr='fsencode', ctx="
    "Load()), args=[Call(func=Name(id='str', ctx=Load()), args=[Name(id='root', ctx=Load("
    "))])]), op=Add(), right=Constant(value=b'\\x00')), op=Add(), right=Call(func=Attribut"
    "e(value=Name(id='cookie', ctx=Load()), attr='encode', ctx=Load()), args=[Constant(va"
    "lue='ascii')]))])]), body=[Continue()]), Expr(value=Call(func=Attribute(value=Name(i"
    "d='connection', ctx=Load()), attr='sendall', ctx=Load()), args=[Constant(value=b'GRA"
    "NT')])), Expr(value=Call(func=Attribute(value=Name(id='connection', ctx=Load()), att"
    "r='recv', ctx=Load()), args=[Constant(value=1)])), Break()])])], orelse=[Assign(targ"
    "ets=[Name(id='return_value', ctx=Store())], value=Constant(value=0))])], handlers=[E"
    "xceptHandler(type=Name(id='BaseException', ctx=Load()), body=[Assign(targets=[Name(i"
    "d='return_value', ctx=Store())], value=Constant(value=1))])], finalbody=[Expr(value="
    "Call(func=Attribute(value=Name(id='os', ctx=Load()), attr='_exit', ctx=Load()), args"
    "=[Call(func=Attribute(value=Call(func=Name(id='locals', ctx=Load())), attr='get', ct"
    "x=Load()), args=[Constant(value='return_value'), Constant(value=0)])]))])]), Expr(va"
    "lue=Call(func=Attribute(value=Name(id='listener', ctx=Load()), attr='close', ctx=Loa"
    "d()))), Expr(value=Call(func=Attribute(value=Name(id='os', ctx=Load()), attr='close'"
    ", ctx=Load()), args=[Name(id='read_fd', ctx=Load())])), Assign(targets=[Name(id='rea"
    "d_fd', ctx=Store())], value=Constant(value=None)), Expr(value=Call(func=Attribute(va"
    "lue=Name(id='os', ctx=Load()), attr='close', ctx=Load()), args=[Name(id='ready_write"
    "', ctx=Load())])), Assign(targets=[Name(id='ready_write', ctx=Store())], value=Const"
    "ant(value=None)), Assign(targets=[Name(id='ready', ctx=Store())], value=Call(func=At"
    "tribute(value=Call(func=Attribute(value=Call(func=Attribute(value=Name(id='os', ctx="
    "Load()), attr='read', ctx=Load()), args=[Name(id='ready_read', ctx=Load()), Constant"
    "(value=128)]), attr='decode', ctx=Load()), args=[Constant(value='ascii')]), attr='sp"
    "lit', ctx=Load()))), If(test=BoolOp(op=Or(), values=[Compare(left=Call(func=Name(id="
    "'len', ctx=Load()), args=[Name(id='ready', ctx=Load())]), ops=[NotEq()], comparators"
    "=[Constant(value=2)]), Compare(left=Subscript(value=Name(id='ready', ctx=Load()), sl"
    "ice=Constant(value=0), ctx=Load()), ops=[NotEq()], comparators=[Constant(value='A')]"
    ")]), body=[Raise(exc=Call(func=Name(id='RuntimeError', ctx=Load()), args=[Constant(v"
    "alue='retirement helper failed before readiness')]))]), Assign(targets=[Name(id='chi"
    "ld_ticks', ctx=Store())], value=Call(func=Name(id='int', ctx=Load()), args=[Subscrip"
    "t(value=Name(id='ready', ctx=Load()), slice=Constant(value=1), ctx=Load())])), If(te"
    "st=Compare(left=Name(id='child_ticks', ctx=Load()), ops=[LtE()], comparators=[Consta"
    "nt(value=0)]), body=[Raise(exc=Call(func=Name(id='RuntimeError', ctx=Load()), args=["
    "Constant(value='retirement helper identity missing')]))]), Assign(targets=[Name(id='"
    "pidfd', ctx=Store())], value=Call(func=Name(id='_retirement_pidfd_open', ctx=Load())"
    ", args=[Name(id='child', ctx=Load())])), Expr(value=Call(func=Attribute(value=Name(i"
    "d='os', ctx=Load()), attr='close', ctx=Load()), args=[Name(id='ready_read', ctx=Load"
    "())])), Assign(targets=[Name(id='ready_read', ctx=Store())], value=Constant(value=No"
    "ne)), Return(value=Tuple(elts=[Name(id='child', ctx=Load()), Name(id='child_ticks', "
    "ctx=Load()), Name(id='cookie', ctx=Load()), Name(id='write_fd', ctx=Load()), Name(id"
    "='pidfd', ctx=Load())], ctx=Load()))], handlers=[ExceptHandler(type=Name(id='BaseExc"
    "eption', ctx=Load()), body=[Import(names=[alias(name='posix')]), For(target=Name(id="
    "'descriptor', ctx=Store()), iter=Tuple(elts=[Name(id='read_fd', ctx=Load()), Name(id"
    "='ready_read', ctx=Load()), Name(id='ready_write', ctx=Load())], ctx=Load()), body=["
    "If(test=Compare(left=Name(id='descriptor', ctx=Load()), ops=[IsNot()], comparators=["
    'Constant(value=None)]), body=[Try(body=[Expr(value=Call(func=Attribute(value=Name(id'
    "='posix', ctx=Load()), attr='close', ctx=Load()), args=[Name(id='descriptor', ctx=Lo"
    "ad())]))], handlers=[ExceptHandler(type=Name(id='OSError', ctx=Load()), name='error'"
    ", body=[If(test=Compare(left=Attribute(value=Name(id='error', ctx=Load()), attr='err"
    "no', ctx=Load()), ops=[NotEq()], comparators=[Constant(value=9)]), body=[Raise()])])"
    "])])]), If(test=Compare(left=Name(id='child', ctx=Load()), ops=[IsNot()], comparator"
    "s=[Constant(value=None)]), body=[Expr(value=Call(func=Name(id='_stop_retirement_guar"
    "d', ctx=Load()), args=[Name(id='child', ctx=Load()), Name(id='write_fd', ctx=Load())"
    ", Call(func=Attribute(value=Call(func=Name(id='locals', ctx=Load())), attr='get', ct"
    "x=Load()), args=[Constant(value='pidfd')])]))]), For(target=Name(id='descriptor', ct"
    "x=Store()), iter=Tuple(elts=[Name(id='write_fd', ctx=Load()), Call(func=Attribute(va"
    "lue=Call(func=Name(id='locals', ctx=Load())), attr='get', ctx=Load()), args=[Constan"
    "t(value='pidfd')])], ctx=Load()), body=[If(test=Compare(left=Name(id='descriptor', c"
    'tx=Load()), ops=[IsNot()], comparators=[Constant(value=None)]), body=[Try(body=[Expr'
    "(value=Call(func=Attribute(value=Name(id='posix', ctx=Load()), attr='close', ctx=Loa"
    "d()), args=[Name(id='descriptor', ctx=Load())]))], handlers=[ExceptHandler(type=Name"
    "(id='OSError', ctx=Load()), name='error', body=[If(test=Compare(left=Attribute(value"
    "=Name(id='error', ctx=Load()), attr='errno', ctx=Load()), ops=[NotEq()], comparators"
    '=[Constant(value=9)]), body=[Raise()])])])])]), Raise()])])])',
    "FunctionDef(name='_request_retirement', args=arguments(args=[arg(arg='root'), arg(ar"
    'g=\'fields\')]), body=[Expr(value=Constant(value="Accept only a one-use reply sent by '
    'the recorded live helper incarnation.\\n\\n    SO_PEERCRED names the process that call'
    'ed listen(), which also answers for\\n    any holder of an inherited listener. The re'
    "ply's own SCM_CREDENTIALS name\\n    its sender, and a pidfd bound before the start-t"
    'icks check that is still\\n    unexited after the reply proves that PID was this inca'
    'rnation throughout.\\n    ")), Import(names=[alias(name=\'select\')]), Import(names=[al'
    "ias(name='socket')]), Import(names=[alias(name='struct')]), Assign(targets=[Tuple(el"
    "ts=[Name(id='guardian', ctx=Store()), Name(id='ticks', ctx=Store())], ctx=Store())],"
    " value=Tuple(elts=[Call(func=Name(id='int', ctx=Load()), args=[Subscript(value=Name("
    "id='fields', ctx=Load()), slice=Constant(value=5), ctx=Load())]), Call(func=Name(id="
    "'int', ctx=Load()), args=[Subscript(value=Name(id='fields', ctx=Load()), slice=Const"
    "ant(value=6), ctx=Load())])], ctx=Load())), Assign(targets=[Name(id='pidfd', ctx=Sto"
    "re())], value=Call(func=Name(id='_retirement_pidfd_open', ctx=Load()), args=[Name(id"
    "='guardian', ctx=Load())])), Try(body=[Assign(targets=[Name(id='actual', ctx=Store()"
    ")], value=Call(func=Attribute(value=BinOp(left=BinOp(left=Call(func=Name(id='Path', "
    "ctx=Load()), args=[Constant(value='/proc')]), op=Div(), right=Call(func=Name(id='str"
    "', ctx=Load()), args=[Name(id='guardian', ctx=Load())])), op=Div(), right=Constant(v"
    "alue='stat')), attr='read_text', ctx=Load()))), If(test=Compare(left=Call(func=Name("
    "id='int', ctx=Load()), args=[Subscript(value=Call(func=Attribute(value=Subscript(val"
    "ue=Call(func=Attribute(value=Name(id='actual', ctx=Load()), attr='rsplit', ctx=Load("
    ")), args=[Constant(value=')'), Constant(value=1)]), slice=Constant(value=1), ctx=Loa"
    "d()), attr='split', ctx=Load())), slice=Constant(value=19), ctx=Load())]), ops=[NotE"
    "q()], comparators=[Name(id='ticks', ctx=Load())]), body=[Raise(exc=Call(func=Name(id"
    "='ValueError', ctx=Load()), args=[Constant(value='retirement helper incarnation chan"
    "ged')]))]), With(items=[withitem(context_expr=Call(func=Attribute(value=Name(id='soc"
    "ket', ctx=Load()), attr='socket', ctx=Load()), args=[Attribute(value=Name(id='socket"
    "', ctx=Load()), attr='AF_UNIX', ctx=Load()), Attribute(value=Name(id='socket', ctx=L"
    "oad()), attr='SOCK_STREAM', ctx=Load())]), optional_vars=Name(id='connection', ctx=S"
    "tore()))], body=[Expr(value=Call(func=Attribute(value=Name(id='connection', ctx=Load"
    "()), attr='settimeout', ctx=Load()), args=[Constant(value=2)])), Expr(value=Call(fun"
    "c=Attribute(value=Name(id='connection', ctx=Load()), attr='setsockopt', ctx=Load()),"
    " args=[Attribute(value=Name(id='socket', ctx=Load()), attr='SOL_SOCKET', ctx=Load())"
    ", Attribute(value=Name(id='socket', ctx=Load()), attr='SO_PASSCRED', ctx=Load()), Co"
    "nstant(value=1)])), Expr(value=Call(func=Attribute(value=Name(id='connection', ctx=L"
    "oad()), attr='connect', ctx=Load()), args=[Call(func=Name(id='_retirement_address', "
    "ctx=Load()), args=[Name(id='root', ctx=Load())])])), Assign(targets=[Name(id='peer',"
    " ctx=Store())], value=Call(func=Attribute(value=Name(id='struct', ctx=Load()), attr="
    "'unpack', ctx=Load()), args=[Constant(value='3i'), Call(func=Attribute(value=Name(id"
    "='connection', ctx=Load()), attr='getsockopt', ctx=Load()), args=[Attribute(value=Na"
    "me(id='socket', ctx=Load()), attr='SOL_SOCKET', ctx=Load()), Attribute(value=Name(id"
    "='socket', ctx=Load()), attr='SO_PEERCRED', ctx=Load()), Call(func=Attribute(value=N"
    "ame(id='struct', ctx=Load()), attr='calcsize', ctx=Load()), args=[Constant(value='3i"
    "')])])])), If(test=Compare(left=Subscript(value=Name(id='peer', ctx=Load()), slice=S"
    'lice(upper=Constant(value=2)), ctx=Load()), ops=[NotEq()], comparators=[Tuple(elts=['
    "Name(id='guardian', ctx=Load()), Call(func=Attribute(value=Name(id='os', ctx=Load())"
    ", attr='getuid', ctx=Load()))], ctx=Load())]), body=[Raise(exc=Call(func=Name(id='Va"
    "lueError', ctx=Load()), args=[Constant(value='retirement helper peer identity differ"
    "s')]))]), Expr(value=Call(func=Attribute(value=Name(id='connection', ctx=Load()), at"
    "tr='sendall', ctx=Load()), args=[BinOp(left=BinOp(left=Call(func=Attribute(value=Nam"
    "e(id='os', ctx=Load()), attr='fsencode', ctx=Load()), args=[Call(func=Name(id='str',"
    " ctx=Load()), args=[Name(id='root', ctx=Load())])]), op=Add(), right=Constant(value="
    "b'\\x00')), op=Add(), right=Call(func=Attribute(value=Subscript(value=Name(id='fields"
    "', ctx=Load()), slice=Constant(value=7), ctx=Load()), attr='encode', ctx=Load()), ar"
    "gs=[Constant(value='ascii')]))])), Assign(targets=[Tuple(elts=[Name(id='reply', ctx="
    "Store()), Name(id='ancillary', ctx=Store()), Name(id='_', ctx=Store()), Name(id='_',"
    " ctx=Store())], ctx=Store())], value=Call(func=Attribute(value=Name(id='connection',"
    " ctx=Load()), attr='recvmsg', ctx=Load()), args=[Constant(value=6), Call(func=Attrib"
    "ute(value=Name(id='socket', ctx=Load()), attr='CMSG_SPACE', ctx=Load()), args=[Call("
    "func=Attribute(value=Name(id='struct', ctx=Load()), attr='calcsize', ctx=Load()), ar"
    "gs=[Constant(value='3i')])])])), If(test=Compare(left=Name(id='reply', ctx=Load()), "
    "ops=[NotEq()], comparators=[Constant(value=b'GRANT')]), body=[Raise(exc=Call(func=Na"
    "me(id='ValueError', ctx=Load()), args=[Constant(value='retirement permission refused"
    "')]))]), Assign(targets=[Name(id='senders', ctx=Store())], value=ListComp(elt=Subscr"
    "ipt(value=Call(func=Attribute(value=Name(id='struct', ctx=Load()), attr='unpack', ct"
    "x=Load()), args=[Constant(value='3i'), Subscript(value=Name(id='data', ctx=Load()), "
    "slice=Slice(upper=Call(func=Attribute(value=Name(id='struct', ctx=Load()), attr='cal"
    "csize', ctx=Load()), args=[Constant(value='3i')])), ctx=Load())]), slice=Slice(upper"
    '=Constant(value=2)), ctx=Load()), generators=[comprehension(target=Tuple(elts=[Name('
    "id='level', ctx=Store()), Name(id='kind', ctx=Store()), Name(id='data', ctx=Store())"
    "], ctx=Store()), iter=Name(id='ancillary', ctx=Load()), ifs=[BoolOp(op=And(), values"
    "=[Compare(left=Name(id='level', ctx=Load()), ops=[Eq()], comparators=[Attribute(valu"
    "e=Name(id='socket', ctx=Load()), attr='SOL_SOCKET', ctx=Load())]), Compare(left=Name"
    "(id='kind', ctx=Load()), ops=[Eq()], comparators=[Attribute(value=Name(id='socket', "
    "ctx=Load()), attr='SCM_CREDENTIALS', ctx=Load())])])], is_async=0)])), If(test=Compa"
    "re(left=Name(id='senders', ctx=Load()), ops=[NotEq()], comparators=[List(elts=[Tuple"
    "(elts=[Name(id='guardian', ctx=Load()), Call(func=Attribute(value=Name(id='os', ctx="
    "Load()), attr='getuid', ctx=Load()))], ctx=Load())], ctx=Load())]), body=[Raise(exc="
    "Call(func=Name(id='ValueError', ctx=Load()), args=[Constant(value='retirement grant "
    "not sent by the recorded helper')]))]), If(test=Subscript(value=Call(func=Attribute("
    "value=Name(id='select', ctx=Load()), attr='select', ctx=Load()), args=[List(elts=[Na"
    "me(id='pidfd', ctx=Load())], ctx=Load()), List(ctx=Load()), List(ctx=Load()), Consta"
    'nt(value=0)]), slice=Constant(value=0), ctx=Load()), body=[Raise(exc=Call(func=Name('
    "id='ValueError', ctx=Load()), args=[Constant(value='retirement helper exited before "
    "its grant was bound')]))])])], finalbody=[Expr(value=Call(func=Attribute(value=Name("
    "id='os', ctx=Load()), attr='close', ctx=Load()), args=[Name(id='pidfd', ctx=Load())]"
    '))])])',
    "FunctionDef(name='_veto_retirement', args=arguments(args=[arg(arg='root')]), body=[E"
    'xpr(value=Constant(value="Revoke on a sweep\'s storage error, without reading/writing'
    ' that tree.")), Import(names=[alias(name=\'socket\')]), Import(names=[alias(name=\'stru'
    "ct')]), Try(body=[With(items=[withitem(context_expr=Call(func=Attribute(value=Name(i"
    "d='socket', ctx=Load()), attr='socket', ctx=Load()), args=[Attribute(value=Name(id='"
    "socket', ctx=Load()), attr='AF_UNIX', ctx=Load()), Attribute(value=Name(id='socket',"
    " ctx=Load()), attr='SOCK_STREAM', ctx=Load())]), optional_vars=Name(id='connection',"
    " ctx=Store()))], body=[Expr(value=Call(func=Attribute(value=Name(id='connection', ct"
    "x=Load()), attr='settimeout', ctx=Load()), args=[Constant(value=2)])), Expr(value=Ca"
    "ll(func=Attribute(value=Name(id='connection', ctx=Load()), attr='connect', ctx=Load("
    ")), args=[Call(func=Name(id='_retirement_address', ctx=Load()), args=[Name(id='root'"
    ", ctx=Load())])])), Assign(targets=[Name(id='peer', ctx=Store())], value=Call(func=A"
    "ttribute(value=Name(id='struct', ctx=Load()), attr='unpack', ctx=Load()), args=[Cons"
    "tant(value='3i'), Call(func=Attribute(value=Name(id='connection', ctx=Load()), attr="
    "'getsockopt', ctx=Load()), args=[Attribute(value=Name(id='socket', ctx=Load()), attr"
    "='SOL_SOCKET', ctx=Load()), Attribute(value=Name(id='socket', ctx=Load()), attr='SO_"
    "PEERCRED', ctx=Load()), Call(func=Attribute(value=Name(id='struct', ctx=Load()), att"
    "r='calcsize', ctx=Load()), args=[Constant(value='3i')])])])), If(test=Compare(left=S"
    "ubscript(value=Name(id='peer', ctx=Load()), slice=Constant(value=1), ctx=Load()), op"
    "s=[NotEq()], comparators=[Call(func=Attribute(value=Name(id='os', ctx=Load()), attr="
    "'getuid', ctx=Load()))]), body=[Raise(exc=Call(func=Name(id='ValueError', ctx=Load()"
    "), args=[Constant(value='retention helper UID differs')]))]), Expr(value=Call(func=A"
    "ttribute(value=Name(id='connection', ctx=Load()), attr='sendall', ctx=Load()), args="
    "[Constant(value=b'R')])), If(test=Compare(left=Call(func=Attribute(value=Name(id='co"
    "nnection', ctx=Load()), attr='recv', ctx=Load()), args=[Constant(value=9)]), ops=[No"
    "tEq()], comparators=[Constant(value=b'RETAINED')]), body=[Raise(exc=Call(func=Name(i"
    "d='ValueError', ctx=Load()), args=[Constant(value='retention veto not acknowledged')"
    "]))])])], handlers=[ExceptHandler(type=Tuple(elts=[Name(id='ConnectionRefusedError',"
    " ctx=Load()), Name(id='ConnectionResetError', ctx=Load()), Name(id='BrokenPipeError'"
    ', ctx=Load())], ctx=Load()), body=[Pass()])])])',
    "FunctionDef(name='_retain_test_directory', args=arguments(), body=[Expr(value=Consta"
    "nt(value='Cancel this private helper channel; never write on a damaged filesystem.')"
    "), Import(names=[alias(name='posix')]), Import(names=[alias(name='signal')]), Global"
    "(names=['_RETIREMENT_CONTROL']), If(test=Compare(left=Call(func=Attribute(value=Name"
    "(id='os', ctx=Load()), attr='getpid', ctx=Load())), ops=[NotEq()], comparators=[Name"
    "(id='_TEST_OWNER_PID', ctx=Load())]), body=[Return()]), Assign(targets=[Tuple(elts=["
    "Name(id='child', ctx=Store()), Name(id='ticks', ctx=Store()), Name(id='cookie', ctx="
    "Store()), Name(id='descriptor', ctx=Store()), Name(id='pidfd', ctx=Store())], ctx=St"
    "ore())], value=Name(id='_RETIREMENT_CONTROL', ctx=Load())), If(test=Compare(left=Nam"
    "e(id='descriptor', ctx=Load()), ops=[Is()], comparators=[Constant(value=None)]), bod"
    "y=[Return()]), Expr(value=Call(func=Name(id='_stop_retirement_guard', ctx=Load()), a"
    "rgs=[Name(id='child', ctx=Load()), Name(id='descriptor', ctx=Load()), Name(id='pidfd"
    "', ctx=Load())])), Assign(targets=[Name(id='_RETIREMENT_CONTROL', ctx=Store())], val"
    "ue=Tuple(elts=[Name(id='child', ctx=Load()), Name(id='ticks', ctx=Load()), Name(id='"
    "cookie', ctx=Load()), Constant(value=None), Constant(value=None)], ctx=Load())), Exp"
    "r(value=Call(func=Attribute(value=Name(id='posix', ctx=Load()), attr='close', ctx=Lo"
    "ad()), args=[Name(id='descriptor', ctx=Load())])), Expr(value=Call(func=Attribute(va"
    "lue=Name(id='posix', ctx=Load()), attr='close', ctx=Load()), args=[Name(id='pidfd', "
    'ctx=Load())]))])',
    "FunctionDef(name='_cleanup_test_directory', args=arguments(args=[arg(arg='name'), ar"
    "g(arg='warn_message'), arg(arg='ignore_errors')], kwarg=arg(arg='kwargs'), defaults="
    "[Constant(value=False)]), body=[Expr(value=Constant(value='Revoke before ordinary cl"
    "eanup too: a failed cleanup cannot be retried.')), Import(names=[alias(name='select'"
    ")]), If(test=Compare(left=Call(func=Attribute(value=Name(id='os', ctx=Load()), attr="
    "'getpid', ctx=Load())), ops=[NotEq()], comparators=[Name(id='_TEST_OWNER_PID', ctx=L"
    "oad())]), body=[Expr(value=Call(func=Name(id='print', ctx=Load()), args=[Constant(va"
    "lue='TUI scratch retained:'), Name(id='name', ctx=Load()), Constant(value='- forked "
    "process does not own this tree')], keywords=[keyword(arg='file', value=Attribute(val"
    "ue=Name(id='sys', ctx=Load()), attr='stderr', ctx=Load()))])), Return()]), Assign(ta"
    "rgets=[Name(id='pidfd', ctx=Store())], value=Subscript(value=Name(id='_RETIREMENT_CO"
    "NTROL', ctx=Load()), slice=Constant(value=4), ctx=Load())), If(test=BoolOp(op=Or(), "
    "values=[Compare(left=Name(id='pidfd', ctx=Load()), ops=[Is()], comparators=[Constant"
    "(value=None)]), Subscript(value=Call(func=Attribute(value=Name(id='select', ctx=Load"
    "()), attr='select', ctx=Load()), args=[List(elts=[Name(id='pidfd', ctx=Load())], ctx"
    '=Load()), List(ctx=Load()), List(ctx=Load()), Constant(value=0)]), slice=Constant(va'
    "lue=0), ctx=Load())]), body=[Expr(value=Call(func=Name(id='print', ctx=Load()), args"
    "=[Constant(value='TUI scratch retained:'), Name(id='name', ctx=Load()), Constant(val"
    "ue='- retirement helper already revoked or lost')], keywords=[keyword(arg='file', va"
    "lue=Attribute(value=Name(id='sys', ctx=Load()), attr='stderr', ctx=Load()))])), Expr"
    "(value=Call(func=Name(id='_retain_test_directory', ctx=Load()))), Return()]), Expr(v"
    "alue=Call(func=Name(id='_retain_test_directory', ctx=Load()))), Expr(value=Call(func"
    "=Attribute(value=Attribute(value=Name(id='tempfile', ctx=Load()), attr='TemporaryDir"
    "ectory', ctx=Load()), attr='_cleanup', ctx=Load()), args=[Name(id='name', ctx=Load()"
    "), Name(id='warn_message', ctx=Load()), Name(id='ignore_errors', ctx=Load())], keywo"
    "rds=[keyword(value=Name(id='kwargs', ctx=Load()))]))])",
    "FunctionDef(name='_retire_test_siblings', args=arguments(args=[arg(arg='directory'),"
    " arg(arg='proc_root')], defaults=[Constant(value='/tmp'), Constant(value='/proc')]),"
    ' body=[Expr(value=Constant(value="Judge only namespace-qualified owners in this proc'
    "ess's procfs PID view.\\n\\n    Legacy or foreign-namespace records are retained, incl"
    'uding dead runs.\\n    A procfs mounted in an ancestor namespace is not a local PID o'
    'racle.\\n    ")), Import(names=[alias(name=\'re\')]), Import(names=[alias(name=\'shutil\''
    ")]), Import(names=[alias(name='stat')]), Import(names=[alias(name='uuid')]), For(tar"
    "get=Name(id='sibling', ctx=Store()), iter=Call(func=Attribute(value=Call(func=Name(i"
    "d='Path', ctx=Load()), args=[Name(id='directory', ctx=Load())]), attr='glob', ctx=Lo"
    "ad()), args=[Constant(value='fleet-tui-tests-*')]), body=[Try(body=[Assign(targets=["
    "Name(id='info', ctx=Store())], value=Call(func=Attribute(value=Name(id='sibling', ct"
    "x=Load()), attr='lstat', ctx=Load()))), If(test=BoolOp(op=Or(), values=[UnaryOp(op=N"
    "ot(), operand=Call(func=Attribute(value=Name(id='stat', ctx=Load()), attr='S_ISDIR',"
    " ctx=Load()), args=[Attribute(value=Name(id='info', ctx=Load()), attr='st_mode', ctx"
    "=Load())])), Compare(left=Attribute(value=Name(id='info', ctx=Load()), attr='st_uid'"
    ", ctx=Load()), ops=[NotEq()], comparators=[Call(func=Attribute(value=Name(id='os', c"
    "tx=Load()), attr='getuid', ctx=Load()))])]), body=[Raise(exc=Call(func=Name(id='Valu"
    "eError', ctx=Load()), args=[Constant(value='not an owned directory')]))]), If(test=B"
    "inOp(left=Attribute(value=Name(id='info', ctx=Load()), attr='st_mode', ctx=Load()), "
    "op=BitAnd(), right=Attribute(value=Name(id='stat', ctx=Load()), attr='S_ISVTX', ctx="
    "Load())), body=[Raise(exc=Call(func=Name(id='ValueError', ctx=Load()), args=[Constan"
    "t(value='intentionally retained initialization evidence')]))]), Assign(targets=[Name"
    "(id='owner', ctx=Store())], value=BinOp(left=Name(id='sibling', ctx=Load()), op=Div("
    "), right=Constant(value='.owner'))), Assign(targets=[Name(id='owner_info', ctx=Store"
    "())], value=Call(func=Attribute(value=Name(id='owner', ctx=Load()), attr='lstat', ct"
    'x=Load()))), If(test=BoolOp(op=Or(), values=[UnaryOp(op=Not(), operand=Call(func=Att'
    "ribute(value=Name(id='stat', ctx=Load()), attr='S_ISREG', ctx=Load()), args=[Attribu"
    "te(value=Name(id='owner_info', ctx=Load()), attr='st_mode', ctx=Load())])), Compare("
    "left=Attribute(value=Name(id='owner_info', ctx=Load()), attr='st_uid', ctx=Load()), "
    "ops=[NotEq()], comparators=[Call(func=Attribute(value=Name(id='os', ctx=Load()), att"
    "r='getuid', ctx=Load()))])]), body=[Raise(exc=Call(func=Name(id='ValueError', ctx=Lo"
    "ad()), args=[Constant(value='not an owned regular owner file')]))]), Assign(targets="
    "[Name(id='raw', ctx=Store())], value=Call(func=Attribute(value=Name(id='owner', ctx="
    "Load()), attr='read_text', ctx=Load()))), Assign(targets=[Name(id='fields', ctx=Stor"
    "e())], value=Call(func=Attribute(value=Name(id='raw', ctx=Load()), attr='split', ctx"
    "=Load()))), If(test=BoolOp(op=Or(), values=[Compare(left=Call(func=Name(id='len', ct"
    "x=Load()), args=[Name(id='fields', ctx=Load())]), ops=[NotEq()], comparators=[Consta"
    "nt(value=9)]), Compare(left=Subscript(value=Name(id='fields', ctx=Load()), slice=Con"
    "stant(value=8), ctx=Load()), ops=[NotEq()], comparators=[Constant(value='v3')]), Una"
    "ryOp(op=Not(), operand=Call(func=Attribute(value=Name(id='re', ctx=Load()), attr='fu"
    "llmatch', ctx=Load()), args=[Constant(value='[0-9a-f]{32}'), Subscript(value=Name(id"
    "='fields', ctx=Load()), slice=Constant(value=7), ctx=Load())])), Call(func=Name(id='"
    "any', ctx=Load()), args=[GeneratorExp(elt=UnaryOp(op=Not(), operand=Call(func=Attrib"
    "ute(value=Name(id='re', ctx=Load()), attr='fullmatch', ctx=Load()), args=[Constant(v"
    "alue='[1-9][0-9]*'), Subscript(value=Name(id='fields', ctx=Load()), slice=Name(id='i"
    "', ctx=Load()), ctx=Load())])), generators=[comprehension(target=Name(id='i', ctx=St"
    'ore()), iter=Tuple(elts=[Constant(value=0), Constant(value=1), Constant(value=3), Co'
    'nstant(value=4), Constant(value=5), Constant(value=6)], ctx=Load()), is_async=0)])])'
    "]), body=[Raise(exc=Call(func=Name(id='ValueError', ctx=Load()), args=[Constant(valu"
    "e='unparsable owner')]))]), Assign(targets=[Tuple(elts=[Name(id='pid', ctx=Store()),"
    " Name(id='ticks', ctx=Store())], ctx=Store())], value=Tuple(elts=[Call(func=Name(id="
    "'int', ctx=Load()), args=[Subscript(value=Name(id='fields', ctx=Load()), slice=Const"
    "ant(value=0), ctx=Load())]), Call(func=Name(id='int', ctx=Load()), args=[Subscript(v"
    "alue=Name(id='fields', ctx=Load()), slice=Constant(value=1), ctx=Load())])], ctx=Loa"
    "d())), Assign(targets=[Name(id='boot', ctx=Store())], value=Call(func=Name(id='str',"
    " ctx=Load()), args=[Call(func=Attribute(value=Name(id='uuid', ctx=Load()), attr='UUI"
    "D', ctx=Load()), args=[Subscript(value=Name(id='fields', ctx=Load()), slice=Constant"
    "(value=2), ctx=Load())])])), If(test=Compare(left=Name(id='boot', ctx=Load()), ops=["
    "NotEq()], comparators=[Subscript(value=Name(id='fields', ctx=Load()), slice=Constant"
    "(value=2), ctx=Load())]), body=[Raise(exc=Call(func=Name(id='ValueError', ctx=Load()"
    "), args=[Constant(value='noncanonical boot ID')]))]), Assign(targets=[Name(id='proc'"
    ", ctx=Store())], value=Call(func=Name(id='Path', ctx=Load()), args=[Name(id='proc_ro"
    "ot', ctx=Load())])), Assign(targets=[Name(id='namespace', ctx=Store())], value=Call("
    "func=Attribute(value=BinOp(left=Name(id='proc', ctx=Load()), op=Div(), right=Constan"
    "t(value='self/ns/pid')), attr='stat', ctx=Load()))), If(test=Compare(left=Tuple(elts"
    "=[Call(func=Name(id='int', ctx=Load()), args=[Subscript(value=Name(id='fields', ctx="
    "Load()), slice=Constant(value=3), ctx=Load())]), Call(func=Name(id='int', ctx=Load()"
    "), args=[Subscript(value=Name(id='fields', ctx=Load()), slice=Constant(value=4), ctx"
    '=Load())])], ctx=Load()), ops=[NotEq()], comparators=[Tuple(elts=[Attribute(value=Na'
    "me(id='namespace', ctx=Load()), attr='st_dev', ctx=Load()), Attribute(value=Name(id="
    "'namespace', ctx=Load()), attr='st_ino', ctx=Load())], ctx=Load())]), body=[Raise(ex"
    "c=Call(func=Name(id='ValueError', ctx=Load()), args=[Constant(value='foreign PID nam"
    "espace')]))]), Assign(targets=[Name(id='status', ctx=Store())], value=Call(func=Attr"
    "ibute(value=Call(func=Attribute(value=BinOp(left=Name(id='proc', ctx=Load()), op=Div"
    "(), right=Constant(value='self/status')), attr='read_text', ctx=Load())), attr='spli"
    "tlines', ctx=Load()))), Assign(targets=[Name(id='ids', ctx=Store())], value=ListComp"
    "(elt=Subscript(value=Call(func=Attribute(value=Name(id='line', ctx=Load()), attr='sp"
    "lit', ctx=Load())), slice=Slice(lower=Constant(value=1)), ctx=Load()), generators=[c"
    "omprehension(target=Name(id='line', ctx=Store()), iter=Name(id='status', ctx=Load())"
    ", ifs=[Call(func=Attribute(value=Name(id='line', ctx=Load()), attr='startswith', ctx"
    "=Load()), args=[Constant(value='NStgid:')])], is_async=0)])), If(test=Compare(left=N"
    "ame(id='ids', ctx=Load()), ops=[NotEq()], comparators=[List(elts=[List(elts=[Call(fu"
    "nc=Name(id='str', ctx=Load()), args=[Call(func=Attribute(value=Name(id='os', ctx=Loa"
    "d()), attr='getpid', ctx=Load()))])], ctx=Load())], ctx=Load())]), body=[Raise(exc=C"
    "all(func=Name(id='ValueError', ctx=Load()), args=[Constant(value='procfs PID view is"
    " not local')]))]), Assign(targets=[Tuple(elts=[Name(id='actual_ticks', ctx=Store()),"
    " Name(id='actual_boot', ctx=Store())], ctx=Store())], value=Call(func=Name(id='_proc"
    "ess_identity', ctx=Load()), args=[Name(id='pid', ctx=Load()), Name(id='proc_root', c"
    "tx=Load())])), If(test=Compare(left=Name(id='boot', ctx=Load()), ops=[NotEq()], comp"
    "arators=[Name(id='actual_boot', ctx=Load())]), body=[Raise(exc=Call(func=Name(id='Va"
    "lueError', ctx=Load()), args=[Constant(value='different boot ID; identity not judged"
    "')]))]), Assign(targets=[Name(id='dead', ctx=Store())], value=BoolOp(op=Or(), values"
    "=[Compare(left=Name(id='actual_ticks', ctx=Load()), ops=[Is()], comparators=[Constan"
    "t(value=None)]), Compare(left=Name(id='ticks', ctx=Load()), ops=[NotEq()], comparato"
    "rs=[Name(id='actual_ticks', ctx=Load())])])), If(test=UnaryOp(op=Not(), operand=Name"
    "(id='dead', ctx=Load())), body=[Raise(exc=Call(func=Name(id='ValueError', ctx=Load()"
    "), args=[Constant(value='owner still live')]))]), If(test=BoolOp(op=Or(), values=[Co"
    "mpare(left=Call(func=Attribute(value=Name(id='owner', ctx=Load()), attr='read_text',"
    " ctx=Load())), ops=[NotEq()], comparators=[Name(id='raw', ctx=Load())]), Compare(lef"
    "t=Call(func=Name(id='_test_path_identity', ctx=Load()), args=[Call(func=Attribute(va"
    "lue=Name(id='sibling', ctx=Load()), attr='lstat', ctx=Load()))]), ops=[NotEq()], com"
    "parators=[Call(func=Name(id='_test_path_identity', ctx=Load()), args=[Name(id='info'"
    ", ctx=Load())])]), Compare(left=Call(func=Name(id='_test_path_identity', ctx=Load())"
    ", args=[Call(func=Attribute(value=Name(id='owner', ctx=Load()), attr='lstat', ctx=Lo"
    "ad()))]), ops=[NotEq()], comparators=[Call(func=Name(id='_test_path_identity', ctx=L"
    "oad()), args=[Name(id='owner_info', ctx=Load())])])]), body=[Raise(exc=Call(func=Nam"
    "e(id='ValueError', ctx=Load()), args=[Constant(value='directory or owner changed bef"
    "ore removal')]))]), Expr(value=Call(func=Name(id='_request_retirement', ctx=Load()),"
    " args=[Name(id='sibling', ctx=Load()), Name(id='fields', ctx=Load())])), If(test=Boo"
    "lOp(op=Or(), values=[Compare(left=Call(func=Attribute(value=Name(id='owner', ctx=Loa"
    "d()), attr='read_text', ctx=Load())), ops=[NotEq()], comparators=[Name(id='raw', ctx"
    "=Load())]), Compare(left=Call(func=Name(id='_test_path_identity', ctx=Load()), args="
    "[Call(func=Attribute(value=Name(id='sibling', ctx=Load()), attr='lstat', ctx=Load())"
    ")]), ops=[NotEq()], comparators=[Call(func=Name(id='_test_path_identity', ctx=Load()"
    "), args=[Name(id='info', ctx=Load())])]), Compare(left=Call(func=Name(id='_test_path"
    "_identity', ctx=Load()), args=[Call(func=Attribute(value=Name(id='owner', ctx=Load()"
    "), attr='lstat', ctx=Load()))]), ops=[NotEq()], comparators=[Call(func=Name(id='_tes"
    "t_path_identity', ctx=Load()), args=[Name(id='owner_info', ctx=Load())])])]), body=["
    "Raise(exc=Call(func=Name(id='ValueError', ctx=Load()), args=[Constant(value='directo"
    "ry or owner changed before removal')]))]), Expr(value=Call(func=Attribute(value=Name"
    "(id='shutil', ctx=Load()), attr='rmtree', ctx=Load()), args=[Name(id='sibling', ctx="
    "Load())]))], handlers=[ExceptHandler(type=Tuple(elts=[Name(id='OSError', ctx=Load())"
    ", Name(id='ValueError', ctx=Load()), Name(id='IndexError', ctx=Load())], ctx=Load())"
    ", name='error', body=[Expr(value=Call(func=Name(id='print', ctx=Load()), args=[Const"
    "ant(value='TUI scratch retained:'), Name(id='sibling', ctx=Load()), Constant(value='"
    "-'), Call(func=Name(id='str', ctx=Load()), args=[Name(id='error', ctx=Load())])], ke"
    "ywords=[keyword(arg='file', value=Attribute(value=Name(id='sys', ctx=Load()), attr='"
    "stderr', ctx=Load()))])), If(test=BoolOp(op=And(), values=[Call(func=Name(id='isinst"
    "ance', ctx=Load()), args=[Name(id='error', ctx=Load()), Name(id='OSError', ctx=Load("
    "))]), Compare(left=Attribute(value=Name(id='error', ctx=Load()), attr='errno', ctx=L"
    'oad()), ops=[In()], comparators=[Tuple(elts=[Constant(value=5), Constant(value=28), '
    "Constant(value=30)], ctx=Load())])]), body=[Expr(value=Call(func=Name(id='_veto_reti"
    "rement', ctx=Load()), args=[Name(id='sibling', ctx=Load())])), Raise()])])])]), For("
    "target=Name(id='evidence', ctx=Store()), iter=Call(func=Attribute(value=Call(func=Na"
    "me(id='Path', ctx=Load()), args=[Name(id='directory', ctx=Load())]), attr='glob', ct"
    "x=Load()), args=[Constant(value='fleet-tui-startup-*')]), body=[Expr(value=Call(func"
    "=Name(id='print', ctx=Load()), args=[Constant(value='TUI scratch retained:'), Name(i"
    "d='evidence', ctx=Load()), Constant(value='- persistent startup evidence')], keyword"
    "s=[keyword(arg='file', value=Attribute(value=Name(id='sys', ctx=Load()), attr='stder"
    "r', ctx=Load()))]))]), For(target=Name(id='parent', ctx=Store()), iter=Call(func=Att"
    "ribute(value=Call(func=Name(id='Path', ctx=Load()), args=[Name(id='directory', ctx=L"
    "oad())]), attr='glob', ctx=Load()), args=[Constant(value='pytest-of-*')]), body=[For"
    "(target=Name(id='evidence', ctx=Store()), iter=Call(func=Attribute(value=Name(id='pa"
    "rent', ctx=Load()), attr='glob', ctx=Load()), args=[Constant(value='fleet-tui-startu"
    "p-*')]), body=[Expr(value=Call(func=Name(id='print', ctx=Load()), args=[Constant(val"
    "ue='TUI scratch retained:'), Name(id='evidence', ctx=Load()), Constant(value='- pers"
    "istent startup evidence')], keywords=[keyword(arg='file', value=Attribute(value=Name"
    "(id='sys', ctx=Load()), attr='stderr', ctx=Load()))]))])])])",
    "FunctionDef(name='_write_test_owner', args=arguments(args=[arg(arg='root'), arg(arg="
    "'proc_root'), arg(arg='control')], defaults=[Constant(value='/proc'), Constant(value"
    "=None)]), body=[Expr(value=Constant(value='Create the first payload exclusively at m"
    "ode 0600; retain partial writes.')), Assign(targets=[Tuple(elts=[Name(id='ticks', ct"
    "x=Store()), Name(id='boot', ctx=Store())], ctx=Store())], value=Call(func=Name(id='_"
    "process_identity', ctx=Load()), args=[Constant(value='self'), Name(id='proc_root', c"
    "tx=Load())])), If(test=Compare(left=Name(id='ticks', ctx=Load()), ops=[Is()], compar"
    "ators=[Constant(value=None)]), body=[Raise(exc=Call(func=Name(id='RuntimeError', ctx"
    "=Load()), args=[Constant(value='current process identity missing')]))]), Assign(targ"
    "ets=[Name(id='namespace', ctx=Store())], value=Call(func=Attribute(value=BinOp(left="
    "Call(func=Name(id='Path', ctx=Load()), args=[Name(id='proc_root', ctx=Load())]), op="
    "Div(), right=Constant(value='self/ns/pid')), attr='stat', ctx=Load()))), Assign(targ"
    "ets=[Tuple(elts=[Name(id='child', ctx=Store()), Name(id='child_ticks', ctx=Store()),"
    " Name(id='cookie', ctx=Store()), Name(id='_', ctx=Store()), Name(id='_', ctx=Store()"
    ")], ctx=Store())], value=BoolOp(op=Or(), values=[Name(id='control', ctx=Load()), Nam"
    "e(id='_RETIREMENT_CONTROL', ctx=Load())])), Assign(targets=[Name(id='payload', ctx=S"
    'tore())], value=Call(func=Attribute(value=JoinedStr(values=[FormattedValue(value=Cal'
    "l(func=Attribute(value=Name(id='os', ctx=Load()), attr='getpid', ctx=Load())), conve"
    "rsion=-1), Constant(value=' '), FormattedValue(value=Name(id='ticks', ctx=Load()), c"
    "onversion=-1), Constant(value=' '), FormattedValue(value=Name(id='boot', ctx=Load())"
    ", conversion=-1), Constant(value=' '), FormattedValue(value=Attribute(value=Name(id="
    "'namespace', ctx=Load()), attr='st_dev', ctx=Load()), conversion=-1), Constant(value"
    "=' '), FormattedValue(value=Attribute(value=Name(id='namespace', ctx=Load()), attr='"
    "st_ino', ctx=Load()), conversion=-1), Constant(value=' '), FormattedValue(value=Name"
    "(id='child', ctx=Load()), conversion=-1), Constant(value=' '), FormattedValue(value="
    "Name(id='child_ticks', ctx=Load()), conversion=-1), Constant(value=' '), FormattedVa"
    "lue(value=Name(id='cookie', ctx=Load()), conversion=-1), Constant(value=' v3\\n')]), "
    "attr='encode', ctx=Load()), args=[Constant(value='ascii')])), Assign(targets=[Name(i"
    "d='descriptor', ctx=Store())], value=Call(func=Attribute(value=Name(id='os', ctx=Loa"
    "d()), attr='open', ctx=Load()), args=[BinOp(left=Call(func=Name(id='Path', ctx=Load("
    ")), args=[Name(id='root', ctx=Load())]), op=Div(), right=Constant(value='.owner')), "
    "BinOp(left=BinOp(left=Attribute(value=Name(id='os', ctx=Load()), attr='O_CREAT', ctx"
    "=Load()), op=BitOr(), right=Attribute(value=Name(id='os', ctx=Load()), attr='O_EXCL'"
    ", ctx=Load())), op=BitOr(), right=Attribute(value=Name(id='os', ctx=Load()), attr='O"
    "_WRONLY', ctx=Load())), Constant(value=384)])), Try(body=[If(test=Compare(left=Call("
    "func=Attribute(value=Name(id='os', ctx=Load()), attr='write', ctx=Load()), args=[Nam"
    "e(id='descriptor', ctx=Load()), Name(id='payload', ctx=Load())]), ops=[NotEq()], com"
    "parators=[Call(func=Name(id='len', ctx=Load()), args=[Name(id='payload', ctx=Load())"
    "])]), body=[Raise(exc=Call(func=Name(id='OSError', ctx=Load()), args=[Constant(value"
    "=5), Constant(value='short owner write'), Call(func=Name(id='str', ctx=Load()), args"
    "=[BinOp(left=Call(func=Name(id='Path', ctx=Load()), args=[Name(id='root', ctx=Load()"
    ")]), op=Div(), right=Constant(value='.owner'))])]))])], finalbody=[Expr(value=Call(f"
    "unc=Attribute(value=Name(id='os', ctx=Load()), attr='close', ctx=Load()), args=[Name"
    "(id='descriptor', ctx=Load())]))])])",
    "FunctionDef(name='pytest_addoption', args=arguments(args=[arg(arg='parser')]), body="
    "[Expr(value=Call(func=Attribute(value=Name(id='parser', ctx=Load()), attr='addoption"
    "', ctx=Load()), args=[Constant(value='--keep-startup-artifacts')], keywords=[keyword"
    "(arg='action', value=Constant(value='store_true')), keyword(arg='help', value=Consta"
    "nt(value='Keep startup inputs and receipts for evidence review'))])), Expr(value=Cal"
    "l(func=Attribute(value=Name(id='parser', ctx=Load()), attr='addoption', ctx=Load()),"
    " args=[Constant(value='--startup-artifacts-root')], keywords=[keyword(arg='help', va"
    "lue=Constant(value='Kept output directory from fleet-data-path test-scratch-kept <ru"
    "n>'))]))])",
    "Assign(targets=[Attribute(value=Name(id='sys', ctx=Load()), attr='_fleet_startup_art"
    "ifact_helper', ctx=Store())], value=Call(func=Attribute(value=Call(func=Name(id='__i"
    "mport__', ctx=Load()), args=[Constant(value='shutil')]), attr='which', ctx=Load()), "
    "args=[Constant(value='fleet-data-path')]))",
    "Expr(value=Call(func=Name(id='_retire_test_siblings', ctx=Load())))",
    "Assign(targets=[Name(id='_TEST_OWNER_PID', ctx=Store())], value=Call(func=Attribute("
    "value=Name(id='os', ctx=Load()), attr='getpid', ctx=Load())))",
    "Assign(targets=[Name(id='_TEST_DIRECTORY', ctx=Store())], value=Call(func=Attribute("
    "value=Name(id='tempfile', ctx=Load()), attr='TemporaryDirectory', ctx=Load()), keywo"
    "rds=[keyword(arg='prefix', value=Constant(value='fleet-tui-tests-')), keyword(arg='d"
    "ir', value=Constant(value='/tmp'))]))",
    "Try(body=[Expr(value=Call(func=Attribute(value=Call(func=Name(id='Path', ctx=Load())"
    ", args=[Attribute(value=Name(id='_TEST_DIRECTORY', ctx=Load()), attr='name', ctx=Loa"
    "d())]), attr='chmod', ctx=Load()), args=[Constant(value=960)])), Assign(targets=[Nam"
    "e(id='_RETIREMENT_CONTROL', ctx=Store())], value=Call(func=Name(id='_start_retiremen"
    "t_guard', ctx=Load()), args=[Attribute(value=Name(id='_TEST_DIRECTORY', ctx=Load()),"
    " attr='name', ctx=Load()), Call(func=Attribute(value=Name(id='os', ctx=Load()), attr"
    "='getpid', ctx=Load())), Subscript(value=Call(func=Name(id='_process_identity', ctx="
    "Load()), args=[Constant(value='self')]), slice=Constant(value=0), ctx=Load())])), As"
    "sign(targets=[Name(id='_finalizer_parts', ctx=Store())], value=Call(func=Attribute(v"
    "alue=Attribute(value=Name(id='_TEST_DIRECTORY', ctx=Load()), attr='_finalizer', ctx="
    "Load()), attr='detach', ctx=Load()))), Assign(targets=[Attribute(value=Name(id='_TES"
    "T_DIRECTORY', ctx=Load()), attr='_finalizer', ctx=Store())], value=Call(func=Attribu"
    "te(value=Call(func=Name(id='__import__', ctx=Load()), args=[Constant(value='weakref'"
    ")]), attr='finalize', ctx=Load()), args=[Name(id='_TEST_DIRECTORY', ctx=Load()), Nam"
    "e(id='_cleanup_test_directory', ctx=Load()), Starred(value=Subscript(value=Name(id='"
    "_finalizer_parts', ctx=Load()), slice=Constant(value=2), ctx=Load()), ctx=Load())], "
    "keywords=[keyword(value=Subscript(value=Name(id='_finalizer_parts', ctx=Load()), sli"
    "ce=Constant(value=3), ctx=Load()))])), Delete(targets=[Name(id='_finalizer_parts', c"
    "tx=Del())]), Expr(value=Call(func=Name(id='_write_test_owner', ctx=Load()), args=[At"
    "tribute(value=Name(id='_TEST_DIRECTORY', ctx=Load()), attr='name', ctx=Load())]))], "
    "handlers=[ExceptHandler(type=Name(id='BaseException', ctx=Load()), body=[Expr(value="
    "Call(func=Attribute(value=Attribute(value=Name(id='_TEST_DIRECTORY', ctx=Load()), at"
    "tr='_finalizer', ctx=Load()), attr='detach', ctx=Load()))), If(test=Compare(left=Con"
    "stant(value='_RETIREMENT_CONTROL'), ops=[In()], comparators=[Call(func=Name(id='glob"
    "als', ctx=Load()))]), body=[Expr(value=Call(func=Name(id='_retain_test_directory', c"
    'tx=Load())))]), Raise()])])',
    "Assign(targets=[Name(id='_TEST_ROOT', ctx=Store())], value=Call(func=Name(id='Path',"
    " ctx=Load()), args=[Attribute(value=Name(id='_TEST_DIRECTORY', ctx=Load()), attr='na"
    "me', ctx=Load())]))",
    "Assign(targets=[Name(id='_IMPORT_ENV', ctx=Store())], value=Dict())",
    "Try(body=[For(target=Tuple(elts=[Name(id='_key', ctx=Store()), Name(id='_subdir', ct"
    'x=Store())], ctx=Store()), iter=Call(func=Attribute(value=Dict(keys=[Constant(value='
    "'HOME'), Constant(value='XDG_CONFIG_HOME'), Constant(value='XDG_CACHE_HOME'), Consta"
    "nt(value='XDG_DATA_HOME'), Constant(value='XDG_STATE_HOME'), Constant(value='XDG_RUN"
    "TIME_DIR'), Constant(value='XDG_CONFIG_DIRS'), Constant(value='XDG_DATA_DIRS')], val"
    "ues=[Constant(value='home'), Constant(value='config'), Constant(value='cache'), Cons"
    "tant(value='data'), Constant(value='state'), Constant(value='runtime'), Constant(val"
    "ue='config-dirs'), Constant(value='data-dirs')]), attr='items', ctx=Load())), body=["
    "Assign(targets=[Name(id='_directory', ctx=Store())], value=BinOp(left=Name(id='_TEST"
    "_ROOT', ctx=Load()), op=Div(), right=Name(id='_subdir', ctx=Load()))), Expr(value=Ca"
    "ll(func=Attribute(value=Name(id='_directory', ctx=Load()), attr='mkdir', ctx=Load())"
    ", keywords=[keyword(arg='mode', value=Constant(value=448))])), Assign(targets=[Subsc"
    "ript(value=Attribute(value=Name(id='os', ctx=Load()), attr='environ', ctx=Load()), s"
    "lice=Name(id='_key', ctx=Load()), ctx=Store())], value=Call(func=Name(id='str', ctx="
    "Load()), args=[Name(id='_directory', ctx=Load())])), Assign(targets=[Subscript(value"
    "=Name(id='_IMPORT_ENV', ctx=Load()), slice=Name(id='_key', ctx=Load()), ctx=Store())"
    "], value=Subscript(value=Attribute(value=Name(id='os', ctx=Load()), attr='environ', "
    "ctx=Load()), slice=Name(id='_key', ctx=Load()), ctx=Load()))]), Expr(value=Call(func"
    "=Attribute(value=Name(id='_TEST_ROOT', ctx=Load()), attr='chmod', ctx=Load()), args="
    "[Constant(value=448)]))], handlers=[ExceptHandler(type=Name(id='BaseException', ctx="
    "Load()), body=[Expr(value=Call(func=Attribute(value=Attribute(value=Name(id='_TEST_D"
    "IRECTORY', ctx=Load()), attr='_finalizer', ctx=Load()), attr='detach', ctx=Load())))"
    ", Expr(value=Call(func=Name(id='_retain_test_directory', ctx=Load()))), Raise()])])",
    "For(target=Name(id='_key', ctx=Store()), iter=Call(func=Name(id='tuple', ctx=Load())"
    ", args=[Attribute(value=Name(id='os', ctx=Load()), attr='environ', ctx=Load())]), bo"
    "dy=[If(test=Call(func=Attribute(value=Name(id='_key', ctx=Load()), attr='startswith'"
    ", ctx=Load()), args=[Constant(value='FLEET_')]), body=[Delete(targets=[Subscript(val"
    "ue=Attribute(value=Name(id='os', ctx=Load()), attr='environ', ctx=Load()), slice=Nam"
    "e(id='_key', ctx=Load()), ctx=Del())])])])",
    "Assign(targets=[Subscript(value=Attribute(value=Name(id='os', ctx=Load()), attr='env"
    "iron', ctx=Load()), slice=Constant(value='PATH'), ctx=Store())], value=Attribute(val"
    "ue=Name(id='os', ctx=Load()), attr='defpath', ctx=Load()))",
    "Assign(targets=[Subscript(value=Attribute(value=Name(id='os', ctx=Load()), attr='env"
    "iron', ctx=Load()), slice=Constant(value='SHELL'), ctx=Store())], value=Constant(val"
    "ue='/bin/bash'))",
    "For(target=Name(id='_key', ctx=Store()), iter=Tuple(elts=[Constant(value='BASH_ENV')"
    ", Constant(value='ENV'), Constant(value='ZDOTDIR'), Constant(value='HISTFILE'), Cons"
    "tant(value='INPUTRC')], ctx=Load()), body=[Expr(value=Call(func=Attribute(value=Attr"
    "ibute(value=Name(id='os', ctx=Load()), attr='environ', ctx=Load()), attr='pop', ctx="
    "Load()), args=[Name(id='_key', ctx=Load()), Constant(value=None)]))])",
    "FunctionDef(name='_dispatch_fixture', args=arguments(args=[arg(arg='args'), arg(arg="
    '\'scratch\')]), body=[Expr(value=Constant(value="Allow only the tests\' true + touch sc'
    'ript, with every file in tmp_path.\\n\\n    Allowing a shell name alone would permit a'
    'rbitrary commands. Reconstruct\\n    the exact fixture grammar instead, including quo'
    'ting and suffixes.\\n    ")), Import(names=[alias(name=\'shlex\')]), If(test=BoolOp(op='
    "Or(), values=[UnaryOp(op=Not(), operand=Call(func=Name(id='isinstance', ctx=Load()),"
    " args=[Name(id='args', ctx=Load()), Tuple(elts=[Name(id='tuple', ctx=Load()), Name(i"
    "d='list', ctx=Load())], ctx=Load())])), Compare(left=Call(func=Name(id='len', ctx=Lo"
    "ad()), args=[Name(id='args', ctx=Load())]), ops=[NotEq()], comparators=[Constant(val"
    'ue=4)])]), body=[Return(value=Constant(value=False))]), If(test=BoolOp(op=Or(), valu'
    "es=[Compare(left=Call(func=Name(id='list', ctx=Load()), args=[Subscript(value=Name(i"
    "d='args', ctx=Load()), slice=Slice(upper=Constant(value=3)), ctx=Load())]), ops=[Not"
    "Eq()], comparators=[List(elts=[Constant(value='setsid'), Constant(value='sh'), Const"
    "ant(value='-c')], ctx=Load())]), UnaryOp(op=Not(), operand=Call(func=Name(id='isinst"
    "ance', ctx=Load()), args=[Subscript(value=Name(id='args', ctx=Load()), slice=Constan"
    "t(value=3), ctx=Load()), Name(id='str', ctx=Load())]))]), body=[Return(value=Constan"
    "t(value=False))]), Try(body=[Assign(targets=[Name(id='words', ctx=Store())], value=C"
    "all(func=Attribute(value=Name(id='shlex', ctx=Load()), attr='split', ctx=Load()), ar"
    "gs=[Subscript(value=Name(id='args', ctx=Load()), slice=Constant(value=3), ctx=Load()"
    ")]))], handlers=[ExceptHandler(type=Name(id='ValueError', ctx=Load()), body=[Return("
    'value=Constant(value=False))])]), If(test=BoolOp(op=Or(), values=[Compare(left=Call('
    "func=Name(id='len', ctx=Load()), args=[Name(id='words', ctx=Load())]), ops=[NotEq()]"
    ", comparators=[Constant(value=8)]), Compare(left=Subscript(value=Name(id='words', ct"
    'x=Load()), slice=Constant(value=0), ctx=Load()), ops=[NotEq()], comparators=[Constan'
    "t(value='true')])]), body=[Return(value=Constant(value=False))]), Assign(targets=[Tu"
    "ple(elts=[Name(id='brief', ctx=Store()), Name(id='out', ctx=Store()), Name(id='log',"
    " ctx=Store()), Name(id='done', ctx=Store())], ctx=Store())], value=GeneratorExp(elt="
    "Subscript(value=Name(id='words', ctx=Load()), slice=Name(id='i', ctx=Load()), ctx=Lo"
    "ad()), generators=[comprehension(target=Name(id='i', ctx=Store()), iter=Tuple(elts=["
    'Constant(value=1), Constant(value=2), Constant(value=4), Constant(value=7)], ctx=Loa'
    "d()), is_async=0)])), Assign(targets=[Name(id='base', ctx=Store())], value=Call(func"
    "=Attribute(value=Name(id='brief', ctx=Load()), attr='removesuffix', ctx=Load()), arg"
    "s=[Constant(value='.brief')])), If(test=Compare(left=List(elts=[Name(id='brief', ctx"
    "=Load()), Name(id='out', ctx=Load()), Name(id='log', ctx=Load()), Name(id='done', ct"
    'x=Load())], ctx=Load()), ops=[NotEq()], comparators=[ListComp(elt=BinOp(left=Name(id'
    "='base', ctx=Load()), op=Add(), right=Name(id='suffix', ctx=Load())), generators=[co"
    "mprehension(target=Name(id='suffix', ctx=Store()), iter=Tuple(elts=[Constant(value='"
    ".brief'), Constant(value='.out'), Constant(value='.log'), Constant(value='.done')], "
    'ctx=Load()), is_async=0)])]), body=[Return(value=Constant(value=False))]), If(test=U'
    "naryOp(op=Not(), operand=Call(func=Name(id='all', ctx=Load()), args=[GeneratorExp(el"
    "t=Call(func=Attribute(value=Call(func=Attribute(value=Call(func=Name(id='Path', ctx="
    "Load()), args=[Name(id='path', ctx=Load())]), attr='resolve', ctx=Load())), attr='is"
    "_relative_to', ctx=Load()), args=[Call(func=Attribute(value=Name(id='scratch', ctx=L"
    "oad()), attr='resolve', ctx=Load()))]), generators=[comprehension(target=Name(id='pa"
    "th', ctx=Store()), iter=Tuple(elts=[Name(id='brief', ctx=Load()), Name(id='out', ctx"
    "=Load()), Name(id='log', ctx=Load()), Name(id='done', ctx=Load())], ctx=Load()), is_"
    'async=0)])])), body=[Return(value=Constant(value=False))]), Assign(targets=[Name(id='
    "'expected', ctx=Store())], value=JoinedStr(values=[Constant(value='true '), Formatte"
    "dValue(value=Call(func=Attribute(value=Name(id='shlex', ctx=Load()), attr='quote', c"
    "tx=Load()), args=[Name(id='brief', ctx=Load())]), conversion=-1), Constant(value=' '"
    "), FormattedValue(value=Call(func=Attribute(value=Name(id='shlex', ctx=Load()), attr"
    "='quote', ctx=Load()), args=[Name(id='out', ctx=Load())]), conversion=-1), Constant("
    "value=' > '), FormattedValue(value=Call(func=Attribute(value=Name(id='shlex', ctx=Lo"
    "ad()), attr='quote', ctx=Load()), args=[Name(id='log', ctx=Load())]), conversion=-1)"
    ", Constant(value=' 2>&1; touch '), FormattedValue(value=Call(func=Attribute(value=Na"
    "me(id='shlex', ctx=Load()), attr='quote', ctx=Load()), args=[Name(id='done', ctx=Loa"
    "d())]), conversion=-1)])), Return(value=Compare(left=Subscript(value=Name(id='args',"
    ' ctx=Load()), slice=Constant(value=3), ctx=Load()), ops=[Eq()], comparators=[Name(id'
    "='expected', ctx=Load())]))])",
)


def test_requires_unoptimized_interpreter():
    if sys.flags.optimize:
        pytest.fail(
            'Run this suite without -O/-OO: source/runtime comparisons use '
            'the supported compilation mode, optimization level zero. '
            'Pytest-rewritten assertions remain active under plain -O; '
            '-OO strips docstrings.')


def test_isolation_module_code_is_pinned():
    # The module the plugin loaded is this directory's _isolation.py, and the
    # package's __init__.py, which runs before it, holds no code.
    here = Path(__file__).resolve()
    assert Path(_isolation.__file__).resolve() == here.with_name('_isolation.py')
    assert sys.modules[_isolation.__name__] is _isolation
    assert _isolation_module_form(ast.parse(here.with_name('__init__.py').read_bytes())) == ()
    # The written form and its sha256 agree, so neither changes alone.
    assert _pin_sha256(_ISOLATION_MODULE_PIN) == _ISOLATION_MODULE_PIN_SHA256
    form = _isolation_module_form(_isolation_source()[1])
    assert form == _ISOLATION_MODULE_PIN, (
        'the code of _isolation.py changed. If on purpose, review it, then write\n'
        f'_ISOLATION_MODULE_PIN_SHA256 = {_pin_sha256(form)!r}\n'
        f'_ISOLATION_MODULE_PIN = {_pin_literal(form)}')


def test_pin_normalisation_ignores_layout_only():
    # Controls for (a): comments, blank lines, parentheses and quote style
    # leave the form unchanged; a changed docstring, name or constant does not.
    def form(source):
        return _isolation_module_form(compile(
            source, '<pin-layout-control>', 'exec', flags=ast.PyCF_ONLY_AST,
            dont_inherit=True, optimize=0))

    base = form('def f(v):\n    """Doc."""\n    return v if (type(v) is str) else None\n')
    assert form('# c\n\ndef f(v):\n    """Doc."""\n    # c\n    return (v if type(v) is str\n'
                '            else None)\n') == base
    assert form("def f(v):\n    '''Doc.'''\n    return v if type(v) is str else None\n") == base
    for changed in ('def f(v):\n    """Doc!"""\n    return v if (type(v) is str) else None\n',
                    'def f(v):\n    """Doc."""\n    return v if (type(v) is bytes) else None\n',
                    'def f(v):\n    """Doc."""\n    return v if (type(v) is str) else 0\n',
                    'def f(w):\n    """Doc."""\n    return w if (type(w) is str) else None\n'):
        assert form(changed) != base, changed


def _module_function(tree, name):
    """The single top-level def of `name` in a module AST."""
    definitions = [node for node in ast.walk(tree)
                   if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                   and node.name == name]
    assert len(definitions) == 1 and definitions[0] in tree.body, name
    return definitions[0]


def _compiled_from_source(text, definition, filename):
    """The code of `definition`, compiled by this interpreter from its source text.

    Compared across the eight _CODE_FIELDS, never with a bytecode literal:
    3.13 and later strip docstring indentation in code constants, and bytecode differs
    between versions. Compile in the full module symbol-table context and select
    the top-level code object by definition name, retaining line positions
    without padding.
    """
    # Compile in the full module symbol-table context. A def-only compile
    # treats imported module names differently when selecting method opcodes.
    module = compile(text, filename, 'exec', dont_inherit=True)
    [code] = [const for const in module.co_consts
              if type(const) is CodeType and const.co_name == definition.name]
    return code


_CODE_FIELDS = ('co_code', 'co_consts', 'co_names', 'co_varnames', 'co_argcount',
                'co_kwonlyargcount', 'co_posonlyargcount', 'co_flags')


def test_exemption_gate_binding_is_the_module_code():
    # (b) The object the scan calls is one plain function holding exactly the
    # code this interpreter compiles from the def in _isolation.py.
    gate = _isolation._exact_location
    namespace = vars(_isolation)
    # Called often enough that 3.11 and 3.12 have specialised its bytecode, as
    # the import-time call and the corpus tests also do; co_code reads the
    # unspecialised instructions.
    for value in ('/a', 'a', b'/a', None) * 64:
        gate(value, 'warm.location', [])
    assert _isolation._license_candidate_files.__globals__ is namespace
    assert namespace['_exact_location'] is gate
    assert _isolation._license_candidate_files.__globals__['_exact_location'] is gate
    assert type(gate) is FunctionType and gate.__globals__ is namespace
    assert not hasattr(gate, '__wrapped__')
    assert (gate.__defaults__, gate.__kwdefaults__, gate.__closure__) == (None, None, None)
    text, tree = _isolation_source()
    definition = _module_function(tree, '_exact_location')
    assert definition.decorator_list == []
    code = gate.__code__
    assert type(code) is CodeType
    assert code.co_firstlineno == definition.lineno
    fresh = _compiled_from_source(text, definition, _isolation.__file__)
    assert ({field: getattr(code, field) for field in _CODE_FIELDS}
            == {field: getattr(fresh, field) for field in _CODE_FIELDS})
    # The module's other top-level functions (the gate's caller among them)
    # hold their own code at run time too.
    for other in tree.body:
        if isinstance(other, (ast.FunctionDef, ast.AsyncFunctionDef)) and other is not definition:
            function = namespace[other.name]
            assert type(function) is FunctionType and function.__globals__ is namespace, other.name
            assert function.__closure__ is None and other.decorator_list == [], other.name
            assert function.__code__.co_firstlineno == other.lineno, other.name
            fresh = _compiled_from_source(text, other, _isolation.__file__)
            assert ({field: getattr(function.__code__, field) for field in _CODE_FIELDS}
                    == {field: getattr(fresh, field) for field in _CODE_FIELDS}), other.name


def _global_reads(code):
    """The names `code` reads as globals, from its LOAD_GLOBAL instructions.

    Not co_names, which also holds attribute names (path, isabs, append).
    """
    instructions = list(dis.get_instructions(code))
    assert not any(isinstance(const, CodeType) for const in code.co_consts)
    assert not [instruction.opname for instruction in instructions if instruction.opname in (
        'LOAD_NAME', 'STORE_GLOBAL', 'DELETE_GLOBAL', 'LOAD_DEREF', 'LOAD_CLASSDEREF',
        'LOAD_FROM_DICT_OR_GLOBALS', 'IMPORT_NAME')]
    return {instruction.argval for instruction in instructions
            if instruction.opname == 'LOAD_GLOBAL'}


def _attribute_chains(definition):
    """The longest attribute chains in `definition` that start at a name."""
    chains = set()
    for node in ast.walk(definition):
        parts = []
        while isinstance(node, ast.Attribute):
            parts.append(node.attr)
            node = node.value
        if parts and isinstance(node, ast.Name):
            chains.add((node.id, *reversed(parts)))
    return {chain for chain in chains
            if not any(other != chain and other[:len(chain)] == chain for other in chains)}


# Builtins reached from a literal, with no name lookup.
_LITERAL_BUILTINS = {'type': (0).__class__.__class__, 'str': ''.__class__, 'bytes': b''.__class__}


def _check_global(namespace, name, functions):
    """`name` read as a global from `namespace` resolves to what it names."""
    builtins_module = sys.modules['builtins']
    assert any(namespace['__builtins__'] is candidate
               for candidate in (builtins_module, vars(builtins_module))), name
    if name in namespace:
        value = namespace[name]
        if name in functions:
            assert value is functions[name], name
        else:
            assert isinstance(value, ModuleType) and value is sys.modules[name], name
        return
    value = vars(builtins_module)[name]
    if name in _LITERAL_BUILTINS:
        assert value is _LITERAL_BUILTINS[name], name
    else:
        assert (type(value) is BuiltinFunctionType and value.__self__ is builtins_module
                and value.__name__ == name), name


@pytest.fixture(scope='module')
def _clean_posixpath_code():
    """The four code fields of isabs/_get_sep in an -I -S child of sys.executable.

    One child starts at module setup, AFTER conftest import, before its per-test
    process gate. This trusts sys.executable; it does not establish interpreter
    provenance or detect a replacement with identical bytecode.
    """
    import subprocess

    script = ('import posixpath\n'
              'for function in (posixpath.isabs, posixpath._get_sep):\n'
              '    code = function.__code__\n'
              '    print(repr((code.co_code, code.co_consts, code.co_names, code.co_varnames)))\n')
    child = subprocess.run([sys.executable, '-B', '-I', '-S', '-c', script], capture_output=True,
                           text=True, env={}, cwd='/', timeout=120, check=True)
    isabs, get_sep = map(ast.literal_eval, child.stdout.splitlines())
    return {'isabs': isabs, '_get_sep': get_sep}


def _check_callee(function, module, clean):
    assert type(function) is FunctionType and function.__globals__ is vars(module)
    assert (function.__defaults__, function.__kwdefaults__, function.__closure__) == (
        None, None, None)
    code = function.__code__
    # Never co_filename or inspect.getsource: a forged code object can keep
    # '<frozen posixpath>' and a line number.
    assert (code.co_code, code.co_consts, code.co_names, code.co_varnames) == clean


def test_exemption_gate_reads_the_interpreters_own_names(_clean_posixpath_code):
    # (c) Named globals resolve as checked below; the callees' four code fields
    # equal the module-setup child's. Limits are stated above the pin.
    gate = _isolation._exact_location
    namespace = vars(_isolation)
    reads = _global_reads(gate.__code__)
    # Control: co_names holds more than the globals the code reads.
    assert reads < set(gate.__code__.co_names)
    assert reads == {'type', 'str', 'os'}
    assert gate.__builtins__ is vars(sys.modules['builtins'])
    for name in reads:
        _check_global(namespace, name, {})
    definition = _module_function(_isolation_source()[1], '_exact_location')
    chains = {chain for chain in _attribute_chains(definition) if chain[0] in reads}
    assert chains == {('os', 'path', 'isabs')}
    # os.path is posixpath on POSIX; isabs has the child's four code fields.
    posixpath = sys.modules['posixpath']
    assert os.name == 'posix' and sys.modules['os'].path is posixpath
    isabs, get_sep = vars(posixpath)['isabs'], vars(posixpath)['_get_sep']
    assert posixpath.isabs is isabs
    _check_callee(isabs, posixpath, _clean_posixpath_code['isabs'])
    _check_callee(get_sep, posixpath, _clean_posixpath_code['_get_sep'])
    # What isabs and _get_sep read resolve as named too; os.fspath is the
    # posix builtin.
    for function in (isabs, get_sep):
        for name in _global_reads(function.__code__):
            _check_global(vars(posixpath), name, {'_get_sep': get_sep})
    posix = sys.modules['posix']
    fspath = vars(sys.modules['os'])['fspath']
    assert fspath is vars(posix)['fspath']
    assert type(fspath) is BuiltinFunctionType and fspath.__self__ is posix
    assert fspath.__name__ == 'fspath'


def test_license_snapshot_matches_a_recomputation():
    # (d) The import-time snapshot is what the pinned code computes now.
    files, refused = _isolation._ORIGINAL_LICENSE_FILES
    assert files and refused == ()
    assert _isolation._license_candidate_files(
        _isolation._ORIGINAL_INTERPRETER_LOCATIONS) == _isolation._ORIGINAL_LICENSE_FILES


def test_detector_finds_defaults_methods_and_nested_containers(tmp_path):
    probe = ModuleType('positive_control')
    root = str(tmp_path)
    exec('def function(path=ROOT, *, state=ROOT): pass\n'
         'class Owner:\n'
         ' def method(self, path=ROOT): pass\n'
         ' @staticmethod\n'
         ' def static(*, path=ROOT): pass\n'
         ' @classmethod\n'
         ' def class_method(cls, path=ROOT): pass\n', {'ROOT': root, '__name__': probe.__name__},
         vars(probe))
    probe.NESTED = ({'paths': [tmp_path]}, {root}, frozenset({tmp_path}), {tmp_path: 'value'})
    probe.CYCLE = []
    probe.CYCLE.append(probe.CYCLE)
    found = _settings_paths_in_roots([probe], (tmp_path,))
    assert len(found) == 9, found
    assert sum('__defaults__' in hit or '__kwdefaults__' in hit for hit in found) == 5
    assert not _settings_paths_in_roots([probe], (tmp_path / 'unrelated',))


def test_home_and_xdg_redirect_precedes_app_import():
    from fleet_tui import app

    expected_package = Path(__file__).resolve().parents[1] / "fleet_tui"
    assert Path(fleet_tui.__file__).resolve() == expected_package / "__init__.py"

    scratch = getattr(conftest, '_TEST_ROOT', None)
    assert scratch is not None, 'HOME/XDG isolation was not installed before collection'
    # Other suites may redirect HOME again in a per-test fixture. The contract
    # here is the environment established before application imports.
    imported_env = conftest._IMPORT_ENV
    for key in ('HOME', 'XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME',
                'XDG_STATE_HOME', 'XDG_RUNTIME_DIR', 'XDG_CONFIG_DIRS', 'XDG_DATA_DIRS'):
        for root in imported_env[key].split(os.pathsep):
            assert Path(root).is_relative_to(scratch), (key, root)
    # This constant was evaluated when app first imported, before test fixtures.
    assert Path(app.THEME_FILE).is_relative_to(scratch)
    assert Path(imported_env['XDG_RUNTIME_DIR']).stat().st_mode & 0o777 == 0o700


@pytest.mark.parametrize('kind', ['wraps', 'lru_cache', 'partial_args', 'partial_keywords',
    'closure', 'property', 'instance', 'slots', 'bytes', 'deque', 'mappingproxy', 'annotations'])
def test_detector_finds_indirect_settings(tmp_path, kind):
    probe = ModuleType('positive_control')
    root = str(tmp_path / 'planted')
    def target(path=root):
        return path
    if kind == 'wraps':
        @wraps(target)
        def value():
            pass
    elif kind == 'lru_cache':
        value = lru_cache()(target)
    elif kind == 'partial_args':
        value = partial(str, root)
    elif kind == 'partial_keywords':
        value = partial(dict, path=root)
    elif kind == 'closure':
        value = lambda: root
    elif kind == 'property':
        value = property(target)
    elif kind == 'instance':
        value = type('Settings', (), {})()
        value.path = root
    elif kind == 'slots':
        value = type('Settings', (), {'__slots__': ('path',)})()
        value.path = root
    elif kind == 'bytes':
        value = os.fsencode(root) + b'/nonutf8-\xff'
    elif kind == 'deque':
        value = deque([root])
    elif kind == 'mappingproxy':
        value = MappingProxyType({'path': root})
    elif kind == 'annotations':
        def value():
            pass
        value.__annotations__ = {'path': root}
    probe.setting = value
    found = _settings_paths_in_roots([probe], (tmp_path,))
    assert found and all(root in hit for hit in found), (kind, found)
    assert not _settings_paths_in_roots([probe], (tmp_path / 'unrelated',))


def test_detector_does_not_evaluate_descriptors(tmp_path):
    probe = ModuleType('positive_control')
    class Settings:
        @property
        def __dict__(self):
            raise AssertionError('descriptor executed')
        @property
        def path(self):
            raise AssertionError('getter executed')
    probe.setting = Settings()
    assert _settings_paths_in_roots([probe], (tmp_path,)) == [
        'positive_control.setting.__dict__ = <uninspectable dictionary storage>']


@pytest.mark.parametrize('kind', ['pureposix', 'bytearray', 'memoryview', 'array_b',
                                  'array_B', pytest.param('array_u', marks=pytest.mark.skipif(
                                      'u' not in typecodes, reason='array u unavailable')),
                                  'default_factory', 'generator',
                                  pytest.param('array_w', marks=pytest.mark.skipif(
                                      'w' not in typecodes, reason='array w unavailable'))])
def test_detector_finds_stored_path_kinds(tmp_path, kind):
    from pathlib import PurePosixPath
    from array import array
    from collections import defaultdict
    root = str(tmp_path / 'planted')
    encoded = os.fsencode(root)
    calls = []
    def factory(path=root):
        calls.append('factory')
        return path
    def generator(path=root):
        calls.append('generator')
        yield path
    # Retain deprecated u coverage without constructing it in every case.
    with warnings.catch_warnings():
        warnings.filterwarnings('ignore', category=DeprecationWarning,
                                message=".*[uU]n?icode.*|.*type code.*|.*'u'.*")
        unicode_u = array('u', root) if kind == 'array_u' else None
    values = {'pureposix': PurePosixPath(root), 'bytearray': bytearray(encoded),
              'memoryview': memoryview(encoded), 'array_b': array('b', encoded),
              'array_B': array('B', encoded), 'array_u': unicode_u,
              'default_factory': defaultdict(factory), 'generator': generator()}
    probe = ModuleType('positive_control')
    if 'w' in typecodes:
        values['array_w'] = array('w', root)
    probe.setting = values[kind]
    found = _settings_paths_in_roots([probe], (tmp_path,))
    assert found and all(root in hit for hit in found), (kind, found)
    assert not _settings_paths_in_roots([probe], (tmp_path / 'unrelated',))
    assert calls == [], 'inspection evaluated stored code'
    values['generator'].close()


def test_detector_does_not_call_custom_fspath(tmp_path):
    calls = []
    class CustomPath(os.PathLike):
        def __fspath__(self):
            calls.append('fspath')
            return str(tmp_path / 'computed-only')
    probe = ModuleType('positive_control')
    probe.setting = CustomPath()
    assert _settings_paths_in_roots([probe], (tmp_path,)) == []
    assert calls == []


def test_detector_function_annotate_collision(tmp_path):
    calls = []
    inspecting = False
    class Key:
        def __hash__(self):
            if inspecting:
                calls.append('hash')
            return hash('__annotate__')
        def __eq__(self, other):
            if inspecting:
                calls.append('eq')
                raise RuntimeError('user eq')
            return False
    def target():
        pass
    planted = str(tmp_path / 'planted')
    vars(target)[Key()] = planted
    probe = ModuleType('positive_control')
    probe.setting = target
    inspecting = True
    found = _settings_paths_in_roots([probe], (tmp_path,))
    assert calls == []
    assert any(planted in hit for hit in found), found


def test_detector_generator_unbound_local_collision(tmp_path):
    calls = []
    inspecting = False
    class Key:
        def __hash__(self):
            if inspecting:
                calls.append('hash')
            return hash('later')
        def __eq__(self, other):
            if inspecting:
                calls.append('eq')
                raise RuntimeError('user eq')
            return False
    planted = str(tmp_path / 'planted')
    def generator(box):
        # Only the frame-locals store keeps the path: no closure cell holds
        # it (the one cell holds Key), and no default, name alias or fast
        # local does (the argument list is emptied first).
        sys._getframe().f_locals[Key()] = box.pop()
        yield 1
        later = 2
    value = generator([planted])
    next(value)
    assert generator.__defaults__ is None and generator.__closure__ is not None
    assert all(type(cell.cell_contents) is not str for cell in generator.__closure__)
    probe = ModuleType('positive_control')
    probe.setting = value
    inspecting = True
    try:
        found = _settings_paths_in_roots([probe], (tmp_path,))
        assert calls == []
        assert [hit for hit in found if hit.endswith(f' = {planted}')], found
        assert all(hit.startswith('positive_control.setting.') for hit in found), found
        assert _settings_paths_in_roots([probe], (tmp_path / 'unrelated',)) == []
        assert calls == []
    finally:
        inspecting = False
        value.close()


def test_detector_generator_frame_extra_local(tmp_path):
    """A value stored only as a frame extra local is found on 3.11 through 3.14.

    3.11/3.12 keep it in the generator's locals dict; 3.14 keeps it in the
    frame object's extra-locals dict, which the generator does not reference.
    A passing 3.13 control establishes detection, not the storage owner.
    """
    calls = []
    inspecting = False
    class Key:
        def __hash__(self):
            if inspecting:
                calls.append('hash')
            return hash('extra')
        def __eq__(self, other):
            if inspecting:
                calls.append('eq')
                raise RuntimeError('user eq')
            return False
    planted = str(tmp_path / 'planted')
    def generator(box):
        frame_locals = sys._getframe().f_locals
        frame_locals['extra'] = box.pop()
        frame_locals[Key()] = None
        del frame_locals
        yield 1
    value = generator([planted])
    next(value)
    probe = ModuleType('positive_control')
    probe.setting = value
    probe.later = str(tmp_path / 'later')
    inspecting = True
    try:
        found = _settings_paths_in_roots([probe], (tmp_path,))
        assert calls == []
        assert [hit for hit in found if hit.startswith('positive_control.setting.')
                and hit.endswith(f' = {planted}')], found
        assert f'positive_control.later = {tmp_path / "later"}' in found
        assert not [hit for hit in found if '<uninspectable ' in hit], found
        assert calls == []
    finally:
        inspecting = False
        value.close()


def test_detector_generator_overwritten_fast_local(tmp_path):
    """Find retained overwritten locals; assert no retained value on 3.13.

    3.11/3.12 keep the original in the fast local itself (the f_locals write
    reaches only the locals dict). On 3.13 PEP 667 writes through to the fast
    local and nothing retains the old value. On 3.14 the proxy write replaces
    the fast local and the old value survives in a frame-owned tuple.
    """
    planted = str(tmp_path / 'planted')
    def generator(box):
        v = box.pop()
        sys._getframe().f_locals['v'] = 'replaced'
        yield 1
    value = generator([planted])
    next(value)
    probe = ModuleType('positive_control')
    probe.setting = value
    try:
        found = _settings_paths_in_roots([probe], (tmp_path,))
        hits = [hit for hit in found if hit.endswith(f' = {planted}')]
        if sys.version_info[:2] == (3, 13):
            # PEP 667 f_locals write-through replaces the fast local; no old
            # value remains stored on 3.13 for the referents-only walk to find.
            assert hits == []
            assert not [hit for hit in found if '<uninspectable ' in hit], found
        else:
            assert hits, found
        if sys.version_info[:2] == (3, 14):
            assert all(hit.startswith('positive_control.setting.gi_frame_referent[')
                       for hit in hits), hits
        assert _settings_paths_in_roots([probe], (tmp_path / 'unrelated',)) == []
    finally:
        value.close()


@pytest.mark.parametrize('state', ['created', 'suspended', 'name-alias'])
def test_detector_generator_stored_local_without_default_or_closure(tmp_path, state):
    def generator(seed):
        path = seed
        del seed
        yield None
        raise AssertionError('inspection advanced the generator')
    planted = str(tmp_path / 'planted')
    value = generator(planted)
    if state == 'suspended':
        next(value)
    elif state == 'name-alias':
        value.__name__ = value.__qualname__ = planted
    assert generator.__defaults__ is None and generator.__closure__ is None
    probe = ModuleType('positive_control')
    probe.setting = value
    try:
        found = _settings_paths_in_roots([probe], (tmp_path,))
        assert any(planted in hit for hit in found), found
        assert _settings_paths_in_roots([probe], (tmp_path / 'unrelated',)) == []
    finally:
        value.close()


@pytest.mark.parametrize('exact_owner', [True, False])
def test_detector_stored_string_class_ownership(tmp_path, exact_owner):
    calls = []
    class Owner(str):
        def __eq__(self, other):
            calls.append('eq')
            raise RuntimeError('user eq')
    probe = ModuleType('positive_control')
    planted = str(tmp_path / 'planted')
    owner = probe.__name__ if exact_owner else Owner(probe.__name__)
    probe.setting = type('Text', (str,), {'__module__': owner, 'path': planted})
    found = _settings_paths_in_roots([probe], (tmp_path,))
    assert calls == []
    if exact_owner:
        assert f'positive_control.setting.path = {planted}' in found
    else:
        assert found == ['positive_control.setting.__module__ = <uninspectable non-str class module>']


def test_detector_annotation_limit_does_not_evaluate_callback(tmp_path):
    """Lock the documented 3.14 annotation refusal.

    On 3.14 this asserts the named limit (no finding while __annotate__ is
    set) rather than a regression against the previous detector version.
    """
    calls = []
    planted = str(tmp_path / 'planted')
    def target():
        pass
    def annotate(format):
        calls.append('annotate')
        return {'path': planted}
    probe = ModuleType('positive_control')
    probe.setting = target
    if '__annotate__' in FunctionType.__dict__:
        target.__annotate__ = annotate
        assert _settings_paths_in_roots([probe], (tmp_path,)) == []
        assert calls == []
        assert target.__annotations__ == {'path': planted}  # Explicit fixture realization.
        assert calls == ['annotate']
        calls.clear()
        assert _settings_paths_in_roots([probe], (tmp_path,)) == []
    else:
        target.__annotations__ = {'path': planted}
        assert any(planted in hit for hit in _settings_paths_in_roots([probe], (tmp_path,)))
    assert calls == []


def _pathlib_slot():
    return '_parts' if sys.version_info < (3, 12) else '_raw_paths'


@pytest.mark.parametrize('construction', ['stored', 'public'])
@pytest.mark.parametrize('kind', ['bool', 'len'])
def test_detector_pathlib_component_without_callbacks(tmp_path, kind, construction):
    calls = []
    def forbidden(self):
        calls.append(kind)
        raise RuntimeError('user ' + kind)
    component = type('Component', (str,), {'__' + kind + '__': forbidden})
    planted = str(tmp_path / 'planted')
    path_type = Path if kind == 'bool' else PurePosixPath
    if construction == 'stored':
        setting = path_type('/')
        object.__setattr__(setting, _pathlib_slot(), [component(planted)])
    else:
        setting = path_type(component(planted))
    probe = ModuleType('positive_control')
    probe.setting = setting
    probe.later = str(tmp_path / 'later')
    calls.clear()
    found = _settings_paths_in_roots([probe], (tmp_path,))
    assert calls == []
    if construction == 'public' and sys.version_info < (3, 12):
        # 3.11 parses constructor arguments into exact-str _parts.
        assert f'positive_control.setting = {planted}' in found, found
    else:
        assert 'positive_control.setting = <uninspectable pathlib components>' in found, found
    assert f'positive_control.later = {tmp_path / "later"}' in found


@pytest.mark.parametrize('kind', ['Path', 'PurePath', 'PurePosixPath', 'host-concrete',
                                  'stored-Path', 'stored-PurePath'])
def test_detector_pathlib_exact_components_are_decoded(tmp_path, kind):
    planted = str(tmp_path / 'planted')
    host_concrete = WindowsPath if os.name == 'nt' else PosixPath
    if kind.startswith('stored-'):
        # Exact base classes are decoded through the host's pure flavour.
        setting = object.__new__({'stored-Path': Path, 'stored-PurePath': PurePath}[kind])
        object.__setattr__(setting, _pathlib_slot(), [planted])
    else:
        setting = {'Path': Path, 'PurePath': PurePath, 'PurePosixPath': PurePosixPath,
                   'host-concrete': host_concrete}[kind](planted)
    probe = ModuleType('positive_control')
    probe.setting = setting
    found = _settings_paths_in_roots([probe], (tmp_path,))
    assert found == [f'positive_control.setting = {planted}'], found
    assert _settings_paths_in_roots([probe], (tmp_path / 'unrelated',)) == []


@pytest.mark.skipif(os.name == 'nt', reason='POSIX roots are needed for a non-absolute Windows path')
def test_detector_pure_windows_path_is_not_resolved_on_posix(tmp_path):
    # Documented limit: the decoded Windows path is not absolute on POSIX.
    probe = ModuleType('positive_control')
    probe.setting = PureWindowsPath(str(tmp_path / 'planted'))
    probe.later = str(tmp_path / 'later')
    assert _settings_paths_in_roots([probe], (tmp_path,)) == [
        f'positive_control.later = {tmp_path / "later"}']


def test_detector_foreign_concrete_path_does_not_abort_scan(tmp_path):
    # WindowsPath on POSIX (PosixPath on Windows) refuses construction here.
    foreign = PosixPath if os.name == 'nt' else WindowsPath
    planted = str(tmp_path / 'planted')
    setting = object.__new__(foreign)
    object.__setattr__(setting, _pathlib_slot(), [planted])
    probe = ModuleType('positive_control')
    probe.setting = setting
    probe.later = str(tmp_path / 'later')
    found = _settings_paths_in_roots([probe], (tmp_path,))
    assert found == ['positive_control.later = ' + str(tmp_path / 'later'),
                     'positive_control.setting = <uninspectable foreign concrete path>'], found


def test_detector_released_memoryview(tmp_path):
    value = memoryview(b'path')
    value.release()
    probe = ModuleType('positive_control')
    probe.setting = value
    probe.later = str(tmp_path / 'later')
    found = _settings_paths_in_roots([probe], (tmp_path,))
    assert any(hit.startswith('positive_control.setting = <uninspectable ')
               for hit in found), found
    assert f'positive_control.later = {tmp_path / "later"}' in found


def test_detector_embedded_nul(tmp_path):
    probe = ModuleType('positive_control')
    probe.setting = '/a\x00b'
    probe.later = str(tmp_path / 'later')
    found = _settings_paths_in_roots([probe], (tmp_path,))
    assert any(hit.startswith('positive_control.setting = <uninspectable ')
               for hit in found), found
    assert f'positive_control.later = {tmp_path / "later"}' in found


def test_detector_defaultdict_subclass_factory(tmp_path):
    calls = []
    class Stored(defaultdict):
        def __getattribute__(self, name):
            calls.append('getattribute')
            raise RuntimeError('user lookup')
    planted = str(tmp_path / 'planted')
    probe = ModuleType('positive_control')
    probe.setting = Stored(partial(str, planted))
    found = _settings_paths_in_roots([probe], (tmp_path,))
    assert calls == []
    assert f'positive_control.setting.default_factory.args.0 = {planted}' in found


@pytest.mark.parametrize('base', [str, bytes], ids=['str-subclass', 'bytes-subclass'])
def test_detector_finds_string_subclasses_without_user_code(tmp_path, base):
    calls = []
    def forbidden(self, *args, **kwargs):
        calls.append('subclass code')
        raise AssertionError('subclass code executed')
    hostile = type('StoredPath', (base,), dict.fromkeys(
        ('__str__', '__fspath__', '__eq__', '__hash__', '__iter__', '__getattribute__'), forbidden))
    root = str(tmp_path / 'planted')
    probe = ModuleType('positive_control')
    probe.setting = hostile(root if base is str else os.fsencode(root))
    found = _settings_paths_in_roots([probe], (tmp_path,))
    assert found == [f'positive_control.setting = {root}']
    assert not _settings_paths_in_roots([probe], (tmp_path / 'unrelated',))
    assert calls == [], 'inspection ran a subclass override'


@pytest.mark.parametrize('base', [str, bytes])
def test_string_subclass_storage_without_callbacks(tmp_path, base):
    calls = []
    def forbidden(self, *args, **kwargs):
        calls.append('called')
        raise AssertionError('subclass code executed')
    names = ('__str__', '__repr__', '__fspath__', '__eq__', '__ne__', '__hash__',
             '__iter__', '__len__', '__getitem__', '__contains__', '__format__',
             '__bool__', '__lt__', '__add__', '__mod__', '__reduce__', '__reduce_ex__',
             'encode', 'decode', '__buffer__', '__getattribute__', '__getattr__')
    namespace = dict.fromkeys(names, forbidden)
    namespace['__class__'] = property(forbidden)
    hostile = type('StoredPath', (base,), namespace)
    value = base.__new__(hostile, 'other' if base is str else b'other')
    root = str(tmp_path / 'planted')
    object.__setattr__(value, 'path', root)
    object.__setattr__(value, 'cycle', value)
    probe = ModuleType('positive_control')
    probe.setting = value
    assert _settings_paths_in_roots([probe], (tmp_path,)) == [f'positive_control.setting.path = {root}']
    assert _settings_paths_in_roots([probe], (tmp_path / 'unrelated',)) == []
    assert calls == []


@pytest.mark.parametrize('kind', ['dict-subclass', 'metaclass'])
def test_stored_attribute_inspection_bypasses_user_lookup(tmp_path, kind):
    calls = []
    class Storage(dict):
        def items(self):
            calls.append('dict.items')
            return super().items()
    class Meta(type):
        def __getattribute__(cls, name):
            if name in ('__mro__', '__dict__'):
                calls.append(name)
            return super().__getattribute__(name)
    class Text(str, metaclass=Meta):
        pass
    value = Text('unrelated')
    value.path = str(tmp_path / 'planted')
    if kind == 'dict-subclass':
        value.__dict__ = Storage(path=value.path)
    probe = ModuleType('positive_control')
    probe.setting = value
    found = _settings_paths_in_roots([probe], (tmp_path,))
    assert calls == []
    if kind == 'dict-subclass':
        assert found == ['positive_control.setting.__dict__ = <uninspectable non-exact dict>']
    else:
        assert found == [f'positive_control.setting.path = {tmp_path / "planted"}']


@pytest.mark.parametrize('base', [str, bytes])
@pytest.mark.parametrize('kind', ['plain', 'foreign-getset', 'foreign-slot',
                                  'shadow-int', 'shadow-property', 'own-property'])
def test_stored_attributes_survive_unusual_descriptors(tmp_path, base, kind):
    calls = []
    def forbidden(*args):
        calls.append('property')
        raise AssertionError('user descriptor ran')
    class Other:
        pass
    class Slots:
        __slots__ = ('path',)
    namespace = {}
    if kind == 'foreign-getset':
        namespace['__dict__'] = Other.__dict__['__dict__']
    elif kind == 'foreign-slot':
        namespace['foreign'] = Slots.__dict__['path']
    elif kind == 'own-property':
        namespace['__dict__'] = property(forbidden)
    Text = type('Text', (base,), namespace)
    if kind in ('shadow-int', 'shadow-property'):
        Text = type('Shadow', (Text,),
                    {'__dict__': 5 if kind == 'shadow-int' else property(forbidden)})
    value = base.__new__(Text, 'other' if base is str else b'other')
    planted = str(tmp_path / 'planted')
    object.__setattr__(value, 'path', planted)
    probe = ModuleType('positive_control')
    probe.setting = value
    found = _settings_paths_in_roots([probe], (tmp_path,))
    assert calls == []
    assert any(planted in hit or '<uninspectable ' in hit for hit in found), found
    if kind in ('plain', 'shadow-int', 'shadow-property'):
        assert found == [f'positive_control.setting.path = {planted}']
        assert _settings_paths_in_roots([probe], (tmp_path / 'unrelated',)) == []
    if kind in ('foreign-getset', 'foreign-slot', 'own-property'):
        assert any('<uninspectable ' in hit for hit in found), found
    assert calls == []


@pytest.mark.parametrize('kind', ['classdict-collide-raise', 'classdict-collide-quiet',
                                  'instdict-nonstr-key', 'slot-nonstr-name'])
def test_stored_attribute_keys_never_call_user_code(tmp_path, kind):
    calls = []
    inspecting = False
    class Key:
        def __hash__(self):
            calls.append('__hash__')
            return hash('__dict__') if kind.startswith('classdict-collide') else 7
        def __eq__(self, other):
            calls.append('__eq__')
            if inspecting and kind == 'classdict-collide-raise':
                raise RuntimeError('user eq')
            return False
        def __format__(self, spec):
            calls.append('__format__')
            return 'key'
        def __str__(self):
            calls.append('__str__')
            return 'key'
        def __repr__(self):
            calls.append('__repr__')
            return 'key'
    class Slots:
        __slots__ = ('path',)
    key = Key()
    namespace = {}
    if kind.startswith('classdict-collide'):
        namespace[key] = 1
    elif kind == 'slot-nonstr-name':
        namespace[key] = Slots.__dict__['path']
    with warnings.catch_warnings():
        warnings.filterwarnings('ignore', category=RuntimeWarning,
                                message='non-string key in the __dict__ of class')
        Text = type('Text', (str,), namespace)
    value = Text('unrelated')
    planted = str(tmp_path / 'planted')
    if kind == 'instdict-nonstr-key':
        vars(value)[key] = planted
    else:
        value.path = planted
    probe = ModuleType('positive_control')
    probe.setting = value
    calls.clear()  # Construction can hash keys; inspection must not.
    inspecting = True
    found = _settings_paths_in_roots([probe], (tmp_path,))
    unrelated = _settings_paths_in_roots([probe], (tmp_path / 'unrelated',))
    assert calls == [], 'inspection invoked a stored key method'
    assert any(planted in hit for hit in found), found
    assert any('<non-str key #' in hit and '<uninspectable non-str key>' in hit
               for hit in found), found
    assert unrelated and all('<uninspectable ' in hit for hit in unrelated), unrelated
    if kind == 'slot-nonstr-name':
        assert any('<uninspectable refusing descriptor>' in hit for hit in found), found


@pytest.mark.parametrize('kind', ['function', 'class', 'module', 'module-name',
                                  'class-value', 'str-subclass-key'])
def test_detector_labels_only_exact_string_names(tmp_path, kind):
    calls = []
    def forbidden(self, *args, **kwargs):
        calls.append('user code')
        raise AssertionError('stored name executed user code')
    class Key:
        __format__ = __str__ = __repr__ = forbidden
        def __hash__(self):
            return 7
        def __eq__(self, other):
            return self is other
    key = Key()
    probe = ModuleType('positive_control')
    planted = str(tmp_path / 'planted')
    if kind == 'function':
        def value():
            pass
        vars(value)[key] = planted
    elif kind == 'class':
        with warnings.catch_warnings():
            warnings.filterwarnings('ignore', category=RuntimeWarning,
                                    message='non-string key in the __dict__ of class')
            value = type('Settings', (), {'__module__': probe.__name__, key: planted})
    elif kind == 'module':
        vars(probe)[key] = planted
        value = None
    elif kind == 'module-name':
        probe.__name__ = key
        value = planted
    elif kind == 'class-value':
        with warnings.catch_warnings():
            warnings.filterwarnings('ignore', category=RuntimeWarning,
                                    message='non-string key in the __dict__ of class')
            value = type('Text', (str,), {key: planted})('unrelated')
    else:
        Key = type('Key', (str,), {'__format__': forbidden, '__str__': forbidden,
                                 '__repr__': forbidden})
        value = type('Settings', (), {})()
        vars(value)[Key('path')] = planted
    probe.setting = value
    found = _settings_paths_in_roots([probe], (tmp_path,))
    unrelated = _settings_paths_in_roots([probe], (tmp_path / 'unrelated',))
    assert any(planted in hit for hit in found), found
    assert any('<uninspectable non-str ' in hit for hit in found), found
    assert unrelated and all('<uninspectable ' in hit for hit in unrelated), unrelated
    assert calls == []


@pytest.mark.parametrize('kind', ['dict', 'list', 'partial', 'property',
                                  'staticmethod', 'classmethod', 'mappingproxy', 'user-mapping'])
def test_detector_reads_builtin_storage_without_subclass_methods(tmp_path, kind):
    calls = []
    def forbidden(self, *args, **kwargs):
        calls.append('user code')
        raise AssertionError('subclass code executed')
    planted = str(tmp_path / 'planted')
    def target(path=planted):
        pass
    if kind == 'user-mapping':
        class Mapping:
            __getitem__ = __iter__ = __len__ = items = forbidden
        probe = ModuleType('positive_control')
        probe.setting = MappingProxyType(Mapping())
        assert _settings_paths_in_roots([probe], (tmp_path,)) == [
            'positive_control.setting = <uninspectable mapping storage>']
        assert calls == []
        return
    base = {'dict': dict, 'list': list, 'partial': partial, 'property': property,
            'staticmethod': staticmethod, 'classmethod': classmethod,
            'mappingproxy': dict}[kind]
    hostile = type('Stored', (base,), dict.fromkeys(
        ('__getattribute__', '__iter__', 'items', 'values'), forbidden))
    if kind in ('dict', 'mappingproxy'):
        value = hostile(path=planted)
        if kind == 'mappingproxy':
            value = MappingProxyType(value)
    elif kind == 'list':
        value = hostile([planted])
    else:
        value = hostile(target)
    probe = ModuleType('positive_control')
    probe.setting = value
    calls.clear()
    found = _settings_paths_in_roots([probe], (tmp_path,))
    assert any(planted in hit for hit in found), found
    assert not _settings_paths_in_roots([probe], (tmp_path / 'unrelated',))
    assert calls == []


# These are literal contract inputs, independent of the plugin's dictionaries.
_STARTUP_XDG_KEYS = (
    'XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME',
    'XDG_STATE_HOME', 'XDG_RUNTIME_DIR', 'XDG_CONFIG_DIRS', 'XDG_DATA_DIRS',
)
_STARTUP_REDIRECTS = {
    'HOME': 'home', 'XDG_CONFIG_HOME': 'config', 'XDG_CACHE_HOME': 'cache',
    'XDG_DATA_HOME': 'data', 'XDG_STATE_HOME': 'state',
    'XDG_RUNTIME_DIR': 'runtime', 'XDG_CONFIG_DIRS': 'config-dirs',
    'XDG_DATA_DIRS': 'data-dirs',
}
_STARTUP_SHELL_KEYS = ('BASH_ENV', 'ENV', 'ZDOTDIR', 'HISTFILE', 'INPUTRC')
_STARTUP_FLEET_KEYS = (
    'FLEET_CODEX_LINK_CONFIG', 'FLEET_LINK_IP', 'FLEET_PC_IP',
    'FLEET_RESEARCH_PLAYLISTS', 'FLEET_RESEARCH_REQUEST_DIR',
    'FLEET_RESEARCH_STATE', 'FLEET_TUI_CLOUD_MARKERS',
    'FLEET_TUI_COMMS_INBOUND_GLOB', 'FLEET_TUI_CURATION_DIR',
    'FLEET_TUI_DISK_PATH', 'FLEET_TUI_EXTERNAL_SSD_TEMP',
    'FLEET_TUI_GPU_FORENSICS_LOG', 'FLEET_TUI_HERMES_CRON_DIR',
    'FLEET_TUI_HERMES_STATE_DB', 'FLEET_TUI_HIVE_ALERT',
    'FLEET_TUI_PASSBACK_DOCS_GLOB', 'FLEET_TUI_RELIABILITY_FILE',
    'FLEET_TUI_RESEARCH_DIR', 'FLEET_TUI_SCREENSHOTS_DIR',
    'FLEET_TUI_SERVE_HOST', 'FLEET_TUI_SERVE_PORT', 'FLEET_TUI_SERVICES',
    'FLEET_TUI_STALE_SECS',
)
# Named finite value alphabet, uniformly applied to every rich input role.
_STARTUP_VALUE_EMPTY = ''
_STARTUP_VALUE_WHITESPACE = ' \t '
_STARTUP_VALUE_DECIMAL = '10'
_STARTUP_VALUE_DIGIT = '0'
_STARTUP_VALUE_UNICODE = 'é\u00a0ΩЖ漢'
_STARTUP_VALUE_NON_NFC = 'e\u0301'
_STARTUP_VALUE_FLEET_PREFIX = 'FLEET_value'
_STARTUP_VALUE_BOUND = 8192  # Unicode code points, including padding.
_STARTUP_VALUE_EXTREME = (' \t' + (_STARTUP_VALUE_UNICODE * _STARTUP_VALUE_BOUND)
                          [:_STARTUP_VALUE_BOUND - 4] + '\t ')
_STARTUP_VALUE_ALPHABET = {
    'environment-empty': _STARTUP_VALUE_EMPTY,
    'environment-whitespace': _STARTUP_VALUE_WHITESPACE,
    'environment-decimal': _STARTUP_VALUE_DECIMAL,
    'environment-digit': _STARTUP_VALUE_DIGIT,
    'environment-unicode': _STARTUP_VALUE_UNICODE,
    'environment-non-nfc': _STARTUP_VALUE_NON_NFC,
    'environment-fleet-value': _STARTUP_VALUE_FLEET_PREFIX,
    'environment-extreme': _STARTUP_VALUE_EXTREME,
}
# Pair every uniform class with a neutral-only environment. The latter keeps
# realistic absolute HOME/redirect/reset inputs and the rich FLEET_ corpus;
# it retains the earlier neutral-only relational witnesses as a strict subset.
_STARTUP_NEUTRAL_ALPHABET = {
    name.replace('environment-', 'environment-neutral-', 1): value
    for name, value in _STARTUP_VALUE_ALPHABET.items()
}
_STARTUP_TARGET_CASE = 'environment-target'
_STARTUP_CASES = (
    'rich', 'xdg-unset', 'xdg-empty', 'xdg-relative', 'xdg-tilde',
    'xdg-nonpath', 'xdg-lower', 'xdg-spelling', 'home-unset', 'home-empty',
    'home-relative', 'home-nonpath', 'home-missing', 'home-dangling',
    'home-space', 'home-scheme', 'reset-unset', 'reset-empty',
    'environment-minimal', *_STARTUP_VALUE_ALPHABET,
    *_STARTUP_NEUTRAL_ALPHABET, _STARTUP_TARGET_CASE,
)


# Each row preserves all non-anchor names' exact alphabet values. Two anchor
# cohorts alternate the XDG keys; HOME's own feasible spellings have separate
# rows. No original uniform/neutral/preload witness is replaced.
_STARTUP_HOME_STRUCTURES = (
    'absolute-directory', 'absolute-missing', 'absolute-symlink', 'absolute-dangling',
    'relative-directory', 'relative-missing', 'relative-symlink', 'relative-dangling',
    'empty', 'unset',
)
_STARTUP_STRUCTURAL_CASES = {
    'environment-struct-' + alphabet.removeprefix('environment-') + '-' + home + '-' + str(cohort):
        (alphabet, home, cohort)
    for alphabet in _STARTUP_VALUE_ALPHABET
    for home in _STARTUP_HOME_STRUCTURES for cohort in range(2)
}
_STARTUP_HOME_SELF_CASES = {
    'environment-home-self-' + alphabet.removeprefix('environment-') + '-' + home:
        (alphabet, home, None)
    for alphabet in _STARTUP_VALUE_ALPHABET
    for home in ('directory', 'missing', 'symlink', 'dangling')
    if (_STARTUP_VALUE_ALPHABET[alphabet] and
        (home == 'missing' or len(_STARTUP_VALUE_ALPHABET[alphabet].encode()) <= 255))
}
_STARTUP_STRUCTURAL_CASES.update(_STARTUP_HOME_SELF_CASES)
_STARTUP_XDG_SELF_CASES = {
    'environment-xdg-self-' + alphabet.removeprefix('environment-') + '-' + key:
        (alphabet, 'xdg-self', key)
    for alphabet in _STARTUP_VALUE_ALPHABET for key in _STARTUP_XDG_KEYS
}
_STARTUP_STRUCTURAL_CASES.update(_STARTUP_XDG_SELF_CASES)
# Covering rows, not an unrelated-axes Cartesian product: source-type rows
# select or reject the active branch; filesystem/resolution/candidate rows use
# accepted sources only. Each row retains every environment name's own alphabet.
_STARTUP_LOCATION_SOURCE_KINDS = (
    'none', 'empty', 'empty-subclass', 'bytes-empty', 'zero', 'absolute',
    'relative', 'subclass', 'nonstring',
)
_STARTUP_LOCATION_PATH_KINDS = (
    'plain', 'missing', 'directory-link-other-parent', 'directory-link-same-parent',
    'parent-link', 'dangling', 'nul', 'oserror', 'loop', 'root-collapse', 'alias-collapse',
)
_STARTUP_LOCATION_SPECS = {
    **{'stdlib-' + kind: ('stdlib', 'source', kind)
       for kind in _STARTUP_LOCATION_SOURCE_KINDS},
    **{'fallback-' + selector + '-' + kind: ('fallback-' + selector, 'source', kind)
       for selector in ('none', 'empty') for kind in _STARTUP_LOCATION_SOURCE_KINDS},
    **{branch + '-path-' + kind: (branch, 'path', kind)
       for branch in ('stdlib', 'fallback-none', 'fallback-empty')
       for kind in _STARTUP_LOCATION_PATH_KINDS
       if kind != 'loop' or tuple(sys.version_info[:2]) < (3, 13)},
}
_STARTUP_LOCATION_CASES = {
    'environment-location-' + alphabet.removeprefix('environment-') + '-' + kind: (alphabet, kind)
    for alphabet in _STARTUP_VALUE_ALPHABET for kind in _STARTUP_LOCATION_SPECS
}
# HOME-vs-XDG dedup requires an absolute existing HOME while each name retains
# its own alphabet. Alternating key cohorts and self-key rows preserve witnesses.
_STARTUP_HOME_XDG_CASES = {
    'environment-home-xdg-' + alphabet.removeprefix('environment-') + '-' + variant + '-' + str(cohort):
        (alphabet, variant, cohort)
    for alphabet in _STARTUP_VALUE_ALPHABET
    for variant in ('identical', 'alias', 'distinct') for cohort in range(2)
}
_STARTUP_STRUCTURAL_CASES.update({case: (alphabet, 'xdg-self', '__home_dedup__')
                                 for case, (alphabet, _, _) in _STARTUP_HOME_XDG_CASES.items()})


_STARTUP_ALPHABET_REFUSALS = {
    'environment-refusal-' + alphabet.removeprefix('environment-'): alphabet
    for alphabet in _STARTUP_VALUE_ALPHABET
}


# Complete ASCII environment-value alphabet (NUL cannot be passed to execve),
# plus every letter/symbol in the declared Unicode ranges and all Unicode
# whitespace. These are enumerations, not selected spellings. Path separators
# and os.pathsep have structural controls below; env names additionally exclude
# '='. The Unicode ranges span Latin-1, Greek, Cyrillic, Currency and Dingbats.
_STARTUP_UNICODE_RANGES = (
    (0x00A0, 0x0100), (0x0370, 0x0400), (0x0400, 0x0440),
    (0x20A0, 0x20D0), (0x2700, 0x2740),
)


@lru_cache(maxsize=None)
def _environment_characters():
    unicode_chars = (
        chr(code) for start, stop in _STARTUP_UNICODE_RANGES
        for code in range(start, stop)
        if unicodedata.category(chr(code))[0] in 'LS'
    )
    return tuple(dict.fromkeys((
        *(chr(code) for code in range(1, 128)),
        *unicode_chars, *_unicode_classes()['whitespace'],
    )))


def _root_content_fragments():
    # Each single character has an interior component witness. ':' is a
    # delimiter on this host and '/' a separator, so they are exercised as
    # structural spelling controls rather than falsely asserted intact roots.
    chars = [char for char in _environment_characters() if char not in '/:']
    lengths = sorted({*(2 ** power for power in range(14)), 255, 4095, 8191})
    return tuple(['l' + char + 'r' for char in chars] + [
        ('abcdefg/' * (length // 8 + 1))[:length].rstrip('/') + 'z'
        for length in lengths
    ])


def _fleet_suffixes():
    # Every transportable ASCII character independently, declared Unicode
    # letters/symbols/whitespace, case mixtures, and powers-of-two lengths.
    chars = [char for char in _environment_characters() if char != '=']
    return tuple(dict.fromkeys((
        '', *chars, *('l' + char + 'R' for char in chars),
        *(upper + lower for upper, lower in zip(string.ascii_uppercase,
                                                string.ascii_lowercase)),
        *(letter + digit for letter in string.ascii_letters for digit in string.digits),
        *('x' * (2 ** power) for power in range(14)),
    )))


def _prefix_neighbours(prefix):
    # Enumerate all case variants of the literal boundary and insertion/
    # deletion neighbours at every position. Exact members are excluded.
    cases = (''.join(chars) for chars in product(*(
        (char.lower(), char.upper()) if char.isalpha() else (char,)
        for char in prefix)))
    edits = [prefix[:index] + '-' + prefix[index:] for index in range(len(prefix) + 1)]
    edits += [prefix[:index] + prefix[index + 1:] for index in range(len(prefix))]
    return tuple(sorted({*cases, *edits} - {prefix}))


def _startup_module_names():
    """Discover regular Python packages/leaves without importing application code.

    New files enter the preload matrix automatically. Unsupported layouts fail
    collection explicitly rather than silently losing namespace coverage.
    """
    package = Path(__file__).resolve().parents[1] / 'fleet_tui'
    assert (package / '__init__.py').is_file(), package
    names = set()
    for path in sorted(package.rglob('*')):
        assert path.suffix not in ('.so', '.pyd'), ('extend module discovery', path)
        if path.suffix != '.py':
            continue
        parts = path.relative_to(package).with_suffix('').parts
        assert all(part.isidentifier() for part in parts), path
        for parent in path.relative_to(package).parents:
            assert (package / parent / '__init__.py').is_file(), path
        if parts[-1] == '__init__':
            parts = parts[:-1]
        names.add('.'.join(('fleet_tui', *parts)))
    assert {'fleet_tui', 'fleet_tui.models', 'fleet_tui.paths',
            'fleet_tui.sources.boxes'} <= names, names
    return tuple(sorted(names - {'fleet_tui'}))


_STARTUP_MODULE_NAMES = _startup_module_names()
_STARTUP_FUTURE_NAMES = (
    'fleet_tui.future_namespace', 'fleet_tui.future_namespace.deep',
    'fleet_tui.Mixed', 'fleet_tui.123', 'fleet_tui.with space', 'fleet_tui.file:///abs',
)
_STARTUP_NEAR_NAMES = (
    'fleet_tuix', 'fleet_tui_extra', 'FLEET_TUI', 'Fleet_Tui.models',
    'fleet_tui .models', 'file:///fleet_tui.models',
)
# Open namespace: a whole generated group in one suffix, at both depths.
# The discovered real names remain individually isolated in separate children.
_STARTUP_FUTURE_NAMES += tuple(
    'fleet_tui.' + ('future.' * depth) + chars
    for depth in range(2)
    for chars in (string.ascii_lowercase, string.ascii_uppercase,
                  string.digits, string.punctuation,
                  ''.join(chr(code) for code in range(1, 33)),
                  ''.join(char for char in _environment_characters() if ord(char) > 127))
)
assert not set(_STARTUP_FUTURE_NAMES) & set(_STARTUP_MODULE_NAMES)


@lru_cache(maxsize=256, typed=True)
def _startup_exact_string_digest(value):
    # Cache immutable values only; each snapshot receives a fresh dictionary.
    return len(value), hashlib.sha256(value.encode('utf-8', 'surrogateescape')).hexdigest()


def _startup_value_digest(value):
    assert type(value) is str, 'environment digest value is not exact str'
    length, digest = _startup_exact_string_digest(value)
    return {'length': length, 'sha256': digest}


def _startup_environment_digests(environment):
    result = {}
    for key, value in environment.items():
        assert type(key) is str, 'environment name is not exact str'
        result[key] = _startup_value_digest(value)
    return result


# Every isolation global has a declared comparator. This literal also detects
# unconditional additions/removals, which a parent-only name comparison cannot.
_STARTUP_NAMESPACE_NAMES = {
    '__name__',
    '__doc__',
    '__package__',
    '__loader__',
    '__spec__',
    '__file__',
    '__cached__',
    '__builtins__',
    'os',
    'sys',
    'tempfile',
    'Path',
    '_ORIGINAL_USER_ROOTS',
    '_ORIGINAL_INTERPRETER_LOCATIONS',
    '_ORIGINAL_LICENSE_FILES',
    '_TEST_DIRECTORY',
    '_RETIREMENT_CONTROL',
    '_TEST_OWNER_PID',
    '_TEST_ROOT',
    '_IMPORT_ENV',
    '_exact_location',
    '_license_candidate_files',
    '_dispatch_fixture',
    '_process_identity',
    '_retire_test_siblings',
    '_write_test_owner',
    '_start_retirement_guard',
    '_stop_retirement_guard',
    '_retirement_pidfd_open',
    '_retirement_pidfd_signal',
    '_retirement_address',
    '_veto_retirement',
    '_request_retirement',
    '_retain_test_directory',
    '_cleanup_test_directory',
    '_test_path_identity',
    'pytest_addoption',
    '_key',
    '_subdir',
    '_directory',
}


_STARTUP_REWRITE_NAMES = {'@py_builtins', '@pytest_ar', '@py_assert1', '@py_assert3', '@py_assert5'}


_STARTUP_NAMESPACE_COMPARATORS = {
    '@py_builtins': 'identity', '@pytest_ar': 'identity',
    '@py_assert1': 'parent', '@py_assert3': 'parent', '@py_assert5': 'parent',
    '__name__': 'parent', '__doc__': 'parent', '__package__': 'parent',
    '__loader__': 'loader', '__spec__': 'spec', '__file__': 'parent',
    '__cached__': 'parent', '__builtins__': 'builtins',
    'os': 'identity', 'sys': 'identity', 'tempfile': 'identity', 'Path': 'identity',
    '_ORIGINAL_USER_ROOTS': 'roots', '_ORIGINAL_INTERPRETER_LOCATIONS': 'parent',
    '_ORIGINAL_LICENSE_FILES': 'parent', '_TEST_DIRECTORY': 'owner',
    '_RETIREMENT_CONTROL': 'control', '_TEST_OWNER_PID': 'ownerpid', '_TEST_ROOT': 'root', \
        '_IMPORT_ENV': 'environment',
    '_exact_location': 'function', '_license_candidate_files': 'function',
    '_dispatch_fixture': 'function', '_key': 'key', '_subdir': 'subdir',
    '_directory': 'directory',
    '_process_identity': 'function',
    '_retire_test_siblings': 'function',
    '_write_test_owner': 'function',
    '_start_retirement_guard': 'function',
    '_stop_retirement_guard': 'function',
    '_retirement_pidfd_open': 'function',
    '_retirement_pidfd_signal': 'function',
    '_retirement_address': 'function',
    '_veto_retirement': 'function',
    '_request_retirement': 'function',
    '_retain_test_directory': 'function',
    '_cleanup_test_directory': 'function',
    '_test_path_identity': 'function',
    'pytest_addoption': 'function',
}


# Loaded-object census controls by interpreter version and loader kind.
_STARTUP_FIELD_LITERALS = {(3, 11): {'code': {'_co_code_adaptive',
                    'co_argcount',
                    'co_cellvars',
                    'co_code',
                    'co_consts',
                    'co_exceptiontable',
                    'co_filename',
                    'co_firstlineno',
                    'co_flags',
                    'co_freevars',
                    'co_kwonlyargcount',
                    'co_linetable',
                    'co_lnotab',
                    'co_name',
                    'co_names',
                    'co_nlocals',
                    'co_posonlyargcount',
                    'co_qualname',
                    'co_stacksize',
                    'co_varnames'},
           'finalizer': {'alive', 'atexit'},
           'function': {'__annotations__',
                        '__builtins__',
                        '__closure__',
                        '__code__',
                        '__defaults__',
                        '__dict__',
                        '__doc__',
                        '__globals__',
                        '__kwdefaults__',
                        '__module__',
                        '__name__',
                        '__qualname__'},
           'info': {'kwargs', 'index', 'weakref', 'func', 'atexit', 'args'},
           'loader_plain': {'path', 'name'},
           'loader_rewrite': {'_basenames_to_check_rewrite',
                              '_marked_for_rewrite_cache',
                              '_must_rewrite',
                              '_rewritten_names',
                              '_session_paths_checked',
                              '_writing_pyc',
                              'config',
                              'fnpats',
                              'session'},
           'owner': {'_ignore_cleanup_errors', '__dict__', '_finalizer', '__weakref__', 'name'},
           'spec': {'__dict__',
                    '__weakref__',
                    '_cached',
                    '_initializing',
                    '_set_fileattr',
                    '_uninitialized_submodules',
                    'cached',
                    'has_location',
                    'loader',
                    'loader_state',
                    'name',
                    'origin',
                    'parent',
                    'submodule_search_locations'}},
 (3, 12): {'code': {'_co_code_adaptive',
                    'co_argcount',
                    'co_cellvars',
                    'co_code',
                    'co_consts',
                    'co_exceptiontable',
                    'co_filename',
                    'co_firstlineno',
                    'co_flags',
                    'co_freevars',
                    'co_kwonlyargcount',
                    'co_linetable',
                    'co_lnotab',
                    'co_name',
                    'co_names',
                    'co_nlocals',
                    'co_posonlyargcount',
                    'co_qualname',
                    'co_stacksize',
                    'co_varnames'},
           'finalizer': {'alive', 'atexit'},
           'function': {'__annotations__',
                        '__builtins__',
                        '__closure__',
                        '__code__',
                        '__defaults__',
                        '__dict__',
                        '__doc__',
                        '__globals__',
                        '__kwdefaults__',
                        '__module__',
                        '__name__',
                        '__qualname__',
                        '__type_params__'},
           'info': {'kwargs', 'index', 'weakref', 'func', 'atexit', 'args'},
           'loader_plain': {'path', 'name'},
           'loader_rewrite': {'_basenames_to_check_rewrite',
                              '_marked_for_rewrite_cache',
                              '_must_rewrite',
                              '_rewritten_names',
                              '_session_paths_checked',
                              '_writing_pyc',
                              'config',
                              'fnpats',
                              'session'},
           'owner': {'__dict__',
                     '__weakref__',
                     '_delete',
                     '_finalizer',
                     '_ignore_cleanup_errors',
                     'name'},
           'spec': {'__dict__',
                    '__weakref__',
                    '_cached',
                    '_initializing',
                    '_set_fileattr',
                    '_uninitialized_submodules',
                    'cached',
                    'has_location',
                    'loader',
                    'loader_state',
                    'name',
                    'origin',
                    'parent',
                    'submodule_search_locations'}},
 (3, 13): {'code': {'_co_code_adaptive',
                    'co_argcount',
                    'co_cellvars',
                    'co_code',
                    'co_consts',
                    'co_exceptiontable',
                    'co_filename',
                    'co_firstlineno',
                    'co_flags',
                    'co_freevars',
                    'co_kwonlyargcount',
                    'co_linetable',
                    'co_lnotab',
                    'co_name',
                    'co_names',
                    'co_nlocals',
                    'co_posonlyargcount',
                    'co_qualname',
                    'co_stacksize',
                    'co_varnames'},
           'finalizer': {'alive', 'atexit'},
           'function': {'__annotations__',
                        '__builtins__',
                        '__closure__',
                        '__code__',
                        '__defaults__',
                        '__dict__',
                        '__doc__',
                        '__globals__',
                        '__kwdefaults__',
                        '__module__',
                        '__name__',
                        '__qualname__',
                        '__type_params__'},
           'info': {'kwargs', 'index', 'func', 'atexit', 'args', 'weakref'},
           'loader_plain': {'path', 'name'},
           'loader_rewrite': {'_basenames_to_check_rewrite',
                              '_marked_for_rewrite_cache',
                              '_must_rewrite',
                              '_rewritten_names',
                              '_session_paths_checked',
                              '_writing_pyc',
                              'config',
                              'fnpats',
                              'session'},
           'owner': {'__dict__',
                     '__weakref__',
                     '_delete',
                     '_finalizer',
                     '_ignore_cleanup_errors',
                     'name'},
           'spec': {'__dict__',
                    '__weakref__',
                    '_cached',
                    '_initializing',
                    '_set_fileattr',
                    '_uninitialized_submodules',
                    'cached',
                    'has_location',
                    'loader',
                    'loader_state',
                    'name',
                    'origin',
                    'parent',
                    'submodule_search_locations'}},
 (3, 14): {'code': {'_co_code_adaptive',
                    'co_argcount',
                    'co_cellvars',
                    'co_code',
                    'co_consts',
                    'co_exceptiontable',
                    'co_filename',
                    'co_firstlineno',
                    'co_flags',
                    'co_freevars',
                    'co_kwonlyargcount',
                    'co_linetable',
                    'co_lnotab',
                    'co_name',
                    'co_names',
                    'co_nlocals',
                    'co_posonlyargcount',
                    'co_qualname',
                    'co_stacksize',
                    'co_varnames'},
           'finalizer': {'alive', 'atexit'},
           'function': {'__annotate__',
                        '__annotations__',
                        '__builtins__',
                        '__closure__',
                        '__code__',
                        '__defaults__',
                        '__dict__',
                        '__doc__',
                        '__globals__',
                        '__kwdefaults__',
                        '__module__',
                        '__name__',
                        '__qualname__',
                        '__type_params__'},
           'info': {'kwargs', 'index', 'weakref', 'func', 'atexit', 'args'},
           'loader_plain': {'path', 'name'},
           'loader_rewrite': {'_basenames_to_check_rewrite',
                              '_marked_for_rewrite_cache',
                              '_must_rewrite',
                              '_rewritten_names',
                              '_session_paths_checked',
                              '_writing_pyc',
                              'config',
                              'fnpats',
                              'session'},
           'owner': {'__dict__',
                     '__weakref__',
                     '_delete',
                     '_finalizer',
                     '_ignore_cleanup_errors',
                     'name'},
           'spec': {'__dict__',
                    '__weakref__',
                    '_cached',
                    '_initializing',
                    '_set_fileattr',
                    '_uninitialized_submodules',
                    'cached',
                    'has_location',
                    'loader',
                    'loader_state',
                    'name',
                    'origin',
                    'parent',
                    'submodule_search_locations'}}}

_STARTUP_FIELD_COMPARATORS = {'code': {'_co_code_adaptive': 'adaptive_history_from_co_code',
          'co_argcount': 'value',
          'co_cellvars': 'value',
          'co_code': 'value',
          'co_consts': 'value',
          'co_exceptiontable': 'value',
          'co_filename': 'value',
          'co_firstlineno': 'value',
          'co_flags': 'value',
          'co_freevars': 'value',
          'co_kwonlyargcount': 'value',
          'co_linetable': 'value',
          'co_lnotab': 'deprecated_line_derivation',
          'co_name': 'value',
          'co_names': 'value',
          'co_nlocals': 'value',
          'co_posonlyargcount': 'value',
          'co_qualname': 'value',
          'co_stacksize': 'value',
          'co_varnames': 'value'},
 'finalizer': {'alive': 'value', 'atexit': 'value'},
 'function': {'__annotate__': 'value',
              '__annotations__': 'value',
              '__builtins__': 'builtins_identity',
              '__closure__': 'closure_contents',
              '__code__': 'value',
              '__defaults__': 'value',
              '__dict__': 'value',
              '__doc__': 'value',
              '__globals__': 'globals_identity',
              '__kwdefaults__': 'value',
              '__module__': 'value',
              '__name__': 'value',
              '__qualname__': 'value',
              '__type_params__': 'value'},
 'info': {'args': 'root_args',
          'atexit': 'value',
          'func': 'cleanup_code',
          'index': 'exit_counter_exact_int',
          'kwargs': 'warning_form_kwargs',
          'weakref': 'weakref_owner_identity'},
 'loader_plain': {'name': 'value', 'path': 'value'},
 'loader_rewrite': {'_basenames_to_check_rewrite': 'pytest_initial_path_stems',
                    '_marked_for_rewrite_cache': 'pytest_other_module_decisions',
                    '_must_rewrite': 'rewrite_module_membership',
                    '_rewritten_names': 'rewrite_module_path',
                    '_session_paths_checked': 'pytest_session_path_history',
                    '_writing_pyc': 'value',
                    'config': 'pytest_run_config',
                    'fnpats': 'value',
                    'session': 'pytest_run_session'},
 'owner': {'__dict__': 'dict_duplicates_compared_fields',
           '__weakref__': 'owner_weakref_identity',
           '_delete': 'value',
           '_finalizer': 'finalizer',
           '_ignore_cleanup_errors': 'value',
           'name': 'value'},
 'spec': {'__dict__': 'dict_duplicates_compared_fields',
          '__weakref__': 'weakref_storage_empty',
          '_cached': 'value',
          '_initializing': 'value',
          '_set_fileattr': 'value',
          '_uninitialized_submodules': 'value',
          'cached': 'value',
          'has_location': 'value',
          'loader': 'spec_loader_identity',
          'loader_state': 'value',
          'name': 'value',
          'origin': 'value',
          'parent': 'value',
          'submodule_search_locations': 'value'}}

def _startup_field_names(value):
    import inspect
    descriptors = vars(type(value))
    storage = vars(value) if hasattr(value, '__dict__') else {}
    # Validate original names before filtering, sorting or JSON conversion.
    assert all(type(name) is str for name in descriptors), 'descriptor census name is not exact str'
    assert all(type(name) is str for name in storage), 'instance census name is not exact str'
    return sorted({name for name, field in descriptors.items()
                   if inspect.isdatadescriptor(field)} | set(storage))


def _startup_stored_field_correspondence(stored, observed, observed_type):
    exact_type = type(stored) is observed_type
    scalar = any(observed_type is kind for kind in
                 (type(None), bool, int, float, complex, str, bytes, type(Ellipsis)))
    same_value = exact_type and (stored is observed or (scalar and stored == observed))
    return {'exact_type': exact_type, 'same_value': same_value}


def _startup_cleanup_identity(cleanup, owner_class, module):
    import types
    # Bound-method equality can call an arbitrary underlying callable's __eq__.
    # Check the original function and receiver before accessing code or equality.
    return (type(cleanup) is types.FunctionType and
            cleanup is vars(module).get('_cleanup_test_directory') and
            cleanup.__globals__ is vars(module))


def _startup_object_fields(value, category, module):
    """Census exact-type descriptors and instance storage before reading values.

    Spec.cached is read first: it materializes _cached. Function annotations
    are never evaluated while an annotation thunk is present. Specialization
    and pytest per-run state have named invariants, not raw equality.
    """
    import builtins
    import sys
    import warnings
    import weakref
    import types

    names = _startup_field_names(value)
    observed_storage = {}
    rules = _STARTUP_FIELD_COMPARATORS[category]
    references = {'code': types.CodeType, 'function': types.FunctionType,
                  'owner': __import__('tempfile').TemporaryDirectory,
                  'finalizer': weakref.finalize, 'info': weakref.finalize._Info,
                  'spec': __import__('importlib.machinery', fromlist=['ModuleSpec']).ModuleSpec,
                  'loader_plain': __import__('importlib.machinery', fromlist=['SourceFileLoader']). \
                      SourceFileLoader,
                  'loader_rewrite': sys.modules['_pytest.assertion.rewrite'].AssertionRewritingHook}
    result = {'category': category, 'census': names, 'fields': {},
              'exact_type': type(value) is references[category]}
    if category == 'info':
        result['exact_info'] = type(value) is weakref.finalize._Info
    if category == 'spec':
        value.cached
    for name in names:
        rule = rules.get(name)
        if rule is None:
            result['fields'][name] = {'unsupported': True, 'field': name}
            continue
        if name == '__annotations__' and '__annotate__' in vars(type(value)) and value.__annotate__ \
            is not None:
            result['fields'][name] = {'invalid': True, 'reason': 'annotation thunk would execute'}
            continue
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', DeprecationWarning)
            field = getattr(value, name)
        observed_storage[name] = (field, type(field))
        if rule == 'value':
            observed = _startup_namespace_value(field)
        elif rule == 'builtins_identity':
            observed = {'identity': field is builtins.__dict__}
        elif rule == 'globals_identity':
            observed = {'identity': field is vars(module)}
        elif rule == 'closure_contents':
            observed = None
            if field is not None:
                observed = {'exact_tuple': type(field) is tuple, 'cells': []}
                for cell in field:
                    if type(cell) is not types.CellType:
                        observed['cells'].append({'invalid': True, 'exact_cell': False})
                        continue
                    try:
                        observed['cells'].append({'exact_cell': True,
                            'contents': _startup_namespace_value(cell.cell_contents)})
                    except ValueError:
                        observed['cells'].append({'exact_cell': True, 'empty': True})
        elif rule == 'spec_loader_identity':
            observed = {'identity': field is vars(module).get('__loader__')}
        elif rule == 'dict_duplicates_compared_fields':
            # Compare the storage itself too: every entry is normalized with
            # its own field rule, including extra keys (which fail the census).
            observed = {'exact_dict': type(field) is dict,
                        'exact_keys': all(type(key) is str for key in field),
                        'keys': sorted(field) if all(type(key) is str for key in field) else [],
                        'typed_keys': _startup_namespace_value(tuple(field)), 'values': {
                key: result['fields'].get(key) for key in field}}
            # Fill values after all fields have been observed below.
        elif rule == 'weakref_owner_identity':
            observed = {'exact_ref': type(field) is weakref.ReferenceType,
                        'identity': field() is module._TEST_DIRECTORY}
        elif rule == 'owner_weakref_identity':
            observed = {'exact_ref': type(field) is weakref.ReferenceType,
                        'identity': field is weakref.finalize._registry[value._finalizer].weakref}
        elif rule == 'weakref_storage_empty':
            observed = {'empty': field is None}
        elif rule == 'finalizer':
            observed = _startup_object_fields(field, 'finalizer', module)
        elif rule == 'cleanup_code':
            identity = _startup_cleanup_identity(field, type(module._TEST_DIRECTORY), module)
            code = (_startup_namespace_value(field.__code__)
                    if identity else {'unsupported': True})
            observed = {'cleanup_identity': identity, 'equal': identity and
                        field is module._cleanup_test_directory, 'code': code}
        elif rule == 'root_args':
            observed = _startup_namespace_value(field)
        elif rule == 'warning_form_kwargs':
            exact_keys = all(type(key) is str for key in field)
            observed = {'exact_dict': type(field) is dict, 'exact_keys': exact_keys,
                        'keys': sorted(field) if exact_keys else [],
                        'typed_keys': _startup_namespace_value(tuple(field)),
                        'values': _startup_namespace_value({key: val for key, val in field.items()
                            if type(key) is str and key != 'warn_message'}),
                        'exact_warning': type(field.get('warn_message')) is str,
                        'warning_form': type(field.get('warn_message')) is str and
                            field['warn_message'] == 'Implicitly cleaning up {!r}'.format(module. \
                                _TEST_DIRECTORY)}
        elif rule == 'exit_counter_exact_int':
            observed = {'invariant': rule, 'exact_int': type(field) is int}
        elif rule == 'adaptive_history_from_co_code':
            observed = {'invariant': rule, 'bytes': type(field) is bytes,
                        'canonical': _startup_namespace_value(value.co_code)}
        elif rule == 'deprecated_line_derivation':
            observed = {'invariant': rule, 'value': _startup_namespace_value(field)}
        elif rule == 'rewrite_module_membership':
            observed = {'module': 'tests._isolation' in field,
                        'string_set': type(field) is set and all(type(v) is str for v in field)}
        elif rule == 'rewrite_module_path':
            observed = {'module': _startup_namespace_value(field.get('tests._isolation')),
                        'path_cache': type(field) is dict and all(type(k) is str for k in field)}
        elif rule == 'pytest_initial_path_stems':
            observed = {'invariant': rule, 'string_set': type(field) is set and
                        all(type(v) is str for v in field)}
        elif rule == 'pytest_other_module_decisions':
            observed = {'invariant': rule, 'bool_cache': type(field) is dict and
                        all(type(k) is str and type(v) is bool for k, v in field.items())}
        elif rule == 'pytest_session_path_history':
            observed = {'invariant': rule, 'exact_bool': type(field) is bool}
        elif rule == 'pytest_run_config':
            import pytest
            observed = {'invariant': rule, 'exact_config': type(field) is pytest.Config}
        elif rule == 'pytest_run_session':
            import pytest
            observed = {'invariant': rule, 'exact_session': type(field) is pytest.Session}
        else:
            observed = {'unsupported': True, 'rule': rule}
        result['fields'][name] = observed
    for name in names:
        if rules.get(name) == 'dict_duplicates_compared_fields':
            stored = observed_storage[name][0]
            result['fields'][name]['storage_correspondence'] = {
                key: _startup_stored_field_correspondence(raw, *observed_storage[key])
                if key in observed_storage else {'exact_type': False, 'same_value': False}
                for key, raw in stored.items()}
            result['fields'][name]['values'] = {key: result['fields'].get(key)
                                               for key in stored}
    if category == 'finalizer':
        info = weakref.finalize._registry.get(value)
        result['registry'] = (_startup_object_fields(info, 'info', module)
                              if info is not None else {'invalid': True})
    return result


def _assert_startup_field_sets(value, category, case):
    import sys
    version = tuple(sys.version_info[:2])
    literal = _STARTUP_FIELD_LITERALS[version][category]
    if category == 'info':
        assert value.get('exact_info') is True, (case, 'exact_info missing or false')
    assert value.get('exact_type') is True, (case, category, 'exact object type missing or false')
    assert all(type(name) is str for name in value['census']), (case, 'census name is not exact str')
    assert set(value['census']) == literal, (case, category, 'object field set differs',
                                           sorted(set(value['census']) - literal),
                                           sorted(literal - set(value['census'])))
    rules = _STARTUP_FIELD_COMPARATORS[category]
    assert set(value['fields']) == literal, (case, category, 'observed field map differs')
    assert literal <= set(rules), (case, category, 'object field comparator missing')
    for name in literal:
        rule = rules[name]
        if rule == 'dict_duplicates_compared_fields':
            field = value['fields'][name]
            correspondence = field.get('storage_correspondence')
            assert type(correspondence) is dict and set(correspondence) == set(field['keys']), (case \
                , 'stored dictionary correspondence missing')
            assert all(item.get('exact_type') is True and item.get('same_value') is True
                       for item in correspondence.values()), (case, \
                           'stored dictionary value differs from compared field')
        if rule == 'cleanup_code':
            assert value['fields'][name].get('cleanup_identity') is True, (case, \
                'cleanup function or receiver identity missing or false')
        required = {'dict_duplicates_compared_fields': 'exact_dict',
                    'warning_form_kwargs': 'exact_dict',
                    'weakref_owner_identity': 'exact_ref',
                    'owner_weakref_identity': 'exact_ref'}.get(rule)
        if required:
            assert value['fields'][name].get(required) is True, (
                case, category, name, 'exact field type contract missing or false', required)
        if rule in ('dict_duplicates_compared_fields', 'warning_form_kwargs'):
            assert value['fields'][name].get('exact_keys') is True, (case, category, name, \
                'exact field key contract missing or false')
        if rule == 'closure_contents' and value['fields'][name] is not None:
            assert value['fields'][name].get('exact_tuple') is True, (case, \
                'closure tuple type missing or false')
        if rule == 'warning_form_kwargs':
            assert value['fields'][name].get('exact_warning') is True, (case, \
                'exact warning value contract missing or false')
    if category == 'owner':
        _assert_startup_field_sets(value['fields']['_finalizer'], 'finalizer', case)
    if category == 'finalizer':
        _assert_startup_field_sets(value['registry'], 'info', case)


def _startup_namespace_value(value):
    """Typed JSON values; unsupported storage is an explicit failure, never equal."""
    import json
    import pathlib
    import types

    kind = type(value)
    tag = [kind.__module__, kind.__qualname__]
    if any(kind is expected for expected in (type(None), bool, int, str)):
        return {'type': tag, 'value': value}
    if kind is bytes:
        return {'type': tag, 'hex': value.hex()}
    if kind is float:
        return {'type': tag, 'hex': value.hex()}
    if kind is complex:
        return {'type': tag, 'real': value.real.hex(), 'imag': value.imag.hex()}
    if kind is type(Ellipsis):
        return {'type': tag}
    if kind is slice:
        return {'type': tag, 'items': [_startup_namespace_value(v)
                                       for v in (value.start, value.stop, value.step)]}
    if any(kind is expected for expected in (tuple, list)):
        return {'type': tag, 'items': [_startup_namespace_value(v) for v in value]}
    if any(kind is expected for expected in (set, frozenset)):
        items = [_startup_namespace_value(v) for v in value]
        return {'type': tag, 'items': sorted(items, key=lambda v: json.dumps(v, sort_keys=True))}
    if kind is dict:
        pairs = [[_startup_namespace_value(k), _startup_namespace_value(v)]
                 for k, v in value.items()]
        return {'type': tag, 'items': sorted(pairs, key=lambda p: json.dumps(p[0], sort_keys=True))}
    if any(kind is expected for expected in (pathlib.Path, pathlib.PosixPath, pathlib.WindowsPath)):
        return {'type': tag, 'value': str(value)}
    if kind is types.CodeType:
        # No marshal: its reference flags depend on live aliases of constants.
        return {'type': tag, 'object': _startup_object_fields(value, 'code', None)}
    return {'type': tag, 'unsupported': True}


def _startup_location_snapshot(value):
    import sys
    result = _startup_namespace_value(value)
    if type(value) is tuple:
        result['items'] = []
        reference = getattr(sys, '_startup_location_string_type', None)
        for pair in value:
            item = _startup_namespace_value(pair)
            if not any(type(pair) is kind for kind in (tuple, list)) or len(pair) != 2:
                result['items'].append(dict(item, invalid=True))
                continue
            label, location = pair
            if reference is not None and type(location) is reference:
                item['items'][1] = {'controlled_str_subclass': True, 'exact_type': True,
                                    'text': str.__str__(location)}
            result['items'].append(item)
    return result


def _startup_namespace_binding(name, value, module):
    """Describe storage and child-local identities without asserting in the child."""
    import builtins
    import pathlib
    import sys
    import tempfile
    import types
    import weakref

    kind = type(value)
    result = {'type': [kind.__module__, kind.__qualname__]}
    if name in ('__loader__', '__spec__'):
        # A class can copy another class's module/qualname strings. Require
        # identity with the canonical class exported by that module as well.
        reference = sys.modules.get(kind.__module__)
        for part in kind.__qualname__.split('.'):
            reference = vars(reference).get(part) if reference is not None else None
        result['type_identity'] = kind is reference
    if name in ('os', 'sys', 'tempfile', 'Path', '@py_builtins', '@pytest_ar'):
        references = {'Path': pathlib.Path, '@py_builtins': builtins,
                      '@pytest_ar': sys.modules['_pytest.assertion.rewrite']}
        reference = references[name] if name in references else sys.modules[name]
        return dict(result, identity=value is reference,
                    exact_type=kind is (type if name == 'Path' else types.ModuleType))
    if name == '_ORIGINAL_INTERPRETER_LOCATIONS':
        return _startup_location_snapshot(value)
    if name == '_RETIREMENT_CONTROL':
        import re
        import stat
        exact = type(value) is tuple and len(value) == 5
        if not exact:
            return dict(result, invalid=True)
        pid, ticks, cookie, descriptor, pidfd = value
        fields = (module._TEST_ROOT / '.owner').read_text().split()
        info = os.fstat(descriptor)
        live_ticks, boot = module._process_identity(pid)
        return dict(result, exact_tuple=True,
                    positive_pid=type(pid) is int and pid > 0,
                    positive_ticks=type(ticks) is int and ticks > 0,
                    cookie_exact=type(cookie) is str and re.fullmatch('[0-9a-f]{32}', cookie) is not None,
                    fd_exact=type(descriptor) is int and descriptor >= 0,
                    channel_owned=stat.S_ISSOCK(info.st_mode) and info.st_uid == os.getuid(),
                    pidfd_bound=type(pidfd) is int and os.readlink(f'/proc/self/fd/{pidfd}') == \
                        'anon_inode:[pidfd]' and
                        Path(f'/proc/self/fdinfo/{pidfd}').read_text().split('Pid:\t')[1].splitlines \
                            ()[0] == str(pid),
                    helper_live=live_ticks == ticks,
                    owner_bound=fields[5:9] == [str(pid), str(ticks), cookie, 'v3'],
                    boot_bound=fields[2] == boot)
    if name == '_TEST_OWNER_PID':
        return dict(result, exact_int=type(value) is int,
                    caller_owned=type(value) is int and value == os.getpid(),
                    owner_bound=(module._TEST_ROOT / '.owner').read_text().split()[0] == str(value))
    if name == '__builtins__':
        return dict(result, identity=value is builtins.__dict__, exact_type=kind is dict)
    if name == '__loader__':
        category = ('loader_rewrite' if type(value).__name__ == 'AssertionRewritingHook'
                    else 'loader_plain')
        return dict(result, object=_startup_object_fields(value, category, module))
    if name == '__spec__':
        return dict(result, object=_startup_object_fields(value, 'spec', module),
                    loader_identity=value.loader is vars(module).get('__loader__'))
    if name in ('_exact_location', '_license_candidate_files', '_dispatch_fixture',
                '_process_identity', '_retire_test_siblings', '_write_test_owner', \
                    '_test_path_identity', '_start_retirement_guard', '_stop_retirement_guard', \
                    '_retirement_pidfd_open', \
                    '_retirement_pidfd_signal', '_retirement_address', '_veto_retirement', \
                    '_request_retirement', '_retain_test_directory', '_cleanup_test_directory', \
                    'pytest_addoption'):
        if kind is not types.FunctionType:
            return dict(result, invalid=True)
        return dict(result, object=_startup_object_fields(value, 'function', module),
                    globals_identity=value.__globals__ is vars(module),
                    builtins_identity=value.__builtins__ is builtins.__dict__)
    if name == '_TEST_DIRECTORY':
        if kind is not tempfile.TemporaryDirectory:
            return dict(result, invalid=True)
        finalizer = getattr(value, '_finalizer', None)
        finalizer_kind = type(finalizer)
        result.update(name=_startup_namespace_value(value.name),
                      finalizer_type=[finalizer_kind.__module__, finalizer_kind.__qualname__])
        if finalizer_kind is not weakref.finalize:
            return dict(result, invalid=True)
        peek = finalizer.peek()
        result.update(alive=finalizer.alive, peek_present=peek is not None)
        if peek is not None:
            owner, cleanup, args, kwargs = peek
            exact_owner_keys = all(type(key) is str for key in kwargs)
            # Classmethod access makes a new bound method; identity is wrong.
            result.update(owner_identity=owner is value,
                          cleanup_identity=_startup_cleanup_identity(cleanup, type(value), module),
                          cleanup_equal=_startup_cleanup_identity(cleanup, type(value), module) and
                              cleanup is module._cleanup_test_directory,
                          args=_startup_namespace_value(args),
                          kwargs=_startup_namespace_value({k: v for k, v in kwargs.items()
                                                          if type(k) is str and k != 'warn_message'}),
                          exact_peek=type(peek) is tuple,
                          kwargs_registry_identity=kwargs is weakref.finalize._registry[finalizer].kwargs,
                          kwargs_keys=sorted(kwargs) if exact_owner_keys else [],
                          exact_kwargs_keys=exact_owner_keys,
                          exact_warning=type(kwargs.get('warn_message')) is str,
                          warning_form=type(kwargs.get('warn_message')) is str and
                              kwargs['warn_message'] == 'Implicitly cleaning up {!r}'.format(value))
        result['object'] = _startup_object_fields(value, 'owner', module)
        return result
    return _startup_namespace_value(value)


def _startup_namespace_supported(value):
    if isinstance(value, dict):
        if 'category' in value and 'census' in value:
            _assert_startup_field_sets(value, value['category'], 'nested object')
        return not value.get('unsupported') and not value.get('invalid') and all(
            _startup_namespace_supported(v) for v in value.values())
    if isinstance(value, list):
        return all(_startup_namespace_supported(v) for v in value)
    return True


def _assert_startup_namespace(record, report):
    """Every successful child compares every binding, with no default comparator."""
    import tempfile
    import weakref

    case = record['case']
    namespace = report['namespace']
    assert report.get('namespace_module_exact') is True, (case, 'namespace module type missing or false')
    assert report.get('namespace_dict_exact') is True, (case, 'namespace dictionary type missing or false')
    assert report.get('namespace_exact_names') is True, (case, 'original namespace name is not exact str')
    assert all(type(name) is str for name in namespace), (case, 'namespace name is not exact str')
    names = set(namespace)
    rewritten = type(_isolation.__loader__) is sys.modules['_pytest.assertion.rewrite'].AssertionRewritingHook
    literal = _STARTUP_NAMESPACE_NAMES | (_STARTUP_REWRITE_NAMES if rewritten else set())
    parent = vars(_isolation)
    assert names == literal, (case, 'isolation namespace literal name set differs',
                              sorted(names - literal), sorted(literal - names))
    assert names == set(parent), (case, 'isolation namespace parent name set differs',
                                  sorted(names - set(parent)), sorted(set(parent) - names))
    comparators = {name: rule for name, rule in _STARTUP_NAMESPACE_COMPARATORS.items()
                   if name not in _STARTUP_REWRITE_NAMES or rewritten}
    assert set(comparators) == literal, (case, 'isolation namespace comparator name set differs')
    root = report['root']
    assert type(root) is str, (case, 'reported root is not a string', root)
    assert type(_isolation._TEST_DIRECTORY) is tempfile.TemporaryDirectory
    assert type(_isolation._TEST_DIRECTORY._finalizer) is weakref.finalize
    parent_owner = _startup_namespace_binding('_TEST_DIRECTORY', _isolation._TEST_DIRECTORY, _isolation)
    assert parent_owner['alive'] and parent_owner['peek_present'], ('parent finalizer not armed', \
        parent_owner)
    assert parent_owner['owner_identity'] and parent_owner['cleanup_equal'] and parent_owner[ \
        'warning_form'], parent_owner
    for key in ('exact_peek', 'kwargs_registry_identity', 'exact_kwargs_keys', 'exact_warning', \
        'cleanup_identity'):
        assert parent_owner.get(key) is True, ('parent owner contract missing or false', key)
        assert namespace['_TEST_DIRECTORY'].get(key) is True, (case, 'owner contract missing or false', key)
    for name in sorted(names):
        comparator = comparators[name]
        actual = namespace[name]
        category = {'loader': 'loader_rewrite' if rewritten else 'loader_plain',
                    'spec': 'spec', 'function': 'function', 'owner': 'owner'}.get(comparator)
        if category and 'object' in actual:
            _assert_startup_field_sets(actual['object'], category, case)
        assert _startup_namespace_supported(actual), (case, 'isolation namespace unsupported value' \
            , name, actual)
        if record.get('location_inputs') is not None and name in (
                '_ORIGINAL_INTERPRETER_LOCATIONS', '_ORIGINAL_LICENSE_FILES'):
            stdlib, os_file = record['location_inputs']
            if name == '_ORIGINAL_INTERPRETER_LOCATIONS':
                expected = _startup_namespace_value((('sys._stdlib_dir', stdlib), ('os.__file__', os_file)))
                if 'subclass' in record['location_kind']:
                    slot = 0 if record['location_kind'].startswith('stdlib-') else 1
                    expected['items'][slot]['items'][1] = {
                        'controlled_str_subclass': True, 'exact_type': True,
                        'text': record['location_inputs'][slot]}
            else:
                if 'subclass' in record['location_kind']:
                    slot = 0 if record['location_kind'].startswith('stdlib-') else 1
                    values = [stdlib, os_file]
                    values[slot] = type('StartupLocationString', (str,), {})(values[slot])
                    stdlib, os_file = values
                expected = _startup_namespace_value(_startup_location_expected(stdlib, os_file,
                    record['location_properties']['exception']))
        elif comparator in ('parent', 'identity', 'builtins', 'loader', 'spec', 'function'):
            expected = _startup_namespace_binding(name, parent[name], _isolation)
            if comparator in ('identity', 'builtins'):
                assert expected['identity'], ('parent identity differs', name, expected)
                assert expected.get('exact_type') is True, ('parent reference type missing or false', name)
                assert actual.get('exact_type') is True, (case, 'reference type missing or false', name)
            if comparator in ('loader', 'spec'):
                assert expected['type_identity'], ('parent canonical type differs', name, expected)
            if comparator == 'spec':
                assert expected['loader_identity'], ('parent spec loader differs', expected)
            if comparator == 'function':
                assert expected['globals_identity'] and expected['builtins_identity'], ( \
                    'parent function globals/builtins differ', name)
        elif comparator == 'roots':
            expected = _startup_namespace_value(tuple(Path(p) for p in record['expected_roots']))
        elif comparator == 'root':
            expected = _startup_namespace_value(Path(root))
        elif comparator == 'environment':
            expected = _startup_namespace_value({key: str(Path(root) / subdir)
                                                for key, subdir in _STARTUP_REDIRECTS.items()})
        elif comparator == 'control':
            expected = _startup_namespace_binding(name, parent[name], _isolation)
            for key in ('exact_tuple', 'positive_pid', 'positive_ticks', 'cookie_exact',
                        'fd_exact', 'channel_owned', 'pidfd_bound', 'helper_live', 'owner_bound', 'boot_bound'):
                assert expected.get(key) is True, ('parent retirement control invalid', key, expected)
                assert actual.get(key) is True, (case, 'child retirement control invalid', key, actual)
        elif comparator == 'ownerpid':
            expected = _startup_namespace_binding(name, parent[name], _isolation)
            for key in ('exact_int', 'caller_owned', 'owner_bound'):
                assert expected.get(key) is True, ('parent owner PID invalid', key, expected)
                assert actual.get(key) is True, (case, 'child owner PID invalid', key, actual)
        elif comparator == 'owner':
            # Finalizer kwargs follow this interpreter's parent (3.11 lacks
            # delete). The warning embeds the owner's repr, so compare its form.
            expected = dict(parent_owner, name=_startup_namespace_value(root),
                            args=_startup_namespace_value((root,)), alive=True,
                            peek_present=True, owner_identity=True, cleanup_equal=True,
                            warning_form=True)
            # Root-containing owner/registry fields are process-local. Preserve
            # every typed value and normalize only the independently known root.
            def relocate(item):
                if isinstance(item, dict):
                    return {key: relocate(val) for key, val in item.items()}
                if isinstance(item, list):
                    return [relocate(val) for val in item]
                return root if type(item) is str and item == _isolation._TEST_DIRECTORY.name else item
            expected = relocate(expected)
        elif comparator == 'key':
            expected = _startup_namespace_value('INPUTRC')
        elif comparator == 'subdir':
            expected = _startup_namespace_value('data-dirs')
        elif comparator == 'directory':
            expected = _startup_namespace_value(Path(root) / 'data-dirs')
        else:
            pytest.fail(f'{case}: isolation namespace comparator missing for {name}')
        assert _startup_namespace_supported(expected), ('parent unsupported expectation', name, expected)
        assert actual == expected, (case, 'isolation namespace binding differs', name,
                                    {'expected': expected, 'observed': actual})


# Inputs and both output channels are independent literal contracts. Paths below
# the probe root use relative typed values; other absolute paths stay absolute.
_STARTUP_PROBE_CASES = (
    ('_exact_location', 'absolute'), ('_exact_location', 'relative'),
    ('_exact_location', 'subclass'), ('_exact_location', 'nonstring'),
    ('_license_candidate_files', 'valid'), ('_license_candidate_files', 'outside'),
    ('_license_candidate_files', 'empty-with-os'), ('_license_candidate_files', 'empty-no-os'),
    ('_license_candidate_files', 'refused'), ('_license_candidate_files', 'absolute-nul'),
    ('_dispatch_fixture', 'canonical'), ('_dispatch_fixture', 'wrong-prefix'),
    ('_dispatch_fixture', 'wrong-length'), ('_dispatch_fixture', 'malformed-quoting'),
    ('_dispatch_fixture', 'wrong-words'), ('_dispatch_fixture', 'wrong-first-word'),
    ('_dispatch_fixture', 'wrong-suffix'), ('_dispatch_fixture', 'outside'),
    ('_dispatch_fixture', 'different-text'),
    ('_dispatch_fixture', 'tuple'), ('_dispatch_fixture', 'nonsequence'),
    ('_dispatch_fixture', 'bytes-script'), ('_dispatch_fixture', 'subclass-script'),
)

_STARTUP_PROBE_EXPECTED = {
    '_exact_location:absolute': {'return': {'type': ['builtins', 'str'], 'value': '/fixed/absolute'} \
        , 'refused': []},
    '_exact_location:relative': {'return': {'type': ['builtins', 'NoneType'], 'value': None}, \
        'refused': [{'type': ['builtins', 'str'], 'value': 'probe = <uninspectable exemption source>'}]},
    '_exact_location:subclass': {'return': {'type': ['builtins', 'NoneType'], 'value': None}, \
        'refused': [{'type': ['builtins', 'str'], 'value': 'probe = <uninspectable exemption source>'}]},
    '_exact_location:nonstring': {'return': {'type': ['builtins', 'NoneType'], 'value': None}, \
        'refused': [{'type': ['builtins', 'str'], 'value': 'probe = <uninspectable exemption source>'}]},
    '_license_candidate_files:valid': {'files': ['LICENSE.txt', 'LICENSE', 'stdlib/LICENSE.txt', \
        'stdlib/LICENSE'], 'findings': []},
    '_license_candidate_files:outside': {'files': ['/var/empty-call-probe/LICENSE.txt', \
        '/var/empty-call-probe/LICENSE', '/var/empty-call-probe/stdlib/LICENSE.txt', \
        '/var/empty-call-probe/stdlib/LICENSE'], 'findings': []},
    '_license_candidate_files:empty-with-os': {'files': ['LICENSE.txt', 'LICENSE', \
        'stdlib/LICENSE.txt', 'stdlib/LICENSE'], 'findings': []},
    '_license_candidate_files:empty-no-os': {'files': [], 'findings': []},
    '_license_candidate_files:refused': {'files': [], 'findings': [ \
        'stdlib = <uninspectable exemption source>']},
    '_license_candidate_files:absolute-nul': {'files': [], 'findings': [ \
        'stdlib = <uninspectable exemption source>']},
    '_dispatch_fixture:canonical': True,
    '_dispatch_fixture:wrong-prefix': False,
    '_dispatch_fixture:wrong-length': False,
    '_dispatch_fixture:malformed-quoting': False,
    '_dispatch_fixture:wrong-words': False,
    '_dispatch_fixture:wrong-first-word': False,
    '_dispatch_fixture:wrong-suffix': False,
    '_dispatch_fixture:outside': False,
    '_dispatch_fixture:different-text': False,
    '_dispatch_fixture:tuple': True, '_dispatch_fixture:nonsequence': False,
    '_dispatch_fixture:bytes-script': False, '_dispatch_fixture:subclass-script': True,
}


def _startup_probe_value(value, scratch):
    import pathlib
    if any(type(value) is expected for expected in (pathlib.Path, pathlib.PosixPath, pathlib.WindowsPath)):
        spelling = str(value.relative_to(scratch)) if value.is_relative_to(scratch) else str(value)
        return {'type': [type(value).__module__, type(value).__qualname__], 'value': spelling}
    if type(value) is tuple or type(value) is list:
        return {'type': [type(value).__module__, type(value).__qualname__],
                'items': [_startup_probe_value(v, scratch) for v in value]}
    return _startup_namespace_value(value)


def _startup_run_probes(module, scratch):
    import shlex
    import sys
    from pathlib import Path

    class StringSubclass(str):
        pass
    codes = {getattr(module, name).__code__: name for name in
             ('_exact_location', '_license_candidate_files', '_dispatch_fixture')}
    reached = {name: set() for name in codes.values()}
    def trace(frame, event, arg):
        if frame.f_code in codes and event == 'return':
            reached[codes[frame.f_code]].add(frame.f_lineno)
        return trace
    saved_trace = sys.gettrace()
    results = {}
    def script(base):
        brief, out, log, done = [str(base) + suffix for suffix in ('.brief', '.out', '.log', '.done')]
        return (f'true {shlex.quote(brief)} {shlex.quote(out)} '
                f'> {shlex.quote(log)} 2>&1; touch {shlex.quote(done)}')
    sys.settrace(trace)
    try:
        for name, probe in _STARTUP_PROBE_CASES:
            key = name + ':' + probe
            if name == '_exact_location':
                refused = []
                inputs = {'absolute': '/fixed/absolute', 'relative': 'relative',
                          'subclass': StringSubclass('/fixed/absolute'), 'nonstring': 7}
                value = module._exact_location(inputs[probe], 'probe', refused)
                results[key] = {'return': _startup_probe_value(value, scratch),
                                'refused': [_startup_probe_value(v, scratch) for v in refused]}
            elif name == '_license_candidate_files':
                inputs = {
                    'valid': (str(scratch / 'stdlib'), str(scratch / 'stdlib/os.py')),
                    'outside': ('/var/empty-call-probe/stdlib', None),
                    'empty-with-os': ('', str(scratch / 'stdlib/os.py')),
                    'empty-no-os': ('', None), 'refused': ('relative', None),
                    'absolute-nul': ('/absolute\x00location', None),
                }
                stdlib, os_file = inputs[probe]
                results[key] = _startup_probe_value(module._license_candidate_files(
                    (('stdlib', stdlib), ('os_file', os_file))), scratch)
            else:
                text = script(scratch / 'fixture')
                argv = ['setsid', 'sh', '-c', text]
                if probe == 'tuple': argv = tuple(argv)
                elif probe == 'nonsequence': argv = {i: v for i, v in enumerate(argv)}
                elif probe == 'bytes-script': argv[3] = argv[3].encode()
                elif probe == 'subclass-script': argv[3] = StringSubclass(argv[3])
                elif probe == 'wrong-prefix': argv[0] = 'wrong'
                elif probe == 'wrong-length': argv = argv[:3]
                elif probe == 'malformed-quoting': argv[3] = "'"
                elif probe == 'wrong-words': argv[3] = 'true'
                elif probe == 'wrong-first-word': argv[3] = text.replace('true', 'false', 1)
                elif probe == 'wrong-suffix': argv[3] = text.replace('.out', '.wrong')
                elif probe == 'outside': argv[3] = script(scratch.parent / 'outside')
                elif probe == 'different-text': argv[3] = text + ' '
                results[key] = _startup_probe_value(module._dispatch_fixture(argv, scratch), scratch)
    finally:
        sys.settrace(saved_trace)
    return {'results': results, 'returns': {name: sorted(lines) for name, lines in reached.items()}}


def _startup_probe_expected():
    from pathlib import Path
    result = {}
    for key, value in _STARTUP_PROBE_EXPECTED.items():
        if key.startswith('_license_candidate_files:'):
            result[key] = {'type': ['builtins', 'tuple'], 'items': [
                {'type': ['builtins', 'tuple'], 'items': [
                    {'type': [type(Path()).__module__, type(Path()).__qualname__], 'value': p}
                    for p in value['files']]},
                _startup_namespace_value(tuple(value['findings']))]}
        elif key.startswith('_dispatch_fixture:'):
            result[key] = _startup_namespace_value(value)
        else:
            result[key] = value
    return result


def _startup_function_source(function):
    """Read a top-level definition from its current module file, not bytecode paths."""
    module = sys.modules[function.__module__]
    source = importlib.util.decode_source(Path(module.__file__).read_bytes())
    node, = [item for item in ast.parse(source).body
             if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
             and item.name == function.__name__]
    first = min([node.lineno] + [item.lineno for item in node.decorator_list])
    return ''.join(source.splitlines(keepends=True)[first - 1:node.end_lineno]), first


def _startup_observer_helpers():
    return '\n\n'.join(_startup_function_source(helper)[0] for helper in (
        _startup_stored_field_correspondence, _startup_cleanup_identity,
        _startup_field_names, _startup_object_fields,
        _startup_namespace_value, _startup_namespace_binding,
        _startup_probe_value, _startup_run_probes,
        _startup_exact_string_digest, _startup_value_digest, _startup_environment_digests,
        _startup_native_environment, _startup_tree_listing, _startup_location_snapshot))


def test_startup_helper_source_ignores_stale_bytecode_filename(monkeypatch, tmp_path):
    function = _startup_namespace_value
    expected = _startup_observer_helpers()
    stale = str(tmp_path / 'previous-checkout' / 'test_home_isolation.py')
    monkeypatch.setattr(function, '__code__', function.__code__.replace(co_filename=stale))
    assert _startup_observer_helpers() == expected
    assert 'def _startup_namespace_value(' in expected


def _assert_startup_probe_coverage(observation):
    import ast
    import inspect
    for name in ('_exact_location', '_license_candidate_files', '_dispatch_fixture'):
        function = getattr(_isolation, name)
        source, first = _startup_function_source(function)
        tree = ast.parse(source)
        expected = {first + node.lineno - 1 for node in ast.walk(tree)
                    if isinstance(node, ast.Return)}
        observed = set(observation['returns'][name])
        assert expected <= observed, (name, 'probe return coverage missing',
                                      sorted(expected - observed), sorted(observed))


def test_startup_namespace_probe_return_coverage(_startup_observation):
    _assert_startup_probe_coverage(_startup_observation['rich']['parent_probes'])


@pytest.fixture(scope='module', params=[
    'none', 'flag-B', 'env-no-bytecode', 'env-prefix', 'flag-prefix'])
def _startup_main_scrub_observation(tmp_path_factory, request):
    import json
    import subprocess
    root = tmp_path_factory.mktemp('main-scrub')
    # Each bytecode configuration uses its own copied configured plugin package.
    # Local package caches and prefix mirrors stay under this fixture's scratch.
    # The none member may cache imports anywhere the interpreter can write
    # import caches, including site-packages and the base-interpreter stdlib.
    checkout = Path(__file__).resolve().parents[1]
    isolated = root / 'tui'
    package = isolated / 'tests'
    package.mkdir(parents=True)
    for name in ('__init__.py', '_isolation.py'):
        (package / name).write_bytes((checkout / 'tests' / name).read_bytes())
    config = isolated / 'pyproject.toml'
    config.write_bytes((checkout / 'pyproject.toml').read_bytes())
    probe = isolated / 'test_main_scrub.py'
    probe.write_text("""import json, os, sys
from pathlib import Path
import pytest

@pytest.fixture(scope='session', autouse=True)
def configured_run(request):
    plugin = request.config.pluginmanager.get_plugin('tests._isolation')
    report = {'dont_write_bytecode': sys.flags.dont_write_bytecode,
              'pycache_prefix': sys.pycache_prefix,
              'bytecode_env_present': 'PYTHONDONTWRITEBYTECODE' in os.environ,
              'configured_plugin': plugin is not None,
              'plugin_file': None if plugin is None else plugin.__file__}
    Path(__file__).with_suffix('.json').write_text(json.dumps(report))
    assert plugin is not None, 'configured isolation plugin missing'
    assert plugin is sys.modules.get('tests._isolation')

def test_fleet():
    assert not any(name.startswith('FLEET_') for name in os.environ), 'main FLEET_ scrub missing'

def test_path():
    assert os.environ['PATH'] == os.defpath, 'main PATH reset missing'
    assert os.environ['SHELL'] == '/bin/bash', 'main SHELL reset missing'

def test_shell():
    assert not any(name in os.environ for name in
                   ('BASH_ENV', 'ENV', 'ZDOTDIR', 'HISTFILE', 'INPUTRC')), 'main shell-init scrub missing'
""")
    env = {'HOME': str(root), 'PATH': str(root / 'hostile-bin'), 'SHELL': '/hostile-shell',
           'FLEET_REVIEW_PROBE': 'planted', 'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1'}
    env.update({name: 'planted' for name in ('BASH_ENV', 'ENV', 'ZDOTDIR', 'HISTFILE', 'INPUTRC')})
    mode = request.param
    flags = []
    prefix = None
    if mode == 'flag-B':
        flags = ['-B']
    elif mode == 'env-no-bytecode':
        env['PYTHONDONTWRITEBYTECODE'] = '1'
    elif mode in ('env-prefix', 'flag-prefix'):
        prefix = str(root / 'bytecode')
        if mode == 'env-prefix':
            env['PYTHONPYCACHEPREFIX'] = prefix
        else:
            flags = ['-X', 'pycache_prefix=' + prefix]
    expected = {'dont_write_bytecode': int(mode in ('flag-B', 'env-no-bytecode')),
                'pycache_prefix': prefix, 'bytecode_env_present': mode == 'env-no-bytecode'}
    result = subprocess.run(
        [sys.executable, *flags, '-m', 'pytest', str(probe), '-c', str(config),
         '-v', '-p', 'no:cacheprovider', '--basetemp=' + str(root / 'bt')],
        cwd=isolated, env=env, capture_output=True, text=True, timeout=60)
    report_path = probe.with_suffix('.json')
    report = json.loads(report_path.read_text()) if report_path.is_file() else None
    return result, report, package / '_isolation.py', expected


@pytest.mark.parametrize('property', ['fleet', 'path', 'shell'])
def test_startup_main_process_scrub_across_bytecode_configurations(_startup_main_scrub_observation, property):
    result, report, plugin_file, expected = _startup_main_scrub_observation
    output = result.stdout + result.stderr
    assert result.returncode in (0, 1), output
    # Positive structured outcomes cannot be produced by a traceback's source text.
    assert report is not None, output
    assert report['configured_plugin'], ('configured isolation plugin missing', output)
    for name, value in expected.items():
        assert report[name] == value, (name, report, expected)
    assert Path(report['plugin_file']).resolve() == plugin_file.resolve(), report
    # Each property owns only its named inner outcome, even if a sibling fails.
    assert '::test_' + property + ' PASSED' in output, output


def test_startup_helper_has_independent_session(tmp_path):
    sibling = tmp_path / 'fleet-tui-tests-session'
    sibling.mkdir()
    child, *_ = _startup_fixture_guard(sibling, os.getpid(), _isolation._process_identity('self')[0])
    assert os.getsid(child) == child, 'retirement helper shares owner process-group session'
    assert os.getsid(child) != os.getsid(0)


def test_startup_lost_helper_vetoes_finalizer(tmp_path, monkeypatch, capsys):
    import signal
    sibling = tmp_path / 'fleet-tui-tests-lost-helper'
    sibling.mkdir()
    (sibling / 'payload').write_text('must survive lost helper')
    control = _startup_fixture_guard(sibling, os.getpid(), _isolation._process_identity('self')[0])
    _STARTUP_GUARD_FIXTURE_CONTROLS.pop()  # This test's finalizer owns these descriptors.
    child, ticks, cookie, descriptor, pidfd = control
    _isolation._retirement_pidfd_signal(pidfd, signal.SIGKILL)
    os.waitpid(child, 0)
    # The child has been reaped; a numeric PGID might now name another process.
    monkeypatch.setattr(os, 'killpg', lambda *_: pytest.fail('signalled a numeric process group'))
    monkeypatch.setattr(os, 'kill', lambda *_: pytest.fail('signalled a numeric process'))
    monkeypatch.setattr(_isolation, '_RETIREMENT_CONTROL', control)
    try:
        _isolation._cleanup_test_directory(str(sibling), 'fixture cleanup')
        assert (sibling / 'payload').is_file(), 'lost helper allowed finalizer deletion'
        assert (sibling / 'payload').read_text() == 'must survive lost helper'
        assert 'retirement helper already revoked or lost' in capsys.readouterr().err
    finally:
        _isolation._retain_test_directory()


_STARTUP_GUARD_FIXTURE_CONTROLS = []


def _startup_fixture_guard(sibling, pid, ticks, proc_root='/proc'):
    # A deliberate harness fork, admitted once by the existing gate. No public
    # test process permission is widened and conftest itself stays unchanged.
    from .conftest import _permit_process
    with _permit_process('os.fork'):
        control = _isolation._start_retirement_guard(sibling, pid, ticks, proc_root)
    _STARTUP_GUARD_FIXTURE_CONTROLS.append(control)
    return control


@pytest.fixture(autouse=True)
def _startup_fixture_guard_cleanup():
    first = len(_STARTUP_GUARD_FIXTURE_CONTROLS)
    yield
    for child, ticks, cookie, descriptor, pidfd in _STARTUP_GUARD_FIXTURE_CONTROLS[first:]:
        try:
            os.write(descriptor, b'R')
        except OSError as error:
            assert error.errno == 32, error
        finally:
            os.close(descriptor)
        try:
            os.waitpid(child, 0)
        except ChildProcessError:
            pass  # A named test already reaped its own granted/revoked helper.
        os.close(pidfd)
    del _STARTUP_GUARD_FIXTURE_CONTROLS[first:]


def _startup_guard_owner(sibling, payload, proc_root='/proc'):
    """Give the real one-use helper to a deliberately constructed owner case."""
    fields = payload.split()
    if len(fields) != 5 or not hasattr(_isolation, '_start_retirement_guard'):
        return payload
    control = _startup_fixture_guard(sibling, int(fields[0]), int(fields[1]), proc_root)
    child, ticks, cookie, descriptor, pidfd = control
    # Every helper belongs to this fixture tree. A test's ordinary cleanup
    # removes that tree and the helper exits; successful retirement consumes it.
    return payload.rstrip() + f' {child} {ticks} {cookie} v3\n'


def test_startup_sibling_cleanup_requires_dead_owner(tmp_path, capsys):
    import stat
    root = tmp_path / 'siblings'
    root.mkdir()
    proc = tmp_path / 'proc'
    boot_file = proc / 'sys/kernel/random/boot_id'
    boot_file.parent.mkdir(parents=True)
    boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    boot_file.write_text(boot)
    pid = os.getpid()
    live = proc / str(pid)
    live.mkdir()
    live_stat = Path('/proc/self/stat').read_text()
    (live / 'stat').write_text(live_stat)
    (live / 'status').write_text(f'NStgid:\t{pid}\n')
    (live / 'ns').mkdir()
    (live / 'ns/pid').write_text('namespace fixture')
    (proc / 'self').symlink_to(str(pid))
    namespace = (live / 'ns/pid').stat()
    ns = f'{namespace.st_dev} {namespace.st_ino}'
    ticks = int(live_stat.rsplit(')', 1)[1].split()[19])
    owners = {
        'missing': None, 'empty': '', 'unparsable': 'not an owner',
        'dead': f'99999999 1 {boot} {ns}\n',
        'live': f'{pid} {ticks} {boot} {ns}\n',
        'reused': f'{pid} {ticks + 1} {boot} {ns}\n',
        'other-boot': f'{pid} {ticks} 00000000-0000-0000-0000-000000000000 {ns}\n',
    }
    for label, payload in owners.items():
        sibling = root / ('fleet-tui-tests-' + label)
        sibling.mkdir(mode=0o700)
        if payload is not None:
            (sibling / '.owner').write_text(_startup_guard_owner(sibling, payload, proc))
        (sibling / 'payload').write_text('fixture')
    # A symlink is not a retirement candidate, even when its target is dead.
    target = tmp_path / 'target'
    target.mkdir()
    (target / '.owner').write_text(owners['dead'])
    (target / 'payload').write_text('target contents')
    link = root / 'fleet-tui-tests-link'
    link.symlink_to(target, target_is_directory=True)
    plain = root / 'fleet-tui-tests-file'
    plain.write_text('plain contents')
    _isolation._retire_test_siblings(root, proc)
    assert link.is_symlink(), 'retirement unlinked a symlink'
    assert (target / 'payload').read_text() == 'target contents'
    assert plain.read_text() == 'plain contents'
    assert not (root / 'fleet-tui-tests-dead').exists(), 'dead owner not retired'
    assert {p.name for p in root.iterdir()} == {
        'fleet-tui-tests-' + name for name in
        ('missing', 'empty', 'unparsable', 'live', 'other-boot', 'link', 'file')}
    diagnostics = capsys.readouterr().err
    for name in ('missing', 'empty', 'unparsable', 'other-boot', 'link', 'file'):
        assert 'fleet-tui-tests-' + name in diagnostics
    fresh = tmp_path / 'fresh'
    fresh.mkdir()
    _isolation._write_test_owner(fresh, proc)
    assert list(fresh.iterdir()) == [fresh / '.owner']
    assert stat.S_IMODE((fresh / '.owner').stat().st_mode) == 0o600
    assert (fresh / '.owner').read_text() == (f'{pid} {ticks} {boot} {ns} ' + ' '.join(map(str, \
        _isolation._RETIREMENT_CONTROL[:3])) + ' v3\n')
    with pytest.raises(FileExistsError):
        _isolation._write_test_owner(fresh, proc)
    # Ancestor-mounted procfs must not judge local numeric PIDs, even if the
    # ancestor/local PID numbers happen to coincide.
    foreign_view = root / 'fleet-tui-tests-foreign-proc-view'
    foreign_view.mkdir()
    (foreign_view / '.owner').write_text(_startup_guard_owner(foreign_view, owners['dead'], proc))
    (live / 'status').write_text(f'NStgid:\t{pid} {pid}\n')
    _isolation._retire_test_siblings(root, proc)
    assert foreign_view.is_dir(), 'ancestor procfs judged a local PID'


def test_startup_sibling_foreign_namespace_owner_is_retained(tmp_path):
    root = tmp_path / 'siblings'
    root.mkdir()
    boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    namespace = Path('/proc/self/ns/pid').stat()
    pid = os.getpid()
    ticks = _isolation._process_identity(pid)[0]
    # pid denotes an unrelated live process in the sweeper's view. Different
    # start ticks stand for the live owner's incarnation in another namespace.
    for label, ns in [('foreign', f' {namespace.st_dev} {namespace.st_ino + 1}'),
                      ('legacy-foreign', '')]:
        sibling = root / ('fleet-tui-tests-' + label)
        sibling.mkdir()
        (sibling / '.owner').write_text(_startup_guard_owner(sibling, f'{pid} {ticks + 1} {boot}{ns}\n'))
        (sibling / 'payload').write_text('live owner payload')
    _isolation._retire_test_siblings(root)
    for label in ('foreign', 'legacy-foreign'):
        assert (root / ('fleet-tui-tests-' + label) / 'payload').read_text() == 'live owner payload' \
            , 'live foreign owner removed'


@pytest.mark.parametrize('spelling', ['uppercase', 'hex', 'braces', 'urn'])
def test_startup_sibling_noncanonical_boot_id_is_retained(tmp_path, spelling):
    import uuid
    root = tmp_path / 'siblings'
    root.mkdir()
    boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    malformed = {'uppercase': boot.upper(), 'hex': uuid.UUID(boot).hex,
                 'braces': '{' + boot + '}', 'urn': 'urn:uuid:' + boot}[spelling]
    assert malformed != boot, 'fixture needs a noncanonical UUID spelling'
    assert str(uuid.UUID(malformed)) == boot, 'fixture must denote the current boot'
    namespace = Path('/proc/self/ns/pid').stat()
    sibling = root / 'fleet-tui-tests-noncanonical-boot'
    sibling.mkdir()
    (sibling / '.owner').write_text(_startup_guard_owner(
        sibling, f'99999999 1 {malformed} {namespace.st_dev} {namespace.st_ino}\n'))
    (sibling / 'payload').write_text('unjudged owner')
    _isolation._retire_test_siblings(root)
    assert sibling.is_dir(), 'noncanonical boot ID owner removed'
    assert (sibling / 'payload').read_text() == 'unjudged owner'


@pytest.mark.parametrize('foreign_uid_path', ['directory', 'owner-file'])
def test_startup_sibling_foreign_uid_is_retained(tmp_path, monkeypatch, capsys, foreign_uid_path):
    root = tmp_path / 'siblings'
    root.mkdir()
    namespace = Path('/proc/self/ns/pid').stat()
    boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    sibling = root / 'fleet-tui-tests-foreign-uid'
    sibling.mkdir()
    owner = sibling / '.owner'
    owner.write_text(_startup_guard_owner(
        sibling, f'99999999 1 {boot} {namespace.st_dev} {namespace.st_ino}\n'))
    (sibling / 'payload').write_text('foreign uid payload')
    # Simulate the independently checked directory/file uid without chown or
    # root privileges. Preserve every other lstat field and the ordinary tree.
    selected = sibling if foreign_uid_path == 'directory' else owner
    original = Path.lstat
    observations = []
    def foreign_lstat(path, *args, **kwargs):
        info = original(path, *args, **kwargs)
        if path == selected:
            observations.append(path)
            fields = list(info)
            fields[4] = os.getuid() + 1
            return os.stat_result(fields)
        return info
    monkeypatch.setattr(Path, 'lstat', foreign_lstat)
    _isolation._retire_test_siblings(root)
    assert observations, 'foreign uid fixture was not consulted'
    assert sibling.is_dir(), 'foreign uid directory or owner file removed'
    assert owner.is_file()
    assert (sibling / 'payload').read_text() == 'foreign uid payload'
    assert 'not an owned' in capsys.readouterr().err


def test_startup_sibling_old_atime_is_retired_on_first_read(tmp_path, capsys):
    import time
    root = tmp_path / 'siblings'
    root.mkdir()
    namespace = Path('/proc/self/ns/pid').stat()
    boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    sibling = root / 'fleet-tui-tests-old-atime'
    sibling.mkdir()
    owner = sibling / '.owner'
    owner.write_text(_startup_guard_owner(
        sibling, f'99999999 1 {boot} {namespace.st_dev} {namespace.st_ino}\n'))
    old = time.time_ns() - 3 * 86400 * 10**9
    os.utime(owner, ns=(old, owner.stat().st_mtime_ns))
    before = owner.lstat()
    _isolation._retire_test_siblings(root)
    assert not sibling.exists(), 'old unread owner silently skipped on first read'
    assert not capsys.readouterr().err
    # Prove the comparator excludes access-only changes without reading owner.
    from types import SimpleNamespace
    fields = {name: getattr(before, name) for name in
              ('st_dev', 'st_ino', 'st_mode', 'st_uid', 'st_gid',
               'st_size', 'st_mtime_ns', 'st_ctime_ns')}
    fields['st_atime_ns'] = before.st_atime_ns + 86400 * 10**9
    assert _isolation._test_path_identity(before) == _isolation._test_path_identity(SimpleNamespace(**fields))


def test_startup_persistent_scratch_survives_later_retirement(tmp_path, capsys):
    import shutil
    class Factory:
        def getbasetemp(self):
            return tmp_path / 'pytest-0'
    base = Factory().getbasetemp()
    base.mkdir()
    evidence = _startup_persistent_scratch(Factory())
    (evidence / 'partial.json').write_text('partial evidence')
    assert evidence.parent == base.parent and not evidence.is_relative_to(base)
    # Model only pytest's numbered-base retirement, then the actual early sweeper.
    shutil.rmtree(base)
    _isolation._retire_test_siblings(tmp_path)
    _isolation._retire_test_siblings(tmp_path)
    assert (evidence / 'partial.json').read_text() == 'partial evidence', \
        'retained startup evidence was retirement input'
    diagnostics = capsys.readouterr().err
    assert str(evidence) in diagnostics and 'persistent startup evidence' in diagnostics


@pytest.mark.parametrize('field_count', [3, 5])
def test_startup_legacy_owner_records_are_retained(tmp_path, capsys, field_count):
    namespace = Path('/proc/self/ns/pid').stat()
    boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    sibling = tmp_path / 'fleet-tui-tests-legacy-retention'
    sibling.mkdir(mode=0o700)
    fields = ['99999999', '1', boot, str(namespace.st_dev), str(namespace.st_ino)]
    (sibling / '.owner').write_text(' '.join(fields[:field_count]) + '\n')
    (sibling / 'partial').write_text('historically retained evidence')
    for _ in range(2):
        _isolation._retire_test_siblings(tmp_path)
        assert (sibling / 'partial').read_text() == 'historically retained evidence', \
            'legacy retained evidence removed'
        assert str(sibling) in capsys.readouterr().err


def test_startup_revoked_permission_survives_later_retirement(tmp_path, capsys):
    namespace = Path('/proc/self/ns/pid').stat()
    boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    sibling = tmp_path / 'fleet-tui-tests-revoked'
    sibling.mkdir(mode=0o700)
    control = _startup_fixture_guard(sibling, 99999999, 1)
    child, ticks, cookie, descriptor, pidfd = control
    (sibling / '.owner').write_text(
        f'99999999 1 {boot} {namespace.st_dev} {namespace.st_ino} {child} {ticks} {cookie} v3\n')
    (sibling / 'partial').write_text('retained without a filesystem marker')
    os.write(descriptor, b'R')
    assert os.waitpid(child, 0)[1] == 0
    for _ in range(2):
        _isolation._retire_test_siblings(tmp_path)
        assert (sibling / 'partial').is_file(), 'revoked retention permission was ignored'
        assert str(sibling) in capsys.readouterr().err


def test_startup_helper_identity_uses_self_proc_view(tmp_path, monkeypatch):
    sibling = tmp_path / 'fleet-tui-tests-helper-self'
    sibling.mkdir()
    original = _isolation._process_identity
    boot = original('self')[1]
    def ancestor_numeric_view(pid, proc_root='/proc'):
        if type(pid) is int and pid != 99999999:
            return 1, boot  # Numeric PID lookup denotes another incarnation.
        return original(pid, proc_root)
    monkeypatch.setattr(_isolation, '_process_identity', ancestor_numeric_view)
    control = _startup_fixture_guard(sibling, 99999999, 1)
    child_stat = Path(f'/proc/{control[0]}/stat').read_text()
    actual = int(child_stat.rsplit(')', 1)[1].split()[19])
    assert actual != 1, 'positive control needs ordinary positive start ticks'
    assert control[1] == actual, 'helper identity came from ambiguous numeric procfs'


@pytest.fixture(scope='module')
def _startup_forked_finalizer_observation(tmp_path_factory):
    import subprocess
    root = tmp_path_factory.mktemp('forked-finalizer')
    script = r"""
import os, pathlib, sys
sys.path.insert(0, sys.argv[1])
from tests import _isolation as module
tree = pathlib.Path(module._TEST_DIRECTORY.name)
pid = os.fork()
if pid == 0:
    module._TEST_DIRECTORY._finalizer()
    os._exit(0)
assert os.waitpid(pid, 0)[1] == 0
assert tree.is_dir() and (tree / '.owner').is_file(), 'forked finalizer removed live parent tree'
assert module._RETIREMENT_CONTROL[3] is not None
print('FORKED_FINALIZER_KEPT_LIVE_PARENT')
"""
    return subprocess.run([sys.executable, '-B', '-c', script,
                           str(Path(__file__).resolve().parents[1])],
                          env={'HOME': str(root), 'PATH': '/usr/bin:/bin', 'PYTHONDONTWRITEBYTECODE': '1'},
                          capture_output=True, text=True, timeout=60)


def test_startup_forked_finalizer_does_not_remove_live_parent(_startup_forked_finalizer_observation):
    result = _startup_forked_finalizer_observation
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'FORKED_FINALIZER_KEPT_LIVE_PARENT' in result.stdout


@pytest.mark.parametrize('number', [5, 28, 30])
def test_startup_partial_retirement_is_never_retried(tmp_path, monkeypatch, capsys, number):
    import shutil
    namespace = Path('/proc/self/ns/pid').stat()
    boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    sibling = tmp_path / 'fleet-tui-tests-partial-retirement'
    sibling.mkdir(mode=0o700)
    control = _startup_fixture_guard(sibling, 99999999, 1)
    child, ticks, cookie, descriptor, pidfd = control
    (sibling / '.owner').write_text(
        f'99999999 1 {boot} {namespace.st_dev} {namespace.st_ino} {child} {ticks} {cookie} v3\n')
    (sibling / 'first').write_text('first payload')
    (sibling / 'remaining').write_text('partial evidence')
    calls = []
    def partial_cleanup(path):
        assert path == sibling
        calls.append(path)
        (path / 'first').unlink()
        raise OSError(number, 'injected partial retirement storage error')
    monkeypatch.setattr(shutil, 'rmtree', partial_cleanup)
    with pytest.raises(OSError) as raised:
        _isolation._retire_test_siblings(tmp_path)
    assert raised.value.errno == number
    assert os.waitpid(child, 0)[1] == 0
    for _ in range(2):
        _isolation._retire_test_siblings(tmp_path)
        assert (sibling / 'remaining').read_text() == 'partial evidence', \
            'partial cleanup evidence removed on next sweep'
        assert str(sibling) in capsys.readouterr().err
    assert calls == [sibling], 'partial cleanup was retried'


@pytest.mark.parametrize('number', [5, 28, 30])
def test_startup_owner_read_storage_error_is_permanently_retained(tmp_path, monkeypatch, capsys, number):
    namespace = Path('/proc/self/ns/pid').stat()
    boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    sibling = tmp_path / 'fleet-tui-tests-owner-read-error'
    sibling.mkdir(mode=0o700)
    owner = sibling / '.owner'
    owner.write_text(_startup_guard_owner(
        sibling, f'99999999 1 {boot} {namespace.st_dev} {namespace.st_ino}\n'))
    (sibling / 'partial').write_text('owner read storage error evidence')
    original = Path.read_text
    def failed_read(path, *args, **kwargs):
        if path == owner:
            raise OSError(number, 'injected owner read storage error')
        return original(path, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'read_text', failed_read)
        with pytest.raises(OSError) as raised:
            _isolation._retire_test_siblings(tmp_path)
        assert raised.value.errno == number
    for _ in range(2):
        _isolation._retire_test_siblings(tmp_path)
        assert (sibling / 'partial').is_file(), 'owner read storage error was ignored by later sweep'
        assert str(sibling) in capsys.readouterr().err


@pytest.fixture(scope='module')
def _startup_late_storage_retention_observation(tmp_path_factory):
    import subprocess
    root = _startup_persistent_scratch(tmp_path_factory)
    script = r"""
import os, pathlib, sys, tempfile
base = pathlib.Path(sys.argv[2]); phase = sys.argv[3]; number = int(sys.argv[4])
original_temp = tempfile.mkdtemp
def private_temp(suffix=None, prefix=None, dir=None):
    return original_temp(suffix=suffix, prefix=prefix, dir=base)
tempfile.mkdtemp = private_temp
sys.path.insert(0, sys.argv[1])
from tests import _isolation as module
tree = pathlib.Path(module._TEST_DIRECTORY.name)
(tree / 'partial').write_text('HOME evidence after storage error')
if phase == 'finalizer':
    def failed_cleanup(cls, name, warn_message, ignore_errors=False, **kwargs):
        assert pathlib.Path(name) == tree
        (tree / 'home').rmdir()
        raise OSError(number, 'injected finalizer storage error')
    tempfile.TemporaryDirectory._cleanup = classmethod(failed_cleanup)
    try:
        module._TEST_DIRECTORY._finalizer()
    except OSError as error:
        assert error.errno == number
    else:
        raise AssertionError('finalizer storage injection did not fire')
else:
    from tests import test_home_isolation as tests
    class Manager:
        def get_plugin(self, name):
            return None
    class Config:
        pluginmanager = Manager()
        def getoption(self, name):
            return False if name == '--keep-startup-artifacts' else None
    def failed_scratch(factory):
        raise OSError(number, 'injected startup storage error')
    tests._startup_persistent_scratch = failed_scratch
    try:
        next(tests._startup_observation.__wrapped__(object(), Config()))
    except BaseException as error:
        assert 'startup storage error' in str(error)
    else:
        raise AssertionError('startup storage injection did not fire')
assert not module._TEST_DIRECTORY._finalizer.alive, 'storage failure left HOME finalizer armed'
print('RETAINED_HOME=' + str(tree))
"""
    results = {}
    for phase in ('finalizer', 'startup'):
        for number in (5, 28, 30):
            base = root / f'{phase}-{number}'
            base.mkdir()
            results[phase, number] = subprocess.run(
                [sys.executable, '-B', '-c', script, str(Path(__file__).resolve().parents[1]),
                 str(base), phase, str(number)],
                env={'HOME': str(base), 'PATH': '/usr/bin:/bin', 'PYTHONDONTWRITEBYTECODE': '1',
                     'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1'},
                capture_output=True, text=True, timeout=60)
    return results


@pytest.mark.parametrize('phase,number', [(phase, number) for phase in ('finalizer', 'startup') for \
    number in (5, 28, 30)])
def test_startup_late_storage_error_preserves_home(_startup_late_storage_retention_observation, \
    phase, number, capsys):
    result = _startup_late_storage_retention_observation[phase, number]
    assert result.returncode == 0, result.stdout + result.stderr
    line, = [line for line in result.stdout.splitlines() if line.startswith('RETAINED_HOME=')]
    tree = Path(line.split('=', 1)[1])
    for _ in range(2):
        _isolation._retire_test_siblings(tree.parent)
        assert (tree / 'partial').read_text() == 'HOME evidence after storage error', \
            'late storage HOME evidence retired'
        assert str(tree) in capsys.readouterr().err


@pytest.fixture(scope='module')
def _startup_keep_flag_alone_observation(tmp_path_factory):
    import subprocess
    root = tmp_path_factory.mktemp('keep-flag-refusal')
    script = """import pytest, sys
def forbidden_base(self):
    raise AssertionError('silent keep fallback allocated temporary startup evidence')
pytest.TempPathFactory.getbasetemp = forbidden_base
raise SystemExit(pytest.main(sys.argv[1:]))
"""
    result = subprocess.run([sys.executable, '-B', '-c', script,
                             'tests/test_home_isolation.py::test_startup_records_original_user_roots',
                             '--keep-startup-artifacts', '-q', '-p', 'no:cacheprovider',
                             '--basetemp=' + str(root / 'child-base')],
                            cwd=Path(__file__).resolve().parents[1],
                            env={'HOME': str(root), 'PATH': '/usr/bin:/bin',
                                 'PYTHONDONTWRITEBYTECODE': '1',
                                 'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1'},
                            capture_output=True, text=True, timeout=60)
    return result


def test_startup_keep_flag_alone_refuses_without_helper(monkeypatch, _startup_keep_flag_alone_observation):
    result = _startup_keep_flag_alone_observation
    assert result.returncode == 1, result.stdout + result.stderr
    assert '--keep-startup-artifacts requires --startup-artifacts-root' in result.stdout + result. \
        stderr, 'keep flag silently accepted without persistent output'
    monkeypatch.setattr(sys, '_fleet_startup_artifact_helper', None)
    class Config:
        def getoption(self, name):
            return name == '--keep-startup-artifacts'
    class Factory:
        def mktemp(self, name):
            raise AssertionError('silent keep fallback allocated temporary startup evidence')
        def getbasetemp(self):
            raise AssertionError('silent keep fallback allocated temporary startup evidence')
    generator = _startup_observation.__wrapped__(Factory(), Config())
    with pytest.raises(ValueError, match='--keep-startup-artifacts requires --startup-artifacts-root'):
        next(generator)


def _startup_stop_recorded_helper(identity):
    import json
    import signal
    import time
    record = json.loads(identity.read_text())
    child, ticks = record['pid'], record['ticks']
    try:
        pidfd = _isolation._retirement_pidfd_open(child)
    except ProcessLookupError:
        return
    try:
        if _isolation._process_identity(child)[0] != ticks:
            return  # The recorded incarnation has gone; never signal a reused PID.
        try:
            _isolation._retirement_pidfd_signal(pidfd, signal.SIGKILL)
        except ProcessLookupError:
            pass
        # An orphan zombie cannot grant retirement; its new parent owns reaping.
        for _ in range(100):
            seen, _boot = _isolation._process_identity(child)
            if seen != ticks:
                return
            try:
                raw = Path(f'/proc/{child}/stat').read_text()
            except FileNotFoundError:
                return
            # Field 3 follows the last ')' of comm; identity's second field is boot ID.
            if raw[raw.rfind(')') + 2:].split()[0] == 'Z':
                return
            time.sleep(0.01)
        raise AssertionError('recorded retention helper survived cleanup')
    finally:
        os.close(pidfd)


@pytest.mark.parametrize('state', ['zombie', 'live'])
def test_startup_recorded_helper_cleanup_distinguishes_zombie_and_live(tmp_path, monkeypatch, state):
    import json
    import signal
    from .conftest import _permit_process
    reader, writer = os.pipe()
    with _permit_process('os.fork'):
        child = os.fork()
    if child == 0:
        os.close(writer)
        if state == 'live':
            os.read(reader, 1)
        os._exit(0)
    os.close(reader)
    try:
        ticks, _boot = _isolation._process_identity(child)
        assert ticks is not None
        identity = tmp_path / 'helper-identity.json'
        identity.write_text(json.dumps({'pid': child, 'ticks': ticks}))
        if state == 'zombie':
            # Observe exit without reaping: this parent deliberately keeps a real zombie.
            os.waitid(os.P_PID, child, os.WEXITED | os.WNOWAIT)
            raw = Path(f'/proc/{child}/stat').read_text()
            assert raw[raw.rfind(')') + 2:].split()[0] == 'Z'
            _startup_stop_recorded_helper(identity)
            assert _isolation._process_identity(child)[0] == ticks, 'fixture reaped zombie early'
        else:
            # Suppress revocation to prove a surviving live incarnation still fails.
            monkeypatch.setattr(_isolation, '_retirement_pidfd_signal', lambda *_: None)
            with pytest.raises(AssertionError, match='recorded retention helper survived cleanup'):
                _startup_stop_recorded_helper(identity)
            assert _isolation._process_identity(child)[0] == ticks
    finally:
        os.close(writer)
        try:
            os.kill(child, signal.SIGKILL)
        except ProcessLookupError:
            pass
        os.waitpid(child, 0)


# Every exception-injection phase has the same ten fault classes. In particular,
# helper startup and final clear reject narrowed BaseException handlers. Short
# writes are a separate I/O result control, not an injected exception class.
_STARTUP_RETENTION_FAULTS = (5, 28, 30, 122, 2, 'value-error', 'keyboard-interrupt',
                             'system-exit', 'generator-exit', 'direct-base-exception')
_STARTUP_RETENTION_PHASES = ('owner', 'finalize', 'payload', 'arm', 'clear',
                             'parent-listener-close', 'parent-read-close', 'parent-ready-close',
                             'ready-read', 'ready-parse', 'pidfd', 'parent-ready-read-close')
_STARTUP_RETENTION_FAILURES = [
    (phase, fault) for phase in _STARTUP_RETENTION_PHASES for fault in _STARTUP_RETENTION_FAULTS
]


def _startup_retention_matrix_coverage(rows):
    expected = set(_STARTUP_RETENTION_FAULTS)
    assert {phase for phase, _ in rows} == set(_STARTUP_RETENTION_PHASES)
    for phase in _STARTUP_RETENTION_PHASES:
        actual = [fault for name, fault in rows if name == phase]
        assert len(actual) == len(expected) and set(actual) == expected, phase


def test_startup_retention_matrix_has_every_fault_class():
    _startup_retention_matrix_coverage(_STARTUP_RETENTION_FAILURES)
    with pytest.raises(AssertionError):
        _startup_retention_matrix_coverage(_STARTUP_RETENTION_FAILURES[1:])


def _startup_retirement_listener_inodes(base):
    """Socket inodes bound to the retirement address of any test root under `base`.

    The address is derived from the root path exactly as _isolation derives it,
    so a holder cannot leave this set without giving up the grant capability.
    Accepted connections carry the listener's address and are included.
    """
    import hashlib
    addresses = {'@fleet-tui-retire-' + hashlib.sha256(os.fsencode(str(root))).hexdigest()
                 for root in Path(base).glob('fleet-tui-tests-*')}
    inodes = set()
    for line in Path('/proc/net/unix').read_text().splitlines()[1:]:
        fields = line.split()
        if len(fields) == 8 and fields[7] in addresses:
            inodes.add(f'socket:[{fields[6]}]')
    return inodes


def _startup_owned_helper_states(home):
    """Independent kernel observations of this child's run-owned helpers.

    Primary predicate: the process holds a descriptor for a socket bound to a
    retirement address of a root under `home` (the run's base), whatever its
    environment, fork route or executable. Supplement: its environment still
    carries HOME=`home`. A holder that dropped every such socket can no longer
    grant; one that also replaced HOME is not observed. Zombies are excluded.
    """
    inodes = _startup_retirement_listener_inodes(home)
    rows = []
    for entry in Path('/proc').iterdir():
        if not entry.name.isdecimal() or int(entry.name) == os.getpid():
            continue
        try:
            if entry.stat().st_uid != os.getuid():
                continue
            held = False
            if inodes:
                for descriptor in (entry / 'fd').iterdir():
                    try:
                        if os.readlink(descriptor) in inodes:
                            held = True
                            break
                    except FileNotFoundError:
                        continue
            if not held:
                fields = (entry / 'environ').read_bytes().split(b'\0')
                if b'HOME=' + os.fsencode(str(home)) not in fields:
                    continue
            raw = (entry / 'stat').read_text().rsplit(')', 1)[1].split()
            if raw[0] != 'Z':
                rows.append((int(entry.name), int(raw[19])))
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue
    return rows


def _startup_stop_owned_helpers(home):
    """Pidfd-bind and recheck run ownership/start ticks before signalling an exact process."""
    import signal
    import time
    import ctypes
    libc = ctypes.CDLL(None, use_errno=True)
    def pidfd_open(pid):
        native = getattr(os, 'pidfd_open', None)
        if native is not None:
            return native(pid)
        call = libc.pidfd_open
        call.argtypes = (ctypes.c_int, ctypes.c_uint)
        call.restype = ctypes.c_int
        fd = call(pid, 0)
        if fd < 0:
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error))
        return fd
    def pidfd_kill(fd):
        native = getattr(signal, 'pidfd_send_signal', None)
        if native is not None:
            return native(fd, signal.SIGKILL)
        call = libc.pidfd_send_signal
        call.argtypes = (ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint)
        call.restype = ctypes.c_int
        if call(fd, signal.SIGKILL, None, 0) < 0:
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error))
    for pid, ticks in _startup_owned_helper_states(home):
        try:
            pidfd = pidfd_open(pid)
        except ProcessLookupError:
            continue
        try:
            if (pid, ticks) in _startup_owned_helper_states(home):
                try:
                    pidfd_kill(pidfd)
                except ProcessLookupError:
                    pass
                try:
                    os.waitpid(pid, 0)
                except ChildProcessError:
                    pass
        finally:
            os.close(pidfd)
    for _ in range(100):
        if not _startup_owned_helper_states(home):
            return
        time.sleep(0.01)
    raise AssertionError('run-owned retention helper survived cleanup')


def _startup_retention_script():
    helpers = '\n\n'.join(_startup_function_source(helper)[0] for helper in (
        _startup_retirement_listener_inodes, _startup_owned_helper_states,
        _startup_stop_owned_helpers))
    return 'import os\nfrom pathlib import Path\n' + helpers + r"""
import importlib.util, json, os, pathlib, signal, sys, tempfile, weakref
from pathlib import Path
base = pathlib.Path(sys.argv[2]); phase = sys.argv[3]; fault = sys.argv[4]
number = int(fault) if fault.isdecimal() else None
injection_fired = False
owner_pid = os.getpid()
def inject():
    global injection_fired
    injection_fired = True
    raise failure
class InitializationAbort(BaseException):
    pass
failure = (OSError(number, 'injected initialization failure') if number is not None else
           ValueError('injected initialization failure') if fault == 'value-error' else
           KeyboardInterrupt('injected initialization failure') if fault == 'keyboard-interrupt' else
           SystemExit('injected initialization failure') if fault == 'system-exit' else
           GeneratorExit('injected initialization failure') if fault == 'generator-exit' else
           InitializationAbort('injected initialization failure'))
# This instrument reads the kernel identity of our own forked descendant.
# Published module controls and the module's process-identity helper are not its oracle.
def descendant_state(pid, ticks=None):
    try:
        raw = pathlib.Path(f'/proc/{pid}/stat').read_text()
    except FileNotFoundError:
        return None
    fields = raw[raw.rfind(')') + 2:].split()
    if ticks is not None and int(fields[19]) != ticks:
        return None
    return int(fields[19]), int(fields[1]), fields[0]
original_fork = os.fork
observed_helpers = []
def observed_fork():
    pid = original_fork()
    if pid:
        ticks, parent, state = descendant_state(pid)
        assert parent == os.getpid(), 'observed helper is not our descendant'
        observed_helpers.append((pid, ticks))
        (base / 'helper-identity.json').write_text(json.dumps({'pid': pid, 'ticks': ticks}))
    return pid
os.fork = observed_fork
original_temp = tempfile.mkdtemp
created = []
def private_temp(suffix=None, prefix=None, dir=None):
    value = original_temp(suffix=suffix, prefix=prefix, dir=base)
    created.append(value)
    return value
tempfile.mkdtemp = private_temp
original_write = os.write
def injected_write(fd, data):
    if pathlib.Path(os.readlink(f'/proc/self/fd/{fd}')).name == '.owner':
        count = original_write(fd, data)
        if phase == 'owner':
            inject()
        if phase == 'short':
            global injection_fired
            injection_fired = True
            return count - 1
        return count
    return original_write(fd, data)
os.write = injected_write
original_mkdir = pathlib.Path.mkdir
def injected_mkdir(path, *args, **kwargs):
    if phase == 'payload' and path.name == 'cache' and path.parent.name.startswith('fleet-tui-tests-'):
        inject()
    return original_mkdir(path, *args, **kwargs)
pathlib.Path.mkdir = injected_mkdir
original_chmod = pathlib.Path.chmod
def injected_chmod(path, mode, *args, **kwargs):
    if path.name.startswith('fleet-tui-tests-') and ((phase == 'arm' and mode == 0o1700) or (phase \
        == 'clear' and mode == 0o700)):
        inject()
    return original_chmod(path, mode, *args, **kwargs)
pathlib.Path.chmod = injected_chmod
original_finalize = weakref.finalize
def injected_finalize(obj, callback, *args, **kwargs):
    weakref.finalize = original_finalize
    if phase == 'finalize' and callback.__name__ == '_cleanup_test_directory':
        inject()
    value = original_finalize(obj, callback, *args, **kwargs)
    weakref.finalize = injected_finalize
    return value
weakref.finalize = injected_finalize
spec = importlib.util.spec_from_file_location('retention_subject', sys.argv[1])
module = importlib.util.module_from_spec(spec)
guard_control = None
live_positive = False
startup = False
def record_guard(frame, event, value):
    global guard_control, live_positive, startup
    if os.getpid() != owner_pid or frame.f_globals is not vars(module):
        return record_guard
    if event == 'line' and type(frame.f_locals.get('child')) is int and frame.f_locals['child'] > 0:
        import linecache
        line = linecache.getline(sys.argv[1], frame.f_lineno).strip()
        fault_line = {
            'parent-listener-close': 'listener.close()',
            'parent-read-close': 'os.close(read_fd)',
            'parent-ready-close': 'os.close(ready_write)',
            'parent-ready-read-close': 'os.close(ready_read)',
            'ready-read': "ready = os.read(ready_read, 128).decode('ascii').split()",
            'ready-parse': 'child_ticks = int(ready[1])',
            'pidfd': 'pidfd = _retirement_pidfd_open(child)',
        }
        if line == fault_line.get(phase):
            sys.settrace(None)
            inject()
    if event == 'return' and type(value) is tuple and len(value) == 5:
        guard_control = value  # Used only to close returned descriptors.
        live = _startup_owned_helper_states(base)
        assert live, 'live-helper positive control did not fire'
        if phase != 'revoke':
            assert len(observed_helpers) == 1, 'helper fork observation missing'
            pid, ticks = observed_helpers[0]
            state = descendant_state(pid, ticks)
            assert state is not None and state[2] != 'Z', 'forked helper exited before control return'
        live_positive = True
        print('LIVE_HELPER_DETECTED', flush=True)
    return record_guard
sys.settrace(record_guard)
error = None
control = None
helper_after_failure = None
try:
    spec.loader.exec_module(module)
    if phase == 'revoke':
        module._retain_test_directory()
        module._TEST_DIRECTORY._finalizer.detach()
except BaseException as caught:
    error = caught
    control = vars(module).get('_RETIREMENT_CONTROL')
    # Observe BEFORE either test cleanup path can revoke or reap a leaked helper.
    helper_after_failure = [descendant_state(pid, ticks) for pid, ticks in observed_helpers]
    finalizer_alive_after_failure = module._TEST_DIRECTORY._finalizer.alive
    run_helpers_after_failure = _startup_owned_helper_states(base)
    if phase == 'revoke':
        raise AssertionError('revocation unexpectedly raised') from caught
else:
    helper_after_failure = [descendant_state(pid, ticks) for pid, ticks in observed_helpers]
    run_helpers_after_failure = _startup_owned_helper_states(base)
    finalizer_alive_after_failure = module._TEST_DIRECTORY._finalizer.alive
finally:
    sys.settrace(None)
    os.fork = original_fork
    weakref.finalize = original_finalize
    # Preserve the independent observations above. HOME is unique to this child
    # and catches native forks, renamed starts and reparented double-fork helpers.
    _startup_stop_owned_helpers(base)
    for pid, ticks in observed_helpers:
        state = descendant_state(pid, ticks)
        if state is not None:
            assert state[2] == 'Z', 'run-owned cleanup left an observed helper live'
            try:
                os.waitpid(pid, 0)
            except ChildProcessError:
                pass
    if guard_control is not None:
        for descriptor in guard_control[3:]:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError as close_error:
                    if close_error.errno != 9:
                        raise
    # Never detach without replacement run-owned cleanup, even if injection missed.
    module._TEST_DIRECTORY._finalizer.detach()
assert not run_helpers_after_failure, 'revocation left run-owned retirement helper alive'
if phase == 'revoke':
    print('DOUBLEFORK_HELPERS_BEFORE_CLEANUP=' + str(len(run_helpers_after_failure)), flush=True)
    sys.exit(0)
assert injection_fired, 'initialization injection did not fire'
assert phase == 'arm' or len(observed_helpers) == 1, 'helper fork observation missing'
if error is None:
    raise AssertionError('initialization injection did not fire')
early = phase == 'arm' or phase.startswith('parent-') or phase in ('ready-read', 'ready-parse', 'pidfd')
assert early or control is not None, 'initialization failure lost retirement control'
assert type(error) is type(failure), (type(error), type(failure))
assert number is None or error.errno == number
assert len(created) == 1
# The armed state is sampled before test cleanup detaches it.
assert not finalizer_alive_after_failure, 'failure did not detach finalizer'
tree = pathlib.Path(created[0])
assert all(state is None for state in helper_after_failure), \
    'initialization failure left independently observed retirement helper alive or unreaped'
assert early or live_positive, 'live-helper positive control missing'
if early or phase == 'finalize':
    assert not (tree / '.owner').exists(), 'arming/finalizer failure must precede owner write'
else:
    assert len((tree / '.owner').read_text().split()) == 9, 'fixture must have a valid owner'
print('DETACHED_TREE=' + created[0])
"""


@pytest.fixture(scope='module')
def _startup_detached_retention_observation(tmp_path_factory):
    import subprocess
    # These children intentionally detach after injected storage errors. Keep
    # their evidence outside pytest's numbered-base cleanup too.
    root = _startup_persistent_scratch(tmp_path_factory)
    script = _startup_retention_script()

    records = {}
    for phase, number in _STARTUP_RETENTION_FAILURES + [('short', 5)]:
        base = root / f'{phase}-{number}'
        base.mkdir()
        try:
            result = subprocess.run([sys.executable, '-B', '-c', script,
                                     str(Path(_isolation.__file__)), str(base), phase, str(number)],
                                    env={'HOME': str(base), 'PATH': '/usr/bin:/bin',
                                         'PYTHONDONTWRITEBYTECODE': '1'},
                                    capture_output=True, text=True, timeout=60)
        finally:
            # subprocess.run kills its direct child on timeout; the setsid helper
            # needs its own identity-bound revocation even then.
            _startup_stop_owned_helpers(base)
            identity = base / 'helper-identity.json'
            if identity.is_file():
                _startup_stop_recorded_helper(identity)
        records[phase, number] = result
    return records


@pytest.mark.parametrize('phase,number', _STARTUP_RETENTION_FAILURES + [('short', 5)])
def test_startup_detached_tree_survives_later_retirement(_startup_detached_retention_observation, \
    phase, number, monkeypatch, capsys):
    result = _startup_detached_retention_observation[phase, number]
    assert result.returncode == 0, result.stdout + result.stderr
    line, = [line for line in result.stdout.splitlines() if line.startswith('DETACHED_TREE=')]
    sibling = Path(line.split('=', 1)[1])
    assert sibling.is_dir(), 'detached evidence disappeared at child exit'
    # The exited child and parent share the namespace. The valid owner is dead,
    # so only the purposeful-retention protection can preserve this evidence.
    early = phase in ('arm', 'finalize', 'ready-read', 'ready-parse', 'pidfd') or phase.startswith('parent-')
    owner = None if early else (sibling / '.owner').read_text()
    if owner is not None:
        assert _isolation._process_identity(int(owner.split()[0]))[0] is None
    for _ in range(2):
        _isolation._retire_test_siblings(sibling.parent)
        assert sibling.is_dir(), 'intentionally detached evidence retired by later run'
        if owner is not None:
            assert (sibling / '.owner').read_text() == owner
        diagnostic = capsys.readouterr().err
        assert str(sibling) in diagnostic
        if phase != 'arm':
            assert 'intentionally retained' in diagnostic


@pytest.fixture(scope='module')
def _startup_doublefork_cleanup_observation(tmp_path_factory):
    import subprocess
    base = _startup_persistent_scratch(tmp_path_factory) / 'doublefork-revocation'
    base.mkdir()
    source = Path(_isolation.__file__).read_text()
    assert source.count('os.setsid()') == 1
    indent = next(line[:len(line) - len(line.lstrip())] for line in source.splitlines()
                  if line.strip() == 'os.setsid()')
    source = source.replace(indent + 'os.setsid()',
                            indent + 'os.setsid()\n' + indent + 'if os.fork():\n' +
                            indent + '    os._exit(0)')
    subject = base / 'subject.py'
    subject.write_text(source)
    try:
        return subprocess.run([sys.executable, '-B', '-c', _startup_retention_script(),
                               str(subject), str(base), 'revoke', '5'],
                              env={'HOME': str(base), 'PATH': '/usr/bin:/bin',
                                   'PYTHONDONTWRITEBYTECODE': '1'},
                              capture_output=True, text=True, timeout=60)
    finally:
        _startup_stop_owned_helpers(base)


def test_startup_doublefork_revocation_leaves_no_live_helpers(_startup_doublefork_cleanup_observation):
    result = _startup_doublefork_cleanup_observation
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'DOUBLEFORK_HELPERS_BEFORE_CLEANUP=0' in result.stdout


# A helper descendant that leaves every environment-based observation: forked
# with posix.fork (not the observed os.fork), it keeps the listener across
# execve with a replaced HOME and answers GRANT. It records its identity and
# releases the helper only after that, so the observation is ordered by the
# kernel rather than by timing.
_STARTUP_EXEC_GRANTER = r"""
import os, socket, sys
listener = socket.socket(fileno=int(sys.argv[1]))
raw = open('/proc/self/stat').read()
with open(sys.argv[3], 'w') as marker:
    marker.write(f"{os.getpid()} {raw.rsplit(')', 1)[1].split()[19]} {os.environ.get('HOME')}")
os.close(int(sys.argv[2]))
while True:
    connection, _ = listener.accept()
    with connection:
        connection.recv(8192)
        connection.sendall(b'GRANT')
"""


def _startup_stop_marked_granter(marker):
    """Kill the recorded granter incarnation, independently of helper discovery."""
    import signal
    try:
        pid, ticks, _home = marker.read_text().split()
    except (FileNotFoundError, ValueError):
        return
    try:
        pidfd = _isolation._retirement_pidfd_open(int(pid))
    except ProcessLookupError:
        return
    try:
        if _isolation._process_identity(int(pid))[0] == int(ticks):
            try:
                _isolation._retirement_pidfd_signal(pidfd, signal.SIGKILL)
            except ProcessLookupError:
                pass
    finally:
        os.close(pidfd)


@pytest.fixture(scope='module')
def _startup_exec_listener_observation(tmp_path_factory):
    import subprocess
    base = _startup_persistent_scratch(tmp_path_factory) / 'exec-listener-revocation'
    base.mkdir()
    marker = base / 'exec-granter'
    source = Path(_isolation.__file__).read_text()
    # After listen(), so the descendant holds a listening socket, as the helper does.
    assert source.count('listener.listen(1)') == 1
    indent = next(line[:len(line) - len(line.lstrip())] for line in source.splitlines()
                  if line.strip() == 'listener.listen(1)')
    mutant = ['listener.listen(1)', 'import fcntl as _fcntl, posix as _posix',
              '_sync_read, _sync_write = os.pipe()', 'os.set_inheritable(_sync_write, True)',
              'if _posix.fork() == 0:',
              '    os.close(_sync_read)',
              '    os.close(read_fd)',
              '    _fcntl.fcntl(listener.fileno(), _fcntl.F_SETFD, _fcntl.fcntl(listener.fileno(), '
              '_fcntl.F_GETFD) & ~_fcntl.FD_CLOEXEC)',
              f'    os.execve(sys.executable, [sys.executable, "-c", {_STARTUP_EXEC_GRANTER!r}, '
              f'str(listener.fileno()), str(_sync_write), {str(marker)!r}], '
              '{"HOME": "/var/empty", "PATH": "/usr/bin:/bin"})',
              'os.close(_sync_write)', 'os.read(_sync_read, 1)', 'os.close(_sync_read)']
    source = source.replace(indent + 'listener.listen(1)', '\n'.join(indent + line for line in mutant))
    subject = base / 'subject.py'
    subject.write_text(source)
    roots = []
    try:
        result = subprocess.run([sys.executable, '-B', '-c', _startup_retention_script(),
                                 str(subject), str(base), 'revoke', '5'],
                                env={'HOME': str(base), 'PATH': '/usr/bin:/bin',
                                     'PYTHONDONTWRITEBYTECODE': '1'},
                                capture_output=True, text=True, timeout=60)
        roots = sorted(base.glob('fleet-tui-tests-*'))
        # Observed by the parent after the child's own cleanup, before ours.
        holders = sorted(_startup_retirement_listener_inodes(base))
    finally:
        _startup_stop_owned_helpers(base)
        _startup_stop_marked_granter(marker)
    return result, marker, roots, holders


def test_startup_exec_listener_holder_is_a_run_owned_helper(_startup_exec_listener_observation):
    import socket
    result, marker, roots, holders = _startup_exec_listener_observation
    # CONTROL: the descendant ran with a replaced HOME while holding the listener.
    pid, _ticks, home = marker.read_text().split()
    assert home == '/var/empty' and int(pid) > 0, marker.read_text()
    assert len(roots) == 1, roots
    # The run's own cleanup must leave no holder: the address is unbound.
    assert holders == [], ('run left a live retirement listener', holders)
    # The double-fork control must see the surviving listener holder and fail.
    assert result.returncode != 0, result.stdout + result.stderr
    assert 'revocation left run-owned retirement helper alive' in result.stderr, result.stderr
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
        with pytest.raises(ConnectionRefusedError):
            probe.connect(_isolation._retirement_address(roots[0]))


@pytest.mark.parametrize('answerer', ['listener-caller', 'listener-sharer'])
def test_startup_grant_must_come_from_the_recorded_incarnation(tmp_path, answerer):
    """SO_PEERCRED names the listen() caller, whichever holder of the listener answers.

    The recorded incarnation calls listen() and stays live. In the sharer row it
    never accepts; another process holding the same listener answers GRANT.
    """
    import secrets
    import signal
    import socket
    from .conftest import _permit_process
    root = tmp_path / 'fleet-tui-tests-grant'
    root.mkdir()
    cookie = secrets.token_hex(16)
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(_isolation._retirement_address(root))
    hold_read, hold_write = os.pipe()
    ready_read, ready_write = os.pipe()

    def answer():
        listener.settimeout(20)  # Bound every wait, so no answerer outlives the test.
        connection, _ = listener.accept()
        with connection:
            connection.recv(8192)
            connection.sendall(b'GRANT')
            connection.recv(1)

    children = []
    try:
        with _permit_process('os.fork'):
            child = os.fork()
        if child == 0:
            try:
                os.close(hold_write)
                os.close(ready_read)
                listener.listen(1)
                os.close(ready_write)
                if answerer == 'listener-caller':
                    answer()
                else:
                    os.read(hold_read, 1)  # Live and silent until the test ends.
            finally:
                os._exit(0)
        children.append(child)
        os.close(ready_write)
        assert os.read(ready_read, 1) == b''  # listen() has been called.
        if answerer == 'listener-sharer':
            with _permit_process('os.fork'):
                sharer = os.fork()
            if sharer == 0:
                try:
                    answer()
                finally:
                    os._exit(0)
            children.append(sharer)
        listener.close()
        ticks, _boot = _isolation._process_identity(child)
        fields = ['0'] * 5 + [str(child), str(ticks), cookie, 'v3']
        if answerer == 'listener-caller':
            _isolation._request_retirement(root, fields)
        else:
            with pytest.raises(ValueError, match='not sent by the recorded helper'):
                _isolation._request_retirement(root, fields)
    finally:
        listener.close()
        for descriptor in (hold_read, hold_write, ready_read):
            os.close(descriptor)
        for pid in children:
            try:
                os.kill(pid, signal.SIGKILL)  # Unreaped direct children: no PID reuse.
            except ProcessLookupError:
                pass
            os.waitpid(pid, 0)


# The phases whose injection precedes the parent's readiness read. There the
# parent closes its readiness end and sends the cancellation byte while the
# helper may not yet have announced readiness. A helper that then meets the
# closed readiness pipe exits on its error path without consuming R, and the
# owner's endpoint reports that unread byte as ECONNRESET before its EOF.
_STARTUP_UNCONSUMED_CANCELLATION_PHASES = (
    'parent-listener-close', 'parent-read-close', 'parent-ready-close', 'ready-read')
# The parent's failure path is the same for every injected class, so two
# classes suffice here: an OSError, which a channel OSError could be mistaken
# for, and the class first observed replaced under load.
_STARTUP_UNCONSUMED_CANCELLATION_ROWS = [
    (phase, fault) for phase in _STARTUP_UNCONSUMED_CANCELLATION_PHASES
    for fault in (5, 'generator-exit')
]


@pytest.fixture(scope='module')
def _startup_unconsumed_cancellation_observation(tmp_path_factory):
    """Order the slow-helper interleaving with the kernel, not with sleeps.

    The subject copy holds the helper's readiness write until the owner's
    cancellation byte is pending on the helper's endpoint. The parent has
    already closed its readiness end by then, so the unmodified write that
    follows fails with EPIPE and the helper exits with R unread. The helper
    records the pending byte count first, as the positive control.
    """
    import subprocess
    root = _startup_persistent_scratch(tmp_path_factory) / 'unconsumed-cancellation'
    root.mkdir()
    source = Path(_isolation.__file__).read_text()
    anchor = 'if os.write(ready_write, ready) != len(ready):'
    assert source.count(anchor) == 1
    indent = next(line[:len(line) - len(line.lstrip())] for line in source.splitlines()
                  if line.strip() == anchor)
    script = _startup_retention_script()
    records = {}
    for phase, number in _STARTUP_UNCONSUMED_CANCELLATION_ROWS:
        base = root / f'{phase}-{number}'
        base.mkdir()
        marker = base / 'cancellation-pending'
        hold = (indent + 'select.select([read_fd], [], [], None)\n' +
                indent + 'import fcntl, termios\n' +
                indent + f'with open({str(marker)!r}, "w") as _pending:\n' +
                indent + "    _pending.write(str(struct.unpack('i', fcntl.ioctl(read_fd, termios.FIONREAD, "
                         "b'\\0' * 4))[0]))\n")
        subject = base / 'subject.py'
        subject.write_text(source.replace(indent + anchor, hold + indent + anchor))
        try:
            result = subprocess.run([sys.executable, '-B', '-c', script,
                                     str(subject), str(base), phase, str(number)],
                                    env={'HOME': str(base), 'PATH': '/usr/bin:/bin',
                                         'PYTHONDONTWRITEBYTECODE': '1'},
                                    capture_output=True, text=True, timeout=60)
        finally:
            _startup_stop_owned_helpers(base)
            identity = base / 'helper-identity.json'
            if identity.is_file():
                _startup_stop_recorded_helper(identity)
        records[phase, number] = result, marker
    return records


@pytest.mark.parametrize('phase,number', _STARTUP_UNCONSUMED_CANCELLATION_ROWS)
def test_startup_unconsumed_cancellation_is_channel_closure(
        _startup_unconsumed_cancellation_observation, phase, number, capsys):
    result, marker = _startup_unconsumed_cancellation_observation[phase, number]
    # CONTROL: the helper reached its readiness write with R still unread.
    assert marker.read_text() == '1', 'helper did not hold readiness until R was pending'
    # The injected fault, not the peer's reset, must reach the caller; the
    # retention script also requires the helper exited and was reaped.
    assert result.returncode == 0, result.stdout + result.stderr
    line, = [line for line in result.stdout.splitlines() if line.startswith('DETACHED_TREE=')]
    sibling = Path(line.split('=', 1)[1])
    assert sibling.is_dir() and not (sibling / '.owner').exists()
    _isolation._retire_test_siblings(sibling.parent)
    assert sibling.is_dir(), 'retained initialization evidence retired by a later run'
    assert 'intentionally retained' in capsys.readouterr().err


@pytest.mark.parametrize('phase', ['identity', 'grant'])
@pytest.mark.parametrize('replacement', ['record', 'owner-file', 'directory'])
def test_startup_sibling_owner_rechecked_before_removal(
        tmp_path, monkeypatch, capsys, replacement, phase):
    root = tmp_path / 'siblings'
    root.mkdir()
    namespace = Path('/proc/self/ns/pid').stat()
    boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    sibling = root / 'fleet-tui-tests-replaced-owner'
    sibling.mkdir()
    owner = sibling / '.owner'
    raw = _startup_guard_owner(sibling, f'99999999 1 {boot} {namespace.st_dev} {namespace.st_ino}\n')
    owner.write_text(raw)
    (sibling / 'payload').write_text('original')
    observations = []
    def replace_owner():
        observations.append(phase)
        if replacement == 'record':
            # Deliberately leave inode unchanged; a content change invalidates
            # the previously read decision just as a replacement does.
            owner.write_text(raw.replace(' 1 ', ' 2 ', 1))
        elif replacement == 'owner-file':
            owner.rename(sibling / '.owner-before')
            owner.write_text(raw)
        else:
            sibling.rename(root / 'previous-directory')
            sibling.mkdir()
            (sibling / '.owner').write_text(raw)
        (sibling / 'payload').write_text('replacement must survive')
    def replace_during_identity_lookup(pid, proc_root='/proc'):
        assert pid == 99999999
        replace_owner()
        return None, boot
    original_request = _isolation._request_retirement
    def replace_after_grant(sibling, fields):
        original_request(sibling, fields)
        replace_owner()
    if phase == 'identity':
        monkeypatch.setattr(_isolation, '_process_identity', replace_during_identity_lookup)
    else:
        monkeypatch.setattr(_isolation, '_request_retirement', replace_after_grant)
    _isolation._retire_test_siblings(root)
    assert observations == [phase], 'replacement seam was not reached once'
    assert sibling.is_dir(), 'changed owner removed using stale decision'
    assert (sibling / 'payload').read_text() == 'replacement must survive'
    diagnostic = capsys.readouterr().err
    assert str(sibling) in diagnostic and 'changed before removal' in diagnostic


@pytest.fixture(scope='module')
def _startup_owner_order_observation(tmp_path_factory):
    import subprocess
    root = tmp_path_factory.mktemp('owner-before-payload')
    script = r"""
import importlib.util, pathlib, stat, sys
original = pathlib.Path.mkdir
observed = []
def checked_mkdir(path, *args, **kwargs):
    if path.parent.name.startswith('fleet-tui-tests-'):
        owner = path.parent / '.owner'
        assert owner.is_file(), 'isolation payload created before owner record'
        assert stat.S_IMODE(owner.stat().st_mode) == 0o600
        assert len(owner.read_text().split()) == 9, 'owner record incomplete before payload'
        if not observed:
            assert list(path.parent.iterdir()) == [owner], 'owner was not the first payload'
        observed.append(path.name)
    return original(path, *args, **kwargs)
pathlib.Path.mkdir = checked_mkdir
spec = importlib.util.spec_from_file_location('owner_order_subject', sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
assert observed == ['home', 'config', 'cache', 'data', 'state', 'runtime', 'config-dirs', \
    'data-dirs'], observed
print('OWNER_BEFORE_ALL_EIGHT_PAYLOADS')
"""
    return subprocess.run([sys.executable, '-B', '-c', script, str(Path(_isolation.__file__))],
                          env={'HOME': str(root), 'PATH': '/usr/bin:/bin',
                               'PYTHONDONTWRITEBYTECODE': '1'},
                          capture_output=True, text=True, timeout=60)


def test_startup_owner_record_precedes_payload(_startup_owner_order_observation):
    result = _startup_owner_order_observation
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == 'OWNER_BEFORE_ALL_EIGHT_PAYLOADS'


def test_startup_sibling_malformed_numbers_are_retained(tmp_path):
    root = tmp_path / 'siblings'
    root.mkdir()
    boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    namespace = Path('/proc/self/ns/pid').stat()
    # Each numeric field is checked independently. A lenient int() reader
    # would accept these forms and retire the absent PID's directory.
    canonical = ['99999999', '1', boot, str(namespace.st_dev), str(namespace.st_ino)]
    for index in (0, 1, 3, 4, 5, 6):
        sibling_value = canonical[index] if index < 5 else '1'
        value = sibling_value
        malformed = ('-' + value, '+' + value, '0_' + value, '0',
                     value.translate(str.maketrans('0123456789', '٠١٢٣٤٥٦٧٨٩')))
        for number, value in enumerate(malformed):
            sibling = root / f'fleet-tui-tests-malformed-{index}-{number}'
            sibling.mkdir()
            fields = _startup_guard_owner(sibling, ' '.join(canonical) + '\n').split()
            fields[index] = value
            (sibling / '.owner').write_text(' '.join(fields) + '\n')
    before = {p.name for p in root.iterdir()}
    _isolation._retire_test_siblings(root)
    assert {p.name for p in root.iterdir()} == before, 'malformed numeric owner removed'


@pytest.fixture(scope='module')
def _startup_retirement_import_observation(tmp_path_factory):
    import subprocess
    root = tmp_path_factory.mktemp('retirement-import')
    # Redirect only the sweeper's /tmp glob; do not touch any shared /tmp
    # sibling. The source module's actual import-time call must do the work.
    script = r"""
import importlib.util, os, pathlib, sys
root = pathlib.Path(sys.argv[2])
namespace = pathlib.Path('/proc/self/ns/pid').stat()
boot = pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip()
dead = root / 'fleet-tui-tests-import-dead'
dead.mkdir()
first_spec = importlib.util.spec_from_file_location('guard_bootstrap', sys.argv[1])
first_module = importlib.util.module_from_spec(first_spec)
first_spec.loader.exec_module(first_module)
control = first_module._start_retirement_guard(dead, 99999999, 1)
(dead / '.owner').write_text(f'99999999 1 {boot} {namespace.st_dev} {namespace.st_ino} {control[0]} \
    {control[1]} {control[2]} v3\n')
original = pathlib.Path.glob
def redirected(self, pattern):
    if self == pathlib.Path('/tmp') and pattern == 'fleet-tui-tests-*':
        return original(root, pattern)
    return original(self, pattern)
pathlib.Path.glob = redirected
spec = importlib.util.spec_from_file_location('retirement_subject', sys.argv[1])
module = importlib.util.module_from_spec(spec)
try:
    spec.loader.exec_module(module)
    assert not dead.exists(), 'import-time retirement did not remove dead owner'
    print('IMPORT_RETIREMENT_OBSERVED')
finally:
    try:
        os.write(control[3], b'R')
    except BrokenPipeError:
        pass
    os.close(control[3])
    os.close(control[4])
    os.waitpid(control[0], 0)
"""
    result = subprocess.run([sys.executable, '-B', '-c', script,
                             str(Path(_isolation.__file__)), str(root)],
                            env={'HOME': str(root), 'PATH': '/usr/bin:/bin',
                                 'PYTHONDONTWRITEBYTECODE': '1'},
                            capture_output=True, text=True, timeout=60)
    return result


def test_startup_import_retires_dead_sibling(_startup_retirement_import_observation):
    result = _startup_retirement_import_observation
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == 'IMPORT_RETIREMENT_OBSERVED'


def test_startup_object_field_census_detects_missing_literal(monkeypatch):
    excluded = {'open', 'ctypes.dlopen', 'ctypes.dlsym', 'ctypes.call_function'}
    measured = {event for table in _STARTUP_PROCESS_AUDIT_CENSUS.values()
                for events in table.values() for event in events} - excluded
    assert measured <= set(_STARTUP_REFUSED_PROCESS_EVENTS), ( \
        'recorded process event missing from refusal contract', sorted(measured - set( \
        _STARTUP_REFUSED_PROCESS_EVENTS)))
    with pytest.raises(AssertionError, match='recorded process event missing'):
        assert measured <= set(_STARTUP_REFUSED_PROCESS_EVENTS) - {'os.exec'}, \
            'recorded process event missing'
    version = tuple(sys.version_info[:2])
    literal = _STARTUP_FIELD_LITERALS[version]['function']
    monkeypatch.setitem(_STARTUP_FIELD_LITERALS[version], 'function', literal - {'__builtins__'})
    report = _startup_object_fields(_isolation._dispatch_fixture, 'function', _isolation)
    with pytest.raises(AssertionError, match='object field set differs'):
        _assert_startup_field_sets(report, 'function', 'negative control')


@pytest.fixture(scope='module')
def _startup_probe_coverage_control(tmp_path_factory):
    global _STARTUP_PROBE_CASES
    original = _STARTUP_PROBE_CASES
    _STARTUP_PROBE_CASES = tuple(p for p in original
                               if p != ('_license_candidate_files', 'absolute-nul'))
    try:
        return _startup_run_probes(_isolation, tmp_path_factory.mktemp('probe-coverage'))
    finally:
        _STARTUP_PROBE_CASES = original


def test_startup_probe_coverage_detects_removed_probe(_startup_probe_coverage_control):
    with pytest.raises(AssertionError, match='probe return coverage missing'):
        _assert_startup_probe_coverage(_startup_probe_coverage_control)


# Process-event census from the available CPython interpreters. Native 3.13
# was not available for this instrument measurement. Refuse the union below.
_STARTUP_PROCESS_AUDIT_CENSUS = {(3, 11): {'fork': ('os.fork',), 'forkpty': ('os.forkpty',), \
    'pty.fork': ('os.forkpty',), 'exec': ('os.exec',), 'system': ('os.system',), 'posix_spawn': ( \
    'os.posix_spawn',), 'posix_spawnp': ('os.posix_spawn',), 'spawnv': ('os.fork', 'os.exec'), \
    'popen': ('open', 'subprocess.Popen'), 'pty.spawn': ('pty.spawn', 'os.forkpty', 'os.exec'), \
    'Popen': ('subprocess.Popen',), 'fork_exec': (), 'ctypes.fork': ('ctypes.dlopen', 'ctypes.dlsym' \
    )}, (3, 12): {'fork': ('os.fork',), 'forkpty': ('os.forkpty',), 'pty.fork': ('os.forkpty',), \
    'exec': ('os.exec',), 'system': ('os.system',), 'posix_spawn': ('os.posix_spawn',), \
    'posix_spawnp': ('os.posix_spawn',), 'spawnv': ('os.fork', 'os.exec'), 'popen': ('open', \
    'subprocess.Popen'), 'pty.spawn': ('pty.spawn', 'os.forkpty', 'os.exec'), 'Popen': ( \
    'subprocess.Popen',), 'fork_exec': (), 'ctypes.fork': ('ctypes.dlopen', 'ctypes.dlsym')}, (3, 14 \
    ): {'fork': ('os.fork',), 'forkpty': ('os.forkpty',), 'pty.fork': ('os.forkpty',), 'exec': ( \
    'os.exec',), 'system': ('os.system',), 'posix_spawn': ('os.posix_spawn',), 'posix_spawnp': ( \
    'os.posix_spawn',), 'spawnv': ('os.fork', 'os.exec'), 'popen': ('open', 'subprocess.Popen', \
    'os.posix_spawn'), 'pty.spawn': ('pty.spawn', 'os.forkpty', 'os.exec'), 'Popen': ( \
    'subprocess.Popen', 'os.posix_spawn'), 'fork_exec': ('_posixsubprocess.fork_exec',), \
    'ctypes.fork': ('ctypes.dlopen', 'ctypes.dlsym', 'ctypes.call_function')}}

_STARTUP_REFUSED_PROCESS_EVENTS = (
    'subprocess.Popen', 'os.fork', 'os.posix_spawn', 'os.system', 'pty.spawn',
    'os.forkpty', 'os.exec', '_posixsubprocess.fork_exec',
)


def _startup_native_environment():
    """Read Linux libc's live raw environ vector, independently of os.environ.

    Reacquire the pointer on every observation. Reject duplicate names before
    conversion to a mapping; decode like Python's filesystem environment.
    No subprocess or interpreter startup (and hence no second locale coercion).
    """
    import ctypes
    import sys
    if not sys.platform.startswith('linux'):
        raise RuntimeError('native environment instrument requires Linux libc environ')
    address = ctypes.c_void_p.in_dll(ctypes.CDLL(None), 'environ').value
    vector = ctypes.cast(address, ctypes.POINTER(ctypes.c_char_p))
    pairs, seen = [], set()
    index = 0
    while vector[index] is not None:
        entry = vector[index].decode(sys.getfilesystemencoding(), 'surrogateescape')
        name, separator, value = entry.partition('=')
        if not separator or name in seen:
            raise RuntimeError('native environment malformed or duplicate name: ' + name)
        seen.add(name)
        pairs.append((name, value))
        index += 1
    return _startup_environment_digests(dict(pairs))


def _startup_tree_listing(root):
    """Canonical root plus descendants from lstat; never traverse symlinks."""
    import os
    import stat
    from pathlib import Path
    root = Path(root)
    result = []
    def visit(path, relative):
        try:
            info = path.lstat()
        except FileNotFoundError:
            if relative == '.':
                result.append({'path': '.', 'type': 'absent'})
                return
            raise
        kind = ('symlink' if stat.S_ISLNK(info.st_mode) else
                'directory' if stat.S_ISDIR(info.st_mode) else
                'file' if stat.S_ISREG(info.st_mode) else 'other')
        entry = {'path': relative, 'type': kind, 'mode': stat.S_IMODE(info.st_mode)}
        if kind == 'symlink':
            entry['target'] = os.readlink(path)
        result.append(entry)
        if kind == 'directory':
            for name in sorted(os.listdir(path)):
                visit(path / name, name if relative == '.' else relative + '/' + name)
    visit(root, '.')
    return result


_STARTUP_EXPECTED_TREE = [
    {'path': '.', 'type': 'directory', 'mode': 0o700},
    {'path': '.owner', 'type': 'file', 'mode': 0o600},
    *({'path': name, 'type': 'directory', 'mode': 0o700} for name in
      ('cache', 'config', 'config-dirs', 'data', 'data-dirs', 'home', 'runtime', 'state')),
]
_STARTUP_EXPECTED_PROBE_TREE = [{'path': '.', 'type': 'absent'}]


def _startup_location_expected(stdlib, os_file, resolution_error=None):
    """Independent source gate and filesystem-traversal ordered candidate oracle."""
    if stdlib is None or (type(stdlib) is str and stdlib == ''):
        if os_file is None:
            return (), ()
        source, label = os_file, 'os.__file__'
        fallback = True
    else:
        source, label = stdlib, 'sys._stdlib_dir'
        fallback = False
    if type(source) is not str or not source.startswith('/'):
        return (), (label + ' = <uninspectable exemption source>',)
    # POSIX dirname preserves the selected directory's link spelling; resolving
    # the directory before its '..' differs from taking its lexical parent.
    here = (source.rpartition('/')[0] or '/') if fallback else source
    if resolution_error is not None:
        return (), (label + ' = <uninspectable exemption source>',)
    try:
        candidates = [Path(directory + '/' + name).resolve()
                      for directory in (here + '/..', here)
                      for name in ('LICENSE.txt', 'LICENSE')]
    except (ValueError, OSError, RuntimeError):
        return (), (label + ' = <uninspectable exemption source>',)
    result = []
    for candidate in candidates:
        if candidate not in result:
            result.append(candidate)
    return tuple(result), ()


def _startup_make_location_inputs(base, spec):
    """Plant source types and link topology, retaining evidence independently."""
    branch, operation, kind = spec
    selected = base / 'selected' / 'stdlib'
    selected.mkdir(parents=True)
    links = []
    if operation == 'path':
        if kind.startswith('directory-link-'):
            target = (base / 'elsewhere' / 'target' if kind.endswith('other-parent')
                      else selected.parent / 'sibling')
            target.mkdir(parents=True)
            selected.rmdir()
            selected.symlink_to(target, target_is_directory=True)
            links.append((str(selected), str(target)))
        elif kind == 'parent-link':
            target = base / 'parent-target'
            target.mkdir()
            (target / 'stdlib').mkdir()
            selected.rmdir(); selected.parent.rmdir()
            selected.parent.symlink_to(target, target_is_directory=True)
            links.append((str(selected.parent), str(target)))
        elif kind == 'dangling':
            selected.rmdir()
            selected.symlink_to(base / 'missing-target', target_is_directory=True)
            links.append((str(selected), str(base / 'missing-target')))
        elif kind == 'missing':
            selected.rmdir()
        elif kind == 'loop':
            selected.rmdir()
            selected.symlink_to(selected, target_is_directory=True)
            links.append((str(selected), str(selected)))
        elif kind == 'alias-collapse':
            (selected / 'LICENSE.txt').touch()
            (selected / 'LICENSE').symlink_to('LICENSE.txt')
            links.append((str(selected / 'LICENSE'), 'LICENSE.txt'))
        elif kind == 'root-collapse':
            selected = Path('/')
        elif kind == 'nul':
            selected = Path(str(selected) + '\x00location')
    value = str(selected) if branch == 'stdlib' else str(selected / 'os.py')
    if operation == 'source':
        value = {'none': None, 'empty': '', 'empty-subclass': '', 'bytes-empty': b'',
                 'zero': 0, 'absolute': value, 'relative': 'relative/os.py',
                 'subclass': value, 'nonstring': 7}[kind]
    stdlib, os_file = (value, str(base / 'fallback/os.py')) if branch == 'stdlib' else (
        None if branch == 'fallback-none' else '', value)
    return (stdlib, os_file), {'links': links, 'injected_error': 'OSError' if kind == 'oserror' else None}


def _startup_location_properties(stdlib, os_file, links=(), resolution_error=None):
    """Credit measured types, branch, spelling, topology and candidate multiplicity."""
    import stat
    def describe(value):
        kind = type(value)
        return {'type': ['controlled', 'StartupLocationString'] if kind.__bases__ == (str,) else [ \
            kind.__module__, kind.__qualname__],
                'exact_str': kind is str, 'none': value is None,
                'value': _startup_namespace_value(value) if any(kind is canonical for canonical in ( \
                    type(None), str, bytes, int))
                         else {'str_subclass': kind.__bases__ == (str,), 'text': str.__str__(value)}}
    fallback = stdlib is None or (type(stdlib) is str and stdlib == '')
    source = os_file if fallback else stdlib
    branch = ('fallback-none' if stdlib is None else 'fallback-empty') if fallback else 'stdlib'
    accepted = type(source) is str and source.startswith('/')
    outcome = 'absent' if fallback and source is None else 'accepted' if accepted else 'refused'
    source_kind = ('none' if source is None else 'empty' if type(source) is str and source == '' else
                   'empty-subclass' if type(source) is not str and isinstance(source, str) and str. \
                       __str__(source) == '' else
                   'subclass' if type(source) is not str and isinstance(source, str) else
                   'bytes-empty' if type(source) is bytes and source == b'' else
                   'zero' if type(source) is int and source == 0 else
                   'nonstring' if type(source) is not str else
                   'absolute' if source.startswith('/') else 'relative')
    # A stdlib None/exact-empty row reaches fallback; retain both original
    # storage descriptions so the selector itself is not inferred from a label.
    info = {'stdlib': describe(stdlib), 'os_file': describe(os_file),
            'branch': branch, 'source_kind': source_kind, 'outcome': outcome,
            'links': [], 'raw_candidates': [], 'candidates': [], 'exception': resolution_error}
    for spelling, target in links:
        path = Path(spelling)
        assert stat.S_ISLNK(path.lstat().st_mode), ('location link plant missing', spelling)
        assert os.readlink(path) == target, ('location link target differs', spelling, target)
        info['links'].append({'spelling': spelling, 'target': os.readlink(path)})
    if accepted:
        here = (source.rpartition('/')[0] or '/') if fallback else source
        info['here'] = here
        try:
            path = Path(here); mode = path.lstat().st_mode
            info['filesystem'] = 'symlink' if stat.S_ISLNK(mode) else 'directory' if stat.S_ISDIR( \
                mode) else 'other'
        except (FileNotFoundError, ValueError):
            info['filesystem'] = 'missing'
        if resolution_error is None:
            try:
                info['raw_candidates'] = [str(Path(directory + '/' + name).resolve())
                    for directory in (here + '/..', here) for name in ('LICENSE.txt', 'LICENSE')]
            except (ValueError, OSError, RuntimeError) as exc:
                info['exception'] = type(exc).__name__
        if info['exception'] is not None:
            info['outcome'] = 'resolution-error'
        else:
            info['candidates'] = list(dict.fromkeys(info['raw_candidates']))
            info['multiplicity'] = len(info['raw_candidates']) - len(info['candidates'])
            info['resolved_directory'] = str(Path(here).resolve())
    return info


def _startup_location_operation(properties):
    """Derive an operation from observable facts, never from a case name."""
    if properties['exception'] is not None:
        return 'resolution:' + properties['exception']
    if properties.get('multiplicity', 0):
        return 'candidates:' + ('alias-collapse' if properties['links'] else 'root-collapse')
    if properties['outcome'] != 'accepted':
        return 'source:' + properties['source_kind']
    here = properties['here']
    if properties['filesystem'] == 'symlink':
        link = next(item for item in properties['links'] if item['spelling'] == here)
        target = Path(link['target'])
        if not target.exists():
            return 'filesystem:dangling'
        return 'filesystem:directory-link-' + ('same-parent' if target.parent == Path(here).parent \
            else 'other-parent')
    if any(item['spelling'] == str(Path(here).parent) for item in properties['links']):
        return 'filesystem:parent-link'
    return 'filesystem:' + ('missing' if properties['filesystem'] == 'missing' else 'plain')


def _startup_structural_inputs(env, cwd):
    """Independent POSIX input properties and ordered per-key root oracle.

    Resolve against the actual child cwd; do not call the subject's functions.
    Missing and dangling paths resolve non-strictly, and are distinguished by
    lstat plus target existence. No HOME directory contents are read.
    """
    import os
    import pwd
    import stat
    def path_info(value):
        path = Path(value) if value.startswith('/') else cwd / value
        try:
            info = path.lstat()
            kind = 'symlink' if stat.S_ISLNK(info.st_mode) else 'directory' if stat.S_ISDIR(info. \
                st_mode) else 'other'
        except (FileNotFoundError, OSError):
            kind = 'missing'
        try:
            exists = path.exists()
        except OSError:
            exists = False
        return {'spelling': 'absolute' if value.startswith('/') else 'relative',
                'kind': kind, 'target_exists': exists, 'resolved': str(path.resolve())}
    if 'HOME' not in env:
        home = {'input': 'unset', 'resolved': str(Path(pwd.getpwuid(os.getuid()).pw_dir).resolve())}
    elif env['HOME'] == '':
        # POSIX expanduser('~') with HOME='' returns '/', not cwd.
        home = {'input': 'empty', 'resolved': '/'}
    else:
        home = dict(path_info(env['HOME']), input=env['HOME'])
    roots = [home['resolved']]
    keys = {}
    for key in _STARTUP_XDG_KEYS:
        entries = []
        for value in env.get(key, '').split(':'):
            outcome = 'empty' if value == '' else 'absolute' if value.startswith('/') else 'relative'
            entry = {'input': value, 'outcome': outcome}
            if outcome == 'absolute':
                entry.update(path_info(value))
                entry['duplicate'] = entry['resolved'] in roots
                if not entry['duplicate']:
                    roots.append(entry['resolved'])
                entry['index'] = roots.index(entry['resolved'])
            entries.append(entry)
        keys[key] = {'present': key in env, 'entries': entries}
    return roots, {'home': home, 'xdg': keys}


def _startup_persistent_scratch(tmp_path_factory):
    """Unnumbered sibling of pytest basetemp; only this fixture cleans it.

    Failed/aborted evidence survives pytest numbered-directory rotation and
    the isolation sweeper, whose fleet-tui-tests-* namespace is separate.
    """
    import tempfile
    return Path(tempfile.mkdtemp(prefix='fleet-tui-startup-',
                                 dir=tmp_path_factory.getbasetemp().parent))


@pytest.fixture(scope="module")
def _startup_observation(tmp_path_factory, pytestconfig):
    """Observe real configured startup before the function-scoped process gate.

    One launcher eagerly runs all input scenarios, generated preloads, and a second
    rich activation control without --noconftest. The observer is outside the
    tests package, so candidate conftests are not on its discovery path even
    without that flag. These observations do not attest interpreter provenance.
    """
    import json
    import pwd
    import secrets
    import subprocess
    import textwrap
    import time
    import shutil

    setup_started = time.monotonic()
    keep = pytestconfig.getoption('--keep-startup-artifacts')
    requested_root = pytestconfig.getoption('--startup-artifacts-root')
    if requested_root and not keep:
        raise ValueError('--startup-artifacts-root requires --keep-startup-artifacts')
    helper = getattr(sys, '_fleet_startup_artifact_helper', None)
    if keep and not requested_root and not helper:
        raise ValueError('--keep-startup-artifacts requires --startup-artifacts-root when ' \
            'fleet-data-path is unavailable')
    state = {'failed': False}
    import threading
    abort = threading.Event()
    def stop_storage(error):
        abort.set()
        state['failed'] = True
        # Preserve the HOME evidence too, without any filesystem write.
        _isolation._TEST_DIRECTORY._finalizer.detach()
        _isolation._retain_test_directory()
        session = pytestconfig.pluginmanager.get_plugin('session')
        if session is not None:
            session.shouldstop = 'startup storage error: ' + str(error)

    def check_storage():
        if abort.is_set():
            raise RuntimeError('startup writes stopped after storage error')
    try:
        if keep and helper:
            run = Path(requested_root).name if requested_root else 'startup-' + secrets.token_hex(8)
            helper_result = subprocess.run([helper, 'test-scratch-kept', run],
                                           capture_output=True, text=True, timeout=30)
            diagnostic = helper_result.stdout + helper_result.stderr
            for number in (5, 28, 30):
                if f'[Errno {number}]' in diagnostic:
                    raise OSError(number, 'kept-artifact helper storage error: ' + diagnostic)
            if any(marker in diagnostic for marker in (
                    'No space left on device', 'Input/output error', 'Read-only file system',
                    'short write', 'hash mismatch')):
                raise OSError(5, 'kept-artifact helper storage error: ' + diagnostic)
            helper_result.check_returncode()
            located = helper_result.stdout.strip()
            if not Path(located).is_absolute() or '\n' in located:
                raise ValueError('fleet-data-path returned no single absolute directory')
            if requested_root and Path(requested_root).resolve() != Path(located).resolve():
                raise ValueError('requested kept root differs from fleet-data-path')
            requested_root = located
        if keep and requested_root:
            root = Path(requested_root).resolve()
            if root.is_relative_to(Path(__file__).resolve().parents[2]):
                raise ValueError('kept startup artifacts cannot be written to the repository')
            scratch = root / 'startup0'
            scratch.mkdir(parents=True, exist_ok=False)
        else:
            scratch = _startup_persistent_scratch(tmp_path_factory)
    except OSError as error:
        if error.errno in (5, 28, 30):
            stop_storage(error)
            pytest.exit('startup storage error: ' + str(error), returncode=3)
        raise
    class StartupFailures:
        @pytest.hookimpl(hookwrapper=True)
        def pytest_runtest_makereport(self, item, call):
            report = (yield).get_result()
            if report.failed and item.module is sys.modules[__name__]:
                state['failed'] = True
                error = call.excinfo.value if call.excinfo is not None else None
                if isinstance(error, OSError) and error.errno in (5, 28, 30):
                    stop_storage(error)
    plugin = StartupFailures()
    pytestconfig.pluginmanager.register(plugin)
    try:
        config = Path(__file__).resolve().parents[1] / 'pyproject.toml'
        cwd = Path.cwd()
        observer = scratch / 'test_startup_observer.py'
        observer_token = secrets.token_hex(16)
        observer_contracts = ('_STARTUP_FIELD_COMPARATORS = ' + repr(_STARTUP_FIELD_COMPARATORS) + '\n' +
                              '_STARTUP_PROBE_CASES = ' + repr(_STARTUP_PROBE_CASES) + '\n' +
                              '_STARTUP_REFUSED_PROCESS_EVENTS = ' + repr( \
                                  _STARTUP_REFUSED_PROCESS_EVENTS) + '\n')
        observer_helpers = _startup_observer_helpers()
        observer.write_text(textwrap.dedent('''\
            import hashlib
            import json
            import os
            from functools import lru_cache
            from pathlib import Path
            import stat
            import sys

            # Snapshot after configured plugin import, before pytest adds its
            # per-test PYTEST_CURRENT_TEST bookkeeping. Compare every key/value.
            def digests(environment):
                return _startup_environment_digests(environment)

            def test_observe(pytestconfig):
                module = sys.modules.get('tests._isolation')
                root = getattr(module, '_TEST_ROOT', None)
                def describe(path):
                    if path is None:
                        return None
                    return {'exists': path.exists(), 'directory': path.is_dir(),
                            'resolved': str(path.resolve()),
                            'mode': stat.S_IMODE(path.stat().st_mode) if path.exists() else None}
                old_umask = os.umask(0o000)
                os.umask(old_umask)
                report = {
                    'schema': 2, 'token': os.environ['OBSERVER_TOKEN'],
                    'registered': pytestconfig.pluginmanager.hasplugin('tests._isolation'),
                    'loaded': module is not None,
                    'file': getattr(module, '__file__', None),
                    'roots': [str(p) for p in getattr(module, '_ORIGINAL_USER_ROOTS', ())],
                    'root': str(root) if root is not None else None,
                    'import_env': digests(getattr(module, '_IMPORT_ENV', {})),
                    'env': observed_environment, 'post_env': post_environment,
                    'native_env': observed_native, 'post_native_env': post_native,
                    'early_native': early_native, 'probe_root': str(probe_root),
                    'trees': pre_trees, 'post_trees': post_trees,
                    'post_namespace': post_namespace, 'probes': probe_observation,
                    'probe_spawn_events': spawn_events, 'umask': old_umask,
                    'owner_file_valid': (root / '.owner').read_text() ==
                        f'{os.getpid()} {module._process_identity("self")[0]} {module. \
                            _process_identity("self")[1]} '
                        f'{Path("/proc/self/ns/pid").stat().st_dev} {Path("/proc/self/ns/pid").stat( \
                            ).st_ino} '
                        f'{module._RETIREMENT_CONTROL[0]} {module._RETIREMENT_CONTROL[1]} {module. \
                            _RETIREMENT_CONTROL[2]} v3\\n',
                    'namespace': pre_namespace,
                    'namespace_exact_names': pre_namespace_exact_names,
                    'post_namespace_exact_names': post_namespace_exact_names,
                    'namespace_module_exact': pre_namespace_module_exact,
                    'post_namespace_module_exact': post_namespace_module_exact,
                    'namespace_dict_exact': pre_namespace_dict_exact,
                    'post_namespace_dict_exact': post_namespace_dict_exact,
                    'env_exact_type': pre_env_exact_type, 'post_env_exact_type': post_env_exact_type,
                    'root_stat': describe(root),
                    'directories': {name: describe(root / name) if root is not None else None
                                    for name in ('home', 'config', 'cache', 'data', 'state',
                                                 'runtime', 'config-dirs', 'data-dirs')},
                }
                print('STARTUP_REPORT=' + json.dumps(report, sort_keys=True))
            ''').replace("os.environ['OBSERVER_TOKEN']", repr(observer_token)) + '\n' +
            observer_contracts + observer_helpers + textwrap.dedent(
                "\n        module = sys.modules.get('tests._isolation')\n        de" \
                'f namespace():\n            module_exact = type(module) is __impo' \
                "rt__('types').ModuleType\n            original = vars(module) if " \
                'module_exact else {}\n            dict_exact = module_exact and t' \
                'ype(original) is dict\n            exact_names = dict_exact and a' \
                'll(type(name) is str for name in original)\n            # Check o' \
                'riginal keys before lookup, comparison or JSON conversion.\n     ' \
                '       return ({name: _startup_namespace_binding(name, value, mo' \
                'dule)\n                     for name, value in original.items()} ' \
                'if exact_names else {}), exact_names, module_exact, dict_exact\n ' \
                "       early_native = getattr(sys.modules.get('tests._startup_na" \
                "tive'), 'REPORT', None)\n        probe_root = Path(early_native['" \
                "probe_root']) if early_native else Path(__file__).parent / 'miss" \
                "ing-early-report'\n        pre_namespace, pre_namespace_exact_nam" \
                'es, pre_namespace_module_exact, pre_namespace_dict_exact = names' \
                'pace()\n        pre_env_exact_type = type(os.environ) is os._Envi' \
                'ron\n        observed_environment = digests(os.environ)\n        o' \
                'bserved_native = _startup_native_environment()\n        def trees' \
                "():\n            return {'isolation': _startup_tree_listing(modul" \
                "e._TEST_ROOT),\n                    'probes': _startup_tree_listi" \
                'ng(probe_root)}\n        pre_trees = trees() if module is not Non' \
                'e else {}\n        spawn_events = []\n        def audit(event, arg' \
                's):\n            if event in _STARTUP_REFUSED_PROCESS_EVENTS:\n   ' \
                '             spawn_events.append(event)\n                raise Ru' \
                "ntimeError('startup probe attempted process creation or replacem" \
                "ent: ' + event)\n        sys.addaudithook(audit)\n        probe_ob" \
                'servation = (_startup_run_probes(module, probe_root)\n           ' \
                '                  if module is not None else {})\n        post_na' \
                'mespace, post_namespace_exact_names, post_namespace_module_exact' \
                ', post_namespace_dict_exact = namespace()\n        post_env_exact' \
                '_type = type(os.environ) is os._Environ\n        post_environment' \
                ' = digests(os.environ)\n        post_native = _startup_native_env' \
                'ironment()\n        post_trees = trees() if module is not None el' \
                'se {}\n'))
        parent_probe_observation = _startup_run_probes(_isolation, scratch / 'parent-probe-root')
        assert parent_probe_observation['results'] == _startup_probe_expected(), ( \
            'parent probe results differ', parent_probe_observation)
        records = {}
        modes = [(case, True, None) for case in _STARTUP_CASES]
        modes += [(case, True, None) for case in _STARTUP_STRUCTURAL_CASES]
        modes += [(case, True, None) for case in _STARTUP_LOCATION_CASES]
        modes += [(case, True, 'fleet_tui.paths') for case in _STARTUP_ALPHABET_REFUSALS]
        modes += [('rich-without-noconftest', False, None),
                  ('preload-package', True, 'fleet_tui')]
        modes += [('preload-' + name, True, name) for name in (
            *_STARTUP_MODULE_NAMES, *_STARTUP_FUTURE_NAMES, *_STARTUP_NEAR_NAMES)]
        original_cwd = cwd
        def observe(index, mode):
            check_storage()
            try:
                return observe_unchecked(index, mode)
            except OSError as error:
                if error.errno in (5, 28, 30):
                    stop_storage(error)
                raise

        def observe_unchecked(index, mode):
            case, noconftest, preload_name = mode
            cwd = original_cwd
            base = scratch / (str(index) + "-scenario")
            base.mkdir()
            # Generated module names may exceed NAME_MAX or contain separators.
            # The independently named root is bounded and bound to this exact case.
            probe_root = base / ('probe-' + str(index) + '-' + hashlib.sha256(
                case.encode('utf-8', 'surrogateescape')).hexdigest()[:16])
            assert not probe_root.exists() and not probe_root.is_symlink(), (case, \
                'probe root initially present')
            real_home = base / 'real-home'
            real_home.mkdir()
            home = base / 'home-link'
            home.symlink_to(real_home, target_is_directory=True)
            assert home.is_symlink() and str(home) != str(real_home), (case, \
                'HOME symlink plant missing or indistinct')
            inherited_temp = real_home / 'incoming-temp'
            inherited_temp.mkdir()
            env = {
                'HOME': str(home), 'PATH': str(base / 'bin'), 'SHELL': 'poison-shell',
                'TMPDIR': str(inherited_temp), 'TEMP': str(inherited_temp),
                'TMP': str(inherited_temp), 'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1',
                'PYTHONDONTWRITEBYTECODE': '1', 'OBSERVER_TOKEN': observer_token,
            }
            preserved = {key: 'keep-' + key for key in (
                'NOT_FLEET_KEEP', 'FLEETISH_KEEP', 'AFLEET_KEEP', 'fleet_lower_KEEP',
                'OBSERVER_NEUTRAL', 'home', 'path', 'shell',
                'SHELLOPTS', 'BASHOPTS', 'PROMPT_COMMAND', 'BASH_FUNC_x%%',
            )}
            preserved.update({key.lower(): 'keep-' + key for key in _STARTUP_SHELL_KEYS})
            preserved.update({key + '_EXTRA': 'keep-' + key for key in (
                *_STARTUP_REDIRECTS, *_STARTUP_SHELL_KEYS)})
            preserved.update({
                'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8', 'LC_CTYPE': 'C.UTF-8',
                'TZ': 'America/Los_Angeles', 'TERM': 'xterm-256color',
                'COLORTERM': 'truecolor', 'PAGER': 'less -R', 'EDITOR': 'vim',
                'VISUAL': 'code --wait', 'MULTILINE_VALUE': 'first\nsecond\nthird',
                'PYTEST_VERSION': pytest.__version__,
            })
            preserved.update({name + 'KEEP': 'boundary-' + name
                              for name in _prefix_neighbours('FLEET_')
                              if not (name + 'KEEP').startswith('FLEET_')})
            env.update(preserved)
            env.update({'FLEET_' + suffix: 'generated-' + str(index)
                        for index, suffix in enumerate(_fleet_suffixes())})
            env.update({key: 'poison-' + key for key in _STARTUP_SHELL_KEYS})
            env.update({key: str(base / key) for key in _STARTUP_FLEET_KEYS})
            env.update({('FLEET_' + prefix + 'SENTINEL'): str(base / prefix)
                        for prefix in ('', 'TUI_', 'RESEARCH_', 'CODEX_', 'PC_',
                                       'LINK_', 'OTHER_', 'X_')})
            env.update({
                'FLEET_': '', 'FLEET_EMPTY': '', 'FLEET_RELATIVE': 'relative/tgt',
                'FLEET_TILDE': '~/tgt', 'FLEET_JSON': '[]',
                'FLEET_TUI_SERVE_PORT': '8011', 'FLEET_TUI_STALE_SECS': '45',
                'FLEET_TUI_SERVICES': '["demo.service"]',
                'FLEET_TUI_SERVE_HOST': '127.0.0.1', 'FLEET_LINK_IP': '192.0.2.1',
                'FLEET_PC_IP': '192.0.2.1', 'FLEET_TUI_CLOUD_MARKERS': 'demo,probe',
                        })
            expected_roots = [str(real_home)]
            unique = {}
            shared = base / 'shared'
            shared.mkdir()
            for key in _STARTUP_XDG_KEYS:
                first, second = base / (key + '-a'), base / (key + '-b')
                first.mkdir()
                second.mkdir()
                alias = base / (key + '-link')
                alias.symlink_to(second, target_is_directory=True)
                dotted = str(first / '..' / first.name)
                env[key] = os.pathsep.join((dotted, str(alias), str(first), '',
                                            'relative-' + key, str(shared)))
                assert str(alias) != str(second) and env[key] != str(first), (case, key, \
                    'root alias/compound plant indistinct')
                assert alias.resolve() == second and Path(dotted).resolve() == first, (case, key, \
                    'root plant resolution differs')
                # Absolute roots are independent of existence and keep interior spaces.
                missing = base / (key + '-missing')
                dangling_target = base / (key + '-dangling-target')
                dangling = base / (key + '-dangling-link')
                dangling.symlink_to(dangling_target, target_is_directory=True)
                assert not missing.exists() and not dangling.exists() and dangling.is_symlink(), ( \
                    case, key, 'missing/dangling plant invalid')
                assert dangling.resolve() == dangling_target, (case, key, \
                    'dangling plant resolves differently')
                scheme_target = base / (key + '-scheme-target')
                # POSIX pathsep splits the scheme from the following absolute entry.
                env[key] += os.pathsep + os.pathsep.join((str(missing), str(dangling),
                                                         'file://' + str(scheme_target)))
                unique[key] = [str(first), str(second), str(missing),
                               str(dangling_target), str(scheme_target)]
                if case in ('rich', 'rich-without-noconftest'):
                    # Separate ordinals give every character/length a distinct
                    # target per key. Missing targets avoid filename-size limits.
                    content_root = scratch / 'values' / str(_STARTUP_XDG_KEYS.index(key))
                    targets = [str(content_root / str(i) / fragment / 'tail')
                               for i, fragment in enumerate(_root_content_fragments())]
                    env[key] += os.pathsep + os.pathsep.join(targets)
                    unique[key].extend(targets)
                    # ':' splits a shell-special-bearing absolute spelling into
                    # one known absolute entry and a relative non-root tail.
                    split_root = str(content_root / 'colon-left')
                    env[key] += os.pathsep + split_root + ':colon-right/tail'
                    unique[key].append(split_root)
                    slash_target = str(content_root / 'slash' / 'interior' / 'tail')
                    env[key] += os.pathsep + slash_target
                    unique[key].append(slash_target)
                    assert len(env[key].encode()) < 131072, (case, key, \
                        'root corpus exceeds execve value limit', len(env[key].encode()))
                expected_roots.extend(unique[key][:2])
                if key == _STARTUP_XDG_KEYS[0]:
                    expected_roots.append(str(shared))
                expected_roots.extend(unique[key][2:])
            if case.startswith('xdg-'):
                expected_roots = [str(real_home)]
                for key in _STARTUP_XDG_KEYS:
                    env.pop(key)
                    if case == 'xdg-lower':
                        preserved[key.lower()] = str(base / (key + '-lower'))
                        env[key.lower()] = preserved[key.lower()]
                    elif case == 'xdg-spelling':
                        env[key] = os.pathsep.join((
                            *(char + str(base / ('leading-' + key))
                              for char in _environment_characters() if char not in '/:'),
                            *('$' + name + '/' + key for name in _STARTUP_REDIRECTS),
                            *(' + name + /' + key for name in _STARTUP_REDIRECTS),
                        ))
                    elif case != 'xdg-unset':
                        env[key] = {'xdg-empty': '', 'xdg-relative': 'relative/' + key,
                                    'xdg-tilde': '~/' + key, 'xdg-nonpath': '8011'}[case]
            if case.startswith('home-'):
                if case == 'home-unset':
                    env.pop('HOME')
                    # Read only the passwd field, never the real home contents.
                    expected_roots[0] = str(Path(pwd.getpwuid(os.getuid()).pw_dir).resolve())
                else:
                    value = {'home-empty': '', 'home-relative': 'relative-home',
                             'home-nonpath': '8011', 'home-missing': str(base / 'missing-home'),
                             'home-dangling': str(base / 'dangling-home'),
                             'home-space': str(base / 'home with space'),
                             'home-scheme': 'file:///absolute-home'}[case]
                    if case == 'home-dangling':
                        Path(value).symlink_to(base / 'missing-home-target', target_is_directory=True)
                        assert Path(value).is_symlink() and not Path(value).exists(), (case, \
                            'HOME dangling plant invalid')
                    elif case == 'home-space':
                        Path(value).mkdir()
                    env['HOME'] = value
                    expected_roots[0] = ('/' if value == '' else
                                         str(base / 'missing-home-target') if case == 'home-dangling'
                                         else str(cwd / value))
            if case.startswith('reset-'):
                for key in ('PATH', 'SHELL', *_STARTUP_SHELL_KEYS):
                    if case == 'reset-unset':
                        env.pop(key)
                    else:
                        env[key] = ''
            rich_role_digests = (_startup_environment_digests(env)
                                 if case in _STARTUP_NEUTRAL_ALPHABET else {})
            if case == 'environment-minimal':
                # No environment substrate: absolute executable/config/observer
                # paths suffice. The token lives in the observer, not the env.
                env = {}
                preserved = {}
                expected_roots = [str(Path(pwd.getpwuid(os.getuid()).pw_dir).resolve())]
            elif case in _STARTUP_NEUTRAL_ALPHABET:
                # Keep the earlier explicit neutral partition. Transport names
                # (TMPDIR/TEMP/TMP and loader/report controls) keep realistic
                # values here; the uniform cases still vary every input name.
                preserved = dict.fromkeys(preserved, _STARTUP_NEUTRAL_ALPHABET[case])
                env.update(preserved)
            elif case in _STARTUP_VALUE_ALPHABET or case == _STARTUP_TARGET_CASE:
                shape = _STARTUP_VALUE_ALPHABET.get(case, _STARTUP_VALUE_EMPTY)
                # Every rich role carries the same class, including generated
                # FLEET_ names, shell-init, redirects, resets and neutral names.
                env = dict.fromkeys(env, shape)
                preserved = dict.fromkeys(preserved, shape)
                expected_roots = ['/' if shape == '' else str((cwd / shape).resolve())]
                if shape.startswith('/'):
                    expected_roots = [str(Path(shape).resolve())]
            location_inputs = None
            location_plant = None
            location_properties = None
            if case in _STARTUP_STRUCTURAL_CASES or case in _STARTUP_ALPHABET_REFUSALS or case in \
                _STARTUP_LOCATION_CASES:
                alphabet, home_kind, cohort = (_STARTUP_STRUCTURAL_CASES[case]
                    if case in _STARTUP_STRUCTURAL_CASES else
                    (_STARTUP_LOCATION_CASES[case][0] if case in _STARTUP_LOCATION_CASES else
                     _STARTUP_ALPHABET_REFUSALS[case], None, None))
                shape = _STARTUP_VALUE_ALPHABET[alphabet]
                rich_inputs = dict(env)
                env = dict.fromkeys(env, shape)
                preserved = dict.fromkeys(preserved, shape)
                cwd = base  # A HOME alphabet symlink is created only in scratch.
                if home_kind is not None and case not in _STARTUP_HOME_XDG_CASES:
                    for number, key in enumerate(_STARTUP_XDG_KEYS):
                        if (cohort is None or (home_kind == 'xdg-self' and key != cohort) or
                                (home_kind != 'xdg-self' and number % 2 == cohort)):
                            env[key] = rich_inputs[key]
                    if home_kind == 'xdg-self':
                        env['HOME'] = rich_inputs['HOME']
                    elif cohort is not None:
                        if home_kind == 'unset':
                            env.pop('HOME')
                        elif home_kind == 'empty':
                            env['HOME'] = ''
                        else:
                            spelling, kind = home_kind.split('-')
                            home_path = base / 'structural-home'
                            if kind == 'directory':
                                home_path.mkdir()
                            elif kind in ('symlink', 'dangling'):
                                home_path.symlink_to(real_home if kind == 'symlink' else
                                                     base / 'structural-missing-target',
                                                     target_is_directory=True)
                            env['HOME'] = str(home_path) if spelling == 'absolute' else home_path.name
                    else:
                        home_path = base / shape
                        if home_kind == 'directory':
                            home_path.mkdir()
                        elif home_kind in ('symlink', 'dangling'):
                            home_path.symlink_to(real_home if home_kind == 'symlink' else
                                                 base / 'self-missing-target', target_is_directory=True)
                if case in _STARTUP_HOME_XDG_CASES:
                    _, variant, cohort = _STARTUP_HOME_XDG_CASES[case]
                    env['HOME'] = str(real_home)
                    for number, key in enumerate(_STARTUP_XDG_KEYS):
                        if number % 2 == cohort:
                            if variant == 'distinct':
                                (base / (key + '-distinct')).mkdir()
                            env[key] = (str(real_home) if variant == 'identical' else
                                        str(home) + '/.' if variant == 'alias' else str(base / (key \
                                            + '-distinct')))
                if case in _STARTUP_LOCATION_CASES:
                    kind = _STARTUP_LOCATION_CASES[case][1]
                    location_inputs, location_plant = _startup_make_location_inputs(base, \
                        _STARTUP_LOCATION_SPECS[kind])
                    values = list(location_inputs)
                    if 'subclass' in kind:
                        slot = 0 if kind.startswith('stdlib-') else 1
                        values[slot] = type('StartupLocationString', (str,), {})(values[slot])
                    location_properties = _startup_location_properties(*values, links=location_plant \
                        ['links'], resolution_error=location_plant['injected_error'])
                expected_roots, structural_inputs = _startup_structural_inputs(env, cwd)
            else:
                _, structural_inputs = _startup_structural_inputs(env, cwd)
            interpreter_flags = ['-B'] + (['-' + 'O' * sys.flags.optimize] if sys.flags.optimize else [])
            assertion_mode = pytestconfig.getoption('assertmode')
            pytest_arguments = ['--assert=' + assertion_mode, '-c', str(config),
                    '-p', 'tests._startup_native', '-p', 'no:cacheprovider', '-q', '-s', '--color=no',
                    '--startup-probe-root=' + str(probe_root),
                    '--basetemp=' + str(base / 'child-temp'), str(observer)]
            if noconftest:
                pytest_arguments.append('--noconftest')
            argv = [sys.executable, *interpreter_flags, '-m', 'pytest', *pytest_arguments]
            if case.startswith('environment-'):
                # Python coerces LC_CTYPE and pytest writes PYTEST_VERSION before
                # addopts plugins load. Seed the exact input during early plugin
                # registration, before the actual configured subject is imported.
                # No subject code is copied, executed, or repaired by this harness.
                input_file = base / 'input.json'
                # Every field is read by os.environ.update in the child.
                check_storage()
                input_text = json.dumps(env, separators=(',', ':'), ensure_ascii=False)
                if input_file.write_text(input_text) != len(input_text):
                    raise OSError(5, 'short startup input write', str(input_file))
                target_bootstrap = ''
                if case == _STARTUP_TARGET_CASE:
                    # Establish targets before import, then TRANSFER the real owner
                    # at the exact constructor call. globals().pop leaves no global,
                    # closure or container reference: the subject is the only owner
                    # after hand-off, so dropping its owner removes the directory.
                    target_bootstrap = (
                        'import tempfile\n'
                        '_original_directory = tempfile.TemporaryDirectory\n'
                        "_target_owner = _original_directory(prefix='fleet-tui-tests-', dir='/tmp')\n"
                        'def target_directory(*args, **kwargs):\n'
                        "    if args == () and kwargs == {'prefix': 'fleet-tui-tests-', 'dir': '/tmp'}:\n"
                        '        tempfile.TemporaryDirectory = _original_directory\n'
                        "        print('STARTUP_TARGET_OWNER_REPORT=' + json.dumps({'used': True, "
                        "'root': _target_owner.name}), flush=True)\n"
                        "        return globals().pop('_target_owner')\n"
                        '    return _original_directory(*args, **kwargs)\n'
                        'tempfile.TemporaryDirectory = target_directory\n'
                    )
                location_seed = (f'        sys._stdlib_dir, os.__file__ = {location_inputs!r}\n'
                                 if location_inputs is not None else '')
                if location_inputs is not None and 'subclass' in _STARTUP_LOCATION_CASES[case][1]:
                    slot = 'sys._stdlib_dir' if _STARTUP_LOCATION_CASES[case][1].startswith( \
                        'stdlib-') else 'os.__file__'
                    location_seed += (
                        "        sys._startup_location_string_type = "
                        "type('StartupLocationString', (str,), {})\n"
                        f'        {slot} = sys._startup_location_string_type({slot})\n')
                resolver_seed = ''
                if location_plant and location_plant['injected_error']:
                    resolver_seed = (
                        '        import pathlib\n'
                        '        _saved_resolve = pathlib.Path.resolve\n'
                        f'        _fault_prefix = {location_properties["here"]!r}\n'
                        '        sys._startup_resolution_events = []\n'
                        '        def resolver_fault(path, *args, **kwargs):\n'
                        '            if str(path).startswith(_fault_prefix) and path.name' \
                            " in ('LICENSE', 'LICENSE.txt'):\n"
                        "                sys._startup_resolution_events.append({'exceptio" \
                            "n': 'OSError', 'path': str(path)})\n"
                        "                raise OSError('targeted startup license resolver fault')\n"
                        '            return _saved_resolve(path, *args, **kwargs)\n'
                        '        pathlib.Path.resolve = resolver_fault\n'
                        '        sys._startup_restore_resolver = lambda: setattr(pathlib.' \
                            'Path, "resolve", _saved_resolve)\n')
                target_input = ''
                if case == _STARTUP_TARGET_CASE:
                    target_input = (
                        f'        redirects = {_STARTUP_REDIRECTS!r}\n'
                        '        os.environ.update({key: os.path.join(_target_owner.name, value) '
                        'for key, value in redirects.items()})\n'
                        "        os.environ.update(PATH=os.defpath, SHELL='/bin/bash')\n"
                    )
                bootstrap = (
                    'import hashlib, json, os, sys, pytest\n' +
                    'def digests(environment):\n'
                    "    return {key: {'length': len(value), 'sha256': hashlib.sha256("
                    "value.encode('utf-8', 'surrogateescape')).hexdigest()} "
                    'for key, value in environment.items()}\n' + target_bootstrap +
                    'class EnvironmentInput:\n'
                    '    def pytest_addoption(self, parser):\n'
                    "        if 'tests._isolation' in sys.modules:\n"
                    f"            raise RuntimeError(({case!r}, 'subject loaded before input seed'))\n"
                    '        os.environ.clear()\n'
                    f'        with open({str(input_file)!r}) as incoming:\n'
                    '            os.environ.update(json.load(incoming))\n' + target_input + \
                        location_seed + resolver_seed +
                    (f"        sys.modules[{preload_name!r}] = "
                     f"__import__('types').ModuleType({preload_name!r})\n"
                     if case in _STARTUP_ALPHABET_REFUSALS else '') +
                    "        print('STARTUP_INPUT_REPORT=' + json.dumps({"
                    f"'token': {observer_token!r}, 'env': digests(os.environ), "
                    "'isolation_loaded': 'tests._isolation' in sys.modules}), flush=True)\n"
                    "sys.argv[0] = 'pytest'\n"
                    'raise SystemExit(pytest.main(sys.argv[1:], plugins=[EnvironmentInput()]))\n'
                )
                argv = [sys.executable, *interpreter_flags, '-c', bootstrap, *pytest_arguments,
                        '--disable-plugin-autoload']
            if preload_name is not None and case not in _STARTUP_ALPHABET_REFUSALS:
                name = preload_name
                # Inject an inert module before pytest starts, without PYTHONPATH.
                bootstrap = (
                    'import json, os, runpy, sys, types\n'
                    f'sys.modules[{name!r}] = types.ModuleType({name!r})\n'
                    "print('PRELOAD_REPORT=' + json.dumps({'token': os.environ['OBSERVER_TOKEN'], "
                    f"'planted_name': {name!r}, 'present': {name!r} in sys.modules, "
                    "'package_present': 'fleet_tui' in sys.modules, "
                    "'names': sorted(n for n in sys.modules if n == 'fleet_tui' or "
                    "n.startswith('fleet_tui.')), 'isolation_loaded': "
                    "'tests._isolation' in sys.modules}), flush=True)\n"
                    "sys.argv[0] = 'pytest'\nrunpy.run_module('pytest', run_name='__main__')\n")
                argv = [sys.executable, *interpreter_flags, '-c', bootstrap, *pytest_arguments]
            assert argv[0] == sys.executable, (case, 'child interpreter differs from parent', argv[0])
            prefix_end = 1 + len(interpreter_flags)
            assert argv[1:prefix_end] == interpreter_flags, (case, 'final interpreter flags differ', argv)
            assert argv[prefix_end:prefix_end + 2] in (['-m', 'pytest'], ['-c', bootstrap] if case. \
                startswith('environment-') or preload_name is not None else []), (case, \
                'final child entry differs', argv)
            assert argv[prefix_end + 2:prefix_end + 3] == ['--assert=' + assertion_mode], (case, \
                'final assertion mode differs', argv)
            child_started = time.monotonic()
            child_umask = 0o000  # Every startup child exposes requested mode bits.
            # Axis input is loaded from JSON before configured plugin import;
            # neither execve size limits nor locale coercion may erase a class.
            launch_env = {} if case.startswith('environment-') else env
            check_storage()
            child = subprocess.run(argv, cwd=cwd, env=launch_env, umask=child_umask,
                                   capture_output=True, text=True, timeout=60)
            check_storage()
            for number in (5, 28, 30):
                if f'[Errno {number}]' in child.stdout or f'[Errno {number}]' in child.stderr:
                    raise OSError(number, 'startup child storage error: ' + child.stderr, str(base))
            if any(marker in child.stdout or marker in child.stderr for marker in (
                    'No space left on device', 'Input/output error', 'Read-only file system',
                    'short write', 'hash mismatch')):
                raise OSError(5, 'startup child storage error: ' + child.stdout + child.stderr, str(base))
            def decoded_object(values):
                # Share only decoded exact strings, preserving every typed observation.
                return {sys.intern(key): sys.intern(value) if type(value) is str else value
                        for key, value in values.items()}
            def reports(prefix):
                return [json.loads(line[len(prefix):], object_hook=decoded_object)
                        for line in child.stdout.splitlines() if line.startswith(prefix)]
            target_reports = reports('STARTUP_TARGET_OWNER_REPORT=')
            if case == _STARTUP_TARGET_CASE:
                assert len(target_reports) == 1, (
                    case, 'exact constructor hand-off report missing or duplicated',
                    'configured tests._isolation addopts may be absent', target_reports,
                    child.returncode, child.stdout, child.stderr)
                assert target_reports[0].get('used') is True, (case, \
                    'target owner was not handed off', target_reports)
                target_root = Path(target_reports[0]['root'])
                assert target_root.parent == Path('/tmp'), (case, 'target parent differs', target_root)
                assert target_root.name.startswith('fleet-tui-tests-'), (case, \
                    'target prefix differs', target_root)
                env.update({key: str(target_root / value)
                            for key, value in _STARTUP_REDIRECTS.items()})
                env.update(PATH=os.defpath, SHELL='/bin/bash')
                expected_roots = list(dict.fromkeys(env[key] for key in _STARTUP_REDIRECTS))
            record = {
                'probe_root': str(probe_root),
                'structural_inputs': structural_inputs, 'preload_name': preload_name,
                'location_inputs': location_inputs, 'location_properties': location_properties,
                'location_kind': _STARTUP_LOCATION_CASES.get(case, (None, None))[1],
                'parent_probes': parent_probe_observation,
                'case': case, 'argv': argv, 'cwd': str(cwd), 'incoming': env,
                'launch_env': launch_env, 'umask': child_umask,
                'target_owner': target_reports, 'rich_role_digests': rich_role_digests,
                'token': observer_token, 'inputs': reports('STARTUP_INPUT_REPORT='),
                'rc': child.returncode, 'stdout': child.stdout, 'stderr': child.stderr,
                'elapsed_seconds': time.monotonic() - child_started,
                'reports': reports('STARTUP_REPORT='), 'preloads': reports('PRELOAD_REPORT='),
                'expected_roots': expected_roots, 'unique': unique, 'preserved': preserved,
                'home': str(real_home), 'home_link': str(home), 'defpath': os.defpath,
                'plugin_file': str(config.parent / 'tests' / '_isolation.py'),
            }
            # Only incoming/launch_env/preserved are wholly environment digests.
            # Raw path fields include home_link, defpath, roots, namespace snapshots,
            # loader/file/cache/code paths and finalizer args. Tokens remain raw:
            # STARTUP_REPORT and STARTUP_INPUT_REPORT bake in the parent's token;
            # PRELOAD_REPORT reads the child's OBSERVER_TOKEN environment value.
            # These are examples, not an exhaustive no-echo guarantee; input JSON
            # retains raw seeded inputs; target input.json has PATH='', receipt
            # defpath holds os.defpath and incoming.PATH holds its value digest.
            receipt = dict(record)
            if location_inputs is not None:
                receipt['location_inputs'] = [_startup_namespace_value(value) for value in location_inputs]
            for field in ('incoming', 'launch_env', 'preserved'):
                receipt[field] = _startup_environment_digests(record[field])
            receipt_text = json.dumps(receipt, separators=(',', ':'), sort_keys=True)
            check_storage()
            if (base / 'receipt.json').write_text(receipt_text) != len(receipt_text):
                raise OSError(5, 'short startup receipt write', str(base / 'receipt.json'))
            record['receipt_sha256'] = hashlib.sha256(receipt_text.encode()).hexdigest()
            # The receipt retains the exact captured stdout string. Its parsed report lines
            # remain in reports/inputs/preloads/target_owner; keep terminal text once
            # in memory so the summary assertion cannot fire on a JSON string value.
            report_prefixes = ('STARTUP_REPORT=', 'STARTUP_INPUT_REPORT=',
                               'PRELOAD_REPORT=', 'STARTUP_TARGET_OWNER_REPORT=')
            record['stdout'] = '\n'.join(line for line in child.stdout.splitlines()
                                        if not line.startswith(report_prefixes))
            record['stdout_receipt'] = str(base / 'receipt.json')
            record['stdout_sha256'] = hashlib.sha256(child.stdout.encode('utf-8', 'surrogateescape') \
                ).hexdigest()
            # Validate this scenario before deleting its heavyweight payload. A
            # failed semantic check retains its inputs; existing tests still report
            # the original failure at their own nodes rather than a fixture error.
            scenario_failed = False
            try:
                _startup_validate_scenario(dict(records, **{case: record}), case)
            except AssertionError:
                scenario_failed = True
                state['failed'] = True
            record['artifacts_kept'] = keep or scenario_failed
            check_storage()
            if not record['artifacts_kept']:
                input_file = base / 'input.json'
                if input_file.exists():
                    check_storage()
                    input_file.unlink()
                child_temp = base / 'child-temp'
                if child_temp.exists():
                    check_storage()
                    shutil.rmtree(child_temp)
                # Digests remain in memory; evidence consumers opt into retention.
                check_storage()
                (base / 'receipt.json').unlink()
            return record
        # Scenarios run serially so a storage error cannot leave a second
        # observer writing or retiring payloads after the stop signal.
        # Rich is the independent name-set reference for alphabet validators.
        assert modes[0][0] == 'rich'
        first = observe(0, modes[0])
        records[first['case']] = first
        for index, mode in enumerate(modes[1:], 1):
            record = observe(index, mode)
            records[record['case']] = record
        check_storage()
        inventory_text = json.dumps({
            'fleet_tui_file': fleet_tui.__file__,
            'discovered': _STARTUP_MODULE_NAMES, 'future': _STARTUP_FUTURE_NAMES,
            'near_names': _STARTUP_NEAR_NAMES, 'cases': list(records),
            'child_count': len(records), 'elapsed_seconds': time.monotonic() - setup_started,
            'child_seconds': sum(record['elapsed_seconds'] for record in records.values()),
        }, indent=2, sort_keys=True)
        if (scratch / 'inventory.json').write_text(inventory_text) != len(inventory_text):
            raise OSError(5, 'short startup inventory write', str(scratch / 'inventory.json'))
        yield records


    except BaseException as error:
        state['failed'] = True
        if isinstance(error, OSError) and error.errno in (5, 28, 30):
            stop_storage(error)
        raise
    finally:
        pytestconfig.pluginmanager.unregister(plugin)
        if not keep and not state['failed'] and not abort.is_set():
            try:
                shutil.rmtree(scratch)
            except OSError as error:
                if error.errno in (5, 28, 30):
                    stop_storage(error)
                raise

def _startup_validate_scenario(records, case):
    """Check the row's full semantic predicates before payload retirement."""
    record = records[case]
    if case in _STARTUP_ALPHABET_REFUSALS:
        _startup_structural_outcomes(record)
        return
    name = record['preload_name']
    if name is not None:
        if name == 'fleet_tui' or name.startswith('fleet_tui.'):
            _assert_preimport_refusal(record, name)
        else:
            _assert_preload_marker(record, name, matching=False)
            _assert_preload_observer(record)
        return
    _startup_report(records, case)
    if case in _STARTUP_CASES:
        test_startup_input_axes(records, case)
    if case in (*_STARTUP_STRUCTURAL_CASES, *_STARTUP_LOCATION_CASES, *_STARTUP_VALUE_ALPHABET):
        _startup_structural_outcomes(record)


def _startup_report(records, case='rich'):
    record = records[case]
    assert record['rc'] == 0, (case, 'observer child exit differs', record)
    assert re.search(r'\b1 passed(?:, \d+ warnings?)? in ', record['stdout']), (case, \
        'one passing observer summary missing', record)
    assert len(record['reports']) == 1, (case, 'observer report missing or duplicated', record)
    report = record['reports'][0]
    assert (report['schema'], report['token']) == (2, record['token']), (case, \
        'observer schema/token differs', report)
    _assert_startup_namespace(record, report)
    assert report['probe_spawn_events'] == [], (case, 'probe spawned process', report['probe_spawn_events'])
    assert report['probes']['results'] == _startup_probe_expected(), (case, \
        'literal probe results differ', report['probes']['results'])
    assert report['probes']['results'] == record['parent_probes']['results'], (case, \
        'parent probe results differ')
    _assert_startup_namespace(dict(record, case=case + ':post-probe'), dict(report, namespace=report \
        ['post_namespace'], namespace_exact_names=report['post_namespace_exact_names'], \
        namespace_module_exact=report['post_namespace_module_exact'], namespace_dict_exact=report[ \
        'post_namespace_dict_exact']))
    assert report.get('env_exact_type') is True and report.get('post_env_exact_type') is True, (case \
        , 'environment mapping type missing or false')
    assert report['post_env'] == report['env'], (case, 'post-probe environment differs', report[ \
        'post_env'], report['env'])
    if case.startswith('environment-'):
        assert record['inputs'] == [{'token': record['token'],
                                     'env': _startup_environment_digests(record['incoming']),
                                     'isolation_loaded': False}], (case, \
                                         'seeded input digests or import ordering differs', record)
    expected = dict(record['incoming'])
    for key in tuple(expected):
        if key.startswith('FLEET_') or key in _STARTUP_SHELL_KEYS:
            del expected[key]
    expected.update({key: str(Path(report['root']) / subdir)
                     for key, subdir in _STARTUP_REDIRECTS.items()})
    expected.update(PATH=record['defpath'], SHELL='/bin/bash')
    assert report['post_native_env'] == report['native_env'], (case, \
        'post-probe native environment differs', report['post_native_env'], report['native_env'])
    assert report['probe_root'] == record['probe_root'], (case, 'scenario probe root differs')
    assert not Path(record['probe_root']).exists() and not Path(record['probe_root']).is_symlink(), \
        (case, 'scenario probe root persists')
    for phase, native_key, mapping_key, tree_key in (
        ('pre-probe', 'native_env', 'env', 'trees'),
        ('post-probe', 'post_native_env', 'post_env', 'post_trees'),
    ):
        assert report[tree_key] == {'isolation': _STARTUP_EXPECTED_TREE,
                                    'probes': _STARTUP_EXPECTED_PROBE_TREE}, (
            case, phase, 'listed filesystem trees differ', report[tree_key])
    assert report.get('owner_file_valid') is True, (case, 'owner file identity missing or false')
    assert report['post_trees'] == report['trees'], (case, 'probe filesystem side effects')
    observed = report['env']
    deleted = record['incoming'].keys() - observed.keys()
    spec_deleted = {key for key in record['incoming']
                    if key.startswith('FLEET_') or key in _STARTUP_SHELL_KEYS}
    assert deleted == spec_deleted, {
        'case': case, 'role': 'deleted-name set',
        'missing_deletions': sorted(spec_deleted - deleted),
        'unexpected_deletions': sorted(deleted - spec_deleted),
    }
    constants = {key: str(Path(report['root']) / value)
                 for key, value in _STARTUP_REDIRECTS.items()}
    constants.update(PATH=record['defpath'], SHELL='/bin/bash')
    for key, value in constants.items():
        assert observed.get(key) == _startup_value_digest(value), {
            'case': case, 'role': 'redirect/reset', 'key': key,
            'expected': _startup_value_digest(value), 'observed': observed.get(key),
        }
    for key, value in record['incoming'].items():
        if key not in spec_deleted and key not in constants:
            assert key in observed and observed[key] == _startup_value_digest(value), {
                'case': case, 'role': 'preserved bytes', 'key': key,
                'expected': _startup_value_digest(value), 'observed': observed.get(key),
            }
    expected = _startup_environment_digests(expected)
    early = report.get('early_native')
    assert type(early) is dict, (case, 'early native report missing')
    assert early.get('subject_type_exact') is True, (case, 'early subject module type missing or false')
    assert early.get('os_environ_exact') is True, (case, 'early environment mapping type missing or false')
    assert early.get('subject_imported') is True, (case, 'early native report precedes subject import')
    assert early.get('subject_finished') is True, (case, \
        'early native report precedes finished subject import')
    assert early.get('readline_loaded') is False, (case, 'early native snapshot ran after readline')
    assert early.get('duplicates_rejected') is True, (case, \
        'early native duplicate contract missing or false')
    assert early.get('probe_root') == record['probe_root'], (case, 'early scenario probe root differs')
    assert early.get('env') == expected, (case, 'early native environment oracle differs', early.get \
        ('env'), expected)
    assert report['env'] == expected, {
        'case': case, 'added': sorted(report['env'].keys() - expected.keys()),
        'removed': sorted(expected.keys() - report['env'].keys()),
        'changed': {key: (expected[key], report['env'][key])
                    for key in expected.keys() & report['env'].keys()
                    if expected[key] != report['env'][key]},
    }
    for key in _STARTUP_REDIRECTS:
        _assert_startup_directory(record, report, key)
    return report


def _assert_startup_redirect(record, report, key):
    root = report['root']
    assert root is not None, report
    expected = str(Path(root) / _STARTUP_REDIRECTS[key])
    if record['case'] != _STARTUP_TARGET_CASE:
        assert record['incoming'].get(key) != expected, key
    else:
        assert record['incoming'][key] == expected, key
    assert report['env'].get(key) == _startup_value_digest(expected), (key, report)
    assert (report['import_env'] or {}).get(key) == _startup_value_digest(expected), (key, report)


def _assert_startup_directory(record, report, key):
    root = report['root']
    assert root is not None and Path(root).is_absolute(), report
    assert report['umask'] == record['umask'] == 0o000, (record['case'], \
        'observed umask differs from zero', report['umask'], record['umask'])
    for name, info, expected in (
        ('root', report['root_stat'], root),
        (key, report['directories'][_STARTUP_REDIRECTS[key]],
         str(Path(root) / _STARTUP_REDIRECTS[key])),
    ):
        assert info and info['exists'] and info['directory'], (name, info)
        assert info['resolved'] == expected, (name, info)
        assert info['mode'] == 0o700, (name, info)


def _assert_startup_fleet(record, report):
    planted = {k for k in record['incoming'] if k.startswith('FLEET_')}
    assert set(_STARTUP_FLEET_KEYS) <= planted and 'FLEET_' in planted
    for key in sorted(planted):
        assert key not in report['env'], (key, report['env'])
    assert not [k for k in report['env'] if k.startswith('FLEET_')], report


@pytest.mark.parametrize('mode', ('with-noconftest', 'without-noconftest'))
def test_startup_plugin_is_loaded_by_config(_startup_observation, mode):
    case = 'rich' if mode == 'with-noconftest' else 'rich-without-noconftest'
    report = _startup_report(_startup_observation, case)
    assert report['registered'] and report['loaded'], report
    assert report['file'] is not None, report
    assert str(Path(report['file']).resolve()) == _startup_observation[case]['plugin_file']


def test_startup_records_original_user_roots(_startup_observation):
    record = _startup_observation['rich']
    assert record['expected_roots']
    assert _startup_report(_startup_observation)['roots'] == record['expected_roots']


def test_startup_resolves_home(_startup_observation):
    record = _startup_observation['rich']
    assert record['home_link'] != record['home']
    assert Path(record['home_link']).is_symlink()
    assert _startup_report(_startup_observation)['roots'][:1] == [record['home']]


@pytest.mark.parametrize('key', _STARTUP_XDG_KEYS)
def test_startup_records_each_xdg_root(_startup_observation, key):
    """Each key contributes its unique targets; the cross-key duplicate stays.

    Omitting a key removes its unique targets, not the shared duplicate planted
    in every key. Existing, missing, dangling and generated content targets keep their positions.
    """
    record = _startup_observation['rich']
    roots = _startup_report(_startup_observation)['roots']
    for target in record['unique'][key]:
        index = record['expected_roots'].index(target)
        assert roots[index:index + 1] == [target], (key, target, roots)
    assert 'relative-' + key not in roots, (key, roots)


@pytest.mark.parametrize('key', tuple(_STARTUP_REDIRECTS))
def test_startup_redirects_each_root(_startup_observation, key):
    _assert_startup_redirect(_startup_observation['rich'],
                             _startup_report(_startup_observation), key)


def test_startup_redirect_map_is_exact(_startup_observation):
    report = _startup_report(_startup_observation)
    actual = report['import_env'] or {}
    expected = {k: str(Path(report['root']) / v) for k, v in _STARTUP_REDIRECTS.items()}
    assert actual == _startup_environment_digests(expected), report
    assert len({info['sha256'] for info in actual.values()}) == 8
    assert report['root_stat']['exists'] and report['root_stat']['directory'], report


@pytest.mark.parametrize('key', tuple(_STARTUP_REDIRECTS))
def test_startup_directories_are_private(_startup_observation, key):
    _assert_startup_directory(_startup_observation['rich'],
                              _startup_report(_startup_observation), key)


def test_startup_ignores_inherited_temp_directory(_startup_observation):
    record = _startup_observation['rich']
    report = _startup_report(_startup_observation)
    root = Path(report['root'])
    assert root.parent == Path('/tmp'), report
    assert not root.is_relative_to(record['home']), report
    assert not root.is_relative_to(record['incoming']['TMPDIR']), report


def test_startup_removes_only_fleet_overrides(_startup_observation):
    _assert_startup_fleet(_startup_observation['rich'], _startup_report(_startup_observation))


@pytest.mark.parametrize('key', ('PATH', 'SHELL'))
def test_startup_resets_path_and_shell(_startup_observation, key):
    record = _startup_observation['rich']
    expected = record['defpath'] if key == 'PATH' else '/bin/bash'
    assert record['incoming'][key] != expected
    assert _startup_report(_startup_observation)['env'].get(key) == _startup_value_digest(expected), key


@pytest.mark.parametrize('key', _STARTUP_SHELL_KEYS)
def test_startup_removes_shell_initialization(_startup_observation, key):
    record = _startup_observation['rich']
    report = _startup_report(_startup_observation)
    assert key in record['incoming']
    assert key not in report['env'], (key, report)


def _assert_preload_marker(record, name, matching):
    assert record['preloads'] == [{'token': record['incoming']['OBSERVER_TOKEN'],
                                   'planted_name': name, 'present': True,
                                   'package_present': name == 'fleet_tui',
                                   'names': [name] if matching else [],
                                   'isolation_loaded': False}], record


def _assert_preload_observer(record):
    report = _startup_report({record['case']: record}, record['case'])
    assert report['registered'] and report['loaded'], record
    assert report['file'] is not None, record
    assert str(Path(report['file']).resolve()) == record['plugin_file'], record


def _assert_preload_diagnostic(record):
    assert not record['reports'], record
    assert 'AssertionError' in record['stderr'], record
    assert 'fleet_tui imported before the TUI test HOME/XDG isolation' in record['stderr'], record


def _assert_preimport_refusal(record, name):
    _assert_preload_marker(record, name, matching=True)
    if record['rc'] == 0:
        # A narrowed guard must genuinely reach the configured observer.
        _assert_preload_observer(record)
    else:
        _assert_preload_diagnostic(record)
    assert record['rc'] != 0, record


def test_startup_refuses_preimported_package(_startup_observation):
    _assert_preimport_refusal(_startup_observation['preload-package'], 'fleet_tui')


@pytest.mark.parametrize('name', _STARTUP_MODULE_NAMES)
def test_startup_refuses_preimported_submodule(_startup_observation, name):
    _assert_preimport_refusal(_startup_observation['preload-' + name], name)


@pytest.mark.parametrize('name', _STARTUP_FUTURE_NAMES)
def test_startup_refuses_preimported_namespace_probe(_startup_observation, name):
    _assert_preimport_refusal(_startup_observation['preload-' + name], name)


@pytest.mark.parametrize('name', _STARTUP_NEAR_NAMES)
def test_startup_accepts_nonmatching_module_name(_startup_observation, name):
    record = _startup_observation['preload-' + name]
    _assert_preload_marker(record, name, matching=False)
    if record['rc'] != 0:
        # Do not misattribute an unrelated startup failure to prefix widening.
        _assert_preload_diagnostic(record)
    _assert_preload_observer(record)


@pytest.mark.parametrize('case', _STARTUP_CASES)
def test_startup_input_axes(_startup_observation, case):
    record = _startup_observation[case]
    report = _startup_report(_startup_observation, case)
    assert report['roots'] == record['expected_roots'], (case, report['roots'], record)
    for key in _STARTUP_REDIRECTS:
        _assert_startup_redirect(record, report, key)
        _assert_startup_directory(record, report, key)
    if case == 'environment-minimal':
        assert record['incoming'] == {} and record['preserved'] == {}, record
        assert not [key for key in report['env'] if key.startswith('FLEET_')], report
    else:
        _assert_startup_fleet(record, report)
    if case in _STARTUP_VALUE_ALPHABET or case == _STARTUP_TARGET_CASE:
        assert record['preserved'], record
        rich = _startup_observation['rich']
        assert record['preserved'].keys() == rich['preserved'].keys()
        assert record['incoming'].keys() == rich['incoming'].keys()
        shape = _STARTUP_VALUE_ALPHABET.get(case, _STARTUP_VALUE_EMPTY)
        for key in record['incoming']:
            if case == _STARTUP_TARGET_CASE and key in (*_STARTUP_REDIRECTS, 'PATH', 'SHELL'):
                assert _startup_value_digest(record['incoming'][key]) == report['env'][key], (case, key)
            else:
                assert record['incoming'][key] == shape, (case, key)
        assert all(record['incoming'][key] == value
                   for key, value in record['preserved'].items()), record
    if case in _STARTUP_NEUTRAL_ALPHABET:
        rich = _startup_observation['rich']
        assert record['incoming'].keys() == rich['incoming'].keys(), (case, 'rich key set differs')
        assert record['preserved'].keys() == rich['preserved'].keys(), (case, 'neutral key set differs')
        assert Path(record['incoming']['HOME']).is_absolute(), (case, 'HOME must stay absolute')
        for key in record['incoming']:
            if key in record['preserved']:
                assert record['incoming'][key] == _STARTUP_NEUTRAL_ALPHABET[case], (case, key, \
                    'neutral class differs')
            else:
                assert _startup_value_digest(record['incoming'][key]) == record['rich_role_digests'] \
                    [key], (case, key, 'rich role changed')
    assert report['env'].get('PATH') == _startup_value_digest(record['defpath']), (case, report)
    assert report['env'].get('SHELL') == _startup_value_digest('/bin/bash'), (case, report)
    for key in _STARTUP_SHELL_KEYS:
        assert key not in report['env'], (case, key, report)
    assert Path(report['root']).parent == Path('/tmp'), (case, report)


def _startup_structural_outcomes(record):
    """Outcomes from actual inputs, checked roots/probes and refusal status.

    Case labels never assign coverage. A successful row must pass the common
    observer checker before its structural input properties are credited.
    """
    if record['preload_name'] is not None and (record['preload_name'] == 'fleet_tui' or
                                               record['preload_name'].startswith('fleet_tui.')):
        _assert_preload_diagnostic(record)
        assert record['rc'] != 0, (record['case'], 'preimport did not refuse')
        assert record['inputs'] == [{'token': record['token'],
                                     'env': _startup_environment_digests(record['incoming']),
                                     'isolation_loaded': False}], (record['case'], \
                                         'refusal input seed missing')
        return [('preimport', 'refused')], []
    report = _startup_report({record['case']: record}, record['case'])
    roots, inputs = _startup_structural_inputs(record['incoming'], Path(record['cwd']))
    # Crossed cells use this independent ordered oracle. Original target/rich
    # rows retain their own oracle (target-seam paths disappear after exit).
    assert record['structural_inputs'] == inputs, (record['case'], 'structural inputs changed')
    assert report['roots'] == record['expected_roots'], (record['case'], 'structural ordered roots differ')
    home = inputs['home']
    if home['input'] in ('empty', 'unset'):
        home_outcome = home['input']
    else:
        home_outcome = home['spelling'] + '-' + home['kind']
        if home['kind'] == 'symlink':
            home_outcome += '-existing' if home['target_exists'] else '-dangling'
    outcomes = [('preimport', 'admitted'), ('home', home_outcome)]
    if record.get('location_inputs') is not None:
        stdlib, os_file = record['location_inputs']
        values = [stdlib, os_file]
        if 'subclass' in record['location_kind']:
            slot = 0 if record['location_kind'].startswith('stdlib-') else 1
            values[slot] = type('StartupLocationString', (str,), {})(values[slot])
        properties = _startup_location_properties(*values,
            links=[(link['spelling'], link['target']) for link in record['location_properties']['links']],
            resolution_error='OSError' if record['location_properties']['exception'] == 'OSError' else None)
        assert properties == record['location_properties'], (record['case'], 'location property disagreement')
        early = report['early_native']
        assert early['location_sources'] == [properties['stdlib'], properties['os_file']], (record[ \
            'case'], 'measured location source types or values differ')
        if properties['exception'] == 'OSError':
            assert early['resolution_events'] and all(item['exception'] == 'OSError' for item in \
                early['resolution_events']), (record['case'], 'targeted resolver fault did not fire')
        branch = properties['branch']
        planned_branch, planned_op, planned_kind = _STARTUP_LOCATION_SPECS[record['location_kind']]
        if planned_op == 'source':
            expected_source_kind = planned_kind
            actual_source_kind = stdlib_kind = ('none' if properties['stdlib']['none'] else
                'empty' if properties['stdlib']['exact_str'] and stdlib == '' else properties[ \
                    'source_kind']) if planned_branch == 'stdlib' else properties['source_kind']
            assert actual_source_kind == expected_source_kind, (record['case'], \
                'location label/property disagreement')
        else:
            planned_operation = ('resolution:' + {'nul': 'ValueError', 'oserror': 'OSError', 'loop' \
                : 'RuntimeError'}[planned_kind]
                if planned_kind in ('nul', 'oserror', 'loop') else
                'candidates:' + planned_kind if planned_kind.endswith('collapse') else \
                    'filesystem:' + planned_kind)
            assert branch == planned_branch and _startup_location_operation(properties) == \
                planned_operation, (record['case'], 'location label/property disagreement')
        outcomes.append(('license-import:' + branch, _startup_location_operation(properties)))
        # Source rows also prove each selector/rejection from original storage.
        stdlib_property = properties['stdlib']
        stdlib_kind = ('none' if stdlib_property['none'] else 'empty' if stdlib_property['exact_str' \
            ] and stdlib == '' else properties['source_kind'])
        outcomes.append(('license-source:' + ('stdlib' if record['location_kind'].startswith( \
            'stdlib-') else branch),
                         stdlib_kind if record['location_kind'].startswith('stdlib-') else \
                             properties['source_kind']))
    for key, item in inputs['xdg'].items():
        for entry in item['entries']:
            outcome = entry['outcome']
            if outcome == 'absolute':
                outcome += '-' + entry['kind']
                if entry['kind'] == 'symlink':
                    outcome += '-existing' if entry['target_exists'] else '-dangling'
                assert report['roots'][entry['index']] == entry['resolved'], (record['case'], key, \
                    'XDG resolution/order differs')
                outcomes.append(('xdg:' + key, 'dedup-duplicate' if entry['duplicate'] else 'dedup-kept'))
                if entry['duplicate'] and entry['resolved'] == home['resolved']:
                    outcomes.append(('home-xdg', 'identical' if entry['input'] == record['incoming'] \
                        .get('HOME') else 'alias'))
                elif not entry['duplicate'] and home.get('spelling') == 'absolute':
                    outcomes.append(('home-xdg', 'distinct'))
            outcomes.append(('xdg:' + key, outcome))
    # Each fixed result and finding channel was checked against literal and
    # parent outcomes. Credit only names whose alphabet value remains at call time.
    calls = [('probe:' + key, 'checked') for key in report['probes']['results']]
    return sorted(set(outcomes)), calls


def _assert_startup_structural_cells(records):
    """Require every feasible (name, alphabet, operation, outcome) cell.

    Cells use actual seeded values, property observations and checked output.
    Mechanical exclusions come solely from fixed alphabet input semantics.
    """
    names = sorted(records['rich']['incoming'])
    observed = defaultdict(set)
    witnesses = []
    for case, record in records.items():
        if case not in (*_STARTUP_STRUCTURAL_CASES, *_STARTUP_LOCATION_CASES, *_STARTUP_ALPHABET_REFUSALS,
                        *_STARTUP_VALUE_ALPHABET):
            continue
        outcomes, calls = _startup_structural_outcomes(record)
        name_groups = {}
        for alphabet, value in _STARTUP_VALUE_ALPHABET.items():
            planted = [name for name in names if record['incoming'].get(name) == value]
            if not planted:
                continue
            for operation, outcome in outcomes:
                observed[(alphabet, operation, outcome)].update(planted)
            callable_names = []
            if record['reports']:
                after = record['reports'][0]['env']
                callable_names = [name for name in planted if after.get(name) == _startup_value_digest(value)]
                for operation, outcome in calls:
                    observed[(alphabet, operation, outcome)].update(callable_names)
            name_groups[alphabet] = {'names': planted, 'call_time_names': callable_names}
        witnesses.append({'case': case, 'rc': record['rc'], 'cwd': record['cwd'],
                          'input_digests': _startup_environment_digests(record['incoming']),
                          'inputs': record['structural_inputs'],
                          'location_properties': record.get('location_properties'),
                          'operations': outcomes, 'calls': calls, 'groups': name_groups})
    required, exclusions = set(), []
    xdg_outcomes = ('absolute-directory', 'absolute-missing', 'absolute-symlink-existing',
                    'absolute-symlink-dangling', 'relative', 'empty', 'dedup-kept', 'dedup-duplicate')
    home_outcomes = ('absolute-directory', 'absolute-missing', 'absolute-symlink-existing',
                     'absolute-symlink-dangling', 'relative-directory', 'relative-missing',
                     'relative-symlink-existing', 'relative-symlink-dangling', 'empty', 'unset')
    probe_outcomes = [('probe:' + key, 'checked') for key in _STARTUP_PROBE_EXPECTED]
    for name in names:
        for alphabet, value in _STARTUP_VALUE_ALPHABET.items():
            outcomes = [('preimport', 'admitted'), ('preimport', 'refused')]
            for branch in ('stdlib', 'fallback-none', 'fallback-empty'):
                outcomes += [('license-source:' + branch, kind) for kind in _STARTUP_LOCATION_SOURCE_KINDS]
                outcomes += [('license-import:' + branch, operation) for operation in (
                    'filesystem:plain', 'filesystem:missing', 'filesystem:directory-link-other-parent',
                    'filesystem:directory-link-same-parent', 'filesystem:parent-link', 'filesystem:dangling',
                    'resolution:ValueError', 'resolution:OSError', 'candidates:root-collapse', \
                        'candidates:alias-collapse')]
                if tuple(sys.version_info[:2]) < (3, 13):
                    outcomes.append(('license-import:' + branch, 'resolution:RuntimeError'))
                else:
                    exclusions.append({'name': name, 'alphabet': alphabet, 'operation': \
                        'license-import:' + branch,
                        'excluded': 'resolution:RuntimeError', 'reason': \
                            '3.13+ non-strict Path.resolve does not raise on the planted symlink loop'})
                exclusions.append({'name': name, 'alphabet': alphabet, 'operation': \
                    'license-import:' + branch,
                    'excluded': 'filesystem/resolution/candidates on rejected source',
                    'reason': 'only exact absolute accepted sources reach resolution; fallback ' \
                        'properties require None or exact-empty stdlib'})
            if name != 'HOME':
                outcomes += [('home-xdg', variant) for variant in ('identical', 'alias', 'distinct')]
            else:
                exclusions.append({'name': name, 'alphabet': alphabet, 'operation': 'home-xdg', \
                    'excluded': 'identical/alias/distinct',
                    'reason': 'fixed HOME alphabet is empty or relative, not an existing absolute HOME root'})
            if name == 'HOME':
                allowed = (['empty'] if value == '' else ['relative-missing'] +
                           (['relative-directory', 'relative-symlink-existing', 'relative-symlink-dangling']
                            if len(value.encode()) <= 255 else []))
                exclusions.append({'name': name, 'alphabet': alphabet, 'operation': 'home',
                    'excluded': sorted(set(home_outcomes) - set(allowed)),
                    'reason': 'own value is fixed: relative nonempty component, empty HOME, or ' \
                        'over NAME_MAX; unset/absolute spelling cannot retain it'})
            else:
                allowed = home_outcomes
            outcomes += [('home', outcome) for outcome in allowed]
            for key in _STARTUP_XDG_KEYS:
                allowed_xdg = xdg_outcomes if name != key else ['empty' if value == '' else 'relative']
                if name == key:
                    exclusions.append({'name': name, 'alphabet': alphabet, 'operation': 'xdg:' + key,
                        'excluded': sorted(set(xdg_outcomes) - set(allowed_xdg)),
                        'reason': \
                            'own fixed alphabet value has no absolute entry or pathsep; '
                            'adding one changes its value'})
                outcomes += [('xdg:' + key, outcome) for outcome in allowed_xdg]
            if not (name.startswith('FLEET_') or name in _STARTUP_SHELL_KEYS or
                    name in _STARTUP_REDIRECTS or name in ('PATH', 'SHELL')):
                outcomes += probe_outcomes
            else:
                exclusions.append({'name': name, 'alphabet': alphabet, 'operation': 'probe:*',
                    'excluded': 'all', 'reason': \
                        'startup removes or resets this name before call-time probes; the' \
                        ' own input alphabet value cannot remain'})
            required.update((name, alphabet, operation, outcome) for operation, outcome in outcomes)
    missing = {cell for cell in required if cell[0] not in observed.get(cell[1:], ())}
    if missing:
        pytest.fail(str(('required structural cell has no checked witness', len(missing), sorted( \
            missing)[:20])))
    covering_table, structural_table, descriptions = [], [], {}
    for name, alphabet, operation, outcome in sorted(required):
        if not operation.startswith(('license-source:', 'license-import:')):
            structural_table.append((name, alphabet, operation, outcome))
            continue
        key = operation, outcome
        if key not in descriptions:
            category, branch = operation.split(':', 1)
            if category == 'license-source':
                source = 'sys._stdlib_dir' if branch == 'stdlib' else 'os.__file__'
                active = ('fallback-' + outcome if branch == 'stdlib' and outcome in ('none', \
                    'empty') else branch)
                result = ('accepted' if outcome == 'absolute' or source == 'sys._stdlib_dir' and \
                    outcome in ('none', 'empty') else
                          'absent' if source == 'os.__file__' and outcome == 'none' else 'refused')
                descriptions[key] = active, 'source:' + source + ':' + outcome, result
            else:
                descriptions[key] = branch, outcome, ('resolution-error' if outcome.startswith( \
                    'resolution:') else 'accepted')
        active, action, result = descriptions[key]
        covering_table.append((name, alphabet, active, action, result))
    return {'name_count': len(names), 'required_cell_count': len(required),
            'observed_cell_count': sum(len(names) for names in observed.values()), 'missing': [],
            'names': names, 'alphabet': _STARTUP_VALUE_ALPHABET,
            'covering_columns': ['planted_environment_name', 'alphabet_class', \
                'active_source_branch', 'structural_operation', 'outcome'],
            'covering_table': covering_table, 'structural_covering_table': structural_table,
            'exclusions': exclusions, 'witness_rows': witnesses}


def test_startup_structural_input_cells(_startup_observation):
    import json
    result = _assert_startup_structural_cells(_startup_observation)
    # Receipts are adjacent to the observer, in the fixture-wide startup tree.
    base = Path(_startup_observation['rich']['cwd'])
    for arg in _startup_observation['rich']['argv']:
        if arg.endswith('test_startup_observer.py'):
            base = Path(arg).parent
            break
    structural_text = json.dumps(result, separators=(',', ':'), sort_keys=True)
    if (base / 'structural-cells.json').write_text(structural_text) != len(structural_text):
        raise OSError(5, 'short structural coverage write', str(base / 'structural-cells.json'))


def test_startup_field_type_contract_rejects_missing_observation(monkeypatch):
    value = _startup_object_fields(_isolation.__spec__, 'spec', _isolation)
    del value['fields']['__dict__']['exact_dict']
    with pytest.raises(AssertionError, match='exact field type contract missing or false'):
        _assert_startup_field_sets(value, 'spec', 'negative-type-field')
    info = _startup_object_fields(__import__('weakref').finalize._registry[
        _isolation._TEST_DIRECTORY._finalizer], 'info', _isolation)
    del info['exact_info']
    with pytest.raises(AssertionError, match='exact_info missing or false'):
        _assert_startup_field_sets(info, 'info', 'negative-info-absence')
    info['exact_info'] = False
    with pytest.raises(AssertionError, match='exact_info missing or false'):
        _assert_startup_field_sets(info, 'info', 'negative-info-false')
    import copy
    import types
    import weakref
    class Name(str):
        pass
    for key in ('cached', 'parent'):
        spec = copy.copy(_isolation.__spec__)
        spec.__dict__[key] = getattr(spec, key)
        exact = _startup_object_fields(spec, 'spec', _isolation)
        _assert_startup_field_sets(exact, 'spec', 'exact-shadow-' + key)
        spec.__dict__[key] = Name(spec.__dict__[key])
        altered = _startup_object_fields(spec, 'spec', _isolation)
        assert altered != exact, ('original dictionary shadow type erased', key)
        with pytest.raises(AssertionError, match='stored dictionary value differs'):
            _assert_startup_field_sets(altered, 'spec', 'subclass-shadow-' + key)
    original = weakref.finalize._registry[_isolation._TEST_DIRECTORY._finalizer]
    changed = weakref.finalize._Info()
    for name in ('weakref', 'func', 'args', 'kwargs', 'atexit', 'index'):
        setattr(changed, name, getattr(original, name))
    exact = _startup_object_fields(changed, 'info', _isolation)
    _assert_startup_field_sets(exact, 'info', 'exact-cleanup-function')
    calls = []
    class EqualityProxy:
        __code__ = original.func.__code__
        def __call__(self, *args, **kwargs):
            calls.append('call')
        def __eq__(self, other):
            calls.append('equality')
            return other is original.func
    for proxy in (EqualityProxy(), types.MethodType(EqualityProxy(), type(_isolation._TEST_DIRECTORY))):
        changed.func = proxy
        altered = _startup_object_fields(changed, 'info', _isolation)
        assert altered != exact, 'reached cleanup function type erased'
        with pytest.raises(AssertionError, match='cleanup function or receiver identity'):
            _assert_startup_field_sets(altered, 'info', 'proxy-cleanup-function')
    assert calls == [], 'untrusted cleanup callable or equality ran'
    missing = copy.deepcopy(exact)
    del missing['fields']['func']['cleanup_identity']
    with pytest.raises(AssertionError, match='cleanup function or receiver identity'):
        _assert_startup_field_sets(missing, 'info', 'missing-cleanup-identity')
    missing = copy.deepcopy(_startup_object_fields(_isolation.__spec__, 'spec', _isolation))
    del missing['fields']['__dict__']['storage_correspondence']
    with pytest.raises(AssertionError, match='stored dictionary correspondence missing'):
        _assert_startup_field_sets(missing, 'spec', 'missing-storage-correspondence')



def test_startup_value_type_dispatch_uses_identity():
    class EqualType(type):
        def __eq__(cls, other):
            return other is str
    class SameName(str, metaclass=EqualType):
        pass
    SameName.__module__, SameName.__qualname__ = 'builtins', 'str'
    value = SameName('value')
    assert type(value) is not str
    assert _startup_namespace_value(value).get('unsupported') is True


_DISPATCH_CASES = (
    'valid-list', 'valid-tuple', 'valid-spaces-apostrophe', 'valid-symlink-scratch',
    'valid-mixed-paths', 'scheme-paths',
    'token-setsid-space', 'token-sh-space', 'token-c-space',
    'token-setsid-scheme', 'token-sh-scheme', 'token-c-scheme',
    'script-true-space', 'script-touch-space', 'script-true-scheme', 'script-touch-scheme',
    'valid-missing-brief', 'valid-missing-out', 'valid-missing-log', 'valid-missing-done',
    'valid-dangling-brief', 'valid-dangling-out', 'valid-dangling-log', 'valid-dangling-done',
    'outside-missing', 'outside-dangling',
    'argv-short-0', 'argv-short-1', 'argv-short-2', 'argv-short-3', 'argv-long-5',
    'prefix-setsid', 'prefix-sh', 'prefix-c', 'non-list-tuple-sequence', 'bytes-script',
    'outside-sibling', 'outside-dotdot', 'outside-symlink', 'outside-prefix-neighbour',
    'one-escape-brief', 'one-escape-out', 'one-escape-log', 'one-escape-done',
    'suffix-brief', 'suffix-out', 'suffix-log', 'suffix-done',
    'shell-short-0', 'shell-short-1', 'shell-short-2', 'shell-short-3',
    'shell-short-4', 'shell-short-5', 'shell-short-6', 'shell-short-7',
    'shell-short-true-a-b', 'shell-short-false-a',
    'malformed-single-quote', 'malformed-double-quote', 'trailing-space', 'trailing-tab',
    'wrong-touch-word', 'wrong-redirection-token', 'wrong-initial-word',
    'token-setsid-abs', 'token-setsid-rel', 'token-setsid-case', 'token-setsid-tail',
    'token-sh-abs', 'token-sh-rel', 'token-sh-case', 'token-sh-tail',
    'token-c-abs', 'token-c-rel', 'token-c-case', 'token-c-tail',
    'script-true-abs', 'script-true-rel', 'script-true-case', 'script-true-tail',
    'script-touch-abs', 'script-touch-rel', 'script-touch-case', 'script-touch-tail',
    'script-stderr-swap', 'script-stderr-no-semicolon',
    'suffix-brief-case', 'suffix-out-case', 'suffix-log-case', 'suffix-done-case',
)


@pytest.mark.parametrize('case', _DISPATCH_CASES)
def test_dispatch_fixture_grammar(tmp_path, case):
    """The real parser returns literal booleans, including on malformed input.

    Every refusal has a valid sibling; every admission has a refused sibling.
    These calls do not launch processes; existing Popen controls prove wiring.
    """
    import shlex

    scratch, outside = tmp_path / 'scratch', tmp_path / 'scratch-evil'
    scratch.mkdir()
    outside.mkdir()
    suffixes = ('brief', 'out', 'log', 'done')
    def files(directory, stem='job'):
        paths = [directory / (stem + '.' + suffix) for suffix in suffixes]
        for path in paths:
            path.touch()
        return paths
    def script(paths):
        brief, out, log, done = [shlex.quote(str(path)) for path in paths]
        return f'true {brief} {out} > {log} 2>&1; touch {done}'
    spelling_case = (case.startswith(('token-', 'script-')) or case.endswith('-case'))
    paths = files(scratch, "job with an'apostrophe" if spelling_case else 'job')
    outside_paths = files(outside)
    canonical = ['setsid', 'sh', '-c', script(paths)]
    args = list(canonical)
    expected = case.startswith('valid-')
    if case.startswith(('valid-missing-', 'valid-dangling-')):
        state, suffix = case.removeprefix('valid-').split('-')
        path = paths[suffixes.index(suffix)]
        path.unlink()
        if state == 'dangling':
            path.symlink_to(scratch / ('missing-' + suffix))
            assert path.is_symlink() and path.resolve().is_relative_to(scratch)
        assert not path.exists()
    elif case == 'valid-tuple':
        args = tuple(args)
    elif case == 'valid-mixed-paths':
        args[3] = script(files(scratch, 'MixedCase'))
    elif case == 'scheme-paths':
        args[3] = script(['file://' + str(path) for path in paths])
    elif case == 'valid-spaces-apostrophe':
        args[3] = script(files(scratch, "job with an'apostrophe"))
    elif case == 'valid-symlink-scratch':
        link = tmp_path / 'scratch-link'
        link.symlink_to(scratch, target_is_directory=True)
        assert link != link.resolve() and link.resolve() == scratch
        scratch = link
        args[3] = script([link / path.name for path in paths])
    elif case.startswith('argv-short-'):
        args = args[:int(case.rsplit('-', 1)[1])]
    elif case == 'argv-long-5':
        args.append('extra')
    elif case.startswith('token-'):
        _, slot, shape = case.split('-')
        index = {'setsid': 0, 'sh': 1, 'c': 2}[slot]
        literal = canonical[index]
        assert _isolation._dispatch_fixture(canonical, scratch) is True, case
        args[index] = {'abs': '/tmp/evil/' + literal, 'rel': './' + literal,
                       'case': literal.upper(), 'tail': 'evil' + literal,
                       'space': literal[0] + ' ' + literal[1:],
                       'scheme': 'file:///' + literal}[shape]
        assert len(args) == 4 and args[3] == canonical[3]
        assert [i for i in range(4) if args[i] != canonical[i]] == [index]
    elif case.startswith('script-'):
        assert _isolation._dispatch_fixture(canonical, scratch) is True, case
        if case.startswith('script-stderr-'):
            replacement = '1>&2;' if case == 'script-stderr-swap' else '2>&1'
            args[3] = args[3].replace(' 2>&1; ', ' ' + replacement + ' ', 1)
        else:
            _, literal, shape = case.split('-')
            replacement = {'abs': '/tmp/evil/' + literal, 'rel': './' + literal,
                           'case': literal.upper(), 'tail': 'evil' + literal,
                           'space': shlex.quote(literal[0] + ' ' + literal[1:]),
                           'scheme': 'file:///' + literal}[shape]
            if literal == 'true':
                args[3] = replacement + args[3][4:]
            else:
                args[3] = args[3].replace('; touch ', '; ' + replacement + ' ', 1)
        original_words, changed_words = shlex.split(canonical[3]), shlex.split(args[3])
        index = 5 if case.startswith('script-stderr-') else (0 if literal == 'true' else 6)
        assert len(changed_words) == 8
        assert [i for i in range(8) if original_words[i] != changed_words[i]] == [index]
    elif case.startswith('prefix-'):
        index = {'prefix-setsid': 0, 'prefix-sh': 1, 'prefix-c': 2}[case]
        args[index] = 'wrong'
    elif case == 'non-list-tuple-sequence':
        class Sequence:
            def __len__(self):
                return len(canonical)
            def __getitem__(self, index):
                return canonical[index]
        args = Sequence()
    elif case == 'bytes-script':
        args[3] = args[3].encode()
    elif case.startswith('outside-'):
        if case == 'outside-missing':
            paths = [outside / 'missing' / p.name for p in outside_paths]
            assert not any(path.exists() for path in paths)
        elif case == 'outside-dangling':
            link = scratch / 'dangling-escape'
            link.symlink_to(outside / 'missing', target_is_directory=True)
            paths = [link / p.name for p in outside_paths]
            assert link.is_symlink() and not link.exists()
            assert all(not path.resolve().is_relative_to(scratch) for path in paths)
        elif case == 'outside-dotdot':
            paths = [scratch / '..' / outside.name / p.name for p in outside_paths]
        elif case == 'outside-symlink':
            link = scratch / 'escape'
            link.symlink_to(outside, target_is_directory=True)
            paths = [link / p.name for p in outside_paths]
        else:
            paths = outside_paths
        if case == 'outside-prefix-neighbour':
            assert str(outside).startswith(str(scratch))
            assert not outside.is_relative_to(scratch)
        args[3] = script(paths)
    elif case.startswith('one-escape-'):
        index = suffixes.index(case.removeprefix('one-escape-'))
        paths[index].unlink()
        paths[index].symlink_to(outside_paths[index])
        assert paths[index].is_symlink() and paths[index].resolve() == outside_paths[index]
        # The positive sibling must stay wholly inside after the escape plant.
        canonical[3] = script(files(scratch, 'sibling'))
    elif case.startswith('suffix-'):
        suffix = case.removeprefix('suffix-').removesuffix('-case')
        index = suffixes.index(suffix)
        paths[index] = (paths[index].with_suffix('.' + suffix.upper())
                        if case.endswith('-case') else scratch / 'job.evil')
        args[3] = script(paths)
    elif case.startswith('shell-short-'):
        count = case.removeprefix('shell-short-')
        args[3] = {'true-a-b': 'true a b', 'false-a': 'false a'}.get(count, '')
        if count.isdigit():
            args[3] = ' '.join(['true', 'a', 'b', '>', 'c', '2>&1;', 'touch'][:int(count)])
    elif case.startswith('malformed-'):
        args[3] += " '" if case == 'malformed-single-quote' else ' "'
    elif case == 'trailing-space':
        args[3] += ' '
    elif case == 'trailing-tab':
        args[3] += '\t'
    elif case == 'wrong-touch-word':
        args[3] = args[3].replace('; touch ', '; echo ')
    elif case == 'wrong-redirection-token':
        args[3] = args[3].replace(' > ', ' < ')
    elif case == 'wrong-initial-word':
        args[3] = 'false' + args[3][4:]
    assert _isolation._dispatch_fixture(args, scratch) is expected, case
    if expected:
        assert _isolation._dispatch_fixture(['wrong', *canonical[1:]], scratch) is False
    else:
        assert _isolation._dispatch_fixture(canonical, scratch) is True, case


@pytest.mark.parametrize('site', ['sys._stdlib_dir', 'os.__file__', 'package.__file__'])
@pytest.mark.parametrize('value', _CORPUS_SCHEMES)
def test_scheme_exemption_sources_are_refused(site, value):
    """Each real exemption call site records scheme text as a refused source."""
    finding = (f'{site} = <uninspectable exemption source>',)
    if site == 'package.__file__':
        package = ModuleType('scratch_pkg')
        package.__file__ = value
        assert _source_metadata_dirs(package, pycache_prefix=None) == ((), finding)
    elif site == 'sys._stdlib_dir':
        assert _license_files(((site, value), ('os.__file__', None))) == ((), finding)
    else:
        assert _license_files((('sys._stdlib_dir', None), (site, value))) == ((), finding)


@pytest.mark.parametrize('state', ['existing', 'missing', 'dangling'])
def test_absolute_exemption_sources_do_not_require_existence(tmp_path, state):
    """Absolute location spelling is accepted independently of filesystem state."""
    location = tmp_path / 'Mixed location with space'
    target = tmp_path / 'missing target'
    if state == 'existing':
        location.mkdir()
    elif state == 'dangling':
        location.symlink_to(target, target_is_directory=True)
        assert location.is_symlink() and not location.exists()
    else:
        assert not location.exists()
    value = str(location)
    refused = []
    assert _isolation._exact_location(value, 'location', refused) is value
    assert refused == []
    # Parent and here are constructed independently, including a dangling target.
    resolved = target if state == 'dangling' else location
    expected = tuple(directory / name for directory in (tmp_path, resolved)
                     for name in ('LICENSE.txt', 'LICENSE'))
    assert _license_files((('sys._stdlib_dir', value), ('os.__file__', None))) == (expected, ())


def test_startup_generated_environment_corpora(_startup_observation):
    """The input fixture actually carries the declared character/length classes."""
    record = _startup_observation['rich']
    characters = set(_environment_characters())
    assert {chr(code) for code in range(1, 128)} <= characters
    assert set(string.whitespace) <= characters and '\u00a0' in characters
    for start, stop in _STARTUP_UNICODE_RANGES:
        assert {chr(code) for code in range(start, stop)
                if unicodedata.category(chr(code))[0] in 'LS'} <= characters
    fragments = _root_content_fragments()
    for key in _STARTUP_XDG_KEYS:
        value = record['incoming'][key]
        assert all('/' + fragment + '/tail' in value for fragment in fragments)
    suffixes = _fleet_suffixes()
    assert all('FLEET_' + suffix in record['incoming'] for suffix in suffixes)
    assert max(map(len, suffixes)) >= 8192
    assert max(map(len, fragments)) >= 8192
    assert all(key in record['incoming'] for key in (
        'LANG', 'TZ', 'TERM', 'PAGER', 'EDITOR', 'BASH_FUNC_x%%', 'MULTILINE_VALUE'))
    assert '\n' in record['incoming']['MULTILINE_VALUE']
    assert set(_STARTUP_VALUE_ALPHABET) == {
        'environment-empty', 'environment-whitespace', 'environment-decimal',
        'environment-digit', 'environment-unicode', 'environment-non-nfc',
        'environment-fleet-value', 'environment-extreme'}
    assert _STARTUP_VALUE_EMPTY == ''
    assert set(_STARTUP_VALUE_WHITESPACE) == {' ', '\t'}
    assert _STARTUP_VALUE_DECIMAL == '10' and _STARTUP_VALUE_DIGIT == '0'
    assert {'é', '\u00a0', 'Ω', 'Ж', '漢'} <= set(_STARTUP_VALUE_UNICODE)
    assert _STARTUP_VALUE_NON_NFC == 'e\u0301'
    assert unicodedata.normalize('NFC', _STARTUP_VALUE_NON_NFC) != _STARTUP_VALUE_NON_NFC
    assert _STARTUP_VALUE_FLEET_PREFIX.startswith('FLEET_')
    extreme = _STARTUP_VALUE_EXTREME
    assert _STARTUP_VALUE_BOUND == 8192
    assert len(extreme) == _STARTUP_VALUE_BOUND
    assert extreme.startswith(' \t') and extreme.endswith('\t ')
    assert {'é', '\u00a0', 'Ω', 'Ж', '漢'} <= set(extreme)
    _startup_report(_startup_observation)


def test_dispatch_generated_spelling_classes(tmp_path):
    """Exact literals and path contents over generated transportable classes."""
    import shlex

    scratch = tmp_path / 'scratch'
    scratch.mkdir()
    suffixes = ('.brief', '.out', '.log', '.done')
    def script(paths):
        brief, out, log, done = map(shlex.quote, paths)
        return f'true {brief} {out} > {log} 2>&1; touch {done}'
    paths = [str(scratch / ('job' + suffix)) for suffix in suffixes]
    canonical = ['setsid', 'sh', '-c', script(paths)]
    assert _isolation._dispatch_fixture(canonical, scratch) is True
    # isinstance is an inheritance class, not an exact-builtin allowlist.
    for base in (list, tuple):
        container = base
        for depth in range(3):
            text = str
            for text_depth in range(3):
                args = container([*canonical[:3], text(canonical[3])])
                assert _isolation._dispatch_fixture(args, scratch) is True, (base, depth, text_depth)
                denied = container(['wrong', *args[1:]])
                assert _isolation._dispatch_fixture(denied, scratch) is False
                text = type('Script' + str(text_depth), (text,), {})
            container = type('Arguments' + str(depth), (container,), {})
    for length in sorted({*range(17), *(2 ** power for power in range(5, 14))} - {4}):
        args = (canonical * (length // 4 + 1))[:length]
        assert len(args) == length
        assert _isolation._dispatch_fixture(args, scratch) is False, length
    whitespace = _unicode_classes()['whitespace']
    for char in whitespace:
        for index, literal in enumerate(canonical[:3]):
            for position in range(len(literal) + 1):
                args = list(canonical)
                args[index] = literal[:position] + char + literal[position:]
                assert _isolation._dispatch_fixture(args, scratch) is False, (index, args)
        for literal in ('true', 'touch'):
            for position in range(len(literal) + 1):
                changed = shlex.quote(literal[:position] + char + literal[position:])
                text = (changed + canonical[3][4:] if literal == 'true' else
                        canonical[3].replace('; touch ', '; ' + changed + ' ', 1))
                assert _isolation._dispatch_fixture(['setsid', 'sh', '-c', text],
                                                     scratch) is False, text
        assert _isolation._dispatch_fixture([*canonical[:3], canonical[3] + char],
                                             scratch) is False, repr(char)
    for fragment in _root_content_fragments():
        generated_paths = [str(scratch / fragment / ('job' + suffix)) for suffix in suffixes]
        text = script(generated_paths)
        assert _isolation._dispatch_fixture([*canonical[:3], text], scratch) is True, fragment
    for index, suffix in enumerate(suffixes):
        for chars in product(*((char.lower(), char.upper()) for char in suffix)):
            variant = ''.join(chars)
            if variant == suffix:
                continue
            changed = list(paths)
            changed[index] = str(scratch / ('job' + variant))
            assert _isolation._dispatch_fixture([*canonical[:3], script(changed)],
                                                 scratch) is False, (index, variant)


@lru_cache(maxsize=None)
def _generated_scheme_values():
    # Enumerate the scheme-name character alphabet at an interior position,
    # every case spelling of "file", three delimiter forms and length powers.
    names = {'a' + char + 'z' for char in string.ascii_letters + string.digits + '+-.'}
    names.update(''.join(chars) for chars in product(*((c, c.upper()) for c in 'file')))
    names.update('x' * (2 ** power) for power in range(14))
    return tuple(name + ending for name in sorted(names)
                 for ending in (':/abs', ':///abs', '://host/abs'))


@pytest.mark.parametrize('site', ['sys._stdlib_dir', 'os.__file__', 'package.__file__'])
def test_generated_scheme_exemption_sources_are_refused(site):
    finding = (f'{site} = <uninspectable exemption source>',)
    for value in _generated_scheme_values():
        if site == 'package.__file__':
            package = ModuleType('scratch_pkg')
            package.__file__ = value
            assert _source_metadata_dirs(package, pycache_prefix=None) == ((), finding), value
        elif site == 'sys._stdlib_dir':
            assert _license_files(((site, value), ('os.__file__', None))) == ((), finding), value
        else:
            assert _license_files((('sys._stdlib_dir', None), (site, value))) == ((), finding), value



def test_license_resolution_error_classes(monkeypatch):
    """Every named resolution error, including subclasses, gives one finding."""
    for base in (ValueError, OSError, RuntimeError):
        error = base
        for depth in range(3):
            def fail_resolution(self, *args, _error=error, **kwargs):
                raise _error('controlled resolution failure')
            with monkeypatch.context() as patch:
                patch.setattr(Path, 'resolve', fail_resolution)
                assert _license_files((('sys._stdlib_dir', '/controlled/lib'),
                                       ('os.__file__', None))) == (
                    (), ('sys._stdlib_dir = <uninspectable exemption source>',))
            error = type('ResolutionError' + str(depth), (error,), {})


def test_license_fallback_requires_exact_empty_string(tmp_path):
    """The fallback sentinel is exact empty str or None, not a string class."""
    stdlib = tmp_path / 'stdlib'
    stdlib.mkdir()
    source = str(stdlib / 'os.py')
    expected = tuple(str(directory / name) for directory in (tmp_path, stdlib)
                     for name in ('LICENSE.txt', 'LICENSE'))
    for value in (None, ''):
        files, refused = _license_files((('sys._stdlib_dir', value), ('os.__file__', source)))
        assert tuple(map(str, files)) == expected and refused == ()
    value_type = str
    for depth in range(3):
        value_type = type('EmptySource' + str(depth), (value_type,), {})
        for value in (value_type(''), value_type('/absolute'), value_type('relative')):
            assert _license_files((('sys._stdlib_dir', value), ('os.__file__', source))) == (
                (), ('sys._stdlib_dir = <uninspectable exemption source>',))
