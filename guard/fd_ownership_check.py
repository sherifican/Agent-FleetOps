#!/usr/bin/env python3
"""fd_ownership_check.py — does anything stand between a descriptor acquisition and the ``try``
whose ``finally`` releases it: a STATEMENT, or a BLOCK the interpreter has to leave?

THAT IS THE WHOLE QUESTION, and the scope is deliberate. The block half was added in the
sixty-fifth round, after two reviewers found by reading what this file passed by counting: an
acquisition in one ``try`` and its release in the ``finally`` of a different one has zero
statements between the two and is still a window, because leaving the first block and entering
the second is work done with the descriptor allocated and nothing registered to release it. An earlier draft of this checker tried to
certify a stronger property: that the shape "slot = None; try: slot = acquire(); finally: if slot
is not None: release(slot)" CLOSES the leak class. It does not, and an adversarial review refuted
it before this file was finished. ``os.open`` returns a raw integer. A cancellation delivered
between the syscall returning and the bytecode that binds the name leaves the slot still None, the
``finally`` releases nothing, and the descriptor is leaked with no owner — the original defect,
unchanged, inside the shape that was supposed to prevent it. The language reference and PEP 343
say this plainly: a KeyboardInterrupt can arrive between any two virtual machine opcodes.

So:

  WHAT THIS COVERS. Source-level gaps, of two kinds. A STATEMENT standing between an acquisition
  and its owner is a window wide enough to hold arbitrary work — another call, another open, a
  return — and every historical instance of this defect in the scanned module is one. A BLOCK
  standing between them is narrower and just as real: the statement count is zero, and the
  interpreter still has to unwind one ``try`` and install another. A lint can see both, because
  both are structure.

  WHAT THIS DOES NOT COVER, and cannot. The window between the acquiring syscall returning and
  the name being bound. No source check can see it, and this one does not pretend to: the
  disclosure is printed with every clean run, not buried in a comment here, because a gate that
  reports success while a class it appears to cover is still open is worse than no gate.

A rejection is NOT a proof of a bug. It means: REWRITE THIS SITE INTO A KNOWN-GOOD SHAPE, so the
ownership is legible to a reader without either of us having to reason about control flow. Every
site this checker cannot classify is reported as an explicit ``unsupported-shape`` entry — never
as a pass. If supporting a real shape would need a control-flow interpreter, the shape stays
unsupported and is reported as such; this file does not reimplement Python's semantics.

THE ACCEPTED LANGUAGE (five shapes, and nothing else):

  A. acquire-then-own — the acquisition statement is IMMEDIATELY followed, in the same statement
     list, by the ``try`` whose ``finally`` releases the slot. Zero statements between.
         fd = os.open(...)
         try:
             ...
         finally:
             _close_quietly(fd)

  B. acquired-inside-owner — the acquisition happens inside the owning ``try`` body, the slot is
     preset to None before that ``try``, and the ``finally`` releases it behind an explicit
     ``is not None`` test. Nothing can stand between, because there is no "between".

  C. with-item — ``with open(...) as f:``. The acquire and the enter cannot be separated. Only
     acquirers that return a context manager qualify.

  D. transfer-by-return — the acquisition is returned, or is assigned to a slot this function
     returns. A callee cannot own what it hands out; the obligation moves to the caller, where
     this checker picks it up again at the call site. STATED HOLE: whether EVERY path of such a
     helper returns the descriptor is a control-flow question, and this checker does not ask it.

  E. immediate-release — ``os.close(os.open(...))``. Degenerate, unambiguous, not worth a report.

REJECTED BY CONSTRUCTION — each of these is a shape that LOOKS like ownership and is not:

  * A CONTEXT-MANAGER FACTORY CALLED BEFORE THE CONTEXT IS ENTERED —
        handle = open(path)     <- the descriptor exists here
        with handle:            <- the guarantee starts here
    Shape C accepts only a ``with`` whose context expression IS the acquisition.
  * AN EXIT-STACK CALLBACK WHOSE ARGUMENT IS AN OPEN CALL —
        stack.callback(os.close, os.open(path))
    The acquisition is an argument: evaluated before the registration runs.
  * A DECORATOR THAT PROMISES OWNERSHIP. A decorator is not a statement in the body, and the body
    is what this reads.
  * A RELEASE IN AN ``except`` HANDLER instead of a ``finally`` — that is the success path leaking.
  * A TRUTHINESS GUARD — ``if fd:`` rather than ``if fd is not None:``. Descriptor 0 is a legal,
    successfully acquired descriptor and it is falsy, so a truthiness guard silently skips exactly
    the case where the acquisition worked.
  * A GUARD ON SUCCESS rather than on existence — ``if ok: close(fd)`` inside the finally.
  * TWO SLOTS SHARING ONE FINALLY as siblings — if the first release raises, the second never
    runs. The isolated spelling (``try: release(a) finally: release(b)``) is accepted.
  * A RELEASE OF A DIFFERENT SLOT than the one acquired.
  * AN ACQUISITION IN ONE ``try`` AND ITS RELEASE IN A DIFFERENT ONE — the ``cross-try`` verdict,
    added in the sixty-fifth round because shape A above could not see it:
        try:
            fd = os.open(...)
        except OSError:
            return None
        # a comment, which is not a statement
        try:
            ...
        finally:
            _close_quietly(fd)
    ZERO STATEMENTS stand between those two blocks, so the statement counter says nothing and the
    site read as ``acquire-then-own``. But the interpreter has to LEAVE the first block and ENTER
    the second, and while it is between them nothing is registered to release the descriptor: the
    handlers of the first block are gone and the ``finally`` of the second has not been installed.
    A comment cannot launder this — comments are not statements, and the statement count is 0 with
    or without one.
    THE TEST IS BLOCK CONTAINMENT, NOT ADJACENCY. A ``try`` that encloses the acquisition is
    harmless when it also encloses the owner (the owner is reached without leaving it) or when it
    is itself nested inside the owner (leaving it does not leave the owner). What is reported is a
    ``try`` holding the acquisition that holds NEITHER — a block the interpreter must exit, on a
    path where no ``finally`` names the slot.

DERIVED ACQUIRERS AND RELEASERS. The module's own helpers hand descriptors out
(``_open_dir_nofollow`` returns one, ``_stage_report`` returns a pair) and take them back
(``_close_quietly``, ``_rescue_then_close``). Both sets are derived from the module by one
shallow rule each, iterated to a fixpoint, rather than hand-listed where they would go stale:
  acquirer — some ``return`` yields, directly or as a tuple element, an acquiring call or a local
             name assigned from one;
  releaser — some call in the body passes the function's own parameter named ``fd`` to a known
             releaser at that releaser's descriptor position; the new releaser's position is
             where ``fd`` sits in its own signature.
Both rules are deliberately shallow. A helper that stores a descriptor on an object, or returns
one inside a container, is not derived, and its call sites are therefore never examined. That is
a stated hole, not a silent one.

USAGE
    python3 guard/fd_ownership_check.py [path ...]        # default: _tools/scan_gate.py
Exit status 0 when every acquisition site is in the accepted language, 1 otherwise.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

# The sentence a clean run must carry. Printed, not commented: the scope limit travels with the
# verdict or it is not a scope limit.
COVERAGE_DISCLOSURE = (
    "COVERAGE: among the acquisitions this lint can classify as OWNED, no source-level statement "
    "stands between one and its owner, and no block boundary does either — an acquisition whose "
    "enclosing try is not the try whose finally releases it is reported as cross-try, not as "
    "clean. THE SENTENCE SAYS NOTHING ABOUT SITES REPORTED UNOWNED, and that qualification is the "
    "whole of it: this lint reads a try/finally as ownership and does NOT read a `with` block as "
    "one, so an acquisition handed to a context manager is listed unowned, and statements standing "
    "between such an acquisition and its `with` are outside everything above. Three sites in the "
    "scanned module are exactly that shape today. The unqualified version of this sentence was "
    "false for them, and a coverage claim in a coverage instrument is the last place a false one "
    "belongs (team review, 51a4686). This also does NOT close the window between the acquiring "
    "syscall returning and the name being bound — a cancellation there leaves the slot unset and "
    "the descriptor unowned, and no source check can see it, because it falls between two "
    "bytecodes and not between two statements."
)

# Standard-library calls that hand back a NEW descriptor. Narrow on purpose: a name not in here is
# not an acquisition as far as this checker is concerned, and it says so rather than implying it
# swept for things it does not know.
STDLIB_ACQUIRERS = {
    "open", "io.open",
    "os.open", "os.fdopen", "os.dup", "os.dup2", "os.pipe", "os.pipe2",
    "os.openpty", "os.memfd_create", "os.eventfd",
    "tempfile.mkstemp", "tempfile.TemporaryFile", "tempfile.NamedTemporaryFile",
    "socket.socket", "socket.socketpair",
}

# Of those, the ones whose return value is a CONTEXT MANAGER, so shape C can apply. An ``int``
# from os.open in a ``with`` is a TypeError, not an ownership claim.
CONTEXT_MANAGER_ACQUIRERS = {
    "open", "io.open", "os.fdopen",
    "tempfile.TemporaryFile", "tempfile.NamedTemporaryFile",
    "socket.socket", "socket.socketpair",
}

# Releasers, mapped to WHICH positional argument is the descriptor. Knowing the position matters:
# `_rescue_then_close(dirfd, fd, via_proc)` names three slots and releases one of them, and a
# checker that counted every Name in the call would report the directory descriptor as released.
STDLIB_RELEASERS = {"os.close": 0, "os.closerange": 0}

# ``os.fdopen(fd, ..., closefd=False)`` BORROWS an existing descriptor rather than acquiring one:
# the caller still owns it and still has to release it. Counting the borrow would double-count the
# site that is already guarded, and the lint would then be satisfied by guarding the borrow.
BORROW_KWARG = "closefd"


# ---------------------------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------------------------

def _dotted(node: ast.AST) -> str | None:
    """``os.path.isfile`` -> "os.path.isfile"; anything not a plain dotted name -> None."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    parts.append(node.id)
    return ".".join(reversed(parts))


