"""Installation gate for guard/hooks/install.sh — the boundary BETWEEN the tracked hook and the hook
Git actually runs.

The hook tests beside this file drive guard/hooks/pre-push directly, so they can pass while a clone
runs no hook at all, an older hook, or a hook copied to a directory Git never consults. This file
pins the installer's contract (design §8) as behaviour, not syntax:

  --pre-push-config          install ONLY the tracked pre-push at Git's effective hook path
                             (git rev-parse --path-format=absolute --git-path hooks/pre-push),
                             after an identity-source + scanner preflight, with a checked parity
                             (sha256sum on both files AND cmp -s) and an executable result.
  --check-pre-push-config    the same preflight plus parity, READ-ONLY: it writes nothing, ever.
  --replace                  with --pre-push-config: a differing pre-existing hook is backed up
                             (bytes + mode) to a path printed as `backup: <abs path>` and only then
                             replaced; without it the differing hook is preserved and the run refuses.
  no arguments               the legacy all-hooks route, preserved.
  anything else              usage error, exit 2, nothing written.

Exit codes pinned here: 0 success · 1 every designed refusal · 2 usage. A refusal never prints a
stdout line beginning with the word `installed`; a success prints one containing the absolute
effective path. Refusals name the missing ACTION with one of a few fixed tokens (see each test) and
never echo an approved identity.

Every test builds its own repository under tmp_path, copies the SHIPPING installer, hook and scanner
into it with a synthetic nonempty identity-terms file, and runs the installer under a scrubbed
environment (HOME=tmp, GIT_CONFIG_NOSYSTEM=1, GIT_CONFIG_GLOBAL=/dev/null, every inherited GIT_*
removed, LC_ALL=C). No real destination, no real identity: the repo-local identity is assembled at
run time so this file's own bytes carry no contiguous identity or key-shaped literal.

Each docstring below says which assertions are PRESERVATION controls (must pass on the old,
argument-blind installer too) and which are REPAIRED behaviour (must FAIL on it). The old installer
ignores its arguments and installs every hook into a hardcoded .git/hooks with no parity check, so
its RED here is behavioural — a sentinel overwritten, a corrupt copy reported as installed, a
symlink replaced — never merely "unknown flag".
"""
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
INSTALLER_SRC = os.path.join(REPO, "guard", "hooks", "install.sh")
HOOK_SRC = os.path.join(REPO, "guard", "hooks", "pre-push")
SCAN_GATE_SRC = os.path.join(REPO, "_tools", "scan_gate.py")
GITIGNORE_SRC = os.path.join(REPO, ".gitignore")

# Assembled at run time: never a contiguous identity literal in tracked bytes.
IDENTITY_A = "fixture" + "@" + "example" + ".invalid"
IDENTITY_B = "outsider" + "@" + "example" + ".invalid"
IDENTITY_TERM = "fixture" + "person"
ZERO = "0" * 40

EXIT_OK, EXIT_REFUSED, EXIT_USAGE = 0, 1, 2

SENTINEL_HOOK = "#!/bin/sh\n# tracked sentinel hook (fixture)\nexit 0\n"
PREEXISTING_SENTINEL = "#!/bin/sh\n# pre-existing hook that the pre-push-only route must never touch\nexit 0\n"
OLD_HOOK = "#!/bin/sh\n# an earlier, different pre-push\nexit 0\n"
OLD_MODE = 0o750

# Exact tokens the contract requires in a refusal (stdout+stderr searched together).
TOKEN_ENV_SHADOW = "FLEETOPS_APPROVED_IDENTITIES"
TOKEN_FILE_SHADOW = "approved_identities.txt"
TOKEN_CONFIG = "fleetops.approvedIdentity"
TOKEN_TERMS = "identity_terms.txt"
TOKEN_SELFTEST = "self-test"
TOKEN_PARITY = "parity"
TOKEN_TRACKED = "guard/hooks/pre-push"
TOKEN_REPLACE = "--replace"
TOKEN_SYMLINK = "symlink"
TOKEN_USAGE = "usage"
TOKEN_SOURCE = "git-config"
BACKUP_LINE = re.compile(r"^backup: (.+)$", re.M)
SUCCESS_LINE = re.compile(r"^installed\b.*$", re.M)


