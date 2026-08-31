#!/usr/bin/env python3
"""org_lint.py — no stray files at the repository root.

file-organization forbids loose files at a directory root, and this repository carries
runnable scripts at its root. Both are true only because the rule has EXCEPTION CLASSES —
and an exception class without a linter widens silently until the rule is decoration.

The classes (skills/file-organization):
  1. tool-required anchors — files a tool, contract, or ecosystem addresses at a fixed
     root path (README, LICENSE, dotfiles, a contract doc other tooling reads by path);
  2. runnable entry points NAMED IN THE README — discoverability is the licence: a root
     script the README never mentions is a stray, whatever it does;
  3. directories — their internal organization is governed by the skill's other rules.

Everything else at the root is a stray and goes RED.

Exit codes: 0 clean · 1 stray root file(s), each named with the class it failed ·
2 CANNOT CHECK (no README to establish class 2 against).
Gate: guard/tests/test_org_lint.py. Red demo: mutation OL1 waives the README-mention
requirement and the gate must go red.
"""
import argparse
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Class 1 — fixed-path anchors tools/contracts address at this repo's root. Extending this
# set is an edit under review, not a runtime option: an env-var escape hatch would be the
# social off-switch.
ANCHORS = {
    "README.md", "LICENSE", "STAGING_README.md", "ACTIONABLE_ADDENDUM.md",
    ".gitignore", ".gitattributes", ".gitmodules",
}


def lint(root):
    """Returns (rc, strays, scanned). rc 0 clean · 1 strays · 2 cannot check."""
    readme = os.path.join(root, "README.md")
    try:
        with open(readme, encoding="utf-8") as fh:
            readme_text = fh.read()
    except OSError:
        return 2, [], 0
    strays, scanned = [], 0
    for name in sorted(os.listdir(root)):
        path = os.path.join(root, name)
        if os.path.isdir(path) or name == ".git":
            continue  # class 3
        scanned += 1
        if name in ANCHORS:
            continue  # class 1
        named = name in readme_text
        if named:
            continue  # class 2 — the README names it
        strays.append("%s — not a tool-required anchor and not named in the README; "
                      "a root file nothing points at is a stray" % name)
    return (1 if strays else 0), strays, scanned


def selftest():
    """Teeth in a throwaway tree: a stray goes red; an anchor and a README-named entry
    point stay green; a tree with no README is CANNOT CHECK."""
    import tempfile
    failures = []
    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, "README.md"), "w", encoding="utf-8") as fh:
            fh.write("Run `entry.py` to start.\n")
        with open(os.path.join(d, "entry.py"), "w", encoding="utf-8") as fh:
            fh.write("print('hi')\n")
        with open(os.path.join(d, "LICENSE"), "w", encoding="utf-8") as fh:
            fh.write("MIT\n")
        os.mkdir(os.path.join(d, "src"))
        rc, strays, _ = lint(d)
        if rc != 0:
            failures.append("anchors + README-named entry point must be clean: %r" % strays)
        with open(os.path.join(d, "leftover_scratch.txt"), "w", encoding="utf-8") as fh:
            fh.write("temp\n")
        rc, strays, _ = lint(d)
        if rc != 1 or not any("leftover_scratch.txt" in s for s in strays):
            failures.append("a stray root file must go RED and be named (rc=%d %r)"
                            % (rc, strays))
    with tempfile.TemporaryDirectory() as d:
        rc, _, _ = lint(d)
        if rc != 2:
            failures.append("no README means class 2 cannot be established — CANNOT "
                            "CHECK, never a pass (rc=%d)" % rc)
    for f in failures:
        print("  FAIL  %s" % f)
    if failures:
        print("org lint selftest: %d check(s) RED" % len(failures))
        return 1
    print("org lint selftest: stray red and named; anchors and README-named entry "
          "points clean; README-less tree is CANNOT CHECK")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=REPO)
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    rc, strays, scanned = lint(args.root)
    if rc == 2:
        print("CANNOT CHECK — no readable README.md at %s; the entry-point exception "
              "cannot be established" % args.root)
        return 2
    for s in strays:
        print("STRAY: %s" % s)
    print("org lint: %d root file(s) checked, %d stray(s)" % (scanned, len(strays)))
    return rc


if __name__ == "__main__":
    sys.exit(main())
