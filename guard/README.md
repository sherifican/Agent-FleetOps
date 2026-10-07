# guard/ — drift guards for a multi-stage research pipeline, mutation-proven

Adapted from a shipping desktop application's `_breaker/` verification stack. The transferable
part was the META-harness: machinery that keeps invariants honest, not the invariants themselves.

The runner's pytest step collects the entire guard tree unless `GUARD_RUNNER_NESTED`
is exactly `1`; every other step runs either way. The four live runner calls in the
outer suite set that marker; their child pytest step announces that it is narrowed
to `guard/tests/test_runner_nested_seam.py`
and rolls under the same `pytest guard/tests/` step name. The outer suite and the
workflow retain the full collection. Live runner calls have a 300-second deadline,
and shimmed runner calls have a 120-second deadline. A timeout sends SIGTERM to the
process group, allows five seconds to exit, then sends SIGKILL; the test fails with
the runner name and a bounded output tail. Every call, including one interrupted by an exception
such as Ctrl-C, ends by sending SIGKILL to the group; if the outer pytest is itself
killed, that cleanup does not run. pytest-timeout's `thread` method ends pytest with
`os._exit`, so no `finally` runs, while its default `signal` method on POSIX raises
inside the test and lets cleanup run. A child that leaves the group with setsid or
setpgid is out of its reach, so if it still holds the pipes the helper stops reading
them about ten seconds after the deadline.
These deadlines are per call. If every call hung, they would add up to more than an hour,
past the workflow step's 45-minute deadline; GitHub would then cancel the step instead of
each test failing by name with TIMEOUT, and the step would still end red.

## The discipline, in one paragraph

A green check proves nothing until the check has been watched failing. So the **teeth-prover runs
first** and plants real defects to confirm every guard can go red (`HAS_TEETH` / `OVERBROAD` /
`VACUOUS` verdicts). Exit codes everywhere: `0` clean · `1` violation · `2` UNMEASURED — and
**2 dominates 1**, because a check that did not run can hide any number of violations beneath it.
Until 2026-08-03 the leg-liveness dry-run wrote fabricated ALIVE state and reported a PASS; the
staleness check could never fire. That defect is why the dry run now returns `2` and says so.

## What runs from a fresh clone (no private data needed)

| Layer | Command | Expectation |
|---|---|---|
| Teeth-prover | `python3 guard/teeth_prover.py` | 10 planted mutations; every guard proves it can fail |
| Contract agreement | `python3 guard/contract_agreement.py` | all four vocabulary surfaces agree (validator · addendum · rollup · preamble) |
| Guard unit gates | `pytest guard/tests/ -q` | 6025 tests, hermetic in the sense that no arm needs a live fleet, a network or a credential. This is the collected count, not a pass count. Read `-rs` for the actual skips on the machine running the suite: the passback arm needs `PASSBACK_OUTBOX`; the publication identity-terms arm needs the private list (`_tools/identity_terms.txt`); and six freed-but-armed preflight cells cannot be built under Python 3.14 because `free_tool_id` clears their event mask. Other arms stand down without `O_TMPFILE`, a filesystem that supports it, an unprivileged account, working default ACLs, or permission to strip an ACL. The unreadable-config control also skips if the account can still read a mode-000 fixture. A skipped arm is not a passing one. The strict xfail that once recorded the scanner's stale-report gap was repaired in `a0dbe05` and is now a live arm |
| Fetch-gate teeth interpreter | `python3 guard/tests/teeth_fetch_gate.py` | Run the fetch-gate teeth and `guard/tests` with Python 3.12 or newer; `python3` in these commands must name that interpreter and pytest must be installed there. Child preflight probes also invoke `python3.11`, `python3.12` and `python3.14` when present; the 3.14 child needs its own pytest installation. Absent children or a child unable to import pytest report skip/UNMEASURED with the measured reason. Each child resolves its own user site, with `-s` respected; a pytest terminal summary is required before its exit status can count as a measurement. An unavailable observer is UNMEASURED, never a pass. |
| Documented counts | `python3 guard/doc_count_drift.py` | every count written into prose or the banner matches what it describes |
| Rendered banner | `python3 guard/banner_render.py` | the PNG keeps its transparent corners, opaque painted interior and transparent exterior (with narrow edge antialiasing), has 2x source geometry, and carries both source and PNG identity stamps |
| Full runner | `guard/run_guards.sh` | the above in order; leg-liveness dry-run returns `2 = UNMEASURED` by design |
| Release run | `guard/run_guards.sh --release` | the same runner with count coverage mandatory: the documented-counts step runs `guard/doc_count_drift.py --release`, so a count instrument that cannot be read (a suite whose test dependencies are absent) is `2 = UNMEASURED` and the affected claim sites are listed, instead of a disclosed skip. Before tagging, a reviewer runs this with BOTH suites' dependencies installed against the staged publication snapshot (the inventory comes from `git ls-files`, the bytes from the working tree — stage every intended document first) and reads the emitted claim inventory. Without `--release`, qualifying declared missing dependencies produce disclosed partial coverage (`N checked, M skipped — coverage is partial`); missing pytest itself cannot produce a collection receipt and is UNMEASURED in both modes |

### Fetch-gate C8 scope clarification

The no-method rule remains an implementation requirement, including indirect
calls through Python callbacks; the teeth do not prove that rule for arbitrary
gates, detector objects, inputs, callback arrangements, or interpreters.
Exit 0 and ALL CHECKS PASSED mean only that the finite assertions and instrument
controls in this teeth version passed on the interpreter used for this run.
They do not certify the absence of detector execution, or sandbox a detector.

