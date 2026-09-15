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
    terms = _load_identity_terms()
    if not terms:
        # AN EMPTY ALTERNATION MATCHES THE EMPTY STRING AT EVERY POSITION. A terms file that
        # exists but holds only comments compiled to "(?i)" and made every line of every file a
        # PERSONAL hit (cold leg, 0c28c5e). No terms means this pattern matches nothing; the
        # missing-file refusal above is unchanged and is where "no identity list" is enforced.
        return re.compile(r"(?!)")
    return re.compile("(?i)" + "|".join(re.escape(t) for t in terms))


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
    """Operational failure: never turn an incomplete scan into a CLEAN result.

    Carries its own reason CLASS, derived here at raise time from this tool's own message. The
    refusal writer reads that attribute instead of rendering the exception, because rendering an
    arbitrary object is how a refusal path acquires unbounded behaviour it cannot guard: the gate
    supplied an exception whose __str__ waits on an event nobody sets, and `except Exception`
    cannot interrupt a callback that never raises. A validated token computed from a string this
    file wrote needs no rendering later.
    """

    def __init__(self, message=""):
        super().__init__(message)
        token = message.split(" ", 1)[0].rstrip(";:,.") if isinstance(message, str) else ""
        self.reason_class = token if re.fullmatch(r"[a-z][a-z0-9\-]*", token or "") else "unclassified"


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

# The mode a report directory is CREATED with. Publishing needs write and traverse, not owner
# read — a pre-existing directory at 0300 is accepted by _harden_report_dir through its O_PATH
# path — and the cleared group/other bits are the confidentiality rule.
_REPORT_DIR_MODE = 0o700
_NOFOLLOW_FLAG = getattr(os, "O_NOFOLLOW", 0)

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

    os.removexattr takes no dir_fd. Two review legs measured that it DOES accept an ordinary
    integer descriptor on this runtime, so the sentence this replaced — "neither a descriptor nor
    a dir_fd" — was false here. The descriptor-directory route is kept anyway, because the
    descriptors this module holds are often O_PATH, and nobody has established that removexattr
    accepts one of those; the proc path works for both. The inode is reached through the
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

    ANCHORED ON THE STAGED DESCRIPTOR. Every metadata call on the STAGED file operates on the descriptor
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
        # The staged inode itself, held open. The only thing between here and the publish that
        # touches the staged NAME is the identity check immediately before the rename, so a
        # redirection in between is detected rather than published.
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
# RESERVED NAMESPACE. These eight names in _reports/ belong to this scanner and are SWEPT after
# a successful publication when they are older than the report that publication staged — an
# entry newer than it, or whose age cannot be read, is left — whoever wrote them. Before this became a set, seven of them
# were ordinary filenames a user could have used; gate review measured a pre-existing file at
# scan_report.superseded.1.txt being destroyed by an ordinary clean scan, with no race and no
# permission failure involved. A filename does not establish provenance, so the reservation is
# stated here and in the adopter documentation rather than inferred. Do not keep anything you
# care about at these names.
_SUPERSEDED_SLOTS = 8


# RESERVED NAME. Where a staged FINDINGS report goes when it could not be published. It belongs
# to this scanner on the same terms as the preservation slots: a later successful publication
# attempts to remove it when it is older than that publication's own stage (newer or
# unreadable-age entries are left, and the attempt may fail), and nothing you care about
# should live at this name.
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
    """Whether deleting the staged file would destroy anything. Answers conservatively.

    A staged CLEAN is not evidence and answers False. A zero-length file is a failure that
    preceded the write and answers False. Everything else answers True — including the case where
    the size cannot be read at all.

    THAT LAST CLAUSE IS THE WHOLE POINT, and it is the second time this file has needed it. Round
    nineteen fixed `_preserve_superseded` treating an lstat error as "the file is absent"; this
    helper then converted an fstat error into False, and its caller reads False as "nothing worth
    keeping" and unlinks. The gate named it exactly — unknown metadata authorizes deletion — and
    measured one injected EIO destroying a staged findings report that a no-injection control
    retained.

    A question this code cannot answer never authorizes destruction. `hits` being non-empty and
    the body having been written are facts already in hand; an fstat that fails changes neither.
    The cost of being wrong in this direction is a leftover temporary file. The cost of being
    wrong in the other direction is the evidence.
    """
    if not hits:
        return False
    try:
        return os.fstat(fd).st_size > 0
    except OSError:
        return True


class _CustodyUnconfirmed(OSError):
    """The link succeeded and the confirmation after it did not: custody MAY have been taken.

    Distinct from FileNotFoundError ("no custody was taken") and from the OSError a refused link
    raises ("this name was never ours"), because callers react to those by moving to the next
    reserved name — and moving on after a successful link gives the same inode a second reserved
    name (cold leg, e1c1404). A caller that sees this stops linking and keeps its stage.
    """


def _link_held_inode(fd, candidate, dirfd):
    """Give the inode behind FD a new name — binding the link to the INODE, not to a pathname.

    Every earlier shape of this module linked by NAME (`os.link(stage_name, candidate)`) and then
    tried to confirm, afterwards, that the name had still meant our inode at the instant of the
    link. Both review legs measured what that costs: a substitution between the write and the
    link attaches a decoy to a reserved name, and the confirmation arrives one syscall too late.

    linkat(2) through the descriptor directory with AT_SYMLINK_FOLLOW is the documented,
    unprivileged way to link a held inode (the AT_EMPTY_PATH form needs CAP_DAC_READ_SEARCH).
    Measured on this box: while the inode still has at least one name the new link IS the held
    inode; once its link count is zero the call fails (ENOENT on the measured kernel) rather than
    linking anything else. So this either attaches OUR bytes to CANDIDATE, or it fails — it
    cannot attach a decoy. ENOENT out of this helper therefore means "no custody REMAINS": the
    kernel refused, or a name was added and the post-link identity check below found it already
    replaced under us (the extra name is gone; the held count is what it was). It does not
    prove the link count is zero, and no caller relies on that. Callers answer it three ways:
    the copy-out keeps its stage and lets its cleanup re-ask the stage name before the close;
    quarantine re-asks the stage name at once and copies out only if it is gone; preservation,
    whose link was a retry away, moves to the next reserved name and takes its rescue only once
    every name has been tried.

    Returns True on success. Raises FileNotFoundError when no custody was taken — the kernel refused, or the check below did (the
    caller's rescue path); `_CustodyUnconfirmed` when the link was made and the check after it
    could not run (custody MAY have been taken — callers stop linking); and other OSError for an
    occupied or unusable candidate, raised by the link itself before any custody.
    """
    if _PROC_FD_DIR is None:
        raise OSError(errno.ENOSYS, "descriptor-directory-unavailable")
    os.link("%s/%d" % (_PROC_FD_DIR, fd), candidate, dst_dir_fd=dirfd, follow_symlinks=True)
    # CONFIRMED ANYWAY. The syscall above cannot attach anything but the held inode; this
    # check covers a broken or substituted link implementation AND the interval between the
    # link and this lstat, in which the candidate name can be replaced. It is the post-link
    # match the cold review leg named as its SHIP bar. A mismatch is
    # reported as ENOENT so that no caller claims custody (each answers it as the docstring says).
    try:
        held = os.fstat(fd)               # the held side first —
        linked = os.lstat(candidate, dir_fd=dirfd)   # the name LAST: it is what custody is claimed over
    except OSError as exc:
        # THE LINK IS MADE AND THE CONFIRMATION FAILED. Neither "no custody" nor "not ours":
        # the reserved name may well hold the inode. Reported as its own class so no caller
        # reads it as a free slot and links the next one (cold leg, e1c1404).
        raise _CustodyUnconfirmed(errno.EIO, "custody-unconfirmed-after-link") from exc
    if (linked.st_dev, linked.st_ino) != (held.st_dev, held.st_ino):
        raise FileNotFoundError(errno.ENOENT, "linked-name-is-not-the-held-inode")
    return True