def _callee(call: ast.Call) -> str | None:
    return _dotted(call.func)


def _is_borrowing_fdopen(call: ast.Call) -> bool:
    if _callee(call) not in ("os.fdopen", "fdopen"):
        return False
    for kw in call.keywords:
        if kw.arg == BORROW_KWARG and isinstance(kw.value, ast.Constant) and kw.value.value is False:
            return True
    return False


def _own_statements(node: ast.AST):
    """Every statement inside NODE, not descending into nested function or class bodies: their
    statements run at a different time and are visited as their own scope."""
    stack = [node]
    while stack:
        cur = stack.pop()
        for child in ast.iter_child_nodes(cur):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and child is not node:
                continue
            stack.append(child)
            if isinstance(child, ast.stmt):
                yield child


def _contains(node: ast.AST, target: ast.AST) -> bool:
    return any(child is target for child in ast.walk(node))


def _slots_of(target: ast.AST) -> list[str]:
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, (ast.Tuple, ast.List)):
        out = []
        for elt in target.elts:
            out.extend(_slots_of(elt))
        return out
    return []


class _Function:
    def __init__(self, node, qualname):
        self.node = node
        self.qualname = qualname


def _functions(tree: ast.Module) -> list[_Function]:
    out = []

    def visit(node, prefix):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qual = prefix + child.name
                out.append(_Function(child, qual))
                visit(child, qual + ".")
            elif isinstance(child, ast.ClassDef):
                visit(child, prefix + child.name + ".")
            else:
                visit(child, prefix)

    visit(tree, "")
    return out


