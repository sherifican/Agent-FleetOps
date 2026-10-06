# fleet_tui — Fleet-control TUI (AGENTS.md / DOX root)

## Purpose
A lightweight **Textual** MONITOR + INBOX + QUICK-CONTROL window over the EXISTING Fleet fleet stack.
It READS state files the fleet already produces + one control semaphore (`watchers.lock`). It is NOT
an orchestrator, NOT a code editor, NOT an intervention gateway.
Full plan: kept in the origin fleet's private notes; this file is self-contained.

⚠ **This file is not auto-ingested by every agent CLI.** An agent follows it when it is asked to read
it, or when a particular CLI is documented to load nested `AGENTS.md` files. Dropping it in `tui/` is
not on its own sufficient to make a coding agent obey it — check your CLI's behaviour rather than
assuming presence is enough.

## HARD contracts (never violate)
1. **Never crash on a missing/malformed state file.** A source returns `[]`/`unavailable`; the widget
   shows a muted "n/a" cell. Degrade panel-by-panel, never die. (This is the #1 trust requirement.)
2. **Sources are PURE + headless.** `sources/*.py` read files / shell-outs and return the dataclass
   records in `models.py`. **NO `textual` import in `sources/`.** They must be unit-testable with no
   GPU, no Textual, no live fleet — only the fixture files in `tests/fixtures/`.
3. **No model calls, no routing, no auto-actions on gated items.** The monitoring loop is all
   deterministic status reads (files, `/api/ps`, `systemctl`) — never an LLM. Controls = the focus
   semaphore (MVP) + (v2) one-key approve that runs the EXISTING gated path.
4. **Light:** Textual UI with explicit Rich rendering, pyte terminal emulation and
   textual-serve browser serving; `max_lines` cap on scroll logs; async `@work` for every shell-out;
   ANSI-strip all log text; the data refresh timer is **1 s** (`app.py:1263`) and every heavy probe is
   cached behind it — meminfo ~4 s, `/api/ps` 5 s, network ~20 s, `fleet-doctor` ≥ 30 s.
   Target < 50 MB RSS. Read cached state; never hammer `:11434` in a tight loop.
5. `models.py` is a **FROZEN interface** — do not change a field without updating every source AND
   widget that uses it (both sides compile against it).

## Layout
- `fleet_tui/models.py`   — dataclass CONTRACTS (frozen)
- `fleet_tui/sources/`    — pure readers including `boxes.py`, `receipts.py`, `throughput.py`, `lanes.py`, `downloads.py`, and `bg_agents.py`
- `fleet_tui/widgets/`    — Textual renderers (dumb; render records only)
- `fleet_tui/app.py`      — the App: layout, refresh timer, key bindings, focus-toggle write
- `fleet_tui/fleet_cli/`  — the `fleet` control-plane CLI (reuses `sources/*`; run via `~/.local/bin/fleet`).
  Verbs: status, targets, tail, route, feedback, preflight, summarize, digest, context, mode,
  presets, research, log, postmortem.
  Same HARD contract: never crash on bad state; every verb degrades to stderr + exit(2).
- `tests/fixtures/`       — deterministic state inputs; the crontab and job roster are synthetic
- `tests/test_*.py`       — hermetic gates for the sources (`pytest`). **Not one file per source**:
                            54 test files and 28 source modules (tracked direct
                            `tui/tests/test_*.py` and `tui/fleet_tui/sources/*.py`, including
                            sources `__init__.py`, counted with `git ls-files`); sources share files, and
                            a handful of UI modules need Textual installed to collect at all.
(Operating and troubleshooting notes live in `README.md` and `CHANGELOG.md`; there is no separate
operator-notes or build-plan file in this export.)

## State contracts — SHAPES are stable, PATHS are not
**Do not hardcode the paths below.** `fleet_tui/paths.py` resolves each key from `FLEET_TUI_<KEY>`
and then XDG JSON; copy `paths.example.json` and edit it. The list that follows is the *authors'*
instance, kept because it shows the shape each reader expects — never as a contract to code against.
Summary:
- **jobs**: `~/.hermes/cron/jobs.json` + `crontab -l` + `~/.hermes/cron/output/<name>/` + logs
- **inbox**: `curation_dir/.dep_update_trigger` · `.trigger` · `.github_action_alert` ·
  `curation_dir/HF_WATCH_DIGEST.md` · `curation_dir/CURATION_REJECTS_REVIEW.md`
- **health**: `fleet-doctor --json` + `GET http://localhost:11434/api/ps` + `systemctl --user is-active …`
- **focus**: `curation_dir/watchers.lock` — presence = ON. **Scope = `noisy`**
  (github-watch + harvester + curation-watcher). **Default = OFF** (no file).

## Verify-before-finish

Run the TUI suite as a non-root Linux user without `CAP_SYS_ADMIN` or
`CAP_SYS_RESOURCE`. Setup refuses root or either capability with the exact failure:
`Failed: TUI process isolation requires non-root without CAP_SYS_ADMIN/SYS_RESOURCE`.
This refusal preserves the kernel process boundary; do not bypass it to run the suite.

Each source ships with its `tests/test_*.py` GREEN (`.venv/bin/pytest tests/test_<x>.py`) before finish.
Never finish on a red or un-run test. On a real block after ≤2 targeted fixes, STOP and report the
blocker in one line (do not thrash) — the orchestrator fixes that module only.

## v4.0 box schema

`~/.fleet_tui/boxes.json` is optional. It accepts either a top-level list or `{ "boxes": [...] }`; each box requires `name` and `kind` (`local` or `remote`). Relay paths are local file paths and are read-only: `receipts_path`, `models_path`, `health_path`, `ledger_path`, `downloads_path`, and `throughput_path`. `device_labels` maps a relay device key to `{ "badge", "color", "power_cap_w" }`. Missing or malformed configuration returns one usable `local` box. Use `docs/boxes.example.json` as a neutral two-box dGPU/iGPU/eGPU example.

## Peer passback labels

Passback prose calls its sender the peer orchestrator. `PEER_BOX_LABEL = "peer orchestrator"` is the single source for displayed box labels and the modal title; passback paths, readers, and seen-state handling are unchanged.


## External path configuration

`fleet_tui/paths.py` owns external layout keys: environment then XDG-aware JSON,
otherwise `None`. Never introduce an author-layout fallback. The earlier State
contracts list describes only the authors' instance, now in `paths.example.json`;
copy and edit that example explicitly. Preserve each reader's safe default and guard
absent paths before I/O; controls refuse absent configuration. `panel_notices()` is
sampled during data gathering, not the fast paint loop. Source constants resolve at
startup, so configuration changes require restart. Headless verification includes
`tests/test_paths.py`; shared fixtures must not import Textual when it is absent.


## Review regression boundaries

Passback composition emits one notice only when passback keys are missing and no
empty Static when configured. `tests/test_passback_compose.py` executes that body
with headless widget stand-ins; Textual layout still requires UI verification.
Configured-reader controls use both environment and XDG JSON discovery, reload the
source, and call its no-argument reader. Playlist config/state/request paths remain
TUI-owned; only its optional notification uses external `curation_dir`.

UI test modules require Textual and Rich; do not report the full suite as executed
when those modules cannot import. Full-suite counts require those dependencies.


## Gathered notice contract

`tests/test_app.py::test_gather_data` pins `path_notices` in the gathered key set,
its dictionary shape, the five panel IDs with external path dependencies, and
string notice values. Keep this contract aligned when adding or removing such
panel dependencies; the notice mapping remains present when its strings are empty.


## Browser serving contract

`./serve.sh` launches `fleet_tui.serve` from this directory using `FLEET_TUI_PYTHON`,
the local venv when present, or `python3`. Dependencies must be installed first.
The server has no authentication and defaults to `127.0.0.1` (loopback only).
All-interface binding requires explicit `FLEET_TUI_SERVE_HOST=0.0.0.0`; restrict
ingress to trusted clients and add authentication before admitting untrusted ones.
Never describe host restrictions as provided or checked by this package.
`tests/test_serve.py` checks resolved defaults and explicit overrides headlessly;
its server stand-in never opens a socket.


## Roster configuration

The bridge `host_label` in `codex_link.json` defaults to `peer`; probing and the
`enabled` switch are unchanged. `FLEET_TUI_SERVICES` is a JSON list of service
unit names (`[]` disables probes); malformed nonempty values retain the labelled
authors' instance defaults. `FLEET_TUI_CLOUD_MARKERS` is a comma-separated
classification roster. The shared unset/empty-value contract is documented in
`README.md` under Roster configuration; keep both readers aligned with it. Cloud process names and display
aliases are labelled authors' instance constants. Unknown profiles keep their
observed name, and absent model/profile data displays only the provider label.
Restart after changing the cloud roster. Headless gates cover neutral bridge
labels, configured services (including empty-string disabling), environment-reload
classification overrides and unknown profiles. Configuration tests restore their
incoming environment and module roster after teardown.


## Orchestrator copy

Runtime handoff, pending-action and gate descriptions use the orchestrator role.
Provider names in model IDs, CLI detection and worker labels remain product
identifiers; never rewrite them as roles. Skills retain their adopting-team voice.


## Technical provenance

Comments and specs state detection and verification contracts without private
report filenames or dated approval stories. Preserve timestamps that are parser
fixtures or release versions; those are data, not operating instructions.


## Specification voice

`guard/voice_check.py` covers both nested specification directories. Specs use
impersonal prose or the adopting team; the skills remain outside that scope.
The scope tests plant plural prose in each nested directory before checking its
neutral counterpart, so an unread directory cannot produce an unearned pass.


## Job fixtures

`tests/fixtures/crontab.txt` and `jobs.json` contain obviously synthetic names,
IDs and paths. Preserve cron command forms and JSON field/type coverage when
changing them; never replace them with live roster dumps.


## Product title

The application title, exported screenshot title, browser server title and launcher
copy use `Fleet TUI`. The serving regression gate asserts that title when constructing
the server; keep these public surfaces aligned.


## Direct dependency inventory

Both `pyproject.toml` and `requirements.txt` declare `rich` for direct rendering
imports. `guard/tests/test_doc_count_drift.py` checks unguarded absolute import
roots across TUI Python sources against runtime/dev/requirements declarations.
Handled try bodies are optional by syntax; handlers, else and finally remain
required. Keep declarations aligned when adding third-party imports.

## Packaging verification

The root install recipe is `pip install -e 'tui[dev]'` in an isolated environment.
`pyproject.toml` uses setuptools PEP 517 with explicit `fleet_tui*` package
discovery; `specs*` and `tests*` are excluded. Keep the project metadata and
Python floor aligned with that recipe. `tests/test_packaging.py` checks the TOML
metadata from source without invoking pip or importing setuptools. Run that test
and the class-body import control in `guard/tests/test_doc_count_drift.py` for
source checks; verify an actual editable install separately in an isolated environment.
The build backend floor must support editable installs.
Keep the dev pytest floor and its packaging assertion aligned; pytest 8.4 applies
configured pythonpath before importing addopts plugins.

## Ratings failure fixture

`tests/test_ratings.py` uses a regular file as the parent of a requested log path
to force a write failure under both root and non-root users. Keep this fixture
inside `tmp_path`; a global nonexistent path or permission bits alone do not
establish an unwritable target when running as root.

## Process isolation test boundary

Permit windows share a locked process-wide count; the last exit restores soft
RLIMIT_NPROC zero. Join test threads before teardown. Threads predating setup
use patched Thread.start but lack the Python 3.11/3.12 profile observer; their
saved native aliases are kernel-refused outside permit windows. Recorded blocks
fail teardown unless an intentional negative control asserts and clears them.
Unaudited native calls inside permit windows remain outside this cooperative
boundary; audited calls on other threads are denied and recorded.
Higher-scope setup runs before this per-test boundary. Module-scoped fixtures
in `test_home_isolation.py` start the existing `sys.executable -I -S` child to
read `posixpath`'s code and one shared real pytest launcher
to observe configured startup isolation. That launcher invokes the declared
environment-input scenarios and the package/submodule preimport-refusal children.
Ordinary children use `sys.executable -B -m pytest`; preload children use
`-B -c` to inject an inert module before entering pytest through runpy, without
PYTHONPATH. Alphabet, structural, location and alphabet-refusal EnvironmentInput
children use `-B -c` and `pytest.main` with an early registration hook that seeds
inputs before configured subject import. In each successful launch form,
addopts imports tests._isolation first; the later command-line
`-p tests._startup_native` imports through ini pythonpath and snapshots native
state before later plugin hooks, including pytest_load_initial_conftests.
The observer carries that report; the parent requires finished subject import,
readline absence, duplicate rejection and exact input-oracle equality.
The helper uses no PYTHONPATH environment input. Matrix and empty-env
posixpath-code children use `-B`. The separate configured main-process scrub
control runs once for each bytecode configuration: none, `-B`,
PYTHONDONTWRITEBYTECODE=1, PYTHONPYCACHEPREFIX and `-X pycache_prefix`.
Each uses a fresh copied configured plugin package and pyproject in fixture
scratch; ordinary caches and prefix mirrors remain there. The probe checks
plugin registration without importing the subject and records actual flags
in a JSON report. Each configuration checks FLEET_ removal, PATH/SHELL reset
and shell-init removal through each property's own named pytest outcome. Every startup child (ordinary, alphabet, neutral-only, target, minimal and
preload) is launched with umask 000. Only successful children record their actual
umask in an observer report, which the parent asserts is 000; refusal children
have no observer report.
Every successful scenario requires 0700 on the root and all eight directories,
so the mask cannot hide requested group/other write bits.
All invocations run during module-scoped setup. These launches do not grant
process creation during ordinary function-scoped test execution.