def _copy_out_unpublished(dirfd, fd, depth=0):
    """Last resort: write the held inode's bytes to a fresh reserved name, by READING not linking.

    THE ANSWER IS WHETHER A COMPLETE COPY IS ON DISK UNDER A NAME THIS SCANNER CONTROLS — a
    reserved name, or the kept stage under the temporary prefix. False means no complete copy
    was made (no source, no stage, a read that failed partway, nothing written): the caller may
    try again. It used to mean "no reserved name was linked", and a caller that read it as "no
    copy" — write_report, through quarantine — made a second copy of the same findings beside
    a complete kept one (invariant leg, 5b1a014).

    This runs when the staged name has stopped naming the staged inode, or its state could not be read — neither of which proves that no
    rename, link or unlink can reach those bytes by name any more. The descriptor still can. The
    copy is a different inode — it is not the file that was staged, and it carries none of that
    file's identity — but it carries the findings, and the alternative measured by review is that
    the next close frees them.

    THE SAME RULE AS EVERYWHERE ELSE, since round thirty-nine: a reserved name is refused when
    the access policy could not be installed on the file behind it. An earlier shape took an
    exception here ("it may be the only remaining copy") — written when a refused name meant a
    deleted stage. The stage is now kept by default, so refusing the name loses nothing: the
    bytes stay under the temporary prefix, narrowed as far as this code can narrow them.

    THE RETAINED STAGE EXISTS BEFORE THE FIRST BYTE IS READ, and the copy is streamed into it.
    The previous shape read the whole source into memory and only then created a stage, so a
    read that failed midway, or a stage that could not be created, returned with the bytes in
    a local variable and the caller's next close freed the last inode reference (cold leg,
    3c075f0). Now whatever was read is on disk when the read fails, and the stage is kept.
    What this cannot do is retain without one creatable temporary name and a readable source;
    when EITHER cannot be had no copy is made, the bytes stay only on the held inode, and the
    caller's next close frees them if no name still reaches it — that is the limit, stated.
    """
    if _PROC_FD_DIR is None:
        return False
    # THE SOURCE IS NARROWED, STRIP AND MODE, EACH BEST EFFORT. The mode so the reopen below can
    # read it (umask 0400 leaves it 0200); the strip because an alias of this inode may survive
    # under another name, and a mode-only narrowing leaves whatever ACL the directory gave it on
    # that alias (invariant leg, 0c28c5e).
    _narrow_leftover(fd)
    # THE SOURCE IS READ THROUGH THE DESCRIPTOR DIRECTORY, OR FROM THE HELD DESCRIPTOR ITSELF.
    # The reopen needs owner-read on the mode, which the narrowing above installs best-effort
    # and does not verify; when it is refused, a descriptor that was opened for reading — the
    # scanner's own stages are O_RDWR since round forty-six — is read with pread, which depends
    # on no mode at all (cold leg, e1c1404). What stays unreadable is a write-only or path-only
    # descriptor whose reopen is refused: the "readable source" half of the limit stated above.
    src = None
    try:
        src = os.open("%s/%d" % (_PROC_FD_DIR, fd), os.O_RDONLY)
    except OSError:
        try:
            os.pread(fd, 1, 0)            # EBADF on a descriptor not open for reading
        except OSError:
            return False
    _src_off = 0
    try:
        nofollow = getattr(os, "O_NOFOLLOW", 0)
        flags = os.O_CREAT | os.O_EXCL | os.O_RDWR | nofollow   # readable: a nested rescue can pread it
        stage_name = None
        stage_fd = None
        keep_stage = True                 # set before the open, with `written`, so the acquisition
        written = 0                       # try below hands straight to the try that owns the fd
        try:
            for _ in range(_STAGE_ATTEMPTS):
                name = ".scan_report_" + "".join(
                    _STAGE_ALPHABET[b % len(_STAGE_ALPHABET)] for b in os.urandom(8))
                try:
                    stage_fd = os.open(name, flags, _REPORT_MODE, dir_fd=dirfd)
                except FileExistsError:
                    continue
                except OSError:
                    return False          # no creatable name: the stated limit
                stage_name = name
                break
            if stage_fd is None:
                return False              # every attempt collided: the stated limit
        except BaseException:
            if stage_fd is not None:
                _close_quietly(stage_fd)  # a cancellation between the open and the owning try
            raise
        # RETENTION IS THE DEFAULT FROM THE MOMENT A STAGE EXISTS. Round thirty set keep_stage only on
        # the error paths it thought of, and a KeyboardInterrupt between two writes took none of
        # them: the finally saw False and deleted three bytes of evidence. The flag now starts True
        # and is cleared in exactly one place — after a confirmed publication — so cancellation,
        # or any exit this code did not anticipate, keeps whatever reached the stage.
        try:
            try:
                # THE STAGE'S CHMOD AND STRIP ARE BEST EFFORT BEFORE THE STREAM. A chmod that
                # RAISED here sat inside the except that returns before any byte was copied, so
                # the copy never happened and the caller's close freed the source (invariant
                # leg, 0c28c5e). Whatever can be written is written; the mode verify after the
                # stream still declines a reserved name when 0600 cannot be established.
                _narrow_leftover(stage_fd)
                _strip_denied = False
                if _XATTR_SUPPORTED:
                    try:
                        _strip_acl_by_fd(stage_fd)
                    except OSError as exc:
                        if exc.errno not in _ACL_ABSENT:
                            _strip_denied = True      # decided AFTER the bytes are on disk
                # STREAMED, SOURCE TO STAGE. A read that fails partway leaves what was read on the
                # stage, and retention is already on; the failure returns False with the bytes kept.
                # A failure BEFORE THE FIRST BYTE leaves nothing, and an empty stage is not
                # evidence: an error releases retention, and the finally removes an EMPTY stage
                # by identity whether or not retention was released — a cancellation here
                # releases nothing, and the stage is removed because it is measured empty (an
                # executed review of d7e4a3c found the first-read failure keeping one forever;
                # gate 41 found this comment claiming the release for the cancellation too).
                try:
                    while True:
                        if src is not None:
                            chunk = os.read(src, 65536)
                        else:
                            chunk = os.pread(fd, 65536, _src_off)
                            _src_off += len(chunk)
                        if not chunk:
                            break
                        off = 0
                        while off < len(chunk):
                            n = os.write(stage_fd, chunk[off:])
                            if n <= 0:
                                # A write that reports no progress would otherwise spin here
                                # forever. It is a failure that has not raised; the stage is
                                # kept when it holds anything.
                                if written == 0:
                                    keep_stage = False
                                return False
                            off += n
                            written += n
                except OSError:
                    if written == 0:
                        keep_stage = False    # nothing reached the stage: not evidence
                    return False          # otherwise the stage holds what was read; retention is on
                if written == 0:
                    keep_stage = False    # nothing readable: an empty stage is not evidence
                    return False
                # THE BYTES ARE ON DISK BEFORE ANY DECISION ABOUT A RESERVED NAME. A mode that did
                # not land, or a strip that was denied (recorded above, decided below), declines
                # the reserved name — and the stage, holding the bytes, is kept.
                try:
                    if stat.S_IMODE(os.fstat(stage_fd).st_mode) != _REPORT_MODE:
                        return True       # complete bytes, kept under the temporary prefix (see the docstring)
                except OSError:
                    return True           # cannot verify the mode: the same — kept, no reserved name
                if _strip_denied:
                    # THE SAME RULE AS QUARANTINE: a reserved name asserts the report's access
                    # policy, and a denied strip means it is not on the file. The bytes are on
                    # the stage and retention is on, so declining the name loses nothing — the
                    # "only remaining copy" exception this function used to take was written
                    # before the stage was kept by default (cold leg, 0c28c5e).
                    return True           # complete bytes, kept; no reserved name
            except OSError:
                return False
            for candidate in _unpublished_slot_names():
                try:
                    _link_held_inode(stage_fd, candidate, dirfd)
                except FileNotFoundError:
                    # NO CUSTODY WAS TAKEN — the link helper refused, or its post-link check did — and
                    # the stage keeps the bytes. Nothing more is done HERE: the finally below asks
                    # once whether the stage name still reaches the stage and copies out only if
                    # it does not. This arm used to make its own further copy first, and with the
                    # finally's re-ask added in round forty-eight a taken stage name produced two
                    # copies of the same findings (invariant leg, e71e440).
                    keep_stage = True
                    return True           # complete bytes, kept; the finally re-asks the name
                except _CustodyUnconfirmed:
                    # THE LINK MAY HAVE LANDED. The next name would be a second one for this
                    # inode; the stage keeps the bytes (retention is on) and no more is tried —
                    # after the same question the stage paths ask before a close: is the stage
                    # name still this inode? A stage whose name was taken meanwhile has this
                    # descriptor as its last reference, and is copied out once more (depth bounds
                    # it) rather than freed (inventory trace, 4e0be0a).
                    keep_stage = True
                    # The re-ask of the stage name happens in the finally below, for this exit
                    # and every other retention exit alike, one level deep. Round forty-seven
                    # asked it here through a helper that dropped the depth, so every level
                    # restarted at zero and a racer who kept taking names drove the chain until
                    # the reserved names, or the descriptors, ran out (invariant leg, cold leg
                    # and an inventory trace, all on 407a89c).
                    return True           # complete bytes, kept (and possibly linked)
                except OSError:
                    continue              # occupied or unusable — the next name
                # THE LINK IS THE HELD INODE BY CONSTRUCTION: it was made through the descriptor
                # directory, which cannot attach anything else. This is the one place retention is
                # released — the bytes now have a reserved name.
                keep_stage = False
                return True
            # EVERY RESERVED NAME WAS TAKEN — AND THE STAGE IS KEPT. A completed stage under the
            # scanner's own temporary prefix promises nothing and claims nothing, which makes it the
            # right place for evidence that no reserved name will take.
            return True                   # retention on: the kept stage IS the copy, complete
        finally:
            # THE CLOSE IS UNDER ITS OWN FINALLY. Round thirty-two put the identity cleanup before the
            # close (it needs the descriptor) and left the close after it unprotected; a cancellation
            # inside the cleanup's lstat leaked the descriptor at three sites (invariant leg, 0829b97).
            try:
                if stage_name is not None:
                    # EMPTINESS IS MEASURED ON THE STAGE, NOT INFERRED FROM THE COUNTER. A
                    # cancellation inside a write lands after the bytes are on disk and before
                    # `written` advances (the mid-copy arm of round thirty); the counter says
                    # nothing reached the stage, the stage says otherwise, and the stage is
                    # what is believed. Unreadable: keeping is the direction that cannot lose.
                    try:
                        _empty = os.fstat(stage_fd).st_size == 0
                    except OSError:
                        _empty = False
                    if _empty:
                        # AN EMPTY STAGE HOLDS NOTHING, and has only ever had one name — the
                        # nlink rule below would keep it forever (gate 37). Identity alone
                        # authorizes removing it, and retention does not apply to it:
                        # retention keeps BYTES through a cancellation, and there are none
                        # (executed review, 4632326 — an interrupt before the first read
                        # kept an empty stage).
                        _remove_own_stage(dirfd, stage_name, stage_fd)
                    elif not keep_stage and depth < 1:
                        # only while the reserved name (or any other) still reaches the copy
                        _remove_stage_if_another_name_remains(dirfd, stage_name, stage_fd, depth)
                    # AT DEPTH ONE THE STAGE IS KEPT, LINKED OR NOT. The rescue-of-a-rescue used to
                    # release its stage after its link, and a racer's second act on the reserved
                    # name then left the copy nameless with no further rescue (cold leg,
                    # 5b1a014). Kept, the module takes no last name of its own at that depth: a
                    # leftover under the temporary prefix is the whole cost, and both names must
                    # be taken by someone else before the close can free anything.
                    elif depth < 1:
                        # KEPT — AND THE NAME IS RE-ASKED BEFORE THE CLOSE. Retention means "do
                        # not unlink the stage name"; it was being read as "the stage name still
                        # reaches this inode", which is a different fact (cold leg, 407a89c). A
                        # stage name taken while the copy was being made leaves this descriptor
                        # as the copy's last reference, on every retention exit — a denied strip,
                        # a mode that did not land, every slot occupied, custody unconfirmed, a
                        # cancellation. The same question write_report and _stage_report ask
                        # before their closes, asked here for the copy, one level deep: at depth
                        # one the close is the limit (§5 needs a racer acting twice).
                        _false_or_rescue(dirfd, stage_name, stage_fd, depth + 1)
            finally:
                _close_quietly(stage_fd)
    finally:
        if src is not None:
            _close_quietly(src)