The execution observer records untrusted code identities only when CPython emits
the selected monitoring events within the armed window in the current thread.
The tripwire records only sys.setprofile, sys.settrace and
sys.monitoring.register_callback while armed. State samples test profile/trace
slots and occupied monitoring tool IDs 0 through 4 at arm and disarm.
The state-wiring matrix generates sample paths × real gate roots × slots.
Structural tests resolve roots from observer call sites and sample routes from the
observer's self-call graph, rejecting unresolved names, aliases, attributes and
exact literal text in every AST string-bearing field, including nested constants.
Existing declarations, phase data, tables and literal-name comparisons have a
counted statement-digest manifest; new occurrences require review. Runtime root
and route classification additionally covers executed dispatches.
Runtime checks reject unclassified root identities and sample frame paths, walking
through intervening frames to the monitoring callback entry by code identity.
Static analysis excludes code built from strings, computed lookup keys, native code
and hostile rewriting of the pins themselves. It drives install arm, detector scan return/unwind
arm, module unwind arm, root return/unwind disarm and the direct-root close
fallback where applicable; the table gives checked reasons for other pairs.
Roots are gate, scan, verdict_blocks and _interpret, with their real code objects.
Unwind-arm cells require both arm and disarm entries when the slot stays live;
other terminal cells require exact phase/slot lists so an extra fallback sample
cannot silently replace a terminal sample. Every driven cell enters `_c8k_call`
itself and requires its verdict operands as structured exception data; it never
parses assertion text. A source pin rejects a separate cell verdict route and
wrapper state access outside the verdict. The shared finally closes monitoring,
restores the recorder, clears retained arguments and restores GC before reporting,
while any root exception is still propagating. State failure takes precedence over
execution failure for every call; neither check is disabled.
Direct-root close cells use excess-argument binding failure before root entry. Local
stand-ins in legacy positive controls are explicitly outside this matrix.
The entry point prints driven and N/A pairs with reasons and fixture differences
(helper seams, execution/audit records, occupant names/configurations and frames
above `_c8k_call`).
These controls target single-slot omissions at the generated path/root pairs in
the real wrapper context, including exception propagation, GC restoration and
retained arguments. Caller inspection above `_c8k_call`, arbitrary inputs and
callback execution remain outside the matrix claim. The fast
preflight and pytest exit-policy regressions live in
`guard/tests/test_fetch_gate_preflight.py` and never run the whole teeth per case.
The freed-mask product discovers accepted global event bits per interpreter and
crosses them with tools 0–5; clearing by free_tool_id is a measured named skip.
Neither mechanism covers all callback-hosted execution: audit hooks (including
hooks installed before import or while armed), a profiler that executes and
removes itself before arm, and callbacks remaining active under a freed tool ID
are specifically not certified by the execution observer.

The sampler derives its method class by reverse reachability from
`check_state` in `_C8Observer`'s self-call graph: `check_state`, `arm`, `exit`,
`unwind`, `install`, `close`. Calls into that class must be resolved self-method
edges from classified method scopes, with only the pinned wrapper entries and
callback registrations exempted. Anonymous scopes and method aliases fail closed.

The context inventory comes from reads and writes in `check_state`, `arm`,
`close`, `_c8k_audit`, `_c8k_state_verdict` and their `_c8k_call` lifecycle:
root/boundary identities and depths; phase and route; armed/armed_once;
owned/ready; hold; recorder identity; profile/trace; monitoring ownership and
its callback, global/local event configuration; trusted code identities;
execution/audit/state records; GC state; and propagating exception. The
`_c8l_read_operands` derives attribute loads and free-name loads from the ASTs of
`check_state`, `arm`, `close`, `_c8k_state_verdict` and `_c8k_call`.
`C8L_OPERAND_CLASSES` must equal that inventory. Its values explicitly distinguish
cell axes from lifecycle-only observations; adding a read requires classification.
This equality is an inventory pin, not proof that every listed value is driven.

The matrix drives each tool slot with three name representatives (empty,
`state matrix`, `coverage`); callback registration for every event bit exposed by
`sys.monitoring.events`; each accepted single global/local event bit; and a CALL
callback with CALL globally enabled. Acceptance is probed through the interpreter
API; rejected settings print their reason. These variants run on every applicable
path/root pair in ambient recorder mode. The original idle slots still run in both
recorder modes. Single dimensions avoid a configuration Cartesian product.
Quiet execution-record rows additionally keep a slot live across the unchanged
real call on install-arm and root-return-disarm; they assert the execution list
is empty and require both state phases. These also reach scan-return-arm on
non-direct roots. Other routes retain their naturally produced records.

Limits: names are representatives, not all strings; callback identities, mask
combinations, local code identities, record contents and arbitrary predicates are
not exhausted. Empty/nonempty audit records and every record list on every route
are not independent cell axes. Other read operands retain the shared lifecycle
without independent value axes. The matrix therefore claims the listed finite
occupant and quiet-record regressions, not every operand/value pair. State entries
remain exact; audit or execution entries cannot substitute for them.

The generated inventory extends the name axis beyond those three representatives. The generator
`_c8l_registration_names()` reads the AST of `guard/tests/teeth_fetch_gate.py`
through `_c8l_source_tree()`, including recursively parsed fixture strings and
registration templates. It extracts literal second arguments of tool registration
calls. The legacy numeric slot placeholder is normalized by token, preserving
quoted names and decoding escaped percent signs in slot templates. Unresolved registration names, direct aliases and unsupported
registration syntax fail closed; computed API spellings and native registration
are outside this static claim. The current monitoring registration API is
`use_tool_id`; the sole dynamic name emitter has its entire body pinned.
Configured event rows now carry whole registration templates so their names are
visible to the same AST inventory, without changing their event configurations.

Each generated source name and one fresh UUID-based runtime name are planted on
every applicable path/root/tool-slot tuple, using idle occupants and ambient
recorder mode. The runtime name is checked absent from this teeth source. The
planted name/slot list is decoded from executable setup strings and must equal
the generated list; the matrix also checks its driven path/root/name/slot product.
The entry point prints that name list, runtime value, cell count and elapsed time.
These extra rows keep the single-dimension approach: they do not cross each name
with every callback, event configuration or recorder mode.

No finite name list covers all debugger/profiler names. A filter keyed on an
arbitrary unseen name introduced by a teeth-source edit is refused by the
whole-module AST pin, not by an assertion that finite cells cover every string. The loop pin requires
the sampler's tool loop to read only presence (`is not None`) and append the
phase/slot entry without inspecting the name; it also rejects extra direct
occupant reads in the sampler. The lexical site inventory counts the reviewed
lexical spellings; it is not a file-wide alias or reflection analysis.
Whole observer, wrapper, verdict, failure, audit, retention and root-classifier
AST bodies pin control flow and downstream local aliases, including new helper
calls, closure/default aliases, mutation, rebinding and reordered reports. The
only occupant getter on sampling routes is the presence read; setup also checks
tool 5 for conflict. Preflight and fixture getters are separately reviewed sites.
Every call site and argument expression equals one reviewed multiset; the wrapper
publishes execution records unconditionally for all calls, with no cell-only
argument. Parameter/local/free reads join attribute/global operand classification.
Lifecycle-only operands remain classified without claiming value exhaustion.
This is a structural claim about the reviewed Python data path, not arbitrary
computed dispatch, native code or hostile rewrites of the pins. Positive controls
exercise direct/embedded discovery, unresolved registrations, omitted name rows
and rejection of a name-dependent sampler. Earlier representative-only limits
above describe the original rows; these added rows and pins extend the name axis.

A deliberately conservative whole-module AST digest covers this source. Every
statement, definition, registration, nested expression and literal (including
fixture code strings and the checks themselves) participates, in source order.
This refuses source changes outside observer bodies as well: audit/monitoring,
trace/profile, finalizer, descriptor, signal or other interpreter callbacks can
run during install, the real call, close, retention cleanup and the state verdict.
Access may use ordinary references, imports and aliases, literal-key reflection
through object or module dictionaries, captured defaults/closures, frame or heap
traversal, or generated Python. Coverage is selected by AST membership, never the
spelling of a local alias or a reachability guess. When the reader and its source
path still refer to the executing module, a new or changed route requires explicit
review before updating the digest, even if it looks unrelated.

