# SPEC — odyssey_crawl_hub.py (the Odysseus data-crawl hub renderer)

Write the file COMPLETELY at `../odyssey_crawl_hub.py`.

## What this is

A **deterministic renderer**: it reads markdown files off disk and emits ONE self-contained HTML page.
No model calls, no network, no cleverness. The same inputs must always produce the same output.

This matters because the source of the design — Odysseus's own `visual_report.py` — works exactly this
way: the LLM emits markdown, and a renderer turns it into the styled page. Style therefore cannot drift
per-model, and presentation stops being something a model can get wrong.

**It is INCREMENTAL by construction.** I will keep adding crawl files over months. Re-running the
script must pick up every file currently on disk and rebuild the page. Never hard-code a file list.

    INPUT   <research-root>/odysseus_crawl/raw/*.md
            <research-root>/odysseus_crawl/reconciled/*.md
    OUTPUT  <research-root>/odysseus_crawl/reports/ODYSSEUS_CRAWL_HUB.html

    usage: odyssey_crawl_hub.py [--root DIR] [--out FILE]

## THE STRUCTURAL RULE (the owner asked for this explicitly)

**RAW and RECONCILED are two separate top-level sections and must never be interleaved.** Raw =
per-leg cited extraction, presented as-authored. Reconciled = adjudicated cross-leg synthesis. A
reader must never be in doubt which they are looking at. Give each its own banner, its own colour
treatment, and its own TOC group.

Order: masthead → provenance strip → `RECONCILED` (the adjudicated view, first because it is what a
returning reader wants) → `RAW` (the evidence it rests on).

## Page structure

1. **Masthead** — eyebrow `ODYSSEUS — HARNESS DATA CRAWL`, an `<h1>` title, and a one-line subtitle.
2. **Provenance strip** — a horizontal bar of `value` + `label` pairs, filled from what is MEASURED at
   render time, never guessed: number of raw docs, number of reconciled docs, total source bytes,
   distinct legs (parsed from filenames, see below), and the render date passed in via `--date` or
   defaulting to the newest input file's mtime. **Never invent a stat.** If a value cannot be
   determined, omit the chip rather than printing a placeholder.
3. **Sticky left TOC**, nested (h2 → h3), grouped under RECONCILED / RAW, with the active section
   highlighted. It must scroll independently and not overlap content.
4. **Content**, rendered from the markdown.

## Filename convention (parse, do not guess)

`RAW_<scope>_<leg>.md` and `RECONCILED_<scope>.md`. From `RAW_core_codex.md` derive scope=`core`,
leg=`codex`. Show the leg as a small pill on that document's header. If a filename does not match,
still render it — fall back to the bare stem as the title and omit the pill. A file that cannot be
parsed must never be silently dropped.

## Markdown support required

Headings (h1–h4), paragraphs, bold/italic, inline code, fenced code blocks, unordered + ordered lists,
blockquotes, horizontal rules, links, and **tables**. Use the `markdown` library if importable
(`pip show markdown`; it IS a dependency of Odysseus so it is likely present) with the `tables` and
`fenced_code` extensions; otherwise fall back to a small internal converter that at minimum handles
headings, paragraphs, lists, fences and tables. Say which path was used in a `<!-- comment -->`.

**Sanitise model-authored HTML.** If `nh3` is importable, run the converted HTML through it. These
documents are written by cloud legs; they are untrusted text.

## THE THREE DEFECTS TO DESIGN OUT (measured on the source design, 2026-07-31)

These were found by rendering Odysseus's own report and inspecting it with two independent vision
passes. Do not reproduce them.

1. **TABLE OVERFLOW — the worst one.** In the reference design, wide comparison tables ran past the
   container and silently clipped their right-most column — which happened to be `Source(s)+Date`, the
   citation column. The two tables even clipped to *different* widths, so the column widths were
   content-driven and unstable.
   **Required fix:** every `<table>` is wrapped in `<div class="table-wrap">` with `overflow-x: auto`,
   the table gets `min-width: max-content` so cells do not crush, and the wrapper shows a visible
   affordance that it scrolls. **No column may ever be unreachable.** Citations are the honesty
   payload of this whole document — clipping them is the one unacceptable failure.
2. **LOW-CONTRAST SECONDARY TEXT.** Inactive TOC entries and provenance labels were dark-grey on dark
   and hard to read. Keep every text colour at a comfortable contrast on the dark background; muted
   text must still be legible, not decorative.
3. **DEAD CHROME.** The reference drew fake refresh/close buttons over its images. This page is a
   static artifact: render no control that does not do something.

## Visual design

Dark, editorial, calm. Adapted from the reference, with the accent corrected — the reference's own
source calls it **"warm terracotta"**, and a vision cross-check read it as copper/terracotta ≈
`#C86D51`, NOT the coral it superficially resembles.

    --bg #121316   --panel #17181c   --ink #e8e6e3   --muted #a49f99
    --accent #c86d51   --accent-light #d98b70   --accent-bg rgba(200,109,81,0.09)
    --raw #6f8fb0        (RAW sections read cool/neutral — evidence)
    --reconciled #c8a24e (RECONCILED reads warm/gold — adjudicated)

- Headings: a serif stack (`Georgia, 'Times New Roman', serif`). Body: system sans. Code: system mono.
- h2 gets a thin accent underline; h1 in the masthead is large and centred.
- Inline code renders as a subtle rounded chip; fenced blocks scroll horizontally rather than wrap.
- Blockquotes get a left accent rail.
- Comfortable measure (~72ch), generous line-height (1.65+).
- **Self-contained**: all CSS inline in a `<style>` block. No external fonts, no CDN, no JS required to
  read the page. A tiny inline script for TOC active-highlighting is fine; the page must remain fully
  readable with JS disabled.
- Responsive: below ~900px the TOC collapses above the content; the body must never scroll sideways
  (only `.table-wrap` and `pre` may).

## Hard rules

- Deterministic: sorted file order, no clock except the resolved render date, no randomness.
- A missing input directory is a clear message and exit 1 — never a traceback, never an empty page.
- Escape everything that is not deliberately HTML.
- Print a summary to stdout: which files were included, and the output path.

## Verification

Gate: `../guard/tests/test_odyssey_hub.py` — do NOT edit it.

    cd .. && python3 -m pytest guard/tests/test_odyssey_hub.py -q

Must be fully green. Do not finish on red.
