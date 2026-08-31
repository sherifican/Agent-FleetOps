#!/usr/bin/env python3
"""org_lint.py — no stray files at the repository root.

file-organization forbids loose files at a directory root, and this repository carries
runnable scripts at its root. Both are true only because the rule has EXCEPTION CLASSES —
and an exception class without a linter widens silently until the rule is decoration.

The classes (skills/file-organization):
  1. tool-required anchors — files a tool, contract, or ecosystem addresses at a fixed
     root path (README, LICENSE, build manifests, VCS dotfiles), PLUS the repository's own
     contract filenames, DECLARED and CHECKED (see "The extension point" below);
  2. runnable entry points NAMED IN THE README — discoverability is the licence: a root
     script the README never mentions is a stray, whatever it does;
  3. directories — their internal organization is governed by the skill's other rules.

Everything else at the root is a stray and goes RED.

WHAT "NAMED IN THE README" MEANS, EXACTLY
    The naive test — `name in readme_text` — is wrong in both directions, and both were
    measured. It licenses any file whose name is a SUFFIX of a name the README does use
    (`leg_contract.py` rides on `check_leg_contract.py`; `ingest.py` rides on
    `vision_ingest.py`), so a stray can be smuggled in by choosing its name. And it reads a
    PROHIBITION as an endorsement: a README saying "never commit scratch_dump.log" licensed
    scratch_dump.log, which turns the README into an allow-list inverter.
    So a mention counts only when it is a WHOLE TOKEN (no word, dot, or dash character
    either side, which is what makes a backtick or inline-code span the cleanest form) and
    the line carrying it is not a prohibition. A name mentioned ONLY in prohibitions is not
    named. The prohibition list is a heuristic and it errs toward RED — the safe direction
    for this arm, since a false stray is argued about and a false pass is not.

THE EXTENSION POINT (class 1, for a repository's own contract files)
    The shipped anchor set is the ECOSYSTEM's, not any one repository's root listing. A
    repository that genuinely has its own fixed-path contract file declares it in
    `guard/org_anchors.txt`, one per line:

        FILENAME: why a tool or contract addresses this exact root path

    Each declaration is CHECKED, so the extension point cannot quietly become a blanket
    allow-list: a declared name that is not present at the root, that carries no reason,
    that contains a path separator, or that names a directory is itself reported. There is
    no environment-variable escape hatch — widening the set is an edit under review.

Exit codes: 0 clean · 1 stray root file(s) or a bad anchor declaration, each named ·
2 CANNOT CHECK (no README to establish class 2 against).
Gate: guard/tests/test_org_lint.py. Red demos: mutation OL1 waives the README-mention
requirement; OL2 puts the raw substring test back; OL3 stops reading a prohibition as a
prohibition. Each must take the gate red.
"""
import argparse
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Class 1 — the ECOSYSTEM's fixed-path anchors: names a tool, packaging standard, or forge
# addresses at a root path in ANY repository. This set is deliberately not this repository's
# `ls`: shipping one repo's root listing as the public exception list makes every adopter's
# build manifest a stray and every one of this repo's own filenames a free pass elsewhere.
ANCHORS = {
    "README.md", "LICENSE", "LICENSE.md", "LICENSE.txt", "NOTICE",
    "pyproject.toml", "setup.py", "setup.cfg", "package.json", "package-lock.json",
    "Cargo.toml", "go.mod", "go.sum", "Makefile",
    ".gitignore", ".gitattributes", ".gitmodules", ".mailmap",
}

# Where a repository declares its OWN contract filenames. Relative to the linted root.
ANCHOR_DECL = os.path.join("guard", "org_anchors.txt")

# A line that PROHIBITS a filename is not a licence for it. Heuristic, and it errs toward
# RED: a name mentioned only in these contexts counts as unnamed.
PROHIBITION = re.compile(
    r"\b(?:never|do not|don't|dont|must not|should not|no longer|stop|avoid|forbidden|"
    r"prohibited|delete|remove|untracked|stray|do NOT)\b", re.IGNORECASE)


def _mentions(name, readme_text):
    """True when the README NAMES `name` as a whole token on a line that is not a
    prohibition. Whole-token matching is what stops a suffix-named stray from riding on a
    longer name the README really does mention."""
    token = re.compile(r"(?<![\w.\-])%s(?![\w.\-])" % re.escape(name))
    for line in readme_text.splitlines():
        m = token.search(line)
        if not m:
            continue
        if PROHIBITION.search(line):
            continue  # a prohibition is not an endorsement
        return True
    return False


