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

**Positioning at the time**: research prototype for Longcat-Next training-data capture. Pivoted 2026-04-18 to **LongcatDesign** open-source product — trajectory machinery preserved as internal session state; no longer the primary pitch.

### v1.0 in-progress milestones (2026-04-18, ongoing)

**Package rename** (commit `ffd4389`): `design_agent` → `longcat_design`. pyproject `longcat-design` distribution, `longcat-design` CLI, all internal imports and references updated. Clean `pip install -e .` registers the new package; both `python -m longcat_design.cli` and `longcat-design` script entry work (modulo the macOS UF_HIDDEN trap documented in GOTCHAS).

**v1.0 #3 — `switch_artifact_type` tool + ArtifactType enum** (commit `21dc44f`): schema gains `ArtifactType` (poster / deck / landing) + `DesignSpec.artifact_type` field + new `StepType` `"artifact_switch"`. New tool registered FIRST in TOOL_SCHEMAS. Planner prompt updated with artifact-type declaration as step 1 of the workflow contract. propose_design_spec falls back to `ctx.state["artifact_type"]` when spec omits the field. 7 → 8 tools wired.

**v1.0 #4 — CLI conversational chat shell + ChatSession persistence** (commit `03517ba`): new `chat.py` (REPL, 8 slash commands: `:help`/`:save`/`:load`/`:new`/`:list`/`:history`/`:tokens`/`:export`/`:exit`) + new `session.py` (`ChatSession`/`ChatMessage`/`TrajectoryRef` + save/load/list helpers). cli.py restructured with subparsers (`chat` default + `run` one-shot). Context injection: every non-first brief prefixed with a compact summary of the latest trajectory so planner can distinguish revision-vs-new-artifact. Planner prompt grows a dedicated "revision vs new-artifact decision" section. Smoke 6 → 7 steps with ChatSession round-trip coverage.

**Third dogfood run** (2026-04-18): LongcatDesign launch poster in 5 minutes / $3.74 / 5 layers / 2-iter critique (revise 0.78 → pass 0.82). Chinese title 「龙猫设计」, English subtitle, red stamp 「开源」, bottom tagline 「对话 · 解构 · 编辑」 — all independently editable layers. Proves the architecture holds up on harder briefs (agent retried NBP 3× when safety-filter rejected first attempts).

---

## v1.0 — LongcatDesign public MVP launch (4 of 11 items done)

Three-artifact conversational design agent on GitHub, MIT-licensed, `pip install longcat-design`-able. See [V1-MVP-PLAN.md](V1-MVP-PLAN.md) for full implementation breakdown with status column.

**Must-haves for launch**:

1. ✅ **Rename** — `pyproject.toml` project name → `longcat-design`; CLI entry `longcat-design`; Python package → `longcat_design/`; docs/README branding. (commit `ffd4389`)
2. ✅ **CLI chat shell** — multi-turn conversational REPL replacing one-shot `cli.py`. 8 slash commands, readline editing, session persistence to `sessions/<id>.json`, resumable via `--resume`. (commit `03517ba`)
3. ✅ **artifact_type tool** — `switch_artifact_type(poster|deck|landing)` + `ArtifactType` enum + `DesignSpec.artifact_type` field. Declares what we're making before spec. (commit `21dc44f`)
4. **HTML renderer** — structured Tailwind CSS + inline base64 assets, self-contained `.html` file. First-class target for posters AND landing pages. Key technical differentiator vs closed SaaS. **← NEXT DEEP WORK**
5. **PPTX renderer** — `python-pptx` writing native PowerPoint type frames, one slide per deck section. Editable in PowerPoint / Keynote / Slides with no special steps.
6. **edit_layer tool** — planner-invocable: modify an existing layer's text/font/color/bbox and recompose. Unlocks `:edit` slash command (currently revisions go through full re-spec path).
7. ✅ **Conversation persistence** — each chat session saves its full message history + trajectory refs to `sessions/<id>.json`. Reload with `:load <id>` or CLI `--resume`. (shipped with #2)
8. **README + docs** — product-facing README (quickstart, showcase, install, config), KB updates for all new pieces. (partial — docs kept current; final polish pending)
9. **1 demo video** — screencast of a multi-turn session producing all 3 artifact types.

**Deferred from v1.0 to v1.x** (keeps launch scope tight):

- Multi-image insets (v0.1 original plan, now **v1.1**)
- Real PSD type layer (was v0.3, now **v1.3**)
- Brand Kit PDF parsing (was v0.6, now **v1.4**)
- Skill sedimentation (was v0.5, now **v1.5**)
- Font generator / custom fonts (was v0.7, still way later)

**Progress** (as of 2026-04-18): **4 of 11 items done** (rename + #3 + #4 + persistence included-with-#4). Remaining core work: HTML renderer (#6, biggest lift), PPTX renderer (#7), edit_layer tool (#5), landing schema (#8), README polish (#9), demo video (#10), smoke HTML/PPTX assertions (#11). Estimate ~15 h coding + 2-4 h docs/video to v1.0 tag.

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
- New `longcat_design/util/vlm.py` dispatches between Anthropic SDK (Claude path) and OpenAI SDK (Qwen path) by model id. Planner / critic stay 100 % on Anthropic SDK so tool_use protocol is untouched.
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

- **Composite text-overlap detector** (`longcat_design/tools/composite.py`) — new `_detect_text_overlaps` + `_effective_text_extent` helpers compute each `kind: "text"` layer's glyph-inclusive vertical footprint (`max(bbox.h, font_size_px × 1.20)`) and flag every colliding pair. Emits `composite.text_overlap_warning` log events AND appends a ⚠ line to the tool_result summary so the planner sees the collision on the next turn without waiting for a full critique pass. Caught the cached `L_title`↔`L_sub` descender crash on the 2026-04-21 longcat-next run (y_overlap=16 px).
- **Figure ↔ text cross-reference detector** (`_placed_ingest_display_map` + `_detect_missing_figure_xrefs`) — assigns display numbers (Fig. 1 / Table 1 / …) in placed-layer reading order, then checks every text layer's `.text` for `Fig. N` / `Figure N` / `Table N` literals (case-insensitive, period-optional). Orphan figures surface in the composite tool_result + log.
- **Planner prompt** (`prompts/planner.md`) gains two poster-workflow rules: "Text-layer vertical rhythm" (descender clearance geometry + stacked-layer spacing formula + mixed-script concrete template) and "Figure ↔ text cross-reference" (display-number scheme + citation patterns + `edit_layer` fix recipe).
- **Critic rubric** (`prompts/critic.md`) adds typography `−0.15` per text-text bbox collision pair (all posters, not just paper) AND visual-density `−0.10` per orphan figure (capped at −0.30) with `major` severity.

Both detectors are deterministic — they run BEFORE the critic so the planner can self-correct in one iteration instead of burning a critic round. Non-paper posters skip the cross-reference check (no placed `ingest_*` layers → empty display map → early return).

### Remaining paper2any gaps (parked for v1.3+)

- `.docx` / `.pptx` ingestion (needs `python-docx` / `python-pptx` readers).
- Multi-paper fusion (cross-paper figure reuse + ingest cache).
- Scanned-PDF OCR fallback (currently raises `ScannedPdfError`).

---

**North Star.** Today a brief is a single text line. The ideal LongcatDesign: user drops in a paper / PDF / docx / markdown / image bundle → we generate the matching poster, landing page, or slide deck → user iterates through the in-HTML edit toolbar and `apply-edits` round-trip. This is the **paper2poster / paper2page / paper2deck** surface. Not a side feature — *this is the product's end state*. v1.0 ships the single-brief story; v1.1 closes the loop.

The round-trip editability guarantee (v1.0 #5 / #6 / #6.5) extends through ingestion: every extracted section, figure, and heading lands as a named `LayerNode`, so the user can reorder / retype / recolor after generation exactly like a single-brief artifact. Ingestion adds an input surface, it does NOT break downstream edits.

**Scope** (~6-8 h):

- **New tool** `ingest_document(file_paths: list[str])` — accepts PDF / DOCX / MD / TXT / images. Returns structured `{title, authors, abstract, sections[], figures[], tables[], key_quotes[]}`. Implementation uses Claude's native PDF input blocks (Anthropic SDK already supports this — no local `pypdf` / `pymupdf` dependency). DOCX via `python-docx`; MD/TXT pass-through.
- **New tool** `passthrough_image(layer_id, source_path, aspect_ratio)` — copies a user-supplied figure (paper diagram, logo, photo) into `layers/` as a `kind: "image"` layer. NOT NBP-generated. Lives alongside `generate_image` (v1.0 #8.75) so the planner can mix original figures + synthesized hero imagery freely.
- **Planner prompt, new "ingestion-mode workflow" section** — when brief references a file, call `ingest_document` FIRST, then map the returned tree onto the artifact schema:
  - `paper.pdf` → 3:4 academic poster (title + authors + 4 sections + figures + QR)
  - `paper.pdf` + "landing" → marketing landing (abstract as hero, method/results as sections, figures inlined)
  - `paper.pdf` + "deck" → slide deck (one section per slide, figures captioned)
- **CLI entry**: `longcat-design run --from-file paper.pdf "poster for CVPR"` and REPL slash `:attach <path>` that binds a file to the next brief.
- **Round-trip smoke**: `paper.pdf → landing.html → user edits title + reorders sections → apply-edits → new run preserves original figures + user edits`. Gates merge.

**Risk**: Claude's PDF vision has a per-call page cap. Long papers (50+ pages) may need a multi-call "summarize each section" fallback. Time-box exploration: if single-call handles 30-page papers cleanly, ship it; longer-paper handling pushes to v1.1.5.

**Demo targets** for the README / launch video:
- `LLaMA-3.pdf` → claymorphism landing (abstract as hero, method + results as pastel cards, every figure as `passthrough_image`)
- Same PDF → 3:4 academic poster (baseline parity with paper2poster prior art)
- Same PDF → 10-slide editable PPTX deck (once v1.0 #7 ships)

**Why v1.1 and not v1.0**: v1.0's bar is "one-brief to 3 artifacts, round-trip editable, production-grade." Document ingestion is the next leap and the "this is the actual product" moment — it deserves its own launch beat, not a crammed-in v1.0 item.

**Note on the old v1.1 "Multi-image insets" scope**: the landing-page portion already shipped as v1.0 #8.75 (`generate_image` tool). Poster / deck inset generation folds into v1.2.

---

## v1.3 — Landing-page refinement: interactive HTML

**Why**: v1.0 HTML output is static. Real landing pages want CTAs, nav sections, scroll anchors — interactive primitives.

**Scope**:
- `render_cta_button(text, href, style)` tool → emits `<a class="...">` in HTML with `role="button"` styling.
- Section anchors (`<section id="..."`) for nav scroll.
- Optional JS component library (ship as a single inlined `<script>` block in the HTML) for reveal-on-scroll, tabs, accordions — non-framework, just vanilla.
- Accessibility baseline: alt text for every image, semantic HTML (`<header> / <main> / <footer>`).

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
- `longcat-design brand ingest <path.pdf>` CLI command
- Uses Claude vision to extract: palette (named hex values), typography rules (font roles), logo files, imagery mood.
- Stored in `brands/<name>/brandkit.json`.
- Planner loads `brandkit.json` into DesignSpec when user says "use the Acme brand kit" or `--brand acme`.
- Replaces the v0 `fetch_brand_asset` stub.

---

## v1.6 — Skills (reusable design templates)

**Why**: After successful runs, the (brief → trajectory) pair is a template. Lovart calls these "Skills" and they're a strong retention feature.

**Scope**:
- `longcat-design skill save <session_id> --name "academic-poster-cvpr"` → extract design_spec + key prompts as reusable template.
- `longcat-design skill apply <name> --override "title=新标题"` → instantiate new session from skill with overrides.
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

**Scope**: separate project. Not a v1.x priority. Probably ships as `longcat-design-web` as a separate repo that imports the Python package.

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
- **Font fallback diagnostic**: `longcat-design audit <session>` flags fallback events the planner silently accepted.
- **Showcase gallery**: maintain a `showcase/` directory in repo with ~10 diverse high-quality example artifacts + their session JSONs.

---

## Open questions (not yet roadmap'd)

- **Pricing / support model for OSS**: pure MIT + donation link? Enterprise support tier? Not decided.
- **Brand Kit format**: invent our own schema, or adopt Figma tokens / Adobe XD style format for import compat?
- **How heavily do we market the Longcat-Next tie-in**: enough to signal team credibility, without implying LongcatDesign is just a Longcat-Next sales funnel. Needs a marketing decision.
- **Docs site**: mkdocs on GitHub Pages vs dedicated docs domain vs README-only.

---

## Killed / explicitly out of scope

- **LangGraph / CrewAI integration** — see [DECISIONS.md](DECISIONS.md).
- **Training-data dataset publishing** as primary product — trajectory machinery preserved internally; not the pitch.
- **Canva integration** — they have distribution, not us. We aim open formats (HTML/PPTX).
- **Mobile app** — indefinitely out of scope.
- **Real-time multi-user collaboration** — single-session single-user is the model.