## Stored string path inspection

Home-isolation controls decode str/bytes subclasses through base type slots.
Subclass attribute lookup, conversion, equality, hashing and iteration overrides
must remain uncalled; hostile-subclass positive controls enforce this boundary.

The stored-path detector reads generator GC-visible storage on CPython 3.11,
3.12, 3.13 and 3.14: fast locals and cells, the 3.11/3.12 locals dictionary,
evaluation-stack values (including a yield-from target), the saved exception
instance, names and function storage, under numeric labels. It also reads the generator frame object's own GC referents: the trace
function; on 3.14, the extra-locals dictionary, legacy locals snapshot and
retained overwritten fast-local values, which the generator itself does not
reference. Extra-local detection is covered on 3.13 as well; a passing control
does not establish the 3.13 storage owner. GC referents can report
the same value more than once. It never reads `f_locals`, whose dict
synchronization can compare hostile keys on 3.11/3.12 and which is a proxy on
3.13/3.14. The generator's own frame and code are excluded by identity; code,
globals and future yields are not traversed.
Other interpreter implementations/versions report uninspectable generator state.
Coroutine and async-generator frame locals remain outside scope. Keep these
limits explicit in the docstring. Ordinary created and suspended generators
have controls whose planted locals have no default/closure fallback. A control
pins overwritten fast-local storage: the planted path must be found on
3.11, 3.12 and 3.14, and on 3.14 only under a frame-referent label. On 3.13,
PEP 667's `f_locals` write-through replaces the fast local and nothing retains
the old value; the control asserts no planted-path hits and no uninspectable finding rather than skipping.

