"""Fast reds for preflight, the interpreter floor, and pytest's exit hold.

The direct-entry probes refuse gate import if preflight falls through: a broken
preflight fails quickly instead of accidentally running the entire teeth suite.
"""
import os
import re
import site
from functools import lru_cache
from pathlib import Path
import shutil
import subprocess

import pytest


# Capture before the per-test HOME/PYTHONPATH isolation. Each child computes its
# own version-specific site under this user base; -s still disables it.
_CHILD_USER_BASE = site.getuserbase()
_CHILD_PYTHONPATH = os.environ.get('PYTHONPATH', '')
_PYTEST_UNAVAILABLE = 'UNMEASURED: child pytest unavailable: '


TEETH = Path(__file__).with_name('teeth_fetch_gate.py')
CONFTEST = Path(__file__).with_name('conftest.py')
PREFIX = 'UNMEASURED: fetch-gate teeth: pre-existing '
FLOOR = ('UNMEASURED: fetch-gate teeth require CPython 3.12+ with sys.monitoring; '
         'use that interpreter for guard validation')
SLOTS = [('sys.gettrace', 'sys.settrace(lambda *a: None)'),
         ('sys.getprofile', 'sys.setprofile(lambda *a: None)')]
SLOTS += [('sys.monitoring tool %d' % tool,
           "sys.monitoring.use_tool_id(%d, 'preflight regression')" % tool)
          for tool in range(6)]


def _python(version):
    executable = shutil.which('python' + version)
    if executable is None:
        pytest.skip('python%s absent: cannot measure that interpreter path' % version)
    return executable


def _run(version, source):
    return subprocess.run([_python(version), '-c', source], text=True,
                          capture_output=True, timeout=20,
                          env=dict(os.environ, PYTEST_ADDOPTS='', PYTEST_DISABLE_PLUGIN_AUTOLOAD='1',
                                   PYTHONDONTWRITEBYTECODE='1',
                                   PYTHONUSERBASE=_CHILD_USER_BASE,
                                   PYTHONPATH=_CHILD_PYTHONPATH))



def _child_pytest_import():
    return ('import sys\ntry:\n    import pytest\n'
            'except ImportError as exc:\n'
            '    print(%r + type(exc).__name__ + ": " + str(exc))\n'
            '    sys.exit(77)\n') % _PYTEST_UNAVAILABLE


def _skip_unavailable_child_pytest(result):
    if result.returncode == 77 and result.stdout.startswith(_PYTEST_UNAVAILABLE):
        pytest.skip(result.stdout.strip())


@pytest.mark.parametrize('version', ['3.12', '3.14'])
@pytest.mark.parametrize('label,setup', SLOTS, ids=[s[0] for s in SLOTS])
def test_preflight_occupied_slot_exits_two_before_gate_import(version, label, setup):
    source = '''import sys, runpy
class NoGate:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'guard' or fullname.startswith('guard.'):
            raise RuntimeError('GATE_IMPORT_REACHED: preflight did not stop')
sys.meta_path.insert(0, NoGate())
'''
    source += setup + '\nrunpy.run_path(%r, run_name="__main__")\n' % str(TEETH)
    result = _run(version, source)
    assert result.returncode == 2, (result.returncode, result.stdout, result.stderr)
    assert result.stdout.splitlines() == [PREFIX + label], result.stdout
    assert 'GATE_IMPORT_REACHED' not in result.stderr, result.stderr


@lru_cache(maxsize=None)
def _global_events(version):
    """Discover single event bits actually accepted in a global mask, not a list."""
    import json
    source = '''import json, sys
mon = sys.monitoring
mon.use_tool_id(0, 'global mask discovery')
accepted, rejected = [], []
for name in sorted(dir(mon.events)):
    bit = getattr(mon.events, name)
    if type(bit) is not int or bit <= 0 or bit & (bit - 1):
        continue
    try:
        mon.set_events(0, bit)
    except ValueError:
        rejected.append((name, bit))
    else:
        mask = mon.get_events(0)
        assert mask > 0, (name, bit, mask)
        accepted.append((name, bit, mask))
    finally:
        mon.set_events(0, 0)
mon.free_tool_id(0)
print(json.dumps({'accepted': accepted, 'rejected': rejected}))
'''
    result = _run(version, source)
    assert result.returncode == 0, (result.returncode, result.stdout, result.stderr)
    measured = json.loads(result.stdout)
    assert ('PY_START', 1, 1) in [tuple(item) for item in measured['accepted']], measured
    assert any(name == 'CALL' for name, bit, mask in measured['accepted']), measured
    print('python%s global event discovery: %s' % (version, result.stdout.strip()))
    return measured['accepted']


