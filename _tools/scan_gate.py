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
import sys, os, re, stat, errno, subprocess, tempfile, shutil

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
        raise ScanRefused("missing-identity-terms")
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

def _current_umask():
    """Read the process umask without leaving it changed.

    There is no getter, so the value must be set to read it and then restored. Between those two
    calls the mask IS 0o022 process-wide, so a concurrent thread creating a file in that window
    would get that mask instead of the real one. This tool is a single-threaded CLI and never
    opens that window in practice; the honest statement is that the window is narrow, not absent.
    Should this ever be imported into a threaded process, read the mask once at startup.
    """
    mask = os.umask(0o022)
    os.umask(mask)
    return mask

ACL_XATTR = "system.posix_acl_access"

# THE published mode of a report, and the only one. Not a cap, not a candidate, not a term in an
# intersection — the number the file lands with on every branch. See _install_posix_acl_policy for
# why the seventeenth round replaced an intersection with a constant.
_REPORT_MODE = 0o600

# The directory the scanner publishes into must be one it can write, list and traverse, and must
# not be one group or other can write. Owner rwx is a precondition of publishing at all; the
# cleared bits are the confidentiality rule.
_REPORT_DIR_MODE = 0o700

# Reaching an INODE that is already open, for the calls that take no fd. Populated once rather
# than probed per call, and None where /proc is not mounted.
_PROC_FD_DIR = next((_d for _d in ("/proc/self/fd", "/dev/fd") if os.path.isdir(_d)), None)

# POSIX-ACL extended attributes are a LINUX API. Elsewhere os.getxattr does not merely fail, it
# does not EXIST — and AttributeError is not an OSError, so it would escape a function documented
# as raising only OSError, and escape the refusal writer's "never raises" contract with it. Gate
# review found that a macOS or Windows adopter would have the refusal writer replace the refusal
# it was called to report. Probed once, here, rather than guessed per platform.
_XATTR_SUPPORTED = all(hasattr(os, _n) for _n in ("getxattr", "setxattr", "removexattr"))

# A filesystem carrying no POSIX extended attributes answers every ACL question with one of these,
# and an ordinary scan there must not become a refusal. What they establish is narrower than it
# looks, so the comment that used to say they "all mean there is no ACL here" has been corrected:
# they mean this POSIX interface is unavailable or the attribute is unset. A filesystem can carry a
# policy through a DIFFERENT interface — NFSv4 registers its own ACL handlers — and this helper
# neither reads nor preserves those. Its domain is POSIX access ACLs; outside that domain it
# preserves mode and ownership only, which is what it did before and is not a new regression.
# ENOTSUP and EOPNOTSUPP are the same value on Linux; both names are listed for platforms where
# they are not.
_ACL_ABSENT = frozenset(
    code for code in (getattr(errno, name, None)
                      for name in ("ENODATA", "ENOATTR", "EOPNOTSUPP", "ENOTSUP"))
    if code is not None)


def _strip_acl_by_fd(fd):
    """Remove the POSIX access ACL from the inode behind ``fd``.

    os.removexattr takes neither a descriptor nor a dir_fd, so the inode is reached through the
    kernel's descriptor directory — /proc/self/fd on Linux, /dev/fd on the BSDs — which is the
    same indirection _harden_report_dir uses to read the mode of an O_PATH directory handle.

    THE PATHNAME FALLBACK IS GONE. It joined a name onto the report directory, and round eighteen
    exists because a name joined onto that directory can be made to resolve somewhere else. Where
    neither descriptor directory exists this raises ENOSYS instead, which the caller turns into a
    refusal: publishing a report while an inherited ACL is still on it would satisfy the mode
    contract and break the access one, and refusing is loud where a silent widening is not. No
    platform we run on takes that branch, and an adopter who hits it should hear about it.
    """
    if _PROC_FD_DIR is None:
        raise OSError(errno.ENOSYS, "report-acl-strip-unreachable")
    os.removexattr("%s/%d" % (_PROC_FD_DIR, fd), ACL_XATTR)


def _install_posix_acl_policy(dirfd, src_name, dst_fd, dst_name):
    """Install the report's access policy on the staged file, before it is ever published.

    ONE RULE, BOTH BRANCHES, ONE NUMBER: the report is published at exactly 0600. Owner read and
    write; nothing for group, nothing for other; whatever the umask masked and whatever stood at
    the canonical name before.

    THE SEVENTEENTH ROUND REVERSED AN INTERSECTION INTO A CONSTANT, and the reversal is the part
    worth reading. Rounds ten to sixteen narrowed by INTERSECTING the staged inode's own mode, so
    that a stricter local policy would still win and nothing could loosen. A cold review leg then
    measured what actually flows in through that intersection on this platform: not operator
    intent, but the umask removing the OWNER's bits. At umask 0400 the staged mkstemp inode is
    0200 and the findings published at 0200 — the class, path and line of every secret found, in a
    file the operator who asked cannot open. Python's own tempfile documentation calls mkstemp
    "readable and writable only by the creating user ID"; under that umask the sentence is false.

    Removing an owner's own bits from a file that owner still owns buys NO confidentiality: the
    uid can restore them whenever it likes. The intersection was therefore paying an availability
    cost — an unreadable report, which reads to a human exactly like a scanner that found nothing
    — for a restriction that was never enforceable. Group and other are what the confidentiality
    argument was always about, and they are cleared unconditionally.

    Five earlier rules this replaces, each of which left one audience behind:

      round ten     capped "other" on new reports, arguing group access expressed a sharing
                    decision. It does not: an ordinary create grants the process's primary group
                    access with no setgid directory, no default ACL, and nobody deciding anything.
      round eleven  made new reports owner-only and left the REPLACEMENT path uncapped, where a
                    planted 0644 republished the findings at 0644.
      round fourteen capped group WRITE on replacement and kept group READ, reasoning that the
                    demonstrated attack was a write. Both legs refused that: a planted 0640 hands
                    the file's group the class, path and line of every secret found, and "there
                    was an existing file" is not a sharing decision by anyone who matters when the
                    existing file came out of the untrusted tree.
      round sixteen intersected BOTH branches with the staged inode — except that the existing
                    branch computed that value and then discarded it, so under umask 0277 a
                    replacement published 0600 beside a new report at 0400, under one docstring
                    claiming both branches implemented one rule.

    NO ACL IS CARRIED ONTO THE PUBLISHED REPORT, and any inherited one is removed. Copying the old
    report's ACL carried a policy the mode alone does not express: a named-user entry on a planted
    report was copied verbatim onto the findings inode, and an inherited default ACL arrived with
    its own mask. The precise claim — the gate corrected a looser one — is that effective access
    through such an entry is whatever the ACL MASK allows, and the mask tracks the group bits of
    the last chmod. So an entry is harmless at 0600 and live again at 0640, and the mode a reader
    inspects says nothing about which of those the file is one chmod away from. Removing is always narrowing,
    so it cannot introduce the failure it prevents.

    ANCHORED ON THE STAGED DESCRIPTOR. Every metadata call here operates on the descriptor
    write_report is still holding, never on the pathname the file was created under. os.chmod on
    this platform cannot decline to follow a symlink — os.chmod is not in
    os.supports_follow_symlinks, and follow_symlinks=False raises NotImplementedError, which is
    not an OSError and would escape the refusal writer's contract — so the one metadata call that
    could not refuse to follow was the one being made on a name the caller had already stopped
    holding. Review measured the consequence: a staged name swapped for a symlink took a 0644 file
    OUTSIDE the scanned tree to 0600 and only then raised, because the verifying stat read the
    symlink's own mode rather than its target's. That is a chmod gadget on anything this uid can
    chmod. fchmod on a held descriptor cannot be redirected at all.

    Ownership is still preserved: a different uid refuses, because an unprivileged process cannot
    give a file away and publishing under an identity the old report did not have is its own
    change of policy. A different gid is repaired where possible and refuses where not.
    """
    try:
        # The staged inode itself, held open. Nothing between here and the publish reads the
        # staged file by name, so nothing between here and the publish can be redirected.
        staged = os.fstat(dst_fd)

        try:
            old = os.lstat(src_name, dir_fd=dirfd)
        except FileNotFoundError:
            # A NEW report. There is nothing to preserve and nothing to probe: the mode is the
            # constant, the same one a replacement lands with.
            pass
        else:
            if not stat.S_ISREG(old.st_mode):
                raise OSError(errno.EINVAL, "report-policy-requires-regular-files")
            if old.st_uid != staged.st_uid:
                raise OSError(errno.EPERM, "report-policy-owner-differs")
            if old.st_gid != staged.st_gid:
                # An ordinary difference — a report written under newgrp, an owner's chgrp, a
                # setgid report directory — and repairable. fchown reaches the staged inode
                # directly, so there is no name here for a racing symlink to occupy.
                try:
                    os.fchown(dst_fd, -1, old.st_gid)
                except OSError as exc:
                    raise OSError(exc.errno, "report-policy-group-not-preservable") from exc
                if os.fstat(dst_fd).st_gid != old.st_gid:
                    raise OSError(errno.EPERM, "report-policy-group-not-preservable")

        # Strip any inherited ACL BEFORE the chmod, while the staged file is still private. Doing
        # it after would open exactly the window this ordering exists to close.
        if _XATTR_SUPPORTED:
            try:
                _strip_acl_by_fd(dst_fd)
            except OSError as exc:
                if exc.errno not in _ACL_ABSENT:
                    raise

        os.fchmod(dst_fd, _REPORT_MODE)
        if stat.S_IMODE(os.fstat(dst_fd).st_mode) != _REPORT_MODE:
            raise OSError(errno.EIO, "report-mode-verification-failed")
    except OSError as exc:
        raise OSError(exc.errno, "report-permission-preservation-failed") from exc