def _scope_calls(scope: ast.AST) -> list[ast.Call]:
    """Calls that BELONG to SCOPE: inside its statements, not inside a nested function or a lambda
    body (both run at a different time and own their own descriptors)."""
    deferred = set()
    for stmt in _own_statements(scope):
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            for sub in ast.walk(stmt):
                deferred.add(id(sub))
        for sub in ast.walk(stmt):
            if isinstance(sub, ast.Lambda):
                for inner in ast.walk(sub.body):
                    deferred.add(id(inner))
    seen, out = set(), []
    for stmt in _own_statements(scope):
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for sub in ast.walk(stmt):
            if isinstance(sub, ast.Call) and id(sub) not in deferred and id(sub) not in seen:
                seen.add(id(sub))
                out.append(sub)
    return out


def _statement_lists(node: ast.AST):
    """Every list of statements in NODE: its own body and every block inside it."""
    for cur in [node] + list(_own_statements(node)):
        for field in ("body", "orelse", "finalbody"):
            value = getattr(cur, field, None)
            if isinstance(value, list) and value and isinstance(value[0], ast.stmt):
                yield value
        if isinstance(cur, ast.Try):
            for handler in cur.handlers:
                yield handler.body


# ---------------------------------------------------------------------------------------------
# Derivation of the module's own acquirers and releasers
# ---------------------------------------------------------------------------------------------