The only normalized data is the value of one top-level digest assignment. Its
shape must be one target and one literal 64-character lowercase hex string;
missing/duplicate assignments, extra targets and executable values are refused.
The canonical form omits empty/absent optional AST fields to share the reviewed
digest across 3.12 and 3.14. Ordinary comments/formatting are not pinned; parsed
type comments and type-ignore metadata remain part of the AST.
The source reader parses the file's bytes, honoring Python's source coding declaration
instead of forcing UTF-8 text. Encoding comments can therefore change the pinned
AST even when the bytes are valid under both decodings. Independent Latin-1
fixtures compare the reader and digest with compiled-byte execution, covering
both dual-valid UTF-8/Latin-1 bytes and bytes invalid under UTF-8.
This is a source regression refusal, not a runtime integrity monitor: it does
not attest imported/native code, external code generation, in-memory or on-disk
changes after the check, or coordinated rewrites of the checker/expected digest.
All source pins trust the module global `__file__`. In-file code can write a clean
copy, rebind `__file__` to that copy (directly or through computed namespace access),
and change the running observer while the pins read the clean copy. This bypass
is not prevented. In-file code can also replace the reader, checker, expected
digest or other globals; these checks do not establish an independent trust root.
The existing finite runtime matrices and body/site pins remain complementary.
Module controls use small independent AST fixtures. The older data-path positive
controls also use an independent fixture instead of requiring the live pin to pass.

Child pytest probes request `--color=no`; terminal-summary parsing also strips
SGR colour escapes while preserving the raw diagnostic output. Passing/failing
and collection-error summaries are accepted; hollow `[True]` launcher output is
refused. Exit status alone is still insufficient. The parser scans whole stdout
lines for summaries, allowing unrelated output before or after them, including
`pytest_unconfigure` and `atexit` output. Any whole line of the accepted shape
can be treated as a summary, including text emitted by tests or hooks.
Skipped-only, warnings-only, deselected-only,
xfailed/xpassed-only, subtests-only and `no tests ran` summaries are recognized
but refused with their actual summary: at least one passed, failed or error count
is required. SGR escapes are normalized; stderr cannot provide the receipt.

The documented-count collector requires a successful pytest exit and takes its
count from `session.testscollected`, observed by the child plugin at
`pytest_sessionfinish`. The plugin also records completion of collection,
`pytest_deselected` events and failed collection reports. It writes a separate
JSON receipt after `pytest.main` returns. Missing or malformed receipts, unfinished
collection and collection failures cannot supply a count. Any actual deselection
is UNMEASURED naming deselection; exit status 5 without deselection names the
absence of collected tests. No stdout or stderr text supplies, changes or
contradicts the total: literal-newline IDs, collection-finish hook lines and
post-summary output have no role in the count decision, even when they look
exactly like pytest summaries.

This source trusts pytest's session state, hook delivery, the child process and
its receipt file. It does not authenticate plugins or tests: code that mutates
pytest state, suppresses hooks, forges the receipt or alters exit status remains
outside the claim. Configuration and plugins can change what pytest collects;
this is a measurement of that collection, not an independent inventory of every
potential test in the source tree.

A failed collection (status 1 or 2) gets the dependency skip only when a separate
child plugin receipt identifies EVERY failed collection report's cause as exactly
`ModuleNotFoundError`, its name is declared by that suite, and the collecting
interpreter's `importlib.util.find_spec` cannot find that top-level module.
A failed or indeterminate lookup is not proof of absence. The ordinary skip line
names the missing modules; release mode reports them as UNMEASURED instead.
The skip marker remains string-compatible for existing instrument callers;
production skip notes carry the names separately for reporting.

Declarations come from the suite directory's parent: `pyproject.toml`'s
`[project] dependencies`, its `dev` extra, and `requirements.txt`. Thus the TUI
reads `tui/pyproject.toml` and `tui/requirements.txt`; the guard suite declares
none. Distribution names are lowercased and runs of hyphens, dots or underscores
become one underscore: this maps `rich`, `textual`, `pyte`, `textual-serve`, `pytest`
and `pytest-asyncio` to `rich`, `textual`, `pyte`, `textual_serve`, `pytest` and
`pytest_asyncio`. Both TUI declaration files explicitly include `rich`, which
TUI modules import directly. A source-inventory test checks absolute import roots
throughout TUI Python sources, including tests and imports inside functions,
against the declared set after excluding standard-library and local top-level
modules/packages. Conventional environment directories are excluded. Imports in
the body of a `try` with handlers are treated as optional; imports in handlers,
`else`, `finally` and bare `try/finally` bodies remain required. This is an AST
syntax rule, not proof that a handler catches an import failure. Dynamic imports
and external code are outside this declaration test.
This is a spelling convention, not a universal distribution-to-import registry:
unrelated import aliases, namespace mappings, undeclared transitive dependencies,
other extras and dynamic declarations are not inferred. Versions, extras and
markers on a named requirement are not evaluated; this checks declared names,
not whether an installation satisfies constraints. Requirements-file includes,
options and unnamed URLs are unsupported and refuse the skip, as do unreadable
declaration files or TOML syntax errors. This is not a full requirement/schema validator.

Repository ownership remains a refusal: matching top-level module filenames or
directory names under flat, `src/` or nested projects take precedence, even for
a declared, absent name. The walk includes untracked source, does not follow
symlink directories, and prunes `.git`, `__pycache__`, `.pytest_cache`, conventional
environment names (`.venv`, `venv`, `.env`, `env`, `site-packages`, `dist-packages`),
and roots containing `pyvenv.cfg` or `conda-meta`. An environment under those roots
therefore does not claim ownership of its installed packages. Source placed under
those reserved environment roots or reached only through symlinks, and arbitrary
runtime aliases without matching paths, are outside this ownership inventory.

An undeclared missing import (including stale imports after a rename), a manually
raised missing-module error naming an undeclared or findable module, missing
children of an installed package, generic `ImportError`, absent/unknown evidence,
and mixtures containing any nonqualifying failure remain UNMEASURED; ordinary
mode cannot pass on those readings. Printed exception-looking text supplies no
receipt. Missing pytest itself also remains UNMEASURED because the child cannot
start its receipt plugin. These checks do not authenticate test/plugin code: a
manually raised error naming a declared, genuinely absent module meets the same
class as an import failure. Forged status, receipts or manipulated import state
remain outside the claim; forged stdout summaries cannot affect the count.

The two pytest measurement child-launch sites under `guard/` are
`doc_count_drift.py::_collect` and
`tests/test_fetch_gate_preflight.py::_pytest_probe` (via `_run`). Both clear
`PYTEST_ADDOPTS` in the child environment, so ambient verbosity, selection,
summary suppression and invalid options do not affect these measurements.
The preflight child disables plugin autoload. The count collector inherits
`PYTEST_DISABLE_PLUGIN_AUTOLOAD` unchanged; its plugin environment can affect
collection, and this change does not standardize that setting.
The preflight formatter controls and count output-noise controls were checked
against installed pytest 9.1.1, using its
`TerminalReporter` summary builders, `KNOWN_TYPES` and `format_session_duration`;
other pytest versions were not measured here. The preflight reader's accepted
stock outcome labels include subtests passed/failed/skipped. Project config or
plugins that suppress or replace the terminal reporter can still make the
preflight reading UNMEASURED; the count collector does not require that reporter.

