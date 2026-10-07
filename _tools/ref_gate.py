#!/usr/bin/env python3
"""
ref_gate.py — the publishing gate's REF layer.

The content gates (wall_check.py, scan_gate.py) answer "is the working tree safe to
publish?". Neither can answer "what would a push actually publish?" — because a push
publishes REFS, not a worktree: `git push --all` publishes every local branch and
`git push --mirror` every local ref, including ones a history rewrite left behind.

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
     A tag is exempt only when its annotation chain ends at a commit that is an ancestor of
     (or equal to) the publishing ref's commit; a tag on unpublished history, or on a tree or
     blob, fails like any stray ref.
  2. NO NEVER-PUBLISH CONTENT ON ANY REACHABLE REF — every entry name of every tree
     reachable from `--all`, not just the checked-out tree, and not only the one name
     rev-list prints for an object that sits under several names.
  3. NO AI CO-AUTHOR TRAILERS (Co-Authored-By naming a model, a model vendor or an
     assistant, as a whole word) on any reachable commit or in any tag annotation
     reachable from any ref, nested annotations included.

fleetops.publishRef is trimmed; an absent, empty, or whitespace-only value defaults
to refs/heads/main. A configured value must start with refs/ and pass
git check-ref-format. Invalid values refuse with one message and status 1.
refs/original/ is reserved for rewrite leftovers and cannot be configured for publication.
Tags are judged against the publishing ref's commit. When that branch is absent locally
(a pull-request checkout has only refs/remotes/origin/*), its refs/remotes/origin counterpart
is the anchor; with neither present, any ref under refs/tags/ makes the gate refuse with 2.
A configured publishing ref that does not point at a commit is refused with 1. Every git call
runs with --no-replace-objects: a push publishes real objects, so a local replace ref cannot
stand in for them (objects that only a replace ref reaches are still scanned).

Note rule 2 and 3 deliberately query the OBJECT layer (tree entries via
`cat-file --batch`, `log --format=%B`) rather than grepping rendered `git log` output: a text search over a
log matches the log's own prose about a thing (a commit *subject* saying "untrack root
__pycache__" is not a path), which produces confident false positives in both directions.

Mutation proof (run: `ref_gate.py --self-test`): builds a throwaway repo, plants a stray
ref carrying a banned blob and a banned trailer, and asserts the gate goes RED on each
rule. A gate that cannot be made to fail is indistinguishable from a gate that passes.

Exit: 0 = clean · 1 = violations · 2 = refused to run (cannot produce a trustworthy verdict)
A shallow clone refuses with 2: missing history can hide violations and makes tag ancestry unjudgeable.
"""

import os
import re
import subprocess
import sys
import tempfile

# Refs that are legitimately publishable. Anything else in a pushable namespace fails.
DEFAULT_PUBLISH_REF = "refs/heads/main"

# Namespaces this gate judges: what a branch or tag push (`--all`, `--tags`) sends, plus
# refs/original/ as a refusal target for rewrite leftovers, never an allowance.
# `push --mirror` also sends refs/remotes/* and every other namespace; this gate does not
# judge a mirror push.
PUSHABLE_PREFIXES = ("refs/heads/", "refs/tags/", "refs/original/")

BANNED_PATH = re.compile(r"(^|/)__pycache__(/|$)|\.pyc$|\.pyo$")
BANNED_TRAILER = re.compile(
    r"(?im)^co-authored-by:[^\n]*?"
    r"(?:\b(?:claude|anthropic|chatgpt|gpt|openai|codex|gemini|gemma|grok|xai|qwen|deepseek"
    r"|llama|mistral|kimi|moonshot|glm|copilot|assistant)\b|\b(?-i:AI)\b)"
)

GIT = ["git", "--no-replace-objects"]


def git(args, cwd):
    """Run a git command, returning stdout. Never masks a failure behind a pipe."""
    p = subprocess.run(
        GIT + args, cwd=cwd, capture_output=True, text=True
    )
    if p.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed rc={p.returncode}: {p.stderr.strip()}")
    return p.stdout


