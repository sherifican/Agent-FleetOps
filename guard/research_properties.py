"""research_properties.py — deterministic property extractor for nondeterministic research artifacts.

The research pipeline emits LLM prose that cannot be diffed against a golden baseline. Instead I
extract deterministic PROPERTIES of the text (stable functions of an unstable artifact) and watch
those. This module is the foundation consumed by the teeth-prover, the stage-closure checker and
the regression differ.

Purity contract: no network, no clock, no randomness, no writes. The only I/O anywhere in this
module is in `load_id_registry` and `extract_file`. Same input bytes -> same output, always.
`extract` NEVER raises on malformed input: a property that cannot be evaluated comes back
ok=False with the reason in `detail`.
"""

import os
import re
import sys
from dataclasses import dataclass

# The vocabularies have exactly ONE owner: check_leg_contract.py in the parent directory.
# If this import fails this module raises ImportError at module import time — deliberately. Falling back to
# a local copy is the exact drift bug this subsystem exists to prevent.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from check_leg_contract import BUCKETS, ACTIONS, BANNED, PRIOS, PROJECTS, RATINGS  # noqa: E402

_ID = r"A-Za-z0-9_-"  # a YouTube id is 11 chars from this class (yes, '_' and '-' included)

# Contexts in which an 11-char run counts as a video id in a REPORT (plain prose must not match):
#   after youtu.be/  |  after watch?v=  |  after youtube.com/embed/
_RE_ID_URL = re.compile(r"(?:youtu\.be/|watch\?v=|youtube\.com/embed/)([%s]{11})" % _ID)
#   trailing _<11 chars> at the end of a slug-like token (RESULT_/FINAL_ filenames). Do NOT split
#   the slug on '_' to find the id — that bug truncated "NgAglRc_ccs" to "ccs". The greedy slug
#   prefix backtracks to the LAST '_' that leaves exactly 11 id chars at the token end.
_RE_ID_SLUG = re.compile(r"(?<![%s])[%s]*_([%s]{11})(?![%s])" % (_ID, _ID, _ID, _ID))
# Registry files are looser: any bounded run of exactly 11 id chars counts (plus the two report
# contexts above, so slugs and URLs in registry files are picked up too).
_RE_ID_RUN = re.compile(r"(?<![%s])([%s]{11})(?![%s])" % (_ID, _ID, _ID))

_RE_RELEVANCE = re.compile(r"^\s*RELEVANCE:\s*(.+?)\s*=\s*(\S+)\s*$", re.MULTILINE)
_RE_NONE_DECL = re.compile(r"\bNONE\s*[—-]")  # explicit "NONE —"/"NONE -" no-items declaration


@dataclass(frozen=True)
class Property:
    name: str        # stable machine key, e.g. "relevance_lines_present"
    ok: bool         # True = the artifact satisfies this property
    value: object    # the measured value (bool, int, or tuple of str) - for reporting
    detail: str      # human-readable; "" when ok is True


# --- shared parsing helpers (pure) -------------------------------------------------------------------

def _clean_cell(cell):
    """Tolerate **bold**, backticks and stray spaces in table cells before comparing."""
    return cell.replace("*", "").replace("`", "").strip()


def _split_row(line):
    parts = line.strip().split("|")
    if parts and parts[0] == "":
        parts = parts[1:]
    if parts and parts[-1] == "":
        parts = parts[:-1]
    return [_clean_cell(p) for p in parts]


def _relevance_pairs(text):
    """All (project, rating) pairs from RELEVANCE lines whose project is in PROJECTS."""
    pairs = []
    for m in _RE_RELEVANCE.finditer(text):
        proj, rating = m.group(1).strip(), m.group(2).strip()
        if proj in PROJECTS:
            pairs.append((proj, rating))
    return pairs


