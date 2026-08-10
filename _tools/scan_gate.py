#!/usr/bin/env python3
"""scan_gate.py — secrets + personal-data scan over the staging tree. Zero-hit gate.

Two classes:
  SECRET — key/token-shaped strings and credential assignments
  PERSONAL — owner identity, emails, real-looking IPs (doc-range IPs are allowed)

Writes _reports/scan_report.txt. Exit 0 only on zero hits of both classes.
Values are never printed — only file, line number, class, and pattern name.

Mutation proof (--self-test): a planted fake API key and a planted identity string
must each go red; a clean fixture must pass.
"""
import sys, os, re, tempfile, shutil

SECRET_PATTERNS = [
    ("openai-style-key",   re.compile(r"sk-[A-Za-z0-9]{20,}")),
    ("github-token",       re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}")),
    # the closing-quote option matters: JSON-style {"api_key": "..."} has a quote between the
    # name and the colon — the first version of this pattern missed exactly that, and the
    # planted-mutation self-test caught it before the gate was trusted
    ("generic-key-assign", re.compile(r"(?i)(api[_-]?key|secret|token|passw(or)?d)['\"]?\s*[:=]\s*['\"][A-Za-z0-9+/_\-]{12,}['\"]")),
    ("aws-key",            re.compile(r"AKIA[0-9A-Z]{16}")),
    ("private-key-block",  re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
]
def _identity_terms():
    """Identity terms live in a GITIGNORED file — the public scan tool must not itself
    reveal what it redacts. Falls back to a refuse-to-run error if the file is absent,
    because a personal-data scan with no identity list is a check that cannot fail."""
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "identity_terms.txt")
    if not os.path.isfile(p):
        sys.stderr.write("scan_gate: _tools/identity_terms.txt missing — refusing to run a toothless scan\n")
        sys.exit(2)
    terms = [t.strip() for t in open(p, encoding="utf8") if t.strip() and not t.startswith("#")]
    return re.compile("(?i)" + "|".join(re.escape(t) for t in terms))

PERSONAL_PATTERNS = [
    ("owner-identity",     _identity_terms()),
    ("email",              re.compile(r"[a-zA-Z0-9._%+-]+@(gmail|proton|outlook|yahoo)\.[a-z]{2,}")),
    ("rfc1918-ip",         re.compile(r"\b(192\.168|10\.\d{1,3}|172\.(1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b")),
    ("home-user-path",     re.compile(r"/home/(?!<user>|USER|\$)[a-z][a-z0-9]*")),
]
# RFC5737 documentation ranges are the sanctioned replacements — never flagged
DOC_IP = re.compile(r"\b(192\.0\.2|198\.51\.100|203\.0\.113)\.\d{1,3}\b")

def scan(staging: str):
    hits = []
    skip_dirs = {".git", "_reports", "_tools"}
    for root, dirs, files in os.walk(staging):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        for f in files:
            rel = os.path.relpath(os.path.join(root, f), staging)
            try:
                lines = open(os.path.join(root, f), encoding="utf8", errors="ignore").read().split("\n")
            except OSError:
                continue
            for i, ln in enumerate(lines, 1):
                probe = DOC_IP.sub("", ln)
                for name, pat in SECRET_PATTERNS:
                    if pat.search(probe):
                        hits.append((rel, i, "SECRET", name))
                for name, pat in PERSONAL_PATTERNS:
                    if pat.search(probe):
                        hits.append((rel, i, "PERSONAL", name))
    return hits

def write_report(staging, hits):
    os.makedirs(os.path.join(staging, "_reports"), exist_ok=True)
    with open(os.path.join(staging, "_reports", "scan_report.txt"), "w") as f:
        if not hits:
            f.write("scan_gate: CLEAN\n")
        for rel, i, cls, name in hits:
            f.write(f"{cls}\t{name}\t{rel}:{i}\n")

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
            'cfg = {"api_key": "abcDEF123456789xyzKLMNO"}\n')
        # MUTATION 2: planted identity — drawn FROM the loaded terms file, never hardcoded,
        # so the self-test stays red-capable for any user's terms (a fresh-clone run with a
        # different terms file exposed the hardcoded version as unable to fail)
        first_term = PERSONAL_PATTERNS[0][1].pattern.split(")", 1)[1].split("|")[0].lstrip("(")
        open(os.path.join(tmp, "skills", "m2.md"), "w").write(
            f"ask {first_term} about it\n")
        hits = scan(tmp)
        classes = {(h[0], h[2]) for h in hits}
        ok_red = ("skills/m1.md", "SECRET") in classes and ("skills/m2.md", "PERSONAL") in classes \
                 and not any(h[0] == "skills/clean.md" for h in hits)
        print("scan_gate self-test:", "PASS (control green, both mutations red)" if (ok_clean and ok_red) else "FAIL")
        return 0 if (ok_clean and ok_red) else 1
    finally:
        shutil.rmtree(tmp)

if __name__ == "__main__":
    if "--self-test" in sys.argv:
        sys.exit(self_test())
    staging = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    hits = scan(staging)
    write_report(staging, hits)
    if hits:
        for rel, i, cls, name in hits[:40]:
            print(f"{cls}\t{name}\t{rel}:{i}")
        print(f"scan_gate: {len(hits)} hit(s) — see _reports/scan_report.txt — batch NOT publishable")
        sys.exit(1)
    print("scan_gate: CLEAN")
