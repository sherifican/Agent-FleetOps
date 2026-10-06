"""Seam wrapper for guard/doc_count_drift.py's REAL command-line entry point.

Runs the module's own `__main__` block with the arguments it was given, so `--release` (or its
absence) reaches the real argv handling. Collection subprocesses are synthetic: the guard
count is copied from the banner solely to isolate CLI skip/release routing from count drift;
the TUI receipt says declared textual is absent. This does NOT measure either suite or prove
that the banner count is correct. No collection runs, so this routing fixture does not depend
on which test dependencies this machine happens to carry.
Everything else — the git-based inventory, the file-based instruments, the banner claims, the
report and exit code — is the real thing.

Invoked by the tests as `python3 <this file> [args...]`, and by the runner tests through a
python3 PATH shim that forwards the runner's own `guard/doc_count_drift.py` invocation here.
"""
import pathlib
import json
import re
import runpy
import subprocess
import sys

REAL = pathlib.Path(__file__).resolve().parents[2] / "doc_count_drift.py"

_real_run = subprocess.run


def _fake_run(cmd, *args, **kwargs):
    parts = [str(c) for c in cmd] if isinstance(cmd, (list, tuple)) else [str(cmd)]
    if "--collect-only" in parts:
        if parts[4] == str(pathlib.Path('guard') / 'tests'):
            banner = (REAL.parent.parent / 'docs' / 'banner.svg').read_text(encoding='utf-8')
            count = re.search(r'>\s*(\d+)\s+guard\b', banner)[1]
            pathlib.Path(parts[3]).write_text(json.dumps({
                'collected': int(count), 'deselected': 0,
                'collection_finished': True, 'failures': []}), encoding='utf-8')
            return subprocess.CompletedProcess(cmd, 0, stdout=f"{count} tests collected in 0.01s\n",
                                               stderr="")
        pathlib.Path(parts[3]).write_text(
            json.dumps({'collected': 0, 'deselected': 0, 'collection_finished': False,
                        'failures': [{'name': 'textual', 'absent': True}]}), encoding="utf-8")
        return subprocess.CompletedProcess(
            cmd, 2, stdout="ImportError: No module named 'textual'\n", stderr="")
    return _real_run(cmd, *args, **kwargs)


subprocess.run = _fake_run
sys.argv = [str(REAL)] + sys.argv[1:]
runpy.run_path(str(REAL), run_name="__main__")
