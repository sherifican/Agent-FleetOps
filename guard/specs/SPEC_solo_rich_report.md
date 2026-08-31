# SPEC — Solo-Rich Reports (the contract)

A **Solo-Rich Report** is a standalone, richly-presented artifact for a research finding that earned
one. It is NOT a prettier FINAL — it is a different tier with a higher evidentiary bar.

Two halves, and they must stay separate in the mind:
- **THIS FILE = the CONTRACT** — what earns one, what it must contain, what disqualifies it.
- `solo_rich_report.py` = the RENDERER — deterministic markdown+assets → one self-contained HTML page.

The renderer is deterministic on purpose. The model emits structured markdown; the renderer makes the
page. Style therefore cannot drift per-model, and presentation stops being something a model can get
wrong. (Same posture as `visual_report.py`, which is where this design came from.)

---

## 1. WHAT EARNS ONE — the gate

The owner's constraint is explicit: *"rich data entry, but without bloat padding."* A trigger of
"seems interesting" produces exactly that bloat. So the gate is **measurable**, and a report must
clear **at least two of the three**:

  **(a) CROSS-LEG CONVERGENCE** — ≥2 independent legs reached the same item at P0, or ≥3 legs at P1+.
       Convergence across independent legs outranks any single leg's confidence rating.
  **(b) ACTIONABLE DENSITY** — ≥5 surviving P0/P1 items after reconcile (surviving = still standing
       once the reconcile has cut what one leg overclaimed).
  **(c) REAL CAPTURED MEDIA** — ≥3 curated frames from this repo's vision pipeline, or ≥3 fetched
       documents/pages actually retrieved.

**CROSS-VIDEO convergence (the same item independently surfacing in ≥3 videos with different leg sets)
counts as (a) on its own and is the strongest signal the pipeline produces.**

Record which criteria were met in the report's own provenance block. A report that cannot name its
qualifying criteria does not get made.

**Automatic disqualifiers**, regardless of score: a single-leg unreconciled RESULT · a sponsored
segment or the creator's own paid course as the core finding · anything whose actionable items are all
`REJECT`.

## 2. STRUCTURE — required sections, in order

1. **Masthead** — title, source link, creator, date, duration.
2. **Provenance block** (machine-filled, never hand-written): legs used + their byte counts ·
   reconcile date · audit verdict · frames captured/kept · items harvested · **which gate criteria
   this report met**.
3. **VERDICT** — ≤120 words. What this is, and what to do. If the honest verdict is "nothing
   actionable", say it and stop; a short honest report beats a padded one.
4. **THE FINDING** — the one thing most worth knowing, argued properly.
5. **BY DATA POINT** — the reconciled body. Every claim: **Claim → Evidence → Analysis → Implications.**
6. **ACTIONABLE ITEMS** — the standard closed vocabularies (6 buckets · 7 actions · P0/P1/P2), with a
   `Convergence` column stating `n/N legs`.
7. **WHAT THIS STACK ALREADY DOES** — honest placement against this repo's stack. Prevents re-adopting what already runs.
8. **OPEN QUESTIONS / UNVERIFIED** — what is not settled and the exact check that would settle it.

Long sections are fine **when the length is doing work**. A section that could be half as long without
losing a claim is padding, and padding is a contract violation, not a style preference.

## 3. EVIDENCE RULES — stricter here than a normal FINAL

- **THE PROVENANCE FLOOR** (always required): source URL · video/document title · creator ·
  the captured media. That is sufficient provenance for a decision artifact — a reader can find the
  claim from it. Do not gate a report on anything beyond this floor.
- **Per-claim anchors** (`[MM:SS]`, fetched URL, `path:line`) are required in two cases and optional
  otherwise:
    (i)  the claim is **contested** — the legs disagreed, or it contradicts a prior belief;
    (ii) the claim is **surprising** — a benchmark number, a price, a version, a capability claim.
  Elsewhere they are a convenience, not a requirement.
  *Rationale (owner, 2026-07-31):* a timestamp is what makes a disputed claim cheap to check —
  "at 05:14 he demonstrates X" versus "the video says X". For an uncontested descriptive statement it
  buys nothing, and demanding it everywhere is rigour theatre that produces padding.
- **Where the leg RESULTs already carry anchors, KEEP them** — it is free. Measured 2026-07-31: legs
  carried 41/110/38 timestamps on one video while its reconciled FINAL carried 0, and 77 of 134 FINALs
  have none. Reconcile drops them for no reason. Read the legs, not only the FINAL.
