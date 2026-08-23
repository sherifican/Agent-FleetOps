## v4.0 — 2026-08-22 — Generalized multi-box fleet monitor

- Added `boxes.json` configuration with a zero-config single `local` default, relay paths, and per-device badge/color/power-cap labels. The shipped example contains dGPU, iGPU, and eGPU labels without assuming any particular hardware.
- Added pure, safe-default readers and renderers for per-box receipt grids, relay model rows, sidecar states, serving-only throughput, background-agent ledgers, downloads, and admission-lane unions.
- Receipt rows reserve the model cell before truncating a filename, preserve a right-flush size/date tail, use KB/MB bands, and escape untrusted receipt paths once. `_pad_markup` now preserves markup at exact visible width.
- All box identity and device metadata now comes from configuration or recorded ledger fields; no deployment identity is encoded in the TUI.

## v3.45 — 2026-08-08 — HEALTH panel gains RAM + swap (the swap half is the point)

- [feat] `sources/health.py`: new `read_meminfo()` parsing `/proc/meminfo` (4s cache, file read, no
  subprocess) + six new `HealthSnapshot` fields — `ram_used_gb/ram_total_gb/ram_pct` and
  `swap_used_gb/swap_total_gb/swap_pct`. Rendered under `disk:` as
  `ram: 9.2/30.3GB (30%)` / `swap: 6.8/8.0GB (85%)`.
- [feat] **Swap thresholds are deliberately TIGHTER than RAM's** — swap red at 75%/yellow at 50%,
  RAM red at 90%/yellow at 75%. Reason, recorded so nobody "tidies" them into agreement: the box
  OOM-killed an application on 2026-08-08 while RAM sat at ~24% and swap at ~86%. **A RAM-only
  readout would have shown green through the entire incident.** `test_incident_state_is_not_all_green`
  replays those exact numbers and fails if the panel renders all-green.
- [fix] Used RAM is `MemTotal - MemAvailable`, never `MemTotal - MemFree`. This box routinely holds
  ~19GB of page cache the kernel returns on demand; a MemFree-based figure would have read ~93% while
  21GB was genuinely available, training the owner to ignore the row.
- [infra] NO RAM temperature field, by design. This machine has no DIMM sensor — hwmon exposes only
  `k10temp`, `nvme` and two NICs, and the `jc42` SPD driver is absent. The owner asked for one; the
  honest answer is the hardware cannot provide it, and a CPU-derived stand-in would be acted on as
  real. `test_no_fabricated_ram_temperature` asserts the field stays absent.
- [infra] `tests/test_ram_monitor.py` — 15 Claude-authored tests, hermetic (fixture text, never the
  live `/proc/meminfo`).
- [fix] Local lane truncated `widgets/format.py` from 683 to 428 lines while adding the rows, silently
  dropping 11 functions (`format_models`, `format_cloud_legs`, the whole ops family). Caught by the
  full-suite gate, restored from git, rows re-applied by hand. Its `health.py`/`models.py` work was
  correct and kept.

## v3.44 — 2026-08-08 — MODELS panel names WHICH codex variant is running (Sol / Terra / Luna)

- [feat] `sources/cloud_legs.py`: new `read_codex_procs()` / `build_codex_rows()` / `codex_status()`,
  mirroring the kimi variant reader. The ☁ CLOUD section now shows **"codex Sol"**, **"codex Terra"**,
  **"codex Luna"** with **"fleet dispatch"** vs **"interactive session"**, replacing the flat
  `codex (session)` row. Owner-reported: the fleet runs codex under several profiles and the panel
  could not tell them apart, so moving the gh-watch cron off Sol/xhigh onto Terra/high was invisible.
  The variant IS the task type — Sol at xhigh is the most expensive configuration on the box.
- [feat] Reasoning effort is surfaced when the wrapper sets it (`codex Sol · fleet dispatch · xhigh`),
  which is what distinguishes `codex-research` (`-p terra` PLUS an xhigh override) from `codex-terra`.
- [fix] HONESTY RULE, gated by test: effort is shown ONLY when `-c model_reasoning_effort=` is literally
  on the argv. Profile-based wrappers leave effort in a toml this reader never opens, so it prints NO
  effort rather than guessing "terra means high" — the toml can be edited, and a row that invents an
  unobserved value is worse than one that stays quiet. Likewise `-p terra` is reported as a PROFILE and
  never upgraded to the claim "the model is gpt-5.6-terra".
