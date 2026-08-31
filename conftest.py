"""Keep pytest out of the script-guards that live at `guard/` root.

`guard/tests/` holds this repo's pytest suite. The `test_*.py` files directly in
`guard/` are NOT pytest tests — they are standalone script-guards that
`guard/mutation_harness.py` runs one at a time via `subprocess`, reading their
stdout marker and exit status as the verdict. They therefore do their checking at
import time and call `sys.exit(1)` when a check fails.

Under pytest that is a collection-time crash: importing one to look for test
functions runs the whole guard, and a failing one raises SystemExit inside the
collector, which pytest reports as INTERNALERROR and abandons the entire run. A
bare `pytest` at the repository root collected nothing at all for that reason.

The ignore is a glob on location, not a hand-kept list of filenames, so a new
script-guard dropped next to the others is covered the day it lands rather than
the day someone remembers this file. The harness reaches them by path and is
unaffected by anything here.
"""

collect_ignore_glob = ["guard/test_*.py"]