def git_bytes(args, cwd, input=None):
    """Run a git command on bytes (tree bodies and file names are not text)."""
    p = subprocess.run(GIT + args, cwd=cwd, capture_output=True, input=input)
    if p.returncode != 0:
        stderr = p.stderr.decode(errors='replace') if isinstance(p.stderr, bytes) else p.stderr
        raise RuntimeError(
            f"git {' '.join(args)} failed rc={p.returncode}: "
            f"{stderr.strip()}")
    return p.stdout


def publishable_refs(repo):
    """An adopter may select one publishing ref; absent config preserves the CI default."""
    result = subprocess.run(GIT + ["config", "--get", "fleetops.publishRef"],
                            cwd=repo, capture_output=True, text=True)
    if result.returncode == 1:
        return {DEFAULT_PUBLISH_REF}
    if result.returncode != 0:
        raise RuntimeError("cannot read fleetops.publishRef")
    ref = result.stdout.strip()
    if not ref:
        return {DEFAULT_PUBLISH_REF}
    if not ref.startswith("refs/"):
        raise RuntimeError("fleetops.publishRef must name a full ref beginning with refs/")
    if ref.startswith("refs/original/"):
        raise RuntimeError("fleetops.publishRef must name a full publishing ref outside refs/original/")
    if subprocess.run(GIT + ["check-ref-format", ref], cwd=repo,
                      capture_output=True).returncode != 0:
        raise RuntimeError("fleetops.publishRef must name a valid full ref")
    return {ref}


def publish_commit(repo, ref):
    """Resolve a ref to its commit sha.

    Returns (sha or None, reason) where reason is "ok", "absent", or "not-a-commit".
    Raises RuntimeError on unexpected git failures.
    """
    out = git(["for-each-ref", "--format=%(refname) %(objectname)", ref], repo)
    sha = None
    for line in out.splitlines():
        name, _, objectname = line.partition(" ")
        if name == ref:
            sha = objectname.strip()
            break
    if sha is None:
        return None, "absent"
    final, kind, _ = tag_chain(repo, sha)
    if kind == "commit":
        return final, "ok"
    return None, "not-a-commit"


def publish_anchor(repo, ref):
    """Determine the anchor ref and commit for tag ancestry checks.

    Returns (anchor_ref or None, commit_sha or None, reason).
    """
    sha, reason = publish_commit(repo, ref)
    if reason == "ok":
        return ref, sha, "ok"
    if reason == "not-a-commit":
        return ref, None, "not-a-commit"
    # absent locally: a pull-request checkout has no local branches, only refs/remotes/origin/*
    if ref.startswith("refs/heads/"):
        remote = "refs/remotes/origin/" + ref[len("refs/heads/"):]
        rsha, rreason = publish_commit(repo, remote)
        if rreason == "ok":
            return remote, rsha, "remote"
    return None, None, "absent"


def tag_chain(repo, sha):
    """Follow an annotated-tag chain to its final object.

    Returns (final_object_sha, final_object_type, [(tag_sha, message), ...]).
    """
    obj = sha
    messages = []
    seen = set()
    while True:
        kind = git(["cat-file", "-t", obj], repo).strip()
        if kind != "tag":
            return obj, kind, messages
        if obj in seen:
            raise RuntimeError(f"tag chain loops at {sha}")
        seen.add(obj)
        body = git(["cat-file", "tag", obj], repo)
        header, _, message = body.partition("\n\n")
        messages.append((obj, message))
        obj = None
        for line in header.splitlines():
            if line.startswith("object "):
                obj = line[len("object "):].strip()
                break
        if obj is None:
            raise RuntimeError(f"tag chain malformed at {sha}")