Launch inventory also includes `run_guards.sh`'s direct pytest command; it consumes
exit status only and now also clears `PYTEST_ADDOPTS` (no summary is parsed).
The standalone entry points in `tests/test_artifact_txn.py`,
`test_contract_agreement.py`, `test_leg_canary.py`, `test_properties.py`,
`test_stage_ledger.py` and `test_teeth_prover.py` call `pytest.main` in-process,
returning its status without parsing output. The doc-count CLI seam invokes the
collector through its production entry point, supplies a synthetic declared
missing-TUI-dependency receipt, and copies the banner guard count to isolate CLI
routing. That seam does not independently verify the banner count. These sites were inspected; the aggregate runner was not executed.

Scanner adjacency rows derive six marks (`:`, `,`, `(`, `)`, `—`, `–`) from
`brief_scan.py`'s docstring, crossed with letters, digits, space after only and
space before only. The same prose supplies whitespace-surrounded hyphens and
case-insensitive whole-word `and`; separate rows check their boundaries. A
bidirectional pin constructs the canonical regex from the prose and refuses
added or missing members and unreviewed regex grammars. The generator additionally
enumerates `re.fullmatch(r"\s", chr(i))` over the interpreter's Unicode range,
crossing the prose-derived forms with whitespace runs and sides in interior
letter contexts, plus one-sided start/end rows. The generator adds the following
explicit product through `scan_text` (older rows are retained):

Consumer product: documented marks, hyphens and three word cases x before/after/both x start/end/interior x letters/digits x every regex whitespace code point x runs 1/2/3.

At a window edge the selected whitespace abuts the negation word or occurrence;
when that side has none, the mark touches the word. The other neighbour uses the
selected letters/digits representative; interior rows use both. The test pins
the generated axis sentence in this README and its docstring, and checks the
exact emitted label and byte-level context product, with omission/mislabel controls. Additional rows
run in one pytest item, retaining failing context labels.
Physical line separators exercise the consumer's `splitlines` boundary; they are
not assumed to join clauses across lines. Arbitrary whitespace mixtures, longer
runs and arbitrary lexical neighbours are outside these finite rows. The A2 rule
is unchanged.

Static field inventory, generated by `_c8l_string_fields()` from the
`ast` node ASDL signatures (runtime versions may add fields):

`Assign.type_comment`, `AsyncFor.type_comment`, `AsyncFunctionDef.name`, `AsyncFunctionDef.type_comment`, `AsyncWith.type_comment`, `Attribute.attr`, `ClassDef.name`, `Constant.kind`, `Constant.value`, `ExceptHandler.name`, `For.type_comment`, `FunctionDef.name`, `FunctionDef.type_comment`, `Global.names`, `ImportFrom.module`, `Interpolation.str`, `MatchAs.name`, `MatchClass.kwd_attrs`, `MatchMapping.rest`, `MatchSingleton.value`, `MatchStar.name`, `Name.id`, `Nonlocal.names`, `ParamSpec.name`, `TypeIgnore.tag`, `TypeVar.name`, `TypeVarTuple.name`, `With.type_comment`, `alias.asname`, `alias.name`, `arg.arg`, `arg.type_comment`, `keyword.arg`.

Every unreviewed classified spelling in these fields is refused; constants are
checked at every depth regardless of parent. Name/attribute occurrences proceed
to the resolved-call checks. Metadata exemptions pin scope, field, spelling and multiplicity, plus the
containing statement for data or the definition's name for declarations. Existing literal comparisons remain pinned data;
new comparisons are refused. Embedded code in strings, computed strings, native
code and hostile edits to the pins are outside this syntax claim.

The additional constructor fixture records a second M.__new__ call after the
fixture's construction record is cleared and before the outer gate returns.
It catches that operation on that fixture even when monitoring is suppressed.
This is finite regression coverage, not certification of all callback routes,
methods, detector shapes or boundary timing. The reset precedes actual scan
return: the witness does not independently establish the exact return instant.
Other methods in quiet C8k fixtures remain dependent on monitoring visibility.

Finalizers and weakref callbacks caused by freeing remain excluded; explicit
__del__ calls are not exempt. Native-only execution, other threads, fixture or
monitor tampering (including a Python call spoofing a monitoring callback entry), objects outside the corpus and work after the outer call's
return/unwind remain uncertified. Unrelated Python code in a window may be
rejected; run serially in a dedicated process.

At teeth import, before importing the gate, a live profile/trace slot, an occupied
monitoring tool ID or a nonzero global event mask makes this run UNMEASURED (exit
2 for the direct entry point and pytest, including continued collection errors),
not a gate violation. Missing required monitoring
capabilities likewise make the direct run UNMEASURED. No callbacks are removed.
This preflight cannot enumerate existing audit hooks or all code-local monitoring
state. It is an environment check, not proof that callbacks are absent.
Instrumentation introduced after preflight is not classified by this check.
Observed regression failures still exit 1; the legacy NOT SOUND marker names a
failed assertion in this bounded suite, not a universal security verdict.
CPython 3.12+ is the existing execution prerequisite, not a promise of identical
instrument behavior on every future version; retain version-specific receipts.

Run the teeth as the main program (`python3 guard/tests/teeth_fetch_gate.py`).
Wrappers that swallow `SystemExit`, including `python -m cProfile` and
`python -m trace`, can report exit 0 even when the teeth print UNMEASURED or fail.
Retain the printed result as well as the direct process exit status.

### Regenerating the banner

**Run `docs/render_banner.sh`. Do not render the banner by hand.**

The banner SVG has rounded corners, which survive into the PNG only if the render keeps a
transparent ground. A headless browser defaults to an opaque white page: it silently drops the
alpha channel and paints those corners white, which on a dark README reads as four white notches.
The output is still the right size, still the right picture, and still commits cleanly — nothing
about it looks wrong except the thing you were not looking at.

That happened twice. Both times the render was retyped from memory and
`--default-background-color=00000000` was the flag that went missing. The second time it survived a
positive control, because the control render carried the flag and the shipped render did not —
proving a renderer works is not the same as proving the command you shipped with works.

So the invocation lives in the script and the property is checked by the guard, which also records
which SVG the PNG came from. Edit the SVG without re-rendering and the guard goes red on staleness
rather than letting a stale image ship.