Named source-directory limit: the detector skips resolved paths inside the
package's own source directory and, when `sys.pycache_prefix` is set, that
directory's bytecode mirror below the prefix. The test computes these
directories once from the package location before any scan, never from
scanned values, and calls no stored-object method to apply them. The
exemption is keyed on the package's `__file__`, never `__path__`: it covers a
regular package whose `__file__` is an exact absolute `str` (that file's
directory). A namespace package (`__file__` is `None`) gets no source directory
and a refused finding, and `__path__` entries outside the `__file__` directory
are not exempt. For such a regular package this keeps
import metadata (`__file__`, `__cached__`, `__spec__`, `__path__` and a
source-file loader's path) out of the findings when the checkout lives under
HOME, which is the ordinary adopter layout. A zip import is outside the
exemption: its archive path lies outside the source directory and is reported.
It is an exemption by location, not by name: a
path elsewhere under a user root is reported whatever attribute holds it,
including those import-metadata names. A path inside the source directory
stored as an ordinary constant is also exempt; shipped package data is not user
settings. Controls scan the package with the checkout's own directory as the
user root (zero findings required, with a positive control showing the
unexempted scan reports it) and keep planted paths under import-metadata names
and a subclass key found.

Named license-file limit: the detector also skips paths that resolve to
exactly one of the `license` helper's absolute candidate files. Every module
reaches that helper through `__builtins__`, and when Python is installed under
HOME (pyenv, uv, conda, asdf, a `$HOME/.local` prefix) its candidates lie
under HOME. site.py builds the helper from `sys._stdlib_dir` (or, when that is
unset, the directory of `os.__file__`) and stores six strings: `LICENSE.txt`
and `LICENSE` joined to that directory's parent, the directory itself and
`os.curdir`. The four absolute ones are exempt as exact resolved files; the two
relative ones (`./LICENSE.txt`, `./LICENSE`) are skipped by the detector's
absolute-path gate, not by the exemption. `tests/_isolation.py` joins and
resolves the four files once, in the snapshot it takes before `fleet_tui` is
imported (it asserts that), from `sys._stdlib_dir` and `os.__file__` as they
were then; the test's default exemption is that stored result. Package import
code rebinding those two locations, `os.pardir`, `os.path.join` or
`os.path.dirname`, or `Path.resolve` therefore cannot move it. The snapshot
does not cover code that runs before it (a venv `.pth` file, `sitecustomize` or
`usercustomize` can rebind `os.pardir`, `os.path.join`, `Path.resolve` or the
two locations before `tests/_isolation.py` reads them), the detector's own
scan-time operations (resolving and comparing scanned values), the
source-directory exemption above (computed when the test module is imported,
from the package's `__file__` and `sys.pycache_prefix`), code that edits the
snapshot's stored values, or filesystem state: the stored files are
`Path(...).resolve()` results taken when they are computed, and every scanned
absolute path is resolved before it is compared with them. If a candidate path
is a symlink at that point, its target is the exempt file, and the candidate's
own spelling, the target and every other path resolving to the target are not
reported. A symlink made there later does not change the stored files, so paths
resolving to its target are reported unless that target is itself a stored
file. The only exempt directories are the package source directory and, with
an absolute `sys.pycache_prefix`, its bytecode mirror below that prefix,
wherever they lie: the source directory inside the installation when the
checkout or the imported package lives there, the mirror wherever the prefix
names (for example under `~/.cache`). HOME, an XDG base directory
(`~/.config`, `~/.cache`, `~/.local/share`, `~/.local/state`, `~/.local/bin`),
the installation and the venv's own tree (`sys.prefix`, `sys.exec_prefix`,
wherever the venv lies, including inside the installation: a checkout under a
HOME or `~/.local` prefix, a venv under a conda base, a pyenv-virtualenv) are
exempt only where one of those exemptions reaches into them: the package
source directory (all of HOME when it resolves to HOME or above it), the
bytecode mirror, and the four license targets: `LICENSE.txt` and `LICENSE` in
the stdlib directory and in its parent, both taken after symlinks resolve, so a
linked stdlib's parent is its target's parent. With the installation's prefix
at HOME and the stdlib in `$HOME/lib/python3.12`, `$HOME/lib/LICENSE.txt` is
not reported and `$HOME/lib/settings.json` is; with the stdlib named
`$HOME/lib64/python3.12` but resolving to `$HOME/lib/python3.12` (through
`$HOME/lib64 -> lib`, as some installers and `python -m venv` make it, or
through `lib64/python3.12 -> ../lib/python3.12` in a real `lib64` directory),
`$HOME/lib/LICENSE.txt` is not reported, and in the second layout
`$HOME/lib64/LICENSE.txt` is.
The residual limit is the four candidates' resolved targets: a setting
stored at a path resolving to one of them, under any name and wherever the
target lies, is not reported; a sibling file, a path below one and any other
path in the installation outside the package source directory and the bytecode
mirror are. Named user-root limit: the user roots (`_ORIGINAL_USER_ROOTS` in
`tests/_isolation.py`) are the resolved HOME plus the resolved absolute entries
of the XDG variables that are set. A relative XDG value is never a root,
although `fleet_tui/paths.py` (`_read_config`) honours a relative
`XDG_CONFIG_HOME`, resolved against the working directory, when it reads its
config; that directory counts only when it lies under a root. A default
listed above that is not named by an absolute set variable counts only through
HOME: its variable is unset, relative or set to another directory (for example
`~/.config` without `XDG_CONFIG_HOME`), and always for `~/.local/bin`, which has
no XDG variable. If such a default resolves outside HOME, itself or through a
symlinked parent (for example `~/.local` a symlink outside HOME, which takes
`~/.local/share`, `~/.local/state` and `~/.local/bin` with it), it is not a
root, so a path under it resolves outside every root and is not reported.
Measured controls on Python 3.11, 3.12 and 3.14 show that the unexempted
package scan reaches exactly those four files and nothing else in the
installation. `__builtins__` and the helper type are never skipped by
name. Controls scan the package with
the resolved `sys.base_prefix` as the user root (zero findings; the unexempted
scan reports exactly the exempt files; paths planted in the installation and
beside the license files are reported), plant settings in HOME and every XDG
default for interpreters installed under `$HOME/.local`, HOME itself, uv, pyenv
and conda layouts, keep venv-tree paths reported (including a venv inside the
installation prefix, a real conda env where prefix, exec_prefix and base_prefix
are the env, and a separate exec_prefix with and without a venv, where
license-named plants go only under venv locations that are not the
installation), compare each venv location with the installation after
resolving both (prefix or exec_prefix given as a symlink to, or a `..`
spelling of, the installation, or base_prefix given as a symlink to it, is the
installation, so it gets no license-named plants), plant license names both at fixed spellings and at the
exempt files' own paths relative to the installation (covering a `lib64`
stdlib, including a venv whose `lib64` is a symlink to `lib`, and a
free-threaded `python3.14t` one), inject a symlinked stdlib
spelling and the `os.__file__` fallback, and rebind `sys`/`os` locations,
`os.pardir` and `os.path.join` before re-executing the test module. Controls
pin the working directory with `monkeypatch.chdir`, so they hold whatever
directory pytest runs from: relative strings (including the helper's
`./LICENSE.txt` and `./LICENSE`) that would resolve onto paths inside the scan
root are not reported. The exemption-source gate
(`type(value) is str and os.path.isabs(value)` in `_exact_location`) has a
normalised-AST pin and compiled-function binding comparisons in
`tests/test_home_isolation.py`. They detect an honest edit of `_isolation.py`
that changes its normalised form or a compiled function. The written whole-module
AST and its SHA-256 omit line/column numbers, empty lists and `None` fields;
docstring text and whitespace remain content. Comments, blank lines, redundant
parentheses and quote style preserving the AST are ignored. To edit the module
on purpose, review the diff and update the written form and SHA-256 with the
values the failing pin prints, in the same commit. The binding check compiles
the whole source module without executing it, then selects each top-level
function code object; this preserves the module symbol table for direct os calls.
Compiling an isolated def can select different call instructions. The gate must be a plain
function in the module namespace with no `__wrapped__`, defaults or closure and
its first line at the AST definition. Its compiled code and the other top-level
functions are compared across eight fields: `co_code`, `co_consts`, `co_names`,
`co_varnames`, `co_argcount`, `co_kwonlyargcount`, `co_posonlyargcount`, and
`co_flags`. A function-body `.pyc` disagreeing with the source is red in that
binding comparison. `co_filename` is not compared: pytest's assertion-rewriting
cache can retain the original compilation path after an honest checkout move.

Named globals (`type`, `str`, `os`), `os.path`, `os.fspath` and the license
snapshot have comparisons too. The callee check detects a parent-only change
of `posixpath.isabs` / `posixpath._get_sep`: their function globals must be
posixpath's and their four fields (`co_code`, `co_consts`, `co_names`,
`co_varnames`) must equal an `-I -S` child of `sys.executable` started at module
setup, after conftest import. It trusts `sys.executable` and cannot see a
replacement with identical bytecode.

Not checked: C-level changes (the interpreter, `posix`, the `str` type), or
patches of objects these comparisons do not name. The following limits also
remain: module-class overrides (`type(os)`, `type(posixpath)`), the callees'
`__builtins__`, module-level `.pyc` constants versus source, a swap undone before
the assertions run, or a forged `sys.executable`. Examples outside scope include
an `os.__class__` property, `posixpath.__class__.__getattribute__`, conftest
rebinding `posixpath.isabs` together with a forged executable, a gate swapped
during collection and restored before assertions, and a planted `.pyc` under
`__pycache__/`. An adversary that runs first and also rewrites these checks
defeats any in-process check; that case is for review.

The interpreter environment is assumed trusted and is outside both this suite
and diff review. An illustrative, not closed list includes site-packages
(`sitecustomize`, `usercustomize`, `.pth`), plugins named by `-p`,
`PYTEST_PLUGINS` or `PYTEST_ADDOPTS` from outside the tree, wrappers on PATH, and
bytecode caches (`__pycache__/` is gitignored, `tui/.gitignore:1`). Run the suite at optimization level zero, without `-O` or `-OO`. The
explicit `pytest.fail` refusal enforces this supported compilation mode.
Pytest-rewritten assertions remain active under plain `-O`; `-OO` strips
docstrings.

Behavioral startup controls exercise the configured plugin with planted
HOME/XDG, temporary-directory, FLEET_, PATH/SHELL and shell-initialization
inputs. FLEET_ removal, PATH/SHELL reset and shell-initialization removal no
longer rest only on the module pin and review. These controls cover their
named inputs, not interpreter provenance or arbitrary earlier-running code.
Regenerating a pin is not evidence that an isolation invariant was preserved;
run the behavioral controls and review the source diff as well.