# The preservation slot, and why it is a SET of names rather than one.
#
# A single name can be OCCUPIED, and an occupant that cannot be removed — a populated directory,
# a file owned by somebody else — used to mean preservation was impossible, which in turn meant a
# report could not be safely replaced. Gate review measured the consequence: an unreadable stale
# CLEAN survived beside an rc 2 because the one slot was taken, so the scanner declined to
# invalidate a report that falsely said this tree had passed. Occupancy of one name must not be
# able to deny preservation, so a bounded set of alternates is tried in order.
# RESERVED NAMESPACE. These eight names in _reports/ belong to this scanner and are DELETED
# after any successful publication, whoever wrote them. Before this became a set, seven of them
# were ordinary filenames a user could have used; gate review measured a pre-existing file at
# scan_report.superseded.1.txt being destroyed by an ordinary clean scan, with no race and no
# permission failure involved. A filename does not establish provenance, so the reservation is
# stated here and in the adopter documentation rather than inferred. Do not keep anything you
# care about at these names.
_SUPERSEDED_SLOTS = 8


# RESERVED NAME. Where a staged FINDINGS report goes when it could not be published. It belongs
# to this scanner on the same terms as the preservation slots: it is removed at the next
# successful publication, and nothing you care about should live at this name.
_UNPUBLISHED_NAME = "scan_report.unpublished.txt"

# A SET of quarantine names, for the same reason preservation needed one. A single name can be
# OCCUPIED — by a populated directory that cannot be removed and is not ours to remove, or by an
# EARLIER run's kept findings, which must not be overwritten to make room for this run's. The gate
# measured both: an operator's directory at the name cost this run its hits, and a replacing
# rename onto the name destroyed the previous run's. A store whose purpose is not losing evidence
# was losing evidence in both directions.
_UNPUBLISHED_SLOTS = 8

# The canonical report's BASENAME. Every operation in the publication path names it relative to
# the validated directory descriptor rather than joining it onto a path that can be re-resolved.
_REPORT_NAME = "scan_report.txt"

# Staged-name generation, standing in for tempfile.mkstemp, which cannot be anchored to a
# descriptor. Same alphabet and length mkstemp uses; the attempt count is mkstemp's TMP_MAX-ish
# bound expressed plainly, and running out is an error rather than a silent reuse.
_STAGE_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789_"
_STAGE_ATTEMPTS = 64


def _unpublished_slot_names():
    """Every BASENAME a retained findings report may occupy, in the order they are tried."""
    yield _UNPUBLISHED_NAME
    for n in range(1, _UNPUBLISHED_SLOTS):
        yield "scan_report.unpublished.%d.txt" % n


def _staged_holds_evidence(fd, hits):
    """True when the staged file is a findings report with bytes actually on disk.

    The caller uses this to decide whether deleting the staged file destroys anything. A staged
    CLEAN is not evidence, and a zero-length file is a failure that preceded the write.
    """
    if not hits:
        return False
    try:
        return os.fstat(fd).st_size > 0
    except OSError:
        return False


def _quarantine_unpublished(dirfd, tmp_name, fd, hits):
    """Keep a staged findings report the publish could not complete. Answer whether it was kept.

    A False answer means the caller should remove the staged file, and that is the ordinary case.

    WHY THIS EXISTS. Every failure path out of write_report used to unlink the staged temporary,
    and `_write_refusal_report` runs next holding only the EXCEPTION — it never receives `hits`
    and cannot carry them. A cold review leg reproduced the consequence from the CLI: a FIFO at
    the canonical report name is not a symlink, so the path check admits it and the policy
    installer refuses it as a non-regular file; the secrets this scan had just found were deleted
    on the way out; and the refusal published over the top said `input-error`. The operator is
    left with a permission complaint and no sign that the tree actually contained secrets. Any
    other OSError from the installer takes the same route — a foreign uid, an unpreservable gid,
    a mode the filesystem will not verify.

    ONLY EVIDENCE IS KEPT, and the asymmetry is deliberate. A staged CLEAN carries nothing, and a
    file saying CLEAN left beside an rc 2 tells a reader this tree was scanned and passed, which
    is the false authorization every other rule here exists to prevent. Findings beside a refusal
    say "there are secrets here", which is the conservative direction.

    An EMPTY staged file is not kept either: the failure preceded the write, so there is nothing
    in it to preserve and a zero-length findings report would be its own false statement.

    The mode goes on through the held descriptor and the staged name is checked to still BE that
    descriptor's inode, for the same reason the publish path does both: this runs inside an
    untrusted directory, and it runs when something has already gone wrong.
    """
    if not _staged_holds_evidence(fd, hits):
        return False
    try:
        held = os.fstat(fd)
        named = os.lstat(tmp_name, dir_fd=dirfd)
        if (named.st_dev, named.st_ino) != (held.st_dev, held.st_ino):
            return False
        # THE SAME ACCESS POLICY AS A PUBLISHED REPORT. The failure that sends us here happens
        # BEFORE the ordinary installer's ACL strip, so retained evidence was arriving with the
        # directory's inherited ACL still on it. At 0600 the mask suppresses named entries, so
        # this was never an immediate read leak — but one chmod re-arms them, and evidence does
        # not get a weaker rule than the report it stands in for.
        if _XATTR_SUPPORTED:
            try:
                _strip_acl_by_fd(fd)
            except OSError:
                pass                      # best effort, and independent of the cap below
        os.fchmod(fd, _REPORT_MODE)
    except OSError:
        return False

    # EXCLUSIVE, never replacing. os.link refuses an occupied name, so an earlier run's kept
    # findings cannot be overwritten to make room for this run's, and a populated directory at
    # one name simply moves us to the next rather than costing anyone their evidence.
    for candidate in _unpublished_slot_names():
        try:
            os.link(tmp_name, candidate, src_dir_fd=dirfd, dst_dir_fd=dirfd)
        except OSError:
            continue                      # occupied, unusable, or unsupported — try the next
        try:
            os.unlink(tmp_name, dir_fd=dirfd)
        except OSError:
            pass                          # the evidence is linked; a leftover stage is harmless
        return True
    return False


