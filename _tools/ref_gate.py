#!/usr/bin/env python3
"""
ref_gate.py — the publishing gate's REF layer.

The content gates (wall_check.py, scan_gate.py) answer "is the working tree safe to
publish?". Neither can answer "what would a push actually publish?" — because a push
publishes REFS, not a worktree, and `git push --all` / `git push --mirror` publishes
EVERY local ref, including ones a history rewrite left behind.

This is not hypothetical. After this repo's history rewrite, `refs/heads/main` was clean
(0 AI-attribution trailers, 0 `__pycache__` blobs) while `refs/original/refs/heads/main`
and a `pre-rewrite-backup` branch both still carried 10 trailers and 40 `__pycache__`
paths. The remote held only `main`, so nothing had leaked — but a single `push --all`
would have re-published precisely what the rewrite removed, and no gate here was looking.
A rewrite is only true of the branch you rewrote.

Rules enforced:
  1. PUBLISHABLE REFS ONLY — every ref in a pushable namespace must be allow-listed.
     A `filter-branch` leftover (`refs/original/*`) or a rewrite backup branch is a
     FAIL, not a warning: it is one flag away from being published.
  2. NO NEVER-PUBLISH CONTENT ON ANY REACHABLE REF — scans objects reachable from
     `--all`, not just the checked-out tree.
  3. NO AI-ATTRIBUTION TRAILERS on any reachable commit.

Note rule 2 and 3 deliberately query the OBJECT layer (`rev-list --objects`,
`log --format=%B`) rather than grepping rendered `git log` output: a text search over a
log matches the log's own prose about a thing (a commit *subject* saying "untrack root
__pycache__" is not a path), which produces confident false positives in both directions.

Mutation proof (run: `ref_gate.py --self-test`): builds a throwaway repo, plants a stray
ref carrying a banned blob and a banned trailer, and asserts the gate goes RED on each
rule. A gate that cannot be made to fail is indistinguishable from a gate that passes.

Exit: 0 = clean · 1 = violations · 2 = refused to run (cannot produce a trustworthy verdict)
"""

import os
import re
import subprocess
import sys
import tempfile

# Refs that are legitimately publishable. Anything else in a pushable namespace fails.
PUBLISHABLE = {"refs/heads/main"}

# Namespaces a push can actually reach. refs/remotes/* is local bookkeeping, never pushed.
PUSHABLE_PREFIXES = ("refs/heads/", "refs/tags/", "refs/original/")

BANNED_PATH = re.compile(r"(^|/)__pycache__(/|$)|\.pyc$|\.pyo$")
BANNED_TRAILER = re.compile(r"(?im)^(Co-Authored-By|Co-authored-by):\s*.*(claude|gpt|codex|gemini|copilot|assistant|\bai\b)")


def git(args, cwd):
    """Run a git command, returning stdout. Never masks a failure behind a pipe."""
    p = subprocess.run(
        ["git"] + args, cwd=cwd, capture_output=True, text=True
    )
    if p.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed rc={p.returncode}: {p.stderr.strip()}")
    return p.stdout


def stray_refs(repo):
    """Rule 1 — every ref a push could carry must be allow-listed."""
    out = git(["for-each-ref", "--format=%(refname) %(objectname)"], repo)
    strays = []
    for line in out.splitlines():
        if not line.strip():
            continue
        name, _, sha = line.partition(" ")
        if not name.startswith(PUSHABLE_PREFIXES):
            continue  # refs/remotes/* etc — not publishable
        if name not in PUBLISHABLE:
            strays.append((name, sha[:9]))
    return strays


def banned_objects(repo):
    """Rule 2 — never-publish paths among objects reachable from ANY ref."""
    out = git(["rev-list", "--all", "--objects"], repo)
    hits = []
    for line in out.splitlines():
        parts = line.split(" ", 1)
        if len(parts) != 2:
            continue  # a bare commit/tree sha with no path
        sha, path = parts
        if BANNED_PATH.search(path):
            hits.append((sha[:9], path))
    return hits


def banned_trailers(repo):
    """Rule 3 — AI-attribution trailers on any reachable commit."""
    out = git(["log", "--all", "--format=%H%x00%B%x00%x00"], repo)
    hits = []
    for rec in out.split("\x00\x00"):
        if not rec.strip():
            continue
        sha, _, body = rec.partition("\x00")
        for m in BANNED_TRAILER.finditer(body):
            hits.append((sha.strip()[:9], m.group(0).strip()))
    return hits