A specification table runs the gate over the type partition (an exact `str`, a
`str` subclass, `bytes`, `bytearray`, an `os.PathLike` over `str` and over
`bytes`, `None`) and the POSIX prefix partition (`''`, `/`, `//`, `/a`, `a`,
`a/`, ` /a`, `./a`), each row with its written return value and `refused`
list; the `str` subclass and `os.PathLike` rows record the calls made on the
value (attribute lookups and a listed set of special methods) and require none.
A generated corpus checks the gate's behaviour as a property, not a list of
spellings: every exact absolute `str` is
returned unchanged with no finding, and every other value (a `str` that is not
absolute, a `str` subclass, a non-`str`) is refused with its named finding. The
oracle is POSIX's rule written out (a `str` starting with `/`), never
`os.path.isabs`; the suite is POSIX-only. The corpus holds every string of
length 0 to 4 over `. / ~ $ \ a <space> : {` and tab, one to three path segments
(`''`, `.`, `..`, `~`, `~root`, `$HOME`, `${HOME}`, `%HOME%`, `python3`,
`os.py`, `__init__.py`, `C:`, a space) joined by `/`, `//`, `/./` or `\`, each
also with a leading and with a trailing space, and the spellings of the
cwd-pinned rows. Its character classes are generated whole from their
definitions over every code point with the running interpreter's Unicode
database, never sampled: every `str.isspace()` character (29), category Cc (65:
C0, DEL and C1), category Cf (163 on Unicode 14.0.0, Python 3.11; 170 on 15.0.0
and 16.0.0, Python 3.12 and 3.14), invisible and blank characters by name (a
whole word SPACE, BLANK, FILLER, INVISIBLE or INHERENT, or VARIATION SELECTOR,
GRAPHEME JOINER or ZERO WIDTH: 303, or 306), slash look-alikes (an NFKC form
containing `/` or a name containing SOLIDUS or SLASH: 51), quotation marks
(categories Pi and Pf, or a name containing QUOTATION or APOSTROPHE: 51), astral
code points (U+10000, U+1F600, U+E0001, U+10FFFF and, for each astral plane with
an assigned code point, planes 1, 2, 3, 14, 15 and 16, its first and last code
point and its first and last assigned one: 20), ASCII punctuation (32), the
surrogate endpoints U+D800, U+DBFF, U+DC00 and U+DFFF with U+DC80 and U+DCFF,
and three sampled characters the planted rows use (an accented letter, U+0301
and a fullwidth letter). Unicode 14.0.0 lacks seven Cf characters
(U+13439-U+1343F) and three named ones (U+11F48, U+13441, U+13442) and ends
plane 3 at U+3134A rather than U+323AF; 15.0.0 and 16.0.0 give identical
classes. Each class character is placed at the start (alone, before a relative
rest, before an absolute rest), right after a leading `/`, inside a segment,
after an inner `/` and at the end of short relative and absolute values, in the
same positions in a 335-character relative and a 336-character absolute value,
before the 8,200-character absolute value (so at the start of an
8,201-character value whose rest is absolute), and inside a segment of the
8,199-character relative and the 8,200-character absolute value. No class
character starts a relative value longer than 336 characters. Every surrogate
(U+D800-U+DFFF)
is placed at the start (alone, before `a`, before `/a`) and right after a
leading `/`. The length boundaries 255, 256, 1023, 1024, 4095, 4096, 4097 and
8192 appear as absolute and relative values, each as one component and as
seven-letter segments. Long values (longer than 300 characters, slash runs of 5
to 8, relative prefixes longer than four characters such as `./x/y/z/w` and
`../../../a`, a 256-character component inside a path) appear as written and
with a leading `/`. Non-`str` values are refused whatever they hold: a `str` and
a `bytes` subclass, `None`, `bool`, `int`, `float`, `complex`, `bytes`,
`bytearray`, `memoryview`, `tuple`, `list`, `dict`, `set`, `frozenset`,
`PurePosixPath`, `PureWindowsPath`, `Path` and another `os.PathLike`, each over
an absolute and a relative content where it has one (`Path('/a')` and
`Path('a')` among them). The oracle reads only the first character, so a value
starting with a slash look-alike or a lone surrogate is relative and refused,
and `/` followed by either is absolute and returned unchanged. A census pins
each generated class by its size and the SHA-256 of its sorted code points,
written out per Unicode database version (on a database with no written
census, such as Unicode 15.1.0 on Python 3.13, the Unicode classes' census
skips with a reason naming the version; the other checks still run), and the
members the planted rows depend
on; it compares the other generator inputs (alphabet, segments, separators,
placements, long values, length boundaries) with sets written out in the test,
not rebuilt from the generators, the exhaustive part with the product over the
written-out alphabet, and the non-`str` values with a written list by exact type
and content. The written-out sets are compared by size and membership: a
dropped, substituted or duplicated member fails it, and reordering one of those
inputs does not. The non-`str` values are compared in order, so swapping two
`_CORPUS_NON_STR` entries fails it. A class definition change fails it when it
changes a class's members. The census covers exactly those class sets and
written-out inputs: it does not pin how `_grammar_corpus` combines segments and
separators or how `_gate_corpus` assembles the parts (it only checks that some
written-out values are members), so a test edit there passes it. A table of
planted wrong gates (widenings and narrowings), each with a hand-written
witness in the corpus, shows the corpus check catches each one, and widening
`os.path.isabs` itself is caught too; the `leading-inner-surrogate` row is a
leading check (`v[:1]`) of a surrogate from inside the range. The corpus is a
behavioural check over its stated classes and cannot exclude an edit keyed on
a property it does not list. Edits of that kind that pass it include a first
character that is an ASCII digit or in category Lt, Mc, Nl or No; a class
character right before an inner `/`, or a directory component made only of Cf
characters; `\r\n` in the value; a valid surrogate pair; a class character at
the start of a relative value longer than 336 characters; a gate that calls
`repr(value)`; slash confusables outside the generated class (UTS #39); and one
keyed on a literal the corpus does not contain (for example
`isabs(value.removeprefix('file://'))` or `value.startswith('lib')`), on a
length above the longest corpus value (8,201 characters), on a character at a
length above the longest value holding it in that position (class characters
other than `/` end values of at most 337 characters, while `/` also ends
length-boundary values of 1,024, 4,096, 4,097 and 8,192 characters; so
refusing an absolute value longer than 400 characters that ends in a space
passes), or on working-directory state. The pin, not the corpus, is what fails
on such edits of `_isolation.py`. Also accepting a value when
`os.path.isdir(os.path.join(value, 'lib'))`, for example, is caught only when a
relative corpus value, joined to the working directory, names a directory
holding `lib`. The corpus's `..` chains reach at most three levels up
(`../../..`), so that depends on the depth of the working directory and its
ancestors, not on `tui/` versus the repository root: measured, it fails from
`/` (where `.` holds `lib`), from `/tmp/x/tui` and from `/tmp/x` (where
`../../..` or `../..` names `/`), and passes from the root of a checkout 14
levels deep and from a `tui/` whose directory and three nearest ancestors hold
no `lib`. Passing from `tui/` depends on the checkout's depth: a `tui/` three
components under `/`, such as `/tmp/x/tui`, fails. The segment
and character-class parts also run through the real call sites (stdlib,
`os.__file__`, package `__file__`)
with the working directory and `HOME` at the test's HOME: every non-absolute
value gives no files or directories and exactly its named finding, except an
empty `sys._stdlib_dir`, which is not a location: as in site.py it selects the
`os.__file__` fallback (unset there), so it gives nothing and no finding.
Cwd-pinned rows per
spelling class (bare, `./`-anchored, `..`, `~`, a `/.` or `/./` segment, a
leading `$`) also show the `$HOME/LICENSE` or package-directory plant each
refused location would exempt is still reported.
The venv-tree check compares both the resolved plants and the reported
spellings, and a control shows its precondition refusing plants that resolve
to exempt files (a venv whose `lib` is a symlink to the installation's).

Exemption locations are used only when they are exact absolute `str`
(`__file__` read from the package namespace by exact name, the snapshot's
`sys._stdlib_dir` or `os.__file__`) or, for `sys.pycache_prefix`, `None` or an
exact `str`; `sys._stdlib_dir` may also be `None` or empty, which selects the
`os.__file__` fallback as in site.py. A relative pycache prefix needs no mirror
because its cache paths are relative and never resolved. Any other value is not
used and is reported as `<uninspectable exemption source>`; no method of it is
called. Controls pass a non-exact `__file__` on a scratch module, non-str and
str-subclass prefixes and license locations, and check that no method is called.

New thread run methods (including subclass overrides) wait for all overlapping
permit windows to close and soft zero to be restored before entering targets.
Do not wait for a newly started target while deliberately holding a permit window.
Later windows can overlap already running targets; the cooperative limitation remains.

Process fixture setup mutations belong inside its restoration try/finally. Cleanup
attempts both profile restorations and the recoverable process limit, clears global
state, then reports errors, including an irreversibly lowered hard limit.

Stored-path controls include Unicode arrays with typecode w when the interpreter
provides it.

Collection now keeps the Unicode array w case on every supported interpreter;
unavailable typecodes skip during execution. Deprecated u remains covered with a scoped warning
filter only for that construction.


Dispatch permits accept only exact builtin argv strings and exact int/None
option values, with exact bool additionally admitted for close_fds and
start_new_session. Stdio bool values are refused. File-like streams, numeric subclasses and text/encoding/errors
options are refused before entering the permit; real dispatch uses DEVNULL and
start_new_session=True. Do not extend that seam to user callbacks or codecs.


String-subclass inspection also traverses genuine stored dict/slot descriptors
following base-slot decoding. It bypasses generic isinstance dispatch so hostile
__class__ properties remain uncalled; cycle and depth bounds still apply.


Permit windows bind to a fixture generation. Cleanup fails on leaked windows,
resets the count and permissions, and invalidates late exits so they cannot
consume the next test's count. The next fixture establishes soft zero before
starting threads. Teardown still restores the incoming process limit.


Current collection is 1179 TUI cases on Python 3.14. Python 3.11/3.12 are
expected to collect the same cases; execution at this revision is unmeasured. Python 3.13 execution at this revision is
unmeasured. Python 3.11/3.12 skip the unavailable array w case; Python 3.14 executes both the array w
case and the Unicode-class census. Baseline Python 3.13 executes array w
and skips the unwritten Unicode 15.1.0 census. Published combined counts
include collected guard cases, not a claim that every guard ran in this
implementation dispatch.

Dispatch argv is snapshotted once immediately after the exact container type
check. Element validation, fixture admission and execution use only that tuple;
the caller's mutable list is never reread. Keyword storage is call-local.


Dispatch permission binds the snapshotted argv, resolved executable path and
singleton native executable list, exact cwd, and sanitized env before the window.
Normalization accepts only exact builtins: str/bytes become filesystem bytes,
list/tuple vectors become tuples, and dict or K=V vector environments become
sorted byte pairs (duplicate keys refused). Popen and fork_exec.call compare all
four fields. Native fork_exec (3.14) compares executables/argv/env and requires
the active wrapper, which checked cwd. posix_spawn and PTY exec compare
path/argv/env and check inherited cwd at admission. Mismatches are denied and
recorded. Every stage is one-shot; native stages require Popen consumption first.
fork_exec.call discards posix_spawn; a native fork_exec or posix_spawn event
discards both native events and fork_exec.call. Python 3.11/3.12 have no native
fork_exec audit event; their wrapper and profile observer remain in use.
Named limit — unaudited native starts and harness tampering: native code/raw
syscalls without audit events during raised RLIMIT_NPROC, and tests calling
`_permit_process` or replacing private globals, remain outside this cooperative
boundary. Neither event recording nor kernel refusal claims a hostile sandbox.
Executable contents/inodes and filesystem changes after resolution are not bound.
For inherited cwd, a concurrent chdir after admission remains outside the check.
An identical in-window start can consume the allowance; caller identity is not
authenticated, and the subsequent identical start is denied and recorded.

Every event admission checks the latch's fixture generation, including worker
threads. A stale window grants no events even while another fixture raises the
process limit; its attempted starts are recorded against the current fixture.


Stored str/bytes-subclass attribute inspection uses type's MRO/class-dictionary
descriptors directly. Only exact instance dicts are iterated, with `dict.items`;
other dictionaries produce an explicit uninspectable finding. When inspecting
an instance, exact-str class attributes are not traversed except builtin storage
descriptors; non-str class entries are flagged and their values inspected.
A stored class itself, including a str/bytes subclass, has its body read only
when `__module__` is exact str equal to the inspected module name. A class whose
`__module__` is a str subclass or other non-None non-exact-str value is reported
uninspectable and its body is not read. An absent/None owner skips the body
without a finding. Path subclasses are not decoded and
can be missed on Python 3.11; exact pathlib types remain covered.
Shadowing properties and non-descriptor __dict__ attributes do not hide a usable
base storage descriptor. If dictionary storage has no usable descriptor, or a
builtin descriptor refuses the instance, report an explicit uninspectable entry.
Never call user descriptors; unset genuine slots remain empty, without a finding.

The Unicode array u control also stays collected and skips if that typecode is
unavailable; deprecation filtering applies only when constructing it.


Stored-name inspection iterates class namespaces and instance dictionaries;
never hash-look-up an inspected key. Only exact str names may be compared,
formatted or used to label findings. Every other key (including str subclasses)
gets a numbered `<non-str key #N>` label and an explicit uninspectable finding;
its stored value is still inspected, including a non-str class entry. Apply the
same name rule to function attributes and module globals. Generator GC references
and their cached mapping keys use numeric labels, like other mapping data.
Module/class ownership names must be exact str before comparison or formatting.
Builtin slots read partial/property/method-wrapper and container storage;
class-based dispatch bypasses user __class__ and metaclass equality. General
mapping data keys are traversed as values under numeric labels, never formatted
as names. Mapping proxies are read only when GC exposes dict backing storage;
other mappings produce an explicit uninspectable finding. These are stored-state
checks with the existing eight-edge bound, not evaluation of computed settings.