def stray_refs(repo, allowed=None, target=None):
    """Rule 1 — every ref a push could carry must be allow-listed."""
    if allowed is None:
        allowed = publishable_refs(repo)
        ref = next(iter(allowed))
        _, target, reason = publish_anchor(repo, ref)
        if reason not in ("ok", "remote"):
            target = None
    out = git(["for-each-ref", "--format=%(refname) %(objectname)"], repo)
    strays = []
    for line in out.splitlines():
        if not line.strip():
            continue
        name, _, sha = line.partition(" ")
        if not name.startswith(PUSHABLE_PREFIXES):
            continue  # refs/remotes/* etc — not publishable
        if name in allowed:
            continue
        if name.startswith("refs/tags/"):
            final, kind, _ = tag_chain(repo, sha)
            if kind == "commit" and target is not None:
                mb = subprocess.run(
                    GIT + ["merge-base", "--is-ancestor", final, target],
                    cwd=repo, capture_output=True, text=True
                )
                if mb.returncode == 0:
                    continue  # exempt: tag peels to a published commit
                elif mb.returncode == 1:
                    pass  # not an ancestor -> stray
                else:
                    raise RuntimeError(f"merge-base failed rc={mb.returncode}: {mb.stderr.strip()}")
        strays.append((name, sha[:9]))
    return strays


def banned_objects(repo):
    """Rule 2 — never-publish names in any tree reachable from ANY ref.

    Every entry of every reachable tree is read, because rev-list names each object
    only once and a banned name can share its object with an allowed one.
    """
    out = git(["rev-list", "--all", "--objects", "--no-object-names"], repo)
    ids = [line.strip() for line in out.splitlines() if line.strip()]
    if not ids:
        return []
    fmt = git(["rev-parse", "--show-object-format"], repo).strip()
    if fmt == "sha1": hash_len = 20
    elif fmt == "sha256": hash_len = 32
    else: raise RuntimeError(f"unknown object format {fmt!r}")

    checks = git_bytes(["cat-file", "--batch-check=%(objectname) %(objecttype)"], repo,
                       input=("\n".join(ids) + "\n").encode())
    lines = checks.decode().splitlines()
    if len(lines) != len(ids):
        raise RuntimeError("object type batch truncated")
    trees = []
    for obj, line in zip(ids, lines):
        parts = line.split(" ")
        if (len(parts) != 2 or parts[0] != obj or
                parts[1] not in ("commit", "tree", "blob", "tag")):
            raise RuntimeError(f"cannot read object type: {line!r}")
        if parts[1] == "tree":
            trees.append(parts[0])
    if not trees:
        return []

    body = git_bytes(["cat-file", "--batch"], repo, input=("\n".join(trees) + "\n").encode())
    hits = []
    seen = set()
    pos = 0
    for tree in trees:
        header_end = body.find(b"\n", pos)
        if header_end == -1:
            raise RuntimeError("tree batch truncated")
        header = body[pos:header_end].decode().split(" ")
        if len(header) != 3 or header[:2] != [tree, "tree"]:
            raise RuntimeError(f"unexpected batch header {header!r}")
        try:
            size = int(header[2])
        except ValueError:
            raise RuntimeError(f"unexpected batch header {header!r}") from None
        if size < 0:
            raise RuntimeError(f"unexpected batch header {header!r}")
        content = body[header_end + 1:header_end + 1 + size]
        if len(content) != size:
            raise RuntimeError("tree batch truncated")
        pos = header_end + 1 + size
        if body[pos:pos + 1] != b"\n":
            raise RuntimeError("tree batch truncated")
        pos += 1
        i = 0
        while i < len(content):
            space = content.find(b" ", i)
            nul = content.find(b"\0", space + 1) if space != -1 else -1
            if space == -1 or nul == -1 or nul + 1 + hash_len > len(content):
                raise RuntimeError(f"tree {tree[:9]} malformed")
            name = os.fsdecode(content[space + 1:nul])
            i = nul + 1 + hash_len
            if BANNED_PATH.search(name) and (tree, name) not in seen:
                seen.add((tree, name))
                hits.append((tree[:9], name))
    if pos != len(body):
        raise RuntimeError("unexpected trailing tree batch data")
    return hits