- [fix] `-p` means PROFILE in codex, not print. The kimi and claude readers in this same module treat
  `-p` as print mode; copying that here would consume the profile value as a flag, losing the variant
  and mislabelling the row. Mode comes from an exact `exec` token instead.
- [fix] Invocation match mirrors the kimi rule — `basename(argv[0]) == "codex"` or a later token
  containing `/` whose basename is `codex` — so `grep -r codex .` is not counted as a codex process.
- [infra] `tests/test_codex_model_variant.py` (20 tests, Claude-authored). Every cmdline fixture was
  read from /proc on 2026-08-08, not invented. Mutation-proven: inventing an effort, ignoring `-p`, and
  substring-matching the program name each turn the suite red on the specific test written for them.
- [fix] Hermeticity: `test_app.py::test_gather_data` and `test_kimi_variant_wiring.py` now stub
  `codex_status` too. The new reader opened a seam they did not patch, so they had begun reading the
  LIVE process table — passing or failing on whatever happened to be running on the box.

## v3.43 — 2026-07-28 — MODELS panel names WHICH Kimi is running (K3 vs K2.7 Code)

- [feat] `sources/cloud_legs.py`: new `read_kimi_procs()` / `build_kimi_rows()` / `kimi_status()` +
  `_iter_proc_cmdlines()` seam. The MODELS panel now shows **"kimi K3"** or **"kimi K2.7 Code"** with
  activity **"fleet dispatch"** vs **"interactive session"**, replacing the flat `kimi (session)` row.
  K3 is selected only by an explicit `-m k3`, so the generic row hid whether the K3 trial was actually
  exercising K3 — it wasn't (see the wrapper fix below).
- [fix] Detection reads the INVOCATION argv, not the matched pid's. Measured live: `pgrep -x kimi-code`
  matches a process whose entire argv is `kimi-code` — the `-m`/`-p` flags live on the launching wrapper
  (`timeout 600 …/kimi -m k3 -p …`, comm=`timeout`). Reading the matched pid's cmdline finds no flags and
  would report every run as default-model/interactive, i.e. the exact bug this closes.
- [fix] Invocation match is `basename(argv[0]) in (kimi, kimi-code)` OR a later token containing `/` whose
  basename is `kimi`. A bare `kimi` token that is not argv[0] is an ARGUMENT — `grep -r kimi ~`
  must not register as a running leg. Mode uses exact token equality (`-p`/`--print`), never substring:
  `--json-path` contains `-p`.
- [infra] `cloud_snapshot()` substitutes the model-specific rows for the generic kimi row, degrading to the
  old row if detection returns nothing or raises — the panel must never die.
- [infra] Gates: `tests/test_kimi_model_variant.py` (16) + `tests/test_kimi_variant_wiring.py` (6). Suite 346 green.

## v3.40 — 2026-07-14 — HEALTH `loaded:` counts sidecars

## v3.42 — 2026-07-16
- MODELS panel now shows **Bonsai-Ternary-27B** as a loaded sidecar (:8100) with VRAM + in-flight, whenever `bonsai-serve` is running (on-demand). One-line SIDECARS addition; inherits the existing sidecar VRAM/busy detection.
- **[fix]** HEALTH's `loaded:` line now includes llama-server **sidecars** (gemma4-vision `:8336`, GLM `:8090`), matching the MODELS panel. Previously it read only ollama `/api/ps`, so it showed `loaded: none` while a resident sidecar was holding VRAM (the owner saw gemma4 loaded in MODELS + ~0.3 GB on the GPU, but HEALTH said nothing loaded). `build_snapshot` gained a `sidecars=` param (backward-compatible default); `snapshot()` passes `modelstate.read_sidecars()`. New test asserts a sidecar is counted in `loaded`. (Context: gemma4-vision runs `--sleep-idle-seconds 600`, so it's usually asleep at ~0.3 GB residual, reloading on demand — not pinned.)