The guard also checks every pixel's alpha against the first painted root child,
the SVG ground rectangle. Its x/y/width/height/rx (and centered opaque stroke)
define the rounded shape at 2x; no banner dimensions or radius are copied into
the checker. Pixels entirely inside must have alpha 255, and pixels entirely
outside must have alpha 0. Only pixel squares intersecting the edge may vary,
with an extra 0.25 device pixel at curved edges for rasterization rounding. The
committed render has two alpha-1 pixels whose squares fall 0.151 pixels outside
the ideal curve. This allowance covers that fringe and stays below one device
pixel from the pixel center to the curve (`sqrt(2)/2 + 0.25 < 1`). Aligned straight
edges receive no extra tolerance. The four corner pixels must still be zero.

Unsupported ground geometry (including elliptical corners, stylesheets,
inherited root paint, transforms and clipping) is UNMEASURED, while existing identity and corner checks
still run. Synthetic tests re-stamp corrupted images to demonstrate that missing
interior paint and stray exterior paint are rejected independently of freshness.
Alpha conformance does not verify the banner's text or RGB content visually.

## Activating the publication hook

The tracked hook source in `guard/hooks/` is not active in a clone. Git does not run hooks from
the work tree, and nothing in the test suite installs one: the fresh-clone checks above run on
synthetic data so that they can pass anywhere. Activation is a separate procedure with two policy
inputs that never travel with a clone, and it has to be repeated in every clone that will push.

The steps below were run end to end in an isolated scratch repository (a fresh source repository,
a disposable bare target, synthetic identities); every quoted output line is one that run printed.

### 1. Prerequisites

The hook itself requires Bash 4+, Git, Python 3 and `tar` (its header says so). The recipe here
also uses `git rev-parse --path-format=absolute`, which needs Git 2.31 or later (the installer falls
back to joining the repository root with the relative path on older Git; the manual parity check in
step 5 does not). The installer uses `install`, `sha256sum` and `cmp`. All of these are taken from
`PATH`.

Two policy inputs are provisioned separately and stay private to the clone:

- `_tools/identity_terms.txt` — the terms the scanner treats as personal data. Gitignored.
- an approved-identity list — the emails a commit may carry. In this recipe that is the repo-local
  Git config key `fleetops.approvedIdentity`; the alternatives (a file or an environment variable)
  are described in step 3, because they take precedence over it.

Both fail closed: with either one missing, the installer refuses to install and an installed hook
refuses to push.

### 2. Provision the two policy inputs

Scanner identity terms:

    if [ -e _tools/identity_terms.txt ] || [ -L _tools/identity_terms.txt ]; then
        echo "_tools/identity_terms.txt exists; not overwriting" >&2
    else
        cp _tools/identity_terms.example.txt _tools/identity_terms.txt
    fi

Then edit the copy: one term per line, for each class the example file names (personal and account
names; machine nicknames and short hostnames, including any alias used in benchmark or log
annotations; LAN domain suffixes; personal email local-parts). The file must be nonempty after
comments. Never commit it — `.gitignore` already lists it, and `git status` must not show it. The
scanner's own self-test derives its planted identity from the first term in the file;
regex-special characters such as `-` or `.` are accepted in that first term.

Approved identities. Inspect what is already configured before adding anything:

    git config --local --get-all fleetops.approvedIdentity

Exit status 1 with no output means nothing is configured locally. Add the email that commits will
carry. This is a literal template: replace `<owner-email>` before running it, and do not add a value
the previous command already printed (`--add` appends; a duplicate adds nothing):

    git config --local --add fleetops.approvedIdentity '<owner-email>'

Matching is exact (`[ "$entry" = "$1" ]` in the hook): no case folding, no wildcards, no
`Name <email>` form — the bare email. Every commit in the push must satisfy it on three surfaces:
the author email, the committer email, and the email in any `Co-authored-by:` or `Signed-off-by:`
trailer. See what a commit actually carries with `git log -1 --format='%ae %ce'`. A GitHub handle
(`<owner-gh>`) is not automatically the right value; the noreply address GitHub offers is an email
like any other and has to be listed if commits carry it.

### 3. Where the hook reads identities, and why the installer refuses shadows

The hook (`guard/hooks/pre-push`) selects its identity source in this order:

1. the file named by `FLEETOPS_APPROVED_IDENTITIES`, when that variable is set;
2. otherwise the default file `_tools/approved_identities.txt`;
3. only when the selected file is absent or holds nothing but comments and whitespace,
   `git config --get-all fleetops.approvedIdentity`.

So a nonempty file silently overrides the config, with no change to the hook. The config route
activated here (`--pre-push-config` and `--check-pre-push-config`) therefore refuses, before
touching anything, when:

- `FLEETOPS_APPROVED_IDENTITIES` is nonempty (`refused: FLEETOPS_APPROVED_IDENTITIES is set ...`);
- `_tools/approved_identities.txt` exists in any form — a file, an empty file, a symlink, a
  dangling symlink (`refused: _tools/approved_identities.txt exists ...`);
- no nonblank `fleetops.approvedIdentity` is in the repository's `--local` config;
- any identity Git resolves for that key from any scope is absent from the repository's `--local`
  config (`... inherited from outside this repository's --local config ...`): every resolved
  identity must also be present locally; an inherited value already present locally is accepted.

The refusal names the category, never a value. When one of these fires and the file or variable is
not something this procedure created, do not delete or unset it to make the check pass: it is
someone's policy, possibly the one actually meant to govern this clone. Find out why it is there
and select the intended source; that decision is outside this recipe. The `.gitignore` entry for
`/_tools/approved_identities.txt` only keeps such a file out of commits — it neither provides a
policy nor removes one.

### 4. Inspect the effective hook path, then install pre-push only

Git may run hooks from somewhere other than `.git/hooks` (`core.hooksPath`, linked worktrees).
Ask Git, and look at what is already there:

    git config --show-origin --get core.hooksPath      # exit 1 and no output: unset
    resolved_hook=$(git rev-parse --path-format=absolute --git-path hooks/pre-push)
    ls -l "$resolved_hook"

A pre-push hook that already exists there with different content stays where it is: the installer
refuses (`refused: a different hook already exists at ...; rerun with --pre-push-config --replace
... Nothing was changed`). Decide what that hook is for before choosing between removing it
yourself and `--replace`, which first backs it up (bytes and mode) to a file in
`fleetops-hook-backups/` under the repository's Git common directory and prints two lines,
`backup: <path>` and `restore: install -m <mode> '<backup>' '<hook>'`, and only then installs. A
symlinked destination or hooks directory is refused outright. The installer never edits
`core.hooksPath`.

Install:

    bash guard/hooks/install.sh --pre-push-config          # add --replace only once the existing hook's disposition is decided

Success prints the selected source category, the effective path, and one line beginning
`installed <path> (guard/hooks/pre-push, mode 755, parity verified by sha256sum and cmp; identity
route: git-config)`. Exit status: 0 installed · 1 refused (nothing changed; stderr says why) · 2
usage error. Only `pre-push` is written. Running the installer with no arguments is the older route
and installs every hook in `guard/hooks/` (currently `commit-msg` and `pre-push`) after the same
git-config identity and scanner preflight. A failed preflight writes no hooks. Use the explicit
`--pre-push-config` route above to install only pre-push and preserve other existing hooks.