def _quarantine_unpublished(dirfd, tmp_name, fd, hits):
    """Keep a staged findings report the publish could not complete. Answer whether it was kept.

    A False answer means THIS FUNCTION DID NOT TAKE CUSTODY of the staged file — nothing more.
    Custody is a reserved name, or a complete copy kept under the temporary prefix by the
    copy-out (its answer is passed through unchanged). It
    does not mean the file should be removed, and the gate required that distinction to be written
    down rather than inferred. The caller decides separately, from _staged_holds_evidence, whether
    removing it would destroy anything: a staged CLEAN or an empty stage is removed, staged
    findings are left where they are. False is returned both when there is nothing worth keeping
    and when there IS and no reserved name would take it.

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
        # A FAILED IDENTITY READ IS NOT "THE STAGE STILL HAS A NAME". This fstat used to sit in a
        # block whose handler returned False, so one failed metadata read on a source whose name
        # was already gone took no custody and attempted no rescue; the close freed the last copy
        # (invariant leg, 0c28c5e). Either half failing means custody by name cannot be
        # established, and the descriptor-based copy is attempted before the caller releases it.
        try:
            held = os.fstat(fd)
            named = os.lstat(tmp_name, dir_fd=dirfd)
            diverged = (named.st_dev, named.st_ino) != (held.st_dev, held.st_ino)
        except OSError:
            diverged = True               # the name is gone, unreadable, or unverifiable: same situation
        if diverged:
            # THE NAME NO LONGER REACHES THESE BYTES, AND THE BYTES ARE STILL HERE. Answering
            # False was right about custody and wrong about consequence: the caller reads the
            # evidence flag, correctly leaves alone a name it must not touch, and then closes
            # the descriptor in its finally — and that close is the destruction, because this
            # descriptor was the last reference. A cold review leg reproduced it from the other
            # end of the module and measured the repair that does NOT work: os.link on the
            # descriptor directory is ENOENT once the link count is zero, so the inode cannot be
            # given a new name. It can still be READ. So the bytes are copied out under a fresh
            # reserved name before anyone closes anything.
            return _copy_out_unpublished(dirfd, fd)
        # THE SAME ACCESS POLICY AS A PUBLISHED REPORT. The failure that sends us here happens
        # BEFORE the ordinary installer's ACL strip, so retained evidence was arriving with the
        # directory's inherited ACL still on it. At 0600 the mask suppresses named entries, so
        # this was never an immediate read leak — but one chmod re-arms them, and evidence does
        # not get a weaker rule than the report it stands in for.
        if _XATTR_SUPPORTED:
            try:
                _strip_acl_by_fd(fd)
            except OSError as exc:
                if exc.errno not in _ACL_ABSENT:
                    # NOT best effort here, and that is the correction. A reserved quarantine name
                    # means "retained evidence, carrying the report's access policy". If the strip
                    # was DENIED then that policy is not on the file, and linking it into a
                    # reserved name anyway reports compliance that was never installed — the gate
                    # measured exactly that, an ACL-bearing retained file at 0600 presented as
                    # retained. Answering False keeps the BYTES only while the staged name is
                    # still this inode — so that is re-asked first (round 40).
                    return _false_or_rescue(dirfd, tmp_name, fd)
        os.fchmod(fd, _REPORT_MODE)
        # VERIFIED BEFORE A RESERVED NAME ASSERTS IT. A reserved name means "retained evidence,
        # carrying the report's access policy"; a chmod that returned without taking effect would
        # have that name assert a policy that is not on the file. Declining keeps the bytes at the
        # staged name, exactly as a denied strip does.
        if stat.S_IMODE(os.fstat(fd).st_mode) != _REPORT_MODE:
            return _false_or_rescue(dirfd, tmp_name, fd)
    except OSError:
        return _false_or_rescue(dirfd, tmp_name, fd)   # fchmod or fstat failed past the check

    # EXCLUSIVE, never replacing. os.link refuses an occupied name, so an earlier run's kept
    # findings cannot be overwritten to make room for this run's, and a populated directory at
    # one name simply moves us to the next rather than costing anyone their evidence.
    for candidate in _unpublished_slot_names():
        try:
            _link_held_inode(fd, candidate, dirfd)
        except FileNotFoundError:
            # NO CUSTODY WAS TAKEN — ENOENT here does not prove the count is zero, only that the
            # reserved name is not ours. Its name may have been taken between the identity check
            # and this link, and the descriptor is the only thing still holding the findings.
            # Two review legs measured the previous shape closing that descriptor on a False
            # answer. The rescue asks first whether the staged name still reaches the stage:
            # intact, the stage IS the copy and nothing more is made (an unconditional copy here
            # put a second 0600 copy beside an intact stage — executed review, 5b1a014); gone,
            # the bytes are copied out through the descriptor. Nothing at the old staged name is
            # ours to remove.
            return _false_or_rescue(dirfd, tmp_name, fd)
        except _CustodyUnconfirmed:
            # THE LINK MAY HAVE LANDED. False here means "no custody confirmed" and nothing
            # more: the stage is left where it is, and no second reserved name is tried.
            return False
        except OSError:
            continue                      # occupied, unusable, or unsupported — try the next
        # THE LINK IS THE HELD INODE BY CONSTRUCTION — made through the descriptor directory,
        # it cannot have attached anything else. The staged name is removed only if it still
        # refers to that same inode; a substituted name is left alone.
        # AND ONLY WHILE ANOTHER NAME STILL REACHES THE INODE. The reserved name just linked can
        # be ended by someone else before this removal; removing the stage then takes the LAST
        # name, and the caller's close frees the findings while believing them kept (cold leg,
        # 0c28c5e). The same nlink rule preservation applies to a status-line slot. A leftover
        # stage is harmless; a nameless inode is the loss this whole path exists to prevent.
        # AND, SINCE ROUND FORTY-NINE, THE LINK COUNT IS RE-READ AFTER THE UNLINK: a reserved name
        # ended inside the helper's own four-syscall window made that unlink the last one, and
        # the helper now copies the bytes out before this function answers and the caller closes.
        _remove_stage_if_another_name_remains(dirfd, tmp_name, fd)
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
    a configuration, and six existing arms were pinning exactly that — a directory the scanner
    cannot write into refuses the scan (owner-unreadable 0300 is accepted; owner read is not
    needed to publish). A umask the operator did not choose, applied to a directory this code made
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
    # swapped for a SYMLINK between the create and this open (a directory-for-directory swap is
    # the creation-flag limit stated below). Every current caller supplies the parent; the
    # pathname form remains for a caller that does not, still opened O_NOFOLLOW.
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
    """Create a directory chain in which every component THIS CALL CREATES is owner-only, resolving each step
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

    try:
        fd, _ = _open_dir_nofollow(cursor, None)
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