## v3.39 — 2026-07-14 — Sidecar in-flight accuracy fix
- **[fix]** A llama-server **sidecar** (gemma4-vision `:8336`, GLM `:8090`) no longer shows **IN-FLIGHT** from *global* GPU busyness. Previously every loaded sidecar was marked `busy = gpu_busy` (any GPU >20% util), so an idle resident sidecar looked "in-flight" whenever *anything else* used the cards — the owner spotted gemma4-vision falsely in-flight while another workload's eval was using the GPUs. Now `_sidecar_busy(port)` checks for an **ESTABLISHED TCP connection on the sidecar's own port** (a client mid-request) — the honest per-sidecar signal — cached ~2s, safe-False on error. Ollama-model in-flight detection (real `/api/ps` + gpu_busy) is unchanged. New tests: idle sidecar stays not-busy under `gpu_busy=True`; a sidecar with a live request shows busy even when the GPU is idle.

## v3.38 — 2026-07-13 — Codex-PC-Link disabled (owner-parked)
- **[change]** The **Codex-PC-Link** bridge (v3.37) is **disabled** by owner request — codex remote only works desktop-client↔desktop-client, so the SSH-tunnel link isn't usable for now. Added an `enabled` flag to `~/.fleet_tui/codex_link.json` (now `false`); when disabled, `codex_link.read_status()` returns `state:"disabled"` and **short-circuits with zero ss/curl probing**, and the `bridges:` line **omits the codex segment entirely** (not shown as a dead "off"). Flip `enabled:true` to restore it later. New tests: `test_disabled_short_circuits` (no probing when disabled) + `format` bridges omit-when-disabled/show-when-up. Test suite made hermetic via an autouse enabled-config fixture (older codex_link tests no longer read the real config).

## v3.37 — 2026-07-13 — Codex-PC-Link bridge status
- **[feature]** The **HEALTH** panel's `bridges:` line now shows the **Codex-PC-Link** status alongside PC + Telegram: `codex↔WinPC ●up/○down/—off`. It reflects Fleet's SSH tunnel to the Windows box's codex app-server — **up** = the forwarded port is listening AND the app-server `/readyz` returns 200; **down** = tunnel up but app-server not ready; **off** = tunnel down. Pure read-only status (never opens/closes the tunnel). Source `sources/codex_link.py` (headless, subprocess-cached ~12s per the refresh-loop rule); config `~/.fleet_tui/codex_link.json` (`{port, host_label}`, defaults 4500/WinPC). 6 hermetic tests. Existing `bridges:` assertions unaffected (substring checks).