def _declared_anchors(root):
    """Read the repository's own declared contract filenames.

    Returns (names, problems). Every declaration is checked here, so the extension point
    is auditable rather than a blanket allow-list."""
    path = os.path.join(root, ANCHOR_DECL)
    try:
        with open(path, encoding="utf-8") as fh:
            raw = fh.read()
    except OSError:
        return set(), []          # no declarations is the normal case, not a problem
    names, problems = set(), []
    for lineno, line in enumerate(raw.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, sep, reason = line.partition(":")
        name, reason = name.strip(), reason.strip()
        where = "%s:%d" % (ANCHOR_DECL, lineno)
        if not sep or not reason:
            problems.append("%s — anchor declaration %r carries no reason; a declaration "
                            "without a stated contract is an allow-list entry" % (where, name))
            continue
        if not name or os.sep in name or "/" in name:
            problems.append("%s — anchor declaration %r is not a bare root filename"
                            % (where, name))
            continue
        target = os.path.join(root, name)
        if os.path.isdir(target):
            problems.append("%s — anchor declaration %r names a directory; directories are "
                            "class 3 and need no declaration" % (where, name))
            continue
        if not os.path.isfile(target):
            problems.append("%s — anchor declaration %r names a file that is not at the "
                            "root; a stale declaration silently widens the exception set"
                            % (where, name))
            continue
        names.add(name)
    return names, problems


def lint(root):
    """Returns (rc, strays, scanned). rc 0 clean · 1 strays · 2 cannot check."""
    readme = os.path.join(root, "README.md")
    try:
        with open(readme, encoding="utf-8") as fh:
            readme_text = fh.read()
    except OSError:
        return 2, [], 0
    declared, strays = _declared_anchors(root)
    scanned = 0
    for name in sorted(os.listdir(root)):
        path = os.path.join(root, name)
        if os.path.isdir(path) or name == ".git":
            continue  # class 3
        scanned += 1
        if name in ANCHORS or name in declared:
            continue  # class 1
        named = _mentions(name, readme_text)
        if named:
            continue  # class 2 — the README names it
        strays.append("%s — not a tool-required anchor and not named in the README; "
                      "a root file nothing points at is a stray" % name)
    return (1 if strays else 0), strays, scanned


def selftest():
    """Teeth in a throwaway tree: a stray goes red; an ecosystem anchor and a README-named
    entry point stay green; a suffix-named stray is NOT licensed by the longer name; a
    README prohibition is not a licence; a declared contract anchor passes but a stale
    declaration is reported; a tree with no README is CANNOT CHECK."""
    import tempfile
    failures = []
    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, "README.md"), "w", encoding="utf-8") as fh:
            fh.write("Run `entry.py` to start.\nnever commit scratch_dump.log\n")
        for name, body in (("entry.py", "print('hi')\n"), ("LICENSE", "MIT\n"),
                           ("package.json", "{}\n"), ("pyproject.toml", "[project]\n")):
            with open(os.path.join(d, name), "w", encoding="utf-8") as fh:
                fh.write(body)
        os.mkdir(os.path.join(d, "src"))
        rc, strays, _ = lint(d)
        if rc != 0:
            failures.append("ecosystem anchors + README-named entry point must be clean: %r"
                            % strays)
        with open(os.path.join(d, "scratch_dump.log"), "w", encoding="utf-8") as fh:
            fh.write("temp\n")
        rc, strays, _ = lint(d)
        if rc != 1 or not any("scratch_dump.log" in s for s in strays):
            failures.append("a file the README only PROHIBITS must go RED and be named "
                            "(rc=%d %r)" % (rc, strays))
        os.remove(os.path.join(d, "scratch_dump.log"))
        with open(os.path.join(d, "try.py"), "w", encoding="utf-8") as fh:
            fh.write("print('suffix stray')\n")   # rides on `entry.py` under a substring test
        rc, strays, _ = lint(d)
        if rc != 1 or not any(s.startswith("try.py") for s in strays):
            failures.append("a stray whose name is a SUFFIX of a README-named script must "
                            "still be a stray (rc=%d %r)" % (rc, strays))
        os.remove(os.path.join(d, "try.py"))
        with open(os.path.join(d, "CONTRACT.md"), "w", encoding="utf-8") as fh:
            fh.write("contract\n")
        os.mkdir(os.path.join(d, "guard"))
        decl = os.path.join(d, ANCHOR_DECL)
        with open(decl, "w", encoding="utf-8") as fh:
            fh.write("CONTRACT.md: read by fixed path by the adopter's tooling\n")
        rc, strays, _ = lint(d)
        if rc != 0:
            failures.append("a DECLARED contract anchor is class 1: %r" % strays)
        with open(decl, "w", encoding="utf-8") as fh:
            fh.write("CONTRACT.md\nGONE.md: names a file that is not here\n")
        rc, strays, _ = lint(d)
        if rc != 1 or not any("GONE.md" in s for s in strays) \
                or not any("no reason" in s for s in strays):
            failures.append("a reasonless or stale anchor declaration must be reported "
                            "(rc=%d %r)" % (rc, strays))
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
    print("org lint selftest: stray red and named; suffix-named stray still a stray; a "
          "README prohibition is not a licence; ecosystem anchors and README-named entry "
          "points clean; declared anchors checked; README-less tree is CANNOT CHECK")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=os.environ.get("ORG_LINT_ROOT") or REPO,
                    help="tree to lint (default: this repository, or $ORG_LINT_ROOT)")
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