@pytest.mark.parametrize('version', ['3.12', '3.14'])
@pytest.mark.parametrize('tool', range(6))
def test_preflight_freed_but_armed_tool_exits_two_before_gate_import(version, tool):
    """Every accepted global event bit × tools 0–5; each cell is a fresh process."""
    failures, cleared, retained = [], [], []
    for name, event, installed_mask in _global_events(version):
        source = '''import sys, runpy
class NoGate:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'guard' or fullname.startswith('guard.'):
            raise RuntimeError('GATE_IMPORT_REACHED: preflight did not stop')
sys.meta_path.insert(0, NoGate())
mon = sys.monitoring
'''
        source += 'tool, event, installed_mask = %r, %r, %r\n' % (tool, event, installed_mask)
        source += '''mon.use_tool_id(tool, 'freed-but-armed preflight regression')
mon.register_callback(tool, event, lambda *args: None)
mon.set_events(tool, event)
assert mon.get_events(tool) == installed_mask
mon.free_tool_id(tool)
assert mon.get_tool(tool) is None
mask = mon.get_events(tool)
assert mask in (0, installed_mask), mask
print('free_tool_id measurement: tool=%d event=%d owner=None mask=%d' % (tool, event, mask),
      file=sys.stderr, flush=True)
if mask == 0:
    sys.exit(77)
'''
        source += 'runpy.run_path(%r, run_name="__main__")\n' % str(TEETH)
        result = _run(version, source)
        measurement = 'free_tool_id measurement: tool=%d event=%d owner=None mask=' % (tool, event)
        if result.returncode == 77:
            assert result.stdout == '', result.stdout
            assert result.stderr.splitlines() == [measurement + '0'], result.stderr
            cleared.append(name)
        elif (result.returncode == 2 and result.stdout.splitlines() ==
              [PREFIX + 'sys.monitoring tool %d' % tool] and
              result.stderr.splitlines() == [measurement + str(installed_mask)]):
            retained.append(name)
        else:
            failures.append((name, tool, result.returncode, result.stdout, result.stderr))
    assert not failures, ('freed-mask cells missed', failures)
    print('python%s tool=%d retained=%s cleared=%s' % (version, tool, retained, cleared))
    if cleared:
        assert not retained, ('mixed free_tool_id behavior requires per-event classification', retained, cleared)
        pytest.skip('python%s tool=%d: measured free_tool_id cleared every accepted global event mask '
                    '(get_tool=None, get_events=0); freed-but-armed cases unavailable: %s'
                    % (version, tool, ','.join(cleared)))


def test_floor_direct_entry_exits_two():
    result = _run('3.11', 'import runpy; runpy.run_path(%r, run_name="__main__")' % str(TEETH))
    assert result.returncode == 2, (result.returncode, result.stdout, result.stderr)
    assert result.stdout.splitlines() == [FLOOR], result.stdout


def test_floor_import_raises():
    source = '''import importlib.util
spec = importlib.util.spec_from_file_location('floor_teeth', %r)
module = importlib.util.module_from_spec(spec)
try:
    spec.loader.exec_module(module)
except RuntimeError as exc:
    assert str(exc) == %r, str(exc)
    print('floor import: RuntimeError: ' + str(exc))
else:
    raise AssertionError('floor import was accepted')
''' % (str(TEETH), FLOOR)
    result = _run('3.11', source)
    assert result.returncode == 0, (result.returncode, result.stdout, result.stderr)
    assert result.stdout.splitlines() == ['floor import: RuntimeError: ' + FLOOR]


def _pytest_probe(tmp_path, *, setup, continued, hook, failure=False, collection_error=False):
    # Copy the actual hook, not a reimplementation. The disposable root also
    # gives its sink fixture an isolated inventory when running ordinary tests.
    root = tmp_path / 'probe'
    tests = root / 'guard' / 'tests'
    tests.mkdir(parents=True)
    shutil.copyfile(CONFTEST, tests / 'conftest.py')
    if setup:
        load = 'import runpy\nrunpy.run_path(%r)\n' % str(TEETH)
        if collection_error:
            body = load
        else:
            # Catch the import refusal to distinguish the hold from pytest's
            # native collection-error exit 2, even without --continue... .
            body = ('import runpy\ntry:\n    runpy.run_path(%r)\n'
                    'except RuntimeError as exc:\n    reason = str(exc)\n'
                    'else:\n    reason = "preflight unexpectedly accepted"\n'
                    'def test_refused():\n    assert False, reason\n') % str(TEETH)
    else:
        body = 'def test_ordinary():\n    assert %r\n' % (not failure)
    (tests / 'test_probe.py').write_text(body, encoding='utf-8')
    args = [str(tests), '-q', '-s', '--color=no', '--confcutdir=' + str(root)]
    if continued:
        args.append('--continue-on-collection-errors')
    if not hook:
        args.append('--noconftest')
    source = _child_pytest_import()
    if setup:
        source += setup + '\n'
    source += 'sys.exit(pytest.main(%r))\n' % args
    result = _run('3.14', source)
    _skip_unavailable_child_pytest(result)
    _assert_pytest_ran(result)
    return result