def _stage_report(dirfd, body, evidence=False):
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
    # O_RDWR, NOT O_WRONLY: the rescue reads a diverged stage back through this descriptor when
    # the reopen by the descriptor directory is refused (a 0200 stage whose chmod did not stick —
    # cold leg, e1c1404). A write-only descriptor could only be read by that reopen.
    flags = os.O_CREAT | os.O_EXCL | os.O_RDWR | nofollow
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
            # THE REPORT IS WRITTEN AS THE BYTES SCAN() ROUND-TRIPPED. scan() stores every path
            # with os.fsdecode — surrogateescape, reversible — and a text-mode write with strict
            # UTF-8 raised UnicodeEncodeError on the first non-UTF-8 name, before a byte reached
            # the disk: this scan's findings, the plain ones included, landed nowhere (cold leg,
            # 0829b97). Encoding with the same error handler puts the original bytes back.
            # POLICY BEFORE BYTES, on the primary stage as on the rescue copy. With a default ACL
            # on the directory the create mode does not yield owner-only access, and a listable
            # `_reports` let a group reader open the temporary name while the findings were being
            # written (cold leg, 0c28c5e). Best effort here; the verified install still follows.
            _narrow_leftover(fd)
            with os.fdopen(fd, "wb", closefd=False) as handle:
                handle.write(body.encode("utf-8", "surrogateescape"))
        except BaseException:
            # KEEP WHAT REACHED THE DISK, WHEN IT IS EVIDENCE. This cleanup predates every
            # retention rule the file has since grown, and it sits where none of them can reach:
            # the caller never receives this descriptor, so the quarantine path and the
            # retain-the-stage rule cannot see these bytes at all. Here is the only chance to
            # keep them.
            #
            # The gate fault-injected an EFBIG partway through a findings body — 128 bytes on
            # disk, removed by this handler, no surviving copy. The cold leg had traced the same
            # path statically five rounds earlier and said it had not fault-injected it; a traced
            # defect with no reproduction attached is still a defect, and it read as lower
            # priority only because it arrived without a measurement.
            #
            # A partial findings report is truncated evidence, which is worth a leftover file. It
            # stays at the staged name, which promises nothing and claims nothing. A partial
            # STATUS line is not evidence and is removed: half of "scan_gate: CLEAN" sitting
            # beside a refusal is the confusion the rest of this file exists to prevent.
            keep = evidence
            try:
                try:
                    if keep:
                        try:
                            keep = os.fstat(fd).st_size > 0
                        except OSError:
                            keep = True   # cannot tell; keeping is the direction that cannot lose
                    # The unlink is identity-checked through the descriptor, so it runs BEFORE
                    # the close: a name that has stopped being this inode is someone else's.
                    if not keep:
                        _remove_own_stage(dirfd, name, fd)
                finally:
                    # KEPT, READABLE, AND STRIPPED — IN CLEANUP THAT RUNS REGARDLESS. The caller
                    # never receives this descriptor, so nothing downstream can narrow a kept
                    # partial stage; under a umask that masks owner read it sat at 0200, with a
                    # default ACL it kept the inherited entries, and a cancellation during the
                    # size read jumped past the narrowing (invariant leg, three rounds). `keep`
                    # is still True here on that cancellation, so the stage is narrowed anyway.
                    if keep:
                        # THE NAME IS RE-CHECKED AFTER THE NARROWING AND BEFORE THE CLOSE, IN
                        # CLEANUP A CANCELLATION INSIDE THE NARROWING CANNOT SKIP. The narrowing
                        # is several syscalls on the descriptor with no eye on the name.
                        # write_report's handler learnt in round forty to look again before its
                        # close; this handler — the one holder of a partial body the caller
                        # never sees — narrowed and closed without looking (cold leg, d7e4a3c),
                        # and the re-check then sat after the narrowing where an interrupt
                        # inside it jumped past (invariant leg, 4632326). Still ours: the name
                        # keeps the bytes. Swapped or unlinked: the bytes are copied out through
                        # the descriptor to a reserved name before the close would free them —
                        # where a descriptor directory exists, a temporary name can be made
                        # and the source can be read, which is the limit `_copy_out_unpublished`
                        # states.
                        try:
                            _narrow_leftover(fd)
                        finally:
                            _false_or_rescue(dirfd, name, fd)
            finally:
                _close_quietly(fd)        # under finally: a cancellation in the cleanup leaked it
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
    try:
        # Through the same helper the report directory uses, which carries the O_PATH fallback.
        # A 0300 directory — create and traverse, no read — fails an O_RDONLY open, and round
        # fourteen built that fallback for exactly this case. It was wired to `_reports` and not
        # to the root above it, so a 0300 `_reports` published while a 0300 scan root could not
        # publish at all and the refusal writer left no artifact either.
        parent_fd, _ = _open_dir_nofollow(staging, None)
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

        fd, tmp_name = _stage_report(dirfd, body, evidence=bool(hits))
        _staged_ctime_ns = None           # unknown age until read: no reference stamp means no sweep
        _published = False                # flips the instant the replace lands: from then on the
        try:                              # stage IS the report, and the handler must not copy it
            # Inside the ownership try, so a cancellation during this read still reaches the close
            # in the finally (invariant leg, 3c075f0).
            try:
                # THE REFERENCE STAMP FOR THE SWEEP BELOW, taken on the staged inode before
                # anything is published. Read through the held descriptor, so no name is involved.
                _staged_ctime_ns = os.fstat(fd).st_ctime_ns
            except OSError:
                _staged_ctime_ns = None
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
            _held = os.fstat(fd)          # the held side first; the name lookup is the syscall before the rename
            _named = os.lstat(tmp_name, dir_fd=dirfd)
            if (_named.st_dev, _named.st_ino) != (_held.st_dev, _held.st_ino):
                raise ScanRefused("report-path-unsafe '_reports/scan_report.txt'")
            os.replace(tmp_name, _REPORT_NAME, src_dir_fd=dirfd, dst_dir_fd=dirfd)
            _published = True             # a cancellation between these two statements reaches
                                          # the handler unflagged: a duplicate copy, no loss
            # A fresh scan has just published a report, so anything preserved from an EARLIER
            # generation is stale. Leaving it meant the sibling slot stayed occupied and the next
            # refusal could not keep the findings this run produced — measured: an old report's
            # hits held the slot while the current ones were destroyed. A planted name had the
            # same effect permanently, which made refusing to overwrite into a
            # denial-of-preservation. The slot belongs to one report generation, and this is
            # where that generation ends. The unpublished name belongs to the generation that
            # could not publish, and a run that HAS published ends that one too.
            #
            # "ANYTHING RESERVED IS AN EARLIER GENERATION" IS FALSE WHILE ANOTHER WRITER IS
            # RUNNING, and the gate reproduced it with no adversary at all: a second writer that
            # could not publish retained its findings under the quarantine name DURING this run,
            # and this sweep removed their only name. So the sweep skips anything created after
            # this run staged its own report. A file another writer quarantined while we were
            # working is newer than our staging by construction.
            #
            # This is a NARROWING, not an ownership proof, and the difference is worth stating.
            # st_ctime cannot be set by a writer the way mtime can, so it is not forgeable
            # without the clock — but a file reserved BEFORE we staged is still swept, and that
            # case is indistinguishable from the stale generation the sweep exists to clear.
            # What it removes is the case that needs nobody to be hostile.
            # NO REFERENCE TIMESTAMP MEANS NO SWEEP. The previous shape short-circuited the age
            # test to "not newer" when the staged fstat had failed, and swept — the second of the
            # two metadata inputs the age guard depends on, repaired one round after the first.
            # A question this code cannot answer never authorizes destruction; that has to hold
            # for the reference side of the comparison too.
            for _name in (_superseded_slot_names(include_unpublished=True)
                          if _staged_ctime_ns is not None else ()):
                # AGED AND REMOVED AS ONE INODE. The age check used to lstat the name and the
                # unlink then acted on the name — a reserved name freed and re-linked by another
                # run's quarantine between the two was deleted on the first occupant's age (cold
                # leg, 0829b97). The entry is opened O_PATH|O_NOFOLLOW, aged by fstat, and removed
                # only while the name still refers to that descriptor's inode. What remains is
                # the helper's own lookup-to-unlink interval, the same as everywhere else.
                # WHERE O_PATH IS ABSENT THE OPEN READS, AND A READING OPEN OF A FIFO WAITS FOR A
                # WRITER: O_NONBLOCK makes it return instead (invariant leg, f69cfff — a FIFO
                # planted at a reserved name hung the scanner after it had published).
                _swept_fd = None
                try:                      # ONE finally owns the descriptor from open to removal
                    try:
                        _swept_fd = os.open(_name, getattr(os, "O_PATH", os.O_RDONLY) | _NOFOLLOW_FLAG
                                            | getattr(os, "O_NONBLOCK", 0), dir_fd=dirfd)
                        _swept = os.fstat(_swept_fd)
                        if _swept.st_ctime_ns >= _staged_ctime_ns:
                            # NOT STRICTLY OLDER — including EQUAL. A retained entry created in
                            # the same tick as this run's stage has the same ctime on a real
                            # filesystem (invariant leg measured it, no clock mocked). Equality is
                            # not age; only a strictly older entry is this run's to end. ctime is
                            # still not provenance and not a monotonic clock — those are limits.
                            continue
                    except FileNotFoundError:
                        continue          # already gone; nothing to remove
                    except OSError:
                        # A QUESTION THIS CODE CANNOT ANSWER NEVER AUTHORIZES DESTRUCTION. The
                        # previous shape fell through to the unlink here, which is precisely the
                        # outcome the age check was added to prevent — the check protects a
                        # concurrent writer's retained findings, and an unreadable answer was letting
                        # them be removed anyway. `_staged_holds_evidence` was rewritten to invert
                        # this same reasoning; the sweep had kept the old direction.
                        continue
                    # THE SWEEP IS AN AGE RULE INSIDE A SCANNER-OWNED NAMESPACE — the README's reserved
                    # names — and, since round thirty-five, it is applied to the inode that was aged
                    # and to nothing else: the entry is held open above, and the removal below is by
                    # identity — through that descriptor for a file; for a directory, which rmdir can only
                    # take by name, by an lstat of the name immediately before the rmdir compared with the
                    # held entry (invariant leg, e1c1404). What the rule cannot do is know who created an
                    # entry; a reserved name is scanner-owned by declaration, not by provenance.
                    if stat.S_ISDIR(_swept.st_mode):
                        # A directory at that name cannot be unlinked, and gate review reproduced
                        # one blocking every later preservation permanently. An EMPTY one is
                        # removable; a populated one is somebody else's data and is left alone.
                        # Same identity rule: the name must still be the directory that was aged.
                        try:
                            _now = os.lstat(_name, dir_fd=dirfd)
                            if (_now.st_dev, _now.st_ino) == (_swept.st_dev, _swept.st_ino):
                                os.rmdir(_name, dir_fd=dirfd)
                        except OSError:
                            pass
                    else:
                        _remove_own_stage(dirfd, _name, _swept_fd)
                finally:
                    if _swept_fd is not None:
                        _close_quietly(_swept_fd)
        except BaseException:
            if _published:
                # ALREADY PUBLISHED. A cancellation or error in the post-publish sweep reaches
                # this handler with the stage already renamed onto the canonical name; the
                # descriptor-based quarantine then copied the published report out again under
                # a reserved name — a duplicate, no loss (executed review, gate 37). Nothing here
                # is unpublished; re-raise and let the finally close the descriptor.
                raise
            # KEEP THE FINDINGS if there are any and they made it to disk. Unlinking here
            # destroyed the hits this scan had just written, and the refusal that follows cannot
            # carry them.
            if not _quarantine_unpublished(dirfd, tmp_name, fd, hits):
                # NOTHING TO KEEP, OR NOWHERE TO KEEP IT. A staged CLEAN carries no evidence and
                # is removed. Staged FINDINGS that no quarantine name would take are LEFT WHERE
                # THEY ARE: the staged name is inside the scanner's own reserved prefix, and a
                # retained temporary holding real hits is strictly better than deleting them
                # because every destination was occupied. The gate measured the alternative — a
                # populated directory at the quarantine name cost a run its findings. "Where
                # they are" is the staged NAME while it still reaches them; a name that diverged
                # is rescued by copy through the descriptor, which needs a descriptor directory,
                # a creatable temporary name and a readable source — the limit
                # `_copy_out_unpublished` states.
                # IDENTITY-CHECKED, like every other unlink of a name this file created. This
                # was the CLEAN twin of the refusal writer's round-31 defect: a staged status
                # line's cleanup unlinked the NAME, and the name had become findings report B's
                # last one (cold leg, f153122).
                if not _staged_holds_evidence(fd, hits):
                    _remove_own_stage(dirfd, tmp_name, fd)
                else:
                    # KEPT, READABLE BY ITS OWNER, AND STRIPPED. The stage is created at the
                    # umask-masked mode, which can be 0200, with whatever ACL the directory gave
                    # it; quarantine only installs the policy on the path that reserves a name. A
                    # kept stage whose policy could not be installed, or whose evidence could not
                    # be read, was left at that create mode — evidence nobody could read (rounds
                    # 24 and 25) — and later with its inherited entries (cold leg, 3c075f0). Best
                    # effort, through the held descriptor; the stage still promises nothing.
                    # THE RE-CHECK RUNS IN CLEANUP AN INTERRUPT INSIDE THE NARROWING CANNOT SKIP — the
                    # same composition `_stage_report` was given in round forty-five; here the pair sat as
                    # two statements and a cancellation inside the first jumped the second (cold leg, 4632326).
                    try:
                        _narrow_leftover(fd)
                        # AND THE NAME IS RE-ASKED AFTER THE NARROWING, immediately before the close
                        # in the finally. "False → the caller keeps the name" is only safe if the
                        # next act is not close(fd); the narrowing above is the same strip + fchmod
                        # the helper stopped trusting, and a decoy renamed onto the name during it
                        # made the close free the last copy (cold leg, 279368a). What remains is the
                        # interval between this check and the close.
                    finally:
                        _false_or_rescue(dirfd, tmp_name, fd)
            raise
        finally:
            _close_quietly(fd)
    finally:
        _close_quietly(dirfd)


