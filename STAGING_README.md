# OSS export staging — one-way curated export target
Pipeline: copy-in → sanitize.py (audit report per file) → wall_check.py (never-publish refusal, mutation-proven) → readme_guard.sh (refuses a tree that DELETED load-bearing README content — the inverse question; deletion passes every other gate)
→ scan gate (secrets + personal data, zero-hit) → Claude review vs PUBLISH_CLASSIFICATION_2026-08-09 →
OWNER GATE per batch → push to the NEW public repo → fresh-clone verify + public CI green.
Rules: no .git is ever copied in; this tree's history begins at its own init; the private backup repo is
never a remote here. Reports land in _reports/.

**These gates are STAGING-side, not CI.** Public CI runs the hermetic suite, the guard layer,
`ref_gate.py` and `readme_guard.sh` only. `wall_check.py` and `scan_gate.py` run here, before a
batch is pushed — that is the point: a secret is caught before it lands, not after. `wall_check.py`
additionally reads the provenance ledger at `_reports/provenance.tsv`, which is gitignored and
deliberately NOT published (it records private source paths). So `wall_check.py` is present in a
clone but cannot pass from one — it is not a check a downstream user is expected to run.