Exact pathlib objects are reconstructed from an exact list of exact-str stored
components, snapshotted before validation. Use PurePath's builtin `_parts` slot
on 3.11 and `_raw_paths` on 3.12/3.14; never stringify the original or use its
parsing caches. Missing or non-exact component storage is explicitly uninspectable.
Reconstruction uses the matching pure flavour (the host's for exact PurePath and
Path), whose constructor never refuses the host OS. The other OS's concrete
class (WindowsPath on POSIX, PosixPath on Windows) is reported as an explicit
uninspectable finding without construction, and scanning continues. Only
absolute decoded paths are resolved; a non-absolute one, such as a Windows drive
path on POSIX, gives no resolution finding. Released buffers and path-resolution
failures also produce explicit findings at their read/resolve sites. Resolution
failures are NUL, I/O errors and, on 3.11/3.12, symlink loops; 3.13+ can resolve
a symlink loop without raising. Defaultdict subclasses expose their factory through
the builtin descriptor without instance lookup.

Named annotation limit on 3.14: while the builtin `__annotate__` slot is non-None,
neither lazy nor already realized `__annotations__` are read. The public descriptor
can evaluate annotations and has no public raw-cache discriminator. Function
`__dict__` remains covered. On 3.11/3.12 never look up `__annotate__` on the function
instance; those versions have no builtin descriptor shielding its dict keys.


Startup preload coverage is generated from every regular Python package/module
under fleet_tui, including nested names, plus inert future-namespace names.
Each child plants exactly one key before configured plugin loading. Near-name
and case-altered controls must reach the observer; narrowing or widening the
namespace guard must fail its corresponding behavioral control. Discovery
rejects unsupported package layouts instead of silently omitting them.
The module-scoped launcher persists its discovered names, individual child
receipts and elapsed times. Adding modules adds startup children automatically.
Dispatch grammar controls vary each literal command/option slot independently
through absolute, relative, case-altered and tail spellings, and cover command
words, stderr punctuation and all four suffix case variants. Parser-only rows
retain canonical siblings; they do not add process permits.


Startup root controls include every XDG key with existing, missing and dangling
absolute targets and an interior space. Root membership depends on absolute
spelling, not existence. Literal FLEET_ removal includes lowercase, mixed-case,
digit and punctuation suffixes. Shell-init neighbours, including SHELLOPTS,
BASHOPTS, PROMPT_COMMAND and BASH_FUNC_x%%, retain their exact values.
Scheme-prefixed exemption strings are refused and recorded at each source site.
The requirements and dev metadata both require pytest 8.4 or later.


Startup character witnesses enumerate every non-NUL ASCII environment value,
all Unicode whitespace, and letters/symbols from declared Latin-1, Greek,
Cyrillic, Currency and Dingbats ranges. Each root character and generated length
has a distinct interior target for every XDG key; separators are structural
controls. Open FLEET_ suffixes include the transportable character alphabet,
case mixtures, digits, punctuation, non-identifiers and generated length powers.
Immediately after the configured subject imports, before later plugin hooks and
with readline absent, the early Linux native environ report must equal the
explicit input minus only FLEET_ and the five shell-init keys, plus the exact
HOME/XDG/PATH/SHELL outputs; duplicate names are rejected before conversion.
At observer-module import, before pytest test-call bookkeeping, both late
os.environ snapshots must equal that oracle and each other, and the late native
snapshots must equal each other around the fixed probes. The native instrument reacquires the live pointer
at each observation, rejects duplicate names before mapping conversion, and
decodes with filesystem encoding and surrogateescape. It starts no process
and introduces no second interpreter locale coercion. It requires Linux libc's
exported environ symbol; its work is linear in the vector's entries and bytes. Locale,
timezone, terminal, pager/editor, function-style and multiline values expose
unrelated changes. PYTEST_VERSION and locale inputs are explicitly supplied.
Generated namespace character groups cross two depths; discovered modules keep
their individual children. Dispatch controls generate whitespace positions,
suffix case products and pathname content; scheme-source controls enumerate
scheme-name characters, delimiters, case products and lengths. Finite generated
corpora describe their enumerated domains, not every possible string predicate.


Direct dispatch type controls enumerate both container bases and generated
inheritance depths, including script str subclasses, with generated invalid
argv lengths. License controls enumerate the three resolution-error classes
and subclasses, and distinguish None/exact empty str fallback from generated
empty str subclasses at the actual license-source call site.


Startup value inputs declare a finite alphabet: empty, ASCII space/tab only,
multi-digit decimal "10", single digit "0", multi-block Unicode with NBSP,
non-NFC "e" plus U+0301, a value starting with FLEET_, and the padded
8,192-code-point extreme. Each of those eight environments sets every rich
input name to the same value: all planted FLEET_ names, the five shell-init
names, all eight HOME/XDG redirects, PATH/SHELL and every neutral preserved
name. A ninth environment supplies the actual target constants to redirects
and resets; other roles carry empty. A real TemporaryDirectory owner made in
bootstrap is transferred once to the subject's exact constructor call, which
restores the stdlib constructor and removes the bootstrap reference. After
hand-off the subject holds the only owner: no bootstrap global, closure or
container retains it. Dropping that owner therefore removes the target tree.
For each of the eight alphabet classes, a second environment plants the class
on every explicitly planted neutral preserved name while retaining the realistic
HOME, XDG, reset, shell-init, FLEET_ and transport inputs. The neutral partition
is the existing preserved-name set; TMPDIR/TEMP/TMP and loader/report controls
retain their realistic values here and are varied in the uniform cases. These neutral-only cases retain the earlier
empty/whitespace/extreme relational detections as a strict subset; they do not
claim relational-predicate coverage in general. The no-variable minimal
case remains. These eighteen axis children do not cross the 18 ordinary cases.
The early pytest registration hook seeds and reports exact input before the
configured isolation-plugin import, avoiding locale/version coercion and execve
size limits. The observer token lives in its file. Per scenario the oracle
separately checks the exact deleted-name set, each redirect/reset constant and
byte-identical preserved values, then checks the whole environment for additions
and all other differences. Children report each environment value's length and
SHA-256 of its UTF-8 surrogateescape bytes; the parent compares those digests
with independently expected values. Input JSON retains raw seeded inputs (the target case has PATH=''). Receipt fields
incoming, launch_env and preserved hold environment-value digests. Raw path
examples include expected_roots, report roots/root, namespace license and
interpreter snapshots, loader/file/cache/code paths, and finalizer arguments.
The rich receipt's home_link is its incoming HOME spelling. Receipt defpath is
os.defpath, also the incoming PATH in the target case. Receipt token is the
parent's token; STARTUP_REPORT and STARTUP_INPUT_REPORT bake that token into
their source, rather than reading the child's environment. PRELOAD_REPORT
reads os.environ['OBSERVER_TOKEN'] in the child. Stdout JSON retains these raw
fields, and input JSON retains raw inputs. These are examples, not an exhaustive
no-echo guarantee. No subject code is extracted or repaired after import.
Raw child stdout remains in each receipt. In memory, parsed report lines remain
structured observations, and terminal text is retained separately; decoded exact
strings may be shared without changing typed observations or removing checks.
HOME/XDG distinct-root controls use existing directories, like overlap controls.
Subject and borrowed module bindings require canonical ModuleType identity; the
Path class binding requires exact type, and the builtins binding and original
namespace storage require exact dict. Early and late environment mappings require
canonical os._Environ identity. Cached environment digests retain immutable tuples
and produce a fresh snapshot dictionary for every entry; all names are still read.
Original environment names require exact str before digesting or JSON transport;
the observer uses the same guarded mapping walker for both late snapshots.
Startup scenarios run serially, each with separate input files,
effective isolated HOME, observer receipt and absent probe root. Parent records retain mode order;
all scenarios and their checks remain present.
The license covering table names planted environment name, alphabet class,
active source branch, structural operation and required outcome; measured witness
properties and stored subject outputs support its cells. Other structural cells
remain in a separate table, with explicit mechanical exclusions.