def derive_acquirers(tree: ast.Module, base: set[str]) -> set[str]:
    """See DERIVED ACQUIRERS in the module docstring. One shallow rule, to a fixpoint."""
    funcs = _functions(tree)
    known = set(base)
    changed = True
    while changed:
        changed = False
        for fn in funcs:
            if fn.node.name in known:
                continue
            acquired = set()
            for stmt in _own_statements(fn.node):
                if isinstance(stmt, ast.Assign) and isinstance(stmt.value, ast.Call) \
                        and _callee(stmt.value) in known and not _is_borrowing_fdopen(stmt.value):
                    for tgt in stmt.targets:
                        acquired.update(_slots_of(tgt))
            for stmt in _own_statements(fn.node):
                if not isinstance(stmt, ast.Return) or stmt.value is None:
                    continue
                pieces = stmt.value.elts if isinstance(stmt.value, ast.Tuple) else [stmt.value]
                if any((isinstance(p, ast.Call) and _callee(p) in known)
                       or (isinstance(p, ast.Name) and p.id in acquired) for p in pieces):
                    known.add(fn.node.name)
                    changed = True
                    break
    return known


def derive_releasers(tree: ast.Module, base: dict[str, int]) -> dict[str, int]:
    """See DERIVED ACQUIRERS AND RELEASERS in the module docstring. A function is a releaser if it
    hands its own ``fd`` parameter to a known releaser at that releaser's descriptor position."""
    funcs = _functions(tree)
    known = dict(base)
    changed = True
    while changed:
        changed = False
        for fn in funcs:
            name = fn.node.name
            if name in known:
                continue
            args = [a.arg for a in fn.node.args.args]
            if "fd" not in args:
                continue
            index = args.index("fd")
            for stmt in _own_statements(fn.node):
                for sub in ast.walk(stmt):
                    if not isinstance(sub, ast.Call):
                        continue
                    pos = known.get(_callee(sub))
                    if pos is None or pos >= len(sub.args):
                        continue
                    arg = sub.args[pos]
                    if isinstance(arg, ast.Name) and arg.id == "fd":
                        known[name] = index
                        changed = True
                        break
                if name in known:
                    break
    return known


# ---------------------------------------------------------------------------------------------
# Release analysis
# ---------------------------------------------------------------------------------------------

def _released_by(stmt: ast.AST, releasers: dict[str, int]) -> set[str]:
    """Slots a statement releases: the descriptor-position argument of a releaser call, or the
    receiver of an ``x.close()``. Not every Name in the call — a releaser that also takes the
    directory descriptor would otherwise read as releasing it."""
    slots = set()
    for sub in ast.walk(stmt):
        if not isinstance(sub, ast.Call):
            continue
        callee = _callee(sub)
        pos = releasers.get(callee)
        if pos is not None and pos < len(sub.args) and isinstance(sub.args[pos], ast.Name):
            slots.add(sub.args[pos].id)
        elif isinstance(sub.func, ast.Attribute) and sub.func.attr == "close" \
                and isinstance(sub.func.value, ast.Name) and not sub.args:
            slots.add(sub.func.value.id)
    return slots


def _none_guard_slots(test: ast.AST) -> set[str]:
    """``slot is not None`` -> {slot}; an ``and`` of those -> all of them. NOTHING else — a bare
    ``if slot:`` is not here on purpose, because descriptor 0 is falsy and legitimately acquired."""
    slots = set()
    if isinstance(test, ast.Compare) and len(test.ops) == 1 and isinstance(test.ops[0], ast.IsNot) \
            and isinstance(test.left, ast.Name) and len(test.comparators) == 1 \
            and isinstance(test.comparators[0], ast.Constant) and test.comparators[0].value is None:
        slots.add(test.left.id)
    elif isinstance(test, ast.BoolOp) and isinstance(test.op, ast.And):
        for value in test.values:
            slots |= _none_guard_slots(value)
    return slots


def _truthy_guard_slots(test: ast.AST) -> set[str]:
    """``if slot:`` and ``if slot and ...:`` — the trap this checker exists to catch as well as
    the gap. Reported by name so the fix is obvious."""
    slots = set()
    if isinstance(test, ast.Name):
        slots.add(test.id)
    elif isinstance(test, ast.BoolOp) and isinstance(test.op, ast.And):
        for value in test.values:
            slots |= _truthy_guard_slots(value)
    return slots