Legacy mode checks every destination before copying any hook. A differing existing file or a
symlink is refused with exit 1 and its path on stderr; the existing hook is preserved. Identical
existing hooks permit an idempotent rerun. To replace only a differing pre-push hook with a backup,
use `--pre-push-config --replace`; decide the disposition of any other conflicting hook yourself.

### 5. Check before every push

    bash guard/hooks/install.sh --check-pre-push-config

This repeats the step 3 preflight and then verifies the installed file, read-only: it must be a
regular file, executable, with the same `sha256sum` as `guard/hooks/pre-push`, and byte-identical
by `cmp -s`. Success ends with `parity: OK — <path> is executable and byte-identical to
guard/hooks/pre-push`; any failure is `refused: ...` with exit 1 and nothing repaired. The same
comparison by hand, in the clone being checked:

    resolved_hook=$(git rev-parse --path-format=absolute --git-path hooks/pre-push)
    sha256sum guard/hooks/pre-push "$resolved_hook"
    cmp -s guard/hooks/pre-push "$resolved_hook" && test -x "$resolved_hook" && echo "parity: identical and executable"

The `sha256sum` line prints two digests; that is a display, not a comparison, and two digests
with no compared result are not a parity check. The `cmp -s` line is the comparison, and `test -x`
is the check that Git will run the file at all. A one-byte drift in the installed copy and a
removed execute bit each make the check refuse.

The check describes one moment. Run it again after anything that could change what it examined:
the tracked hook changing (a pull that touches `guard/hooks/pre-push` leaves the installed copy
stale), the policy changing, `core.hooksPath` changing, a new shell or environment (a profile that
exports `FLEETOPS_APPROVED_IDENTITIES`), and before each push. The hook itself is unchanged by all
of this and keeps the file-first precedence from step 3: a `_tools/approved_identities.txt` created
after the check is selected by the very next push, and a push made without running the check gets
no warning. Passing this check does not establish that the config remains the source afterwards.

### 6. Rehearse in an isolated repository first

There is no rehearsal document under `docs/`; the rehearsal is small enough to describe here. Use an
isolated synthetic repository with a one-commit clean history and a disposable bare target — never
a real remote. From the package root, create that history with these two shell lines:

```bash
package=$PWD; rehearsal=$(mktemp -d); git init -b main "$rehearsal/source" && git init --bare "$rehearsal/target.git"
git -C "$package" archive HEAD | tar -x -C "$rehearsal/source"; cd "$rehearsal/source" && git config user.name Fixture && git config user.email fixture@example.invalid && git add . && git -c commit.gpgsign=false commit -m 'Synthetic clean baseline'
```

The archived tree must pass the scanner with the synthetic policy below. Push `main` to
`"$rehearsal/target.git"` for the following checks. A first push of the real clone to an empty target
selects all reachable history, not just this clean tree; the one-commit expectation applies only
to the synthetic repository. Expect these states:

- Provision synthetic inputs: a repo-local `fleetops.approvedIdentity` of `fixture@example.invalid`
  (also the commit author and committer), and a terms file whose first term
  does not occur in the clean content. Confirm that removing the config, and separately emptying
  the terms file, each make `--check-pre-push-config` refuse; then restore them.
- Install (step 4), check (step 5).
- Clean acceptance: an ordinary commit pushed to the empty bare target succeeds with exit 0, the
  target's ref advances, and the hook prints `pre-push gate: CLEAN — scanned 1 distinct selected
  commit tree(s) ...`. A no-op push (nothing to send) is not this control.
- Planted refusal: commit a file in an ordinary tracked path containing a key-shaped literal
  (assemble it at run time, e.g. `api_key = '<24 alphanumerics>'`), confirm
  `--check-pre-push-config` still passes (policy and parity are intact), then push. Expected:
  `PRE-PUSH BLOCKED: scanner rejected commit <sha>'s archived tree ...`, exit 1, the target's ref
  unchanged. `_tools/` is scanned, so a plant there also refuses. Do not plant under a directory
  the scanner skips (`_reports/`, `.git/`, caches — the list is in `_tools/scan_gate.py`'s
  docstring): such a plant is accepted, which proves nothing about the scanner. Drop the planted
  commit before the next clean push.
- Identity refusal: a commit whose author, or whose `Co-authored-by:` trailer, is
  `outsider@example.invalid` is refused with `PRE-PUSH BLOCKED: <sha> carries a non-approved ...`.

Two behaviours to know before the first real push:

- The hook refuses a shallow repository (`PRE-PUSH BLOCKED: shallow history cannot establish the
  commit range.`). Clone without `--depth`, or run `git fetch --unshallow` first.
- A first push to an empty remote has no destination tip to diff against, so the hook scans every
  commit reachable from the pushed ref, and every one of them must pass. A source tree whose older
  commits contain deliberate scanner literals — fixtures, examples — is refused even when HEAD is
  clean, and the refusal names the introducing commit. That refusal is correct: those bytes would
  be published. Do not waive it, disable the scanner, or bypass the hook. Whether a real
  repository's outgoing history is acceptable is a separate measurement on that repository; the
  rehearsal above does not establish it.

### 7. Rollback

Restoring the hook and restoring the policy are two different actions.

- A hook that was replaced: run the `restore:` line the installer printed, verbatim; its shape is
  `install -m <mode> '<backup>' '<hook>'`. Then `sha256sum '<backup>' "$resolved_hook"` and
  `cmp -s` the two; the digests must match, and `--check-pre-push-config` must now refuse, because
  the restored file is not the tracked hook.
- A hook that did not exist before installation: confirm it is still the tracked copy, then remove
  only that file — `cmp -s guard/hooks/pre-push "$resolved_hook" && rm -- "$resolved_hook"`. Leave
  every other hook in the directory alone.
- Neither action touches `fleetops.approvedIdentity` or `_tools/identity_terms.txt`. If the policy
  was provisioned only for this activation, remove it as a separate, deliberate step
  (`git config --local --unset-all fleetops.approvedIdentity`; delete the terms file); if it was
  there before, leave it. Restoring a hook never restores a policy, and removing one does not
  remove the other.

## What fail-closes without private data — deliberately

`mutation_harness.py` sandboxes the code under test, applies surgical mutations, and asserts the
paired guard goes red (`KILLED` / `SURVIVED` / `ABSTAINED` — a guard that declined to assert
anything did not catch the bug). Its baseline check pins measured corpus sizes; without the private
measurement corpus it **aborts before mutating anything** — a harness that cannot reproduce the
clean baseline refuses to certify mutations against it. That refusal is the integrity rule, not a
missing feature. A synthetic public corpus is planned.

## What is NOT checked, and why a guard for it was declined

