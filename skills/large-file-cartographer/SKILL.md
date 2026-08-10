---
name: large-file-cartographer
description: >
  Use when you need a faithful structural understanding of a very large file
  (or long log) and want a cheap, reusable navigation index instead of
  re-reading the whole thing each time. Reference case: a ~35K-line /
  ~478K-token monolith (measured on the reference setup). When the active
  model is a local coding model with a modest context, such a file does NOT
  fit one window — this skill is then a genuine necessity, not just a cost /
  precision / reuse optimization. Walks the file in fixed-size line windows
  via scripts/read_window.py, accumulating findings to a DURABLE structure-map
  file between reads so the model never has to hold the whole file at once.
  Output is a committable navigation index (symbol -> line range -> one-line
  purpose) that future audits read instead of re-chunking the monolith.
  Invoke when the user says "map <big file>", "build a structure map of
  <big file>", "read the whole <file> and summarize it", "I need the full
  picture of <file>", or when an audit needs whole-file coverage of a file
  that exceeds context.
license: MIT
metadata:
  version: 1.0.0
  priority: STANDARD
  tags: [large-file, chunking, map-reduce, audit, navigation]
---

# Large-File Cartographer

## Why this exists

**Encoded lesson:** an earlier version of this skill claimed the context
window "cannot HOLD" the target file (~478K tokens vs a "128K" window).
**That 128K figure was wrong** — it came from a config TEMPLATE example value,
not the live model. Check the *actual* context of the *live* model before
asserting a hard limit. A large-context frontier model *can* hold a
~35,000-line / ~478K-token monolith (measured on the reference setup) in one
window, so for the frontier leg this is an optimization, not a hard limit.

**With a local coding model of modest context, though, the same file does NOT
fit one window** — so for local-model work this skill is a genuine necessity,
not just an optimization. Reasons to build a map instead of reading the file
whole every time:
- **Local context limit:** a local model with modest context cannot hold
  ~478K tokens; chunking is the only way to get whole-file coverage on the
  free local lane.
- **Cost (frontier leg):** one whole-file read is ~478K input tokens — *every*
  time. A committed map is built once and reused for ~free thereafter. If your
  paid API budget is constrained, avoiding repeat full reads matters.
- **Precision / attention:** packing ~478K into even a large window can dilute
  attention; a small map + targeted ranged re-reads is often sharper than one
  dump.
- **Reuse:** the map is a durable navigation index (symbol -> line range).

The mechanism is unchanged: read a window -> **extract substantive findings to
a durable map file on disk** -> advance -> repeat to EOF -> synthesize from
the small map. The map file is the deliverable; each raw window is only
transiently loaded.

## When to use

Use this skill when:
1. The file is very large (rule of thumb: > ~250K chars / ~70K tokens) AND you
   want a reusable map or to avoid the per-read token cost of dumping it
   whole — OR the active model's context genuinely can't hold it, AND
2. You need whole-file coverage — a structural map, an inventory of every
   symbol, or a confident "what is in here and where" answer, AND
3. A targeted `grep` + line-range read would NOT answer the question (because
   the question is about the whole file, not one known symbol).

DO NOT use this skill when:
- **A grep + ranged read already answers it.** If the user asks "where is
  `<symbol>` and what does it do", just `grep -n` the symbol and read that
  range. Cartography is overkill and expensive for targeted questions.
- The file already fits in context. Just read it normally.
- A current structure map already exists and the source has not changed since
  it was generated (see Anti-patterns — reuse, do not regenerate).

## Scope

- Primary target: any single very large source file (the reference case was a
  ~35K-line legacy monolith).
- Also valid for any other file that exceeds context (large generated files,
  long logs, big concatenated assets).
- Read-only: this skill never edits the source file. It only reads it and
  writes a separate map file.

## Preconditions

1. Python 3.10+ available in the environment.
2. `scripts/read_window.py` exists alongside this SKILL.md.
3. Your agent environment's file read limit is set sanely (~200,000 chars).
   The default window (1500 lines ≈ ~80K chars) stays well under that and
   under the context window.

## Procedure

### Step 1 — Size check (decide if cartography is even warranted)

```
python3 scripts/read_window.py --file "<target>" --meta-only
```

Read `total_lines` and `approx_chars` from the `WINDOW_META` envelope. If the
file fits in context (< ~250K chars), STOP — read it normally instead. If the
user's question names a specific symbol, STOP — `grep -n "<symbol>"` + a
ranged read is the right tool, not cartography.

### Step 2 — Choose a window size

Default **1500 lines** per window. That is ~80K chars ≈ ~23K tokens — safely
inside a 200K-char read limit, cheap per call, and small enough to keep
attention sharp and to fit a modest local-model context. (On a large-context
frontier model the window could be larger, but small windows keep per-pass
cost and attention dilution down — which is the whole reason to map rather
than dump.)

### Step 3 — Walk the file, accumulating findings to a map file

Start at line 1. For each window:

```
python3 scripts/read_window.py --file "<target>" --start <N> --lines 1500
```

