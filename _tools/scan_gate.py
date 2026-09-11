#!/usr/bin/env python3
"""scan_gate.py — secrets + personal-data scan over the staging tree. Zero-hit gate.

Two classes:
  SECRET — key/token-shaped strings and credential assignments
  PERSONAL — owner identity, emails, real-looking IPs (doc-range IPs are allowed)

Writes _reports/scan_report.txt. Exit 0 only on zero hits of both classes.
Values are never printed — only file, line number, class, and pattern name.

Coverage disclosure — the five surfaces:
  contents: checkout roots read staged blobs; standalone/nested exports read filesystem bytes.
  UTF-8 (errors ignored) and valid aligned UTF-16/32 LE/BE text are scanned, with or without BOM.
  Malformed BOM-declared wide text refuses; malformed BOM-less streams are not guaranteed.
  filenames and paths: covered by the name arm, as a separate case from contents.
  binaries: arbitrary extraction and embedded text at arbitrary offsets are NOT covered.
  compressed payloads: NOT covered.
  git history: NOT covered by this invocation; the hook selects and archives its commit range.
  History outside that range, recursive submodules and symlink target contents are not guaranteed.
  This list is written down because a scanner that does not say which of the five it reads gets
  read as covering all five.
  These directories are skipped outright: .git, _reports, __pycache__, .pytest_cache,
  .venv, node_modules. The scanner and other files under _tools are scanned.

Mutation proof (--self-test): a planted fake API key and a planted identity string
must each go red; a clean fixture must pass.
"""
import sys, os, re, subprocess, tempfile, shutil

SECRET_PATTERNS = [
    ("anthropic-key",      re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}")),
    ("openai-style-key",   re.compile(r"sk-[A-Za-z0-9]{20,}")),
    ("github-token",       re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}")),
    # the closing-quote option matters: JSON-style {"api_key": "..."} has a quote between the
    # name and the colon — the first version of this pattern missed exactly that, and the
    # planted-mutation self-test caught it before the gate was trusted
    ("generic-key-assign", re.compile(r"(?i)(api[_-]?key|secret|token|passw(or)?d)['\"]?\s*[:=]\s*['\"][A-Za-z0-9+/_\-]{12,}['\"]")),
    ("aws-key",            re.compile(r"AKIA[0-9A-Z]{16}")),
    ("private-key-block",  re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
]
def _load_identity_terms():
    """Identity terms live in a GITIGNORED file — the public scan tool must not itself
    reveal what it redacts. Refuses to run if the file is absent, because a personal-data scan
    with no identity list is a check that cannot fail.

    CALLED LAZILY, and that is the whole point. Building this at import time made `import
    scan_gate` exit 2 on any clone without the private file, so a public test that merely wanted
    the pattern SHAPES could not even be collected — the fixture would have depended on an owner
    prerequisite nobody can satisfy from a fresh clone. The fail-closed refusal now fires at SCAN
    time, where it always belonged, and is not weakened by an inch."""
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "identity_terms.txt")
    if not os.path.isfile(p):
        sys.stderr.write(
            "scan_gate: _tools/identity_terms.txt missing — refusing to run a toothless scan.\n"
            "   This is a MISSING INPUT, not a broken scanner. Copy _tools/identity_terms.example.txt\n"
            "   to _tools/identity_terms.txt (it is gitignored) and supply one term per line for each\n"
            "   class it names: personal and account names; machine nicknames and short hostnames,\n"
            "   including any box alias used in benchmark or log annotations; LAN domain suffixes;\n"
            "   personal email local-parts.\n")
        sys.exit(2)
    terms = [t.strip() for t in open(p, encoding="utf8") if t.strip() and not t.startswith("#")]
    return terms


def _identity_terms():
    return re.compile("(?i)" + "|".join(re.escape(t) for t in _load_identity_terms()))


# The identity-INDEPENDENT shapes. These need no private file, so a test may import them on any
# clone; the identity term is added by personal_patterns() at scan time.
PERSONAL_SHAPES = [
    ("email",              re.compile(r"[a-zA-Z0-9._%+-]+@(gmail|proton|outlook|yahoo)\.[a-z]{2,}")),
    ("rfc1918-ip",         re.compile(r"\b(192\.168|10\.\d{1,3}|172\.(1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b")),
    ("home-user-path",     re.compile(r"/home/(?!<user>|USER|\$)[a-z][a-z0-9]*")),
]


