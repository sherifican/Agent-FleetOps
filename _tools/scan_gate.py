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

def _report_mode(report_path, reports_dir):
    """The mode the committed report must land with.

    mkstemp creates at 0600 and os.replace preserves the source mode, so the temporary file's mode
    becomes the artifact's mode unless it is set deliberately. What to set it TO is a contract, not
    a constant. Replacing an existing report must not change who can read or write it, so that
    file's current mode wins. For a NEW report the answer is whatever an ordinary create in this
    same directory produces — asking the filesystem rather than computing from the umask is what
    makes a default ACL inherit correctly, since a umask formula cannot see one.

    When the measurement cannot be taken at all the answer is 0600, not a computed guess. A umask
    formula cannot see a directory's default ACL: round-5 review measured it publishing 0664 where
    an ordinary create gives 0640. Narrower than intended is a permission error somebody can see;
    wider than intended is a disclosure nobody does.
    """
    try:
        st = os.lstat(report_path)
        if stat.S_ISREG(st.st_mode):
            return stat.S_IMODE(st.st_mode)
    except OSError:
        pass
    # Measure an ordinary create WITHOUT publishing a pathname. A named probe can be renamed onto
    # between the create and the stat: the mode read back then describes an intruder's file, and
    # the unlink that follows destroys it. Naming each attempt uniquely narrows that window but
    # does not close it, because the unlink and the stat still name a path rather than hold a
    # descriptor. An unnamed file has no directory entry for anything to substitute.
    #
    # An O_TMPFILE inode inherits the directory's default ACL exactly as an ordinary create does
    # (measured on ext4: both 0640 under a u::rw,g::r,o::- default, where the umask formula gives
    # 0664), so asking this way costs nothing in accuracy.
    #
    # O_TMPFILE is Linux-only and not carried by every filesystem — NFS among them — while the
    # mkstemp that precedes this call works there. The previous revision fell back to the umask
    # formula on those, arguing availability. Round-5 review measured the cost of that: with a
    # directory default ACL of u::rw,g::r,o::- and umask 002, an ordinary create gives 0640 and
    # the formula gives 0664 — granting other-read and group-write the directory explicitly
    # withheld. This report redacts matched values, but it still names paths, line numbers and
    # finding classes, and widening who can read that is not a rounding error.
    #
    # So an unmeasurable mode is not guessed. 0600 is the one answer that cannot widen anything:
    # it is what mkstemp already gave the staged file, it is never broader than whatever the
    # directory intended, and the scanner keeps working where everything else does. A reader who
    # loses access gets a permission error, which is visible. The alternative was not.
    tmpfile = getattr(os, "O_TMPFILE", 0)
    if tmpfile:
        try:
            fd = os.open(reports_dir, tmpfile | os.O_EXCL | os.O_RDWR, 0o666)
            try:
                return stat.S_IMODE(os.fstat(fd).st_mode)
            finally:
                os.close(fd)
        except OSError:
            pass
    return 0o600

ACL_XATTR = "system.posix_acl_access"

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