# Every status report this tool writes begins with these bytes; a findings report never does.
_STATUS_LINE_PREFIX = b"scan_gate: "


def _open_held_copy(dirfd, name, expect):
    """Open NAME and return a descriptor ONLY if it still refers to the inode we preserved.

    This is the hold that preservation never had. Three review legs, across two providers and
    with no shared premise, arrived at the same sentence about this path: rounds eighteen to
    twenty-three moved every metadata operation onto a held descriptor so that a name in the
    report directory could not be the object of a chmod or a replace, and preservation went on
    asking a NAME whether the policy was on "the inode we actually hold". A name lookup is not a
    hold, and the legs reproduced what that costs — a planted file narrowed and reported as our
    preserved copy, a planted status line read as our classification and the findings released.

    Returns (fd, via_proc), or (None, False) when the name is gone, is not a regular file, or is
    no longer the inode it was linked to. O_PATH where available, for the reasons the narrowing
    already documents: it needs no read permission and it cannot wait on a FIFO.

    What this does NOT do is make the path race-free, and the limits section says so plainly. The
    descriptor pins an INODE. Whether some directory entry still names that inode at the instant
    a later syscall runs is a different question, and POSIX offers no way to bind the two.
    """
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    nonblock = getattr(os, "O_NONBLOCK", 0)
    opath = getattr(os, "O_PATH", 0)
    via_proc = bool(opath) and _PROC_FD_DIR is not None
    flags = (opath if via_proc else (os.O_RDONLY | nonblock)) | nofollow
    try:
        fd = os.open(name, flags, dir_fd=dirfd)
    except OSError:
        return None, False
    # THE ACQUISITION INTERVAL IS OWNED HERE. Between the open and the return the caller has
    # not received the descriptor, so its cleanup cannot close it; an interrupt in this window
    # leaked the handle (measured by the gate). Ordinary OSError still answers (None, False);
    # anything else closes and re-raises.
    try:
        try:
            got = os.fstat(fd)
        except OSError:
            _close_quietly(fd)
            return None, False
        if not stat.S_ISREG(got.st_mode) or (got.st_dev, got.st_ino) != (expect.st_dev, expect.st_ino):
            _close_quietly(fd)
            return None, False
    except BaseException:
        _close_quietly(fd)
        raise
    return fd, via_proc


def _read_prefix_held(fd, via_proc, count):
    """Read the first COUNT bytes of the inode behind FD, without naming it again."""
    if via_proc:
        nonblock = getattr(os, "O_NONBLOCK", 0)
        try:
            rfd = os.open("%s/%d" % (_PROC_FD_DIR, fd), os.O_RDONLY | nonblock)
        except OSError:
            return None
        try:
            return os.read(rfd, count)
        except OSError:
            return None
        finally:
            _close_quietly(rfd)
    try:
        return os.pread(fd, count, 0)
    except OSError:
        return None


def _narrow_held_copy(fd, via_proc):
    """The narrowing, on an inode already held. See _narrow_kept_copy for why each step is here."""
    target = "%s/%d" % (_PROC_FD_DIR, fd) if via_proc else fd
    # WHERE THE XATTR CALLS DO NOT EXIST, NOTHING WAS STRIPPED — and a review leg was right that
    # returning True here reported a removal that was never attempted. The cap below is still
    # ATTEMPTED either way — its own failure is swallowed on this branch — and what changes is
    # the claim this function makes about the strip.
    if not _XATTR_SUPPORTED:
        try:
            os.chmod(target, _REPORT_MODE)
        except OSError:
            pass
        return False
    try:
        _strip_acl_by_fd(fd)
    except OSError as exc:
        if exc.errno not in _ACL_ABSENT:
            try:
                os.chmod(target, _REPORT_MODE)
            except OSError:
                pass
            return False
    try:
        os.chmod(target, _REPORT_MODE)
    except OSError:
        return False
    # THE ANSWER IS WHETHER THE POLICY IS VERIFIED ON THE INODE, not whether chmod returned —
    # and where it cannot be verified (no xattr API: the early return above) the answer is
    # False, which is the platform limit the README states. The publish
    # path has verified its fchmod by fstat since round seventeen; this path answered True on
    # the return code alone, and a chmod that returns without taking effect (a filesystem that
    # ignores mode bits) would then record a slot and authorize the replace while the preserved
    # copy stayed group- or other-readable — the leak preservation exists to close.
    try:
        return stat.S_IMODE(os.fstat(fd).st_mode) == _REPORT_MODE
    except OSError:
        return False


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

    RETURNS WHETHER THE POLICY IS ACTUALLY ON THE INODE, which is the whole of round twenty-six.
    Every step here is still best effort in the sense that no ordinary I/O error raises (a
    cancellation still propagates through the finally) and nothing is deleted —
    but "best effort" was being read by the caller as "done". A denied strip was swallowed, this
    function returned None either way, and a reserved name went on standing for a compliance that
    had not been installed. The caller now decides what to do with the answer; failing to narrow
    still destroys nothing.
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
        return False
    try:
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                return False              # not a preserved report; not ours to re-mode
        except OSError:
            return False
        # ONE IMPLEMENTATION OF THE NARROWING, not two that drift. This name is kept for its
        # suite, which exercises the name-based entry directly; the work is the held-descriptor helper's, so the claim this
        # function makes about the policy is the same claim that helper makes.
        return _narrow_held_copy(fd, via_proc)
    finally:
        _close_quietly(fd)


def _replace_canonical_guarded(dirfd, tmp_name, fd, slot_guard):
    """THE ONE PLACE A REFUSAL REPLACES THE CANONICAL NAME. Both refusal branches call this.

    Round twenty-nine put the spent-authorization re-check before the ordinary replace and not
    before the fallback one, and the gate reproduced the fallback spending a stale authorization
    the very next round. That is the third time this file has fixed one branch and left its
    sibling. The review leg's structural advice was to put the shared preconditions in one
    publication path, so that there is no second branch to forget — this is that path.

    Returns True after replacing. Returns False when the authorization no longer applies: the
    preserved slot no longer refers to the inode that was preserved, OR the canonical name no
    longer refers to the inode preservation classified. Raises when the staged name has stopped
    naming the staged inode, which is the caller's existing refusal reason.

    The second refusal is round thirty-one's. The authorization used to say only "the copy of
    what I saw is still there"; it never said WHAT it had seen. So a findings report B that
    arrived at the canonical name after report A was preserved was replaced on A's authority —
    nobody had preserved B, and the guard for A could not tell. The gate substituted B during
    the policy install and watched it go. Now the authorization names the inode it authorizes
    destroying, and is refused when the name has stopped reaching it.
    """
    # THE AUTHORIZATION IS RE-CHECKED WHERE IT IS SPENT, not only where it was computed — and it
    # is checked on BOTH ends: the copy preservation kept, and the original it was a copy of.
    if not _slot_still_holds(dirfd, slot_guard):
        return False
    if not _canonical_still_classified(dirfd, slot_guard):
        return False
    # THE NAME LOOKUP FOR THE REPLACE SOURCE IS THE SYSCALL BEFORE THE RENAME, and nowhere
    # earlier; the held side is read before it, since a descriptor cannot change under us. Round thirty-one had it first and the two guards after it, which put two lstat
    # round-trips between "the staged name is our inode" and the rename that acts on that name;
    # the cold leg planted a 0644 file at the staged name during the second guard and the rename
    # published the plant under the canonical name, mode and all. What remains after this
    # ordering is the lookup-to-rename interval that no pathname check closes (limits §5).
    held = os.fstat(fd)                   # the held side first: it cannot change under us
    named = os.lstat(tmp_name, dir_fd=dirfd)
    if (named.st_dev, named.st_ino) != (held.st_dev, held.st_ino):
        raise OSError(errno.EIO, "report-staged-name-diverged")
    os.replace(tmp_name, _REPORT_NAME, src_dir_fd=dirfd, dst_dir_fd=dirfd)
    return True


def _narrow_leftover(fd):
    """A kept leftover the caller never receives gets the strip and the mode, best effort.

    Two keep paths — a partial stage after a write failure, and a stage kept after a failed
    quarantine — set the mode and never attempted the strip, so a findings inode with whatever
    ACL its directory gave it sat under a name that promises nothing (cold leg, 3c075f0). The
    leftover still claims nothing; it is simply as narrow as this code can make it. Neither call
    is verified afterwards: a leftover whose fchmod failed under a umask that masks owner read
    stays at 0200 — on disk, unreadable to the owner — and nothing else can be done for it here.
    A leftover whose strip was DENIED keeps its inherited entries, masked to nothing at 0600 and
    one chmod from live; that is why no reserved name is ever taken in that state, and why the
    leftover sits under the temporary prefix, which claims nothing (README limits).
    """
    if _XATTR_SUPPORTED:
        try:
            _strip_acl_by_fd(fd)
        except OSError:
            pass
    try:
        os.fchmod(fd, _REPORT_MODE)
    except OSError:
        # A PATH-ONLY DESCRIPTOR CANNOT BE FCHMOD'ED (EBADF — the platform fact
        # `_harden_report_dir` and `_narrow_held_copy` already work around), and preservation's no-slot rescue hands exactly such a descriptor to
        # the copy-out, whose reopen then needs owner-read on an inode this could not narrow: a
        # mode-000 findings report whose name was taken was freed at the close (cold leg and an
        # executed review, both on 4e0be0a). The same detour `_narrow_held_copy` uses reaches
        # the held inode: chmod through the descriptor directory, which follows the magic link
        # to the inode this descriptor pins and to nothing else. Not verified here, on purpose:
        # for the copy-out the reopen that follows IS the verification — owner-read that did not
        # land makes the reopen fail and the rescue decline, which is the path-only limit the
        # README states; a verified answer would change no caller's next act (cold leg, 407a89c).
        if _PROC_FD_DIR is not None:
            try:
                os.chmod("%s/%d" % (_PROC_FD_DIR, fd), _REPORT_MODE)
            except OSError:
                pass