def analyse_finally(finalbody, releasers) -> dict[str, str]:
    """How each slot is released in FINALBODY: "none-guarded", "truthy-guarded", "other-guarded"
    or "unguarded"."""
    found: dict[str, str] = {}
    rank = {"none-guarded": 3, "unguarded": 2, "other-guarded": 1, "truthy-guarded": 0}

    def record(slot, how):
        if rank[how] > rank.get(found.get(slot, "truthy-guarded"), -1) or slot not in found:
            found[slot] = how

    def walk(stmts, none_guarded, truthy_guarded, other_guarded):
        for stmt in stmts:
            if isinstance(stmt, ast.If):
                nones = _none_guard_slots(stmt.test)
                truthy = _truthy_guard_slots(stmt.test)
                other = not nones and not truthy
                walk(stmt.body, none_guarded | nones, truthy_guarded | truthy,
                     other_guarded or other)
                walk(stmt.orelse, none_guarded, truthy_guarded, other_guarded)
                continue
            if isinstance(stmt, ast.Try):
                walk(stmt.body, none_guarded, truthy_guarded, other_guarded)
                for handler in stmt.handlers:
                    walk(handler.body, none_guarded, truthy_guarded, other_guarded)
                walk(stmt.orelse, none_guarded, truthy_guarded, other_guarded)
                walk(stmt.finalbody, none_guarded, truthy_guarded, other_guarded)
                continue
            for slot in _released_by(stmt, releasers):
                if slot in none_guarded:
                    record(slot, "none-guarded")
                elif slot in truthy_guarded:
                    record(slot, "truthy-guarded")
                elif other_guarded:
                    record(slot, "other-guarded")
                else:
                    record(slot, "unguarded")

    walk(finalbody, set(), set(), False)
    return found


def shared_finally_slots(finalbody, releasers) -> set[str]:
    """Slots released as SIBLINGS of a release of a DIFFERENT slot at the same level. If the first
    such release raises, the later ones never run. The isolated spelling — each release alone in a
    ``try`` whose ``finally`` holds the next — nests, so its levels hold one slot each."""
    bad: set[str] = set()

    def level(stmts):
        here: set[str] = set()
        for stmt in stmts:
            if isinstance(stmt, ast.Try):
                level(stmt.body)
                for handler in stmt.handlers:
                    level(handler.body)
                level(stmt.orelse)
                level(stmt.finalbody)
                continue
            if isinstance(stmt, ast.If):
                here |= _collect(stmt.body) | _collect(stmt.orelse)
                continue
            here |= _released_by(stmt, releasers)
        if len(here) > 1:
            bad.update(here)

    def _collect(stmts) -> set[str]:
        out: set[str] = set()
        for stmt in stmts:
            if isinstance(stmt, ast.Try):
                level([stmt])
                continue
            if isinstance(stmt, ast.If):
                out |= _collect(stmt.body) | _collect(stmt.orelse)
                continue
            out |= _released_by(stmt, releasers)
        return out

    level(finalbody)
    return bad


# ---------------------------------------------------------------------------------------------
# Sites
# ---------------------------------------------------------------------------------------------

class Site:
    """One acquisition, and the verdict on whether anything stands between it and its owner."""

    def __init__(self, path, lineno, col, function, expr, callee):
        self.path = path
        self.lineno = lineno
        self.col = col
        self.function = function
        self.expr = expr
        self.callee = callee
        self.verdict = "unsupported-shape"
        self.shape = None
        self.detail = ""
        self.gap = None
        self.owner_lineno = None      # the try whose finally releases the slot, when there is one

    @property
    def ok(self) -> bool:
        return self.verdict == "ok"

    def __repr__(self):
        return "%s:%d %s %s -> %s%s" % (self.path, self.lineno, self.function, self.callee,
                                        self.verdict, " [%s]" % self.shape if self.shape else "")


def _enclosing_assign(scope: ast.AST, call: ast.Call):
    for stmt in _own_statements(scope):
        if isinstance(stmt, ast.Assign) and stmt.value is call:
            return stmt
    return None


def _returned_names(scope: ast.AST) -> set[str]:
    out = set()
    for stmt in _own_statements(scope):
        if isinstance(stmt, ast.Return) and stmt.value is not None:
            pieces = stmt.value.elts if isinstance(stmt.value, ast.Tuple) else [stmt.value]
            for piece in pieces:
                if isinstance(piece, ast.Name):
                    out.add(piece.id)
    return out