def _superseded_slot_names(include_unpublished=False):
    """Every BASENAME the preserved copy may occupy, in the order they are tried.

    Basenames, not paths: the publication path names everything relative to the validated
    directory descriptor, so a path joined onto `reports_dir` would be exactly the re-resolution
    round eighteen exists to remove.

    include_unpublished adds the quarantine name, which is NOT a preservation slot and is never
    linked into — it is only ever swept. Preservation must not try to link findings into it,
    because a scan that could not publish already owns that name.
    """
    yield "scan_report.superseded.txt"
    for n in range(1, _SUPERSEDED_SLOTS):
        yield "scan_report.superseded.%d.txt" % n
    if include_unpublished:
        for name in _unpublished_slot_names():
            yield name


def _superseded_slots(reports_dir, include_unpublished=False):
    """The same names joined onto a directory path, for callers that hold one rather than an fd."""
    for name in _superseded_slot_names(include_unpublished):
        yield os.path.join(reports_dir, name)


def _harden_report_dir(reports_dir, restore_owner=False, parent_fd=None):
    """Remove group and other WRITE from the report directory, without following a symlink.

    Directory write permission, not file mode, is what governs unlink and create. os.makedirs
    defaults to 0o777, so at umask 0 this directory was created world-writable with an owner-only
    report inside it, and any local account could delete that report and publish its own CLEAN at
    the same path. Measured: 0777 at umask 0, 0775 at umask 0002 — ordinary where per-user groups
    are configured. Four rounds hardened the FILE; every permission arm asserted the file.

    Creation mode alone is not enough, because exist_ok=True leaves an EXISTING directory's mode
    untouched and the scanned tree can ship its own `_reports`.

    Anchored on a DESCRIPTOR opened O_NOFOLLOW rather than on the pathname. Review found the
    pathname version following a raced symlink and chmod-ing the target — mutating something
    outside the tree before refusing. os.chmod(follow_symlinks=False) is not the fix: on Linux it
    raises NotImplementedError, which is not an OSError and would escape the refusal writer's
    contract. A dirfd answers both: it cannot be swapped after it is open, and fchmod on it needs
    no follow_symlinks argument at all.

    Group and other lose WRITE and keep whatever read or traverse the operator gave them: this
    narrows who can FORGE the report, not who can find it, and it touches the scanner's own output
    directory rather than anything it was asked to scan.

    THE OWNER'S rwx IS RESTORED ONLY ON A DIRECTORY THIS TOOL JUST CREATED (restore_owner), which
    the seventeenth round added. os.mkdir is umask-masked, so mode=0o700 arrived as 0500 at umask
    0277 and as 0100 at umask 0600, and the very next mkstemp raised EACCES inside the scanner's
    OWN output directory — a tree whose owner can write it perfectly well, refused by the
    publication path. That is round fourteen's availability regression again, moved out of the
    hardening and into the creation.

    The restriction to a just-created directory is the point, and the first draft of this fix did
    not have it: applying the restoration unconditionally made the scanner chmod a PRE-EXISTING
    0500 report directory up to 0700 and publish into it. A directory the operator set that way is
    a configuration, and six existing arms were pinning exactly that — an unreadable directory
    refuses the scan. A umask the operator did not choose, applied to a directory this code made
    one line earlier, is not a configuration. Only the second one is repaired.
    """
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    opath = getattr(os, "O_PATH", 0)

    # O_RDONLY first, then O_PATH. A directory at 0300 — WRITE and TRAVERSE but not READ — lets
    # its owner create and traverse named entries perfectly well, and an O_RDONLY open of it
    # fails. Round fourteen used O_RDONLY alone and turned that into a refusal: gate review
    # measured the parent publishing normally in exactly that fixture while this code refused,
    # with a control confirming the owner could still create there. That is an availability
    # regression introduced by the hardening, not a filesystem limit.
    #
    # O_PATH opens the directory without requiring read, but an O_PATH descriptor cannot be
    # fchmod'd, so the mode is reached through its /proc/self/fd entry. Both handles are still
    # O_NOFOLLOW, so neither can be swapped onto a symlink after the check.
    # Relative to a held parent where the caller has one — that is what stops the name being
    # substituted between the create and this open. Where it does not (the refusal writer, which
    # creates nothing), the pathname form is used and the directory is still opened O_NOFOLLOW.
    _name = "_reports" if parent_fd is not None else reports_dir
    fd = None
    via_proc = False
    try:
        fd, via_proc = _open_dir_nofollow(_name, parent_fd)
    except OSError as exc:
        raise ScanRefused("report-dir-unsafe '_reports'") from exc
    try:
        target = "/proc/self/fd/%d" % fd if via_proc else fd
        mode = stat.S_IMODE(os.stat(target).st_mode if via_proc else os.fstat(fd).st_mode)
        # RESTORATION IS AUTHORIZED BY CREATION, AND BY NOTHING ELSE. Round nineteen added an
        # emptiness probe on top, reasoning that it confined widening to a directory with nothing
        # in it. The gate refuted that: the probe fell back to st_nlink when the directory could
        # not be listed, and a populated directory has the same link count as an empty one, so the
        # probe authorized exactly what it was added to prevent. Worse, removing the fallback and
        # keeping the probe broke the umask case it was sitting next to — a directory this tool
        # had just created at 0100 cannot be listed either, so restoration was withheld from the
        # very directory it exists for, and the publish then failed EACCES inside it.
        #
        # So the probe is gone. `restore_owner` means this call's own mkdir returned success, and
        # a FileExistsError never sets it. The residual the gate named stands and is not papered
        # over: a same-uid actor who substitutes the inode between that mkdir and the open of it
        # gets owner bits on a directory of their own. A creation flag cannot identify an inode,
        # and nothing short of a trusted parent closes it.
        _restore = restore_owner
        want = ((mode | _REPORT_DIR_MODE) if _restore else mode) & ~0o022
        if mode != want:
            os.chmod(target, want)
            now = stat.S_IMODE(os.stat(target).st_mode if via_proc else os.fstat(fd).st_mode)
            if now & 0o022:
                raise ScanRefused("report-dir-writable '_reports'")
            if _restore and now & _REPORT_DIR_MODE != _REPORT_DIR_MODE:
                # Only when restoration was actually REQUESTED. Round seventeen checked against a
                # fixed 0700 either way, which made this a regression rather than a guard: a
                # pre-existing 0322 directory is narrowed by this very function to 0300 and was
                # then refused for lacking owner read, while a directory already AT 0300 skipped
                # the branch and published. The same effective directory was accepted or refused
                # depending on which side of our own narrowing it started. Owner read is not
                # needed to publish — create and traverse are — so it is not required of a
                # directory the operator configured.
                raise ScanRefused("report-dir-unsafe '_reports'")
    except OSError as exc:
        _close_quietly(fd)
        raise ScanRefused("report-dir-unsafe '_reports'") from exc
    except BaseException:
        _close_quietly(fd)
        raise
    # THE DESCRIPTOR IS RETURNED OPEN, and it is the whole point of round eighteen. Closing it
    # here and then naming the directory again is what let a substitution between the hardening
    # and the publish redirect everything that followed onto an attacker's directory: the gate
    # reproduced a report published OUTSIDE the scanned tree, over a file it did not own, with a
    # preservation slot outside the tree deleted on the way. Every later operation in the
    # publication path is performed relative to THIS descriptor, so they all reach the directory
    # that was validated rather than whatever the name resolves to by then. The caller owns it and
    # must close it.
    return fd