def sha256(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def mode_of(path):
    return stat.S_IMODE(os.lstat(path).st_mode)


def combined(r):
    return r.stdout + r.stderr


def success_lines(r):
    return SUCCESS_LINE.findall(r.stdout)


def assert_no_identity_printed(r):
    out = combined(r)
    assert IDENTITY_A not in out and IDENTITY_B not in out, (
        "the installer must never echo an approved identity (policy values stay out of output): "
        + out)


def assert_refused(r, token, code=EXIT_REFUSED):
    assert r.returncode == code, f"expected exit {code}, got {r.returncode}\n{combined(r)}"
    assert not success_lines(r), "a refusal must not print a success line:\n" + r.stdout
    assert token in combined(r), f"refusal must name the action via {token!r}:\n{combined(r)}"
    assert_no_identity_printed(r)


class Fixture:
    """A throwaway repository carrying the shipping installer, hook and scanner."""

    def __init__(self, tmp_path, name="repo"):
        self.tmp = tmp_path
        self.home = tmp_path / "home"
        self.home.mkdir(exist_ok=True)
        self.tmpdir = tmp_path / "tmpdir"  # TMPDIR for every subprocess: mktemp cannot leave tmp_path
        self.tmpdir.mkdir(exist_ok=True)
        self.repo = tmp_path / name

    # -- environment ---------------------------------------------------------------------------
    def env(self, extra=None, path_prefix=None):
        e = {k: v for k, v in os.environ.items()
             if not k.startswith("GIT_") and k != TOKEN_ENV_SHADOW}
        e.update({"HOME": str(self.home), "GIT_CONFIG_NOSYSTEM": "1",
                  "GIT_CONFIG_GLOBAL": "/dev/null", "LC_ALL": "C",
                  "TMPDIR": str(self.tmpdir), "PYTHONDONTWRITEBYTECODE": "1"})
        if path_prefix:
            e["PATH"] = str(path_prefix) + os.pathsep + e.get("PATH", "")
        e.update(extra or {})
        return e

    def git(self, *args, cwd=None, check=True, extra=None):
        return subprocess.run(["git", "-C", str(cwd or self.repo)] + list(args),
                              capture_output=True, text=True, check=check, env=self.env(extra))

    def installer(self, *args, cwd=None, extra=None, path_prefix=None, stdin=""):
        cwd = cwd or self.repo
        return subprocess.run(["bash", str(cwd / "guard" / "hooks" / "install.sh")] + list(args),
                              cwd=str(cwd), input=stdin, capture_output=True, text=True,
                              env=self.env(extra, path_prefix))

    def effective_hook(self, cwd=None):
        out = self.git("rev-parse", "--path-format=absolute", "--git-path", "hooks/pre-push",
                       cwd=cwd).stdout.strip()
        assert os.path.isabs(out), out
        return out

    def config_path(self, cwd=None):
        return self.git("rev-parse", "--path-format=absolute", "--git-path", "config",
                        cwd=cwd).stdout.strip()

    def tracked_hook(self, cwd=None):
        return str((cwd or self.repo) / "guard" / "hooks" / "pre-push")

    # -- state ---------------------------------------------------------------------------------
    def snapshot(self, cwd=None):
        """bytes+mode of everything the installer could touch: the effective hooks directory,
        the repo config, the tracked hook, the policy inputs, and core.hooksPath."""
        hooks_dir = os.path.dirname(self.effective_hook(cwd))
        state = {}
        if os.path.isdir(hooks_dir):
            for name in sorted(os.listdir(hooks_dir)):
                p = os.path.join(hooks_dir, name)
                st = os.lstat(p)
                body = os.readlink(p) if stat.S_ISLNK(st.st_mode) else (
                    open(p, "rb").read() if stat.S_ISREG(st.st_mode) else None)
                state["hooks/" + name] = (body, stat.S_IMODE(st.st_mode))
        else:
            state["hooks-dir"] = "ABSENT"
        for label, p in (("config", self.config_path(cwd)),
                         ("tracked", self.tracked_hook(cwd)),
                         ("terms", str((cwd or self.repo) / "_tools" / "identity_terms.txt")),
                         ("default-policy",
                          str((cwd or self.repo) / "_tools" / "approved_identities.txt"))):
            if os.path.lexists(p):
                st = os.lstat(p)
                body = os.readlink(p) if stat.S_ISLNK(st.st_mode) else open(p, "rb").read()
                state[label] = (body, stat.S_IMODE(st.st_mode))
            else:
                state[label] = "ABSENT"
        state["core.hooksPath"] = self.git("config", "--get", "core.hooksPath",
                                           cwd=cwd, check=False).stdout
        state["local-config"] = self.git("config", "--local", "--list", cwd=cwd).stdout
        return state

    def install_by_hand(self, cwd=None):
        """A valid installation made WITHOUT the installer, so check-mode measurements do not
        depend on the install route under test."""
        dest = self.effective_hook(cwd)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copyfile(self.tracked_hook(cwd), dest)
        os.chmod(dest, 0o755)
        return dest


def make_fixture(tmp_path, name="repo", identity_terms=IDENTITY_TERM + "\n",
                 config_identity=IDENTITY_A):
    fx = Fixture(tmp_path, name)
    repo = fx.repo
    (repo / "guard" / "hooks").mkdir(parents=True)
    (repo / "_tools").mkdir()
    shutil.copy(INSTALLER_SRC, repo / "guard" / "hooks" / "install.sh")
    shutil.copy(HOOK_SRC, repo / "guard" / "hooks" / "pre-push")
    (repo / "guard" / "hooks" / "commit-msg").write_text(SENTINEL_HOOK)
    for name in ("install.sh", "pre-push", "commit-msg"):
        os.chmod(repo / "guard" / "hooks" / name, 0o755)
    shutil.copy(SCAN_GATE_SRC, repo / "_tools" / "scan_gate.py")
    (repo / "README.md").write_text("fixture repository; nothing private here\n")
    # The real ignore rules plus the two private inputs, so "publishable" means what it means
    # in the shipping repository even on a parent that lacks the C4 row.
    (repo / ".gitignore").write_text(open(GITIGNORE_SRC).read()
                                     + "\n_tools/identity_terms.txt\n_tools/approved_identities.txt\n")
    fx.git("init", "-q", "-b", "main")
    fx.git("config", "user.name", "fixture")
    fx.git("config", "user.email", IDENTITY_A)
    if config_identity is not None:
        fx.git("config", "--local", "fleetops.approvedIdentity", config_identity)
    fx.git("add", "-A")
    fx.git("commit", "-q", "--no-verify", "-m", "base")
    if identity_terms is not None:
        (repo / "_tools" / "identity_terms.txt").write_text(identity_terms)
    return fx


def plant_preexisting_sentinel(fx):
    """A hook Git would run that is NOT the tracked pre-push: the pre-push-only route must leave it
    byte-for-byte alone (the legacy route may overwrite it, by design)."""
    hooks_dir = os.path.dirname(fx.effective_hook())
    os.makedirs(hooks_dir, exist_ok=True)
    p = os.path.join(hooks_dir, "commit-msg")
    open(p, "w").write(PREEXISTING_SENTINEL)
    os.chmod(p, 0o755)
    return p


# -- injected failures ---------------------------------------------------------------------------

def _shim_dir(tmp_path):
    d = tmp_path / "shims"
    d.mkdir(exist_ok=True)
    return d


def corrupting_copy_shims(tmp_path):
    """`install` and `cp` shims that land guard/hooks/pre-push at its destination with the first
    byte altered and delegate every other copy (a backup, say) to the real tool. Whatever the
    installer trusts after this copy is what a parity check must refute."""
    d = _shim_dir(tmp_path)
    for tool in ("install", "cp"):
        real = shutil.which(tool)
        assert real, tool
        body = f'''#!/usr/bin/env python3
import os, sys
args = sys.argv[1:]
pos, i = [], 0
while i < len(args):
    a = args[i]
    if a in ("-m", "-o", "-g", "-t", "--mode", "--owner", "--group", "--target-directory"):
        i += 2
        continue
    if a.startswith("-"):
        i += 1
        continue
    pos.append(a)
    i += 1
if len(pos) < 2 or not pos[-2].replace(os.sep, "/").endswith("guard/hooks/pre-push"):
    os.execv({real!r}, [{real!r}] + args)
src, dst = pos[-2], pos[-1]
if os.path.isdir(dst):
    dst = os.path.join(dst, os.path.basename(src))
data = bytearray(open(src, "rb").read())
data[0] = (data[0] + 1) % 256
with open(dst, "wb") as f:
    f.write(bytes(data))
os.chmod(dst, 0o755)
sys.stderr.write("injected: corrupt copy of pre-push\\n")
'''
        p = d / tool
        p.write_text(body)
        os.chmod(p, 0o755)
    return d


def failing_cmp_shim(tmp_path):
    d = _shim_dir(tmp_path)
    p = d / "cmp"
    p.write_text("#!/bin/sh\necho 'injected: cmp reports a difference' >&2\nexit 1\n")
    os.chmod(p, 0o755)
    return d


FAILING_SCANNER = "#!/usr/bin/env python3\nimport sys\nsys.exit(1)\n"


def witness_scanner(witness_path):
    return f'''#!/usr/bin/env python3
import json, sys
data = sys.stdin.read()
with open({str(witness_path)!r}, "a") as f:
    f.write(json.dumps({{"argv": sys.argv[1:], "stdin": data}}) + "\\n")
sys.exit(0)
'''


def force_unreadable(fx, path):
    """A deterministic read failure for the hook's `sed` over the selected policy file. chmod 000
    is the honest instrument for an unprivileged run; root reads mode-000 files, so there the
    failure is injected at the read itself via a PATH `sed` shim. Returns (path_prefix, label)."""
    if os.geteuid() != 0:
        os.chmod(path, 0)
        return None, "chmod 000"
    d = _shim_dir(fx.tmp)
    p = d / "sed"
    p.write_text("#!/bin/sh\necho 'injected: policy file unreadable' >&2\nexit 1\n")
    os.chmod(p, 0o755)
    return d, "sed shim (euid 0 defeats chmod)"


# ================================================================================================
# 1. install only pre-push, byte-equal, executable, parity-checked
# ================================================================================================

@pytest.mark.parametrize("case", ["legacy-no-args", "fresh", "reinstall-identical",
                                  "fresh-corrupt-copy", "symlink-destination", "unknown-argument"])
def test_install_pre_push_and_parity(tmp_path, case):
    """Guards against an installer that ignores its arguments and installs EVERY hook, that trusts
    its own copy without a checked parity, that replaces a symlink, or that accepts unknown flags.

    Preservation control (GREEN on the old installer): `legacy-no-args` — no arguments still
    installs all tracked hooks, executable, with an `installed` line.
    Repaired behaviour (RED on the old installer, on exact behaviour): `fresh` and
    `reinstall-identical` — the pre-existing commit-msg sentinel is left byte-identical and only
    pre-push is added; `fresh-corrupt-copy` — a copy that lands corrupted is REFUSED (nonzero,
    no success line, nothing left at the effective path) instead of reported as installed;
    `symlink-destination` — a symlinked destination is refused and left in place, even with
    --replace; `unknown-argument` — exit 2, nothing written.
    Mutants killed: M-NO-COPY (fresh: hook absent), M-WRONG-HOOK (fresh: bytes differ from the
    tracked pre-push), M-NO-CMP (fresh-corrupt-copy: corrupt copy reported installed),
    M-INSTALL-ALL (fresh/reinstall: sentinel overwritten).
    """
    fx = make_fixture(tmp_path)
    sentinel = plant_preexisting_sentinel(fx)
    dest = fx.effective_hook()
    hooks_dir = os.path.dirname(dest)
    tracked_hash = sha256(fx.tracked_hook())

    if case == "legacy-no-args":
        r = fx.installer()
        assert r.returncode == EXIT_OK, combined(r)
        for name in ("pre-push", "commit-msg"):
            p = os.path.join(hooks_dir, name)
            assert os.path.isfile(p) and os.access(p, os.X_OK), name
            assert sha256(p) == sha256(str(fx.repo / "guard" / "hooks" / name)), name
        assert "installed" in r.stdout, r.stdout
        assert "install.sh" not in os.listdir(hooks_dir), "the installer must not install itself"
        return

    if case == "unknown-argument":
        before = fx.snapshot()
        r = fx.installer("--bogus-flag")
        assert fx.snapshot() == before, "an unknown argument must write nothing:\n" + combined(r)
        assert_refused(r, TOKEN_USAGE, code=EXIT_USAGE)
        return

    if case == "symlink-destination":
        # Measured 2026-09-05 (git 2.53): `git rev-parse --path-format=absolute --git-path
        # hooks/pre-push` FOLLOWS a symlink and returns its target, so `-L` on that output can
        # never see the link. The relative form (`--git-path hooks/pre-push`, cwd-relative) does
        # not resolve; the symlink test must be made on `$root/<relative form>` (or the form as-is
        # when it is already absolute). `dest` below was resolved BEFORE the link was planted, so
        # it is the unresolved hooks-directory path the refusal must report.
        target = tmp_path / "elsewhere" / "pre-push"
        target.parent.mkdir()
        target.write_text(OLD_HOOK)
        os.symlink(str(target), dest)
        r = fx.installer("--pre-push-config", "--replace")
        assert os.path.islink(dest) and os.readlink(dest) == str(target), (
            "a symlinked destination must be left in place, not replaced:\n" + combined(r))
        assert target.read_text() == OLD_HOOK, "the symlink target must be untouched"
        assert_refused(r, TOKEN_SYMLINK)
        assert dest in combined(r), "the refusal must report the destination path:\n" + combined(r)
        return

    if case == "reinstall-identical":
        fx.install_by_hand()

    if case == "fresh-corrupt-copy":
        r = fx.installer("--pre-push-config", path_prefix=corrupting_copy_shims(tmp_path))
        assert not os.path.lexists(dest), (
            "a copy that failed parity must not be left at the effective path:\n" + combined(r))
        assert r.returncode != EXIT_OK, "a corrupt copy must not be reported as installed:\n" + combined(r)
        assert not success_lines(r), r.stdout
        assert TOKEN_PARITY in combined(r), combined(r)
        assert open(sentinel).read() == PREEXISTING_SENTINEL, "rollback must remove only its own file"
        assert_no_identity_printed(r)
        return

    listing_before = sorted(os.listdir(hooks_dir))
    r = fx.installer("--pre-push-config")
    assert open(sentinel).read() == PREEXISTING_SENTINEL and mode_of(sentinel) == 0o755, (
        "the pre-push-only route must leave every other hook byte-identical:\n" + combined(r))
    assert sorted(os.listdir(hooks_dir)) == sorted(set(listing_before) | {"pre-push"}), (
        "only pre-push may be added to the hooks directory")
    assert os.path.isfile(dest) and not os.path.islink(dest), dest
    assert sha256(dest) == tracked_hash, "installed bytes must equal the tracked pre-push"
    assert os.access(dest, os.X_OK) and mode_of(dest) & 0o111 == 0o111, "installed hook must be executable"
    assert r.returncode == EXIT_OK, combined(r)
    lines = success_lines(r)
    assert lines and any(dest in ln for ln in lines), (
        "success must print an `installed` line naming the absolute effective path:\n" + r.stdout)
    assert TOKEN_SOURCE in r.stdout, "success must name the selected identity source category"
    assert_no_identity_printed(r)
    # The read-only check must agree with the installation it just made.
    c = fx.installer("--check-pre-push-config")
    assert c.returncode == EXIT_OK, combined(c)


# ================================================================================================
# 2. Git's effective hook path, not a hardcoded .git/hooks
# ================================================================================================

@pytest.mark.parametrize("layout", ["standard", "linked-worktree", "hookspath-relative",
                                    "hookspath-absolute"])
def test_effective_hook_path(tmp_path, layout):
    """Guards against installing into a hardcoded `.git/hooks` that Git will not consult (a
    `core.hooksPath` repository) or cannot hold (a linked worktree, whose `.git` is a file).

    Preservation control (GREEN on the old installer): `standard` — the default path is
    `.git/hooks/pre-push`, the old installer lands there too, and this case asserts only that.
    Repaired behaviour (RED on the old installer): the three nonstandard layouts — the file Git
    resolves via `git rev-parse --git-path hooks/pre-push` must carry the tracked bytes, be
    executable, `core.hooksPath` must be left exactly as found, no stray copy may appear at the
    hardcoded path, the success line must print that path, and the read-only check must agree.
    Mutants killed: M-HARDCODE-GIT-DIR (worktree: `.git` is a file; hooksPath: wrong directory),
    M-IGNORE-HOOKSPATH (relative/absolute: resolved path empty).
    """
    fx = make_fixture(tmp_path)
    cwd = fx.repo
    if layout == "linked-worktree":
        cwd = tmp_path / "linked"
        fx.git("worktree", "add", "-q", str(cwd))
        assert os.path.isfile(cwd / ".git"), "a linked worktree's .git is a FILE"
        (cwd / "_tools" / "identity_terms.txt").write_text(IDENTITY_TERM + "\n")
    elif layout == "hookspath-relative":
        fx.git("config", "core.hooksPath", "custom-hooks")
    elif layout == "hookspath-absolute":
        fx.git("config", "core.hooksPath", str(tmp_path / "abs-hooks"))

    hookspath_before = fx.git("config", "--get", "core.hooksPath", cwd=cwd, check=False).stdout
    dest = fx.effective_hook(cwd)
    if layout == "standard":
        assert dest == str(fx.repo / ".git" / "hooks" / "pre-push")
    elif layout == "linked-worktree":
        assert dest == str(fx.repo / ".git" / "hooks" / "pre-push"), dest
    elif layout == "hookspath-relative":
        assert dest == str(fx.repo / "custom-hooks" / "pre-push"), dest
    else:
        assert dest == str(tmp_path / "abs-hooks" / "pre-push"), dest

    r = fx.installer("--pre-push-config", cwd=cwd)
    assert os.path.isfile(dest), (
        f"Git's resolved hook path {dest} must hold the installed hook:\n" + combined(r))
    assert sha256(dest) == sha256(fx.tracked_hook(cwd)), "resolved path must carry the tracked bytes"
    assert os.access(dest, os.X_OK), "installed hook must be executable"
    assert r.returncode == EXIT_OK, combined(r)
    assert fx.git("config", "--get", "core.hooksPath", cwd=cwd, check=False).stdout == hookspath_before, (
        "the installer must never edit core.hooksPath")
    if layout.startswith("hookspath"):
        assert not os.path.lexists(fx.repo / ".git" / "hooks" / "pre-push"), (
            "no stray copy at the hardcoded path Git will not run")
    if layout == "linked-worktree":
        assert os.path.isfile(cwd / ".git"), "the worktree's .git file must survive"
    if layout == "standard":
        return  # pure preservation control: the output/check contract for this layout is pinned
                # by test_install_pre_push_and_parity[fresh], so the old installer stays GREEN here
    assert dest in r.stdout, "success must print the effective path:\n" + r.stdout
    c = fx.installer("--check-pre-push-config", cwd=cwd)
    assert c.returncode == EXIT_OK, combined(c)


# ================================================================================================
# 3. the check is a check: it refuses drift and writes nothing
# ================================================================================================

@pytest.mark.parametrize("case", ["valid", "one-byte-drift", "installed-missing",
                                  "installed-nonexecutable", "tracked-source-missing"])
def test_parity_check_is_read_only(tmp_path, case):
    """Guards against a check that prints two hashes and exits 0 without comparing them, and
    against a check that silently REPAIRS the installed hook (a check that changes what it
    measures cannot report the state the adopter had).

    Preservation control: none — the old installer has no check mode; every case is RED on it
    because it installs (writes) and exits 0 regardless of drift.
    Repaired behaviour: `valid` exits 0 with the effective path printed; each other case exits 1
    naming `parity` (or the tracked path when the source is missing); in every case the hooks
    directory, config, tracked hook and policy inputs are byte-identical before and after.
    Mutants killed: M-PRINT-HASHES-ONLY (drift/nonexecutable exit 0), M-CHECK-AUTOREPAIR
    (snapshot differs after the check).
    """
    fx = make_fixture(tmp_path)
    dest = fx.install_by_hand()
    token = TOKEN_PARITY
    if case == "one-byte-drift":
        data = bytearray(open(dest, "rb").read())
        data[-1] = (data[-1] + 1) % 256
        open(dest, "wb").write(bytes(data))
    elif case == "installed-missing":
        os.unlink(dest)
    elif case == "installed-nonexecutable":
        os.chmod(dest, 0o644)
    elif case == "tracked-source-missing":
        os.unlink(fx.tracked_hook())
        token = TOKEN_TRACKED

    before = fx.snapshot()
    r = fx.installer("--check-pre-push-config")
    assert fx.snapshot() == before, "the check must change nothing on disk:\n" + combined(r)
    if case == "valid":
        assert r.returncode == EXIT_OK, combined(r)
        assert dest in r.stdout and TOKEN_SOURCE in r.stdout, r.stdout
        assert_no_identity_printed(r)
    else:
        assert_refused(r, token)


# ================================================================================================
# 4. config-source activation refuses every shadowing policy source
# ================================================================================================

SHADOW_CASES = ["env-file-b", "env-file-a", "env-nonexistent", "env-unreadable",
                "default-file-b", "default-empty", "default-comment-only",
                "default-dangling-symlink"]


def plant_shadow(fx, case):
    """Plant one conflict; return (env extra, remover) where remover deletes ONLY the conflict."""
    default = fx.repo / "_tools" / "approved_identities.txt"
    outside = fx.tmp / "outside-policy.txt"
    if case.startswith("env"):
        if case == "env-file-b":
            outside.write_text(IDENTITY_B + "\n")
        elif case == "env-file-a":
            outside.write_text(IDENTITY_A + "\n")
        elif case == "env-unreadable":
            outside.write_text(IDENTITY_B + "\n")
            os.chmod(outside, 0)
        return {TOKEN_ENV_SHADOW: str(outside)}, (lambda: None), TOKEN_ENV_SHADOW
    if case == "default-file-b":
        default.write_text(IDENTITY_B + "\n")
    elif case == "default-empty":
        default.write_text("")
    elif case == "default-comment-only":
        default.write_text("# nobody approved here\n   \n")
    elif case == "default-dangling-symlink":
        os.symlink(str(fx.tmp / "does-not-exist"), default)
    return {}, (lambda: os.unlink(default)), TOKEN_FILE_SHADOW


@pytest.mark.parametrize("case", SHADOW_CASES)
@pytest.mark.parametrize("mode", ["--check-pre-push-config", "--pre-push-config"])
def test_config_source_shadow_refused(tmp_path, mode, case):
    """Guards against an activation that reports the git-config identity as the policy while the
    hook would actually read a file: a nonempty FLEETOPS_APPROVED_IDENTITIES or an EXISTING
    default `_tools/approved_identities.txt` (empty, comment-only, dangling symlink included)
    shadows the configured list, so the config route must refuse until that conflict is gone.

    Preservation control: the clean run after the conflict is removed exits 0 (the old installer
    also exits 0 there, but by installing, which is not a check).
    Repaired behaviour (RED on the old installer): every planted conflict exits 1 naming the
    conflicting source (`FLEETOPS_APPROVED_IDENTITIES` or `approved_identities.txt`), prints no
    identity, writes nothing (check mode) or installs nothing (install mode); removing only the
    planted conflict restores acceptance.
    Mutants killed: M-NO-SHADOW-CHECK (every case exits 0), M-ONLY-CHECK-DEFAULT (env cases
    exit 0).
    """
    fx = make_fixture(tmp_path)
    dest = fx.effective_hook()
    if mode == "--check-pre-push-config":
        fx.install_by_hand()
    extra, remove_conflict, token = plant_shadow(fx, case)
    before = fx.snapshot()

    r = fx.installer(mode, extra=extra)
    if mode == "--check-pre-push-config":
        assert fx.snapshot() == before, "a shadowed check must change nothing on disk:\n" + combined(r)
    else:
        assert not os.path.lexists(dest), "a shadowed activation must install nothing:\n" + combined(r)
    assert_refused(r, token)
    if case.startswith("default"):
        assert os.path.lexists(fx.repo / "_tools" / "approved_identities.txt"), (
            "the refusal must not delete the planted default file")

    remove_conflict()
    ok = fx.installer(mode)
    assert ok.returncode == EXIT_OK, (
        "removing only the planted conflict must restore acceptance:\n" + combined(ok))
    assert os.path.isfile(dest) and sha256(dest) == sha256(fx.tracked_hook())
    assert_no_identity_printed(ok)


# ================================================================================================
# 5. each required input is required on its own
# ================================================================================================

MISSING_CASES = ["valid", "config-missing", "config-blank", "config-inherited-global-only",
                 "config-inherited-global-extra", "terms-missing", "terms-empty",
                 "scanner-selftest-fails", "scanner-stdin-disconnected"]


@pytest.mark.parametrize("case", MISSING_CASES)
@pytest.mark.parametrize("mode", ["--check-pre-push-config", "--pre-push-config"])
def test_missing_inputs_refused(tmp_path, mode, case):
    """Guards against an activation that skips the policy check (no configured identity, a blank
    one, or one inherited from outside the repository) or skips the scanner self-test (missing or
    empty identity terms, a scanner whose self-test fails, or a self-test handed the hook's stdin).

    Every case has all OTHER inputs valid and (check mode) a valid installed hook, so the refusal
    observed is the one named and not some other missing prerequisite.
    Preservation control: `valid` exits 0 (the old installer exits 0 by installing).
    Repaired behaviour (RED on the old installer): each refusal exits 1 with the token of the
    missing action — `fleetops.approvedIdentity`, `identity_terms.txt`, `self-test` — without
    echoing an identity, and installs/changes nothing. `scanner-stdin-disconnected` proves the
    self-test was actually invoked (`--self-test` in the witness) with stdin at /dev/null while the
    installer's own stdin carried data.
    Mutants killed: M-SKIP-POLICY-CHECK (config-* exit 0), M-SKIP-SCANNER-SELFTEST
    (scanner-selftest-fails exits 0; witness absent).
    """
    fx = make_fixture(tmp_path)
    dest = fx.effective_hook()
    if mode == "--check-pre-push-config":
        fx.install_by_hand()
    extra, token, witness = {}, None, None
    scanner = fx.repo / "_tools" / "scan_gate.py"
    terms = fx.repo / "_tools" / "identity_terms.txt"
    global_cfg = tmp_path / "inherited-gitconfig"

    if case == "config-missing":
        fx.git("config", "--unset-all", "fleetops.approvedIdentity")
        token = TOKEN_CONFIG
    elif case == "config-blank":
        fx.git("config", "--replace-all", "fleetops.approvedIdentity", " ")
        token = TOKEN_CONFIG
    elif case == "config-inherited-global-only":
        fx.git("config", "--unset-all", "fleetops.approvedIdentity")
        global_cfg.write_text("[fleetops]\n\tapprovedIdentity = " + IDENTITY_A + "\n")
        extra = {"GIT_CONFIG_GLOBAL": str(global_cfg)}
        token = TOKEN_CONFIG
    elif case == "config-inherited-global-extra":
        global_cfg.write_text("[fleetops]\n\tapprovedIdentity = " + IDENTITY_B + "\n")
        extra = {"GIT_CONFIG_GLOBAL": str(global_cfg)}
        token = TOKEN_CONFIG
    elif case == "terms-missing":
        os.unlink(terms)
        token = TOKEN_TERMS
    elif case == "terms-empty":
        terms.write_text("")
        token = TOKEN_TERMS
    elif case == "scanner-selftest-fails":
        scanner.write_text(FAILING_SCANNER)
        token = TOKEN_SELFTEST
    elif case == "scanner-stdin-disconnected":
        witness = tmp_path / "selftest-witness.jsonl"
        scanner.write_text(witness_scanner(witness))

    before = fx.snapshot()
    r = fx.installer(mode, extra=extra, stdin="not-for-the-scanner\n")
    if token is None:
        assert r.returncode == EXIT_OK, combined(r)
        assert_no_identity_printed(r)
        if witness is not None:
            assert witness.exists(), "the scanner self-test must actually be invoked:\n" + combined(r)
            calls = [json.loads(ln) for ln in witness.read_text().splitlines()]
            selftests = [c for c in calls if "--self-test" in c["argv"]]
            assert selftests, f"no --self-test invocation recorded: {calls}"
            assert all(c["stdin"] == "" for c in selftests), (
                f"the self-test must run with stdin disconnected (</dev/null): {selftests}")
        return
    if mode == "--check-pre-push-config":
        assert fx.snapshot() == before, "a check with a missing input must change nothing on disk:\n" + combined(r)
    else:
        assert not os.path.lexists(dest), "an activation with a missing input must install nothing:\n" + combined(r)
    assert_refused(r, token)


# ================================================================================================
# 6. the unchanged hook's own precedence, characterised so nothing above can be misread as a fix
# ================================================================================================

def run_hook(fx, localsha, remotesha, extra=None, path_prefix=None):
    dest = fx.tmp / "destination.git"
    if not dest.exists():
        subprocess.run(["git", "init", "-q", "--bare", str(dest)], check=True,
                       capture_output=True, text=True, env=fx.env())
    line = f"refs/heads/main {localsha} refs/heads/main {remotesha}\n"
    return subprocess.run(["bash", fx.tracked_hook(), "destination", str(dest)],
                          cwd=str(fx.repo), input=line, capture_output=True, text=True,
                          env=fx.env(extra, path_prefix))


def commit_as(fx, identity, rel, body):
    p = fx.repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    fx.git("add", rel)
    fx.git("commit", "-q", "--no-verify", "-m", "add " + rel,
           extra={"GIT_AUTHOR_EMAIL": identity, "GIT_COMMITTER_EMAIL": identity,
                  "GIT_AUTHOR_NAME": "fixture", "GIT_COMMITTER_NAME": "fixture"})
    return fx.git("rev-parse", "HEAD").stdout.strip()


@pytest.mark.parametrize("case", ["config-only-a", "env-file-b", "default-file-b",
                                  "default-empty-falls-back", "default-comment-only-falls-back",
                                  "unreadable-selected-policy"])
def test_legacy_identity_precedence(tmp_path, case):
    """Characterises guard/hooks/pre-push AS SHIPPED: a nonempty file (env path or the default
    path) shadows `fleetops.approvedIdentity`; an empty or comment-only file falls back to config;
    an unreadable selected file blocks. This is the reason the config route above refuses those
    sources — and this test is what stops the installer tests from being read as a change to the
    hook's precedence. No precedence fix is claimed or tested here.

    Preservation controls: ALL of it — every assertion here must pass on the parent, because the
    hook is unchanged. `config-only-a` is the positive control that the fixture push works.
    Instrument for the unreadable case: chmod 000 unprivileged, a failing `sed` shim under euid 0.
    """
    fx = make_fixture(tmp_path)
    base = fx.git("rev-parse", "HEAD").stdout.strip()
    extra, path_prefix = {}, None
    default = fx.repo / "_tools" / "approved_identities.txt"
    if case == "env-file-b":
        outside = tmp_path / "outside-policy.txt"
        outside.write_text(IDENTITY_B + "\n")
        extra = {TOKEN_ENV_SHADOW: str(outside)}
    elif case == "default-file-b":
        default.write_text(IDENTITY_B + "\n")
    elif case == "default-empty-falls-back":
        default.write_text("")
    elif case == "default-comment-only-falls-back":
        default.write_text("# nobody here\n  \n")
    elif case == "unreadable-selected-policy":
        outside = tmp_path / "outside-policy.txt"
        outside.write_text(IDENTITY_B + "\n")
        path_prefix, instrument = force_unreadable(fx, outside)
        extra = {TOKEN_ENV_SHADOW: str(outside)}
        head = commit_as(fx, IDENTITY_B, "docs/note.md", "an ordinary line\n")
        r = run_hook(fx, head, base, extra=extra, path_prefix=path_prefix)
        assert r.returncode != 0, f"unreadable selected policy must block ({instrument}):\n{combined(r)}"
        assert "cannot read approved-identity list" in r.stdout, (instrument, r.stdout)
        return

    selected = IDENTITY_B if case in ("env-file-b", "default-file-b") else IDENTITY_A
    rejected = IDENTITY_A if selected == IDENTITY_B else IDENTITY_B
    head_ok = commit_as(fx, selected, "docs/one.md", "an ordinary line\n")
    r = run_hook(fx, head_ok, base, extra=extra)
    assert r.returncode == 0 and "CLEAN" in r.stdout, (
        f"{case}: the selected source's identity must be accepted:\n{combined(r)}")
    fx.git("reset", "-q", "--hard", base)
    head_bad = commit_as(fx, rejected, "docs/two.md", "an ordinary line\n")
    r = run_hook(fx, head_bad, base, extra=extra)
    assert r.returncode != 0, f"{case}: the shadowed identity must be refused:\n{combined(r)}"
    assert "non-approved" in r.stdout, r.stdout


# ================================================================================================
# 7. replacement is explicit, backed up, verified, and reversible
# ================================================================================================

def restore_from_backup(backup, dest):
    shutil.copyfile(backup, dest)
    os.chmod(dest, mode_of(backup))


def files_with_hash(root, digest, exclude):
    """Every regular file under root (TMPDIR, HOME and the repo all live there) carrying digest."""
    found = []
    for base, _dirs, names in os.walk(root):
        for n in names:
            p = os.path.join(base, n)
            if os.path.islink(p) or not os.path.isfile(p) or os.path.realpath(p) == os.path.realpath(exclude):
                continue
            try:
                if sha256(p) == digest:
                    found.append(os.path.realpath(p))
            except OSError:
                continue
    return found


@pytest.mark.parametrize("case", ["refuse-without-replace", "replace-ok",
                                  "replace-corrupt-copy", "replace-cmp-fails"])
def test_replace_and_restore(tmp_path, case):
    """Guards against silently overwriting a differing hook, against replacing it with no backup,
    and against reporting success when the copy or its parity check failed.

    Preservation control: none for the old installer (it overwrites unconditionally and prints
    `installed` whatever landed); each case is RED on it.
    Repaired behaviour: `refuse-without-replace` — a differing executable hook is left with its
    bytes and mode, exit 1 naming `--replace`, no backup line; `replace-ok` — with --replace the
    old bytes and mode are saved at the path printed as `backup: <abs path>` (never a publishable
    path), the tracked hook is installed and parity-checked, and restoring the backup by hand
    reproduces the old hash, after which the read-only check refuses; `replace-corrupt-copy` and
    `replace-cmp-fails` — an injected copy corruption / parity failure yields nonzero, no success
    line, the backup intact with the old bytes, and the effective path either restored or empty.
    Mutants killed: M-OVERWRITE-WITHOUT-BACKUP (refuse/replace-ok), M-FALSE-INSTALL-SUCCESS
    (corrupt-copy / cmp-fails).
    """
    fx = make_fixture(tmp_path)
    dest = fx.effective_hook()
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    open(dest, "w").write(OLD_HOOK)
    os.chmod(dest, OLD_MODE)
    old_hash = sha256(dest)
    tracked_hash = sha256(fx.tracked_hook())

    if case == "refuse-without-replace":
        r = fx.installer("--pre-push-config")
        assert os.path.isfile(dest) and sha256(dest) == old_hash and mode_of(dest) == OLD_MODE, (
            "a differing pre-existing hook must be preserved without --replace:\n" + combined(r))
        assert_refused(r, TOKEN_REPLACE)
        assert not BACKUP_LINE.search(r.stdout), "no backup is made on a refusal"
        return

    prefix = None
    if case == "replace-corrupt-copy":
        prefix = corrupting_copy_shims(tmp_path)
    elif case == "replace-cmp-fails":
        prefix = failing_cmp_shim(tmp_path)
    r = fx.installer("--pre-push-config", "--replace", path_prefix=prefix)

    copies = files_with_hash(tmp_path, old_hash, exclude=dest)
    assert copies, ("the old hook's bytes must be saved somewhere before it is replaced "
                    "(no file under the fixture carries them):\n" + combined(r))
    m = BACKUP_LINE.search(r.stdout)
    assert m, "replacement must print `backup: <abs path>`:\n" + combined(r)
    backup = m.group(1).strip()
    assert os.path.isabs(backup) and os.path.isfile(backup), backup
    assert os.path.realpath(backup) in copies, f"printed backup path {backup} is not one of the copies found"
    assert sha256(backup) == old_hash, "the backup must hold the old bytes"
    assert mode_of(backup) == OLD_MODE, "the backup must preserve the old mode"
    assert fx.git("ls-files", "--others", "--exclude-standard").stdout == "", (
        "the backup must not land at a publishable (untracked, unignored) path")

    if case == "replace-ok":
        assert r.returncode == EXIT_OK, combined(r)
        assert sha256(dest) == tracked_hash and os.access(dest, os.X_OK), "replacement not installed"
        assert any(dest in ln for ln in success_lines(r)), r.stdout
        assert_no_identity_printed(r)
        restore_from_backup(backup, dest)
        assert sha256(dest) == old_hash and mode_of(dest) == OLD_MODE, "restore must reproduce the old hook"
        c = fx.installer("--check-pre-push-config")
        assert_refused(c, TOKEN_PARITY)
        assert sha256(dest) == old_hash, "the check must not repair the restored hook"
        return

    assert r.returncode != EXIT_OK, "a failed copy/check must not exit 0:\n" + combined(r)
    assert not success_lines(r), "a failed copy/check must not print success:\n" + r.stdout
    assert (not os.path.lexists(dest)) or sha256(dest) == old_hash, (
        "after a failed replacement the effective path holds the old hook or nothing — never an "
        "unverified copy")
    restore_from_backup(backup, dest)
    assert sha256(dest) == old_hash and mode_of(dest) == OLD_MODE, "old state must be recoverable"
    assert_no_identity_printed(r)


# ================================================================================================
# 8. the private approved-identity file is ignored; public tool files are not
# ================================================================================================

@pytest.mark.parametrize("rel,ignored,pattern", [
    ("_tools/approved_identities.txt", True, "/_tools/approved_identities.txt"),
    ("_tools/identity_terms.txt", True, "_tools/identity_terms.txt"),
    ("_tools/identity_terms.example.txt", False, None),
    ("_tools/scan_gate.py", False, None),
    ("guard/hooks/pre-push", False, None),
])
def test_private_identity_ignore(tmp_path, rel, ignored, pattern):
    """Guards the shipping `.gitignore` (copied verbatim into a scratch repository): the private
    approved-identity list must be ignored by its own explicit rule, while the public example,
    the scanner and the hook stay publishable.

    Preservation controls: `_tools/identity_terms.txt` ignored (the pre-existing rule; positive
    control for the query), the three public paths NOT ignored.
    Repaired behaviour (RED on a parent without the C4 row): `_tools/approved_identities.txt`
    ignored by the rule `/_tools/approved_identities.txt`.
    Mutants killed: M-NO-APPROVED-IGNORE (first row not ignored), M-IGNORE-ALL-TOOLS (scan_gate.py
    and the example become ignored).
    """
    fx = Fixture(tmp_path)
    fx.repo.mkdir()
    shutil.copy(GITIGNORE_SRC, fx.repo / ".gitignore")
    fx.git("init", "-q", "-b", "main")
    r = fx.git("check-ignore", "-v", "--no-index", rel, check=False)
    assert r.returncode in (0, 1), combined(r)
    if ignored:
        assert r.returncode == 0, f"{rel} must be ignored by the shipping .gitignore:\n{combined(r)}"
        source, _, matched = r.stdout.rstrip("\n").partition("\t")
        assert matched == rel and source.split(":", 2)[2] == pattern, r.stdout
    else:
        assert r.returncode == 1, f"{rel} must stay publishable:\n{combined(r)}"
