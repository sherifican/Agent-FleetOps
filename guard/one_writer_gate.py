#!/usr/bin/env python3
"""one_writer_gate.py — one coder per repository, enforced at edit-transaction start.

The shipped default for concurrent editing is that there is none: ONE coder per repo.
An edit pass opening against a working tree that carries ANOTHER job's dirty files
refuses to start — commit or stash between passes. The concurrent-region alternative in
specs/driver-lock-protocol.md is a reference design, not shipped; this gate is the
supported path.

The refusal names ONLY real paths, verbatim from the version-control status — a refusal
that fabricates or garbles a path teaches its operator to ignore refusals, which is the
social off-switch failure all over again.

Usage: one_writer_gate.py [--repo DIR] [--claim FILE ...]
  --claim lists the files THIS job owns; dirty files inside the claim do not refuse
  (they are this job's own in-progress work). Any dirty file outside the claim does.

Exit codes: 0 proceed · 1 refuse (foreign dirty files, each named) · 2 CANNOT CHECK
(not a version-controlled tree — absence of the ledger is never a pass).
Gate: guard/tests/test_one_writer_gate.py. Red demo: mutation OW1 blinds the
foreign-file detection and the gate must go red.
"""
import argparse
import os
import subprocess
import sys
import tempfile


def dirty_files(repo):
    """Paths git reports as modified/untracked, or None when this is not a git tree."""
    p = subprocess.run(["git", "-C", repo, "status", "--porcelain"],
                       capture_output=True, text=True)
    if p.returncode != 0:
        return None
    paths = []
    for line in p.stdout.splitlines():
        if len(line) < 4:
            continue
        path = line[3:]
        if " -> " in path:  # rename: the new side is the live byte
            path = path.split(" -> ", 1)[1]
        paths.append(path.strip('"'))
    return paths


def check(repo, claimed):
    """Returns (rc, foreign, dirty). rc 0 proceed · 1 refuse · 2 cannot check."""
    dirty = dirty_files(repo)
    if dirty is None:
        return 2, [], []
    claimed_set = {c.replace(os.sep, "/") for c in claimed}
    foreign = [f for f in dirty if f.replace(os.sep, "/") not in claimed_set]
    return (1 if foreign else 0), foreign, dirty


def selftest():
    """Teeth in a throwaway repo: a clean tree proceeds; a foreign dirty file refuses and
    is named; a dirty file inside this job's claim proceeds; a non-repo is CANNOT CHECK."""
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
    with tempfile.TemporaryDirectory() as empty:
        rc, _, _ = check(empty, [])
        if rc != 2:
            failures.append("a non-repo must be CANNOT CHECK, never a pass (rc=%d)" % rc)
    for f in failures:
        print("  FAIL  %s" % f)
    if failures:
        print("one-writer gate selftest: %d check(s) RED" % len(failures))
        return 1
    print("one-writer gate selftest: refusal fires on a foreign dirty file, names it, "
          "and stays quiet for this job's own claim")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", default=".")
    ap.add_argument("--claim", nargs="*", default=[],
                    help="files this job owns (dirty inside the claim does not refuse)")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    rc, foreign, dirty = check(args.repo, args.claim)
    if rc == 2:
        print("CANNOT CHECK — %s is not a version-controlled tree; the one-writer rule "
              "cannot be established here" % args.repo)
    elif rc == 1:
        print("REFUSED — the tree carries %d dirty file(s) outside this job's claim; "
              "commit or stash between passes:" % len(foreign))
        for f in foreign:
            print("  %s" % f)
    else:
        print("proceed — no dirty files outside this job's claim (%d dirty, all claimed)"
              % len(dirty))
    return rc


if __name__ == "__main__":
    sys.exit(main())
