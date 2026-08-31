#!/usr/bin/env python3
"""one_writer_gate.py — one coder per repository, enforced at edit-transaction start.

The shipped default for concurrent editing is that there is none: ONE coder per repo.
An edit pass opening against a working tree that carries ANOTHER job's dirty files
refuses to start — commit or stash between passes. The concurrent-region alternative in
specs/driver-lock-protocol.md is a reference design, not shipped; this gate is the
supported path.

TWO MECHANISMS, AND THE FIRST ONE IS THE GATE
    1. A LOCK, acquired atomically. A dirty-tree snapshot is not a gate: two jobs that
       both look at the same CLEAN tree both see nothing to refuse, both proceed, and both
       write. Measured — both calls returned "proceed" against one tree. So proceeding now
       requires winning `O_CREAT|O_EXCL` on a lock file that records OWNER, SCOPE (the
       claimed paths), pid and time; the winner holds it for the transaction and releases
       it afterwards. Exactly one contender can win, whatever the tree looks like.
    2. The dirty-tree refusal, evaluated UNDER the lock: a tree carrying files outside
       this job's claim means someone was editing without the lock, and that is still a
       refusal.

    A lock left behind by a dead process is reported STALE and is NOT broken automatically
    — an auto-breaking lock is a lock with a social off-switch. `--break-stale` clears one,
    and refuses while the holder's pid is still alive. (Pids are reused; the check is a
    heuristic, and the record carries the creation time so an operator can judge.)

THE REFUSAL NAMES ONLY REAL PATHS
    Verbatim from the version-control ledger, and never assembled. Blindly splitting every
    porcelain line on " -> " fabricated `bar.txt` — a path that did not exist — out of an
    untracked file literally named `foo -> bar.txt`; only a rename or copy carries an
    original path, and in `--porcelain -z` it arrives as its own NUL-terminated field, with
    no quoting to strip. A refusal that garbles a path teaches its operator to ignore
    refusals, which is the social off-switch failure all over again. Deletions are named
    with their status letters, so a path that is dirty-because-absent does not read as a
    file you can go look at.

    Claims are normalized against the repository root before comparison, so an absolute
    `--claim` matches git's root-relative reporting instead of refusing the job's own file.

Usage: one_writer_gate.py [--repo DIR] [--claim FILE ...] [--owner NAME]
                          [--acquire | --release | --status | --break-stale] [--selftest]
  no mode flag   preflight only: reports what the gate WOULD say and reserves NOTHING.
  --acquire      take the lock and run the dirty-tree check under it; hold until --release.
  --release      release a lock this owner holds.

Exit codes: 0 proceed · 1 refuse (lock held elsewhere, or foreign dirty files, each
named) · 2 CANNOT CHECK (not a version-controlled tree — absence of the ledger is never
a pass).
Gate: guard/tests/test_one_writer_gate.py. Red demos: OW1 blinds the foreign-file
detection; OW2 drops O_EXCL so both contenders acquire; OW3 restores the blind " -> "
split so a refusal fabricates a path.
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import time

LOCK_NAME = "one-writer.lock"


# ── the version-control ledger ──────────────────────────────────────────────────────────
def repo_root(repo):
    """The repository root, or None when this is not a version-controlled tree."""
    p = subprocess.run(["git", "-C", repo, "rev-parse", "--show-toplevel"],
                       capture_output=True, text=True)
    if p.returncode != 0:
        return None
    return p.stdout.strip() or None


def _parse_porcelain_z(blob):
    """(xy, path) per entry, from `git status --porcelain -z`.

    -z is deliberate: it emits raw bytes with NO quoting, and it puts a rename/copy's
    ORIGINAL path in its own field. Both are what the naive line parser got wrong."""
    fields = [f for f in blob.split("\0") if f != ""]
    entries, i = [], 0
    while i < len(fields):
        f = fields[i]
        i += 1
        if len(f) < 4:
            continue
        xy, path = f[:2], f[3:]
        # ONLY a rename or copy carries an original path, and it is the NEXT field. Every
        # other status has exactly one path, which may itself contain " -> ".
        if "R" in xy or "C" in xy:
            if i < len(fields):
                i += 1
        entries.append((xy, path))
    return entries


def dirty_entries(repo):
    """[(xy, path), ...] as version control reports them, or None when not a repo."""
    p = subprocess.run(["git", "-C", repo, "status", "--porcelain", "-z"],
                       capture_output=True, text=True)
    if p.returncode != 0:
        return None
    return _parse_porcelain_z(p.stdout)


def dirty_files(repo):
    """Paths git reports as modified/untracked, or None when this is not a git tree."""
    entries = dirty_entries(repo)
    if entries is None:
        return None
    return [path for _xy, path in entries]


def normalize_claims(repo, claimed):
    """Claims as the ledger names them: root-relative, forward-slashed, normalized.

    An absolute --claim never matched git's root-relative porcelain, so a job's own file
    was refused as foreign."""
    root = repo_root(repo) or os.path.abspath(repo)
    out = set()
    for c in claimed:
        if not c:
            continue
        absolute = c if os.path.isabs(c) else os.path.join(repo, c)
        rel = os.path.relpath(os.path.normpath(absolute), root)
        out.add(rel.replace(os.sep, "/"))
    return out


def check(repo, claimed):
    """Returns (rc, foreign, dirty). rc 0 proceed · 1 refuse · 2 cannot check.

    This is the dirty-tree arm ONLY. On its own it reserves nothing — see acquire()."""
    dirty = dirty_files(repo)
    if dirty is None:
        return 2, [], []
    claimed_set = normalize_claims(repo, claimed)
    foreign = [f for f in dirty if f.replace(os.sep, "/") not in claimed_set]
    return (1 if foreign else 0), foreign, dirty


def describe(repo, paths):
    """Each path with its status letters and whether it is on disk, so a deletion does not
    read as a file the operator can go and look at."""
    entries = dict((path, xy) for xy, path in (dirty_entries(repo) or []))
    out = []
    for p in paths:
        xy = entries.get(p, "??")
        on_disk = os.path.exists(os.path.join(repo, p))
        out.append("%s  %s%s" % (xy, p, "" if on_disk else "   (deleted — not on disk)"))
    return out


# ── the lock ────────────────────────────────────────────────────────────────────────────
def lock_path(repo):
    """Inside the git directory, so taking the lock never dirties the working tree."""
    p = subprocess.run(["git", "-C", repo, "rev-parse", "--absolute-git-dir"],
                       capture_output=True, text=True)
    base = p.stdout.strip() if p.returncode == 0 and p.stdout.strip() else os.path.abspath(repo)
    return os.path.join(base, LOCK_NAME)


def held_by(repo):
    """The holder's record, {} when the lock file exists but carries no readable record,
    or None when the lock is free."""
    path = lock_path(repo)
    try:
        with open(path, encoding="utf-8") as fh:
            raw = fh.read()
    except OSError:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return {}


def _alive(pid):
    try:
        os.kill(int(pid), 0)
    except (OSError, TypeError, ValueError):
        return False
    return True


def acquire(repo, owner, scope):
    """Atomically take the one-writer lock. Returns (True, record) or (False, holder).

    O_CREAT|O_EXCL is the whole mechanism: the kernel serializes the create, so exactly
    one of any number of simultaneous contenders gets the file."""
    path = lock_path(repo)
    record = {"owner": owner, "scope": sorted(scope), "pid": os.getpid(),
              "created": time.time()}
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    try:
        fd = os.open(path, flags, 0o644)
    except FileExistsError:
        holder = held_by(repo) or {}
        holder.setdefault("owner", "<record not written yet>")
        holder["stale"] = not _alive(holder.get("pid"))
        return False, holder
    except OSError as exc:
        return False, {"owner": "<lock unusable>", "error": str(exc)}
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(record, fh)
    return True, record


def release(repo, owner):
    """Release a lock THIS owner holds. Returns (True, "") or (False, reason)."""
    holder = held_by(repo)
    if holder is None:
        return False, "no lock is held; nothing to release"
    if holder.get("owner") != owner:
        return False, ("the lock is held by %r, not %r — releasing another owner's lock is "
                       "how a one-writer rule becomes a no-writer rule"
                       % (holder.get("owner"), owner))
    try:
        os.unlink(lock_path(repo))
    except OSError as exc:
        return False, "could not remove the lock: %s" % exc
    return True, ""


def break_stale(repo):
    """Clear a lock whose holder is gone. Refuses while the holder is alive."""
    holder = held_by(repo)
    if holder is None:
        return False, "no lock is held"
    if _alive(holder.get("pid")):
        return False, ("the holder (pid %s, owner %r) is still running — this is a live "
                       "lock, not a stale one" % (holder.get("pid"), holder.get("owner")))
    try:
        os.unlink(lock_path(repo))
    except OSError as exc:
        return False, "could not remove the lock: %s" % exc
    return True, ""


def open_transaction(repo, owner, claimed):
    """The shipped proceed path: take the lock, THEN check the tree under it.

    Returns (rc, lines, acquired). A refusal releases anything it took."""
    if repo_root(repo) is None:
        return 2, ["CANNOT CHECK — %s is not a version-controlled tree; the one-writer "
                   "rule cannot be established here" % repo], False
    ok, holder = acquire(repo, owner, normalize_claims(repo, claimed))
    if not ok:
        line = ("REFUSED — the one-writer lock is held by %r (pid %s)"
                % (holder.get("owner"), holder.get("pid")))
        if holder.get("stale"):
            line += "; that process is gone — inspect it and use --break-stale"
        return 1, [line], False
    rc, foreign, dirty = check(repo, claimed)
    if rc != 0:
        release(repo, owner)
        lines = ["REFUSED — the tree carries %d dirty file(s) outside this job's claim; "
                 "commit or stash between passes:" % len(foreign)]
        lines += ["  " + d for d in describe(repo, foreign)]
        return 1, lines, False
    return 0, ["proceed — lock held by %r; %d dirty file(s), all claimed"
               % (owner, len(dirty))], True


# ── teeth ───────────────────────────────────────────────────────────────────────────────
def _race(repo, n=8):
    """n threads contend for the lock at a barrier. Returns the number that acquired."""
    import threading
    barrier = threading.Barrier(n)
    won = []
    lock = threading.Lock()

    def contend(i):
        barrier.wait()
        ok, _ = acquire(repo, "job-%d" % i, ["f%d.txt" % i])
        if ok:
            with lock:
                won.append("job-%d" % i)

    threads = [threading.Thread(target=contend, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return won


def selftest():
    """Teeth in a throwaway repo: exactly one of several contenders acquires; a second
    job is refused while the first holds; release lets the next in; a clean tree proceeds;
    a foreign dirty file refuses and is named; a claim given as an absolute path is this
    job's own; a refusal never names a path git did not report; a non-repo is CANNOT
    CHECK."""
    failures = []
    with tempfile.TemporaryDirectory() as d:
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")

        def git(*a):
            return subprocess.run(["git", "-C", d] + list(a), capture_output=True,
                                  text=True, env=env)
        git("init", "-q")
        with open(os.path.join(d, "a.txt"), "w", encoding="utf-8") as fh:
            fh.write("committed\n")
        git("add", "a.txt")
        git("commit", "-qm", "base")

        won = _race(d)
        if len(won) != 1:
            failures.append("exactly one contender may acquire the lock; %d did (%r)"
                            % (len(won), won))
        rc, _, acquired = open_transaction(d, "second-job", ["a.txt"])
        if rc != 1 or acquired:
            failures.append("a second job must be REFUSED while the lock is held (rc=%d)" % rc)
        if won:
            ok, why = release(d, "not-the-owner")
            if ok:
                failures.append("releasing another owner's lock must be refused")
            ok, why = release(d, won[0])
            if not ok:
                failures.append("the holder must be able to release: %s" % why)
        rc, _, acquired = open_transaction(d, "third-job", ["a.txt"])
        if rc != 0 or not acquired:
            failures.append("a released lock must let the next job in (rc=%d)" % rc)
        release(d, "third-job")

        rc, foreign, _ = check(d, ["a.txt"])
        if rc != 0:
            failures.append("clean tree must proceed (rc=%d foreign=%r)" % (rc, foreign))
        with open(os.path.join(d, "other_job.txt"), "w", encoding="utf-8") as fh:
            fh.write("someone else's in-flight edit\n")
        rc, foreign, _ = check(d, ["a.txt"])
        if rc != 1 or foreign != ["other_job.txt"]:
            failures.append("foreign dirty file must refuse and be named "
                            "(rc=%d foreign=%r)" % (rc, foreign))
        rc, foreign, _ = check(d, ["a.txt", "other_job.txt"])
        if rc != 0:
            failures.append("a dirty file inside this job's claim must proceed (rc=%d)" % rc)
        rc, foreign, _ = check(d, [os.path.join(d, "a.txt"),
                                   os.path.join(d, "other_job.txt")])
        if rc != 0:
            failures.append("an ABSOLUTE claim names the same file as the ledger's "
                            "relative path (rc=%d foreign=%r)" % (rc, foreign))
        os.remove(os.path.join(d, "other_job.txt"))

        weird = "foo -> bar.txt"
        with open(os.path.join(d, weird), "w", encoding="utf-8") as fh:
            fh.write("a filename that contains the rename arrow\n")
        rc, foreign, _ = check(d, [])
        if foreign != [weird]:
            failures.append("a file literally named %r must be named verbatim, not split "
                            "into a path that does not exist (got %r)" % (weird, foreign))
        for f in foreign:
            if not os.path.exists(os.path.join(d, f)):
                failures.append("the refusal named %r, which is not on disk — a fabricated "
                                "path teaches the operator to ignore refusals" % f)
        os.remove(os.path.join(d, weird))

        git("mv", "a.txt", "renamed.txt")
        rc, foreign, _ = check(d, [])
        if "renamed.txt" not in foreign or "a.txt" in foreign:
            failures.append("a rename must be named by its NEW path only (got %r)" % foreign)
    with tempfile.TemporaryDirectory() as empty:
        rc, _, _ = check(empty, [])
        if rc != 2:
            failures.append("a non-repo must be CANNOT CHECK, never a pass (rc=%d)" % rc)
    for f in failures:
        print("  FAIL  %s" % f)
    if failures:
        print("one-writer gate selftest: %d check(s) RED" % len(failures))
        return 1
    print("one-writer gate selftest: exactly one contender acquires, a second is refused "
          "until release, the refusal names only real paths, and an absolute claim is "
          "this job's own")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", default=".")
    ap.add_argument("--claim", nargs="*", default=[],
                    help="files this job owns (dirty inside the claim does not refuse)")
    ap.add_argument("--owner", default="one-writer-%d" % os.getpid(),
                    help="who is taking the lock (recorded in it)")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--acquire", action="store_true",
                      help="take the lock and check under it; hold until --release")
    mode.add_argument("--release", action="store_true", help="release a lock this owner holds")
    mode.add_argument("--status", action="store_true", help="report who holds the lock")
    mode.add_argument("--break-stale", action="store_true",
                      help="clear a lock whose holder process is gone (refuses if alive)")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    if args.status:
        holder = held_by(args.repo)
        if holder is None:
            print("one-writer lock: free")
            return 0
        print("one-writer lock held by %r (pid %s, scope %r)%s"
              % (holder.get("owner"), holder.get("pid"), holder.get("scope"),
                 "" if _alive(holder.get("pid")) else " — holder process is GONE (stale)"))
        return 1
    if args.release:
        ok, why = release(args.repo, args.owner)
        print("released" if ok else "NOT released — %s" % why)
        return 0 if ok else 1
    if args.break_stale:
        ok, why = break_stale(args.repo)
        print("stale lock cleared" if ok else "NOT cleared — %s" % why)
        return 0 if ok else 1
    if args.acquire:
        rc, lines, _ = open_transaction(args.repo, args.owner, args.claim)
        for ln in lines:
            print(ln)
        return rc
    rc, foreign, dirty = check(args.repo, args.claim)
    if rc == 2:
        print("CANNOT CHECK — %s is not a version-controlled tree; the one-writer rule "
              "cannot be established here" % args.repo)
    elif rc == 1:
        print("WOULD REFUSE — the tree carries %d dirty file(s) outside this job's claim; "
              "commit or stash between passes:" % len(foreign))
        for line in describe(args.repo, foreign):
            print("  %s" % line)
    else:
        print("would proceed — no dirty files outside this job's claim (%d dirty, all "
              "claimed)" % len(dirty))
    print("NOTE: preflight only — this reserved NOTHING. Two jobs can both get this "
          "answer. Use --acquire to take the lock the transaction runs under.")
    return rc


if __name__ == "__main__":
    sys.exit(main())