## v3.36 — 2026-07-13 — Research Playlists panel (replaces FOCUS on the main view)
- **[feature]** New **RESEARCH PLAYLISTS** panel above HEALTH on the Fleet tab. One clickable row per
  research playlist (default: the owner's "AI Stuff" YouTube playlist). Clicking `▶ check <name>` writes a
  check-request intent to `~/.fleet_tui/research_requests/` + fires a Telegram confirmation — the TUI does
  NOT run the check (not an orchestrator); the request surfaces to Claude, who runs the check→stage flow.
  Files: config `~/.fleet_tui/research_playlists.json`; source `sources/research_playlists.py` (pure readers
  + thin request-writer, mirrors `dispatch.py`); formatter `format_research_playlists`; new `Playlist`
  model; last-checked stamped in a sidecar state file. 6 hermetic tests.
- **[change]** The **FOCUS** panel is removed from the main Fleet view (per owner: hidden unless accessed via
  the menu). Focus is functionally unchanged — the `f` key still toggles focus mode, and the focus modal
  (palette/help) now shows the live CURRENT status. Updated the two integration tests that queried `#focus`.

## v3.35 — 2026-07-11 — in-flight modal: label sidecars as SERVICES, not "no linked dispatch"
- **[fix]** Owner-reported: the v3.34 in-flight modal's **▶ watch output** button never appeared and every
  local row read **"busy — no linked TUI dispatch"**. Root cause was a DESIGN GAP, not a matching bug — the
  join was verified correct end-to-end (a real `fleet-model-dispatch <model>` TUI dispatch DOES link, cmd →
  `dispatch.recent()` → non-None `base` → watch button, for local AND cloud). The real problem: the
  persistent **vision sidecar** `gemma4-e4b-q4-k-m (:8336)` (and the GLM `:8090` sidecar) are loaded
  ModelStates flagged `busy` by the GLOBAL gpu-util flag, so they entered the list, could never match a
  dispatch, and got mislabeled as stalled tasks — dominating the modal so no `base`/watch ever showed.
- **[fix]** `sources/inflight.build_inflight` now detects a sidecar (name ends `(:<port>)`) and emits a
  `kind:'service'` entry with an accurate label (`vision service (:8336)` / `GLM service (:8090)` / generic
  `llama-server service (:<port>)`) — never "no linked TUI dispatch". The genuinely-untracked busy local
  fallback copy is now accurate: **"loaded · GPU active · no linked TUI dispatch"** (busy is a global flag,
  not proof this model is computing a task). Real dispatch-linked rows (local + cloud) still carry `base` and
  get the ▶ watch button.
- **[feat]** `InFlightTasksModal` renders a service row distinctly (⚙ glyph, no "→ dispatch" arrow, a
  "persistent service — always loaded; no task to watch" note) so it never implies a watch button.
- Local-lane (`aider-edit`/qwen3-coder) wrote the `sources/inflight.py` logic; Claude authored the spec, the
  non-gameable test gate, adjudicated, and did the `app.py` wiring. Full suite green (7 new inflight cases) +
  live-rendered demo confirming the sidecar-as-service label and a watch button on a real dispatch.

## v3.34 — 2026-07-11 — click an in-flight model → see what task it's working on
- **[feat]** Owner ask: clicking the MODELS panel body now opens an **IN-FLIGHT** modal showing what each
  busy model/leg (local OR cloud) is working on — the dispatch **title** it was given + its full brief (the
  inline cloud line truncates at 60 chars; this shows it whole). A dispatch-linked row gets a **▶ watch
  output** button (opens the live output). New pure join `sources/inflight.build_inflight` correlates a busy
  local model to its dispatch by the target in its cmd (`fleet-model-dispatch <model>`); a busy model with
  NO linked TUI dispatch is reported HONESTLY (no fabricated title — gpu-busy is global). When nothing is
  in-flight, the body click still opens the on-disk inventory (also on `m`). `dispatch.recent()` now exposes
  `cmd`; `cloud_legs` exposes the dispatch `base`. 298 tests green (11 new) + live-verified.

## v3.33 — 2026-07-11 — sidecars report REAL VRAM + keep their family color (fix the "gray + 0GB")
- **[fix]** The owner saw the gemma4 vision sidecar as **gray at 0GB**. Two causes, both fixed: (1) the new
  `_sidecar_vram_mb` reader took only the FIRST `nvidia-smi` compute-app row, but a sidecar spanning BOTH
  cards lists the same pid twice (live :8336 = 148 + 146 MiB) → it now **sums all rows for the pid(s)**
  (0.1→0.3GB, ~½ undercount gone); (2) the port-tagged name `gemma4-e4b-q4-k-m (:8336)` overflowed the
  kanban's 30-col cell, so `_pad_markup` dropped the family color + truncated the GB → `_clean_model_name`
  now **strips the redundant GGUF dash-quant when a `(:port)` suffix follows** (`gemma4-e4b (:8336)`), so it
  fits and stays **[green]** with `0.3GB` shown. ollama colon-quants (`:Q4_K_M`) untouched.
  Local-lane first-pass VRAM reader, Claude-adjudicated/hardened + gated (multi-GPU-sum + fit/color tests).
  Full suite 287 green + live-verified. (owner-reported)

## v3.22 — 2026-07-07 — fix ssd2 temp clipping
- **[fix]** The POSTURE panel shrank HEALTH's vertical share so the 2nd SSD temp (ssd2) clipped below the
  fold. Combined cpu·ssd·ssd2 onto one dense row + HEALTH 2fr→3fr so all sensors stay visible. (owner-reported.)

## v3.32 — 2026-07-11 — show llama-server sidecars as loaded local models
- **[fix]** The models panel only read ollama /api/ps, so the gemma4 vision snap (`llama-server` on :8336)
  and the GLM :8090 sidecar were invisible — the owner saw sustained GPU utilization with "no local models
  loaded". Added `read_sidecars()` (queries :8336/:8090 /v1/models, safe []-on-error) + a backward-compatible
  `sidecars` arg to `build_model_states`; sidecars now show as loaded (`<id> (:<port>)`, busy-flagged).
  Local-lane built, Claude-gated (8 tests) + live-verified. Full suite 281 green. (owner-reported)

## v3.31 — 2026-07-08 — detect cloud legs by real process name (kimi = `kimi-code`)
- **[fix]** A running kimi leg was invisible in the MODELS ☁ CLOUD section — the kimi CLI runs as process
  `kimi-code`, but `external_cloud_procs` did `pgrep -x kimi` (exact name). Added `SESSION_PROCS` mapping
  each leg to its real process names (kimi → `kimi`+`kimi-code`); now a live kimi leg shows. (owner-reported;
  local-lane built, Claude-gated + live-verified against a running kimi. 26 cloud-legs tests, full suite green.)

## v3.30 — 2026-07-08 — cloud legs: don't count the orchestrator + show "Claude (model)"
- **[fix]** The interactive orchestrator (this session's bare `claude`) was being shown as a running
  "claude (session)" — because "claude" was added to the shared markers that `external_cloud_procs` pgreps.
  Split out `SESSION_MARKERS` (codex/grok/kimi, no claude) so a bare `claude` is never counted as a leg.
- **[feat]** Running Claude legs now display with the model tag: **`Claude (Opus 4.8)` / `Claude (Sonnet 5)`**
  (dispatch legs) and `Claude (<model>) · worker` (external `claude -p` workers) — so you see which model is
  in use, only when a leg is actually running. (local-lane built, Claude-gated + adjudicated. 269 tests.)

## v3.29 — 2026-07-07 — MODELS panel shows running Claude worker legs + model
- **[feat]** The ☁ CLOUD sub-section now surfaces running **Claude worker legs** and *which model*:
  `claude-opus`/`claude-sonnet` dispatches show by leg name; external `claude -p` workers show as
  `claude <Opus 4.8|Sonnet 5|Haiku 4.5> (worker)` (model parsed from `--model`). Extends
  `sources/cloud_legs.py` (local-lane built, Claude-gated).
- **[fix]** Worker detection is **token-based** (`-p`/`--print` as a standalone token) — a substring check
  false-flagged the orchestrator (whose cmdline carries `--json-path`/`--spawned-by`). Adjudication catch;
  regression-tested. 266 tests green.

## v3.28 — 2026-07-07 — register Claude worker legs in the dispatch registry
- **[feat]** Added `claude-opus` (Opus 4.8) + `claude-sonnet` (Sonnet 5) subscription worker legs to the
  dispatch registry (`sources/targets.py`) so they're selectable in the dispatch box; also registered in
  the model-delegation routing skill with a ToS/volume guardrail. (Haiku: no standing leg — free local
  owns the cheap lane.)

## v3.27 — 2026-07-07 — right-click closes any pop-up; curation log ordered by date
- **[feat]** Right-click anywhere in a modal now **closes it** (new `FleetModal` base — every pop-up
  inherits it), so the whole TUI is operable with just the mouse (left-click acts, right-click closes) —
  no reaching for Esc. Left-click still leaves interactive modals open.
- **[fix]** The curation log showed passes in file order (not chronological) and surfaced two malformed
  ledger entries (the `PASS 0 system-init` marker + a `<ISO timestamp>` format-template) at the top. Now
  it sorts by **date, newest first**, and skips non-dated entries. (Duplicate pass *numbers* like two
  `PASS 63` are a ledger-data quirk — reused counters between NO-OP cron passes and CHANGE passes — not a
  display bug; date-ordering makes it read correctly regardless.)

## v3.26 — 2026-07-07 — curation log + trigger, alert hand-off, Ops-click fix
- **[fix]** Clicking a task in the Ops list was **broken** whenever a filter was active — the click
  handler indexed the raw `_data['ops']` while the panel renders the *filtered* list, so it selected the
  wrong row. Now it indexes the same visible (filtered) list. Regression-tested. (owner-reported)
- **[feat]** **Curation log** (key **`C`** / palette): a `CurationModal` showing recent curation passes
  (pass #, date, CHANGE/NO-OP, headline + what each applied) from `CURATION_LEDGER.md`, plus a
  **▶ Trigger curation pass** button that flips the gated `.trigger` to pending so the next orchestrator
  turn runs a full pass. New pure `sources/curation.py`. (codex research wave #8.)
- **[feat]** **Alert hand-off** — every pending INBOX item gets a **▶ hand off** button that routes the
  alert to Claude/whoever's responsible: it queues the alert to `~/.claude/curation/.action_requests`,
  which the `curation_reminder` hook now drains into the next orchestrator turn so Claude actions/routes
  it. New pure `sources/actions.py` (deduped queue). Ack now also covers automation/hive/backup/supply.
- **[infra]** New gates `tests/test_curation_source.py`, `tests/test_actions.py`, + Ops-click regression;
  254 tests green; modals + hand-off pilot-verified.

## v3.25 — 2026-07-07 — Ops keyboard navigation (finishes roadmap wave 6c)
- **[feat]** The Ops master-detail is now fully keyboard-drivable: **`j`/`k`** (or **↑/↓**) move the
  selection through the visible task list, **Enter** drills into the selected item (dispatch → output
  modal, job/cron → detail modal). All three are no-ops off the Ops tab, so the keys stay free elsewhere
  and never steal Enter from buttons/inputs. Nav respects the active `F` category + `/` text filters.
- **[infra]** Gated in `tests/test_ops_keynav.py` (nav clamps, filter-aware, tab-scoped, Enter drill);
  live keypress-verified. 246 tests green. This closes the last deferred TUI roadmap item — **all
  8 waves + 6a/6b/6c shipped.**

## v3.24 — 2026-07-07 — Trends axis labels
- **[qol]** Both Trends plots now label their axes: y-axis `util %` / `°C`, x-axis
  `← older   samples (~1s each)   newer →` so the time direction + units are explicit at a glance.

## v3.23 — 2026-07-07 — inbox HF summary + screenshot legibility
- **[fix]** INBOX no longer floods with the raw HF-watch digest (stray JSON + markdown). `hf_item` now
  renders a **clean summary** — latest scan header + a bullet list of the model names flagged for eval —
  never the raw `### SIGNAL`/```json``` dump. (Regression from the v3.12 HF-path fix; owner-reported.)
- **[fix]** Screenshots (`s`) were "jagged": the Trends plots used plotext **braille** markers (U+2800),
  which no common monospace font has → every plot point rendered as a tofu box. Switched to `marker="sd"`
  (block-element chars ▀▄▚ that Fira Code / DejaVu do cover). Also `_tighten_svg` now injects
  **`DejaVu Sans Mono`** into the SVG font fallback (Fira Code wasn't installed → offline viewers fell
  back to a font without box/block glyphs). Result: clean crisp plots + borders in any viewer.



## v3.21 — 2026-07-07 — phone/browser view (wave 8)
- **[feat]** `./serve.sh` (`fleet_tui/serve.py`, `textual-serve`) serves the TUI over HTTP so it opens in
  a **browser — including the phone on home wifi** at `http://<fleet-LAN-ip>:8011`. Each browser session
  spawns its own `python -m fleet_tui`, so it's the same monitor, just reachable.
- **[infra/security]** Binds `0.0.0.0:8011` (so the phone can reach it) but **firewall-scoped** with the
  houselan-fw pattern to loopback + PC-link (192.0.2.0/24) + home-LAN (198.51.100.0/24), DROP for
  anything off the home network — verified reachable on the LAN IP, never public; persisted to
  `/etc/iptables/rules.v4`. Host/port env-overridable. 242 tests green. (Roadmap wave #8, owner-approved
  "both" = loopback + home-LAN.)

## v3.20 — 2026-07-07 — Trends tab: live GPU/CPU sparkline plots (wave 7)
- **[feat]** New **Trends** tab with two live `textual-plotext` charts — **utilization %** (gpu0/gpu1/cpu,
  0-100) and **temperature °C** — over a rolling ~90-sample window (1 sample/refresh). Given the freeze
  history, watching util/temp climb is genuinely useful. Plots redraw only while the Trends tab is active
  (and on switch-to), so cost stays near-zero elsewhere.
- **[infra]** `textual-plotext` **1.0.1 confirmed compatible with our Textual 8.2.8** (grok's "stale 2023"
  info was outdated — a fresh release exists); smoke-tested import+mount+render before integrating.
  Added `textual-plotext>=1.0` + `pyte>=0.8` (embedded terminal, was undeclared) to `pyproject.toml`.
  History sampling never touches the cosmetic frame path. 239 tests green; live plot render verified.

## v3.19 — 2026-07-07 — declutter the footer legend
- **[qol]** The bottom hotkey legend overflowed (19 bindings → some never showed). Now only the 7
  highest-traffic keys are visible in the footer (`? d i p / q Ctrl+\``); every other key stays fully
  active but hidden from the footer — the complete reference lives in the `?` help overlay and the
  Ctrl+P command palette. Converted the binding list to `Binding(..., show=False)` form.

## v3.18 — 2026-07-07 — CPU load %, and cosmetics wired into the new sections
- **[feat]** HEALTH CPU line now shows a **load %** beside the temp (`cpu 67°C 10%`), matching the GPU
  util format + the same `_pct_color` bands. Dependency-free `/proc/stat` delta reader (no subprocess).
- **[feat]** Cosmetics now cover the v3.12–3.17 additions: new **`posture`** per-panel animation gate
  (the POSTURE `● attn` chip breathes when there's attention, toggle in the `c` menu) + a new recolorable
  **`attn`** color slot driving the POSTURE alert/CRITICAL markup + the attn chip. Cosmetics menu builds
  its panel-animation rows dynamically from `CATS` (so future panels auto-appear).
- **[fix]** Header attention counter kept plain-text — confirmed the Header sub-title renders Rich markup
  literally, so no `[color]` tags there (documented inline).
- **[infra]** 239 tests green; cosmetics-defaults + menu-render verified live.

## v3.17 — 2026-07-07 — filter-as-you-type on the Ops list
- **[feat]** Press **`/`** → jump to Ops + focus a live filter box; typing narrows the task list by
  case-insensitive substring over title/detail/id (compose with the existing `F` category filter). The
  active filter shows as a cyan `/needle` chip in the TASKS title; `Enter` releases focus so single-key
  bindings work again. `sources/ops.filter_ops()` gained an optional `text=` arg (pure, tested).
  (Roadmap wave #6b; grok's "filter-as-you-type" — the signature ops-TUI pattern both legs flagged.)
- **[infra]** Text-filter logic gated in `tests/test_stale_and_filter.py`; 239 tests green; live pilot
  confirmed `/` focuses the box, switches tabs, and the title chip renders.

## v3.16 — 2026-07-07 — Ctrl+P command palette = full fleet-action surface
- **[feat]** Broadened the Ctrl+P command palette from 3 focus entries to a **16-command
  fuzzy-searchable control surface** (`FleetCommands` provider): inbox, passback, alerts, failures,
  dispatch, jobs, model warm/unload/inventory, ops filter, refresh, terminal, screenshot, focus,
  cosmetics — each with a one-line description. Every entry invokes an *existing gated action* (the
  palette adds discovery, not capability), so nothing has to be memorized — especially valuable on
  phone / textual-web. (Roadmap wave #6a; grok landscape #1.)
- **[infra]** `FocusCommands` → `FleetCommands`; integration test broadened to assert the full set
  discovers with help + fuzzy-search still resolves focus. 238 tests green.

## v3.15 — 2026-07-07 — WinClaude passback inbox + header attention counter
- **[feat]** New `sources/passback.py` + **PassbackModal** (key **`p`**): WinClaude→Fleet passback files
  (the two peer-message globs) newest-first with an unread `●` marker;
  opening the modal marks them seen. Seen-state lives in `~/.fleet_tui/passback_seen.json` — the passback
  files themselves are never touched. (Roadmap wave #5.)
- **[qol]** **Header attention counter** — the Header sub-title now shows a single-glance
  `⚠N partialN fbN pbN` (alerts / degraded dispatches / feedback-due / new passback), or `✓clear` when
  nothing's pending, before the theme name. Updates every refresh. (Roadmap wave #4 / codex QoL #1.)
- **[infra]** passback source local-lane-built + Claude-gated (`tests/test_passback.py`); wiring +
  counter Claude-authored (`tests/test_passback_wiring.py`). Caught + fixed a `w`-key collision
  (warm-model already owned it → passback moved to `p`). 238 tests green; live pilot verified.

## v3.14 — 2026-07-07 — POSTURE panel (backup / supply-chain / upstream)
- **[feat]** New Fleet-tab **POSTURE** panel (new `sources/posture.py` + `format_posture()`): last-good
  backup (repos + mirror), last-abort reason, latest supply-chain scan (alerts/hooks/new), and upstream
  drift (count behind + CRITICAL items with local→latest). Title shows a `● attn` chip when a backup/supply
  alert is pending or an upstream CRITICAL is behind; body click opens the INBOX to clear the alert.
- **[infra]** Source built by the local lane (qwen3-coder), Claude-authored gates
  (`tests/test_posture.py` + `tests/test_format_posture.py`), Claude adjudicated (hardened `_read_json`
  against valid-but-non-dict JSON per the never-crash contract) and wired the panel. 231 tests green;
  live-render verified against the real ledgers. (Roadmap wave #3.)

## v3.13 — 2026-07-07 — dispatch PARTIAL/degraded status is first-class
- **[feat]** `sources/dispatch.py` now detects the `<out>.PARTIAL` marker the cloud-leg wrappers
  (codex-fleet / grok-dispatch) drop when a run produced output but the worker exited rc≠0. `recent()`
  and `full_output()` carry a `partial` bool; a degraded run renders `⚠ PARTIAL (rc≠0 — UNVERIFIED)`
  in the dispatch list and a full banner in the output modal — no longer silently shown as `✓ done`.
- **[infra]** Marker convention confirmed against the live wrappers + dispatch dir before locking the
  parser (both `<base>.out.PARTIAL` and a bare `<base>.PARTIAL` are accepted; presence is the signal).
  Claude-authored gate `tests/test_dispatch_partial.py`; 221 tests green. (codex research leg wave #2.)

## v3.12 — 2026-07-07 — INBOX surfaces ALL fleet alert channels
- **[feat]** `sources/inbox.py` now reads every fleet alert channel, not just 2 of them: the new
  `.automation_alert` fail-loudly channel (JSON-lines), `.backup_alert`, `.supply_chain_alert`,
  `.hive_drift_alert`, and `.telegram_trigger` — surfaced crit-class first. Closes the gap where a
  week of new fail-loudly plumbing was invisible to the always-on monitor. (Both TUI research legs
  independently ranked this the #1 gap.)
- **[feat]** Generic per-source `ack()` extended: alert files truncate, JSON triggers flip
  `pending=false`; `telegram` is deliberately read-only (Claude owns clearing that trigger).
- **[fix]** `HF_DIGEST` pointed at a non-existent path (`~/fleet_optests/HF_WATCH_DIGEST.md`);
  repointed to the live `~/.claude/curation/HF_WATCH_DIGEST.md`, so the HF-watch inbox item
  works again. (Found by the codex research leg.)
- **[infra]** Built via the local-lane loop (Claude spec + Claude-authored pytest gate →
  qwen3-coder → adjudicate → wire); 216 tests green, live-smoke verified.

## v3.11 — 2026-07-06 — MODELS inventory modal
- **[feat]** Click an idle MODELS panel → installed-models inventory modal.

## v3.10 — 2026-07-06 — dispatch Clear-done
- **[qol]** Dispatch box: "Clear done" button; fixed recents-list crowding.

## v3.8–3.9 — 2026-07-04 — embedded terminal + control-plane
- **[feat]** Embedded `pyte`-backed PTY terminal, `Ctrl+`` toggle (replaces the retired tmux bottom pane).
- **[fix]** Empty-terminal regression — a leftover `compose()` child masked `render()`; removed it,
  added a regression guard (`tests/test_terminal.py`).
- **[infra]** `run.sh`/`restart.sh` retired tmux respawn — TUI runs directly so relaunch always loads
  fresh code (fixes the stale-code-pinned-across-relaunch problem).
- **[feat]** Model warm button (7c); dispatch presets as one-click buttons (7b); hermetic-tests conftest.
- **[fix]** Stale-run detection wired; the Ops filter (was a latent no-op) now works.

*(Earlier history — fleet CLI backlog #39–42, first-cut embedded terminal — predates this changelog;
see `git log` in the repo. This file is the going-forward record from v3.12 onward, with the recent
waves backfilled for continuity.)*