def _open_dir_nofollow(name, parent_fd):
    """Open a directory relative to a held parent, never following a symlink at the last name.

    Returns (fd, via_proc). The O_PATH fallback exists for a directory with write and search but
    no read — round fourteen's availability case — and its mode is reached through the kernel's
    descriptor directory because an O_PATH handle cannot be fchmod'd.
    """
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    opath = getattr(os, "O_PATH", 0)
    flags = os.O_DIRECTORY | nofollow
    try:
        return os.open(name, os.O_RDONLY | flags, dir_fd=parent_fd), False
    except OSError:
        if not opath:
            raise
        return os.open(name, opath | flags, dir_fd=parent_fd), True


def _makedirs_owner_only(path):
    """Create a directory chain in which EVERY component is owner-only, resolving each step
    against a HELD DESCRIPTOR rather than a pathname.

    Two findings met here, one from each review leg, and they are the same defect seen from
    different sides. os.makedirs applies its mode argument to the last component only, so every
    ancestor it created took the default 0o777 masked by the umask — measured at umask 0 as
    world-writable ancestors wrapped around an owner-only report directory, which is an ancestor
    anyone can RENAME. And the first repair for that did pathname mkdir followed by pathname
    chmod: the gate injected a rename-and-symlink between those two calls and the chmod landed on
    a directory outside the supplied tree, with publication following it there.

    So each component is created relative to the descriptor of the one above it, opened
    O_NOFOLLOW, and its mode set through that descriptor. mkdir is umask-masked, so the mode is
    applied after creation rather than trusted to arrive. Only components this call creates are
    touched; an ancestor that already existed belongs to the operator and is left exactly as it is.

    Best effort throughout: this runs before the publication path proper, and a failure here
    surfaces as the ordinary report-write error the caller already handles.
    """
    missing = []
    cursor = os.path.abspath(path)
    while cursor and not os.path.isdir(cursor):
        missing.append(cursor)
        parent = os.path.dirname(cursor)
        if parent == cursor:
            break
        cursor = parent
    if not missing:
        return

    nofollow = getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(cursor, os.O_RDONLY | os.O_DIRECTORY | nofollow)
    except OSError:
        return
    try:
        for component in reversed(missing):
            name = os.path.basename(component)
            if not name:
                continue
            made = False
            try:
                os.mkdir(name, _REPORT_DIR_MODE, dir_fd=fd)
                made = True
            except FileExistsError:
                pass                      # somebody else got there first
            except OSError:
                return
            try:
                child, via_proc = _open_dir_nofollow(name, fd)
            except OSError:
                return
            try:
                # ONLY A COMPONENT THIS CALL ACTUALLY CREATED. The comment on the branch above
                # used to say "not ours to re-mode" and then execution fell straight through to
                # the chmod anyway — the gate injected a competing mkdir that left an operator's
                # POPULATED 0500 directory here, and the remaining code took it to 0700. A comment
                # is not a control-flow statement, which is the whole lesson of this round.
                if made:
                    try:
                        if via_proc:
                            if _PROC_FD_DIR is not None:
                                os.chmod("%s/%d" % (_PROC_FD_DIR, child), _REPORT_DIR_MODE)
                        else:
                            os.fchmod(child, _REPORT_DIR_MODE)
                    except OSError:
                        pass
            except BaseException:
                # The child is open and the loop has not taken ownership of it yet. An interrupt
                # between the open and the handover leaked it; the gate counted the descriptor.
                _close_quietly(child)
                raise
            _close_quietly(fd)
            fd = child
    finally:
        _close_quietly(fd)


def _close_quietly(fd):
    """Close a descriptor without letting the close itself become the failure being reported."""
    try:
        os.close(fd)
    except OSError:
        pass


def _stage_report(dirfd, body):
    """Create the staged report INSIDE the validated directory and write the body into it.

    This replaces tempfile.mkstemp, which resolves its directory by NAME and so cannot be anchored
    to a descriptor. The name is generated the same way mkstemp generates one and the create is
    O_EXCL, so an occupied name is retried rather than trusted; O_NOFOLLOW means a symlink planted
    at a guessed name is refused outright instead of followed.

    The mode argument is 0o600 and is umask-masked exactly as mkstemp's is, which does not matter:
    the policy installer sets the published mode through this descriptor before anything is
    published under it.
    """
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | nofollow
    last = None
    for _ in range(_STAGE_ATTEMPTS):
        name = ".scan_report_" + "".join(
            _STAGE_ALPHABET[b % len(_STAGE_ALPHABET)] for b in os.urandom(8))
        try:
            fd = os.open(name, flags, 0o600, dir_fd=dirfd)
        except FileExistsError as exc:
            last = exc
            continue
        try:
            with os.fdopen(fd, "w", closefd=False) as handle:
                handle.write(body)
        except BaseException:
            _close_quietly(fd)
            try:
                os.unlink(name, dir_fd=dirfd)
            except OSError:
                pass
            raise
        return fd, name
    raise OSError(errno.EEXIST, "report-staging-name-unavailable") from last