def _assert_pytest_ran(result):
    # Exit 1 alone is also an import/launcher failure. Require the child's own
    # terminal summary, including collection errors, before interpreting status.
    # Pytest uses SGR escapes for colour; retain the raw streams in diagnostics.
    plain = re.sub(r'\x1b\[[0-9;]*m', '', result.stdout)
    duration = r'-?\d+(?:\.\d+)?s(?: \((?:\d+ days?, )?\d+:\d{2}:\d{2}\))?'
    count = (r'(?:[1-9]\d* (?:passed|failed|skipped|deselected|xfailed|xpassed|'
             r'subtests passed|subtests failed|subtests skipped)|'
             r'1 (?:error|warning)|(?:[2-9]|[1-9]\d+) (?:errors|warnings))')
    pattern = re.compile(r'(?:=+ )?(?:no tests ran|' + count + r'(?:, ' + count +
                         r')*) in ' + duration + r'(?: =+)?')
    summaries = [line for line in plain.splitlines() if pattern.fullmatch(line)]
    assert summaries, ('child pytest did not run: no recognized stdout summary',
                       result.returncode, result.stdout, result.stderr)
    assert any(re.search(r'(?:^|, | )([1-9]\d*) (?:passed|failed|errors?)(?:,| in)', line)
               for line in summaries), (
        'child pytest summary has no passed, failed or error count', summaries,
        result.returncode, result.stdout, result.stderr)



@pytest.mark.parametrize('failure', [False, True])
def test_pytest_probe_refuses_launcher_failure(failure):
    result = subprocess.CompletedProcess([], int(failure), '',
                                         "ModuleNotFoundError: No module named 'pytest'")
    with pytest.raises(AssertionError, match='child pytest did not run'):
        _assert_pytest_ran(result)
    result.stdout = '1 %s in 0.01s\n' % ('failed' if failure else 'passed')
    _assert_pytest_ran(result)


@pytest.mark.parametrize('summary', ['1 passed', '1 failed', '1 error', '2 errors'])
@pytest.mark.parametrize('colour', ['', '\x1b[31m', '\x1b[1;32m'])
def test_pytest_summary_colour_and_hollow_refusal(summary, colour):
    reset = '\x1b[0m' if colour else ''
    result = subprocess.CompletedProcess([], 1, colour + summary + reset +
                                         ' in 0.01s' + reset + '\n', '')
    _assert_pytest_ran(result)
    # A launcher can exit 1 with sys.exit([True]); this is no pytest receipt.
    result.stdout = colour + '[True]' + reset + '\n'
    result.stderr = '[True]\n'
    with pytest.raises(AssertionError, match='child pytest did not run'):
        _assert_pytest_ran(result)


@pytest.mark.parametrize('label,setup', SLOTS[:2] + SLOTS[-1:])
@pytest.mark.parametrize('continued', [False, True])
@pytest.mark.parametrize('hook', [True, False])
def test_conftest_holds_unmeasured_over_failure(tmp_path, continued, hook, label, setup):
    result = _pytest_probe(tmp_path, setup=setup, continued=continued, hook=hook)
    assert result.returncode == (2 if hook else 1), (
        result.returncode, result.stdout, result.stderr)
    assert PREFIX + label in result.stdout, result.stdout
    print('exit hold: continued=%s hook=%s rc=%s' % (continued, hook, result.returncode))


@pytest.mark.parametrize('label,setup', SLOTS[:2] + SLOTS[-1:])
@pytest.mark.parametrize('continued', [False, True])
def test_conftest_holds_actual_collection_refusal(tmp_path, continued, label, setup):
    result = _pytest_probe(tmp_path, setup=setup, continued=continued, hook=True,
                           collection_error=True)
    assert result.returncode == 2, (result.returncode, result.stdout, result.stderr)
    assert PREFIX + label in result.stdout, result.stdout