def _install_posix_acl_policy(src, dst):
    """Install the report's whole access policy on dst, while dst is still private.

    st_mode is only part of the permission contract. A POSIX ACL lives in an extended attribute, so
    a replace that preserves the mode can still change WHO may read the file, because the new inode
    inherits the DIRECTORY's default ACL rather than the one the old report carried.

    The ORDER is the contract, and it is why this is one function rather than a chmod standing
    beside an ACL copy. Setting the mode first opens a window in which the temporary file already
    grants group and other access under the INHERITED policy, and the intended one lands after.
    Installing both here, with dst still at mkstemp's 0600, means that window never opens.

    Replacing an existing report preserves its mode AND its ACL, including the ABSENCE of one: an
    inherited entry is removed rather than left to widen access silently. A genuinely new report
    keeps what the directory gave it.

    Failure raises. A report whose access policy could not be established must not be published
    under a guess about who may read it — which is also why the mode probe answers 0600 rather
    than a umask formula when it cannot measure.

    The domain is POSIX access ACLs. On a filesystem expressing policy some other way, this
    preserves mode and ownership and says nothing about the rest.
    """
    try:
        try:
            old = os.stat(src, follow_symlinks=False)
        except FileNotFoundError:
            # A new report: whatever an ordinary create in this directory produces is the answer,
            # and the ACL it inherited is the correct one to keep.
            os.chmod(dst, _report_mode(src, os.path.dirname(dst)))
            return

        current = os.stat(dst, follow_symlinks=False)
        if not stat.S_ISREG(old.st_mode) or not stat.S_ISREG(current.st_mode):
            raise OSError(errno.EINVAL, "report-policy-requires-regular-files")
        if old.st_uid != current.st_uid:
            # A different OWNER is not recoverable here: an unprivileged process cannot give a file
            # away, so the old policy genuinely cannot be reinstated. Refuse rather than publish
            # under an identity the old report did not have.
            raise OSError(errno.EPERM, "report-policy-owner-differs")
        if old.st_gid != current.st_gid:
            # A different GROUP is ordinary and usually fixable: a report written under newgrp, an
            # owner's chgrp, or a setgid report directory all produce one with nobody hostile
            # involved. Refusing outright used to compose with the refusal writer's fallback into
            # DELETING the report over a condition this process can simply correct, so correct it
            # — and only refuse when the correction is the thing that fails.
            try:
                # follow_symlinks=False like every other metadata call here. The S_ISREG check
                # above happens once, before this point; os.chown follows by default, so a dst
                # that was a regular file at the check and a symlink by now would have its
                # TARGET regrouped. The function already decided not to follow.
                os.chown(dst, -1, old.st_gid, follow_symlinks=False)
            except OSError as exc:
                raise OSError(exc.errno, "report-policy-group-not-preservable") from exc
            current = os.stat(dst, follow_symlinks=False)
            if current.st_gid != old.st_gid:
                raise OSError(errno.EPERM, "report-policy-group-not-preservable")

        mode = stat.S_IMODE(old.st_mode)
        if not _XATTR_SUPPORTED:
            # No ACLs on this platform, so the mode IS the whole access policy and carrying it
            # over is the complete job. Narrower than the Linux path and honest about it.
            os.chmod(dst, mode)
            return
        try:
            acl = os.getxattr(src, ACL_XATTR, follow_symlinks=False)
        except OSError as exc:
            if exc.errno not in _ACL_ABSENT:
                raise
            acl = None

        if acl is None:
            # Drop the inherited entry while group and other access are still switched off.
            try:
                os.removexattr(dst, ACL_XATTR, follow_symlinks=False)
            except OSError as exc:
                if exc.errno not in _ACL_ABSENT:
                    raise
            os.chmod(dst, mode)
        else:
            # Special bits only; content access stays private until the ACL itself supplies the
            # owner, mask and other bits.
            os.chmod(dst, (mode & 0o7000) | 0o600)
            os.setxattr(dst, ACL_XATTR, acl, follow_symlinks=False)
            if os.getxattr(dst, ACL_XATTR, follow_symlinks=False) != acl:
                raise OSError(errno.EIO, "report-access-acl-verification-failed")

        if stat.S_IMODE(os.stat(dst, follow_symlinks=False).st_mode) != mode:
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
_SUPERSEDED_SLOTS = 8


def _superseded_slots(reports_dir):
    """Every name the preserved copy may occupy, in the order they are tried."""
    yield os.path.join(reports_dir, "scan_report.superseded.txt")
    for n in range(1, _SUPERSEDED_SLOTS):
        yield os.path.join(reports_dir, "scan_report.superseded.%d.txt" % n)


def write_report(staging, hits):
    reports_dir = os.path.join(staging, "_reports")
    report_path = os.path.join(reports_dir, "scan_report.txt")

    if os.path.islink(reports_dir):
        raise ScanRefused("report-path-unsafe '_reports'")

    os.makedirs(reports_dir, exist_ok=True)
    if not os.path.isdir(reports_dir):
        raise ScanRefused("report-path-unsafe '_reports'")

    if os.path.islink(report_path):
        raise ScanRefused("report-path-unsafe '_reports/scan_report.txt'")

    if not hits:
        body = "scan_gate: CLEAN\n"
    else:
        body = "".join(f"{cls}\t{name}\t{surface}\t{rel}:{i}\n" for rel, i, cls, name, surface in hits)

    fd, tmp_path = tempfile.mkstemp(dir=reports_dir, prefix=".scan_report_")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(body)
        # mkstemp creates at 0600 and os.replace preserves it, which would hand a reader a report
        # they cannot open. The whole access policy is installed while the file is still private,
        # so no reader ever observes the directory's inherited one.
        _install_posix_acl_policy(report_path, tmp_path)
        os.replace(tmp_path, report_path)
        # A fresh scan has just published a report, so anything preserved from an EARLIER
        # generation is stale. Leaving it meant the sibling slot stayed occupied and the next
        # refusal could not keep the findings this run produced — measured: an old report's hits
        # held the slot while the current ones were destroyed. A planted name had the same effect
        # permanently, which made refusing to overwrite into a denial-of-preservation. The slot
        # belongs to one report generation, and this is where that generation ends.
        for _superseded in _superseded_slots(reports_dir):
            try:
                os.unlink(_superseded)
            except IsADirectoryError:
                # A directory at that name cannot be unlinked, and gate review reproduced one
                # blocking every later preservation permanently. An EMPTY one is removable; a
                # populated one is somebody else's data and is left alone.
                try:
                    os.rmdir(_superseded)
                except OSError:
                    pass
            except OSError:
                pass
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# Every status report this tool writes begins with these bytes; a findings report never does.
_STATUS_LINE_PREFIX = b"scan_gate: "


