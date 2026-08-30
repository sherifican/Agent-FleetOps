#!/usr/bin/env python3
"""scrub_arm.py — no private material in public-bound bytes.

TWO PATTERN CLASSES, checked on every run:
  private-material : bytes that identify a private environment — private address ranges, home
                     paths that name a real account.
  quoted-speech    : a quoted person. A rule name can be generalized; a quoted person cannot —
                     removal is the only fix, so this arm's job is to catch it BEFORE publish.

TWO PROFILES (both documented here; pick with --profile or SCRUB_PROFILE):
  adopter    (default) — the SHIPPED generic baseline only. A fresh clone runs this with no
               extra material, and a clean result is meaningful for the baseline's classes.
  maintainer — baseline PLUS a private overlay: the phrases, names and quoted speech that must
               never appear in public bytes. The overlay lives OUTSIDE the repo (--overlay or
               SCRUB_OVERLAY). Under this profile an ABSENT overlay is CANNOT_CHECK, exit 2 —
               never a pass. If it defaulted to green, every fresh clone would read clean:
               the silent-clear problem inside its own fix.

OVERLAY FORMAT: one Python regex per line, matched case-insensitively per line of text;
  `#` starts a comment; prefix a line with `quoted-speech:` to file it under that class
  (default class is private-material).

CORPUS: tracked files under --root (`git ls-files` — public-bound bytes are what git will
  publish). Outside a git tree it walks the directory instead and says so. The committed plant
  under guard/tests/fixtures/ is EXCLUDED from the scan and exercised by --selftest.

SELF-TEST (--selftest): runs the baseline over the committed plant fixture and requires every
  baseline rule to flag at least one PLANT: line and zero CLEAN: control lines — teeth and
  breadth in one pass. A rule that misses its own plant, or flags the clean control, fails.
  The plant is a standing negative control, not a one-time demo.

Exit codes (this subsystem's convention): 0 clean · 1 flagged · 2 CANNOT_CHECK / unmeasured.
"""
import argparse
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PLANT = os.path.join(HERE, "tests", "fixtures", "scrub_plant.txt")
SKIP_DIRS = ("guard/tests/fixtures/",)  # committed plants — exercised by --selftest, never scanned

# ── the shipped generic baseline ────────────────────────────────────────────────────────────
# (class, rule, regex) — case-insensitive, applied per line. Home paths allow the neutral
# placeholder names documentation legitimately uses; anything else after /home/ (or /Users/,
# or the Windows profile dir) reads as a real account.
_NEUTRAL = r"(?!user\b|<user>|username\b|example\b)"
# The reflexive words and the opening quote are assembled from PIECES so that no source line
# here spells out the thing its own rule looks for. This is an exemption BY CONSTRUCTION, not a
# carve-out: no file and no directory is excused from the scan, and a real self-attributed quote
# written anywhere in this file — including this one — is still caught. (Unit-gated.)
_REFLEXIVE = r"\b(?:him|her)" + r"self\b"
_QUOTE_OPEN = "[" + chr(34) + chr(0x201c) + "]"
BASELINE = [
    ("private-material", "private-address-range",
     r"\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
     r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}"
     r"|192\.168\.\d{1,3}\.\d{1,3})\b"),
    ("private-material", "home-path",
     r"(?:/home/|/Users/|C:\\Users\\)" + _NEUTRAL + r"[A-Za-z0-9_.-]+"),
    ("quoted-speech", "person-attribution",
     r"\b(?:(?:the owner|he|she)\s+(?:said|says|told|asked|wrote|picked|named|put it)"
     r"|the user\s+(?:said|told|asked|wrote|picked|named))\b[^\n]{0,40}[\"\u201c]"),
    ("quoted-speech", "self-reference-attribution",
     _REFLEXIVE + r"[^\"\n]{0,30}" + _QUOTE_OPEN),
]


def _compile(rules):
    return [(cls, name, re.compile(rx, re.IGNORECASE)) for cls, name, rx in rules]


def load_overlay(path):
    """One regex per line; # comments; optional `quoted-speech:` class prefix."""
    rules = []
    with open(path, encoding="utf-8") as fh:
        for i, raw in enumerate(fh, 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            cls = "private-material"
            if line.startswith("quoted-speech:"):
                cls, line = "quoted-speech", line[len("quoted-speech:"):].strip()
            elif line.startswith("private-material:"):
                line = line[len("private-material:"):].strip()
            rules.append((cls, "overlay:%d" % i, line))
    return rules


def corpus(root):
    p = subprocess.run(["git", "-C", root, "ls-files"], capture_output=True, text=True)
    if p.returncode == 0 and p.stdout.strip():
        rels, mode = p.stdout.splitlines(), "tracked files (git ls-files)"
    else:
        rels = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d != ".git"]
            for f in filenames:
                rels.append(os.path.relpath(os.path.join(dirpath, f), root))
        mode = "directory walk (not a git tree — untracked bytes are scanned too)"
    return sorted(r.replace(os.sep, "/") for r in rels), mode