The four animations in `README.md` each sit beside their Mermaid source, kept in collapsed
blocks. **Nothing verifies that an animation still matches
the source printed next to it.** Edit the mermaid and not the gif, and the page shows a diagram that
is no longer the one it documents. That gap is real and it is not guarded.

A guard was proposed for it, modelled on `banner.stamp` — record the source digest and the gif
digest, go red when they part. Three reviewers were asked to attack the proposal and all three said
do not build it, for reasons that survived checking:

- **Two hashes prove coexistence, not derivation.** A stamp says "these are the two files I
  recorded". It cannot say the gif was rendered *from* that source. Change a node label, keep the
  old gif, update only the recorded source digest, and both hashes agree with their files while the
  picture is stale. No attacker is required — a convenient "refresh the hashes" step is the bypass.
- **There is no renderer here to establish a baseline.** The banner works because `banner.svg` is a
  standalone source with `render_banner.sh` beside it. No mermaid-to-animation pipeline is tracked in
  this repository, so no baseline could be regenerated and reviewed; seeding one from today's files
  would bless whatever is there.
- **The output is not bit-reproducible anyway.** Rendering mermaid to an animation depends on
  headless browser timing and font rasterization. Hashing the result would fail on ordinary edits
  that changed nothing visible.

So the honest statement is the one in this heading: the correspondence is unchecked, the mermaid
block is the authority, and the animation beside it is illustration. Recording that is worth more
than a guard whose green could not be trusted — a checker that cannot fail for the right reason is
the failure this directory exists to argue against.

## Provenance note

During export, this directory's own gates caught the exporter twice: a sanitization pass made the
contract surfaces cwd-relative and the `isabs()` unit gate refused it; the mutation harness refused
its baseline in the corpus-less tree. Guards that police their own maintainers are the point.

A third export defect escaped both of those gates and shipped. The de-identification pass that turns an
absolute home path into `~` rewrote a PATH entry in `guard/leg_canary.py`, and `exec` performs no tilde
expansion — so the published canary's cron-PATH remedy was a dead string from the first export commit until
`074ca30`, while the origin copy, which kept the absolute path, was never affected. Nothing caught it; it was
found a month later while the function was being rewritten for another reason. The two catches above are
mistakes the gates could see in the tree in front of them; a rewrite that is correct as prose and wrong as a
string handed to `exec` is the case they cannot, and the only thing that pins it now is a test written after
the fact (`test_local_bin_entry_is_expanded_not_literal`). The lesson is narrower than "sanitise carefully":
a path in code has semantics a path in prose does not, and the export step that makes one look like the
other is itself a code change that needs its own test.

The ref gate defaults to `refs/heads/main`; adopters publishing another branch can set `git config fleetops.publishRef refs/heads/release` before running `python3 _tools/ref_gate.py .`. Other local publishing refs still fail the gate. A tag passes only when it resolves to a commit already on the publishing ref, so a release tag on `main` is fine and a tag on unpublished history, or on a tree or blob, still fails. In a pull-request checkout, which has no local branch, the `refs/remotes/origin` copy of the publishing branch is used instead; with neither present, any ref under `refs/tags/` makes the gate refuse with exit 2. A configured publishing ref that does not point at a commit is refused. Tag annotations reachable from any ref, including nested annotated tags, are checked for AI co-author trailers (`Co-Authored-By` naming a model, a model vendor or an assistant as a word, versioned spellings such as `Qwen3-Coder` or `GPT6` included) the same way commits are. Because names are matched as words, a person whose name is also a model's name (Gemma, Kimi) is refused too; the report prints the whole trailer line so the author can see why. Never-publish names (`__pycache__`, `.pyc`, `.pyo`) are checked on every entry of every reachable tree, so a banned file cannot hide behind an identical allowed one; trees are read in bounded batches. Every git call runs with `--no-replace-objects`, so a local replace ref cannot stand in for the real objects a push publishes; objects that only a replace ref reaches are still scanned. The gate refuses a shallow clone with exit 2, because missing history can hide violations.

For the honesty stop hook on a host with `ps` and `pgrep`, copy `guard/honesty_gate.config.minimal.example.json` to `guard/honesty_gate.config.json`, then run `python3 guard/honesty_stop_gate.py --check-config`; this process-only example inherits the claim and subject defaults and avoids optional service/container binaries. Adapt the config to the subjects and probes actually used on your host.

Both activation routes explicitly stop after a failed identity-config or scanner self-test preflight, even when shell errexit is disabled. The read-only check uses the same explicit refusal.

`fleetops.publishRef` is trimmed before validation. Unset, empty, and whitespace-only values use `refs/heads/main`. A nonblank value must start with `refs/` and pass `git check-ref-format`; a short name such as `main` or an invalid ref is refused with one line and status 1. Surrounding whitespace around a valid full ref is accepted. `refs/original/` remains reserved for refused rewrite leftovers and cannot be selected as a publishing ref.

The existing `HONESTY_GATE_CONFIG` environment variable selects an alternative settings file. `guard/tests/test_honesty_example_config.py` copies the shipped minimal example there and checks acceptance with only process probes available; removing that copy restores defaults and refuses the unresolved optional commands. The test controls command availability and does not probe live services.


## Instance policy and external archive paths

- `_tools/wall_check.py`: this file encodes the authors' policy; adopters replace `WALL_SOURCE_PATTERNS` and `WALL_CONTENT_PATTERNS`. For preparation, follow [In-repository provenance](#in-repository-provenance) below.
- `_tools/readme_guard.sh`: this file encodes the authors' policy; adopters replace the table at lines 8–12.
- `guard/leg_canary.py`: this file encodes the authors' policy; adopters replace the table at lines 44–49. Its `_default_runner` wrapper conventions and `main` cron PATH must be adapted to the same roster; `prepend_local_bin` expands the home directory, because a literal `~` in PATH is never expanded by exec and silently contributes nothing.

`vision_ingest.py` takes external paths only from `VI_BACKUP_ROOT`, `VI_BACKUP_MOUNT`
and optional `VI_ALERT_SCRIPT`. Export edited values from the authors' instance in
`docs/vision_ingest.example.json`; that file is documentation, never auto-loaded.
Without both backup settings, archive prints `not configured` and leaves primary
files in place. The root must lie under the configured mount, which must be mounted.
Hash read-back verification and retention on corruption remain enforced. An unset
alert script disables that optional notification only; it does not change the verdict.


### In-repository provenance

This is the operator procedure for both copy-in and reviewed release preparation when changes
are authored in this repository. `_reports/provenance.tsv` is a private, gitignored ledger;
it must not be published. A fresh clone does not carry it. A maintainer must recover the
reviewed source records or establish them from evidence before accepting a release. Missing
history is a review gap, not permission to label every file as authored here.

