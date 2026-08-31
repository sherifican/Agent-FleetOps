#!/usr/bin/env python3
"""Teeth test for passback_send_check — a check that has never been seen to fail is not a check.

Two mutations, each must turn it red in the RIGHT way:
  M1  edit a file the PC already holds  -> must report DIVERGED and exit 1 (VIOLATION)
  M2  make the recipient unreachable    -> must exit 2 (UNMEASURED), never 0
Restores byte-exactly and verifies the restore by hash.
"""
import hashlib, importlib.util, io, os, shutil, sys, contextlib

sys.path.insert(0, "./guard")
spec = importlib.util.spec_from_file_location("psc", "./guard/passback_send_check.py")
psc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(psc)

OUTBOX = os.path.expanduser(os.environ.get("PASSBACK_OUTBOX", "") or "")
if not OUTBOX:
    print("2 CANNOT_CHECK: set PASSBACK_OUTBOX to the outbox this teeth test should exercise.\n"
          "   There is no default — a guessed layout is one machine's, and a check aimed at a\n"
          "   path that does not exist here would report a clean-looking nothing.")
    sys.exit(2)
TARGET = OUTBOX + "/replies/" + os.environ.get("PASSBACK_TEETH_TARGET", "REPLY_example.md")
BACKUP = os.environ.get("TEETH_BACKUP", "/tmp/teeth_target.bak")


def sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def run():
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        rc = psc.main()
    return rc, buf.getvalue()


ok = True
orig = sha(TARGET)
shutil.copy2(TARGET, BACKUP)
print(f"target: {TARGET.split('/')[-1]}  sha={orig[:12]}")

# --- M1: DIVERGED ---
try:
    with open(TARGET, "a", encoding="utf-8") as fh:
        fh.write("\n<!-- teeth probe -->\n")
    rc, out = run()
    hit = "DIVERGED" in out and TARGET.split("/")[-1] in out
    print(f"  M1 edit-a-sent-file       -> exit {rc}, DIVERGED reported: {hit}  "
          f"{'PASS' if (hit and rc == 1) else 'FAIL'}")
    ok &= hit and rc == 1
finally:
    shutil.copy2(BACKUP, TARGET)
    restored = sha(TARGET)
    print(f"  restore verified by hash  -> {'OK' if restored == orig else 'MISMATCH!'}")
    ok &= restored == orig

# --- M2: recipient unreachable must be UNMEASURED, never clean ---
saved = psc.PCSH
try:
    psc.PCSH = "/bin/false"
    rc, out = run()
    print(f"  M2 unreachable recipient  -> exit {rc}  {'PASS' if rc == 2 else 'FAIL'}")
    ok &= rc == 2
finally:
    psc.PCSH = saved

# --- control: does it ever report clean when it should not? ---
rc, out = run()
print(f"  baseline (restored)       -> exit {rc} (2 expected: not-pushed files are UNMEASURED)")

print("\nRESULT:", "HAS TEETH — both mutations detected, restore clean" if ok else "NO TEETH — do not trust it")
sys.exit(0 if ok else 1)