def write_report(staging, hits):
    reports_dir = os.path.join(staging, "_reports")

    if os.path.islink(reports_dir):
        raise ScanRefused("report-path-unsafe '_reports'")

    # THE REPORT DIRECTORY IS CREATED AND OPENED RELATIVE TO A HELD PARENT DESCRIPTOR. Round
    # eighteen anchored everything INSIDE the report directory and left its creation resolving by
    # pathname, which the gate then reproduced: a hook that renamed the just-created directory
    # aside and left a symlink at the name made the following pathname chmod land on a directory
    # outside the supplied tree, and publication followed it there. Holding the parent and
    # opening O_NOFOLLOW removes the symlink substitution; the mode repair is an fchmod on the
    # descriptor rather than a chmod on a name that can be re-resolved.
    if not os.path.isdir(staging):
        _makedirs_owner_only(staging)
    # The staging path itself is the root the caller gave us and is opened by name: it is the
    # trust boundary, not something inside it. Everything BELOW it is descriptor-relative from
    # here on. Hardening an ancestor the caller named would be a different decision and is not
    # this function's to make.
    _nofollow = getattr(os, "O_NOFOLLOW", 0)
    try:
        parent_fd = os.open(staging, os.O_RDONLY | os.O_DIRECTORY | _nofollow)
    except OSError as exc:
        raise ScanRefused("report-path-unsafe '_reports'") from exc
    try:
        created = False
        try:
            os.mkdir("_reports", _REPORT_DIR_MODE, dir_fd=parent_fd)
            created = True
        except FileExistsError:
            # Anything OTHER than a directory at this name is an ordinary report-write failure and
            # must keep classifying as one. Catching the create's own exception to learn whether
            # this call made the directory must not quietly re-badge a failure mode that predates
            # it: a regular file at _reports was reported as report-write-error before this line
            # existed, and two arms pin that name.
            if not os.path.isdir(reports_dir):
                raise
        dirfd = _harden_report_dir(reports_dir, restore_owner=created, parent_fd=parent_fd)
    finally:
        _close_quietly(parent_fd)
    try:
        # EVERY NAME FROM HERE IS RELATIVE TO dirfd, and that is the whole of round eighteen. The
        # directory this descriptor refers to is the one that was validated; the pathname
        # `reports_dir` may by now resolve somewhere else entirely. The gate reproduced exactly
        # that: a substitution immediately after the hardening published the findings OVER a file
        # outside the scanned tree and deleted an outside preservation slot on the way past, with
        # every in-function check still passing because each one re-resolved the same swapped name.
        if not hits:
            body = "scan_gate: CLEAN\n"
        else:
            body = "".join(f"{cls}\t{name}\t{surface}\t{rel}:{i}\n"
                           for rel, i, cls, name, surface in hits)

        fd, tmp_name = _stage_report(dirfd, body)
        try:
            # THE CANONICAL NAME IS JUDGED AFTER THE FINDINGS ARE ON DISK, and the order is the
            # finding. This check used to sit above _stage_report, so a symlink planted at the
            # report name raised before anything was staged: the hits existed only in the argument
            # list, the raise took them with it, and `_write_refusal_report` — which receives the
            # exception and never the hits — published a refusal over the top. The cold leg named
            # it the FIFO bug's sibling, and it is exactly that: a FIFO is admitted, staged,
            # refused by the policy installer and then QUARANTINED, while a symlink was refused
            # one step earlier where the quarantine could not see it. Staged first, every refusal
            # from here on reaches the handler with the evidence already written.
            try:
                _existing = os.lstat(_REPORT_NAME, dir_fd=dirfd)
            except FileNotFoundError:
                pass
            except OSError as exc:
                raise ScanRefused("report-path-unsafe '_reports/scan_report.txt'") from exc
            else:
                if stat.S_ISLNK(_existing.st_mode):
                    raise ScanRefused("report-path-unsafe '_reports/scan_report.txt'")
            # THE STAGED DESCRIPTOR STAYS OPEN ACROSS THE POLICY INSTALL. Writing the body and
            # then naming the file again to set its metadata is what let a swapped name receive
            # the mode call; the policy installer carries that measurement.
            _install_posix_acl_policy(dirfd, _REPORT_NAME, fd, tmp_name)
            # The staged NAME must still be the inode the policy was installed on. The gate was
            # right that a pathname recheck ALONE introduces another race window — it reproduced
            # a forged publish through one. What changed is the word alone: the directory is now
            # held, so the only writer who can still win this race is one who can already create
            # inside the scanner's own report directory, which the hardening removes from group
            # and other. It narrows the window; it does not close it, and renameat would
            # otherwise publish a planted symlink under the canonical name.
            _named = os.lstat(tmp_name, dir_fd=dirfd)
            _held = os.fstat(fd)
            if (_named.st_dev, _named.st_ino) != (_held.st_dev, _held.st_ino):
                raise ScanRefused("report-path-unsafe '_reports/scan_report.txt'")
            os.replace(tmp_name, _REPORT_NAME, src_dir_fd=dirfd, dst_dir_fd=dirfd)
            # A fresh scan has just published a report, so anything preserved from an EARLIER
            # generation is stale. Leaving it meant the sibling slot stayed occupied and the next
            # refusal could not keep the findings this run produced — measured: an old report's
            # hits held the slot while the current ones were destroyed. A planted name had the
            # same effect permanently, which made refusing to overwrite into a
            # denial-of-preservation. The slot belongs to one report generation, and this is
            # where that generation ends. The unpublished name belongs to the generation that
            # could not publish, and a run that HAS published ends that one too.
            for _name in _superseded_slot_names(include_unpublished=True):
                try:
                    os.unlink(_name, dir_fd=dirfd)
                except IsADirectoryError:
                    # A directory at that name cannot be unlinked, and gate review reproduced one
                    # blocking every later preservation permanently. An EMPTY one is removable; a
                    # populated one is somebody else's data and is left alone.
                    try:
                        os.rmdir(_name, dir_fd=dirfd)
                    except OSError:
                        pass
                except OSError:
                    pass
        except BaseException:
            # KEEP THE FINDINGS if there are any and they made it to disk. Unlinking here
            # destroyed the hits this scan had just written, and the refusal that follows cannot
            # carry them.
            if not _quarantine_unpublished(dirfd, tmp_name, fd, hits):
                # NOTHING TO KEEP, OR NOWHERE TO KEEP IT. A staged CLEAN carries no evidence and
                # is removed. Staged FINDINGS that no quarantine name would take are LEFT WHERE
                # THEY ARE: the staged name is inside the scanner's own reserved prefix, and a
                # retained temporary holding real hits is strictly better than deleting them
                # because every destination was occupied. The gate measured the alternative — a
                # populated directory at the quarantine name cost a run its findings.
                if not _staged_holds_evidence(fd, hits):
                    try:
                        os.unlink(tmp_name, dir_fd=dirfd)
                    except OSError:
                        pass
            raise
        finally:
            _close_quietly(fd)
    finally:
        _close_quietly(dirfd)


# Every status report this tool writes begins with these bytes; a findings report never does.
_STATUS_LINE_PREFIX = b"scan_gate: "