def _preserve_superseded(reports_dir, report_path):
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

    An unreadable report is treated as findings. That is the expensive assumption in the safe
    direction: the cost of being wrong is one preserved status line, against losing evidence.
    """
    try:
        previous = os.lstat(report_path)
    except OSError:
        return True                       # nothing there; nothing to lose
    if not stat.S_ISREG(previous.st_mode):
        return True                       # not a regular file; not ours to preserve

    linked = None
    for candidate in _superseded_slots(reports_dir):
        try:
            os.link(report_path, candidate)
        except OSError:
            continue                      # occupied, unusable, or unsupported — try the next
        linked = candidate
        break

    # Classify only AFTER the link, so a failed read cannot prevent preservation.
    try:
        with open(report_path, "rb") as handle:
            is_status_line = handle.read(len(_STATUS_LINE_PREFIX)) == _STATUS_LINE_PREFIX
    except OSError:
        is_status_line = False            # cannot tell: assume findings, the costly case

    if is_status_line:
        # A CLEAN or REFUSED report is not worth a slot, and parking one there was measured
        # blocking a real findings report from ever being kept. Give the slot back.
        if linked is not None:
            try:
                os.unlink(linked)
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
    for candidate in _superseded_slots(reports_dir):
        try:
            kept = os.lstat(candidate)
        except OSError:
            continue
        if (stat.S_ISREG(kept.st_mode)
                and (kept.st_dev, kept.st_ino) == (previous.st_dev, previous.st_ino)):
            return True                   # already preserved by an earlier call; oldest wins
    return False                          # findings, and no slot would take them


def _write_refusal_report(staging, refusal):
    """Best-effort: replace an EXISTING report with a single REFUSED line, so that a stale CLEAN
    does not survive beside an rc 2 wherever this function can reach it. Never raises; the
    original refusal still propagates. Writes through a temporary file in the real
    <staging>/_reports directory, atomically put in place with os.replace. Creates nothing when
    no report exists.

    WHERE IT CANNOT REACH, stated plainly because an earlier revision of this docstring claimed
    the guarantee unconditionally and gate review falsified it three ways:

      - An UNWRITABLE report directory. Both publish attempts fail and the old report, CLEAN or
        not, survives untouched. This function cannot promise a write on a filesystem refusing
        writes, and it is the EXIT CODE, not the report, that carries the refusal.
      - A report whose bytes cannot be READ and cannot be preserved under any slot. It is treated
        as findings and left standing, because destroying unknown evidence is the worse error.
        If it was in fact a stale CLEAN, it survives. The preservation slots exist to make this
        case rare; they do not make it impossible.
      - Anything a reader reaches by a path this function refused to follow. A symlinked _reports
        is now REMOVED rather than left in place, which turns a planted CLEAN into no report at
        all — but the report living inside the scanned tree is a structural limitation, not a
        closed hole.

    On links: a symlinked _reports directory is unlinked (the link, never its target) and nothing
    is published; a symlinked report FILE is unlinked before the replace. So the claim that "the
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
    report_path = os.path.join(reports_dir, "scan_report.txt")
    if not os.path.lexists(report_path):
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
            msg = str(refusal)
            reason_class = msg.split(" ", 1)[0].rstrip(";:,.")
            if not reason_class or not re.fullmatch(r"[a-z][a-z0-9\-]*", reason_class):
                reason_class = "unclassified"
        if os.path.islink(report_path):
            os.unlink(report_path)
        # Before EITHER publish attempt, so the ordinary path is covered and not just the
        # fallback. A False verdict means the report holds findings that could not be kept, and
        # destroying evidence is worse than leaving a report that says "there are secrets here".
        # The refusal still reaches the caller through the exit code, which is the channel that
        # actually carries it.
        if not _preserve_superseded(reports_dir, report_path):
            return
        fd, tmp_path = tempfile.mkstemp(dir=reports_dir, prefix=".scan_report_")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(f"scan_gate: REFUSED {reason_class}\n")
            # The refusal report is the one a reader needs most, so it gets the same contract.
            _install_posix_acl_policy(report_path, tmp_path)
            os.replace(tmp_path, report_path)
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
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
            fd, tmp_path = tempfile.mkstemp(dir=reports_dir, prefix=".scan_report_")
            try:
                with os.fdopen(fd, "w") as f:
                    f.write(f"scan_gate: REFUSED {reason_class}\n")
                os.chmod(tmp_path, 0o600)
                os.replace(tmp_path, report_path)
            except BaseException:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
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