@pytest.mark.parametrize('failure', [False, True])
def test_conftest_preserves_ordinary_status(tmp_path, failure):
    result = _pytest_probe(tmp_path, setup=None, continued=False, hook=True, failure=failure)
    assert result.returncode == int(failure), (result.returncode, result.stdout, result.stderr)
    print('ordinary: failure=%s rc=%s' % (failure, result.returncode))


@pytest.mark.parametrize('output', [
    'tests/test_fixture.py::test_value[1 error in 0.01s]\n',
    'tests/test_fixture.py::test_value[2 errors] in 0.01s\n',
    'a docstring says 1 passed in 0.01s\n',
])
def test_pytest_summary_must_occupy_a_whole_stdout_line(output):
    result = subprocess.CompletedProcess([], 1, output, '')
    with pytest.raises(AssertionError, match='child pytest did not run'):
        _assert_pytest_ran(result)


@pytest.mark.parametrize('summary', [
    '1 passed, 2 warnings in 0.01s',
    '=== 1 failed, 2 errors in 0.01s ===',
])
def test_pytest_summary_accepts_compound_terminal_counts(summary):
    _assert_pytest_ran(subprocess.CompletedProcess([], 1, summary + '\n', ''))


@pytest.mark.parametrize('seconds', [-0.94, 0, 59.99, 72.34, 400000])
def test_pytest_stock_outcomes_and_duration(seconds):
    from _pytest.terminal import TerminalReporter, KNOWN_TYPES, format_session_duration
    reporter = object.__new__(TerminalReporter)
    reporter._get_main_color = lambda: ('green', KNOWN_TYPES)
    for kind in KNOWN_TYPES:
        reporter._get_reports_to_display = lambda key: [None] if key in ('passed', kind) else []
        parts, _ = reporter._build_normal_summary_stats_line()
        summary = ', '.join(text for text, _ in parts) + ' in ' + format_session_duration(seconds)
        _assert_pytest_ran(subprocess.CompletedProcess([], 0, 'BEFORE\n=== ' + summary + ' ===\nAFTER\n', ''))


@pytest.mark.parametrize('summary', ['1 skipped', '2 warnings', '3 deselected', '1 xfailed',
                                    '1 xpassed', '2 subtests passed', 'no tests ran'])
def test_pytest_summary_requires_execution_count(summary):
    result = subprocess.CompletedProcess([], 0, summary + ' in 0.01s\n', '')
    with pytest.raises(AssertionError, match='summary has no passed, failed or error count'):
        _assert_pytest_ran(result)
    result.stdout = '1 passed, 1 skipped in 0.01s\n'
    _assert_pytest_ran(result)


def test_pytest_stdout_only():
    with pytest.raises(AssertionError, match='no recognized stdout summary'):
        _assert_pytest_ran(subprocess.CompletedProcess([], 0, '', '1 passed in 0.01s\n'))


@pytest.mark.parametrize('options', ['-q', '-qqq', '-v', '--no-summary', '-k absent', '--bad-option'])
def test_probe_neutralizes_addopts(tmp_path, monkeypatch, options):
    monkeypatch.setenv('PYTEST_ADDOPTS', options)
    result = _pytest_probe(tmp_path, setup=None, continued=False, hook=True)
    assert result.returncode == 0


def test_pytest_real_teardown(tmp_path):
    (tmp_path / 'test_ok.py').write_text('def test_ok(): pass\n')
    (tmp_path / 'conftest.py').write_text(
        "def pytest_unconfigure(config):\n    print('TEARDOWN')\n")
    source = _child_pytest_import() + 'raise SystemExit(pytest.main(%r))' % [
        str(tmp_path), '-q', '--color=no', '--confcutdir=' + str(tmp_path)]
    result = _run('3.14', source)
    _skip_unavailable_child_pytest(result)
    assert result.returncode == 0 and result.stdout.rstrip().endswith('TEARDOWN')
    _assert_pytest_ran(result)


@pytest.mark.parametrize('summary', ['1 errors', '2 error', '1 passed, 1 warnings',
                                   '1 passed, 2 warning'])
def test_preflight_nonstock_plural_refused(summary):
    _assert_pytest_ran(subprocess.CompletedProcess([], 1, '1 error in 0.01s\n', ''))
    with pytest.raises(AssertionError, match='no recognized stdout summary'):
        _assert_pytest_ran(subprocess.CompletedProcess([], 1, summary + ' in 0.01s\n', ''))
