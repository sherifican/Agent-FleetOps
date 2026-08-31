#!/usr/bin/env python3
"""Keep the published voice first-person singular.

This repository is written by one person, and the owner wants the published prose to
say so. First-person plural is the default register of technical writing, so it comes
back on its own: every new paragraph, comment and docstring is another chance for it
to reappear, and nobody re-reads the whole tree looking for a pronoun.

The hazard is that some of these words are DATA, not voice. `guard/brief_scan.py`
exists to detect leaked hypotheses and appeals to consensus in dispatch briefs, so it
carries literal patterns and quoted example sentences using exactly this vocabulary.
Rewriting those would break the scanner while leaving every test green, because the
fixtures would have been rewritten to match. Files like that are declared in
`guard/voice_allow.tsv` with a reason.

Every declaration is CHECKED. A listed file that no longer contains any of these words
is reported as a stale exemption, so the allow-list cannot quietly widen into a blanket
permission the way a hand-kept exception list usually does.

  0  the published prose is first-person singular
  1  plural voice found in prose, or a stale exemption
  2  UNMEASURED — the file list could not be read, or nothing was scanned
"""

import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALLOW = os.path.join("guard", "voice_allow.tsv")

# Word-boundary matching only: "between", "answer", "power" and "thus" are not hits.
PLURAL = re.compile(
    r"\b(?:we|us|our|ours|ourselves|we['']re|we['']ve|we['']ll|we['']d|let['']s)\b",
    re.IGNORECASE)

TEXT_EXT = {".md", ".py", ".sh", ".tsv", ".txt", ".yml", ".yaml", ".toml",
            ".json", ".cfg", ".ini", ".template", ".svg", ".html", ".css", ".js"}


def _tracked(root):
    try:
        out = subprocess.run(["git", "ls-files", "-z"], cwd=root,
                             capture_output=True, text=True, timeout=60)
        if out.returncode == 0 and out.stdout:
            return [f for f in out.stdout.split("\0") if f]
    except (OSError, subprocess.SubprocessError):
        pass
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        for fn in filenames:
            found.append(os.path.relpath(os.path.join(dirpath, fn), root))
    return sorted(found)


def _allowed(root):
    """{path: reason}. A declaration without a reason is not a declaration."""
    path = os.path.join(root, ALLOW)
    out = {}
    if not os.path.isfile(path):
        return out
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            rel, _, reason = line.partition("\t")
            if rel.strip() and reason.strip():
                out[rel.strip()] = reason.strip()
    return out


def scan(root=ROOT):
    """{path: [(lineno, text)]} for every tracked text file."""
    hits = {}
    for rel in _tracked(root):
        if os.path.splitext(rel)[1].lower() not in TEXT_EXT:
            continue
        full = os.path.join(root, rel)
        try:
            with open(full, "rb") as fh:
                raw = fh.read()
        except OSError:
            continue
        if b"\0" in raw[:8000]:
            continue
        found = [(i, ln) for i, ln in
                 enumerate(raw.decode("utf-8", "replace").splitlines(), 1)
                 if PLURAL.search(ln)]
        if found:
            hits[rel] = found
    return hits


def check(root=ROOT):
    files = [f for f in _tracked(root)
             if os.path.splitext(f)[1].lower() in TEXT_EXT]
    if not files:
        return 2, ["UNMEASURED: no text files were scanned, so a clean result would "
                   "mean nothing"]

    allow = _allowed(root)
    hits = scan(root)
    offenders = {p: v for p, v in hits.items() if p not in allow}
    stale = [p for p in allow if p not in hits]

    lines = [f"   scanned {len(files)} text file(s) · {len(allow)} declared exemption(s)"]
    total = sum(len(v) for v in offenders.values())
    for p, v in sorted(offenders.items()):
        lines.append(f"   PLURAL  {p}")
        for no, txt in v[:6]:
            lines.append(f"             {no}: {txt.strip()[:96]}")
        if len(v) > 6:
            lines.append(f"             ... and {len(v) - 6} more")
    for p in sorted(stale):
        lines.append(f"   STALE EXEMPTION  {p} no longer contains any — drop its line "
                     f"from {ALLOW}")

    if offenders or stale:
        head = []
        if offenders:
            head.append(f"{total} plural-voice line(s) in {len(offenders)} file(s)")
        if stale:
            head.append(f"{len(stale)} exemption(s) no longer needed")
        return 1, ["; ".join(head)] + lines
    return 0, ["the published prose is first-person singular"] + lines


def _selftest():
    import tempfile
    import pathlib
    failures = []

    def case(name, ok):
        print(f"   {'ok  ' if ok else 'FAIL'}  {name}")
        if not ok:
            failures.append(name)

    with tempfile.TemporaryDirectory() as td:
        os.makedirs(os.path.join(td, "guard"))
        doc = pathlib.Path(td) / "DOC.md"
        allow = pathlib.Path(td) / ALLOW

        doc.write_text("This is the record. It states what was measured.\n")
        case("singular prose passes (green)", check(td)[0] == 0)

        doc.write_text("Between the answer and the power, nothing plural is used.\n")
        case("plural INSIDE words is not a hit (between/answer/power)", check(td)[0] == 0)

        doc.write_text("We measured it, and our result stands.\n")
        case("plural prose goes red", check(td)[0] == 1)

        allow.write_text("DOC.md\tthe scanner's own fixtures live here\n")
        case("a declared exemption with a reason is honoured", check(td)[0] == 0)

        allow.write_text("DOC.md\n")          # no reason
        case("an exemption without a reason does not count", check(td)[0] == 1)

        allow.write_text("DOC.md\tstill needed\nGONE.md\tnothing here any more\n")
        case("an exemption for a file with no hits is reported stale",
             check(td)[0] == 1)

        doc.write_text("Plain singular prose again.\n")
        allow.write_text("DOC.md\tno longer contains any\n")
        case("an exemption that outlived its need goes red", check(td)[0] == 1)

        os.remove(str(allow))
        case("with no allow-list at all, plural still goes red",
             (doc.write_text("Our result.\n"), check(td)[0])[1] == 1)

    if failures:
        print(f"SELFTEST FAILED ({len(failures)}): " + ", ".join(failures))
        return 1
    print("voice_check selftest: plural prose goes red, plural inside words does not, "
          "and an exemption must carry a reason and must still be needed")
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    code, report = check()
    print("voice — " + report[0])
    for ln in report[1:]:
        print(ln)
    raise SystemExit(code)