def _assigns_none_to(stmt: ast.stmt, slot: str):
    """True if STMT sets SLOT to None, False if it sets it to something else, None if untouched.
    ``slot = None`` and the pair spelling ``slot, other = (None, False)`` both count."""
    targets = stmt.targets if isinstance(stmt, ast.Assign) else (
        [stmt.target] if isinstance(stmt, ast.AnnAssign) else [])
    for tgt in targets:
        if isinstance(tgt, ast.Name) and tgt.id == slot:
            value = stmt.value
            return value is not None and isinstance(value, ast.Constant) and value.value is None
        if isinstance(tgt, (ast.Tuple, ast.List)) and slot in _slots_of(tgt):
            value = stmt.value
            if isinstance(value, (ast.Tuple, ast.List)) and len(value.elts) == len(tgt.elts):
                for elt_t, elt_v in zip(tgt.elts, value.elts):
                    if isinstance(elt_t, ast.Name) and elt_t.id == slot:
                        return isinstance(elt_v, ast.Constant) and elt_v.value is None
            return False
    return None


def _preset_to_none_before(container: list, owner: ast.Try, slot: str) -> bool:
    seen = False
    for stmt in container:
        if stmt is owner:
            return seen
        verdict = _assigns_none_to(stmt, slot)
        if verdict is not None:
            seen = verdict
    return False


def _enclosing_trys(scope: ast.AST, call: ast.Call) -> list[ast.Try]:
    """Every ``try`` in SCOPE that holds CALL anywhere — body, handler, orelse or finalbody —
    innermost last. These are the blocks the interpreter is inside at the moment the descriptor
    comes back."""
    holders = [stmt for stmt in _own_statements(scope)
               if isinstance(stmt, ast.Try) and _contains(stmt, call)]
    # Innermost last: a try that contains another is the outer one.
    holders.sort(key=lambda t: sum(1 for other in holders if other is not t and _contains(other, t)))
    return holders


def _escaped_blocks(scope: ast.AST, call: ast.Call, owner: ast.Try) -> list[ast.Try]:
    """The ``try`` blocks the interpreter must LEAVE to get from the acquisition to OWNER.

    A try enclosing the acquisition is harmless in exactly two situations, and this returns
    neither of them:

      * it also encloses OWNER — control reaches the owning block without leaving it;
      * it is nested INSIDE owner — leaving it does not leave the block that releases the slot.

    Anything else is a block the interpreter exits while the descriptor is allocated and nothing
    is registered to release it. Note that the count of STATEMENTS between the two can be zero in
    this shape, which is exactly why the statement counter could not see it.
    """
    out = []
    for block in _enclosing_trys(scope, call):
        if block is owner or _contains(block, owner) or _contains(owner, block):
            continue
        out.append(block)
    return out


def _statements_between(container: list, acq_stmt: ast.stmt, owner: ast.Try):
    try:
        i = container.index(acq_stmt)
        j = container.index(owner)
    except ValueError:
        return None
    return (j - i - 1) if j > i else None


def analyse(path: Path, source: str | None = None) -> list[Site]:
    """Classify every descriptor acquisition in PATH. Never raises on a classification it cannot
    make: that becomes an ``unsupported-shape`` site."""
    text = source if source is not None else Path(path).read_text(encoding="utf8")
    tree = ast.parse(text)
    acquirers = derive_acquirers(tree, STDLIB_ACQUIRERS)
    releasers = derive_releasers(tree, STDLIB_RELEASERS)
    lines = text.splitlines()

    scopes = [(tree, "<module>")] + [(fn.node, fn.qualname) for fn in _functions(tree)]
    sites: list[Site] = []
    for scope, qualname in scopes:
        for call in _scope_calls(scope):
            callee = _callee(call)
            if callee is None or callee not in acquirers or _is_borrowing_fdopen(call):
                continue
            expr = lines[call.lineno - 1].strip() if call.lineno - 1 < len(lines) else ""
            site = Site(str(path), call.lineno, call.col_offset, qualname, expr, callee)
            _classify(scope, call, site, releasers)
            sites.append(site)
    sites.sort(key=lambda s: (s.lineno, s.col))
    return sites