def _false_or_rescue(dirfd, tmp_name, fd, depth=0):
    """The answer to a failure AFTER quarantine's identity check: False if the staged name is still
    the held inode (the caller keeps the name, as before), else the descriptor-based rescue.

    The strip, the fchmod and the mode verify each used to `return False` on failure, trusting the
    identity check made three syscalls earlier. An executed on-box review renamed a decoy onto the
    staged name during the failing call: the caller then kept "the name" — the decoy — and its
    close freed the findings (round 40). The pre-check twin of this was closed in rounds 37–38.
    """
    try:
        held = os.fstat(fd)               # the held side first — it cannot change under us —
        named = os.lstat(tmp_name, dir_fd=dirfd)   # and the name LAST, like every other spend site
        if (named.st_dev, named.st_ino) == (held.st_dev, held.st_ino):
            return False                  # still ours by name: the caller keeps it
    except OSError:
        pass                              # cannot tell: treat as diverged
    return _copy_out_unpublished(dirfd, fd, depth)   # the depth travels with the rescue (gate 43)


def _remove_stage_if_another_name_remains(dirfd, name, fd, depth=0):
    """Remove NAME (identity-checked) only if the held inode has at least one other name — and
    if that unlink turns out to have taken the last name anyway, copy the bytes out before the
    caller's close can free them — where a copy can be made; when none can (no creatable name,
    no readable source) the close frees them, which is the copy-out's stated limit, and the
    caller still answers "custody taken" for a link that no longer exists (invariant leg, 5b1a014).

    The nlink read and the unlink are FOUR syscalls apart (the nlink fstat, then the helper's
    fstat, lstat and unlink), and the interval is the same check-then-act limit as everywhere
    in this file. What the pre-check closes is the wide case: a reserved name ended before the
    stage removal. What the post-check closes is the narrow one a cold leg named on e71e440:
    the reserved name ended INSIDE that interval, the stage unlink took the last name, the
    caller was told custody was taken, and its close freed the findings. The release-path
    question is not "is the name still ours" (after a deliberate unlink it never is) but "does
    the inode still have a name": the link count is re-read on the held descriptor after the
    unlink, and zero sends the bytes through the copy-out, one level deep.
    """
    try:
        nlink = os.fstat(fd).st_nlink
    except OSError:
        return                            # cannot tell BEFORE any act: keep the name
    if nlink == 1:
        return                            # the stage may be the last name: keep it
    if nlink >= 2:
        _remove_own_stage(dirfd, name, fd)
    # nlink was ZERO ON ENTRY (every name already taken — the racer's two acts, and no act of
    # ours; the cold leg on 5b1a014 read the old `>= 2` gate skipping exactly the case this
    # helper exists for), or our unlink may just have taken the last name: either way this
    # descriptor may be the last reference, and the copy-out decides on the count it reads.
    if depth < 1:
        try:
            nameless = os.fstat(fd).st_nlink == 0
        except OSError:
            # CANNOT TELL AFTER THE UNLINK — and "keep the name" no longer means anything, the
            # name is gone. A copy is attempted: at worst a duplicate, where silence was a loss
            # (executed review, 5b1a014).
            nameless = True
        if nameless:
            _copy_out_unpublished(dirfd, fd, depth + 1)


def _remove_own_stage(dirfd, tmp_name, fd):
    """Remove NAME only if it still refers to the held inode. Best effort. Identity, not provenance.

    Used for refusal stages, published rescue stages and released status slots alike: the
    module's rule against deleting a name it has not just verified applies to each, and one
    helper means one place to get it right. The interval between the lstat and the unlink is
    the documented limit; this narrows the window to that interval and does not close it.

    THE NAME IS LOOKED UP LAST. The unlink acts on the name, so the name is what must be fresh
    when the comparison is made; a lookup taken before the held fstat is stale by the time it
    is compared, and a findings inode renamed onto an empty stage's name between the two
    syscalls lost its last name to a match against that stale lookup (cold leg, d7e4a3c). The
    same order as `_false_or_rescue` and `_replace_canonical_guarded`.
    """
    try:
        held = os.fstat(fd)
        named = os.lstat(tmp_name, dir_fd=dirfd)
        if (named.st_dev, named.st_ino) == (held.st_dev, held.st_ino):
            os.unlink(tmp_name, dir_fd=dirfd)
    except OSError:
        pass


def _slot_still_holds(dirfd, guard):
    """Does the preserved slot still refer to the inode preservation actually kept?

    `_preserve_superseded` closes its descriptor and answers a boolean, and the caller then stages
    a refusal body and installs its policy before replacing anything. That is many syscalls, and a
    review leg named the consequence: the authorization is SPENT long after it was computed, so a
    slot taken in that window leaves the findings with no name once the replace lands.

    This does not close the interval between its own check and the following rename — nothing in
    POSIX can — and it is not offered as if it did. It closes the wide one.
    """
    slots = [entry for entry in guard if entry[0] == "slot"]
    if not slots:
        return True                       # nothing was preserved; nothing to re-check
    _tag, name, dev, ino = slots[0]
    try:
        seen = os.lstat(name, dir_fd=dirfd)
    except OSError:
        return False                      # cannot confirm the copy is still there: do not spend
    return stat.S_ISREG(seen.st_mode) and (seen.st_dev, seen.st_ino) == (dev, ino)


def _canonical_still_classified(dirfd, guard):
    """Does the canonical name still refer to what preservation classified?

    `_preserve_superseded` records the identity of the report it looked at — regular file,
    status line, absent — as the first thing it learns, before any slot is taken. This asks
    whether that is still what the name reaches. A different inode there is a report nobody
    classified and nobody preserved, and replacing it is not what the caller was authorized to
    do. A recorded absence must still be an absence for the same reason.

    This closes substitution BY NAME between preservation and the check. It does not see an
    in-place write to the same inode after classification — a (dev, ino) pair is an identity,
    not a content stamp — and the interval between this check and the rename is the documented
    limit (§5 of the README's limits).
    """
    canon = [entry for entry in guard if entry[0] == "canonical"]
    if not canon:
        return True                       # preservation was not consulted; nothing recorded
    _tag, name, dev, ino = canon[-1]
    try:
        seen = os.lstat(name, dir_fd=dirfd)
    except FileNotFoundError:
        return dev is None                # absent now: only fine if it was absent then
    except OSError:
        return False                      # cannot confirm: do not spend
    if dev is None:
        return False                      # something arrived at a name that was absent
    return (seen.st_dev, seen.st_ino) == (dev, ino)