def personal_patterns():
    """The full personal set: the owner-identity term first, then the shapes. Compiled on call."""
    return [("owner-identity", _identity_terms())] + PERSONAL_SHAPES
# RFC5737 documentation ranges are the sanctioned replacements — never flagged
DOC_IP = re.compile(r"\b(192\.0\.2|198\.51\.100|203\.0\.113)\.\d{1,3}\b")

def _allowlist(staging: str):
    """Explicit, reviewable exceptions: _tools/scan_allow.tsv lines of
    'exact-relative-path<TAB>pattern-name[<TAB>surface]' — a hit matching all three is deliberate
    (e.g. the owner's public GitHub handle in the root README). Every entry is a human decision on
    record.

    THE SURFACE COLUMN IS NOT OPTIONAL IN MEANING, only in syntax. Without it a content exemption
    would silently excuse the same pattern in a FILENAME, which is a different decision nobody made.
    A two-column row therefore means "content", stated in the file's own header, and a row must say
    `name` out loud to excuse the name arm."""
    p = os.path.join(staging, "_tools", "scan_allow.tsv")
    if not os.path.isfile(p):
        return []
    out = []
    for ln in open(p, encoding="utf8"):
        ln = ln.rstrip("\n")
        if not ln or ln.startswith("#"):
            continue
        parts = ln.split("\t")
        if len(parts) == 2:
            out.append((parts[0], parts[1], "content"))
        elif len(parts) == 3 and parts[2].strip() in ("content", "name"):
            out.append((parts[0], parts[1], parts[2].strip()))
    return out

class ScanRefused(Exception):
    """Operational failure: never turn an incomplete scan into a CLEAN result."""