After reading each window, APPEND to the map file (do not keep it all in your
head). For the window, record:
- **Window log row:** `| <n> | <start>–<end> | <one-line summary of what this span contains> |`
- **Every top-level symbol** in the span: `def`/`class`/major constant, its
  exact line, and a one-line purpose.
- **Flags:** any protected function (see below), `TODO`/`FIXME`/`HACK`,
  hardcoded absolute path references (e.g. leftover OS-specific paths from an
  earlier era of the project, or any non-portable absolute path), version
  constants, or anything an auditor would want to find again.

Then read `WINDOW_META.next_start` and `WINDOW_META.eof`. If `eof` is false,
call again with `--start <next_start>`. **Repeat until `eof` is true.** Do
this as many times as it takes — there is no cap; the loop is deterministic
and ends at EOF.

> Protected functions: if the project has functions that must never be edited
> (see [[protected-function-guard]]), flag every sighting of them while
> mapping, and never edit them.

### Step 4 — Synthesize the durable structure map

Once `eof` is reached, read back the accumulated map file (small — fits in
context) and finalize it into the output schema below. The synthesis is built
from the map notes, NOT from the raw file (which is no longer in context).

### Step 5 — Emit + persist the map

Write the finished map to a durable, committable path (default
`maps/<filestem>_structure_map.md`). Tell the owner it exists so future audits
read the small map instead of re-walking the monolith.

## Output schema (`<filestem>_structure_map.md`)

```markdown
# <filename> — Structure Map
> Generated <ISO date> via large-file-cartographer v1.0.0
> Source: <path> | <total_lines> lines | ~<tokens> tokens | walked in <N> windows of <size>
> This map is a NAVIGATION INDEX, not a substitute for the source. Re-read the
> cited line range before asserting any specific PASS/FAIL.

## Window log
| Window | Lines | Summary |
|--------|-------|---------|
| 1 | 1–1500 | shebang, imports, module constants, VERSION="<x.y.z>" (L<nnn> if in range) |
| 2 | 1501–3000 | ... |

## Symbol index
| Symbol | Kind | Lines | Purpose | Flags |
|--------|------|-------|---------|-------|
| <symbol> | func | <start>–<end> | <one-line purpose> | PROTECTED |
| ... | | | | |

## Section / responsibility map
- Lines 1–520: configuration + constants
- Lines 521–2400: <subsystem> ...

## Flags index
- PROTECTED functions: <symbol> (L<nnn>), ...
- TODO/FIXME/HACK: L<...>, L<...>
- Hardcoded absolute paths (leftover OS-specific or other non-portable paths): L<...>
- VERSION constant: L<nnn> = "<x.y.z>"
```

## Anti-patterns

1. **Do not trust the map for verdicts.** The map is a navigation index built
   from summaries. For any specific audit claim (a value, a behavior, a
   PASS/FAIL), re-read the exact cited line range from the source first.
   Verify the artifact before propagating a claim or correction.
2. **Do not regenerate every audit.** A full walk of a ~478K-token file costs
   ~478K input tokens. Build the map ONCE, commit it, and reuse it.
   Regenerate only when the source file actually changes (check mtime / git
   diff vs the map's generated date).
3. **Do not use giant windows.** A window near the context limit reintroduces
   the amnesia problem. Keep windows ≤ ~1500–2000 lines.
4. **Do not use this for targeted lookups.** `grep -n` + ranged read is faster
   and far cheaper when you already know the symbol.
5. **Do not edit the source.** Read-only. The map is a separate file.
6. **Do not compress the map to save tokens** — the map must stay legible and
   complete; its whole value is faithful navigation.

## Verification

To confirm the skill works on a known large file at a path like
`src/<big_file>.py`:
1. `python3 scripts/read_window.py --file "src/<big_file>.py" --meta-only` →
   prints `total_lines` and `eof` handling.
2. `python3 scripts/read_window.py --file "src/<big_file>.py" --start <N> --lines 20` →
   the window includes a known landmark line (e.g. a `VERSION` constant) you
   located beforehand with `grep -n`.
3. Run a full walk on a smaller large file and confirm: (a) the loop
   terminates exactly at `eof`, (b) the map's symbol line ranges are correct
   when spot-read, (c) all of the project's protected functions are flagged
   if present.

## Related skills

- [[protected-function-guard]] — if the project has protected functions, the
  map MUST flag them; pair these skills when mapping the file that contains
  them.
- Audit/verification skills that consume line-range citations get a fast index
  into the file from this map.
- Always re-read cited ranges before asserting any verdict; the map is a
  navigation index, not evidence (verify the artifact before propagating a
  correction).
- This skill is the practical mechanism for "full whole-file coverage" without
  blowing context — required on any lane where the file cannot fit one window.

## Config knobs (if your agent framework supports per-skill config)

```yaml
config:
  default_window_lines:
    type: int
    default: 1500
    description: Lines per window. Keep ≤ ~2000 to stay inside context.
  map_output_dir:
    type: string
    default: "maps"
    description: Where finished structure maps are written + committed.
  flag_protected_functions:
    type: bool
    default: true
    description: Flag the project's protected functions on sight.
```
