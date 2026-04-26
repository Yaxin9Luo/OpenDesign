# Roadmap

Versions are intentionally small and shippable. The **v1.0 launch** is the next significant milestone — everything below is ordered against that bar. Post-launch work continues in v1.x.

When you complete a version, move it to "Shipped" with the date.

---

## Shipped

### v0 — layered poster pipeline research prototype (2026-04-17 → 2026-04-18)

Original code-name: **Design-Agent**. End-to-end agent: one-line brief → text-free background (NBP) + N text layers (Pillow) → PSD with named pixel layers + SVG with real `<text>` + flat preview + structured `Trajectory` JSON. Anthropic SDK + handwritten tool loop, OpenRouter + Anthropic stock backends, 7 tools, 6-step no-API smoke test, 2-iter critic loop.

Validated with two real runs:
- 5-layer 国宝回家 poster (100 s, $1.41, score 0.86)
- 18-layer CVPR academic poster (196 s, $2.49, score 0.86)

**Positioning at the time**: research prototype for Longcat-Next training-data capture. Pivoted 2026-04-18 to **OpenDesign** open-source product — trajectory machinery preserved as internal session state; no longer the primary pitch.

### v1.0 — OpenDesign public MVP (2026-04-18 → 2026-04-20, **code complete**)

Three-artifact conversational design agent, MIT-licensed, `pip install open-design`-able. All 10 code items (#1 – #8.75) shipped across the commit series below; the only remaining launch-blocker is a screencast demo video (non-code, item #10 in [V1-MVP-PLAN.md](V1-MVP-PLAN.md)).

| # | Item | Commit |
|---|---|---|
| 1 | **KB/docs pivot** (narrative → open-source product) | 2026-04-18 docs batch |
| 2 | **Package rename** `design_agent` → `open_design` | [`ffd4389`](https://github.com/Yaxin9Luo/OpenDesign/commit/ffd4389) |
| 3 | **`switch_artifact_type` tool** + `ArtifactType` enum + `DesignSpec.artifact_type` | [`21dc44f`](https://github.com/Yaxin9Luo/OpenDesign/commit/21dc44f) |
| 4 | **CLI chat shell** — multi-turn REPL, 8 slash commands, `ChatSession` persistence (absorbs item #7) | [`03517ba`](https://github.com/Yaxin9Luo/OpenDesign/commit/03517ba) |
| 5 | **`edit_layer` tool** — targeted subset-diff onto a single text layer (`text / font / fill / bbox / effects / …`); re-renders just that PNG, no implicit composite | [`eabaab9`](https://github.com/Yaxin9Luo/OpenDesign/commit/eabaab9) |
| 6 | **HTML renderer** (poster) + in-browser edit toolbar (drag handle, font / size / color / family inputs, Save → download) | [`0937428`](https://github.com/Yaxin9Luo/OpenDesign/commit/0937428) |
| 6.5 | **`apply-edits` CLI** — round-trip edited HTML back into new PSD / SVG / HTML / PNG run with parent_run_id lineage | [`695f200`](https://github.com/Yaxin9Luo/OpenDesign/commit/695f200) |
| 7 | **PPTX renderer** (deck) — native `TextFrame`s, per-slide PNG thumbs + grid preview, `python-pptx` 1.0.x | [`ecdea54`](https://github.com/Yaxin9Luo/OpenDesign/commit/ecdea54) |
| 8 | **Landing mode** — section-tree schema + flow-layout HTML renderer, `LayerKind += "section"` | [`adea66b`](https://github.com/Yaxin9Luo/OpenDesign/commit/adea66b) |
| 8.5 | **6 bundled design systems** — minimalist / editorial / claymorphism / liquid-glass / glassmorphism / neubrutalism. All CSS inlined, zero external deps. | [`c16a7f7`](https://github.com/Yaxin9Luo/OpenDesign/commit/c16a7f7) |
| 8.5-fix | **Critic text-only for landing** — grades section tree + copy quality instead of a lossy Pillow preview | [`64522f9`](https://github.com/Yaxin9Luo/OpenDesign/commit/64522f9) |
| 8.75 | **Landing NBP imagery** — `generate_image` tool (10th), `LayerKind += "image"`, per-style imagery prompts, `<figure>` inline in HTML | [`9b6b6d0`](https://github.com/Yaxin9Luo/OpenDesign/commit/9b6b6d0) |
| 9 | **README + docs** — product-facing quickstart, feature matrix, paper2any walk-through; KB kept current through v1.2.5 | ongoing |

**Dogfood validation**:
- 国宝回家 poster (v0 baseline): 100 s / $1.41 / score 0.86 · CVPR academic poster: 196 s / $2.49 / score 0.86
- OpenDesign launch poster (2026-04-18): 5 min / $3.74 / 5 layers / pass 0.82 (2-iter critique loop)
- 茉语 milk-tea landing (claymorphism, 2026-04-19): 207 s / $2.20 / 5 NBP images across 4 sections / pass 0.94

**Remaining launch blocker**:
- **#10 demo video** — screencast of a multi-turn session producing all 3 artifact types. Non-code, requires recording + narration.

**Deferred from v1.0 to v1.x** (scope kept tight):

- Multi-image insets → shipped inside v1.0 #8.75 (landing) + v1.1 `ingest_document` (paper figures).
- Real PSD type layer → **v1.4**.
- Brand Kit PDF parsing → **v1.5**.
- Skill sedimentation → **v1.6**.
- Font generator / custom fonts → later.

---

## v1.1 — paper2any foundation ✅ SHIPPED 2026-04-20 (commit `dc93960`)

- `ingest_document(file_paths: list[str])` tool: dispatches on `.pdf` / `.md,.txt` / `.png,.jpg,.jpeg,.webp`.
- CLI `--from-file <path>` and chat `:attach <path>` entry points.
- Poster-mode `kind="image"` layer + bbox hydration so ingested PNGs composite into PSD / SVG / HTML.
- Sonnet-4.6-default ingest model + 10-min HTTP timeout + graceful bbox-locator fallback.
- 43-page / 17 MB Longcat-Next paper verified end-to-end.

## v1.2 — paper2any production ★ ✅ SHIPPED 2026-04-21

Four commits over one work session closed the remaining gaps between "ingest runs" and "output worth shipping":

### v1.2.0 — pymupdf-native figure extraction + Qwen-VL-Max ingest (commit `ce50f2a`)

- **Dropped the Claude-Sonnet vision locator.** It produced half-page screenshots, clipped diagrams, and hallucinated "figures" on text-only pages. Replaced with pymupdf native:
  - `doc.extract_image(xref)` for embedded rasters → native-resolution PNGs (e.g. Fig. 1 histograms 1890×1211 instead of the old pipeline's text-contaminated 1454×820).
  - `page.get_drawings()` + proximity clustering + 300 dpi render for vector diagrams.
  - Zero LLM guessing for localization.
- **Default ingest_model = `qwen/qwen-vl-max`** via OpenRouter (~5× cheaper than Sonnet 4.6 for "read paper + match captions"). VLM only handles structure extraction + per-figure caption matching + fake-figure filtering (logos / page headers / equation renders).
- New `open_design/util/vlm.py` dispatches between Anthropic SDK (Claude path) and OpenAI SDK (Qwen path) by model id. Planner / critic stay 100 % on Anthropic SDK so tool_use protocol is untouched.
- `runner._materialize_layer_graph` skips layers with `bbox=None` so planner-unplaced ingest candidates don't crash trajectory serialization.

### v1.2.1 — `kind="table"` layer + native table rendering (commit `da664a5`)

- **New LayerKind `"table"`** with structured `rows / headers / caption / col_highlight_rule` fields on `LayerNode`. Ingest registers one `ingest_table_NN` layer per VLM-validated data table.
- **Renderers**:
  - **Deck** → native `slide.shapes.add_table(…)` — live editable PPTX table with column-width autoscale, font-size autoscale, winner-cell bolding.
  - **Landing** → real `<table>` with header styling + zebra rows + `.ld-table-winner` class.
  - **Poster / PSD / SVG** → PIL-drawn PNG via `util/table_png.render_table_png` with deep-green winner text (bundled Noto SC is bold-only so color carries the highlight).
- Extraction: `util/pdf.extract_table_candidates` uses `page.find_tables()` for localization only (cell splits unreliable); VLM reads the bbox image + pymupdf's raw-cell guess and returns clean rows + `col_highlight_rule: ["", "max", "max", "min", …]`.
- Refinements: `dedup_tables_against_figures` (drop candidates ≥ 70 % contained in vector-figure bboxes), CJK-capable PIL font loader, multi-row-header flattening prompt ("Parent / Child" pattern).

### v1.2.2 — poster visual-density rules (commit `a08bbb9`)

Fixed the "1 figure + 3 columns of body text = research-paper PDF printed as PNG" anti-pattern.

- **Ingest tool_result summary** now lists top-20 figure candidates ranked by `(has_caption, is_vector, min_side_px, -page)` with `(page, size, strategy, caption)` per figure. Tables listed alongside with shape + caption. Planner can pick deliberately instead of referencing `fig_01` and moving on.
- **Planner prompt** gains "Poster workflow (paper2poster) — visual-density rules" section: figure-count floor (≥4 when ≥5 available), figure diversity (system diagram + qualitative + quantitative), min 600 px shorter side per figure, text density caps (≤30 words per body layer, ≤8 words per title).
- **Critic rubric** rebalanced to add `visual_density` criterion (weight 0.20). Penalties: −0.40 if ≤1 figure placed despite ≥3 available (blocker), −0.25 if <3 placed when ≥5 available, −0.15 if registered table missing, −0.20 if image-area below 45 % of canvas, −0.15 if table height < 400 px.

### v1.2.3 — composite aspect-preserve (commit `349c899`)

When the planner still under-specs a bbox, the composite layer now degrades gracefully instead of squishing:

- **Images**: contain-fit / letterbox via `_aspect_fit_contain` — matches HTML `object-fit: contain` behavior across PSD / SVG / preview paths. SVG gains `preserveAspectRatio="xMidYMid meet"`.
- **Tables**: re-rendered at the planner's exact bbox dims during composite (`render_table_png(width_px=bw, max_height_px=bh, …)`), so font autoscale + row-truncation fires at the actual size. A 14-row table in a 900×110 strip used to LANCZOS-squish to 13 px per row; now the PNG is regenerated at bbox and rows drop until the remaining fit at legible font.
- New `composite.bbox_aspect_warning` diagnostic log fires when bbox aspect ≥ 2× source aspect (for planner-prompt tuning data).

### v1.2 end-to-end verification (longcat-next-2026.pdf, 43 pages, 17 MB)

| artifact | v4 baseline | v7 with all v1.2.x fixes |
|---|---|---|
| **poster** | 1 figure, critique 0.62, ~12 % image area | 5 figs + 1 table, critique 0.86, ~58 % image area |
| **landing** | — | 1 table + 3 section figures, critique pass 0.92 |
| **deck** | — | 12 slides, 15×12 editable PPTX table with 8 bolded winner cells, pass 0.88 |

Smoke **16/16** green. Tool count unchanged at 11.

### v1.2.4 — text-overlap detector + figure cross-reference enforcement (2026-04-21)

Closed two of the four parked paper2any polish items in one pass.

- **Composite text-overlap detector** (`open_design/tools/composite.py`) — new `_detect_text_overlaps` + `_effective_text_extent` helpers compute each `kind: "text"` layer's glyph-inclusive vertical footprint (`max(bbox.h, font_size_px × 1.20)`) and flag every colliding pair. Emits `composite.text_overlap_warning` log events AND appends a ⚠ line to the tool_result summary so the planner sees the collision on the next turn without waiting for a full critique pass. Caught the cached `L_title`↔`L_sub` descender crash on the 2026-04-21 longcat-next run (y_overlap=16 px).
- **Figure ↔ text cross-reference detector** (`_placed_ingest_display_map` + `_detect_missing_figure_xrefs`) — assigns display numbers (Fig. 1 / Table 1 / …) in placed-layer reading order, then checks every text layer's `.text` for `Fig. N` / `Figure N` / `Table N` literals (case-insensitive, period-optional). Orphan figures surface in the composite tool_result + log.
- **Planner prompt** (`prompts/planner.md`) gains two poster-workflow rules: "Text-layer vertical rhythm" (descender clearance geometry + stacked-layer spacing formula + mixed-script concrete template) and "Figure ↔ text cross-reference" (display-number scheme + citation patterns + `edit_layer` fix recipe).
- **Critic rubric** (`prompts/critic.md`) adds typography `−0.15` per text-text bbox collision pair (all posters, not just paper) AND visual-density `−0.10` per orphan figure (capped at −0.30) with `major` severity.

Both detectors are deterministic — they run BEFORE the critic so the planner can self-correct in one iteration instead of burning a critic round. Non-paper posters skip the cross-reference check (no placed `ingest_*` layers → empty display map → early return).

### v1.2.5 — .docx / .pptx ingest + scanned-PDF OCR fallback (2026-04-21)

Closed the last three parked paper2any gaps in one pass.

- **`.docx` branch** (`_ingest_docx` in `ingest_document.py`): reads Heading 1/2/Title styled paragraphs to build a section tree, extracts embedded images via `doc.part.rels` into `ingest_fig_NN` layers. No VLM call — Word's structural metadata is faithful enough that we can build the manifest directly (faster, free).
- **`.pptx` branch** (`_ingest_pptx`): each slide becomes one manifest section (title placeholder → heading, body placeholders → summary + key_points); picture shapes → `ingest_fig_NN` layers. Reuses the existing `python-pptx` dep (we already had it for writing decks).
- **Scanned-PDF OCR fallback** (`_ocr_scanned_pdf`): when `detect_scanned_pdf` returns True, render each page at 200 dpi and run Qwen-VL-Max OCR in parallel (6 workers) to rebuild `page_texts`; figure / table candidates skip (scanned PDFs have no separable embedded rasters). Structure extraction then runs as normal.
- New shared helper `_register_image_blob` allocates sequential `ingest_fig_NN` ids so docx/pptx images show up to the planner the same way PDF figures do — the figure-cross-reference detector in composite picks them up with zero changes.
- Dependency add: `python-docx>=1.1.0`. Dispatcher extended with `.docx` / `.pptx` extensions + planner prompt mentions the new formats.
- Smoke: two new steps (`check_ingest_document_docx`, `check_ingest_document_pptx`) build a minimal fixture, run ingest end-to-end, assert section tree + embedded figure. Suite now 18/18.

### Remaining paper2any gaps (parked for v1.3+)

- Multi-paper fusion (cross-paper figure reuse + ingest cache).

---

**North Star.** Today a brief is a single text line. The ideal OpenDesign: user drops in a paper / PDF / docx / markdown / image bundle → we generate the matching poster, landing page, or slide deck → user iterates through the in-HTML edit toolbar and `apply-edits` round-trip. This is the **paper2poster / paper2page / paper2deck** surface. Not a side feature — *this is the product's end state*. v1.0 ships the single-brief story; v1.1 closes the loop.

The round-trip editability guarantee (v1.0 #5 / #6 / #6.5) extends through ingestion: every extracted section, figure, and heading lands as a named `LayerNode`, so the user can reorder / retype / recolor after generation exactly like a single-brief artifact. Ingestion adds an input surface, it does NOT break downstream edits.

**Scope** (~6-8 h):

- **New tool** `ingest_document(file_paths: list[str])` — accepts PDF / DOCX / MD / TXT / images. Returns structured `{title, authors, abstract, sections[], figures[], tables[], key_quotes[]}`. Implementation uses Claude's native PDF input blocks (Anthropic SDK already supports this — no local `pypdf` / `pymupdf` dependency). DOCX via `python-docx`; MD/TXT pass-through.
- **New tool** `passthrough_image(layer_id, source_path, aspect_ratio)` — copies a user-supplied figure (paper diagram, logo, photo) into `layers/` as a `kind: "image"` layer. NOT NBP-generated. Lives alongside `generate_image` (v1.0 #8.75) so the planner can mix original figures + synthesized hero imagery freely.
- **Planner prompt, new "ingestion-mode workflow" section** — when brief references a file, call `ingest_document` FIRST, then map the returned tree onto the artifact schema:
  - `paper.pdf` → 3:4 academic poster (title + authors + 4 sections + figures + QR)
  - `paper.pdf` + "landing" → marketing landing (abstract as hero, method/results as sections, figures inlined)
  - `paper.pdf` + "deck" → slide deck (one section per slide, figures captioned)
- **CLI entry**: `open-design run --from-file paper.pdf "poster for CVPR"` and REPL slash `:attach <path>` that binds a file to the next brief.
- **Round-trip smoke**: `paper.pdf → landing.html → user edits title + reorders sections → apply-edits → new run preserves original figures + user edits`. Gates merge.

**Risk**: Claude's PDF vision has a per-call page cap. Long papers (50+ pages) may need a multi-call "summarize each section" fallback. Time-box exploration: if single-call handles 30-page papers cleanly, ship it; longer-paper handling pushes to v1.1.5.

**Demo targets** for the README / launch video:
- `LLaMA-3.pdf` → claymorphism landing (abstract as hero, method + results as pastel cards, every figure as `passthrough_image`)
- Same PDF → 3:4 academic poster (baseline parity with paper2poster prior art)
- Same PDF → 10-slide editable PPTX deck (once v1.0 #7 ships)

**Why v1.1 and not v1.0**: v1.0's bar is "one-brief to 3 artifacts, round-trip editable, production-grade." Document ingestion is the next leap and the "this is the actual product" moment — it deserves its own launch beat, not a crammed-in v1.0 item.

**Note on the old v1.1 "Multi-image insets" scope**: the landing-page portion already shipped as v1.0 #8.75 (`generate_image` tool). Poster / deck inset generation folds into v1.2.

---

## v1.3.1 — Paper2landing imagery policy ✅ SHIPPED 2026-04-21

First real paper2landing dogfood (run `20260421-201900-cb30b9bd` on longcat-next-2026.pdf, claymorphism) used **5 NBP stock icons vs 1 ingested figure** — a marketing aesthetic, not academic. Root cause: planner prompt said "NBP for hero + feature icons" as the default, with no carve-out for paper landings.

Fix (`prompts/planner.md` landing workflow):

- New **"Paper landing imagery policy"** section parallel to v1.2.2's poster visual-density rules:
  - ≥ 3 ingested figure layers if ≥ 5 available (≥ 5 if ≥ 10).
  - Ingested tables MUST appear on the landing as `kind: "table"` (no cropped screenshots).
  - NBP RESERVED for imagery the paper can't provide; 0 NBP for content sections.
- Default mapping (paper → landing sections): hero = paper's Fig. 1, method = system diagram + pipeline, results = benchmark table + scaling plot, showcase = qualitative examples.
- Style preference: **editorial** (default), `minimalist`, or `liquid-glass`. Explicitly avoid claymorphism / neubrutalism / glassmorphism for paper landings.

Verified re-run (`20260421-203226-fc1740c9`, editorial): **0 NBP calls**, **9 paper figures + 1 paper table** across 8 sections (hero + abstract + contributions + method [4 figures] + results + showcase [2 figures] + cta + footer). Wall 6:26, $4.07, pass 0.88 in one iteration.

## v1.3.0 — Interactive landing pages ✅ SHIPPED 2026-04-21

From static HTML pages to production-grade marketing pages. Four user-visible improvements, all backward compatible:

- **CTA buttons** as first-class `LayerKind "cta"` — planner declares `{kind: "cta", text, href, variant}` alongside text/image/table; renderer emits `<a role="button" class="ld-cta ld-cta--{variant}">` with per-design-system styling (minimalist filled accent, editorial underline-link, claymorphism puffy 3D, liquid-glass frosted pill, glassmorphism aurora frosted, neubrutalism hard-shadow block).
- **Section anchors + auto top nav** — every section gets `id="sec-{slug}"`; a `<header><nav>` auto-renders when the spec has ≥ 4 sections OR `design_system.show_nav=true`. Hero and footer sections are skipped from the nav. Active link gets `aria-current="page"` via JS.
- **Inline vanilla JS** (~1.6 KB) — `IntersectionObserver` reveal-on-scroll (`[data-reveal]` → `.is-revealed`), smooth anchor click → `scrollIntoView`, nav active-link tracking. Respects `prefers-reduced-motion`.
- **Accessibility baseline** — semantic `<header>` / `<main>` / `<footer>` (last section with variant `footer` is auto-upgraded to `<footer class="ld-section">` outside `<main>`), `<img alt>` from layer name, `role="button"` on CTAs, `aria-current` on active nav link.

`apply_edits` round-trip preserves CTAs with `href` + `variant` intact via `data-*` attributes (CTAs are `contenteditable=false` so the edit toolbar can't silently mutate link text). Smoke expanded to a 5-section fixture (hero + features + pricing + cta + footer) asserting 19 HTML markers + full round-trip; suite stays 18/18.

## v2.0 — Training-data pipeline ✅ SHIPPED 2026-04-22 (PR [#1](https://github.com/Yaxin9Luo/OpenDesign/pull/1))

**The mission closes the loop**: the project is now both a product (OSS design agent) and an explicit training-data producer for the Longcat-Next layered-generation model. Every user run doubles as a distillation sample. Three commits, all on main:

### v2.0 — `DistillTrajectory` schema + multi-provider LLM backend ([30cab95](https://github.com/Yaxin9Luo/OpenDesign/commit/30cab95))

- **v2 trajectory schema**: `DistillTrajectory` (pure model decisions + lean tool results + episode reward) replaces v1 `Trajectory` (product/debug/training hybrid). 144 KB → 44 KB per equivalent run (70% size drop). Top-level `final_reward` + `terminal_status` (`pass` / `revise` / `fail` / `max_turns` / `abort`) consumable by offline RL trainers directly.
- **New tool contract**: `ToolResultRecord` replaces `ToolObservation`; `obs_ok(payload)` / `obs_error(message, category, payload)` — **no more `summary` / `next_actions` / `artifacts` kwargs**. These "hint fields" were removed at the tool layer (not just stripped from JSON) to eliminate train↔deploy distribution shift in RL. Workflow contract lives entirely in `prompts/planner.md` now.
- **`StepType` slimmed** to `{input, reasoning, tool_call, tool_result, finalize}` (dropped `thought` / `artifact_switch` / `design_spec` / `critique` — recoverable from tool_call args + tool_result payload). `AgentTraceStep` drops `timestamp` / `spec_snapshot` / `observation` too.
- **`ThinkingBlockRecord`** captures extended-thinking verbatim with `signature` (Anthropic) or empty (OpenAI-compat). Both plain + redacted blocks preserved for faithful replay.
- **Multi-provider LLM backend** (`open_design/llm_backend.py`): `LLMBackend` Protocol + `AnthropicBackend` + `OpenAICompatBackend`. **9 protocol differences normalized** (reasoning field, tool calling, system prompt placement, vision blocks, stop reason vocab, thinking control, interleaved beta, cache telemetry, replay constraints). Planner + critic have zero provider-aware branches.
- **Default planner + critic → `moonshotai/kimi-k2.6`** (OpenRouter). Kimi costs ~$3.58/run with 12 reasoning steps captured in full plaintext (vs Claude Opus 4.7 at ~$8-12/run with ~80% thinking redacted). Claude is one env var away: `PLANNER_MODEL=anthropic/claude-opus-4.7`. Mix-and-match (e.g. planner on Claude + critic on DeepSeek-R1) works out of the box.

### v2.1 — Versioned intermediate artifacts ([c264545](https://github.com/Yaxin9Luo/OpenDesign/commit/c264545))

**Closes the gap left by v2.0**: v2 trajectory captured every model decision but the artifacts those decisions produced still lived in clobber-prone fixed paths. DPO pairs (rejected vs chosen) + layered-gen SFT (per-layer edit history) need this state preserved.

- `out/runs/<run_id>/composites/iter_NN/{poster.html,psd,svg,preview.png}` — every composite call writes into its own subdirectory; prior iterations preserved.
- `out/runs/<run_id>/final/` — **relative symlinks** to the latest iter, refreshed atomically after every successful composite. Product consumers use stable `final/` paths; versioning is invisible.
- Layer PNGs gain `.vN.png` suffix (`text_L1_title.v1.png` → `.v2.png` on `edit_layer` re-render). Prior versions stay on disk.
- Tool result payloads chain via `version` / `relative_path` / `supersedes_sha256` (absent on v1). Composite payload gains `iteration` + `{preview,html,pptx}_relative_path` + `supersedes_preview_sha256` for DPO pair extraction.
- `ToolContext.next_layer_version(layer_id)` + `ctx.next_composite_iter()` helpers drive the bumping.
- Smoke #19 verifies revise-loop preservation + `supersedes_sha256` chain integrity — suite now **19/19**.

### v2.2 — SFT jsonl exporter ([eeb6490](https://github.com/Yaxin9Luo/OpenDesign/commit/eeb6490))

`scripts/export_sft_jsonl.py` flattens a directory of v2 trajectories into OpenAI-compat SFT jsonl — **one record per assistant turn** (planner or critic). Each record is self-contained: full message history up to the turn, the turn's `reasoning_content` + `tool_calls` (OpenAI shape), the tool catalog, per-turn `usage`, episode `final_reward` + `terminal_status`. CLI filters by `--min-reward / --source / --actor / --provider / --terminal-status`. Apply-edits trajectories auto-skipped (no model decisions).

---

## v2.3 — Paper2any Tier-1 product fixes ✅ SHIPPED 2026-04-22

Five independent improvements, each surfacing from 10+ real dogfood runs. Shipped as 5 separate commits on main — each with its own smoke + dogfood — so regressions bisect cleanly. Paper2any now usable for academic users at the recommend-to-a-labmate bar.

### v2.3.1 — Deck speaker notes ([1368422](https://github.com/Yaxin9Luo/OpenDesign/commit/1368422))

`LayerNode.speaker_notes: str | None` (slide-only). PPTX renderer populates `slide.notes_slide.notes_text_frame.text` from planner-drafted talking points + timing cues + Q&A prompts. Decks are now presentation-ready, not just handouts. Planner prompt guides "≤ 200 words, skip cover/thank-you/divider slides, match slide body language".

### v2.3.2 — Short caption ([bb3aec8](https://github.com/Yaxin9Luo/OpenDesign/commit/bb3aec8))

VLM ingest returns both full caption AND `short_caption ≤ 15 chars` ("Architecture", "Scaling curves", "Ablation"). Same for tables (`short_title`). Registered as `rendered_layers[fig].caption_short` + `caption_short` key on table records; exposed in the `figures` / `tables` payload to the planner. No schema change — free-form key. Planner picks per-bbox: full caption for landing `<figcaption>` (plenty of room), short for poster footer / deck caption slots.

### v2.3.3 — Poster templates ([b3a4e21](https://github.com/Yaxin9Luo/OpenDesign/commit/b3a4e21))

`open-design run --template NAME` resolves 5 bundled canvas presets:

| Template | Canvas | Aspect | Use |
|---|---|---|---|
| `neurips-portrait` | 1536×2048 @300dpi | 3:4 | NeurIPS / CVPR / ICLR portrait |
| `cvpr-landscape` | 2048×1536 @300dpi | 4:3 | CVPR landscape |
| `icml-portrait` | 1536×2048 @300dpi | 3:4 | ICML portrait |
| `a0-portrait` | 2378×3366 @300dpi | 1:√2 | ISO A0 portrait (EU/CN venues) |
| `a0-landscape` | 3366×2378 @300dpi | √2:1 | ISO A0 landscape |

Injected via brief prologue (same mechanism as `--from-file`) — planner sees a `Template: <name>` block with the resolved canvas dict, writes it onto `DesignSpec.canvas` verbatim unless free-text overrides. No schema change. Case-insensitive + hyphen/underscore tolerant name resolution.

### v2.3.4 — KaTeX for landing ([ce759d8](https://github.com/Yaxin9Luo/OpenDesign/commit/ce759d8))

Self-hosted KaTeX 0.16.9 bundle under `assets/vendor/katex/`: CSS + core JS + auto-render + 20 woff2 fonts (base64-inlined as `data:` URIs so the landing HTML stays portable with zero CDN dependency). Delimiters supported: `$…$` (inline), `$$…$$` (display), `\(…\)`, `\[…\]`. Gated on a `_has_math()` scan of the layer_graph — landings without math skip the ~645 KB bundle entirely. Rendering runs client-side on `DOMContentLoaded`, scoped to `.ld-landing` so the edit toolbar is untouched. 5 new markers in smoke: `<style id="ld-katex-css">`, `renderMathInElement`, `data:font/woff2`, inline + display math preservation.

### v2.3.5 — Sub-figure extraction ([4a77830](https://github.com/Yaxin9Luo/OpenDesign/commit/4a77830))

VLM caption-matching prompt gains `sub_panels: [{label, bbox, caption, short_caption}]` return field. `_register_sub_panels` Pillow-crops each panel out of the parent PNG and registers as `ingest_fig_NN_<label>` (e.g. `ingest_fig_02_a`) — naming convention carries the parent relationship, no `parent_layer_id` schema field. Parent layer stays in the catalog too, so the planner can place either the composite view OR individual panels. New smoke check `check_sub_figure_registration` synthesizes a 2-panel 600×300 composite, verifies both crops + parent breadcrumb + correct pixel colors. Suite now **20/20**.

### v2.3.6 — Planner figure-catalog cap ([4d8f58d](https://github.com/Yaxin9Luo/OpenDesign/commit/4d8f58d))

**Regression discovered via v2.3 triple dogfood** on longcat-next-2026.pdf: v2.3.5 doubled the ingest figure catalog from ~45 to ~85 (parent + sub-panels). Dumping all 85 into the planner tool_result summary overwhelmed Kimi K2.6's reasoning budget — **3 consecutive Kimi runs hit `terminal_status=max_turns` with 0 DesignSpec emissions** ($1.46 + $1.28 + $2.01 in losses).

Fix: apply the existing `_PLANNER_FIG_CATALOG_CAP = 20` constant (previously defined but unused) via the existing `_rank_figure_ids_for_planner` heuristic (caption > vector > size > page). Registry still holds all 80+ layers; the cap is only on the summary dict the planner reads. New `n_figures_truncated` + `catalog_note` fields tell the planner how to reach lower-ranked layers by layer_id.

### v2.3 combined dogfood verification (longcat-next-2026.pdf, Claude Opus 4.7)

Kimi K2.6 after the v2.3.6 cap fix still hit max_turns — root cause turned out to be **Kimi's model-level over-reasoning on complex poster geometry**, not catalog size. Single trajectory had 47,798 characters of reasoning iterating bbox arithmetic (`"640×520... 640×540... 640×580..."`) before exhausting budget without emitting `propose_design_spec`. Re-routed to Claude Opus 4.7 via `PLANNER_MODEL=anthropic/claude-opus-4.7` — same code paths, different SDK (the v2 `LLMBackend` abstraction paying off at production-use time). Results:

| Artifact | Terminal | Reward | Wall | Cost | Trace Steps | Notes |
|---|---|---|---|---|---|---|
| **Landing** | **pass** | **0.88** | 6:50 | $3.96 | 18 (1 critique) | Math preserved in text; KaTeX injected (648 KB bundle, 9.8 MB HTML); editorial style |
| **Deck** | **pass** | **0.88** | 10:47 | $9.45 | 33 (1 critique) | **18 of 18 content slides got speaker_notes** populated (planner auto-drafted) |
| **Poster** | fail | 0.68 | 11:00 | $10.41 | 85 (2 critiques) | `--template neurips-portrait` → 1536×2048 canvas applied ✓; revise loop degraded 0.86 → 0.68 across 85 edit-layer steps (pre-existing poster-revise-loop flaw, not v2.3-introduced; parked for v2.4) |

Model routing insight: **Kimi K2.6 works fine on landing/deck (simple flow layouts) but stalls on paper posters (complex 2D bbox geometry).** Claude Opus 4.7 is the production planner for paper2poster until a cheaper model handles constraint satisfaction. Documented in [DECISIONS.md § 2026-04-22](DECISIONS.md).

---

## v2.7.2 → v2.8.1-phase1 — Cloud Design diff response ✅ SHIPPED 2026-04-26

Four releases motivated by side-by-side dogfood of Anthropic's Claude Design product on the same paper (longcat-next-2026.pdf). The .pptx Claude Design produced is `PptxGenJS Presentation` with native shapes (verified by extracting `docProps/app.xml` `<Company>PptxGenJS</Company>` + counting `<p:sp>` shapes). Two surface bugs in their output (notes off-by-one cascade from slide 8-13; non-monotonic section numbers §3.2→§3.1) drove v2.7.2; their forked vision-verifier-agent design drove v2.7.3; their claim-graph-driven slide arc (matrix→explanation→mechanism→evidence→implication, NOT paper chapter order) drove v2.8.0; their 8+ slide-archetype library drove v2.8.1.

Full diff analysis at `~/.claude/plans/export-block-eager-wilkinson.md`. Implementation plan committed at [docs/IMPLEMENTATION_v2.8.md](IMPLEMENTATION_v2.8.md). Wave 1 (v2.7.2 + v2.7.3) and Wave 2 (v2.8.0 + v2.8.1-phase1) shipped via parallel git worktrees + integration branches; smoke 31→42, all green at every merge.

### v2.7.2 — Stable-id speaker_notes + section renumber (tag `v2.7.2`)

`SlideNode.speaker_notes` is now first-class on the LayerNode (was already there but contract under-documented; smoke #27 hardens it). `SlideNode.section_number` (new optional field) is auto-renumbered post-reorder by `apply_section_policy` in `_composite_deck`. New `Settings.section_number_policy: Literal["renumber"|"strip"|"preserve"]` (default `renumber`). Avoids two bugs we observed in Cloud Design's PptxGenJS deck. New `open_design/util/section_renumber.py` (immutable). Smoke #25-27. Zero API cost.

### v2.7.3 — Vision critic as forked sub-agent (tag `v2.7.3`)

Inline `critique_tool.py` replaced by `CriticAgent` in `open_design/agents/critic_agent.py` — independent sub-agent with own `make_backend(settings, settings.critic_model, role="critic")` instance, own `critic_max_turns=10` budget, own `out/<run>/trajectory/critic.jsonl`. All three artifact types (deck/landing/poster) now use vision-based critic, not just poster (which already did). Provenance is a critic priority dimension (`category="provenance"` blocker). New `CritiqueIssue` + `CritiqueReport` Pydantic schemas. Three new prompts: `critic_vision_{deck,landing,poster}.md`. Old `open_design/critic.py` + 3 old critic prompts deleted (no fallback path). Smoke #28-31. API cost: +30-60% per critique round (vision PNG payload), but turn budget is now decoupled from planner.

### v2.8.0 — Claim graph extractor (tag `v2.8.0`)

New `ClaimGraphExtractor` sub-agent in `open_design/agents/claim_graph_extractor.py` runs between enhancer and planner when input is a PDF (skipped via `--no-claim-graph`). Extracts `ClaimGraph(thesis, tensions[], mechanisms[], evidence[], implications[])` — strict Pydantic schema with `EvidenceNode.raw_quote` MUST be substring of paper raw_text (echoes v2.7 hard constraint; validated by `open_design/util/claim_graph_validator.py`). Planner consumes the graph to follow talk arc (cover→tensions→mechanisms→evidence→implications→takeaways) instead of paper chapter order. `LayerNode.covers: list[str]` records which graph node ids each slide presents. CriticAgent accepts `claim_graph` and reports `category="claim_coverage"` issues via `lookup_claim_node` tool when tensions/mechanisms are uncovered. Provenance validator extended to also check `ClaimGraph.evidence[*].raw_quote`. Default `claim_graph_model = "moonshotai/kimi-k2.6"` (agent-coding model with strict JSON discipline). Smoke #32-36. API cost: +20-40% per run from extractor + critic claim-coverage checks.

### v2.8.1 Phase 1 — Slide archetype library (tag `v2.8.1-phase1`)

New `SlideArchetype` literal with 10 values (4 implemented this phase, 6 placeholders for Phase 2/3). New `LayerNode.archetype` field, default `"evidence_snapshot"`. `pptx_renderer.py` dispatches via `ARCHETYPE_RENDERERS: dict[str, Callable]` with default fallback (zero behavior change for existing decks). Phase 1 archetypes:

| Archetype | Layout |
|---|---|
| `cover_editorial` | Large serif title (NotoSerifSC-Bold) + subtitle + author byline |
| `evidence_snapshot` | One huge metric (~250pt) + footnote, lots of white space |
| `takeaway_list` | 3 bullet items (bold lead phrase + supporting sentence) + optional slogan |
| `thanks_qa` | Headline + contact row + optional code/weights link |

Each archetype is deterministic (smoke #42 verifies byte-identical XML across two renders), uses native python-pptx shapes (not raster), and preserves v2.7.2's `_with_section_prefix` on titles + speaker_notes binding by slide_id. Templated path (`_write_pptx_templated`) untouched. New planner archetype-selection rules + critic `archetype_consistency` check inside `visual_hierarchy` category. Phase 2/3 archetypes (pipeline_horizontal / tension_two_column / section_divider / cover_technical / residual_stack_vertical / conflict_vs_cooperation) declared as placeholders, fall through to default. Smoke #37-42. Zero API cost.

### Combined wave verification (longcat-next-2026.pdf, 2026-04-26)

- **Smoke**: 42/42 pass on main at every merge boundary (wave1-integration → main, wave2-integration → main).
- **Lessons learned**: Agent tool's `isolation: "worktree"` reuses stale base in some cases — manual `git worktree add` from current main is the reliable path for parallel multi-agent dispatch.
- **Conflict resolution**: scripted via `/tmp/resolve_*_conflicts.py` (smoke label renumbering is mechanical; new content blocks kept on both sides).
- **Killed**: `Path B (HTML deck)` from the diff analysis. Cloud Design's .pptx is also native PptxGenJS-generated, so editability is not our moat. Real moat = v2.7 provenance + v2.8.0 claim-graph grounding (both unique to us).

---

## Deferred to v1.3.5

- **Tab groups** (new `kind: "tabs"` container + per-tab `LayerNode` children)
- **Accordions** (`kind: "accordion"`)
- **Email capture / newsletter forms** — needs backend endpoint policy, not v1.x scope

## Deferred to v2.3

- **DPO pair exporter** (`scripts/export_dpo_jsonl.py`) — mirror of SFT exporter but pairs `composites/iter_N-1/preview.png` (rejected, critic revise verdict) with `iter_N/preview.png` (chosen, critic pass verdict). All substrate data on disk thanks to v2.1; just needs the flattener.
- **Per-layer SFT exporter** — DistillTrajectory captures the model's layer-by-layer decisions + layer `.vN.png` chain captures the visual evolution. Useful for training layered-gen models that predict one layer at a time.

---

## v2.4 — NEXT: prompt-enhancer + font library + image-manipulable output (planned 2026-04-23+)

Three-item slice motivated by 2026-04-22 dogfood session (BAGEL landing, LLMSurgeon landing, TinyML-agent-memory deck). All three problems surfaced from the same observation: **quality of generation is dominated by brief quality, not planner capability**. Opus 4.7 already produces 0.88–0.92 reward outputs when the brief is tight; it silently regresses when the brief is sloppy.

### v2.4.1 — **Prompt Enhancer agent** (the big one)

**Why**: Across 3 dogfood runs the author (acting as human-in-the-loop prompt enhancer) delivered identical reward-level outputs by (a) expanding a one-line user intent into a 3k-char multi-section brief, (b) injecting artifact-type-aware imagery discipline, (c) adding style-prefix + palette hex, (d) pre-flighting ingest and detecting the "rasterize-to-shrink kills embedded-figure extraction" failure mode. We should encode that expertise into an agent stage so the system works for users who don't know these rules.

**Shape**: new `PromptEnhancer` stage that runs BEFORE `planner.start`. Input: raw user message + attachment list. Output: structured enhanced brief + artifact-type decision + per-section figure-placement hints. Behaves like a specialized reasoning model with its own system prompt (not a new tool — more like a `before_planner` hook).

**System-prompt / skill must encode these lessons**:

1. **Ingest-first discipline.** Always call `ingest_document` first. After it returns, gate the plan on `total_figures > 0` (unless artifact is a no-paper poster / free-form design). If 0 figures, either (a) surface the error to the user with a concrete fix ("PDF is rasterized — supply original embedded-image PDF"), or (b) fall back to a brief variant that restricts content sections to table + NBP only.
2. **Paper figures are primary; NBP is decorative.** Method / results / capabilities / qualitative sections MUST reference `ingest_fig_*` layer IDs. NBP `generate_image` is restricted to (a) hero/cover ambient background, (b) subtle decorative accents between sections when no paper figure fits. Never NBP-substitute a scientific figure.
3. **Tables: real rows or none.** When requesting an HTML or native-PPTX `<table>`, the brief must require "concrete cell values transcribed from the PDF; omit the table entirely if transcription fails." Empty `rows=[]` placeholders are forbidden.
4. **Equations: KaTeX display math.** For papers, include `$$...$$` blocks for the central objective / loss / attribution / surgery / key-theorem equations. Renderer injects KaTeX vendor bundle automatically — enhancer only needs to request the content.
5. **Style-prefix verbatim.** Any NBP call must repeat the same palette + mood prose as a style prefix (enforced by the brief), so all generated images read as one coherent piece.
6. **Palette + typography with concrete hex / font-name, not prose.** "Editorial minimalist" is useless; "`#141414` ink on `#FAFAF7` cream with `#7F1D1D` single accent; `NotoSerifSC-Bold` title / `NotoSansSC-Bold` body" is actionable.
7. **Artifact-type ↔ table-kind routing.** Deck → native PPTX table. Landing → HTML `<table class='benchmark-table'>`. Poster → native table. Enhancer must infer or confirm artifact type first, then pick the right table directive.
8. **Length discipline is artifact-specific.** Research-talk decks: declare explicit slide count (10 / 12 / 16). Paper landings: "unlimited length, info density > brevity, expand rather than compress." Marketing landings: "short and punchy, < 5 sections." Posters: single-page fixed canvas.
9. **Callback / narrative coherence.** For decks, call out "echo the cover's motif in the closing slide." For landings, reuse the hero's accent color only in CTAs + 1-2 highlight spans — never decoratively.
10. **Pre-flight warnings.** If input PDF > 30 MB, warn the user that pre-rasterizing (ghostscript, print-to-PDF) will destroy `page.get_images()` extraction; recommend uploading the original PDF instead.
11. **Per-section outline > freestyle.** The enhanced brief should contain a numbered section-by-section outline, each section declaring (a) role, (b) expected content, (c) expected imagery kind (`ingest_fig_*` / HTML `<table>` / KaTeX math / NBP ambient / text-only). Opus 4.7 follows explicit outlines 100% of the time; it improvises badly when given vague briefs.
12. **Negative constraints.** Always list "never substitute NBP for X / never leave empty placeholders / never pre-rasterize" explicitly — negative rules bite harder than positive ones in brief-following.

**Deliverables**:
- `open_design/agents/prompt_enhancer.py` — the stage, reuses `LLMBackend` with Opus 4.7 default
- `prompts/prompt_enhancer.md` — the system-prompt/skill encoding the 12 rules above
- `runner.py` wiring — run enhancer before `planner.start`, log `prompt.enhance.request/done` events
- `--skip-enhancer` CLI flag for users who want raw pass-through
- Three regression fixtures replaying the 2026-04-22 dogfood briefs (BAGEL, LLMSurgeon, TinyML deck) — assert enhancer produces a brief containing all required directives

**Risk**: adds a ~20–40 s latency per run and ~$0.30–0.80 cost. Must be opt-outable for power users.

### v2.4.2 — Font library expansion

**Why**: Today only `NotoSansSC-Bold` + `NotoSerifSC-Bold` are bundled ([config.py:101](../open_design/config.py:101)). Users routinely want more weight / style variety (regular, medium, display, mono, decorative).

**Scope**:
- Add 6–8 more OFL-licensed families under `assets/fonts/`: NotoSansSC-Regular, NotoSerifSC-Regular, NotoSansMono, Inter (regular + bold), a display serif (e.g. Playfair Display), a modern sans (e.g. IBM Plex Sans). All OFL or SIL license.
- Extend `Settings.fonts` dict + `default_text_font` / `default_title_font` to a family-registry pattern: resolve family → weight → style → file.
- PSD writer + SVG embedder + PPTX renderer all need to honor the new registry.
- Brief-level: typography block can declare `body_font: "Inter-Regular"` and resolve via registry.

**Size impact**: bundled `.whl` grows from ~40 MB to ~80 MB. Acceptable — one-time download.

### v2.4.3 — Interactive image manipulation in output

**Why**: Current outputs (PPTX / PSD / HTML) have images at fixed positions and sizes. Users — especially on landing pages — want to drag-reposition and resize images post-generation without going back through the agent loop.

**Scope by artifact**:
- **Landing (HTML)**: wrap every `kind: "image"` in a `<div class="draggable-resizable">` with minimal vanilla-JS (`interact.js` or equivalent, ~50 KB) that adds pointer drag + corner-handle resize, persisting position/size to `localStorage`. Optional export: "Save adjusted layout" downloads a `layout.json` that the agent can re-ingest.
- **PPTX**: already editable in PowerPoint natively — no work needed.
- **PSD**: already editable in Photoshop natively — no work needed.
- **Poster PNG**: skip — flat raster, by definition not draggable. Designers export to PSD for editing.

**Sub-scope**: a "Designer preview" mode (`open-design preview <run-id>`) that opens the landing `index.html` in a local server with draggable/resizable layer overlays + a "Lock positions" toggle.

**Risk**: interact.js drag handles can conflict with section-level scroll-snap. Contain handles inside section bounds; disable drag on mobile viewport (< 768 px).

### v2.4 verification plan

- Re-run BAGEL landing with enhancer on + off — measure brief character length, reward delta, paper-figure usage ratio
- Re-run LLMSurgeon landing with new Playfair Display title font
- Interactive drag-resize smoke-test on LLMSurgeon landing in Safari + Chrome

---

## v1.4 — Real PSD type layer

**Why**: Designer UX improvement. v1.0 PSD has named pixel layers (designer can move/resize/reorder but can't double-click to edit text). Real PSD type layers close the gap.

**Scope** (~150 lines, tricky due to psd-tools API brittleness):
- Replace `composite._write_psd` text branch with `TypeLayer` construction (font metadata, actual vector text).
- Fall back to pixel layer if font unavailable on designer's machine.
- Annotate in trajectory: `LayerNode.psd_type_layer: bool`.

**Risk**: psd-tools' type-layer write API is under-documented. Time-box: if not working in 8 hours, leave pixel-layer fallback permanent.

---

## v1.5 — Brand Kit ingestion

**Why**: Claude Design's big UX win is "upload your brand PDF, every artifact respects your palette/typography/imagery." We need parity for teams with existing brand guidelines.

**Scope** (~300 lines):
- `open-design brand ingest <path.pdf>` CLI command
- Uses Claude vision to extract: palette (named hex values), typography rules (font roles), logo files, imagery mood.
- Stored in `brands/<name>/brandkit.json`.
- Planner loads `brandkit.json` into DesignSpec when user says "use the Acme brand kit" or `--brand acme`.
- Replaces the v0 `fetch_brand_asset` stub.

---

## v1.6 — Skills (reusable design templates)

**Why**: After successful runs, the (brief → trajectory) pair is a template. Lovart calls these "Skills" and they're a strong retention feature.

**Scope**:
- `open-design skill save <session_id> --name "academic-poster-cvpr"` → extract design_spec + key prompts as reusable template.
- `open-design skill apply <name> --override "title=新标题"` → instantiate new session from skill with overrides.
- Skills in `skills/<name>.yaml`.

---

## v1.7 — Layout balancer (post-processor)

**Why**: Inspired by Paper2Any's `balancer_agent`. Claude sometimes produces layouts with subtle overlaps or spacing issues. A deterministic post-processor pass catches the obvious ones.

**Scope** (~80 lines): Shapely-based or custom rectangle intersection check on layer bboxes after composite; generates suggestions (or auto-applies within a threshold).

---

## v1.8 — Local model support

**Why**: Full self-hosting story. Some teams can't send API requests to external providers.

**Scope**:
- Support local Ollama / vLLM / llama.cpp endpoint via env var (`LOCAL_LLM_BASE_URL`).
- Adjust planner prompt to work with smaller models (Llama 3.1 70B baseline).
- Fallback model for background generation: local FLUX / SDXL integration (separate deploy, documented).

**Risk**: quality bar; local 70B models don't plan multi-tool workflows as well as Opus 4.7. Document as "experimental" until we get scores within 10% of Opus baseline.

---

## v2.0 — Web UI (optional, distant)

**Why**: Some users won't ever adopt a CLI tool. A thin browser UI over the same core agent could unlock broader adoption.

**Scope**: separate project. Not a v1.x priority. Probably ships as `open-design-web` as a separate repo that imports the Python package.

---

## v2.x+ — Future directions

- **Video / animation**: Seedance-style ambient motion videos. Significant.
- **Drawio / diagram output**: steal idea from Paper2Any.
- **Multi-page batch**: regenerate N size variants of same design from one session.
- **Collaboration / sync**: if user demand arises.

---

## Always-on backlog (no version assigned)

Continuous improvements, not feature ships:

- **Cost estimator accuracy**: replace heuristic with real `usage.cost` from OpenRouter response (already returned, just not aggregated).
- **Critic rubric evolution**: audit frequent issue categories, re-balance rubric weights.
- **Prompt cache tuning**: set `cache_control` on planner system prompt + tool catalog (stable turn-to-turn).
- **Font fallback diagnostic**: `open-design audit <session>` flags fallback events the planner silently accepted.
- **Showcase gallery**: maintain a `showcase/` directory in repo with ~10 diverse high-quality example artifacts + their session JSONs.

---

## Open questions (not yet roadmap'd)

- **Pricing / support model for OSS**: pure MIT + donation link? Enterprise support tier? Not decided.
- **Brand Kit format**: invent our own schema, or adopt Figma tokens / Adobe XD style format for import compat?
- **How heavily do we market the Longcat-Next tie-in**: enough to signal team credibility, without implying OpenDesign is just a Longcat-Next sales funnel. Needs a marketing decision.
- **Docs site**: mkdocs on GitHub Pages vs dedicated docs domain vs README-only.

---

## Killed / explicitly out of scope

- **LangGraph / CrewAI integration** — see [DECISIONS.md](DECISIONS.md).
- **Training-data dataset publishing** as primary product — trajectory machinery preserved internally; not the pitch.
- **Canva integration** — they have distribution, not us. We aim open formats (HTML/PPTX).
- **Mobile app** — indefinitely out of scope.
- **Real-time multi-user collaboration** — single-session single-user is the model.