Every successful observer reports every key of vars(tests._isolation), with
its type and comparable storage. Names must equal both the literal contract
and the parent's namespace, with an explicit comparator for every name. The
five assertion-rewriting globals are expected exactly when the parent's loaded
_isolation.__loader__ is pytest's AssertionRewritingHook; otherwise they are
absent. Their module identities and parent-value comparators remain explicit.
Parent and child use sys.executable. Interpreter flags (-B and the parent's
-O/-OO level) and pytest arguments (--assert with the parent's actual pytest
mode) are separate lists, checked after every bootstrap has rebuilt final argv.
Optimization and assertion rewriting are independent axes. Cache paths
(__cached__, spec.cached and backing _cached) compare directly because both
processes use the same optimization level. Optimized suite use remains refused.
Source pinning and its layout controls parse an unoptimized AST explicitly, so
CPython 3.14 does not strip their source docstrings when the parent uses -OO.

For functions, recursive code objects, the TemporaryDirectory owner, its
weakref.finalize and registry entry, loader and spec, the observer derives
per-instance field names from the exact type's own data descriptors plus
vars(obj), where present. Both this census and the observed field map must
equal a written literal for the interpreter version and loader kind. No field
without a named comparator passes. This covers those fields, not complete state.
Function builtins must be builtins.__dict__ in the child, globals must be the
module's dictionary, and closure contents compare as typed values. On 3.14,
annotations are never read while __annotate__ is non-None. Code fields compare
recursively without marshal: _co_code_adaptive has a named call-history
invariant with canonical co_code compared separately; deprecated co_lnotab is
read with its warning contained and compared as a line-table derivation.
Spec.cached is read before backing _cached and dictionary normalization.
Instance dictionaries duplicate their individually compared fields only after
checking each original stored value against the observed field: exact type
and object identity, or equal value for an exact canonical scalar type.
The cleanup callback requires exact FunctionType identity with the module's
_cleanup_test_directory and that function's module dictionary as globals,
before code access or callable equality. These contracts are mandatory reports.
Instance dictionaries and finalizer kwargs require exact dict type by identity.
Original census names and dictionary field keys require exact str before
filtering, sorting, comparison or JSON conversion. warn_message requires an
exact str key and exact str value before its owner-specific form comparison.
Location snapshots retain each original pair's tuple/list type before walking
its typed leaves. The peek kwargs must be the same object as registry kwargs;
that registry comparator supplies the exact dictionary, key and value checks.
Closure storage requires an exact tuple and exact CellType entries.
Registry entries require child-local identity with weakref.finalize._Info and
emit a mandatory exact_info result, checked independently of their field census.
Registry and owner weakrefs require exact weakref.ReferenceType, in addition
to their owner/reference identity checks. Typed value and probe-output dispatch
compare canonical type objects by identity, never by class-name strings or
class equality. Unknown storage is refused. Path comparisons cover the canonical
outer Path/PosixPath/WindowsPath class and its string value; they do not inspect
component storage inside pathlib. The imported-location snapshot
recognizes only the controller's explicit inert str-subclass input by exact
class identity, so import-time rejection can be tested without treating
arbitrary subclasses as supported namespace storage. SourceFileLoader name/path compare to the parent's.
The rewriting hook compares exact type, fnpats, _writing_pyc, this module's
_must_rewrite membership and _rewritten_names path; its other fields use the
named per-run structural invariants described under limit (a) below.

Stored _ORIGINAL_LICENSE_FILES and _ORIGINAL_INTERPRETER_LOCATIONS compare
full typed, ordered values with the parent's stored snapshots, without calling
the license helper again. _ORIGINAL_USER_ROOTS uses the scenario's expected
ordered roots; _TEST_ROOT uses the reported root; _IMPORT_ENV uses the exact
redirect map. The TemporaryDirectory owner has exact type and a string name
equal to report root; its exact weakref.finalize must be alive, point back to
that owner, call the exact module _cleanup_test_directory function, and carry
the root string as its sole argument. Keyword names and delete/ignore_errors
values follow the parent interpreter; warn_message must have the form embedding
this owner's repr. Python 3.11 lacks delete, so the parent keyword comparison
keeps that version correct. The registry's weakref must refer to this owner;
func additionally compares the cleanup callable's full code and module globals.
That wrapper revokes retirement before invoking tempfile's native cleanup.
Registry args/kwargs
follow the same root and warning-form rules, and atexit compares by value.
Registry index is an exact integer used only for per-process exit order; its
numeric value is not compared. This checks cleanup is armed when observed.
os/sys/tempfile and Path compare child-local module/class identities. Loop
residue compares _key to INPUTRC, _subdir to data-dirs, and _directory to
Path(root)/data-dirs. Loader/spec types require identity with their canonical
exported classes. All loader dunders retain explicit comparators, including
__builtins__ identity with builtins.__dict__. Unsupported storage fails.

At observer-module import, every successful child takes its namespace/field
report and post-isolation snapshots from both environment instruments, plus
canonical listings of _TEST_ROOT and the independently named scenario probe root,
runs the fixed literal probes for all three namespace functions under its own
environment, then takes fresh reports, both environment snapshots and both tree
listings at that same pytest bookkeeping stage. Each listing includes the root
entry or an explicit absent-root marker, followed by every descendant's relative
path, lstat type, mode and symlink target, without following symlinks. The literal
expected isolation tree is a 0700 root and eight empty 0700 directories: cache,
config, config-dirs, data, data-dirs, home, runtime and state. The probe root is
absent before and after each scenario. Its distinct path is carried into the
observer and receipt, used by both tree checks and probe-path normalization,
and never deleted or reset inside the observation window. Both pre/post listings
must equal those literals and each other. Both namespace reports compare to the contracts and
parent; the early post-subject/pre-hook native report equals the exact oracle,
with duplicates rejected and readline absent. Late mapping snapshots equal the
oracle and each other; late native snapshots equal each other around the probes. Probe results
compare to both a literal expected table and the parent's observations. The
_exact_location return and its ordered refusal findings are separate typed
output channels. Scratch paths normalize relative to the probe root; outside
paths remain absolute. Parent tracing, restricted to those three code objects,
checks reached return lines against their source AST Return statements. Removing
a probe is a negative coverage control. The declared probes parse/resolve
inert inputs. After the isolation module, observer imports, namespace report,
pre-probe environment snapshots and tree listings have run, the observer installs
an audit hook before calling the probes. During that remaining child lifetime it
refuses and records these events: subprocess.Popen, os.fork, os.posix_spawn,
os.system, pty.spawn, os.forkpty, os.exec and _posixsubprocess.fork_exec. The last
event is raised by the direct primitive on measured CPython 3.14, but not on
measured 3.11/3.12. Process creation or replacement through a primitive that
raises none of those events is not observed by this hook: a ctypes call to libc
fork, or direct _posixsubprocess.fork_exec on 3.11/3.12, are measured examples.
Native code can itself raise a refused event. Earlier import-time activity is
outside this hook's refusal window. The literal process-event census is checked
for consistency with the recorded measurements and the refusal list, excluding
non-process open and ctypes lookup/call events; this is not live-event emission
measurement in the consistency test.
Every successful axis, near-name and accepted-preload child reaches these
comparisons; refusal children still have no observer report.

Subject environment writes are on the shared module-level startup path, but
execution depends on input membership: the redirect/reset assignments execute
in successful scenarios, each shell-init pop is attempted, and FLEET_ deletion
executes only for present FLEET_ names (zero times in the minimal environment).
Preimport-refusal children stop at the top-of-module assert, before the module's
TemporaryDirectory is constructed. The parent checks the refusal diagnostic and
absence of an observer report, not refusal-side filesystem effects. A mutant that
evaluates the preimport predicate directly for mkdir mode 0777, and moves the
assertion after the redirect loop, survives the whole suite with its pin
regenerated. This variant adds no module-global flag (an added flag is now
caught by the literal name set). The finalizer still removes that tree before
the parent could stat it: refusal side effects remain a residual.
The module-level _TEST_DIRECTORY is distinct from the target-seam owner
transferred above. The parent does not stat the reported sandbox root after a
child exits. Detaching _TEST_DIRECTORY's finalizer and deleting that owner after
_TEST_ROOT is assigned now fails the literal namespace-name comparison while
the child exits zero, with its pin regenerated. Post-exit removal is still
unchecked: an armed finalizer at observation time does not prove it ran.
Refusal-side filesystem effects and post-exit removal are separate limits;
their composition has not been measured.

The startup value fault model targets per-variable predicates: one variable's
own value selects the behaviour and its witness lies in the declared role/alphabet
matrix. This is the design scope supported by mutation receipts, not a proven
exclusion of every predicate. A discriminatory per-variable mutant with such a
witness that survives is a suite bug. Cross-variable predicates remain residual
even when every operand value is in the matrix. Structural crossings also
exercise some relational inputs, without covering relational predicates in
general. Retaining FLEET_TUI_STALE_SECS when its value is "0" and PATH is not
"0" remains outside the uniform/neutral and structural rows' witnesses.
A module-global mode flag fails the literal name set. A transient FLEET_ mode
entry removed by startup has no lasting binding, but its resulting directory
modes are observed in each successful row.