def _narrow_kept_copy(dirfd, name):
    """Narrow a preserved findings report to owner-only, and strip any ACL it carries.

    A hard link keeps the old inode's mode and ACL by definition — which is the point when
    preserving evidence and the problem when that evidence was published wide. Review put it
    exactly: preservation keeps the leak the owner-only publish was about to close. A findings
    report sitting at a planted 0644 is replaced owner-only at the canonical name while the
    preserved copy stays group- and other-readable beside it.

    THE STRIP AND THE CAP ARE INDEPENDENT, which they were not until round seventeen. They shared
    one try block, so an ACL removal that failed for any reason other than "there is no ACL here"
    skipped the chmod entirely and left the preserved copy at the mode it was planted with. Both
    review legs reached that independently — one ruled it blocking, the other ranked it MED — and
    an independent convergence is the strongest signal a paired review produces. The two are not
    alternatives and neither substitutes for the other: the strip closes a channel the mode cannot
    express, and the cap closes the one it can.

    THIS NARROWS EVERY NAME FOR THE INODE, not just this one. Two of them are ours and deliberate
    — the canonical report is about to be replaced anyway, and a preserved copy nobody should have
    been able to read is safe to narrow in the interim. But an inode can also carry a hard link
    the scanner never made, OUTSIDE the report directory, and that alias is narrowed too. The gate
    asked for the tradeoff to be stated rather than discovered: this tool will restrict a file it
    did not create if that file shares an inode with a findings report inside the tree it was
    asked to scan. Narrowing is the only direction it moves, an owner can undo it with one chmod,
    and the alternative is publishing the findings to whoever holds the other name.

    Every step is best effort. Failing to narrow the kept copy must not destroy it: the canonical
    publish still lands owner-only, and a preserved copy that could not be narrowed is strictly
    better than no preserved copy at all.
    """
    # AN O_PATH HANDLE, WHICH ANSWERS THREE FINDINGS AT ONCE.
    #
    # It needs no read permission, so a copy planted at 0044 — other-readable with the owner's own
    # bits clear, and the owner is not "other" — can still be reached. An O_RDONLY open of that
    # file fails EACCES for its own owner.
    #
    # It never blocks. open(2): opening the read end of a FIFO waits for a writer, so a name that
    # has become a FIFO stopped the one function that must always return. O_PATH does not open the
    # file description at all, so there is nothing to wait for.
    #
    # And it is O_NOFOLLOW and descriptor-relative, so the mode lands on the inode that was
    # preserved rather than on whatever a symlink at the name points at — the previous form did
    # os.chmod(name, ..., dir_fd=dirfd), which follows a symlink at the final component.
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    nonblock = getattr(os, "O_NONBLOCK", 0)
    opath = getattr(os, "O_PATH", 0)
    via_proc = bool(opath) and _PROC_FD_DIR is not None
    flags = (opath if via_proc else (os.O_RDONLY | nonblock)) | nofollow
    try:
        fd = os.open(name, flags, dir_fd=dirfd)
    except OSError:
        return
    try:
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                return                    # not a preserved report; not ours to re-mode
        except OSError:
            return
        target = "%s/%d" % (_PROC_FD_DIR, fd) if via_proc else fd
        if _XATTR_SUPPORTED:
            try:
                _strip_acl_by_fd(fd)
            except OSError:
                pass                      # best effort, and INDEPENDENT of the cap below
        try:
            # THE REPORT MODE, not the planted mode narrowed. `_kept & 0o600` mapped 0044 to 0000
            # and left the only surviving copy of the findings unreadable by its owner. 0600 is
            # narrower than a planted mode for group and other in every case AND readable, which
            # is what an evidence copy has to be.
            os.chmod(target, _REPORT_MODE)
        except OSError:
            pass
    finally:
        _close_quietly(fd)


def _preserve_superseded(dirfd, report_name):
    """Keep the report about to be replaced, and say whether replacing it is now safe.

    Returns True when the caller may replace the report, False when replacing it would destroy
    findings that could NOT be preserved.

    os.replace destroys the old bytes, so keeping the NAME is not keeping the EVIDENCE. A hard
    LINK keeps the old INODE — its content, mode, owner and ACL, not a copy that resembles them.

    Two rules decide the outcome, and they are not symmetric. A stale "CLEAN" beside an rc 2 is
    dangerous: it tells a reader this tree was scanned and passed, which is a false authorization.
    A stale FINDINGS report beside an rc 2 is not dangerous — it says there are secrets here,
    which is the conservative direction. So a status line may always be replaced, and findings may
    be replaced ONLY once they are safely kept. Gate review found the earlier version replacing
    them regardless, with every failure to preserve swallowed on the way.

    The link is attempted BEFORE the report is read, because a hard link needs no read permission
    on its source. Reading first made an unreadable findings report unpreservable and therefore
    destroyable — measured at mode 000.

    An unreadable report is treated as findings. That is the assumption in the safe direction for
    EVIDENCE, and its cost is larger than an earlier revision of this docstring claimed. That
    revision said the cost was one preserved status line. It is not: when the report cannot be
    read AND cannot be preserved under any slot, it is left standing, so an unreadable stale
    CLEAN survives beside an rc 2 and keeps telling a reader this tree passed. The slots make
    that case rarer; they do not remove it. Eight names are a finite capacity, not an
    authorization boundary.
    """
    try:
        previous = os.lstat(report_name, dir_fd=dirfd)
    except FileNotFoundError:
        return True                       # CONFIRMED absent; nothing to lose
    except OSError:
        # COULD NOT LOOK. This answered True under a comment reading "nothing there; nothing to
        # lose", and an EIO or a transient EACCES is not evidence that the file is gone — it is
        # evidence that the question was not answered. Answering True authorizes the caller to
        # replace a findings report without preserving it. The gate injected one EIO and one
        # EACCES into this single call, left every other call real, and watched the findings
        # disappear; its no-injection control kept them.
        #
        # Returning False rather than raising is deliberate. The caller treats False as "do not
        # replace" and stops; raising here would be caught by the refusal writer's own guard and
        # routed into a fallback that replaces the report anyway, which is the same destruction
        # arriving by a longer path.
        return False
    if not stat.S_ISREG(previous.st_mode):
        return True                       # not a regular file; not ours to preserve

    linked = None
    for candidate in _superseded_slot_names():
        try:
            os.link(report_name, candidate, src_dir_fd=dirfd, dst_dir_fd=dirfd,
                    follow_symlinks=False)
        except OSError:
            continue                      # occupied, unusable, or unsupported — try the next
        linked = candidate
        break

    if linked is not None:
        # NARROW THE PRESERVED COPY. The slot is a SECOND published name inside the untrusted
        # tree, and a hard link keeps the old inode's mode and ACL by definition — which is the
        # point when preserving evidence and the problem when that evidence was published wide.
        # Review put it exactly: preservation keeps the leak the owner-only publish was about to
        # close. A findings report sitting at a planted 0644 is replaced owner-only at the
        # canonical name while the preserved copy stays group- and other-readable beside it, and
        # os.replace would have dropped that inode entirely.
        #
        # This narrows BOTH names, because they are one inode — deliberately. The canonical one is
        # about to be replaced, and narrowing a report nobody should have been able to read is
        # safe in the interim. If preservation is refused and the report stays, it stays narrower
        # than it was, which is the direction that cannot hurt.
        _narrow_kept_copy(dirfd, linked)

    # Classify only AFTER the link, so a failed read cannot prevent preservation — and classify
    # THROUGH THE LINK, not through report_path. The two names described the same inode at link
    # time, but only the link is a name nobody else is replacing. An os.replace onto report_path
    # between the link and this read leaves the classification describing a DIFFERENT inode from
    # the one that was preserved, and a status line verdict then unlinks the findings just kept.
    # An independent review leg supplied that interleaving; this reads the inode we actually hold.
    classify_name = linked if linked is not None else report_name
    try:
        _nofollow = getattr(os, "O_NOFOLLOW", 0)
        # O_NONBLOCK so a name that has become a FIFO cannot stop this. A non-blocking FIFO read
        # returns nothing, the report is then classified as findings, and treating an unreadable
        # report as findings is already this function's documented safe direction.
        _nonblock = getattr(os, "O_NONBLOCK", 0)
        _cfd = os.open(classify_name, os.O_RDONLY | _nofollow | _nonblock, dir_fd=dirfd)
        try:
            is_status_line = os.read(_cfd, len(_STATUS_LINE_PREFIX)) == _STATUS_LINE_PREFIX
        finally:
            _close_quietly(_cfd)
    except OSError:
        is_status_line = False            # cannot tell: assume findings, the costly case

    if is_status_line:
        # A CLEAN or REFUSED report is not worth a slot, and parking one there was measured
        # blocking a real findings report from ever being kept. Give the slot back.
        if linked is not None:
            try:
                os.unlink(linked, dir_fd=dirfd)
            except OSError:
                pass
        return True

    if linked is not None:
        return True

    # Nothing could be linked. The findings may still be preserved already, by an earlier call
    # that linked them under one of these names — but ONLY a second directory entry for THIS
    # inode counts. The previous revision asked os.stat, which FOLLOWS symlinks, so a symlink
    # planted at the slot and pointing back at the report answered "already preserved" when
    # nothing was preserved at all, and the caller then destroyed the only copy. Both gate legs
    # reproduced that independently, from the CLI, with no race.
    #
    # lstat does not follow. st_ino is unique only within a filesystem, so st_dev travels with
    # it. A hard link is by definition a regular file, so a directory or a device at the name
    # cannot pass either.
    for candidate in _superseded_slot_names():
        try:
            kept = os.lstat(candidate, dir_fd=dirfd)
        except OSError:
            continue
        if (stat.S_ISREG(kept.st_mode)
                and (kept.st_dev, kept.st_ino) == (previous.st_dev, previous.st_ino)):
            # NARROWED HERE TOO. This branch answered "already preserved" and returned without
            # touching the mode, so a copy an earlier call left wide — or one whose narrowing
            # failed that time — stayed wide for every run afterwards. The gate ruled it blocking,
            # and it is the same defect as the one below in a place the eye skips: the publish
            # about to happen is owner-only, and the second name beside it was not.
            _narrow_kept_copy(dirfd, candidate)
            return True                   # already preserved by an earlier call; oldest wins
    return False                          # findings, and no slot would take them