def _git(staging, args, reason, rel="."):
    try:
        return subprocess.run(["git", "-C", staging] + args,
                              capture_output=True, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        raise ScanRefused(f"{reason} {rel!r}") from None


def _publishable_files(staging: str, skip_dirs):
    """Return (relative path, staged blob OID), or (path, None) for an export.

    A local .git directory/file selects Git, including linked worktrees. An export nested in
    another checkout has no such marker and must not accidentally scan the ancestor's index.
    A selected Git failure never falls back to worktree bytes; an empty index stays empty.
    """
    if os.path.lexists(os.path.join(staging, ".git")):
        top = _git(staging, ["rev-parse", "--show-toplevel"], "git-root-error")
        if os.path.realpath(os.fsdecode(top.removesuffix(b"\n"))) != os.path.realpath(staging):
            raise ScanRefused("git-root-mismatch '.'")
        out = _git(staging, ["ls-files", "--stage", "-z"], "git-index-error")
        if out and not out.endswith(b"\0"):
            raise ScanRefused("malformed-index '.'")
        entries = []
        for row in out.split(b"\0")[:-1]:
            meta, sep, path = row.partition(b"\t")
            fields = meta.split()
            if (not sep or not path or len(fields) != 3
                    or fields[0] not in (b"100644", b"100755", b"120000", b"160000")
                    or not re.fullmatch(rb"(?:[0-9a-f]{40}|[0-9a-f]{64})", fields[1])
                    or fields[2] not in (b"0", b"1", b"2", b"3")):
                raise ScanRefused("malformed-index '.'")
            rel = os.fsdecode(path)  # reversible surrogateescape, not lossy replacement
            if rel.startswith("/") or any(p in ("", ".", "..") for p in rel.split("/")):
                raise ScanRefused("malformed-index-path '.'")
            if fields[2] != b"0":
                raise ScanRefused(f"unmerged-index {rel!r}")
            # Gitlinks refer to commits, not file blobs; submodule recursion is outside scope.
            if fields[0] != b"160000" and not any(p in skip_dirs for p in rel.split("/")):
                entries.append((rel, fields[1].decode("ascii")))
        return entries

    def walk_error(error):
        raise ScanRefused(f"unreadable-walk {error.filename!r}")

    walked = []
    for root, dirs, files in os.walk(staging, onerror=walk_error):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        for f in files:
            walked.append((os.path.relpath(os.path.join(root, f), staging), None))
    return walked


def _text_views(raw, rel):
    """Strict declared wide text, otherwise UTF-8 plus every valid offset-zero wide view."""
    # UTF-32 LE starts with the UTF-16 LE prefix: inspect longer BOMs first.
    for bom, codec in ((b"\xff\xfe\x00\x00", "utf-32-le"),
                       (b"\x00\x00\xfe\xff", "utf-32-be"),
                       (b"\xff\xfe", "utf-16-le"), (b"\xfe\xff", "utf-16-be")):
        if raw.startswith(bom):
            try:
                yield raw[len(bom):].decode(codec, "strict")
            except UnicodeError:
                raise ScanRefused(f"invalid-wide-encoding {rel!r}") from None
            return
    yield raw.decode("utf8", "ignore")
    for codec in ("utf-16-le", "utf-16-be", "utf-32-le", "utf-32-be"):
        try:
            view = raw.decode(codec, "strict")
        except UnicodeError:
            continue
        yield view


def _allowed(allow, rel, name, surface):
    # EXACT relative path, not substring: a substring entry "README.md" silently allowlisted EVERY
    # README in the tree — found 2026-08-23 when a planted identity term in templates/README.md
    # sailed through the teeth test. Every allow entry is one file, one pattern, one SURFACE, one
    # human decision: a content exemption must never excuse the same string in a filename.
    return any(sub == rel and pname == name and psurface == surface
               for sub, pname, psurface in allow)


def scan(staging: str):
    if not os.path.isdir(staging):
        raise ScanRefused("invalid-staging-directory")
    allow = _allowlist(staging)
    personal = personal_patterns()
    hits = []
    skip_dirs = {".git", "_reports", "__pycache__", ".pytest_cache", ".venv", "node_modules"}
    for rel, oid in _publishable_files(staging, skip_dirs):
            # THE NAME ARM. A path is published bytes too: a file called after a private host or
            # carrying a key in its name leaks whatever its contents are. Line 0 means "the path,
            # not a line in it", and the surface field says which arm fired so a report cannot be
            # read as if the two were the same finding.
            name_probe = DOC_IP.sub("", rel)
            for name, pat in SECRET_PATTERNS:
                if pat.search(name_probe):
                    hits.append((rel, 0, "SECRET", name, "name"))
            for name, pat in personal:
                if pat.search(name_probe):
                    if _allowed(allow, rel, name, "name"):
                        continue
                    hits.append((rel, 0, "PERSONAL", name, "name"))
            try:
                if oid is not None:
                    raw = _git(staging, ["cat-file", "blob", oid], "unreadable-blob", rel)
                else:
                    with open(os.path.join(staging, rel), "rb") as f:
                        raw = f.read()
            except OSError:
                raise ScanRefused(f"unreadable-input {rel!r}") from None
            lines = ((i, ln) for view in _text_views(raw, rel)
                     for i, ln in enumerate(view.split("\n"), 1))
            for i, ln in lines:
                probe = DOC_IP.sub("", ln)
                for name, pat in SECRET_PATTERNS:
                    if pat.search(probe):
                        hits.append((rel, i, "SECRET", name, "content"))
                for name, pat in personal:
                    if pat.search(probe):
                        if _allowed(allow, rel, name, "content"):
                            continue
                        hits.append((rel, i, "PERSONAL", name, "content"))
    return list(dict.fromkeys(hits))

def write_report(staging, hits):
    os.makedirs(os.path.join(staging, "_reports"), exist_ok=True)
    with open(os.path.join(staging, "_reports", "scan_report.txt"), "w") as f:
        if not hits:
            f.write("scan_gate: CLEAN\n")
        for rel, i, cls, name, surface in hits:
            f.write(f"{cls}\t{name}\t{surface}\t{rel}:{i}\n")


def _write_refusal_report(staging, refusal):
    """Best-effort: if a report already exists, overwrite it with a single REFUSED line so a
    stale CLEAN cannot survive beside an rc 2. Never raises; the original ScanRefused still
    propagates. If no report exists, there is nothing to clear."""
    report_path = os.path.join(staging, "_reports", "scan_report.txt")
    if not os.path.isfile(report_path):
        return
    msg = str(refusal)
    reason_class = msg.split(" ", 1)[0].rstrip(";:,.")
    if not reason_class or not re.fullmatch(r"[a-z][a-z0-9\-]*", reason_class):
        reason_class = "unclassified"
    try:
        with open(report_path, "w") as f:
            f.write(f"scan_gate: REFUSED {reason_class}\n")
    except (OSError, UnicodeError):
        pass

def self_test():
    tmp = tempfile.mkdtemp(prefix="scangate_selftest_")
    try:
        os.makedirs(os.path.join(tmp, "skills"))
        open(os.path.join(tmp, "skills", "clean.md"), "w").write(
            "a generic doc. doc ip 203.0.113.7 is fine. path /home/<user>/x is fine.\n")
        hits = scan(tmp)
        ok_clean = not hits
        # MUTATION 1: planted secret
        open(os.path.join(tmp, "skills", "m1.md"), "w").write(
            'cfg = {"api_' + 'key": "abcDEF123456789xyzKLMNO"}\n')
        # MUTATION 2: planted identity — drawn FROM the loaded terms file, never hardcoded,
        # so the self-test stays red-capable for any user's terms (a fresh-clone run with a
        # different terms file exposed the hardcoded version as unable to fail)
        # Plant the literal term, not its escaped regex representation.
        first_term = _load_identity_terms()[0]
        open(os.path.join(tmp, "skills", "m2.md"), "w").write(
            f"ask {first_term} about it\n")
        # MUTATION 3: the NAME arm. A clean-bodied file whose NAME carries the same identity
        # term, beside a clean-named control — and, decisively, a CONTENT allow row for that exact
        # path and pattern. A content exemption is a decision about the file's text; letting it
        # silence the filename would excuse a leak nobody reviewed.
        name_dirty = f"skills/notes-{first_term}.md"
        open(os.path.join(tmp, name_dirty), "w").write("nothing private in this body\n")
        open(os.path.join(tmp, "skills", "clean-name.md"), "w").write("also nothing private\n")
        os.makedirs(os.path.join(tmp, "_tools"), exist_ok=True)
        open(os.path.join(tmp, "_tools", "scan_allow.tsv"), "w").write(
            f"{name_dirty}\towner-identity\tcontent\n")
        hits = scan(tmp)
        classes = {(h[0], h[2]) for h in hits}
        surfaces = {(h[0], h[2], h[4]) for h in hits}
        ok_red = ("skills/m1.md", "SECRET") in classes and ("skills/m2.md", "PERSONAL") in classes \
                 and not any(h[0] == "skills/clean.md" for h in hits)
        ok_name = (name_dirty, "PERSONAL", "name") in surfaces \
                  and not any(h[0] == "skills/clean-name.md" for h in hits)
        ok = ok_clean and ok_red and ok_name
        print("scan_gate self-test:",
              "PASS (control green; content, identity and NAME-arm mutations red, the last with a "
              "content allow row that must not silence it)" if ok else "FAIL")
        if not ok:
            print(f"   control_clean={ok_clean} content_mutations={ok_red} name_arm={ok_name}")
        return 0 if ok else 1
    finally:
        shutil.rmtree(tmp)

def main():
    if sys.argv[1:] == ["--self-test"]:
        return self_test()
    if len(sys.argv) > 2 or (len(sys.argv) > 1 and sys.argv[1].startswith("-")):
        raise ScanRefused("unsupported-arguments; usage: scan_gate.py [directory | --self-test]")
    staging = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        hits = scan(staging)
    except ScanRefused as refusal:
        _write_refusal_report(staging, refusal)
        raise
    try:
        write_report(staging, hits)
    except (OSError, UnicodeError):
        raise ScanRefused("report-write-error '_reports/scan_report.txt'") from None
    if hits:
        for rel, i, cls, name, surface in hits[:40]:
            print(f"{cls}\t{name}\t{surface}\t{rel}:{i}")
        print(f"scan_gate: {len(hits)} hit(s) — see _reports/scan_report.txt — batch NOT publishable")
        return 1
    print("scan_gate: CLEAN")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ScanRefused as error:
        sys.stderr.write(f"scan_gate: REFUSED {error}\n")
        sys.exit(2)
    except (OSError, UnicodeError) as error:
        # Policy I/O failures are also non-clean; never echo input bytes from exception text.
        sys.stderr.write(f"scan_gate: REFUSED input-error {getattr(error, 'filename', None)!r}\n")
        sys.exit(2)