def _find_table(text):
    """Locate the FIRST qualifying §5 table.

    A header row is a line starting with '|' that has BOTH an "item"-ish column and an
    "action"/"suggested"-ish column at DIFFERENT indices. Data rows start two lines below the
    header (header, then the |:---| separator) and stop at the first line not starting with '|'.
    A row whose item cell is empty or only ':'/'-'/spaces is a separator, not data.

    Returns (header_cells, data_rows, column_map) or None. column_map holds the indices of the
    item/action/bucket/priority columns; a missing optional column maps to None, which means the
    corresponding property has nothing to judge for that table — never a violation by itself.
    """
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if not line.lstrip().startswith("|"):
            continue
        header = [c.lower() for c in _split_row(line)]
        item_idx = next((j for j, c in enumerate(header) if "item" in c), None)
        act_idx = next((j for j, c in enumerate(header)
                        if "action" in c or "suggested" in c), None)
        if item_idx is None or act_idx is None or item_idx == act_idx:
            continue

        def _col(*needles):
            return next((j for j, c in enumerate(header)
                         if any(n in c for n in needles)), None)

        cols = {
            "item": item_idx,
            "action": act_idx,
            "bucket": _col("bucket"),
            "priority": _col("prior"),
        }
        rows = []
        for body in lines[i + 2:]:
            if not body.lstrip().startswith("|"):
                break
            row = _split_row(body)
            item = row[item_idx] if item_idx < len(row) else ""
            if not item or all(ch in ":- " for ch in item):
                continue  # separator, not data
            rows.append(row)
        return header, rows, cols
    return None


def _cell(row, idx):
    """A missing/short optional column yields '' — nothing to judge, not a violation."""
    if idx is None or idx >= len(row):
        return ""
    return row[idx]


def _report_ids(text):
    """Ids found in the report contexts only (URLs + slug-suffixes)."""
    return set(_RE_ID_URL.findall(text)) | set(_RE_ID_SLUG.findall(text))


# --- the twelve properties ----------------------------------------------------------------------------