def _preserve_superseded(dirfd, report_name, guard_out=None):
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
        if guard_out is not None:
            guard_out.append(("canonical", report_name, None, None))
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
    # WHAT WAS CLASSIFIED IS RECORDED BEFORE ANYTHING IS DONE ABOUT IT, so the authorization this
    # call returns names the inode it is about — on every path below, the status-line one
    # included. Recorded here and not beside the slot: a status line takes no slot, and the
    # replace that follows a status-line verdict is exactly as capable of destroying a findings
    # report that arrived afterwards.
    if guard_out is not None:
        guard_out.append(("canonical", report_name, previous.st_dev, previous.st_ino))
    if not stat.S_ISREG(previous.st_mode):
        return True                       # not a regular file; not ours to preserve

    linked = None
    # THE SAME INODE NEVER TAKES A SECOND SLOT FROM ONE RUN. (Two runs preserving the same
    # report at once can each take one — the concurrent same-UID writer limit; capacity, not
    # findings.) The link loop below takes the first FREE name,
    # and the "already preserved" scan ran only when no name was free — so every refusal over a
    # report whose policy could not be installed (a denied strip; no xattr API at all) linked
    # the same inode into a fresh slot, and eight refusals of one report exhausted the capacity
    # the README calls finite (gate 38, measured: three refusals, three slots). A slot that
    # already holds this inode is used as-is; a free name is taken only when none does.
    for candidate in _superseded_slot_names():
        try:
            kept = os.lstat(candidate, dir_fd=dirfd)
        except OSError:
            continue
        if (stat.S_ISREG(kept.st_mode)
                and (kept.st_dev, kept.st_ino) == (previous.st_dev, previous.st_ino)):
            linked = candidate
            break
    # THE LINK IS MADE FROM A HELD DESCRIPTOR, NOT FROM THE NAME. This was the one reserved-name
    # link still made by pathname, and the cold leg reproduced what the README said that costs:
    # a report substituted at the canonical name between the lookup above and the link took a
    # reserved second name at whatever mode it had (e1c1404). The canonical name is opened and
    # bound by identity to `previous`; the link is then the recorded inode by construction. A
    # name that no longer holds that inode preserves nothing, and the replacement is declined.
    _cfd = None
    _unconfirmed = False
    if linked is None:
        _cfd, _cvia = _open_held_copy(dirfd, report_name, previous)
        if _cfd is None:
            return False
    try:
        for candidate in (_superseded_slot_names() if linked is None else ()):
            try:
                _link_held_inode(_cfd, candidate, dirfd)
            except FileNotFoundError:
                continue                  # no custody: taken under us, or the inode has no name left
            except _CustodyUnconfirmed:
                # CUSTODY MAY HAVE LANDED; a second name must not follow. This does NOT return
                # here: the rescue below still runs, because a slot that was taken under us after
                # the link and a canonical name taken meanwhile leave this descriptor as the last
                # reference (invariant leg, 4e0be0a — the early return closed it unrescued).
                _unconfirmed = True
                break
            except OSError:
                continue                  # occupied, unusable, or unsupported — try the next
            linked = candidate
            break
        if linked is None and _cfd is not None:
            # NO SLOT TOOK THE HELD INODE. If its name has meanwhile stopped reaching it — the
            # substitution that used to hand a decoy a reserved name now leaves the recorded
            # inode with this descriptor as its last reference — the bytes are copied out
            # through the descriptor to an unpublished name: the same copy-out the stage paths
            # get, except that this descriptor is path-only, so the copy-out's narrowing reaches
            # it through the descriptor directory rather than fchmod, and its bytes are read by
            # the reopen rather than pread (round forty-seven; the sentence here used to say
            # "the same rescue", which a cold leg measured as false for a mode-000 report).
            # Still at its name: nothing to do, and the replacement is declined below.
            _false_or_rescue(dirfd, report_name, _cfd)
    finally:
        if _cfd is not None:
            _close_quietly(_cfd)
    if _unconfirmed:
        return False                      # nothing this call can vouch for; the replacement is declined

    held_fd, held_via_proc = (None, False)
    if linked is not None:
        held_fd, held_via_proc = _open_held_copy(dirfd, linked, previous)
        if held_fd is None:
            # THE SLOT IS NO LONGER THE INODE THAT WAS LINKED INTO IT (by this call, or by the
            # earlier one whose slot the pre-scan found). Something replaced that name
            # between the link and this open. Everything downstream — the narrowing, the
            # classification, the decision to release the slot — would be describing a file this
            # scan never preserved, which is exactly the sequence three legs reproduced. Nothing
            # is unlinked (the name is not ours to remove now, and round twenty-six is why that
            # matters) and the replacement is declined, so the findings stay where they are.
            return False

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
        #
        # The ANSWER is kept. A reserved name THIS FUNCTION CREATES asserts that the inode it
        # linked carries the report's access policy; it says nothing about a file that was
        # already sitting at such a name, and nothing about what another writer may put there
        # afterwards. Read as a claim about whatever currently occupies the name, it is false —
        # the gate demonstrated a substitution. A reserved name asserts that the retained
        # report carries its access policy; if it does not, this call has to decide that below rather than
        # hand the caller an authorization built on a strip that was refused.
        try:
            narrowed = _narrow_held_copy(held_fd, held_via_proc)
        except BaseException:
            _close_quietly(held_fd)       # an interrupt here used to leak the descriptor
            raise

    # Classify only AFTER the link, so a failed read cannot prevent preservation — and classify
    # THROUGH THE LINK where one was made, not through report_path (with no link, the else
    # branch below opens the name bound by identity to the recorded inode, and that is the only
    # way it reads it). The two names described the same inode at link
    # time, and the link is the name LESS likely to be replaced under us — not, as this comment
    # said until round twenty-seven, a name nobody else is replacing. The gate landed a rename
    # into the reserved slot between the link and this read and the classification then described
    # the wrong inode; reading through a pathname is not reading through a held descriptor. That
    # WAS the defect (past tense): an os.replace onto report_path between the link and a by-name
    # read left the classification describing a DIFFERENT inode from the one preserved, and a
    # status line verdict then unlinked the findings just kept. An independent review leg
    # supplied that interleaving; the read below is through the descriptor we actually hold.
    _slot_has_another_name = False
    if held_fd is not None:
        # READ THROUGH THE DESCRIPTOR. A held inode cannot be swapped under a read, which is the
        # difference between classifying what we preserved and classifying what someone left at
        # the name. The link count is read here too, from the same descriptor, because releasing
        # a slot is a destructive act and it needs to know whether this is the last name.
        try:
            _prefix = _read_prefix_held(held_fd, held_via_proc, len(_STATUS_LINE_PREFIX))
            is_status_line = _prefix == _STATUS_LINE_PREFIX
            try:
                _slot_has_another_name = os.fstat(held_fd).st_nlink >= 2
            except OSError:
                _slot_has_another_name = False
        except BaseException:
            _close_quietly(held_fd)
            raise
        # The descriptor stays open past this point: the release decision below needs it to
        # confirm the slot NAME still refers to this inode before anything is unlinked.
    else:
        # THE VERDICT COMES FROM THE INODE THAT WAS RECORDED, or there is no verdict. With no
        # slot there is no held copy, and this branch used to open the NAME and read whatever
        # was there. The invariant leg ran it on f153122: findings A at the name, every slot
        # occupied, a status line at the name for exactly the duration of this open, A restored
        # before the descriptor was even returned. The verdict was "status line", the canonical
        # guard saw A back in place and passed, and the replace destroyed A's only name — with
        # no concurrent activity after the swap, so outside the documented check-to-rename
        # interval. The identity-checked helper binds the bytes read to `previous`; a name that
        # has stopped reaching that inode answers None, and with nothing preserved and nothing
        # classifiable the replacement is declined. A FIFO cannot block this: identity is
        # compared before any read, and a FIFO is not the regular inode that was recorded.
        _cfd, _cvia = _open_held_copy(dirfd, report_name, previous)
        if _cfd is None:
            return False
        try:
            # `_read_prefix_held` answers None when it cannot read, and None is not the status
            # prefix: an unreadable copy is classified as findings, the costly case. (An
            # `except OSError` that used to sit here was unreachable — cold leg, d7e4a3c.)
            _prefix = _read_prefix_held(_cfd, _cvia, len(_STATUS_LINE_PREFIX))
            is_status_line = _prefix == _STATUS_LINE_PREFIX
        finally:
            _close_quietly(_cfd)

    if is_status_line:
        # A CLEAN or REFUSED report is not worth a slot, and parking one there was measured
        # blocking a real findings report from ever being kept. Give the slot back — but ONLY
        # while another name still reaches the inode. If this slot is the last one, releasing it
        # destroys the file to reclaim a name, which is the trade round twenty-six refused.
        #
        # AND ONLY WHILE THE SLOT NAME STILL REFERS TO THAT INODE. The link count says the
        # status inode has another name; it says nothing about what the slot name reaches now.
        # The invariant leg swapped the slot for findings report B's last name while the prefix
        # was being read, the count (canonical + alias) was still two, and the slot — B — was
        # unlinked by name. The release goes through the identity-checked helper on the
        # descriptor held since the link, and a slot that is no longer this inode is left.
        try:
            if linked is not None and _slot_has_another_name and held_fd is not None:
                _remove_own_stage(dirfd, linked, held_fd)
        finally:
            if held_fd is not None:
                _close_quietly(held_fd)   # under finally: a cancellation in the cleanup leaked it
        return True
    if held_fd is not None:
        _close_quietly(held_fd)
        held_fd = None

    if linked is not None:
        if narrowed:
            if guard_out is not None:
                guard_out.append(("slot", linked, previous.st_dev, previous.st_ino))
            return True
        # THE POLICY WAS DENIED ON THE INODE WE JUST RESERVED A NAME FOR, so the replacement is
        # refused. Round twenty-three settled the shape for quarantine and preservation was left
        # behind: a reserved name means "retained evidence, carrying the report's access policy",
        # and a successful link was authorizing the caller to replace the canonical report while
        # that retained inode still carried an ACL.
        #
        # THE NAME IS KEPT, and that half was wrong in this round's first shape. It gave the name
        # back too, on the reasoning that a link is a second NAME for the report's own inode and
        # so removing it removes no bytes. That sentence holds only while the canonical name still
        # REACHES that inode, and this module exists because it may stop reaching it at any
        # moment: take the report between the link and the forfeit and the reserved name is the
        # only name left, so giving it back destroys the findings. A review leg aimed at that
        # sentence and the arm reproduces it. POSIX has no way to ask for a name to be removed
        # only if it is not the last one, so a check before the unlink would be a smaller window
        # rather than a closed one.
        #
        # Not removing it costs nothing here, and this is where preservation genuinely differs
        # from quarantine rather than merely lagging it. A quarantined name would survive BESIDE
        # a freshly published report and stand in for it. This one does not: the replacement is
        # declined, so ordinarily the inode goes on standing at the canonical name too, and an
        # ACL on the reserved name is an ACL already on the report itself — not a channel this
        # call opened. After a successful write_report the sweep attempts cleanup of the older
        # entries in both reserved families; newer or unreadable-age entries are left.
        #
        # "ORDINARILY" IS DOING REAL WORK IN THAT SENTENCE, and the round that wrote it said it
        # unconditionally. The gate's own probe removed the canonical entry during preservation
        # and left this reserved link as the SOLE name for the findings. That does not weaken the
        # decision — it is the strongest argument for it, because under that schedule giving the
        # name back is precisely what would destroy them.
        return False

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
            # THROUGH A HELD DESCRIPTOR, like the other branch. Round twenty-eight anchored the
            # fresh-link path and left this one comparing an lstat and then handing the NAME to a
            # helper that opens it again — so the identity test and the narrowing could describe
            # two different files. A leg reproduced it: True returned after stripping and
            # chmodding one inode having checked another. The same defect in a second place, two
            # rounds later; the sibling of a fixed branch is where it goes to live.
            _kept_fd, _kept_via_proc = _open_held_copy(dirfd, candidate, previous)
            if _kept_fd is None:
                return False              # the slot stopped being the inode we just checked
            try:
                if _narrow_held_copy(_kept_fd, _kept_via_proc):
                    if guard_out is not None:
                        guard_out.append(("slot", candidate, previous.st_dev, previous.st_ino))
                    return True           # already preserved by an earlier call; oldest wins
            finally:
                _close_quietly(_kept_fd)
            # AND THE NAME STAYS. This copy is a second name for the SAME inode the report is
            # standing on — the identity check above is what establishes that — so an ACL on it
            # is an ACL already on the report itself, not a channel this call opened. Unlinking a
            # name an earlier call reserved would destroy something to fix nothing. Declining the
            # replacement is the half that matters: the wide inode is not left behind under a
            # reserved name while an owner-only report takes its place.
            return False
    return False                          # findings, and no slot would take them