- **Per-claim binding**: where a citation IS given, it attaches to the specific claim, never to a
  paragraph in bulk. (Adapted from Tongyi WebWeaver's writer scaffold.)
- **MUST-CITE**: any number, version, benchmark, price, capability claim, or quoted assertion.
- **MUST-NOT-CITE as fact**: this report's own inference, an opinion, or a projection. Label those `[inference]`.
- **`⚠️ UNVERIFIED`** on anything not confirmed here — and say what would still need checking.
- **`ADOPT` keeps its evidence bar**: only for something personally run or verified. Otherwise `VET`.
- Never invent an install command, repo path, licence, or version. Leave it out.

## 4. MEDIA — real evidence only

- Images must be **frames this repo's vision pipeline captured** or **pages this run fetched**. An OpenGraph/marketing
  preview card is NOT evidence and is banned. (This is the specific failure of the design this format
  was adapted from: its figures were link-preview cards dressed as documentation.)

- **★ THE RULE THAT MATTERS (owner, 2026-07-31): EVERY SCREENSHOT MUST CARRY ITS RELEVANT DATA POINT.**
  An image earns its place by being **evidence for a specific claim**, not by being a nice frame from
  the video. Concretely, each image must:
    1. be **placed WITH the claim it evidences** — adjacent to that data point, not pooled in a gallery;
    2. carry a caption that names **the data point it shows**, not merely what is on screen.
       "The `/auto-validate` gate file, read-only to the builder [16:00]" — not "a terminal window";
    3. show something **a sentence cannot convey as well** — a UI, a diagram, a benchmark table, a
       config, a demonstrated result.
  **A frame that does not evidence a data point is padding and must be dropped, however good it looks.**
  This is the difference between illustration and evidence, and it is the whole reason this pipeline captures real
  frames instead of using preview cards.

- **Cap: 8 images** — a deliberate loosening from the standard card's tighter limit, but it is a
  CEILING, not a target. Three frames that each carry a data point beat eight that decorate.
- An image with no caption, or whose caption does not name a data point, is **dropped by the renderer**,
  not rendered blank — so the rule is mechanical, not advisory.

### 4.1 THE BINDING MECHANISM (how "placed with its claim" is enforced)

A caption alone does not place a frame. Placement is declared in the caption sidecar's FIRST line:

    @anchor: A1
    Per-role cost accounting, live: ARCHITECT claude-fable-5 … [16:00]

`@anchor: <token>` names the section the frame evidences. The renderer matches `<token>` against
section headings (prefix match on the heading text, e.g. `A1` matches `### A1. ★ The one thing…`) and
inserts the figure **immediately after that section's first paragraph** — beside the claim, in the
reading flow.

- A frame whose anchor matches **no** heading is NOT silently relocated to a gallery — it is reported
  as an error on stdout and dropped. A mis-anchored frame is a broken binding, and a broken binding
  that silently degrades into decoration is exactly the failure this section exists to prevent.
- A frame with a caption but **no** `@anchor:` line goes to a clearly-labelled trailing
  **"Unplaced evidence"** section, and the renderer prints a warning naming it. Unplaced is allowed but
  never invisible — it is a visible admission that the frame is not doing evidentiary work yet.
- **A trailing gallery is NOT the default.** Anchored placement is. The first build of this format
  pooled every frame into a "Captured Evidence" section at the end, which satisfied the caption rule
  while defeating its purpose: a gallery at the bottom is decoration, a frame beside its claim is
  evidence.

## 5. MERMAID — encouraged, with a rule

Use a mermaid diagram where it genuinely helps: proposed workflows, system/process structure, how
components/agents/models interconnect, decision flows, before/after architectures.

**Do NOT** use one to restate a list. If the diagram has no edges, it wanted to be a list.

**Render mermaid to inline SVG at BUILD time.** No runtime JS, no CDN — these pages are opened from
disk and must render offline, forever, with nothing to load.

## 6. LINKAGE — the two-way rule

- A Solo-Rich report lives at `research/solo_rich/SOLO_<slug>_<date>.html`.
- Its **hub card MUST carry a link to it** (`data-solo-report` + a visible affordance).
- **Both directions must resolve.** A card pointing at a missing report, or a report with no card, is a
  broken artifact — the same class as a citation that does not resolve. This is a guard property, not
  a convention.

## 7. HONESTY — the standing rules apply, harder

The report is a *derived* artifact: everything in it must trace to the reconciled FINAL, the leg
RESULTs, or captured media. **Never introduce a claim at render time that is not in the sources.**
If the sources disagree, say so and show both — do not smooth it into a single confident line.