def banned_trailers(repo):
    """Rule 3 — AI-attribution trailers on any reachable commit or tag message."""
    out = git(["log", "--all", "--format=%H%x00%B%x00%x00"], repo)
    hits = []
    for rec in out.split("\x00\x00"):
        if not rec.strip():
            continue
        sha, _, body = rec.partition("\x00")
        for m in BANNED_TRAILER.finditer(body):
            hits.append((sha.strip()[:9], m.group(0).strip()))

    # Scan tag annotations reachable from EVERY ref, not only refs/tags/.
    seen_tags = set()
    tags_out = git(["for-each-ref", "--format=%(objecttype) %(objectname)"], repo)
    for line in tags_out.splitlines():
        if not line.strip():
            continue
        objtype, _, sha = line.partition(" ")
        if objtype != "tag":
            continue
        _, _, messages = tag_chain(repo, sha)
        for tag_sha, message in messages:
            if tag_sha in seen_tags:
                continue
            seen_tags.add(tag_sha)
            for m in BANNED_TRAILER.finditer(message):
                hits.append(("tag " + tag_sha[:9], m.group(0).strip()))

    return hits


def check(repo, quiet=False):
    def say(*a):
        if not quiet:
            print(*a)

    if not os.path.isdir(os.path.join(repo, ".git")):
        sys.stderr.write(f"ref_gate: {repo} is not a git repo — refusing to emit a verdict\n")
        return 2

    shallow_result = subprocess.run(
        GIT + ["rev-parse", "--is-shallow-repository"],
        cwd=repo, capture_output=True, text=True
    )
    if shallow_result.returncode != 0:
        sys.stderr.write(f"ref_gate: cannot tell whether {repo} is shallow — refusing to emit a verdict\n")
        return 2
    if shallow_result.stdout.strip() == "true":
        sys.stderr.write(f"ref_gate: {repo} is a shallow clone — history is incomplete, so ancestry and reachable-object checks cannot be judged; fetch full history (git fetch --unshallow; in GitHub Actions, checkout with fetch-depth: 0) — refusing to emit a verdict\n")
        return 2

    try:
        allowed = publishable_refs(repo)
    except RuntimeError as error:
        say(f"ref_gate: REFUSED {error}")
        return 1

    ref = next(iter(allowed))

    try:
        anchor_ref, target, reason = publish_anchor(repo, ref)

        if reason == "not-a-commit":
            say(f"ref_gate: REFUSED fleetops.publishRef {ref} does not point at a commit")
            return 1

        if reason == "absent":
            tags_out = git(["for-each-ref", "--format=%(refname)", "refs/tags/"], repo)
            if tags_out.strip():
                sys.stderr.write(f"ref_gate: publishing ref {ref} is absent (and no refs/remotes/origin counterpart) — tags cannot be judged; refusing to emit a verdict\n")
                return 2
            say(f"  publishing ref {ref} absent; no tags to judge")

        if reason == "remote":
            say(f"  anchor: {anchor_ref} (local {ref} absent, as in a pull-request checkout)")

        strays = stray_refs(repo, allowed=allowed, target=target)
        objs = banned_objects(repo)
        trailers = banned_trailers(repo)
    except RuntimeError as error:
        sys.stderr.write(f"ref_gate: {error} — refusing to emit a verdict\n")
        return 2

    say(f"ref_gate: {repo}")
    say(f"  publishable allow-list: {sorted(allowed)}")

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

        # Tag exemption: a lightweight tag at main's tip must be GREEN.
        git(["tag", "v0.0.1"], repo)
        if check(repo, quiet=True) != 0:
            failures.append("BASELINE: a release tag on main's tip was not green — tag exemption is over-firing")

        # Mutation 4 — a tag on an orphan commit (unreachable from main) must FAIL.
        tree = git(["rev-parse", "HEAD^{tree}"], repo).strip()
        orphan = git(["-c", "user.name=t", "-c", "user.email=t@t", "commit-tree", tree, "-m", "orphan"], repo).strip()
        git(["tag", "v9.9.9", orphan], repo)
        if check(repo, quiet=True) != 1:
            failures.append("MUTATION 4: a tag on a commit unreachable from the publishing ref did NOT trip rule 1")
        git(["tag", "-d", "v9.9.9"], repo)
        git(["tag", "-d", "v0.0.1"], repo)

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
    print("ref_gate --self-test: PASSED — all 3 rules provably go red; tags exempt only on published history; prose/path discrimination holds")
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