def scan(root, rules):
    rels, mode = corpus(root)
    compiled = _compile(rules)
    hits, unreadable = [], []
    scanned = skipped = 0
    for rel in rels:
        if any(rel.startswith(s) for s in SKIP_DIRS):
            skipped += 1
            continue
        path = os.path.join(root, rel)
        try:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
        except UnicodeDecodeError:
            skipped += 1          # binary — not public TEXT; images get their own gate at the port
            continue
        except OSError as e:
            unreadable.append((rel, str(e)))
            continue
        scanned += 1
        for n, line in enumerate(text.splitlines(), 1):
            for cls, name, cre in compiled:
                if cre.search(line):
                    hits.append((rel, n, cls, name, line.strip()[:120]))
    return hits, scanned, skipped, unreadable, mode


def selftest(plant_path):
    try:
        with open(plant_path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except OSError as e:
        print("SELFTEST CANNOT RUN — plant fixture unreadable: %s" % e)
        return 2
    plants = [l for l in lines if l.startswith("PLANT:")]
    cleans = [l for l in lines if l.startswith("CLEAN:")]
    if not plants or not cleans:
        print("SELFTEST RED — the plant must carry PLANT: lines and CLEAN: control lines")
        return 1
    failures = 0
    for cls, name, cre in _compile(BASELINE):
        if any(cre.search(l) for l in plants):
            print("  ok    [%s/%s] flags its plant" % (cls, name))
        else:
            print("  FAIL  [%s/%s] caught no PLANT line — this rule has no teeth" % (cls, name))
            failures += 1
    for l in cleans:
        wrong = [name for cls, name, cre in _compile(BASELINE) if cre.search(l)]
        if wrong:
            print("  FAIL  clean control flagged by %s — too wide: %s" % (wrong, l[:80]))
            failures += 1
    if failures:
        print("SELFTEST RED — %d failure(s); a scrub pass from this arm proves nothing" % failures)
        return 1
    print("scrub selftest: every baseline rule flags its plant; the clean controls stay clean")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="no private material in public-bound bytes")
    ap.add_argument("--root", default=os.path.dirname(HERE), help="tree to scan (default: repo root)")
    ap.add_argument("--profile", default=os.environ.get("SCRUB_PROFILE", "adopter"),
                    choices=("adopter", "maintainer"))
    ap.add_argument("--overlay", default=os.environ.get("SCRUB_OVERLAY"),
                    help="private pattern file OUTSIDE the repo (maintainer profile)")
    ap.add_argument("--selftest", action="store_true", help="prove the baseline rules have teeth")
    ap.add_argument("--plant", default=PLANT, help="plant fixture for --selftest")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest(args.plant)

    rules = list(BASELINE)
    if args.profile == "maintainer":
        if not args.overlay or not os.path.isfile(args.overlay):
            print("CANNOT_CHECK — the maintainer profile requires the private overlay "
                  "(--overlay or SCRUB_OVERLAY), and none is readable here.")
            print("An absent overlay is never a pass: green here would mean every fresh clone")
            print("reads clean — the silent-clear problem inside its own fix. exit 2.")
            return 2
        rules += load_overlay(args.overlay)

    hits, scanned, skipped, unreadable, mode = scan(args.root, rules)
    print("scrub arm — profile=%s · %s · %d file(s) scanned, %d skipped (fixtures/binary)"
          % (args.profile, mode, scanned, skipped))
    for rel, n, cls, name, frag in hits:
        print("  ⛔ %s:%d [%s/%s] %s" % (rel, n, cls, name, frag))
    for rel, err in unreadable:
        print("  ? %s: UNREADABLE — %s" % (rel, err))
    if unreadable:
        print("2 UNMEASURED — %d file(s) could not be read, so the scan is not a verdict"
              % len(unreadable))
        return 2
    if hits:
        print("FLAGGED — %d hit(s). Private material may not ship; generalize a rule, REMOVE a "
              "quoted person." % len(hits))
        return 1
    print("clean for the %s profile's %d rule(s) — a negative only as wide as its patterns"
          % (args.profile, len(rules)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