def extract(text, *, id_registry=None, kind="leg"):
    """Extract every property from the artifact TEXT. Never raises; garbage in -> verdicts out."""

    def safe(name, fn):
        # One bad property must not take out the other eleven.
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - the contract is "never raise"
            return Property(name, False, None, "internal error: %s" % exc)

    def p_relevance_lines_present():
        pairs = _relevance_pairs(text)
        found = {p for p, _ in pairs}
        missing = [p for p in PROJECTS if p not in found]
        ok = not missing
        return Property("relevance_lines_present", ok, tuple(sorted(found)),
                        "" if ok else "missing RELEVANCE lines for: " + ", ".join(missing))

    def p_relevance_values_valid():
        pairs = _relevance_pairs(text)
        bad = sorted({"%s=%s" % (p, r) for p, r in pairs if r.upper() not in RATINGS})
        ok = not bad
        return Property("relevance_values_valid", ok, tuple(bad),
                        "" if ok else "ratings outside the vocabulary: " + ", ".join(bad))

    def p_relevance_calibrated():
        pairs = _relevance_pairs(text)
        distinct = sorted({r.upper() for _, r in pairs})
        # ok=False only when at least one rating was found AND none of them is NONE/LOW
        # (mirrors the existing "everything HIGH is uncalibrated" rule).
        ok = not distinct or bool(set(distinct) & {"NONE", "LOW"})
        return Property("relevance_calibrated", ok, tuple(distinct),
                        "" if ok else "every rating is %s — no NONE/LOW present, uncalibrated"
                        % "/".join(distinct))

    def p_table_present():
        found = _find_table(text) is not None
        declared = bool(_RE_NONE_DECL.search(text))
        ok = found or declared
        return Property("table_present", ok, ok,
                        "" if ok else "no §5 actionable table and no explicit NONE declaration")

    def _table():
        t = _find_table(text)
        return t if t is not None else ([], [], {})

    def p_row_count():
        _, rows, _ = _table()
        # REPORTS, does not judge: ok is True whenever the count could be determined at all,
        # including zero. Only the closure checks turn a count into a verdict.
        return Property("row_count", True, len(rows), "")

    def p_actions_in_vocab():
        _, rows, cols = _table()
        bad = sorted({_cell(r, cols.get("action")) for r in rows
                      if _cell(r, cols.get("action"))
                      and _cell(r, cols.get("action")).upper() not in ACTIONS})
        ok = not bad
        return Property("actions_in_vocab", ok, tuple(bad),
                        "" if ok else "actions outside the vocabulary: " + ", ".join(bad))

    def p_no_banned_verbs():
        _, rows, cols = _table()
        hits = []
        for r in rows:
            cell = _cell(r, cols.get("action"))
            if not cell:
                continue
            label = _cell(r, cols.get("item"))
            for verb, fix in BANNED.items():
                if verb in cell.upper():
                    hits.append((label, "%s (use %s)" % (verb, fix)))
        ok = not hits
        return Property("no_banned_verbs", ok, tuple(sorted(hits)),
                        "" if ok else "banned action synonyms: "
                        + ", ".join("%s -> %s" % h for h in sorted(hits)))

    def p_buckets_in_vocab():
        _, rows, cols = _table()
        bad = sorted({_cell(r, cols.get("bucket")) for r in rows
                      if _cell(r, cols.get("bucket"))
                      and _cell(r, cols.get("bucket")) not in BUCKETS})
        ok = not bad
        return Property("buckets_in_vocab", ok, tuple(bad),
                        "" if ok else "buckets outside the vocabulary: " + ", ".join(bad))

    def p_priorities_in_vocab():
        _, rows, cols = _table()
        bad = []
        for r in rows:
            cell = _cell(r, cols.get("priority"))
            if not cell:
                continue
            norm = "".join(ch for ch in cell.upper() if ch in "P012")
            if norm not in PRIOS:
                bad.append(cell)
        bad = sorted(set(bad))
        ok = not bad
        return Property("priorities_in_vocab", ok, tuple(bad),
                        "" if ok else "priorities outside the vocabulary: " + ", ".join(bad))

    def p_video_ids_resolve():
        # THE FABRICATION GUARD. Skipping must be visible in the detail string, never silent —
        # an unevaluated check that looks identical to a passing check is how a guard goes vacuous.
        if id_registry is None:
            return Property("video_ids_resolve", True, None,
                            "not evaluated (no registry supplied)")
        unresolved = sorted(i for i in _report_ids(text) if i not in id_registry)
        ok = not unresolved
        return Property("video_ids_resolve", ok, tuple(unresolved),
                        "" if ok else "ids not in the registry: " + ", ".join(unresolved))

    def p_word_count():
        # Reports, does not judge.
        return Property("word_count", True, len(text.split()), "")

    def p_has_source_link():
        # ⚠ ARTIFACT-KIND AWARE. Measured 2026-07-31: this fired on 36.7% of the leg corpus — but the
        # contract never ASKS a leg for the source URL (the brief GIVES it to them; nothing requires
        # echoing it back). A property that fires on a third of a corpus that was never asked to
        # satisfy it is the "always fires" defect, which is as useless as one that never fires and
        # actively erodes trust in the rest of the guard.
        # A FINAL is different: the hub card links from it, so there the link is critical.
        found = "youtu.be/" in text or "youtube.com/watch" in text
        if kind != "final":
            return Property("has_source_link", True, found,
                            f"not judged for kind={kind!r} (legs are not asked to cite the URL)")
        return Property("has_source_link", found, found,
                        "" if found else "no youtu.be/ or youtube.com/watch URL found")

    builders = {
        "relevance_lines_present": p_relevance_lines_present,
        "relevance_values_valid": p_relevance_values_valid,
        "relevance_calibrated": p_relevance_calibrated,
        "table_present": p_table_present,
        "row_count": p_row_count,
        "actions_in_vocab": p_actions_in_vocab,
        "no_banned_verbs": p_no_banned_verbs,
        "buckets_in_vocab": p_buckets_in_vocab,
        "priorities_in_vocab": p_priorities_in_vocab,
        "video_ids_resolve": p_video_ids_resolve,
        "word_count": p_word_count,
        "has_source_link": p_has_source_link,
    }
    return {name: safe(name, fn) for name, fn in builders.items()}


# --- the only two functions allowed to touch I/O -------------------------------------------------------

def load_id_registry(paths):
    """Read the given files and return every distinct 11-char YouTube id found in them.

    Missing/unreadable files are skipped silently — this is a registry, not an assertion.
    """
    ids = set()
    for path in paths:
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError:
            continue
        ids.update(_RE_ID_URL.findall(text))
        ids.update(_RE_ID_SLUG.findall(text))
        ids.update(_RE_ID_RUN.findall(text))
    return frozenset(ids)


def extract_file(path, *, id_registry=None, kind="leg"):
    """Thin convenience wrapper: read the file (utf-8, errors=replace) and call `extract`."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError as err:
        return {"readable": Property("readable", False, None, str(err))}
    return extract(text, id_registry=id_registry, kind=kind)
