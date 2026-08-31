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
  (default class is private-material). THREE WAYS AN OVERLAY IS REFUSED (2, never a quiet pass),
  because each of them prints a green for a check that did not run:
    - supplied under a profile other than maintainer — the rules would be silently dropped while
      the caller believes their private patterns ran;
    - compiling to ZERO rules (empty, or only comments) — the file EXISTING is not the check
      RUNNING, and the banner would still say profile=maintainer;
    - resolving inside the scanned tree, or tracked by git — a denylist in the corpus publishes
      the very strings it exists to keep out. A literal one at least flags itself; one written in
      regex syntax does not match its own line, so nothing would ever say so.

CORPUS: the INDEX under --root (`git ls-files -s`) — names AND bytes both come from what git
  will publish, so a value staged for commit cannot hide behind a worktree file that was edited
  afterwards. A symlink is scanned as the path it STORES, not as the file it points at. Outside a
  git tree it walks the directory instead and says so. Bytes that fail to decode are still
  scanned (UTF-16 in both byte orders, then latin-1): a decode failure is a fact about the
  decoder, never evidence the bytes are clean. Only the committed plant is exempt, BY NAME.

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
# EXEMPTIONS ARE FILES, NAMED ONE BY ONE — never a directory prefix. A prefix is a hidey-hole:
# every future file under it inherits the exemption silently, so a private address planted in any
# other fixture would be invisible. Only the committed plant, which exists to be caught by
# --selftest, is excused, and it is excused by NAME.
SKIP_FILES = ("guard/tests/fixtures/scrub_plant.txt",)

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
    # The tilde shorthand for the same thing: a tilde immediately followed by an account name
    # names a real account, so it belongs to this class. The account-LESS home anchors (a bare
    # tilde-slash, or the home environment variable) name nobody, and a corpus-wide rule for
    # them measures 151 tracked lines in this repo — documentation legitimately writes adopter
    # config paths that way — so the baseline does not claim them. What ships those anchors as
    # somebody's actual LAYOUT is a fallback default in code, and that is gated structurally by
    # guard/tests/test_shipped_defaults.py, which can fire, rather than by a regex that cannot.
    ("private-material", "home-path-tilde",
     r"(?<![\w.~-])~" + _NEUTRAL + r"[A-Za-z][A-Za-z0-9_.-]{2,}/"),
    ("quoted-speech", "person-attribution",
     r"\b(?:(?:the owner|he|she)\s+(?:said|says|told|asked|wrote|picked|named|put it)"
     r"|the user\s+(?:said|told|asked|wrote|picked|named))\b[^\n]{0,40}[\"\u201c]"),
    ("quoted-speech", "self-reference-attribution",
     _REFLEXIVE + r"[^\"\n]{0,30}" + _QUOTE_OPEN),
]


def _compile(rules):
    return [(cls, name, re.compile(rx, re.IGNORECASE)) for cls, name, rx in rules]


def overlay_placement(overlay, root):
    """Why this overlay may not be used where it sits. Empty string = it is acceptably outside.

    The prose has said "the overlay lives OUTSIDE the repo" since the arm was written, and the
    only check was that the path existed. Prose is not a mechanism.
    """
    ov, rt = os.path.realpath(overlay), os.path.realpath(root)
    if ov == rt or ov.startswith(rt + os.sep):
        return "it resolves inside the scanned tree (--root %s)" % rt
    p = subprocess.run(["git", "-C", os.path.dirname(ov) or ".", "ls-files", "--error-unmatch",
                        "--", ov], capture_output=True, text=True)
    if p.returncode == 0:
        return "git tracks it, so publishing that repo publishes the denylist"
    return ""


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
    """The entries git will publish: (rel, git-mode, blob-id) from the INDEX.

    The names and the BYTES must come from the same place. Reading names from the index and
    bytes from the worktree lets a value that is staged for commit ship while the scan reads a
    file that was overwritten afterwards — the scan would be about a version nobody publishes.
    """
    p = subprocess.run(["git", "-C", root, "ls-files", "-s", "-z"],
                       capture_output=True, text=True)
    if p.returncode == 0 and p.stdout.strip():
        entries = []
        for rec in p.stdout.split("\0"):
            if not rec:
                continue
            meta, _, rel = rec.partition("\t")
            bits = meta.split()
            if len(bits) < 3:
                continue
            entries.append((rel.replace(os.sep, "/"), bits[0], bits[1]))
        return sorted(entries), "index entries (git ls-files -s — the bytes git will publish)"

    entries = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        for f in filenames:
            full = os.path.join(dirpath, f)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            entries.append((rel, "120000" if os.path.islink(full) else "100644", None))
    return sorted(entries), "directory walk (not a git tree — untracked bytes are scanned too)"


def _blob_bytes(root, blob_ids):
    """Read every blob in one `git cat-file --batch` pass. Missing ids simply do not come back;
    the caller reports those as unreadable rather than counting them clean."""
    ids = sorted({b for b in blob_ids if b})
    if not ids:
        return {}
    p = subprocess.run(["git", "-C", root, "cat-file", "--batch"],
                       input=("\n".join(ids) + "\n").encode(), capture_output=True)
    out, got, i = p.stdout, {}, 0
    while i < len(out):
        nl = out.find(b"\n", i)
        if nl < 0:
            break
        head = out[i:nl].decode("utf-8", "replace").split()
        if len(head) != 3 or head[1] != "blob":
            break                     # missing / not a blob: leave it absent, never assume empty
        size = int(head[2])
        got[head[0]] = out[nl + 1:nl + 1 + size]
        i = nl + 1 + size + 1
    return got