def _write_refusal_report(staging, refusal):
    """Best-effort: replace an EXISTING report with a single REFUSED line, so that a stale CLEAN
    does not survive beside an rc 2 wherever this function can reach it. A stale CLEAN is
    replaced on every platform; a FINDINGS report is replaced only where its preserved copy's
    access policy can be verified (POSIX ACL xattr API present) — elsewhere it is left standing,
    which is the safe direction and a stated limit.

    NEVER RAISES AN ERROR, and never blocks — the two halves of a contract whose point is that
    this function must not displace the failure it was called to report. The gate asked for the
    boundary to be stated: it does NOT catch KeyboardInterrupt or SystemExit. A cancellation is
    not a refusal to report, and swallowing one would be a different defect. The original refusal
    still propagates. Writes through a temporary file in the real
    <staging>/_reports directory, atomically put in place with os.replace.

    Its guard catches Exception around publication, and classification reads a validated reason
    class rather than rendering the refusal — an earlier revision of this paragraph described an
    OSError/UnicodeError-only guard and said a ValueError out of __str__ would propagate. Both
    stopped being true and the paragraph stood anyway, which is the defect this file keeps
    relearning.

    "Creates nothing when no report exists" stood here and was not quite true; the cold leg
    caught it. The existence test is an os.lstat that does not follow, which a DANGLING SYMLINK
    satisfies — it was os.path.lexists when the note was written — so at
    a report name pointing nowhere this function publishes a regular file holding a REFUSED line
    OVER the link, where write_report would have refused the same state outright. It no longer
    unlinks the link first: round twenty-seven removed that step, because os.replace does not
    follow a symlink at its destination and the preliminary unlink was the one place the canonical
    name could go missing — and, under a concurrent rename, the one place this writer could delete
    a real findings file on the strength of an lstat taken a moment earlier. The two writers still
    disagree about whether a symlink is a report, and this one is still the permissive side; the
    disagreement is now settled by a replace rather than by a delete.

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
    symlinked report FILE is replaced as a directory entry by the refusal report — os.replace
    swaps the entry and never follows it. The preliminary unlink that once left the name missing
    between unlink and replace was removed in round twenty-seven, so "the canonical name is never
    unlinked" is true on both branches. What the function does NOT do is write through a link.

    "NEVER RAISES" IS NOW THE WIDE CLAIM, and the paragraph that stood here said the opposite for
    two rounds after it stopped being true. It said the guard was scoped to OSError and
    UnicodeError, and that an unexpected type — a refusal whose __str__ raised — would propagate
    and replace the refusal being reported. It also recorded a deliberate decision NOT to widen
    the catch, on the grounds that swallowing would hide the defects this file exists to surface.
    That trade WAS later made, by a different round, and nobody came back to this paragraph. The
    gate found the contradiction between it and the code eight lines below it.

    What is true: the guard is `except Exception`, so no ordinary error out of the publication
    body displaces the refusal. KeyboardInterrupt and SystemExit are deliberately not caught. And
    nothing here renders the refusal object, so the question of what its __str__ does no longer
    arises — classification reads a validated reason class the exception carried from its own
    raise site. AttributeError from the POSIX-ACL path on a non-Linux platform is closed at its
    source rather than by this catch, which is unchanged and still the right shape.

    When the replacement cannot be published under the old policy, a private 0600 refusal
    replaces it instead. 0600 is not simply "narrower": against an old 0400 or 0000 report it
    ADDS owner write, while removing group and other access. It is narrower for every reader
    other than the owner, which is the property that matters here, and the earlier blanket
    "never wider" was wrong."""
    # "NEVER RAISES" STARTS AT THE FIRST LINE. os.path.join on a staging value that is not a
    # path raised TypeError before any guard below was reached; a leg traced it in round 27 and
    # the ledger carried it unfixed for five rounds. A refusal writer with nothing to write into
    # returns, as it does for every other unreachable directory.
    try:
        reports_dir = os.path.join(staging, "_reports")
    except (TypeError, ValueError):
        return
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
    # THE SAME RESOLUTION THE PUBLISHER USES, because otherwise the two writers disagree about
    # which tree they are in. O_NOFOLLOW applies to the TRAILING component only — open(2):
    # "Symbolic links in earlier components of the pathname will still be followed" — so naming
    # <staging>/_reports here re-walked a symlinked scan root that write_report had just refused
    # by holding a descriptor, and replaced the report inside its target. Anything the publisher
    # refuses by holding a descriptor, this writer would otherwise still reach by name.
    try:
        parent_fd, _ = _open_dir_nofollow(staging, None)
    except OSError:
        return
    try:
        dirfd = _harden_report_dir(reports_dir, parent_fd=parent_fd)
    except (ScanRefused, OSError):
        return
    finally:
        _close_quietly(parent_fd)
    try:
        _publish_refusal(dirfd, refusal)
    except Exception:
        # THE CONTRACT IS ABSOLUTE, and it was not. This function is called to REPORT a failure
        # and must never displace it, but its inner guard caught only (OSError, UnicodeError) —
        # and classification USED TO call str() on the refusal object, which runs arbitrary code.
        # A cold review leg raised ValueError from an exception's __str__ and watched it escape
        # the one function in this file that is not allowed to raise, taking the original refusal
        # with it. That rendering is gone as of round twenty-three; this catch remains because a
        # publication body can still fail in ordinary ways, and it is the catch, not the removal,
        # that keeps such a failure from displacing the refusal.
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
        # Classified INSIDE the guarded block. The original reason was that str() on a refusal is
        # not guaranteed to succeed; that call no longer exists, and the placement is still right
        # for a plainer one — everything here, classification included, must sit where a failure
        # cannot propagate out of a function whose whole job is to not displace the refusal it was
        # called to report.
        if isinstance(refusal, (OSError, UnicodeError)):
            reason_class = "input-error"
        else:
            # NO ARBITRARY RENDERING HAPPENS HERE. The previous form called str() on the refusal
            # and wrapped it in `except Exception` — which handles a __str__ that RAISES and does
            # nothing at all about one that never returns. The gate supplied exactly that and
            # measured this function waiting on it; catching exceptions cannot interrupt a
            # callback that does not raise, so the guard was aimed at the wrong failure.
            #
            # ScanRefused computes its own class at raise time from a message this file wrote.
            # Here that is one dictionary lookup and a pattern check, with nothing executed on
            # the caller's behalf. The honest residual: a class that overrides __getattribute__
            # can still interfere with the lookup. That is narrower than rendering, and it is not
            # reachable from a directory's contents — main() routes only ScanRefused, OSError and
            # UnicodeError into this function.
            try:
                reason_class = refusal.__dict__.get("reason_class")
            except Exception:
                reason_class = None
            if not isinstance(reason_class, str) or not re.fullmatch(r"[a-z][a-z0-9\-]*", reason_class):
                reason_class = "unclassified"
        # THE PRELIMINARY SYMLINK UNLINK IS GONE, and removing it closes two things at once.
        #
        # It read an lstat and then destroyed the name that lstat described. In a report directory
        # another process can write to, those are two different instants: the gate landed a rename
        # putting a real findings file over the symlink in between, and this writer deleted the
        # findings on the strength of a verdict about something else. Its arm is in GROUP 36.
        #
        # Nothing is lost by dropping it, because os.replace does NOT follow a symlink at its
        # destination — it replaces the name. A symlink at the report name now makes the policy
        # installer refuse (it requires a regular file there) and the publish routes through the
        # fallback below, which replaces the name just the same. The fallback's own comment
        # already noted that this branch was the ONLY reason the canonical name is ever missing;
        # that window is gone with it.
        # Before EITHER publish attempt, so the ordinary path is covered and not just the
        # fallback. A False verdict means the report holds findings that could not be kept, and
        # destroying evidence is worse than leaving a report that says "there are secrets here".
        # The refusal still reaches the caller through the exit code, which is the channel that
        # actually carries it.
        _slot_guard = []
        if not _preserve_superseded(dirfd, _REPORT_NAME, _slot_guard):
            return
        fd, tmp_name = _stage_report(dirfd, f"scan_gate: REFUSED {reason_class}\n")
        try:
            # Same descriptor discipline as write_report: the fd stays open across the policy
            # install so no metadata call here is made on a name that can be swapped, and every
            # name is relative to the directory that was validated.
            # The refusal report is the one a reader needs most, so it gets the same contract.
            _install_posix_acl_policy(dirfd, _REPORT_NAME, fd, tmp_name)
            if not _replace_canonical_guarded(dirfd, tmp_name, fd, _slot_guard):
                _remove_own_stage(dirfd, tmp_name, fd)   # declined: the refusal stage is unneeded
                return
        except BaseException:
            # IDENTITY-CHECKED, not unconditional. The publication path raises precisely when the
            # staged name has stopped being our inode — and this handler then deleted that name
            # anyway, which the gate measured taking a foreign findings file's last link. The
            # cleanup helper removes the stage only if it is still the inode we hold.
            _remove_own_stage(dirfd, tmp_name, fd)
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
        #   "No window exists" is now true of BOTH branches. It used to be true of this one
        #   only, because the symlinked-report branch above unlinked the canonical name before
        #   its replace and a failure in between left the name missing. Round twenty-seven
        #   removed that unlink; a symlink at the report name now routes here and is replaced
        #   atomically like everything else.
        #
        # If even this fails, the old report's BYTES are left as they were. Its metadata may
        # not be: preservation narrows the inode it links — and the preserved copy and the
        # canonical report are one inode — so a report that could not be replaced may already
        # have had its ACL stripped and its mode set to 0600. This function cannot promise a
        # write on a filesystem refusing writes, and it is the EXIT CODE, not the report, that
        # says this scan refused.
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
                # VERIFIED, as the ordinary path's policy install has been since round seventeen.
                # Under a umask that masks owner read the stage is created 0200; an fchmod that
                # returned without effect then published an owner-unreadable refusal (the ledger's
                # oldest open item, from round 23's cold leg). Raising here lands in this
                # fallback's own except, which publishes nothing — the original stays.
                if stat.S_IMODE(os.fstat(fd).st_mode) != _REPORT_MODE:
                    raise OSError(errno.EIO, "report-mode-verification-failed")
                # THE SAME CHECK THE ORDINARY PATH MAKES, twelve lines above, for the reason
                # stated there: renameat would otherwise publish a planted symlink under the
                # canonical name. This is the path that runs when something has already gone
                # wrong, which is the worse place to omit it.
                if not _replace_canonical_guarded(dirfd, tmp_name, fd, _slot_guard):
                    _remove_own_stage(dirfd, tmp_name, fd)
                    return
            except BaseException:
                _remove_own_stage(dirfd, tmp_name, fd)   # identity-checked; see the branch above
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