The preserved-name domain is finite and explicitly built in _startup_observation:
the twelve neutral/near-prefix and shell-neighbour names; lowercase variants of
the five shell-init names; _EXTRA suffixes of the eight redirect and five
shell-init names; LANG/LC_ALL/LC_CTYPE/TZ/TERM/COLORTERM/PAGER/EDITOR/VISUAL,
MULTILINE_VALUE and PYTEST_VERSION; and the generated case/insertion/deletion
FLEET_ prefix neighbours ending KEEP; FLEET_- is filtered out because the
resulting name still starts with FLEET_. Uniform alphabet cases assign the value
to every key in env at the dict.fromkeys call: all rich FLEET_ names, the five
shell-init names, eight HOME/XDG redirects, PATH/SHELL, TMPDIR/TEMP/TMP,
PYTEST_DISABLE_PLUGIN_AUTOLOAD, PYTHONDONTWRITEBYTECODE, OBSERVER_TOKEN,
and this preserved-name set. Neutral-only cases vary just the preserved set. In _startup_report,
the 'preserved bytes' loop checks every other incoming nonremoved/nonreset name
for the values it actually has, and whole-environment equality checks additions
and all other differences. An unplanted name with no such value has no value-keyed
witness: removing NO_COLOR only when NO_COLOR == "0" survives the whole suite;
the same removal on planted TERM fails (both pins regenerated). NO_COLOR is not
added to the matrix; another literal would not close this name-domain limit.

Namespace observation has three further limits. (a) Process state outside the
namespace and the named finalizer-registry/rewriting-hook observations is not
observed if it changes no reported output. Registry weakref/func/args/kwargs/
atexit compare as above; index has the named exit_counter_exact_int invariant.
The hook's other-module _rewritten_names entries are per-collection path cache
state; only this module's path and string-key dictionary structure compare.
_must_rewrite compares this module's membership and string-set structure, not
other names. _basenames_to_check_rewrite uses pytest_initial_path_stems
(string-set structure): stems differ with initial collection paths.
_marked_for_rewrite_cache uses pytest_other_module_decisions (str-to-bool cache
structure), _session_paths_checked uses pytest_session_path_history (exact bool
type), config uses pytest_run_config (exact Config type), and session uses
pytest_run_session (exact Session type). Their values belong to separate pytest
runs and are not compared. Changes preserving these named invariants, including
an unrelated name added to _must_rewrite or the initial-path stem set, remain
outside the value comparison.
(b) In-process mimicry: comparisons read references inside the same child.
At module end, gated on TERM == "0", rebinding os to a ModuleType clone carrying
os.__dict__ and installing that same clone as sys.modules['os'] survives the
whole suite with its pin regenerated; binding and reference change together.
(c) Parent-versus-child value comparison detects child-only per-variable changes.
An unconditional change reaches the parent too: name changes are caught by the
literal name set, while value changes rely on the parent's own tests and the pin.
Observation covers the declared names, per-instance fields and probe-reachable
outputs within these limits. Fixed probe inputs run under the declared child
environments and compare return values, refusal findings, both environment
instruments and both listed trees. A name with no planted value has no
value-keyed witness; a name alone does not hide an otherwise observed side
effect. Effects only on inputs outside those fixed probes have no exercised
witness. Filesystem effects outside the two listed trees, or removed before
the next listing, remain unobserved. Native state equals the explicit oracle immediately after configured subject
imports, before later plugin hooks and with readline absent. At observer import,
the mapping oracle is retained and native pre/post equality is required around
the fixed probes. A native change persisting across those probe snapshots is
detected, including a call-time write with no new module global. A native change
after the early snapshot and before the pre-probe baseline, or after the
post-probe snapshot, is outside these checks if it leaves no other observed
namespace, mapping, tree or probe-output difference; so is a change made and
restored between the probe snapshots. Hooks, patched functions, threads and
atexit can act in those windows; their mechanism alone is not an exemption.
CI's setup-python 3.12 readline linkage is unmeasured here; host EditLine
3.11/3.12 receipts do not establish that linkage for other builds.
Other residuals are string predicates outside the matrix, relational or mixed
presence predicates, thresholds above 8,192 code points, ownership or ACL changes
that leave S_IMODE at 0700, and writes a later unconditional removal makes
equivalent. Input seeding observes the configured subject boundary, not earlier
interpreter activity.


A witness is one matrix row: one child's complete seeded environment, cwd,
preimported module identity, interpreter locations and filesystem inputs.
The structural coverage relation requires feasible (planted name, alphabet
class, structural operation, outcome) cells, derived from actual seeded values
and checked observer output or nonzero refusal exit plus its diagnostic.
Original uniform and neutral-only rows remain. Shared crossed rows retain
alphabet values on every non-anchor name, alternate XDG anchors, and keep
separate XDG self-value rows so no key loses its own alphabet witness. Each
class carries absolute and relative HOME directory/missing/symlink/dangling
properties and empty/unset HOME where compatible; absolute per-key XDG
entries with existing, missing and dangling targets, relative/empty entries,
ordered resolution and duplicate handling, including HOME-vs-XDG identical and
alias spellings with a distinct-root control. Import-time license-location
coverage is a covering table over planted environment name, alphabet class,
active source branch, structural operation and outcome. stdlib and fallback
inputs include None, exact empty, empty/nonempty str subclasses, falsy bytes/int,
exact absolute/relative and truthy non-str values. Accepted sources alone cross
plain/missing paths, selected-directory links to both same-parent and other-parent
targets, symlinked parents, dangling links, NUL resolution errors, a narrowly
targeted OSError resolver fault, and candidate collapse at root or file aliases.
The injected resolver is restored after subject import before other observations;
its actual exception is recorded. Symlink-loop RuntimeError rows apply on host
3.11/3.12; non-strict 3.13+ resolution does not raise for that input and is an
explicit exclusion. Fallback properties apply only when stdlib is None or exact
empty; rejected sources never reach resolution/filesystem/candidate operations.
Input types, sentinels, link spellings and targets, resolution results/exceptions,
ordered candidates, multiplicity and stored subject output supply the evidence;
case labels alone supply no coverage, and label/property disagreements fail. Fixed call-time
probes also exercise exact-str type/absolute spelling and argv container/script
types, lengths, prefix, quoting, suffix, containment and reconstructed text.
The coverage artifact groups cell witnesses by row and records each row's actual
input digests and properties; the run fails on an empty required cell.

Mechanical exclusions follow fixed input semantics: HOME's own alphabet values
are relative components or empty, so they cannot simultaneously be unset or
absolute; empty HOME cannot name a user-created link, and the 8,192-code-point
component exceeds NAME_MAX and cannot name a created directory or symlink.
An XDG key's own fixed alphabet value has no absolute entry or path separator;
appending one would change that value. Startup removes or resets FLEET_,
shell-init, redirect and PATH/SHELL names before probes, so their incoming
alphabet values cannot remain at that call stage. Import-time crossings retain
those values while testing the relevant structural operations. Compatible
names share rows. Refusal crossings use fleet_tui.paths; a predicate combining
another specific preimported identity with an alphabet value remains relational
and has no such combined witness, although each discovered identity retains
its original rich-environment refusal row.


Startup test scratch ownership and retention
------------------------------------------
The startup observer validates each scenario before retiring its compact input
and child pytest tree. A passing module releases its remaining scratch in
fixture teardown. Receipt digests stay in memory; raw-receipt consumers must
request `--keep-startup-artifacts`. Failed scenarios and failed modules retain
their evidence in unnumbered fleet-tui-startup-* directories beside pytest
basetemp, outside its numbered-directory rotation. Only the creating fixture
cleans successful startup evidence; later runs never retire these directories. Startup scenarios execute serially so a storage stop cannot
leave a second observer writing. ENOSPC, EIO, EROFS and detected short writes
stop further startup writes and cleanup, retaining partial evidence.