# Bytes a human would call text: printable ASCII, the usual whitespace, and the printable
# half of the 8-bit range (so latin-1 prose reads as text, not as noise).
_TEXTY = frozenset(bytes(range(0x20, 0x7F)) + b"\t\n\r\f\v" + bytes(range(0xA0, 0x100)))
_RUNS = re.compile(rb"[\x20-\x7e]{8,}")


def _is_binary(data):
    if b"\0" in data:
        return True
    head = data[:8000]
    return bool(head) and sum(1 for b in head if b not in _TEXTY) * 10 > len(head)


def _views(data):
    """Every reading of these bytes that could carry a private string.

    A file is NEVER dropped for failing to decode. A decoder saying no is a fact about the
    decoder, not evidence that the bytes are clean: latin-1 and UTF-16 files carrying ASCII-
    compatible addresses used to be skipped and the run still returned 0.

      * valid UTF-8            -> read as UTF-8
      * NULs present           -> additionally read as UTF-16, both byte orders (that is how a
                                  BOM-less wide-encoded address becomes visible)
      * 8-bit text             -> read as latin-1, which cannot fail
      * genuinely binary bytes -> the printable ASCII RUNS inside them, like `strings`. Reading
                                  compressed bytes as latin-1 instead invents matches out of
                                  noise; runs keep any embedded text visible without that.
    """
    views = []
    try:
        views.append(data.decode("utf-8"))
    except UnicodeDecodeError:
        pass
    if b"\0" in data:
        for codec in ("utf-16-le", "utf-16-be"):
            views.append(data.decode(codec, "replace"))
    if _is_binary(data):
        views.append(b"\n".join(_RUNS.findall(data)).decode("ascii", "replace"))
    else:
        fallback = data.decode("latin-1")
        if fallback not in views:
            views.append(fallback)
    return views


def scan(root, rules):
    entries, mode = corpus(root)
    compiled = _compile(rules)
    blobs = _blob_bytes(root, [b for _, _, b in entries])
    found, unreadable = {}, []
    scanned = exempt = 0
    for rel, gitmode, blob in entries:
        if rel in SKIP_FILES:
            exempt += 1
            continue
        if gitmode == "160000":       # a submodule pointer: a commit id, no bytes of ours
            continue
        if blob is not None:
            if blob not in blobs:
                unreadable.append((rel, "blob %s could not be read from the object store" % blob[:12]))
                continue
            data = blobs[blob]
        else:
            path = os.path.join(root, rel)
            try:
                # A symlink's public bytes are the PATH IT STORES — following it scans some other
                # file's content and never looks at the link text itself.
                data = (os.readlink(path).encode("utf-8", "surrogateescape")
                        if os.path.islink(path) else open(path, "rb").read())
            except OSError as e:
                unreadable.append((rel, str(e)))
                continue
        scanned += 1
        for text in _views(data):
            for n, line in enumerate(text.splitlines(), 1):
                for cls, name, cre in compiled:
                    if cre.search(line):
                        found.setdefault((rel, n, cls, name), line.strip()[:120])
    hits = [(rel, n, cls, name, frag) for (rel, n, cls, name), frag in sorted(found.items())]
    return hits, scanned, exempt, unreadable, mode


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
    if args.overlay and args.profile != "maintainer":
        print("CANNOT_CHECK — an overlay was supplied but the profile is %r, and overlay rules "
              "load only under maintainer." % args.profile)
        print("Ignoring it would print a green for the BASELINE while the caller believes their")
        print("private patterns ran — the runner defaults to adopter, so exporting the overlay")
        print("and forgetting the profile is the easy mistake. Re-run with --profile maintainer.")
        return 2
    if args.profile == "maintainer":
        if not args.overlay or not os.path.isfile(args.overlay):
            print("CANNOT_CHECK — the maintainer profile requires the private overlay "
                  "(--overlay or SCRUB_OVERLAY), and none is readable here.")
            print("An absent overlay is never a pass: green here would mean every fresh clone")
            print("reads clean — the silent-clear problem inside its own fix. exit 2.")
            return 2
        misplaced = overlay_placement(args.overlay, args.root)
        if misplaced:
            print("CANNOT_CHECK — the overlay must live OUTSIDE the scanned tree, and %s" % misplaced)
            print("A denylist inside the corpus publishes the private strings it exists to keep")
            print("out. Move it outside the tree and re-run. exit 2.")
            return 2
        try:
            overlay_rules = load_overlay(args.overlay)
        except OSError as e:
            print("CANNOT_CHECK — the overlay could not be read: %s. exit 2." % e)
            return 2
        if not overlay_rules:
            print("CANNOT_CHECK — the overlay at %s compiles to ZERO rules (empty, or nothing but "
                  "comments)." % args.overlay)
            print("The file existing is not the check running: this would quietly degrade the")
            print("maintainer profile to the baseline while the banner still says maintainer.")
            return 2
        rules += overlay_rules

    hits, scanned, exempt, unreadable, mode = scan(args.root, rules)
    print("scrub arm — profile=%s · %s · %d file(s) scanned, %d exempt by name (committed plant)"
          % (args.profile, mode, scanned, exempt))
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