1. Freeze the intended release snapshot for review and inventory it with the existing
   `staged_files()` implementation. Run from its repository root:

   ```bash
   python3 - <<'PY'
   import runpy
   staged_files = runpy.run_path("_tools/wall_check.py")["staged_files"]
   for path in sorted(staged_files(".")):
       print(path)
   PY
   ```

   This walks the filesystem tree, including eligible untracked files, rather than the Git
   index. It excludes directories named `.git`, `_reports`, `_tools`, `__pycache__`,
   `.pytest_cache`, `.venv`, and `node_modules` at any depth, and files named
   `STAGING_README.md`. Gitignored files outside those exclusions are still included.
   Review each listed file; keep unrelated scratch outside this inventory. Do not substitute
   `git ls-files`, and do not infer coverage of excluded files or Git history.

2. Review each file's origin against its source record and the release diff/history. Retain
   existing source entries for copied files, even when they are edited in place. Record the
   actual source for new imports and preserve earlier source history in private review notes;
   a rename must retain that history too. Do not replace an imported origin with a local path
   to evade a source wall. For a file genuinely authored here, after reviewing its creation
   and contents, declare `in-repository:<repository-relative-path>` as its maintained origin.
   Retain the reviewer decision and the reviewed revision or content digest in private review
   notes. This string is a declaration in the existing source field, not a new parser feature.
   If a file's origin cannot be established, hold publication and resolve that gap.

3. Maintain `_reports/provenance.tsv` with one reviewed entry per inventory file:
   `staged_path<TAB>source_path`, using a literal tab and repository-relative, forward-slash
   paths in the first field. For example, only after the preceding review, a newly authored
   `docs/release-note.md` can have the source field `in-repository:docs/release-note.md`.
   Keep both fields nonempty and paths unique; preserve copied-file origins. Empty lines and
   lines beginning with `#` are ignored. The parser requires two tab-separated fields, but
   does not enforce nonempty values or uniqueness (a later duplicate overwrites the earlier
   one), so review those properties explicitly. Never bulk-generate self-origin declarations.

4. Run the existing gate on that same snapshot and retain its exit status and findings:

   ```bash
   python3 _tools/wall_check.py .
   ```

   Status `0` means no wall hit under the configured patterns and declarations. Status `2`
   means the manifest is absent or invalid: it is not decodable as UTF-8, or contains a
   nonempty, non-comment line that is not exactly two tab-separated fields. There is no usable
   provenance check in these cases. A readable, valid manifest
   with an omitted per-file entry instead returns `1`, as do prohibited source origins and
   prohibited content. An empty manifest with inventoried files therefore returns `1`.
   Resolve the reported cause and repeat review after any snapshot or ledger change; neither
   a bypass flag nor a replacement origin is a remedy. This gate checks declarations and
   content patterns, not whether the declared origin is true. It does not replace the other
   publication gates or the review/owner gate described in `STAGING_README.md`.

For acceptance, apply this procedure to a disposable copy of the release snapshot, keeping
its fixture ledger private. Record the actual copy sources for the snapshot files and their
retained source history; review a newly authored, innocuous file individually and add its
in-repository entry. Require status `0`. From that accepted fixture, change one condition at
a time, invoking the same gate each time: remove the manifest (`2`), replace it with a
malformed line (`2`), omit the authored file's entry (`1`), give that entry a synthetic
prohibited imported origin such as `fixture/memory/import.md` (`1`), and restore the reviewed
origin but plant content selected from `WALL_CONTENT_PATTERNS` (`1`). Restore the accepted
fixture between cases and require `0` again at the end. These deliberate plants belong only
in the disposable copy; their results demonstrate the gate's dispositions, not real release
provenance. Retain commands, statuses, and the review record before discarding the copy.


The sourced-invocation regression covers all three routes with a preflight fixture
that returns failure, retaining the actual caller guards and route dispatch. It
initializes the hook digest and uses a valid installed hook so a later parity
refusal cannot mask a missing caller guard. The separate legacy real-scanner
failure case remains a preservation control. Each caller guard is mutation-tested
independently. Short publishing names receive a specific full-ref requirement;
`refs/original/` refusals retain their separate message and one-line CLI contract.

The explicit blank-code-point policy has its own membership digest in
`tests/fixtures/unicode16_blank.sha256`, separate from the UCD category digest.
Removing the null notehead or adding a code point must fail that pin.

Ground-paint measurement uses explicit attribute allowlists: root `svg` admits
`id`, `viewBox`, `width`, `height`, `role`, `aria-label`, `opacity`; the ground
`rect` admits `id`, `x`, `y`, `width`, `height`, `rx`, `ry`, `fill`, `fill-opacity`,
`opacity`, `stroke`, `stroke-width`, `stroke-opacity` and no children. Root viewport
extents must equal the viewBox extents. Geometry must describe circular corners;
paint and opacity must describe an opaque ground and optional centered stroke.
Solid paint accepts three/six-digit hex and sixteen basic CSS names (default black).

A ground fill `url(#id)` may reference one unique local `linearGradient`, directly
under the root or plain `defs` ancestors (only `id` attributes). Gradient attributes
are `id`, `x1`, `y1`, `x2`, `y2`, `gradientUnits`, `spreadMethod`, `opacity`,
`fill-opacity`, `style`. Coordinates must be finite numbers or percentages, units
must be objectBoundingBox/userSpaceOnUse and spread pad/reflect/repeat. Because
all stops are opaque, these linear geometries preserve full fill coverage.
Radial gradients are UNMEASURED: focal geometry is not modeled.
Stops admit `id`, `offset`, `stop-color`, `stop-opacity`, `opacity`, `fill-opacity`,
`style`, with no children. Inline style admits only opacity/fill-opacity and, on
stops, stop-color/stop-opacity. Every supplied opacity must equal 1; stop colors
must be explicit opaque solids. Duplicate/unknown declarations and inheritance
are UNMEASURED, as are all other attributes on the modeled elements.

Animation elements (including animation by href), stylesheets, scripts, event
handlers, foreign elements, processing instructions other than the XML declaration,
and DOCTYPE/entities anywhere in the document are UNMEASURED. Later artwork is
otherwise unconstrained; it cannot introduce those document-wide effects.
The checker reports `ground shape : MEASURED` when it checks alpha coverage;
that does not assert RGB accuracy or a current SVG/PNG stamp. The committed opaque
linear gradient can be measured; stale render stamps still require regeneration.


All measured numeric attributes and inline opacity values share the CSS Syntax 3
number-token grammar, an ASCII subset of SVG 1.1 number: optional sign, integer
or fraction, optional exponent. Non-finite results, Unicode digits, underscores,
trailing decimal points and unit suffixes such as `px` are UNMEASURED. Only
modeled gradient coordinates/offsets additionally accept `%`. XML whitespace
may surround numbers; viewBox additionally admits comma separators.

The root must be `svg` in `http://www.w3.org/2000/svg`. An unnamespaced XML
document or a different namespace is UNMEASURED.

Gradient percentages use one number token immediately followed by `%`: `50 %`,
`50%%` and `%` are UNMEASURED. Like plain numbers, percentages admit outer XML
whitespace (`50% ` is accepted); whitespace inside the token is refused.
