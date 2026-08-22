# OSS export staging — one-way curated export target
Pipeline: copy-in → sanitize.py (audit report per file) → wall_check.py (never-publish refusal, mutation-proven) → readme_guard.sh (refuses a tree that DELETED load-bearing README content — the inverse question; deletion passes every other gate)
→ scan gate (secrets + personal data, zero-hit) → Claude review vs PUBLISH_CLASSIFICATION_2026-08-09 →
OWNER GATE per batch → push to the NEW public repo → fresh-clone verify + public CI green.
Rules: no .git is ever copied in; this tree's history begins at its own init; the private backup repo is
never a remote here. Reports land in _reports/.