The early isolation plugin keeps `dir="/tmp"`. Immediately after allocating
its directory, it sets mode 01700 before creating `.owner` exclusively at mode
0600 with PID, Linux process start ticks, boot ID, PID-namespace device/inode,
helper PID/start ticks, a random capability and the format marker v3 (nine fields).
Owner and helper start ticks come from their own `/proc/self/stat`, even for
ancestor-mounted procfs; the helper sends its ticks before announcing readiness.
The sticky bit is a permanent retirement veto for intentionally retained
initialization evidence. It is cleared to 0700 only after all eight payload
directories initialize successfully. Every initialization detach leaves this
pre-armed veto in place, with no retention write attempted after an error.
If initial arming fails, no owner has yet been written, so retirement refuses
the missing record. An error while clearing the veto leaves initialization
evidence retained under the failed chmod operation. Retirement reports each
sticky tree as intentionally retained. SIGKILL after successful initialization
remains eligible for dead-owner retirement; SIGKILL during initialization can
leave retained evidence. The startup storage handler keeps its separate raw
evidence tree and detaches HOME cleanup too. Before publishing an owner, a
separate helper listens on a memory-only abstract Unix socket; its pidfd is
preallocated. Retention sends cancellation through its private socket pair, observes peer
closure (EOF, read directly or after the single ECONNRESET reported when the helper released
its last endpoint with the byte unread; a write's EPIPE or ECONNRESET still requires that EOF),
then reaps its exact pidfd-bound direct child when available. It performs no filesystem write after the error. Normal finalizer
cleanup also revokes first, so partial cleanup cannot qualify for a later retry.
If the helper is already revoked/lost, the finalizer retains the HOME tree.
A sweep storage error vetoes through the abstract socket without reading or
writing the damaged owner. Connection refusal/reset means that issuer is gone
or consumed; an unexpected IPC error propagates and is not a retention success.
No marker write is attempted on ENOSPC/EIO/EROFS. Failure to create a helper or
pidfd occurs before publishing the owner and retains the unqualified tree.
Import-time sibling retirement judges only nine-field v3 records in the same
namespace and boot, with a single local PID in procfs's NStgid view. Only an
absent PID or different start ticks in that verified local view, plus one grant
from the recorded live helper incarnation, permits removal. SO_PEERCRED names
the listen() caller, which also covers any other holder of that listener, so the
request binds a pidfd before its start-ticks check, reads the reply's
SCM_CREDENTIALS sender, and requires that sender to be the recorded PID while
the pidfd is still unexited; the helper stays live until the requester closes. That grant is
consumed before rmtree; every later sweep retains any partial remainder.
Missing, empty, malformed, unreadable, legacy three/five-field, foreign-namespace,
foreign-boot and ambiguous procfs-view records are reported and retained;
age is never evidence of death. Dead foreign or legacy runs may therefore remain.
New nine-field records also survive older three/five-field readers. Forked children
cannot revoke the live parent's helper or clean its tree through an inherited
finalizer. They are not independent owners: after the recorded parent dies they do not veto
retirement. The helper setsid survives an owner process-group SIGKILL; loss of
the helper or its PID namespace conservatively retains the tree. An ordinary
crash can leave a helper awaiting the next sweep, so this adds a process/descriptor
lifetime. Such a helper exits when a sweep with the same view of its root grants
(or vetoes) retirement, or when its tree is removed. A sweep that sees the tree at
another path, e.g. outside a sandbox with a private `/tmp`, hashes a different
socket address and cannot reach it; unless one of those happens or it is killed,
the helper's lifetime is unbounded. Normal exit and purposeful retention revoke/reap the helper. A matching
owner zombie remains until reaped. Behavioral tests cover
foreign/legacy live owners, import-time retirement, malformed numeric fields,
symlinks, plain files, PID reuse and an ancestor-mounted procfs simulation. The recheck before removal compares device, inode, mode, UID, GID, size,
mtime_ns, ctime_ns and owner bytes. It excludes access time, which reading
.owner may update, and reports live owners and every changed-identity skip.
It detects observed replacements; it is not an atomic defense against a concurrent
replacement after that recheck. Checks occur before and after requesting the
one-use grant. The owning process never rewrites its owner. Retirement stderr
names visible raw startup roots directly in the swept directory or its
pytest-of-* children as retained; it does not discover arbitrary external roots.
Retirement fixtures admit their deliberate helper fork once through the existing
process permission context; conftest's ordinary process gate is unchanged.
Helper setup redirects descriptors 0/1/2, not every unrelated inherited descriptor;
an extra pipe writer can remain in an abruptly orphaned helper. Trusted pidfd/IPC
availability is required for revocation: unexpected descriptor or transport failure
propagates and is not proof that a surviving issuer can never grant later.
Some Linux Python builds omit os.pidfd_open and signal.pidfd_send_signal despite
kernel support. The helper then calls the host libc pidfd functions through
ctypes with explicit signatures and errno handling; no dependency is installed.
Unavailable kernel/libc facilities fail before qualified owner publication.

When `fleet-data-path` is available on the incoming PATH, kept startup artifacts
use `fleet-data-path test-scratch-kept <run>`. An explicit
`--startup-artifacts-root` must agree with that helper and lie outside this
repository. Without the helper, --keep-startup-artifacts requires that explicit external
root and refuses before allocating startup evidence when it is absent. Pytest retention is `failed`, count 1; an explicit
`--basetemp` skips session-end base-directory removal and numbered-directory
rotation; under the `failed` policy, passing tests' `tmp_path` directories are
still removed. Its caller must bind receipts before removing its own temporary tree. No pre-existing evidence is cleanup input.

Retirement mutation controls also check noncanonical boot-ID spellings,
foreign directory and owner-file UIDs, an owner record before all eight payload
directories, and owner content/file/directory replacement during identity lookup.
Each behavioral control remains active when the module pin is regenerated.

The generated startup observer imports pytest inside the Config/Session
comparator branches; it does not inherit imports from the parent test module.

Startup regression boundaries
-----------------------------
Observer helper definitions and probe-return source locations are read from the
loaded module's current file, independently of cached code-object filenames.
A configured pytest main-process control covers all five declared bytecode
configurations, checking FLEET_ removal, PATH/SHELL reset and shell-init removal
in each. Local package caches and prefix mirrors stay in copied scratch; the
none configuration may write bytecode wherever the interpreter can write import
caches, including site-packages and the base-interpreter stdlib. Retirement controls check the helper's separate session, loss of its
pidfd-bound incarnation before finalization, replacement both during identity
lookup and after a one-use grant, and actual helper revocation on initialization
failure independently of the sticky retention veto. A passing whole suite leaves
exactly five fleet-tui-startup-* directories beside pytest basetemp, by design,
each holding deliberately retained retention evidence that no later run retires:
the storage-error tree (one child per late storage-fault row), the fault-matrix
tree (one child per phase/fault row), `doublefork-revocation`,
`exec-listener-revocation` and `unconsumed-cancellation` (one child per row).
The startup observer's directory is removed when its module passes.

Owner-write and short-write initialization controls check actual helper death
independently of the sticky tree veto. The child revokes/reaps its exact helper
in a finally before diagnostic assertions. The parent also revokes the recorded
helper incarnation in a finally when the child times out; PID/start ticks and
pidfd binding prevent signalling a reused PID. An orphan zombie awaits its new
parent's reaping and cannot grant retirement. The identity report is written
before injected storage failure, never as a retention write after that failure.
The declared Python 3.11 floor requires whole-file parse and collection with
a 3.11 interpreter. CI's python-floor job uses setup-python 3.11 to ast-parse
every tracked tui/**/*.py file, including modules not imported during collection,
and fails on any SyntaxError; it then installs TUI dependencies and collects
the suite. Newer-interpreter collection alone cannot establish the floor. Hosted job execution must be verified separately in CI.

Retention failure observations record each helper's PID and Linux start ticks
at its fork, independently of published control and module identity helpers.
A return-time positive control detects that live descendant. After failure,
the child reads /proc state before any test cleanup and requires the recorded
incarnation to be absent (dead and reaped), including during arm failure when
no control was published. Owner and payload rows require a control binding and
inject EIO, ENOSPC, EROFS, EDQUOT, ENOENT, ValueError, KeyboardInterrupt,
SystemExit, GeneratorExit and a custom direct BaseException;
arm rows use the same fault set. Short-write and clear-mode rows remain.
Cleanup uses the independently recorded descendant even on failing paths;
control descriptors serve cleanup only. Recorded-helper cleanup reads field 3
of /proc/<pid>/stat after the last ')' for zombie state; real zombie and live
controls distinguish exit without reaping from a surviving helper.

Finalizer-construction fault rows use the full initialization fault set and require
the bound helper to be revoked and reaped before cleanup, with no owner written.
They reject a callback swap moved outside its initialization handler. On failing
paths an unobserved native fork remains a test failure. Cleanup discovers helpers
by the child-controlled HOME and binds their identities to pidfds before signalling;
returned control descriptors are closed only after that replacement cleanup. A
fork-observed identity record remains a secondary parent fallback; it never supplies
the independent death oracle for an unobserved native fork.

Helper initialization and test cleanup
--------------------------------------
The parent's guarded startup window includes fork, descriptor closes, readiness
read/parse, pidfd acquisition and the last close before control publication.
Every injected phase carries all ten exception classes; a census rejects a
missing or duplicate fault row. The short-write return-value control is separate
from that exception-class matrix. Final clear-mode faults carry all ten classes.

Revocation uses a private socket pair rather than numeric PID/PGID signalling.
The stock helper consumes the cancellation byte then exits; a sole surviving
double-fork descendant uses the same endpoint. Peer EOF is observed before
successful revocation returns: EOF establishes channel closure, not arbitrary
descendant process death. Additional copied endpoints share the receive queue;
one byte is not a broadcast, and extra holders can delay EOF and make revocation
fail rather than return success. Startup failure closes the parent's extra peer
copy first; an unexpected close error propagates before cancellation. The control keeps its five-field shape, with a socket
instead of a command pipe. No retention operation writes the damaged tree.
With a pidfd, waitid reaps only that incarnation; before acquisition, the direct
child is reaped by waitpid. Concurrent external reaping is outside the latter
reap guarantee, but it cannot redirect any cancellation signal to a reused PID.
Peer closure is EOF on the owner's endpoint. A helper that fails before consuming
the cancellation byte (for example, its readiness write meets the parent's closed
pipe) makes Linux report ECONNRESET once and then EOF; that reset followed by an
immediately readable EOF is closure too. Any other channel error, received data,
or missing peer EOF propagates and retains the tree;
it is not reported as successful helper revocation. A deterministic row holds a
copied helper's readiness write until the byte is pending, for each phase that
fails before the readiness read.

Retention children, the parent timeout fallback and the double-fork control
discover live helpers as every same-UID process holding a descriptor for a
socket bound to the retirement address of a `fleet-tui-tests-*` root under the
child's base (inodes from `/proc/net/unix`, holders from `/proc/<pid>/fd`),
independently of fork API, function spelling, executable, environment and
published control; the unique HOME passed to that child is only a supplement.
Limits: a root no longer under the base is not addressed; zombies, processes of
another UID or network namespace, and a process that dropped every such socket
and replaced HOME are not observed (without the listener it cannot grant; one
still holding the cancellation endpoint delays EOF, so revocation fails).
Cleanup binds pidfds and rechecks ownership/start ticks. Death observations
precede cleanup and include both observed descendants and the scan. A control
forks a descendant with posix.fork that keeps the listener across execve with
HOME=/var/empty and answers GRANT; the double-fork control must fail on it, and
no bound listener may remain after that run. Finalizer-construction injection targets its callback; no
fault row relies on the retirement-start function name. A missed injection
fails after run-owned cleanup. A double-fork control reads live leftovers
after module revocation and before test cleanup. An orphan zombie cannot grant
retirement and remains the responsibility of its adopting parent to reap.