def _classify(scope, call, site, releasers) -> None:
    # --- shape E: released in the same expression ---------------------------------------------
    for stmt in _own_statements(scope):
        for sub in ast.walk(stmt):
            if isinstance(sub, ast.Call) and _callee(sub) in releasers:
                pos = releasers[_callee(sub)]
                if pos < len(sub.args) and sub.args[pos] is call:
                    site.verdict, site.shape, site.gap = "ok", "immediate-release", 0
                    return

    # --- shape C: the acquisition IS the context expression of a with -------------------------
    for stmt in _own_statements(scope):
        if isinstance(stmt, (ast.With, ast.AsyncWith)):
            for item in stmt.items:
                if item.context_expr is call:
                    if site.callee in CONTEXT_MANAGER_ACQUIRERS:
                        site.verdict, site.shape, site.gap = "ok", "with-item", 0
                    else:
                        site.detail = ("a with-item whose acquirer does not return a context "
                                       "manager: %s" % site.callee)
                    return

    # --- shape D: handed straight back to the caller ------------------------------------------
    for stmt in _own_statements(scope):
        if isinstance(stmt, ast.Return) and stmt.value is not None:
            pieces = stmt.value.elts if isinstance(stmt.value, ast.Tuple) else [stmt.value]
            if any(piece is call for piece in pieces):
                site.verdict, site.shape, site.gap = "ok", "transfer-by-return", 0
                return

    assign = _enclosing_assign(scope, call)
    if assign is None:
        parent = None
        for stmt in _own_statements(scope):
            for sub in ast.walk(stmt):
                if isinstance(sub, ast.Call) and sub is not call and any(
                        _contains(a, call) for a in list(sub.args) + [k.value for k in sub.keywords]):
                    parent = _callee(sub) or "<call>"
        if parent is not None:
            site.verdict = "unsupported-shape"
            site.detail = ("acquisition-in-call-argument: the descriptor exists before %s(...) "
                           "runs, so a raise while the other arguments evaluate loses it" % parent)
        else:
            site.verdict = "unowned"
            site.detail = ("the acquisition is not bound to a slot, so no finally can name it "
                           "(a comprehension source, a bare expression, an attribute target)")
        return

    slots = []
    for tgt in assign.targets:
        slots.extend(_slots_of(tgt))
    if not slots:
        site.verdict = "unsupported-shape"
        site.detail = "the acquisition is assigned to something that is not a plain name"
        return

    # shape D again: a slot this function returns is the caller's problem, not this block's.
    returned = _returned_names(scope)
    if any(slot in returned for slot in slots):
        site.verdict, site.shape, site.gap = "ok", "transfer-by-return", 0
        return

    owners = []
    for container in _statement_lists(scope):
        for stmt in container:
            if not isinstance(stmt, ast.Try) or not stmt.finalbody:
                continue
            released = analyse_finally(stmt.finalbody, releasers)
            shared = shared_finally_slots(stmt.finalbody, releasers)
            for slot in slots:
                if slot not in released:
                    continue
                inside = any(_contains(b, call) for b in stmt.body)
                acq_stmt = next((s for s in container if _contains(s, call)), None)
                gap = (None if acq_stmt is None or acq_stmt is stmt
                       else _statements_between(container, acq_stmt, stmt))
                owners.append({"try": stmt, "container": container, "slot": slot,
                               "guard": released[slot], "inside": inside, "gap": gap,
                               "shared": slot in shared})

    if not owners:
        site.verdict = "unowned"
        site.detail = ("no finally in %s releases %s — the descriptor survives any raise below the "
                       "acquisition" % (site.function, "/".join(slots)))
        return

    # The owner this site is ABOUT: the one containing the acquisition if there is one, else the
    # nearest below it in the same block. Naming the wrong owner is how a checker reports a true
    # defect with a false reason.
    owners.sort(key=lambda o: (not o["inside"], o["gap"] is None,
                               o["gap"] if o["gap"] is not None else 0))
    best = owners[0]
    owner, container, slot = best["try"], best["container"], best["slot"]
    site.owner_lineno = owner.lineno

    if best["shared"]:
        site.verdict = "shared-finally"
        site.detail = ("the finally at line %d releases %s beside a release of another slot at the "
                       "same level; if the earlier release raises, this one never runs"
                       % (owner.lineno, slot))
        return

    if best["guard"] == "truthy-guarded":
        site.verdict = "falsy-guard"
        site.detail = ("the finally at line %d releases %s behind `if %s:` rather than "
                       "`if %s is not None:`; descriptor 0 is falsy and legitimately acquired, so "
                       "the one case the guard exists for is the case it skips"
                       % (owner.lineno, slot, slot, slot))
        return
    if best["guard"] == "other-guarded":
        site.verdict = "unsupported-shape"
        site.detail = ("the finally at line %d releases %s behind a guard that is not about "
                       "whether the descriptor exists" % (owner.lineno, slot))
        return

    if best["inside"]:
        if best["guard"] != "none-guarded":
            site.verdict = "unsupported-shape"
            site.detail = ("the acquisition is inside the try at line %d and its finally releases "
                           "%s unconditionally, so a raise before the acquisition releases an "
                           "unbound or stale slot" % (owner.lineno, slot))
            return
        if not _preset_to_none_before(container, owner, slot):
            site.verdict = "unsupported-shape"
            site.detail = ("%s is not preset to None before the try at line %d that owns it, so "
                           "the None guard in its finally cannot mean 'not acquired yet'"
                           % (slot, owner.lineno))
            return
        site.verdict, site.shape, site.gap = "ok", "acquired-inside-owner", 0
        return

    if best["gap"] is None:
        site.verdict = "unsupported-shape"
        site.detail = ("the finally that releases %s is not in the block that holds the "
                       "acquisition; the nearest is the try at line %d" % (slot, owner.lineno))
        return
    if best["gap"] > 0:
        # STATEMENTS FIRST, BLOCKS SECOND. When a site has both, the older and more specific
        # message is the one printed; it already fails the gate, and two names for one site would
        # make the baseline below ambiguous.
        site.verdict = "gap"
        site.gap = best["gap"]
        site.detail = ("%d statement(s) stand between the acquisition and the try at line %d whose "
                       "finally releases %s — every one of them is a raise that leaks it"
                       % (best["gap"], owner.lineno, slot))
        return

    # --- ZERO STATEMENTS BETWEEN. Now ask whether a BLOCK stands between. ----------------------
    #
    # CONSERVATISM IS THE `gap is None` BRANCH ABOVE, not a second check here. A DELETED DRAFT of
    # this rule refused sites whose releasing block the ranking could not separate from another
    # candidate. It could never fire: by the time control reaches this line the owner is in the
    # same statement list as the acquisition and is the very next statement in it, and two
    # distinct statements cannot both be the next one. Anything genuinely undetermined — a release
    # in one branch and another in a sibling branch, a finally in a block the acquisition is not
    # in — has already returned unsupported-shape above with the acquisition's block unlocated,
    # and `cross-try-owner-in-another-block` in the arm file is the fixture that proves it. A rule
    # that can only ever say "clean" is not a rule, so that draft was removed rather than kept as
    # reassurance.
    escaped = _escaped_blocks(scope, call, owner)
    if escaped:
        inner = escaped[-1]
        site.verdict = "cross-try"
        site.gap = 0
        site.detail = ("the acquisition is inside the try at line %d, and the finally that "
                       "releases %s belongs to a DIFFERENT try at line %d. No statement stands "
                       "between them, but the interpreter must leave the first block and enter "
                       "the second, and in that interval the handlers of the first are gone and "
                       "the finally of the second is not installed yet, so a cancellation there "
                       "leaves %s bound with nothing to close it"
                       % (inner.lineno, slot, owner.lineno, slot))
        return

    site.verdict, site.shape, site.gap = "ok", "acquire-then-own", 0