def _write_refusal_report(staging, refusal):
    """Best-effort: replace an EXISTING report with a single REFUSED line, so that a stale CLEAN
    does not survive beside an rc 2 wherever this function can reach it.

    NEVER RAISES AN ERROR, and never blocks — the two halves of a contract whose point is that
    this function must not displace the failure it was called to report. The gate asked for the
    boundary to be stated: it does NOT catch KeyboardInterrupt or SystemExit. A cancellation is
    not a refusal to report, and swallowing one would be a different defect. The original refusal
    still propagates. Writes through a temporary file in the real
    <staging>/_reports directory, atomically put in place with os.replace.

    "Creates nothing when no report exists" stood here and was not quite true; the cold leg
    caught it. The existence test is os.path.lexists, which a DANGLING SYMLINK satisfies — so at
    a report name pointing nowhere this function unlinks the link and creates a regular file
    holding a REFUSED line, where write_report would have refused the same state outright. The
    two writers therefore disagree about whether a symlink is a report, and this one is the
    permissive side. That is the intended direction for a writer whose job is to leave a refusal
    record rather than to publish, but it is a creation, so the sentence now says so.

    WHERE IT CANNOT REACH, stated plainly because an earlier revision of this docstring claimed
    the guarantee unconditionally and gate review falsified it three ways:

      - An UNWRITABLE report directory. Both publish attempts fail and the old report, CLEAN or
        not, survives untouched. This function cannot promise a write on a filesystem refusing
        writes, and it is the EXIT CODE, not the report, that carries the refusal.
      - A report whose bytes cannot be READ and cannot be preserved under any slot. It is treated
        as findings and left standing, because destroying unknown evidence is the worse error.
        If it was in fact a stale CLEAN, it survives. The preservation slots exist to make this
        case rare; they do not make it impossible.
      - Anything a reader reaches by a path this function refused to follow. A symlinked
        _reports directory is LEFT EXACTLY AS IT IS and nothing is published, so a CLEAN planted
        behind it stays readable at the canonical path. Removing that link was written and then
        reverted, because a symlinked _reports can be a deliberate setup; the gate below records
        the limitation rather than claiming it closed.

    On links, the two branches differ and an earlier draft of THIS docstring wrongly described
    them as one: a symlinked _reports DIRECTORY is not unlinked and nothing is written; a
    symlinked report FILE is unlinked before the replace. So the claim that "the
    canonical name is never unlinked" — which an earlier revision made — is false on that branch,
    and there IS a window in which the report is missing: between that unlink and the replace, or
    if the publish then fails. What the function does NOT do is write through a link.

    "Never raises" is scoped to the errors this can expect — OSError and UnicodeError. An
    unexpected type still propagates: a refusal whose __str__ raises ValueError will replace the
    refusal being reported, which `except Exception` would close at the cost of swallowing the
    defects this file exists to surface. That trade has not been made, so the narrower claim is
    the true one. AttributeError from the POSIX-ACL path on a non-Linux platform used to escape
    the same way; that one is closed at its source rather than by widening this catch.

    When the replacement cannot be published under the old policy, a private 0600 refusal
    replaces it instead. 0600 is not simply "narrower": against an old 0400 or 0000 report it
    ADDS owner write, while removing group and other access. It is narrower for every reader
    other than the owner, which is the property that matters here, and the earlier blanket
    "never wider" was wrong."""
    reports_dir = os.path.join(staging, "_reports")
    if os.path.islink(reports_dir) or not os.path.isdir(reports_dir):
        # KNOWN STRUCTURAL LIMITATION, left in place deliberately and pinned by an arm.
        #
        # The scanned tree is UNTRUSTED, so it can ship `_reports -> payload/` with a prewritten
        # `scan_gate: CLEAN` behind it. This writer correctly refuses to publish through the link
        # — and a reader following the documented path is then handed that planted CLEAN beside
        # an rc 2. Gate review reproduced it from the CLI with no race.
        #
        # Removing the link was tried and REVERTED. A symlinked _reports can be a deliberate
        # setup (reports collected outside the tree), and deleting it destroys that configuration
        # to defend against a plant — while still not closing the class, because every defence
        # available here is writer-side and the exposure is reader-side.
        #
        # The actual fix is to stop authorizing from a path inside the scanned tree: keep the
        # artifact outside it, or hand the caller the O_NOFOLLOW descriptor this scanner already
        # held. That is a design change and is NOT made here. Until it is, the guarantee this
        # function offers is bounded by the exit code, which no plant can forge.
        return
    # The refusal path publishes into the same directory and needs the same container guarantee.
    # Round fourteen hardened it in write_report ONLY, which the gate caught: a refusal wrote an
    # owner-only report into a directory group or other could still rewrite, so the hardening
    # covered the path that usually succeeds and not the one that runs when something is already
    # wrong. Failure to harden is swallowed here rather than raised, because this function must not
    # displace the refusal it was called to report — but then nothing is published either, which
    # the caller already treats as the report being unavailable.
    try:
        dirfd = _harden_report_dir(reports_dir)
    except (ScanRefused, OSError):
        return
    try:
        _publish_refusal(dirfd, refusal)
    except Exception:
        # THE CONTRACT IS ABSOLUTE, and it was not. This function is called to REPORT a failure
        # and must never displace it, but its inner guard caught only (OSError, UnicodeError) —
        # and classification calls str() on the refusal object, which runs arbitrary code. A cold
        # review leg raised ValueError from an exception's __str__ and watched it escape the one
        # function in this file that is not allowed to raise, taking the original refusal with it.
        # The exit code still carries the refusal, which is the channel that actually matters;
        # what must not happen is this writer replacing it with an error of its own.
        # BaseException is deliberately NOT caught: a KeyboardInterrupt or SystemExit is not a
        # refusal to report, and swallowing those would be a different bug.
        pass
    finally:
        _close_quietly(dirfd)