def check(repo, quiet=False):
    def say(*a):
        if not quiet:
            print(*a)

    if not os.path.isdir(os.path.join(repo, ".git")):
        sys.stderr.write(f"ref_gate: {repo} is not a git repo — refusing to emit a verdict\n")
        return 2

    strays = stray_refs(repo)
    objs = banned_objects(repo)
    trailers = banned_trailers(repo)

    say(f"ref_gate: {repo}")
    say(f"  publishable allow-list: {sorted(PUBLISHABLE)}")

    if strays:
        say(f"  [FAIL] {len(strays)} ref(s) outside the allow-list — a push --all/--mirror would publish these:")
        for name, sha in strays:
            say(f"         {name} @ {sha}")
    else:
        say("  [OK]   no stray publishable refs")

    if objs:
        say(f"  [FAIL] {len(objs)} never-publish object(s) reachable from --all:")
        for sha, path in objs[:20]:
            say(f"         {sha}  {path}")
        if len(objs) > 20:
            say(f"         … and {len(objs) - 20} more")
    else:
        say("  [OK]   no never-publish objects reachable")

    if trailers:
        say(f"  [FAIL] {len(trailers)} AI-attribution trailer(s) in reachable history:")
        for sha, t in trailers[:20]:
            say(f"         {sha}  {t}")
    else:
        say("  [OK]   no AI-attribution trailers")

    rc = 1 if (strays or objs or trailers) else 0
    say(f"  => {'VIOLATIONS' if rc else 'CLEAN'}")
    return rc


def _plant(repo, path, content, message):
    full = os.path.join(repo, path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w") as fh:
        fh.write(content)
    git(["add", "-A"], repo)
    git(["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", message], repo)


def self_test():
    """Mutation proof: each rule must be provably capable of going red."""
    failures = []
    with tempfile.TemporaryDirectory() as td:
        repo = os.path.join(td, "r")
        os.makedirs(repo)
        git(["init", "-q", "-b", "main"], repo)
        _plant(repo, "ok.py", "print('hi')\n", "init: clean commit")

        # Baseline: a clean repo on main alone must be GREEN, or the gate proves nothing.
        if check(repo, quiet=True) != 0:
            failures.append("BASELINE: a clean single-main repo was not green — gate is over-firing")

        # Mutation 1 — a stray ref (the exact filter-branch leftover shape).
        git(["update-ref", "refs/original/refs/heads/main", "refs/heads/main"], repo)
        if not stray_refs(repo):
            failures.append("MUTATION 1: a planted refs/original/* leftover did NOT trip rule 1")
        if check(repo, quiet=True) != 1:
            failures.append("MUTATION 1: gate did not go red on a stray ref")
        git(["update-ref", "-d", "refs/original/refs/heads/main"], repo)

        # Mutation 2 — a banned object reachable only from a NON-checked-out ref.
        git(["checkout", "-q", "-b", "sidecar"], repo)
        _plant(repo, "pkg/__pycache__/mod.cpython-312.pyc", "\x00compiled\x00", "add: compiled artifact")
        git(["checkout", "-q", "main"], repo)
        hits = banned_objects(repo)
        if not hits:
            failures.append("MUTATION 2: a .pyc reachable only from a sidecar branch did NOT trip rule 2")
        if check(repo, quiet=True) != 1:
            failures.append("MUTATION 2: gate did not go red on an off-branch banned object")

        # Mutation 3 — an AI-attribution trailer on a non-main ref.
        git(["checkout", "-q", "sidecar"], repo)
        _plant(repo, "note.txt", "x\n", "chore: thing\n\nCo-Authored-By: Claude <noreply@anthropic.com>")
        git(["checkout", "-q", "main"], repo)
        if not banned_trailers(repo):
            failures.append("MUTATION 3: a planted Co-Authored-By trailer did NOT trip rule 3")
        if check(repo, quiet=True) != 1:
            failures.append("MUTATION 3: gate did not go red on an attribution trailer")

        # Prose-vs-path discrimination: a commit whose MESSAGE says __pycache__ but which
        # adds no such path must NOT trip rule 2. This is the false-positive that a
        # `git log | grep` implementation would produce.
        git(["checkout", "-q", "-b", "prose"], repo)
        _plant(repo, "clean.txt", "y\n", "fix: untrack root __pycache__ (swept in by add -A)")
        git(["checkout", "-q", "main"], repo)
        prose_hits = [h for h in banned_objects(repo) if "clean.txt" in h[1]]
        if prose_hits:
            failures.append("DISCRIMINATION: a commit MESSAGE mentioning __pycache__ was read as a path")

    if failures:
        print("ref_gate --self-test: FAILED")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("ref_gate --self-test: PASSED — all 3 rules provably go red; prose/path discrimination holds")
    return 0


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        sys.exit(self_test())
    target = None
    for a in sys.argv[1:]:
        if not a.startswith("-"):
            target = a
    if target is None:
        target = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.exit(check(target))