# ---------------------------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------------------------

MARKS = {"ok": "OK", "gap": "GAP", "cross-try": "CROSS-TRY", "unowned": "UNOWNED",
         "falsy-guard": "FALSY-GUARD", "shared-finally": "SHARED-FINALLY",
         "unsupported-shape": "UNSUPPORTED"}


def report(sites: list[Site]) -> str:
    out = []
    for site in sites:
        out.append("%-14s %s:%d  %s  %s" % (MARKS[site.verdict], site.path, site.lineno,
                                            site.function, site.callee))
        if site.shape:
            out.append("               shape: %s" % site.shape)
        if site.detail:
            out.append("               %s" % site.detail)
    bad = [s for s in sites if not s.ok]
    out.append("")
    out.append("%d acquisition site(s): %d in the accepted language, %d not"
               % (len(sites), len(sites) - len(bad), len(bad)))
    for verdict in ("gap", "cross-try", "unowned", "falsy-guard", "shared-finally",
                    "unsupported-shape"):
        n = sum(1 for s in sites if s.verdict == verdict)
        if n:
            out.append("   %-16s %d" % (MARKS[verdict], n))
    # The disclosure rides with the VERDICT, clean or not. A reader who sees only the last line
    # has to see the scope limit in it.
    out.append(COVERAGE_DISCLOSURE)
    return "\n".join(out)


def main(argv: list[str]) -> int:
    paths = [Path(a) for a in argv[1:]]
    if not paths:
        paths = [Path(__file__).resolve().parents[1] / "_tools" / "scan_gate.py"]
    bad = 0
    for path in paths:
        sites = analyse(path)
        print(report(sites))
        bad += sum(1 for s in sites if not s.ok)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
