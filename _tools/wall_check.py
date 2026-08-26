#!/usr/bin/env python3
"""wall_check.py — the never-publish classification as code.

Refuses any staged file whose PROVENANCE or CONTENT matches a never-publish wall:
  - provenance walls: files copied from the memory tree, curation data, hive content,
    auth-adjacent files, env files (checked via the provenance manifest each copy-in
    step must append to: _reports/provenance.tsv  "staged_path<TAB>source_path")
  - content walls: strings that identify never-publish material even without provenance
    (private repo name, memory-tree paths, auth file names)

Exit 0 = clean. Exit 1 = WALL HIT (listed). Exit 2 = missing/invalid provenance manifest.
A staged file with NO provenance line is a wall hit by definition — untracked origin
is not publishable. This tool is deliberately fail-closed.

Mutation proof (run: wall_check.py --self-test): plants a fake memory-file copy and a
fake unprovenanced file in a temp staging tree and MUST go red on both; a clean tree
must pass. The self-test failing means this gate cannot be trusted.
"""
import sys, os, re, tempfile, shutil

WALL_SOURCE_PATTERNS = [
    r"/memory(/|$)",                       # the memory tree, any depth
    r"/\.claude/curation/(?!README)",      # curation data (its README documents architecture)
    r"CURATION_LEDGER|CURATION_REJECTS|HARVEST_",
    r"/\.claude/hive/",                    # hive content sections
    r"/\.hermes/(auth|\.env|profiles/)",   # auth-adjacent
    r"\.env($|\.)", r"auth\.json$", r"id_ed25519", r"\.pem$", r"kanban\.db$",
    r"memories?/", r"transcripts?/",
]
WALL_CONTENT_PATTERNS = [
    r"System_Functions_Off-Box_Backup",
    r"/projects/-home-[a-z]+/memory/",
    r"BWS_ACCESS_TOKEN\s*=",
    r"api\.kimi\.com|OPENROUTER_API_KEY\s*=",
]

def provenance(staging: str):
    man = os.path.join(staging, "_reports", "provenance.tsv")
    if not os.path.isfile(man):
        return None
    m = {}
    for ln in open(man, encoding="utf8"):
        ln = ln.rstrip("\n")
        if not ln or ln.startswith("#"):
            continue
        parts = ln.split("\t")
        if len(parts) != 2:
            return None
        m[parts[0]] = parts[1]
    return m

def staged_files(staging: str):
    skip_dirs = {".git", "_reports", "_tools", "__pycache__", ".pytest_cache", ".venv", "node_modules"}
    for root, dirs, files in os.walk(staging):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        for f in files:
            if f in ("STAGING_README.md",):
                continue
            p = os.path.join(root, f)
            yield os.path.relpath(p, staging).replace(os.sep, "/")

def check(staging: str):
    hits = []
    prov = provenance(staging)
    if prov is None:
        print("wall_check: provenance manifest missing/invalid (_reports/provenance.tsv)")
        return 2, []
    for rel in staged_files(staging):
        src = prov.get(rel)
        if src is None:
            hits.append((rel, "NO PROVENANCE — untracked origin is not publishable"))
            continue
        for pat in WALL_SOURCE_PATTERNS:
            if re.search(pat, src):
                hits.append((rel, f"source wall: {pat}  (from {src})"))
                break
        try:
            txt = open(os.path.join(staging, rel), encoding="utf8", errors="ignore").read()
        except OSError:
            hits.append((rel, "unreadable"))
            continue
        for pat in WALL_CONTENT_PATTERNS:
            if re.search(pat, txt):
                hits.append((rel, f"content wall: {pat}"))
                break
    return (1 if hits else 0), hits

def self_test():
    tmp = tempfile.mkdtemp(prefix="wallcheck_selftest_")
    try:
        os.makedirs(os.path.join(tmp, "_reports"))
        os.makedirs(os.path.join(tmp, "skills"))
        # clean file with provenance -> must pass
        open(os.path.join(tmp, "skills", "ok.md"), "w").write("a generic skill\n")
        # MUTATION 1: a copy whose provenance is the memory tree -> must go red
        open(os.path.join(tmp, "skills", "leak.md"), "w").write("innocuous text\n")
        # MUTATION 2: a file with no provenance line at all -> must go red
        open(os.path.join(tmp, "skills", "orphan.md"), "w").write("no origin\n")
        with open(os.path.join(tmp, "_reports", "provenance.tsv"), "w") as f:
            f.write("skills/ok.md\t/home/user/.claude/skills/generic/SKILL.md\n")
            f.write("skills/leak.md\t/home/user/.claude/projects/-home-user/memory/user-owner.md\n")
        rc, hits = check(tmp)
        hit_files = {h[0] for h in hits}
        ok = (rc == 1
              and "skills/leak.md" in hit_files
              and "skills/orphan.md" in hit_files
              and "skills/ok.md" not in hit_files)
        # clean-tree control: remove the two mutants -> must pass
        os.remove(os.path.join(tmp, "skills", "leak.md"))
        os.remove(os.path.join(tmp, "skills", "orphan.md"))
        rc2, hits2 = check(tmp)
        ok = ok and rc2 == 0 and not hits2
        print("wall_check self-test:", "PASS (both mutations red, control green)" if ok else "FAIL")
        return 0 if ok else 1
    finally:
        shutil.rmtree(tmp)

if __name__ == "__main__":
    if "--self-test" in sys.argv:
        sys.exit(self_test())
    staging = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    rc, hits = check(staging)
    if rc == 0:
        print(f"wall_check: CLEAN ({sum(1 for _ in staged_files(staging))} staged files)")
    else:
        for rel, why in hits:
            print(f"WALL HIT  {rel}  ::  {why}")
        print(f"wall_check: {len(hits)} hit(s) — batch is NOT publishable")
    sys.exit(rc)