def _publish_refusal(dirfd, refusal):
    """The refusal writer's body, with the validated directory descriptor already in hand.

    Split out at round eighteen so the descriptor has exactly one owner and one close, rather
    than a return path through the middle of a function that must never raise.
    """
    try:
        os.lstat(_REPORT_NAME, dir_fd=dirfd)
    except OSError:
        return
    # Bound before the guarded block so the fallback below always has a class to write, even when
    # classification itself is what failed.
    reason_class = "unclassified"
    try:
        # Classified INSIDE the guarded block. str() on a refusal is not guaranteed to succeed,
        # and out here a failure would propagate from a function whose whole job is to not let the
        # report writer displace the refusal it was called to report.
        if isinstance(refusal, (OSError, UnicodeError)):
            reason_class = "input-error"
        else:
            # str() on an exception runs whatever __str__ it has. Guarded so that an
            # unrenderable refusal still produces a REPORT with a class, rather than skipping
            # the publish entirely and leaving a stale CLEAN standing beside the failure.
            try:
                msg = str(refusal)
            except Exception:
                msg = ""
            reason_class = msg.split(" ", 1)[0].rstrip(";:,.")
            if not reason_class or not re.fullmatch(r"[a-z][a-z0-9\-]*", reason_class):
                reason_class = "unclassified"
        try:
            if stat.S_ISLNK(os.lstat(_REPORT_NAME, dir_fd=dirfd).st_mode):
                os.unlink(_REPORT_NAME, dir_fd=dirfd)
        except OSError:
            pass
        # Before EITHER publish attempt, so the ordinary path is covered and not just the
        # fallback. A False verdict means the report holds findings that could not be kept, and
        # destroying evidence is worse than leaving a report that says "there are secrets here".
        # The refusal still reaches the caller through the exit code, which is the channel that
        # actually carries it.
        if not _preserve_superseded(dirfd, _REPORT_NAME):
            return
        fd, tmp_name = _stage_report(dirfd, f"scan_gate: REFUSED {reason_class}\n")
        try:
            # Same descriptor discipline as write_report: the fd stays open across the policy
            # install so no metadata call here is made on a name that can be swapped, and every
            # name is relative to the directory that was validated.
            # The refusal report is the one a reader needs most, so it gets the same contract.
            _install_posix_acl_policy(dirfd, _REPORT_NAME, fd, tmp_name)
            _named = os.lstat(tmp_name, dir_fd=dirfd)
            _held = os.fstat(fd)
            if (_named.st_dev, _named.st_ino) != (_held.st_dev, _held.st_ino):
                raise OSError(errno.EIO, "report-staged-name-diverged")
            os.replace(tmp_name, _REPORT_NAME, src_dir_fd=dirfd, dst_dir_fd=dirfd)
        except BaseException:
            try:
                os.unlink(tmp_name, dir_fd=dirfd)
            except OSError:
                pass
            raise
        finally:
            try:
                os.close(fd)
            except OSError:
                pass
    except (OSError, UnicodeError):
        # The replacement could not be published under the old report's access policy. The previous
        # revision UNLINKED the canonical report here, reasoning that a stale "CLEAN" beside an
        # rc 2 is the worst outcome. Round-5 review found that wrong in two directions at once.
        #
        # Not every old report says CLEAN. One carrying HITS is evidence, and deleting it is a loss
        # no refusal justifies. And an attacker able to provoke both a refusal and a policy failure
        # was handed a way to erase the report through this writer's own authority — a capability
        # they did not otherwise have if they could not write this directory.
        #
        # So the name is not dropped HERE. The refusal is published at the private mode mkstemp
        # already gave the staged file, and replaces the old bytes atomically, so THIS branch
        # opens no window in which the report is missing. Two corrections gate review forced on
        # the claims this comment used to make:
        #
        #   0600 is not unconditionally narrower. Against an old 0400 or 0000 report it ADDS
        #   owner write. It is narrower for every reader other than the owner, which is the
        #   property this fallback needs, and "never wider" was simply wrong.
        #
        #   "No window exists" is true of this branch only. The symlinked-report branch above
        #   unlinks the canonical name before the replace, and a failure in between leaves it
        #   missing.
        #
        # If even this fails, the old report is left exactly as it was — this function cannot
        # promise a write on a filesystem refusing writes, and it is the EXIT CODE, not the
        # report, that says this scan refused.
        try:
            fd, tmp_name = _stage_report(dirfd, f"scan_gate: REFUSED {reason_class}\n")
            try:
                # Strip any inherited ACL here too. This fallback runs when the ordinary policy
                # install failed, and it used to ASSIGN 0600 and stop.
                #
                # THE REASON THIS COMMENT USED TO GIVE WAS WRONG, and the gate corrected it. It
                # said a report at 0600 carrying an inherited named-user entry is "the channel the
                # mode cap cannot see". On a POSIX-ACL filesystem a chmod writes the group bits
                # into the ACL MASK, so chmod 0600 sets mask::--- and every named-user and
                # named-group entry is masked to nothing. Measured here: a file at 0644 with
                # user:<name>:r-- and mask::r-- becomes mask::--- with that entry marked
                # "#effective:---" the moment it is chmod 0600. At that instant the retained entry
                # grants nothing, and claiming otherwise overstated the finding.
                #
                # The strip is still right, for the reason that survives the correction: the mask
                # is what suppresses those entries, and the mask is one chmod from coming back.
                # The same measurement, continued — chmod 0640 restored mask::r-- and the entry
                # went back to effective read. An owner widening their own report, or a backup
                # tool restoring modes, re-arms every entry the strip would have removed, and no
                # ACL is visible in anything a reader is likely to inspect. Removing the entries
                # makes that impossible rather than merely currently harmless.
                if _XATTR_SUPPORTED:
                    try:
                        _strip_acl_by_fd(fd)
                    except OSError as exc:
                        if exc.errno not in _ACL_ABSENT:
                            raise
                os.fchmod(fd, _REPORT_MODE)
                # THE SAME CHECK THE ORDINARY PATH MAKES, twelve lines above, for the reason
                # stated there: renameat would otherwise publish a planted symlink under the
                # canonical name. This is the path that runs when something has already gone
                # wrong, which is the worse place to omit it.
                _fnamed = os.lstat(tmp_name, dir_fd=dirfd)
                _fheld = os.fstat(fd)
                if (_fnamed.st_dev, _fnamed.st_ino) != (_fheld.st_dev, _fheld.st_ino):
                    raise OSError(errno.EIO, "report-staged-name-diverged")
                os.replace(tmp_name, _REPORT_NAME, src_dir_fd=dirfd, dst_dir_fd=dirfd)
            except BaseException:
                try:
                    os.unlink(tmp_name, dir_fd=dirfd)
                except OSError:
                    pass
                raise
            finally:
                _close_quietly(fd)
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
    except (ScanRefused, OSError, UnicodeError) as refusal:
        _write_refusal_report(staging, refusal)
        raise
    try:
        write_report(staging, hits)
    except (ScanRefused, OSError, UnicodeError) as exc:
        if isinstance(exc, ScanRefused):
            _write_refusal_report(staging, exc)
            raise
        _write_refusal_report(staging, ScanRefused("report-write-error"))
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
