# Decisions Log

Append-only record of non-obvious design choices and the reasoning behind them. **Don't reopen settled questions without reading the rationale here first.** When you make a new decision, add an entry at the top with the date.

Format: each entry has **Decision** (one-line), **Alternatives considered**, **Rationale**, optionally **Revisit when** (conditions that should trigger reopening).

---

## 2026-04-21 — v1.2.5 .docx / .pptx ingest branches + scanned-PDF OCR fallback

**Decision**: Extend `ingest_document` to swallow `.docx` and `.pptx` inputs via their native structural readers (no VLM) and to fall back to VLM-page-OCR when `detect_scanned_pdf` fires on a PDF. All three branches funnel into the same downstream contract: one dict per file with `manifest.sections`, `registered_figure_ids`, and the `rendered_layers` records that composite + planner + critic already know how to consume.

- **`.docx`** (`python-docx`): read the paragraph stream, bucket by nearest preceding Heading-style para, extract embedded images via `doc.part.rels`. Builds the manifest directly — Word's structural metadata is faithful enough that we don't need a VLM call (faster, free, more reliable).
- **`.pptx`** (`python-pptx`, already in deps for writing): each slide = one section (title placeholder → heading, body placeholders → summary + key_points), picture shapes → `ingest_fig_NN` layers.
- **Scanned-PDF OCR** (`_ocr_scanned_pdf`): on scanned detection, render each page at 200 dpi and OCR through `vlm_call_json` in a `ThreadPoolExecutor(6)`. Failures on individual pages degrade to empty strings — partial text is better than a blocking error. Figure / table candidates skip entirely on this path (scanned PDFs have the "figures" baked into page rasters, not separable).
- New shared `_register_image_blob` helper allocates sequential `ingest_fig_NN` ids for docx/pptx images so the existing figure-cross-reference detector (v1.2.4) works untouched.

**Alternatives considered**:
1. **Convert .docx / .pptx to PDF first, then run the PDF pipeline.** Rejected: requires LibreOffice or Word installed (non-trivial CI dep) AND loses structural fidelity — docx heading styles and pptx slide structure are exactly what we want to preserve; round-tripping via PDF throws that away and then we'd pay VLM costs to re-derive what was already there.
2. **Use VLM on .docx too (render pages as images, then structure-extract like PDF).** Rejected: Word is a structured format; ignoring its structure to use VLM is strictly worse (slower, costlier, more error-prone) than reading the tree directly.
3. **Tesseract for scanned-PDF OCR.** Rejected: adds a heavy binary dep (tesseract + language packs) that our users have to install separately. We already have Qwen-VL-Max wired via `util/vlm.py`; running OCR through it is zero new deps. Quality is similar or better (modern VLMs match or beat Tesseract on anything non-trivial), language coverage is inherent, and per-page cost (~$0.001) is irrelevant next to planner / critic spend.
4. **Keep raising `ScannedPdfError`.** Rejected: the whole point of the parked item was to let scanned papers work. If a PDF is scanned, the user usually doesn't know or care — they just want the poster.

**Rationale**: Adding two structural readers + one VLM-OCR loop unlocks the three most common "my input is a non-PDF" complaints in one commit. The existing downstream contract is powerful enough that each branch is <100 lines and none need composite / renderer / critic changes. Dep footprint grows by one (`python-docx`); `python-pptx` was already in for deck writing. Smoke gains two no-API checks that run against fixtures built inline in the smoke file (no binary test data in-repo).

**Revisit when**: (a) docx tables become common in user input → add a table-extraction path reusing the existing `kind: "table"` layer shape; (b) scanned PDFs frequently contain diagrams we'd want to extract → add a VLM-driven page-region crop pass on the OCR fallback path; (c) `.rtf` / `.pages` / other formats surface repeatedly → add more branches (trivial).

---

## 2026-04-21 — v1.2.4 deterministic text-overlap + figure-xref detectors in composite

**Decision**: Close the planner↔critic loop in ONE turn for two recurring paper-poster failure modes by running deterministic detectors inside `composite` and appending their findings to the tool_result summary (the planner reads it on the next turn). Two checks:

1. **Text-overlap detector** (`_detect_text_overlaps` + `_effective_text_extent`) — compute glyph-inclusive extent `max(bbox.h, font_size_px × 1.20)` for every `kind: "text"` layer, flag every colliding pair, emit `composite.text_overlap_warning` log + ⚠ summary line. Catches the canonical `L_title` descender ↔ `L_sub` cap-height crash (real case from the 2026-04-21 longcat-next run: title at y=100 h=200 fs=180 → effective footprint y=316; sub at y=300 → 16 px collision).
2. **Figure ↔ text cross-reference detector** (`_placed_ingest_display_map` + `_detect_missing_figure_xrefs`) — assign display numbers to placed `ingest_fig_NN` / `ingest_table_NN` layers in reading order (first-placed = Fig. 1 on the poster, even if it was paper Fig. 7), scan every text layer's `.text` for `Fig. N` / `Figure N` / `Table N` (case-insensitive, period optional), list orphans in the summary. Non-paper posters skip entirely (empty display map → early return).

Planner + critic prompts gain matching rules so expectation (prompt) and enforcement (detector + critic) stay aligned.

**Alternatives considered**:
1. **Only extend the critic rubric, no detector.** Rejected: wastes a critic iteration on a mechanical check. Deterministic geometry catches the issue in ≪ 1 ms + $0 instead of a full critic round + LLM call.
2. **Auto-fix the collision at composite time** (shift the lower layer's y). Rejected: silently mutates the planner's DesignSpec. The planner should learn to emit correct bboxes; surfacing the warning in tool_result teaches the loop without hiding the error.
3. **Use the paper's figure numbers** (`ingest_fig_01` → Fig. 1 regardless of placement order). Rejected: the poster is a new narrative arc, not a paper reproduction — the first figure the viewer sees should be "Fig. 1" in the poster's reading order. Paper figure numbers live in the caption text for reference.
4. **Extract display numbers by parsing "Fig. N" out of text layers**. Rejected: requires solving an NER task and degrades to "no cross-refs found" when the planner omits citations. Assigning in reading order matches how a designer would actually do it.

**Rationale**: Both detectors run on every composite and close the planner↔critic loop by giving the planner actionable diagnostics in the same observation it used to produce a critic-flagged poster. The `composite.text_overlap_warning` log event also becomes self-supervision data for future prompt tuning (grep trajectories for frequent collision patterns, refine the vertical-rhythm rule).

**Revisit when**: (a) planner citations drift to patterns like "见图 2" / "參考 Fig. 2" → extend the regex or move to an NLU check; (b) CJK title descender characteristics differ enough from Latin that the `1.20` multiplier is wrong → split the multiplier per script.

---

## 2026-04-21 — v1.2.1 composite aspect-preserve for image + table layers (commit `349c899`)

**Decision**: Treat the planner's `bbox.w / bbox.h` ratio as a *desired placement*, not a *forced output aspect*. In `composite.py`, image layers contain-fit (letterbox-style) via new `_aspect_fit_contain(src_size, dst_size) -> (nw, nh, off_x, off_y)` across `_write_preview` + `_write_psd` + `_write_svg` (SVG uses the native `preserveAspectRatio="xMidYMid meet"` attribute). Table layers **re-render at the planner's exact bbox dims at composite time** — call `render_table_png(rows=…, headers=…, width_px=bw, max_height_px=bh, col_highlight_rule=…)` instead of resizing the pre-baked PNG. A new `composite.bbox_aspect_warning` log fires when bbox aspect deviates ≥ 2× from source aspect, for planner-prompt tuning data.

**Alternatives considered**:
1. **Force planner to always pick correct bboxes** via prompt. Rejected: prompt rules are advisory; planners drift. The composite layer is the deterministic backstop.
2. **Cover-fit (crop overflow)** instead of contain-fit for images. Rejected: crops lose figure content (benchmarks / architecture diagrams have no disposable edges). Letterbox preserves content; the only cost is a transparent margin that the z-stack happily ignores.
3. **Auto-expand bbox** when aspect mismatch detected. Rejected: would overlap other layers — not worth the placement-correctness complexity for this gain.
4. **Pre-bake table PNG at source aspect + letterbox like images**. Rejected: table text doesn't scale — letterboxing a wide source table into a square bbox means the rendered text is still wide + short + unreadable. Re-rendering at bbox dims lets `render_table_png`'s row-count autoscale kick in at the real size (font shrinks, rows truncate gracefully when `max_height_px` is tight).

**Rationale**: v1.2 planner rules pushed paper posters from 1-figure to 5-figure layouts — but early runs still produced a 900×110 strip for a 14-row benchmark table (LANCZOS-squished to 13 px per row). Aspect-preserve is the layer-agnostic fix: images degrade gracefully to letterbox, tables degrade gracefully via font autoscale + row truncation. Verified against v6 baseline (table 1376×190 squished) vs v7 (table 680×360 re-rendered, all 14 rows readable, 8 winner cells still bolded).

**Revisit when**: (a) we see frequent `bbox_aspect_warning` events for a specific figure category → tune planner prompt to allocate better; (b) we add a real vector-native PSD text layer (v1.4) → SVG-style native aspect on PSD too.

---

## 2026-04-21 — v1.2 poster visual-density rules: ≥ 4 figures or it's not a poster (commit `a08bbb9`)

**Decision**: Encode the "visual-first, not text-first" definition of an academic poster directly into both the planner prompt (prescriptive) and the critic prompt (enforcement). Planner hard rules: figure-count floor (≥ 4 placed when ≥ 5 available; ≥ 6 when ≥ 10), figure diversity (1 system diagram + 1-2 qualitative + 1-2 quantitative), image-area target ≥ 45 % of canvas, min 600 px short side per figure, text density caps (≤ 30 words / body layer, ≤ 8 words / title, ≤ 6 words / section heading). Critic rubric rebalanced: added `visual_density` criterion at weight 0.20, with explicit per-violation penalties (−0.40 if ≤ 1 figure placed despite ≥ 3 available = **blocker**; −0.25 if < 3 placed when ≥ 5; −0.15 if registered table missing; −0.20 if image-area < 45 %; −0.15 if table height < 400 px; −0.10 if figure short-side < 300 px; −0.10 if body text > 180 words total).

**Alternatives considered**:
1. **Just update the critic, not the planner.** Rejected: produces "revise" loops that never actually converge because the planner doesn't know what to change. Both ends need the contract.
2. **Generic "prefer visuals" guidance.** Rejected: generic rules don't change behavior. Hard numeric floors are measurable; planners and critics can check them exactly.
3. **Ship only the ingest-summary enrichment** (the per-figure catalog) and let the planner figure out density itself. Rejected: dogfood showed the planner picks `fig_01` and moves on even when shown a 20-figure ranked list. The density rule is necessary in addition to visibility.
4. **Unlimited figure catalog in ingest summary.** Rejected: 43-figure papers pushed the planner's output budget over `max_tokens` before it could emit `propose_design_spec`. Capped at top 20 ranked by `(has_caption, is_vector, min_side_px, -page)`.

**Rationale**: Pre-rule baseline was 1 figure + 3 columns of body text on the poster — a research-paper's page 1 printed as PNG, not a conference artifact. Post-rules: 4-5 figures + 1 table, critique 0.62 → 0.86, image-area 12 % → 58 %. The critic rubric carries the same weights; violations now show up as concrete deduction lines in the issues array so the planner's next revision knows exactly what to fix.

**Revisit when**: (a) non-paper posters start hitting the visual-density penalty (brief doesn't mention a paper → `visual_density = 1.0` default, but if we grow other paper-like briefs we'll need the detection to widen); (b) density floor feels too aggressive for short papers (< 5 figures available) — already have conditional on `n_figures_available ≥ 5`.

---

## 2026-04-21 — v1.2 `kind="table"` LayerKind with native PPTX / HTML / PIL renderers (commit `da664a5`)

**Decision**: Treat paper tables as **structured data with native renderers**, not cropped images. New `LayerKind "table"` with `rows: list[list[str]]`, `headers: list[str]`, `caption: str`, `col_highlight_rule: list[str]` (per-column `"max"` / `"min"` / `""`) fields on `LayerNode`. Ingest uses pymupdf `page.find_tables()` for **localization only** (cell splits are unreliable on paper layouts — drops headers into single cells, misclassifies figure panels), then sends each candidate region's cropped PNG + pymupdf's raw-cell guess to Qwen-VL-Max, which returns clean structured data or `is_table: false` (reject). Renderers: deck → native `slide.shapes.add_table(…)` with column-width + font-size autoscale + bold-winner cells; landing → real `<table>` with `.ld-table-winner` class; poster / PSD / SVG → PIL-drawn PNG via `util/table_png.render_table_png` with deep-green winner text (the bundled NotoSansSC is bold-only so color carries the highlight instead of font weight).

**Alternatives considered**:
1. **Crop tables as images like figures** (the pre-v1.2 default). Rejected: tables have text, and text in cropped images is tiny and unreadable at deck / poster scale. Every dogfood run confirmed: "the histograms look great, but the benchmark numbers are a gray smudge".
2. **Trust pymupdf `find_tables().extract()` for cell content.** Rejected: on the test paper it misclassified a figure collage (p. 14) as a 3×5 table and jammed a 12-column header ("MMMU MathVista OCRBench…") into a single cell on p. 16. VLM-validated parsing gives clean data + rejection for non-tables.
3. **Second LLM call just for layout validation**, re-parse cells ourselves. Rejected: more code, more chances to drift from source truth. A single VLM call that does both "is this a real table?" and "if yes, here's the clean parse" is simpler and the VLM is already in the hot path for figure caption matching.
4. **Render poster tables with a real bold font loaded separately.** Rejected for v1.2: bundled project fonts are `NotoSansSC-Bold.otf` + `NotoSerifSC-Bold.otf` — no regular weight. Adding a regular Noto SC would add ~8 MB to the repo; deep-green color on winner cells reads as distinctly as bold without the asset cost.

**Rationale**: Tables are the single most-crop-hostile content in papers. Rendering them as structured primitives unlocks three wins simultaneously: (a) deck tables are **editable** in PowerPoint — the user can swap a metric or update a number without re-running the pipeline; (b) landing tables scale with viewport width via CSS; (c) poster tables carry all 14 rows at legible resolution because the PIL renderer picks row height from bbox. Winner highlighting (`col_highlight_rule`) is cheap VLM output (~80 extra tokens per table) but high narrative leverage — it draws the eye to exactly the point the paper is making.

**Revisit when**: (a) papers with > 2-row-header tables start failing — prompt already asks the VLM to flatten to "Parent / Child" strings; if that degrades, add post-processing; (b) scanned PDFs with image-embedded tables appear — the VLM parse path already handles this but we'd need to relax `find_tables()` localization; (c) we add a regular Noto SC weight — switch poster renderer to bold-winner-by-weight instead of green-by-color.

---

## 2026-04-21 — v1.2 Qwen-VL-Max via OpenRouter as default ingest model; dual-SDK dispatcher (commit `ce50f2a`)

**Decision**: Default `ingest_model` = `qwen/qwen-vl-max` (via OpenRouter) instead of `anthropic/claude-sonnet-4-6`. Introduce `longcat_design/util/vlm.py` as a neutral dispatcher with `vlm_call_json(...)` that routes by model id: `anthropic/` / `claude-` → Anthropic SDK; `qwen/` → OpenAI SDK against OpenRouter's OpenAI-compat `/api/v1`. Ingest is the **only** surface that uses the OpenAI SDK — planner and critic stay 100 % Anthropic SDK to preserve tool_use protocol compatibility (the OpenAI-compat endpoint's tool_use shape is different enough to break them). Override via `INGEST_MODEL` env var; stock-Anthropic mode falls back to `claude-sonnet-4-7`.

**Alternatives considered**:
1. **Stay on Claude Sonnet 4.6 for ingest.** Rejected: Sonnet 4.6's vision grounding was unreliable enough to motivate the whole pymupdf refactor — but even post-refactor, its "read this paper" performance is slower and ~5× more expensive than Qwen-VL-Max for the same non-reasoning workload (structure extraction + caption matching + table parsing).
2. **Bump to Claude Sonnet 4.7.** Better grounding than 4.6, but still the ~5× cost gap vs Qwen for this workload. Kept 4.7 as the stock-Anthropic fallback when `OPENROUTER_API_KEY` is absent.
3. **Route everything through OpenRouter's Anthropic-compat endpoint** to keep a single SDK. Not possible: OpenRouter serves Qwen only via the OpenAI-compat endpoint (Alibaba DashScope's native API shape).
4. **Write our own thin HTTP client** instead of pulling in `openai`. Rejected: the `openai` SDK handles retries, streaming, base64 / multipart image encoding, rate-limit backoff, and the tail of quirks between OpenAI-compat providers. Adding one dep is cheaper than maintaining those ourselves.
5. **Qwen-VL-Plus instead of Qwen-VL-Max.** Plus is cheaper but didn't reliably refuse fake figures (it accepted black placeholders as "a rectangle figure"). Max's reject quality is what makes the pymupdf-everything-VLM-filter approach work.

**Rationale**: The v1.2 pymupdf refactor reduced the VLM's job from "localize and identify figures" (hard, needed spatial grounding) to "read text and match captions" (easy, commodity VLM territory). That's Qwen-VL-Max's sweet spot. The dual-SDK dispatcher is localized to one module (`util/vlm.py`) and one call site (`tools/ingest_document.py`) — it doesn't leak into planner or critic. Cost data on the 43-page Longcat-Next paper: old Sonnet 4.6 ingest ~$1.80 per run; Qwen-VL-Max ~$0.35 per run (caption matching × 153 candidates is the hot loop, parallelized 6 wide).

**Revisit when**: (a) Qwen-VL-Max reject quality drops on a specific paper category (e.g. medical imaging with lots of annotated grids) → A/B against Sonnet 4.7; (b) OpenRouter adds an Anthropic-compat Qwen endpoint → collapse back to single SDK; (c) we want ingest to run fully offline → local Qwen-VL via Ollama, same OpenAI-compat dispatch.

---

## 2026-04-21 — v1.2 pymupdf-native figure extraction replaces Claude-vision bbox locator (commit `ce50f2a`)

**Decision**: Drop the Claude-Sonnet vision "find the figure bboxes on this rasterized page" step entirely. Use pymupdf **directly** for figure localization: `page.get_images(full=True)` + `doc.extract_image(xref)` pulls every embedded raster image at its **native author-uploaded resolution** (lossless — the PDF's actual bytes); `page.get_drawings()` clustered by `_merge_rects(raw_bboxes, tol=12pt)` + filtered by `min_side_pt=80` / `max_area_frac=0.80` + rendered at **300 dpi** catches vector diagrams (architecture illustrations, pipeline flows). Dedup raster vs vector per page (raster wins on ≥ 80 % containment + min-side ≥ 200 px). VLM (Qwen-VL-Max) only handles caption matching + fake-figure filtering on the already-cropped candidates.

**Alternatives considered**:
1. **Upgrade the VLM locator** from Sonnet 4.6 to Sonnet 4.7 (better vision grounding) or Qwen-VL-Max. Rejected: "guess pixel bboxes on a rasterized page" is the **wrong abstraction** for this problem. The figure bboxes are already **encoded in the PDF**; the vision model is inherently worse than reading them directly. No amount of model-swap fixes "rasterizing text + guessing coordinates".
2. **Layout-parsing libraries** (pdfplumber, PyPDF2, pdfminer.six). Rejected: all of them are text-centric; none handle the "extract embedded raster bytes + separate vector drawings" workflow cleanly. pymupdf covers the full stack (text + images + drawings + tables) in one import.
3. **Ship the old locator as fallback** in case pymupdf misses something. Rejected for YAGNI: in dogfood on the Longcat-Next paper pymupdf found 135 raster + 18 vector candidates; the VLM caption-matching filter drops the 110 noise ones (black placeholders, equation renders, snowflake icons, horse photos). There's no failure mode the locator catches that the current path misses.

**Rationale**: The Sonnet 4.6 locator produced half-page screenshots (Fig. 1 came back as the top half of page 1 with abstract text + GitHub URLs + histograms all bundled together), clipped diagrams (Fig. 2 was the title-banner-only top strip), and hallucinated figures on text-only pages. None of these are model-quality failures — they're architectural failures of "rasterize the page, ask a VLM to paint rectangles". pymupdf native gives us author-uploaded PNG bytes directly, which is what the old pipeline was trying to approximate through two lossy steps. Dogfood results: Fig. 1 crop went from 1454 × 820 (with text contamination) to 1890 × 1211 (pure histograms, zero post-processing); slides no longer have blurry table cells because the source PNG is already print-resolution.

**Coupled decision — `detect_scanned_pdf` guard**: If the whole PDF has almost no extractable text AND zero vector drawings, it's almost certainly a scanned PDF we can't figure-extract from. Raise `ScannedPdfError` with a clear user message rather than silently returning zero figures.

**Revisit when**: (a) we add scanned-PDF OCR support → the detector becomes a dispatch fork, not a hard error; (b) a paper format uses SVG-embedded figures (LaTeX with `\includegraphics{*.svg}` compiled via `svg.latex`) that `get_images()` misses → add a third extraction strategy for embedded XObject SVGs.

---

## 2026-04-20 — v1.1 paper2any: polymorphic `ingest_document` + Sonnet default + pymupdf figure cropping

**Decision**: Ship v1.1 document ingestion as a **single polymorphic tool** (`ingest_document(file_paths: list[str])`) dispatching by file extension — not a family of sibling tools (`ingest_pdf` / `ingest_markdown` / `passthrough_image`). Use **Sonnet 4.6 by default** (via `settings.ingest_model`) for the PDF structure-extraction + bbox-locator calls, not Opus. Crop figures with **pymupdf** locally and passthrough the original pixels — do NOT re-generate paper figures via NBP. Register ingested figures in `ctx.state["rendered_layers"]` with stable `ingest_fig_NN` / `ingest_img_<sha8>` layer_ids so the planner can reference them as image children in `propose_design_spec.layer_graph` with NO `src_path` — composite hydrates automatically (same pattern as generate_image for landing/deck).

**Alternatives considered**:
1. **Three sibling tools**: `ingest_pdf`, `ingest_markdown`, `passthrough_image`. Rejected: planner prompt vocab would triple; all three share the same success envelope (produce layer_ids + return manifest summary); the extension dispatch is a 10-LOC switch inside one tool. One tool, one mental model.
2. **Opus for structure extraction** (same as planner). Rejected: dogfood on a 43-page / 17 MB Longcat-Next paper showed Opus taking 28 min and timing out on OpenRouter. Sonnet 4.6 completes the same task in ~5 min at ~1/5 the cost with no degradation — "extract title/sections/figures from a PDF" is not a reasoning task.
3. **Re-generate paper figures via NBP** for "stylistic consistency." Rejected: defeats the whole point of paper2poster for academic use. A research figure is a specific artifact (benchmark chart, architecture diagram); restylizing it via NBP breaks fidelity. Users who want stylized hero imagery call `generate_image` for NEW visuals the paper doesn't provide.
4. **No figure extraction** — let Claude describe figures and call `generate_image` with the description. Simplest, but same fidelity loss as #3. Passthrough is the academic-poster killer feature.
5. **pdf2image / pypdfium2 instead of pymupdf**. Rejected for now: pymupdf is mature, MIT-equivalent for runtime use (Artifex AGPL dual-licensed, but runtime-library consumers are fine), no system deps (vs pdf2image needing poppler). Revisit if we need to hard-ship under pure BSD/MIT — pypdfium2 has same API shape.

**Rationale**: The polymorphic tool keeps the planner prompt tight (one tool description, one decision point — "do I have attached files?"). Sonnet for ingest is a 3-5× speedup + 5× cost saving vs Opus with no quality hit on extraction. pymupdf passthrough preserves research fidelity for the CVPR-style academic poster use case the paper2any North Star calls out. Hydration pattern is now a three-peat (landing + deck + poster all use it); refactoring into a shared helper is future hygiene, not a blocker.

**Coupled decision — poster image layer support**: The poster composite path previously assumed every non-background layer was a `render_text_layer`-output (full-canvas transparent RGBA with glyphs cropped at bbox). Ingested image layers (native-sized PNG, `kind="image"`) broke that assumption. Added `_hydrate_poster_layer_bboxes` (copies bbox from spec.layer_graph onto rendered_layers records that lack one — ingested figures register with bbox=None) + new `elif kind == "image"` branches in `_write_psd` / `_write_svg` / `_write_preview` / `html_renderer.write_html` that resize the native-sized PNG to bbox dimensions. Symmetric to the landing+deck `<figure>` / picture-shape image paths.

**Resilience choices captured during dogfood**: `manifest["figures"]` coerced from `None` → `[]` (Sonnet sometimes emits null for figure-light papers — setdefault is a no-op when key is present-with-None). Bbox-locator failures per-page fall back to full-page passthrough for that figure instead of killing the run. 10-min HTTP timeout on the ingest Anthropic client (env-overridable via `INGEST_HTTP_TIMEOUT`) — big PDFs shouldn't hang 20+ min silently.

**Revisit when**: (a) `.docx` + `.pptx` ingestion required → adds `python-docx` dep, likely second dispatch path for Office XML (v1.2); (b) multi-paper fusion requested (currently `file_paths` accepts multiple but planner treats them as independent attachments) — would need a new `consolidate_documents` tool; (c) Sonnet 4.6 bbox quality drops on visually dense pages → switch to Opus via `INGEST_MODEL=anthropic/claude-opus-4.7`; (d) figure extraction quality issues → evaluate pypdfium2 / pdfplumber as pymupdf alternatives; (e) `paper2any.reuse_ingest` ships → ingest_document gains a "load from previous run_dir" fast path to skip re-ingestion on retries.

**Branch / commit**: `dc93960` on `main` (FF-merged from `feat/training-data-capture`). 11 tools, smoke 16/16, real-paper paper2poster dogfood completed on 43-page Longcat-Next paper with 25 passthrough figures into a 20-layer 1536×2048 poster.

---

## 2026-04-20 — Enable Claude extended thinking on Planner + Critic via `interleaved-thinking-2025-05-14` beta; trajectory schema v0 → v1

**Decision**: Turn on Anthropic extended thinking for both `PlannerLoop` and `Critic`, with the `interleaved-thinking-2025-05-14` beta header so thinking blocks may also appear *between* tool calls. Capture every `thinking` / `redacted_thinking` content block into a new `AgentTraceStep(type="reasoning")` with the original `signature`. Default `budget_tokens=10000` per call, env-overridable via `PLANNER_THINKING_BUDGET` / `CRITIC_THINKING_BUDGET` / `ENABLE_INTERLEAVED_THINKING`. Also record `stop_reason` + `cache_read_input_tokens` + `cache_creation_input_tokens` per turn. Trajectory `metadata.version` bumps `v0` → `v1`; all new fields are optional for backward compat.

**Alternatives considered**:
1. **Standard thinking only** (no interleaved beta). Rejected: standard thinking emits blocks only at the start of each `messages.create` turn, so once a tool is called the model's *per-tool-decision* reasoning is invisible. Interleaved is the mode that produces high-quality tool-use CoT SFT/RL data — the whole point of this capture.
2. **Thinking on Planner only, not Critic**. Rejected: Critic's `rationale` field is short and under-reasoned; without CoT we can't train a reward model that distinguishes "correct verdict, good justification" from "correct verdict, lucky guess." Cost per run is bounded (1-2 critic calls max via `max_critique_iters=2`).
3. **Defer thinking capture until we're sure we'll train on it** (just store raw responses). Rejected: the marginal cost of wiring extended thinking now is small (~100 LOC total); deferring means every trajectory collected before the cutover is CoT-less. Collect data in the richest shape available.
4. **Put thinking in a sidecar file** instead of inline in trajectory JSON. Rejected (for now): trajectory size blows up ~2-4× but stays in the low-100s-KB range; single-file simplicity for SFT loaders outweighs the size. Revisit if trajectories exceed ~500 KB.

**Rationale**: Addresses the single biggest gap in the training-data pipeline per [DATA-CONTRACT.md § Lane 6](DATA-CONTRACT.md). The existing `type="thought"` steps are polished text between tool calls ("*Now I'll generate all 10 images in parallel.*"), not reasoning — can't ground a CoT SFT on them. With interleaved thinking, every tool choice is preceded by an actual reasoning block that the model was free to make arbitrarily long.

**Schema evolution**: Per [DATA-CONTRACT.md schema evolution policy](DATA-CONTRACT.md), old v0 trajectories continue to load since all new `AgentTraceStep` fields default to `None`. Smoke check `[14/14] reasoning step + ThinkingBlockRecord roundtrip` covers the new fields including redacted blocks. Trajectory size grows but remains JSON-queryable; no external data store needed.

**Guardrails**:
- `messages.append({"role":"assistant","content":resp.content})` in planner.py **must not** be rewritten by hand — the SDK's raw content block list carries opaque `signature` fields that Anthropic verifies on next turn. Replay breaks silently otherwise. Comment added in planner.py docstring + at call site.
- `max_tokens` assertion added in planner.py: must strictly exceed `budget_tokens`, else `messages.create` 400s.
- `budget_tokens=0` keeps the feature fully off (dev / cheap runs); no prompt changes required.

**Revisit when**: (a) OpenRouter stops passing the interleaved beta header through to Anthropic (trigger: a real run 400s with the beta enabled — fallback to `ENABLE_INTERLEAVED_THINKING=0`); (b) trajectories routinely exceed ~500 KB (spin out thinking to `out/runs/<run_id>/thinking.jsonl` sidecar); (c) we need thinking on Gemini NBP — would require a separate capture path since NBP doesn't expose CoT blocks; (d) Anthropic deprecates `interleaved-thinking-2025-05-14` — migrate to the successor beta header with the same capture pattern.

**Branch**: `feat/training-data-capture` — umbrella for subsequent training-data-related PRs (NBP metadata / DPO pairs / edit lineage / raw-response sidecar). Does not merge into main until a few related commits are stacked, to avoid churning reviewers.

---

## 2026-04-20 — Deck uses `kind="slide"` in existing LayerNode tree (no separate `DeckStructure` model); critic is text-only

**Decision**: v1.0 #7's PPTX renderer reuses the existing `LayerNode` tree with a new `kind="slide"` literal — top-level slide nodes, each with `children[]` holding positioned `text` / `image` / `background` elements via pixel `bbox`. No parallel `DeckStructure` pydantic model. The critic for deck mode is **text-only** (mirrors landing) — grades the slide tree / density / arc from the DesignSpec without sending any vision input.

**Alternatives considered**:
1. **Separate `DeckStructure` / `SlideNode` models** (as suggested loosely in V1-MVP-PLAN.md row 7). Rejected: pattern-match from landing (which already introduced `kind="section"` into the same `LayerNode` tree) says reuse. A parallel model would duplicate `bbox` / `children` / z-index plumbing for zero gain — the runtime, apply-edits (future), and trajectory shape all benefit from one node type.
2. **Vision-based critic on a stitched multi-slide PNG**. Rejected: (a) for 20-slide decks the stitched PNG risks the 5 MB vision input cap; (b) the Pillow-rendered per-slide PNG is a simplified approximation — PowerPoint/Keynote do the real type layout, so grading the preview grades the approximation, not the artifact; (c) text-only gets us consistent architecture with landing and the DesignSpec IS authoritative for deck structure.
3. **Skip the critic for deck entirely in v1.0** — ship deck without self-review. Rejected: breaks the UX parity across the 3 artifact types (poster + landing both have critic; absence on deck feels unfinished).

**Rationale**: Reusing `LayerNode` is a 1-line schema change (`LayerKind += "slide"`); the existing `children[]` walking machinery in composite + hydration patterns (`_hydrate_landing_image_srcs` → `_hydrate_deck_image_srcs`) transfer directly. Text-only critic mirrors the `_evaluate_landing` branch almost exactly — a new `_evaluate_deck` + `_build_deck_user_text` helper + `prompts/critic-deck.md` rubric, no vision content block. Smoke [13/13] `check_deck_mode` verifies the .pptx reopens cleanly with correct slide count + native text runs + picture shapes.

**Revisit when**: (a) a user complains that the critic misses visual bugs a renderer-of-the-actual-pptx would catch (would bring back a *headless LibreOffice / Keynote CLI render* path, not the Pillow approximation); (b) deck gets table / chart / shape elements that can't be expressed in the current `LayerNode` polymorphic shape (would motivate a dedicated `SlideElement` union type).

---

## 2026-04-19 — NBP generates landing imagery too, via a NEW tool `generate_image` (not overloaded `generate_background`)

**Decision**: Introduce a second image-generation tool dedicated to landing-mode inline imagery. `generate_background` stays poster-only (full-canvas, text-free, has `safe_zones`); `generate_image` is landing-only (inline in a section's `children[]`, no `safe_zones`, flow layout). Both hit NBP under the hood but are semantically separate in the tool vocabulary.

**Alternatives considered**:
1. **Overload `generate_background`** with a `kind` parameter or context-detect. Rejected: "background" literally means "behind everything, full canvas" — using it for a `80×80` feature-card icon breaks the mental model and makes the prompt schema's `safe_zones` semantically weird.
2. **Defer landing imagery to v1.1** (keep landings text-only for v1.0 launch). Rejected after user feedback: a text-only landing "能用 but 不能商业化," landing as a product category requires imagery to read as real.
3. **Use a third-party stock image service** (Unsplash / Pexels) instead of NBP. Rejected: kills the "one-conversation one-landing" pitch — users would have to curate images separately. NBP is the differentiator.

**Rationale**: Clean separation of semantics in the tool registry was cheap (1 new file `tools/generate_image.py`, ~90 LOC) and pays off in planner prompting — the per-style "Imagery prompts" guides can speak directly to `generate_image` without carrying the "this tool is also used for full-canvas backgrounds" caveat. Verified by milk-tea brand dogfood (run `20260419-204503-b5300878`): 10 tools wired, planner correctly picked `generate_image` for 5 landing slots (1 hero + 4 feature icons), $2.20 total, critic pass 0.94.

**Coupled decision — hydrating image src_path into the section tree**: The planner uses a two-step flow — declare the image layer inside a section's `children[]` via `propose_design_spec` (structure), then separately call `generate_image(layer_id=...)` (content, writes PNG to `rendered_layers`). The DesignSpec's `children[]` initially has `src_path=None`. Before composite renders the HTML, a new helper `_hydrate_landing_image_srcs` walks the section tree and copies `src_path` from `rendered_layers` onto matching children via `model_copy(update=...)`. This keeps the two sources of truth consistent without the planner having to re-issue `propose_design_spec` after every `generate_image` call.

**Revisit when**: (a) NBP's API gains native negative-prompt support (we could then consolidate generate_background + generate_image if they become truly symmetric); (b) users consistently want non-NBP images in landings (would need a third tool `fetch_image(url)` or similar).

---

## 2026-04-19 — Landing design systems ship as BUNDLED in-repo assets, NOT as references to user's local skill files

**Decision**: All 6 landing design-system guides + their CSS live inside the repo at `prompts/design-systems/*.md` and `assets/design-systems/*.css`. The planner reads the bundled guides; the HTML renderer loads the bundled CSS via `_load_design_system_css` and inlines it. The package is completely self-contained — no dependency on the user's local `~/.claude/skills/` or anywhere else.

**Alternatives considered**:
1. **Reference `~/.claude/skills/ccg/domains/frontend-design/*` directly at runtime**. Rejected immediately once we realized this is a distribution bug: external users who `pip install longcat-design` don't have those skill files on their machine, so the planner would get empty guides and the renderer would have no CSS.
2. **Distill to ONE "design system guide" covering all 6 styles in a single file**. Rejected: too coarse — planner needs style-specific vocabulary (claymorphism's "soft 3D clay render" prefix, neubrutalism's "thick black outlines + saturated candy colors," etc.), not a generic lecture.
3. **Ship CSS as Python string constants in `html_renderer.py`**. Rejected: ~500 LOC of CSS in a Python file is unreadable and makes CSS changes require Python edits + imports. Splitting to `.css` files + runtime `read_text` is cleaner and lets CSS tooling (formatters, linters) work naturally.

**Rationale**: Product-distribution correctness (user's pointed-out concern) + maintainability. The dev-time workflow was: read my local skill files → distill patterns into LongcatDesign-specific guides (NOT copy-paste, to avoid license questions and because skill files are too generic for a landing-with-contenteditable use case) → write fresh CSS tuned to our specific HTML structure. Runtime workflow: zero external lookups.

**Style loudness ladder (codified in `prompts/design-systems/README.md`)**:
- 3/10 `minimalist` (Stripe/Linear) — default if in doubt
- 3/10 `editorial` (NYT magazine) — publications, long-form
- 5/10 `claymorphism` (soft 3D pastel) — friendly consumer
- 5/10 `liquid-glass` (Apple premium) — media-rich, design-forward
- 6/10 `glassmorphism` (aurora frosted) — AI/SaaS with color energy
- 10/10 `neubrutalism` (candy + hard shadows) — indie / devtool with attitude

**Revisit when**: (a) the 6 styles need a 7th (e.g. Material You, skeuomorphism) — plug in one more `.md` + `.css` pair, update the LandingStyle Literal and the README ladder; (b) Anthropic adopts a public skill-distribution channel — we could then re-link instead of bundle to reduce repo size.

---

## 2026-04-19 — Landing critic is TEXT-ONLY; poster/deck critic stays vision-based

**Decision**: `Critic.evaluate` branches on `design_spec.artifact_type`. For LANDING, it skips the preview-PNG vision input entirely and grades against a content-level rubric in `prompts/critic-landing.md` (section composition, copy quality, design-system fit, typography hierarchy, content pacing). For poster/deck, it keeps the existing vision-based rubric in `prompts/critic.md`.

**Alternatives considered**:
1. **Use vision on landing anyway, just live with the false fails**. Rejected: the first milk-tea dogfood (run `20260419-192002-bfcf00b0`) produced a perfect claymorphism landing but the critic, seeing the simplified Pillow-rendered `preview.png` (no CSS, tofu-square emojis, grid layout broken), called it "fail, 0.18, 8 blockers." This would poison trajectory.critique_loop for every landing and undermine user trust in critique signals.
2. **Render the real HTML via headless browser (Playwright/Selenium) for the critic**. Rejected for v1.0: adds ~150MB dep + binary setup complexity, slows critique from ~2s to ~20s+, and gives marginal value when the DesignSpec itself already encodes everything a critic needs (section tree, fonts, colors, copy).
3. **Skip critique entirely for landing**. Rejected: critic still catches real issues — content pacing, missing sections, style/brief mismatch, copy length — that add genuine value without needing a rendered image.

**Rationale**: The DesignSpec for a landing IS the source of truth. The HTML is a deterministic render of it. Asking a vision model to grade a bad render of a good spec is strictly worse than asking it to grade the spec directly. Confirmed by re-run on the same milk-tea brief (run `20260419-204503-b5300878` with the fix): 0.94 pass, 2 minor issues — matches what a human would call "commercially shippable."

**Companion fix — `IssueCategory` widened**: added `"copy"` and `"content"` to the Literal so the landing critic's natural categories (copy quality, content balance) don't fail Pydantic validation. Doesn't affect the poster/deck critic (those categories just go unused there).

**Revisit when**: (a) landing HTMLs reliably get rasterized with full CSS fidelity (Playwright deps become acceptable, or a lightweight CSS-aware raster emerges); (b) text-only critique misses a class of bugs that only a visual inspection would catch — so far (milk-tea pass 0.94 matches user verdict) there's no evidence of this.

---

## 2026-04-19 — `edit_layer` scope: within-turn critique-revise helper, NOT cross-turn chat edit (verified by dogfood)

**Decision**: Keep `edit_layer` reading from `ctx.state["rendered_layers"]` (live per-turn blackboard). Do NOT extend it to read from the prior Trajectory on disk. Its primary value lies in the **within-turn critique-revise loop**, not cross-turn chat edits.

**Alternatives**:
1. **Extend `edit_layer` to reconstruct state from prior Trajectory** (the "Route X" from planning) — would make cross-turn `:edit foo.bar=baz` or chat-turn "make title bigger" hit edit_layer directly, but breaks the 2026-04-18 ToolContext isolation decision and doubles the tool's responsibility.
2. **Make `edit_layer` read from `ctx.state["design_spec"].layer_graph`** — changes the source of truth from "what was rendered" to "what was declared", breaks the principle that edit_layer edits _rendered_ artifacts.
3. **Status quo (chosen)** — cross-turn revisions use `render_text_layer` with updated values (the prior DesignSpec in the brief prefix gives the planner the baseline); within-turn revise loops use `edit_layer` for targeted diffs.

**Rationale**: Confirmed by dogfood run `20260419-133320-46eb3e8a` ($3.19, 120s, 2 critique iters):
- Planner called `edit_layer` 2× in the iter-1 revise phase (L1_version: bbox + font_size_px; L4_rule: partial bbox + fill) with correct semantics and status=ok.
- Planner correctly distinguished NEW layers (L6/L7/L8 via render_text_layer) from EXISTING layers needing tweaks (L1/L4 via edit_layer).
- Partial bbox merge survived real use: L4's diff was `{y: 1790}`, x/w/h preserved from prior state.
- No `edit_layer → not_found` failures → Opus 4.7 respects the planner.md guidance that edit_layer is for layers already in the current turn's rendered_layers.

Per-turn cost of fully supporting cross-turn edits: +1h implementation + permanent architectural complexity. Benefit: edit_layer would be callable in chat revision turns, saving maybe $0.1 per turn vs render_text_layer. Not worth it — planner can just pass the updated values to render_text_layer directly on a new-ctx turn, same end result.

**Consequence**: V1-MVP-PLAN.md #5 remains accurate as written; the `edit_layer` tool's "Conversational edits" framing in planner.md is correct — "conversational" here means "within the planner's tool-use conversation with itself during the critique-revise loop", not "between chat turns".

**Revisit when**: (a) users start chaining many short-command revisions in chat (5+ per session) where the extra render_text_layer cost dominates — at that point, Route X becomes defensible; (b) the trajectory-as-unit-of-work model gets replaced by something finer-grained.

---

## 2026-04-18 — Chat shell: `ChatSession` is a thin outer wrapper; each turn gets a FRESH `ToolContext`

**Decision**: In chat mode, each user turn that triggers generation creates a brand-new `PipelineRunner` + `ToolContext`. The outer `ChatSession` only stores `TrajectoryRef` entries (paths + summary metadata) and `ChatMessage` history, NOT carrying `ToolContext` state across turns.

**Alternatives**:
1. One long-lived `ToolContext` per session, tools append to `rendered_layers` across turns — appealing for "edit the previous title" workflows.
2. Session-level state mirror (copy prior trajectory's state into new ToolContext on each turn) — more complex, still error-prone.

**Rationale**: Per-turn isolation matches the Trajectory-as-unit-of-work principle. Each generation is self-contained: brief → DesignSpec → layers → composition → critique → finalize. If the user wants to revise the prior artifact, the planner re-proposes the spec (possibly reusing layer_ids to overwrite) — this is cheap at the current scale (~$1-4 per turn) and keeps the data model clean for SFT extraction. Inter-turn carryover lives at the CONVERSATION layer (ChatSession.trajectories + prior-trajectory context injection in the user brief), not at the ToolContext layer. `edit_layer` (v1.0 #5) will still respect this: it'll take a layer_id + diff and produce a new trajectory with that single layer replaced, not mutate an old one in-place.

**Revisit when**: we observe users consistently producing "revision chains" (5+ successive edits to same artifact) where the full per-turn regeneration cost dominates — at that point, a selective "patch only these layers" fast-path might pay off.

---

## 2026-04-19 — CORRECTION: Chat context injection must include the FULL prior DesignSpec, not just metadata

**Decision**: `chat.py._build_contextual_brief` now injects the full prior `DesignSpec` JSON (including `layer_graph` with per-layer text, font_family, font_size_px, bbox, palette, mood, composition_notes) whenever the session has a prior trajectory. Metadata-only summary (run_id + verdict + path) was insufficient.

**This overrides** the 2026-04-18 decision (immediately below) which argued for latest-only metadata summary on token-budget grounds. That decision was wrong.

**Root-cause bug** that triggered the correction: user ran a Neural Networks poster on turn 1 (9 layers), then said "make the title bigger" on turn 2. Planner received a brief prefixed with `run_id: 20260418-231249-ede40b1a` + `type: poster` + path, but had no actual access to the prior spec (no filesystem-read tool). When forced to revise "the prior artifact" with zero visibility into its content, the planner regressed to the strongest few-shot anchor in `prompts/planner.md` (国宝回家) and produced a poster with palette `['#1a0f0a','#fafafa','#a02018','#c9a45a']`, mood `['oriental epic',...]`, layers `国宝回家 / National Treasures / 归途` — verbatim from the few-shot example. Cost: $1.44 wasted on generating the wrong poster. Session: `session_20260418-231218_f285acbc`.

**Fix** (commit pending):
1. `chat.py`: load the prior trajectory's `design_spec` from disk, dump it as indented JSON inside a ```` ```json ```` block in the contextual brief, alongside a "COPY this, don't invent" instruction.
2. `prompts/planner.md`: add an explicit guard — when the brief prefix contains a `### Prior DesignSpec` block, that IS the starting point; few-shot anchors (国宝回家) are for FIRST-TURN briefs only.

**Rationale for the reversal**: token cost was the wrong optimization. A 9-layer spec serializes to ~3-4 KB (~1K tokens ≈ ~$0.02 extra per turn). The alternative is the planner fabricating a completely different artifact — costing a full $1-4 per wrong turn and, worse, poisoning the training-data trajectory with a spec that doesn't actually describe what the user wanted. Grounding > token-saving.

**Revisit when**: sessions accumulate long histories (>10 trajectories) where even latest-only injection becomes heavy. At that point, compact the injected spec: drop references field, trim layer_graph to `(id, kind, bbox, text, font_size_px)` per layer, drop src_path (runtime-only). For now, full spec is fine at <10 trajectories per session.

---

## 2026-04-18 — Chat context injection: summarize ONLY the latest trajectory, not the full history (SUPERSEDED 2026-04-19)

> **⚠️ This decision was wrong. Superseded by the 2026-04-19 entry above.** The token-budget argument held, but the quality impact (planner regressed to the 国宝回家 few-shot when asked to revise a Neural Networks poster) outweighed the savings by 50-100×. Kept here as record; the fix is full-spec injection.

**Decision (superseded)**: Each new user brief in a chat session is prefixed with a summary of *only the latest* `TrajectoryRef` (run_id, artifact_type, n_layers, verdict, path). Not the full `ChatSession.trajectories` list. Not the full design_spec. Just a pointer to the most recent and a decision prompt.

**Alternatives**:
1. Inject full prior-artifact design_spec (palette, mood, composition_notes, full layer_graph) — lets planner reuse those values exactly.
2. Inject all prior trajectories in the session — lets planner reference artifact #1 when making artifact #5.
3. Inject nothing — treat every turn as independent, rely on the user's brief to provide full context.

**Rationale**: Option 1 would roughly double each turn's input tokens (spec + layer_graph is ~2K tokens). Option 2 scales linearly with session length — by turn 10, we'd be wasting tokens on stale context. Option 3 makes revisions ("make the title bigger") meaningless because planner has no clue what "the title" refers to. The chosen middle path (latest-only summary) is cheap (~200 tokens), enough to distinguish revision-vs-new-artifact, and matches the natural chat UX where users primarily refer to "the thing I just made." If a user wants to reference an earlier artifact, they can say so explicitly ("like the first poster but with cyan text") and the planner can use `:history` / the session file as needed.

**Revisit when**: users consistently want to cross-reference artifacts earlier than the latest (we'd add an optional `--artifact-context=all` flag), OR when `edit_layer` lands and the planner needs more structured layer metadata in its context (at that point, inject `layer_manifest` alongside the high-level summary).

---

## 2026-04-18 — CLI subcommand split: `chat` (default) + `run` (one-shot), backward-compatible

**Decision**: `longcat-design` with no subcommand launches the chat REPL. The old one-shot behavior (single brief → single trajectory) moves to `longcat-design run "<brief>"`. Both work via the same `cli.py` with argparse subparsers.

**Alternatives**:
1. Keep one-shot as default, add `longcat-design chat` explicitly — preserves v0 CLI behavior but makes the headline v1.0 feature a secondary option.
2. Remove one-shot entirely, chat is the only interface — cleaner, but breaks automation/scripting/CI use cases and loses an easy path for batch dataset generation.
3. Two separate binaries (`longcat-design-chat` + `longcat-design-run`) — extra pyproject scripts, extra PATH clutter, no real benefit.

**Rationale**: Chat is the v1.0 product-UX headline — should be the frictionless default. One-shot is still valuable for (a) dataset generation scripts that call it in a loop, (b) CI smoke tests, (c) users who just want one artifact and are scared of a REPL. Keeping both under argparse subparsers is zero extra infra. There are no external users yet, so "breaking" the no-arg one-shot form costs nothing in adoption — and the explicit `run` subcommand is arguably clearer even in isolation.

**Revisit when**: usage telemetry (if we ever add it) shows `run` used <5% of the time AND no one complains — at that point consider removing `run` for simplicity. OR if the REPL becomes too slow to start (cold start >2s) for scripting use — in which case maybe reverse the default.

---

## 2026-04-18 — PIVOT: rebrand as LongcatDesign, reposition as open-source Claude Design alternative

**Decision**: Stop framing the project primarily as "Longcat-Next training-data pipeline / research prototype." Rename to **LongcatDesign** and ship as an **open-source product** — a terminal-first conversational design agent that is a true alternative to Claude Design (Anthropic's closed SaaS released 2026-04-18). v1.0 MVP covers three artifact types (poster / slide deck / landing page) with HTML as first-class output.

**Alternatives considered**:
1. **Stay the course** — continue v0.1 → v0.2 → v0.3 under trajectory-first framing. Rejected: after Claude Design launched, the product-form for AI design agents is validated in a way training-data emission isn't; missing the window leaves us building for a use case (Longcat-Next model training) that may not monetize user pain the way shipping a real product does.
2. **Build full SaaS clone of Claude Design** (browser UI, multi-user, Canva integration) — rejected as [VISION.md](VISION.md) / [COMPETITORS.md](COMPETITORS.md) note: we can't win on UX polish budget against Anthropic, but can win on open-source + terminal-first + open formats.
3. **Abandon trajectory emission entirely** — rejected: kept as internal session state. Enables resume / undo / optional future training-data harvesting with no product dilution.

**Rationale**: Claude Design validates the UX thesis (conversational iteration > one-shot) and reveals the market gap we can actually own: open-source, terminal-first, open output formats, self-hostable, model-agnostic. Also: rebranding to `LongcatDesign` aligns with our team's Longcat ecosystem (Longcat-Next et al.) — signals team credibility without forcing the training-data framing into the product pitch. Trajectory machinery is preserved; if the Longcat-Next team wants to harvest session data later, it's a feature-flag flip, not a refactor.

**Consequences**:
- [VISION.md](VISION.md) rewritten around product positioning.
- [ROADMAP.md](ROADMAP.md) reordered: v1.0 = MVP launch; old v0.x items (multi-image insets, real PSD type layer, Brand Kit) become v1.1-v1.5.
- [DATA-CONTRACT.md](DATA-CONTRACT.md) carries a banner: schema preserved, no longer the product — see its header for details.
- [COMPETITORS.md](COMPETITORS.md) needs partial revision (flagged for rewrite); differentiation-vs-Claude-Design section is newly relevant.
- [V1-MVP-PLAN.md](V1-MVP-PLAN.md) (new doc) is the single-page shipping plan for v1.0.
- Package will rename `design_agent` → `longcat_design` (pending user confirmation; tracked in todo).

**Revisit when**: (a) v1.0 ships and we have real adoption numbers — if GitHub stars/active users stay in low dozens after 3 months, reconsider positioning; (b) Anthropic open-sources Claude Design or exposes a "layered output" API — would shift the competitive landscape; (c) Longcat-Next team explicitly needs the training-data pipeline to launch — can reactivate with config flag without abandoning product.

---

## 2026-04-18 — OpenRouter as primary LLM backend (with stock Anthropic fallback)

**Decision**: Support both `OPENROUTER_API_KEY` and `ANTHROPIC_API_KEY` in env; OpenRouter takes precedence when both are set. Anthropic Python SDK is used either way (OpenRouter exposes Anthropic-compatible `/messages`).

**Alternatives**:
- Anthropic only — simpler, but locks the user to one billing relationship + the user's account had credit issues.
- OpenAI SDK against OpenRouter's OpenAI-compat endpoint — would require rewriting `planner.py` (different tool_use protocol).
- LiteLLM or similar abstraction layer — extra dependency, extra abstraction, no concrete benefit since OpenRouter Anthropic-format works.

**Rationale**: Path of least code change (just `base_url` swap on the existing Anthropic SDK), preserves the entire tool-use protocol verbatim, gives the user a single key for many providers + per-call cost reporting, removes the credit-balance failure mode.

**Revisit when**: OpenRouter goes down or changes their Anthropic-compat endpoint behavior; or Longcat-Next is hostable and we want to point planner/critic at our own model.

---

## 2026-04-18 — `load_dotenv(override=True)`

**Decision**: `.env` always wins over shell-exported env vars.

**Rationale**: Shells (especially zsh with random `.zshrc` lines or keychain integration) can export empty values for `ANTHROPIC_API_KEY` / `ANTHROPIC_BASE_URL` that silently mask `.env`. We hit this twice in one session. Project-local `.env` is the explicit source of truth for this project's config; shell env is for the user's defaults. `.env` wins.

**Revisit when**: Never. This is the right default for project-local config.

---

## 2026-04-18 — Gemini SDK output: always re-encode through PIL

**Decision**: After `part.as_image().save()`, force a Pillow `Image.open(BytesIO(inline_data.data)).save(path, format='PNG')` re-encode regardless of what the SDK returned.

**Rationale**: `genai.Image.save("foo.png")` writes the raw `inline_data.data` bytes (which are JPEG from Gemini's side) and just names the file `.png`. Downstream tooling (psd-tools embedding, browsers parsing the SVG `<image>` tag) checks magic bytes, gets confused. Re-encoding through PIL guarantees the file format matches the file extension.

**Revisit when**: Gemini SDK starts returning true PNG bytes consistently (would need to verify across model variants).

---

## 2026-04-17 — v0 = Plan B (PSD pixel layers + SVG vector text), NOT real PSD type layer

**Decision**: In v0, every text element is rendered to a transparent RGBA PNG (Pillow) and embedded into the PSD as a *named pixel layer*. The SVG carries the *real* vector text via `<text>` elements with embedded subsetted-WOFF2 fonts. Photoshop's true `TypeLayer` is deferred to v0.2+.

**Alternatives**:
- Plan A — real PSD type layer (`psd-tools` writes a `TypeTool` block)
- Plan C — only output SVG, skip PSD entirely

**Rationale**: `psd-tools` real type layer support is brittle (font matching across systems is non-deterministic, OpenType feature support is partial, and the API is poorly documented). Getting it right would have eaten 1-2 weeks before the first end-to-end run. SVG with embedded fonts gives true editability *now* with zero font-matching risk. PSD as named pixel layers gives Photoshop users the right *layer structure* (move/resize/order/blend works) at the cost of needing to retype text — acceptable v0 compromise.

**Revisit when**: v0.2 work begins; or when a user blocks on "I need to edit Chinese text directly in Photoshop without re-typing."

---

## 2026-04-17 — Anthropic SDK + handwritten tool loop, NOT LangGraph / CrewAI / etc.

**Decision**: The planner's tool-use loop is a hand-written ~150-line `PlannerLoop` class that calls `client.messages.create(..., tools=[...])` directly and threads `tool_result` messages back manually.

**Alternatives**: LangGraph, CrewAI, AutoGen, Anthropic's `beta.tools` helpers, etc.

**Rationale**: The trajectory IS the product. We need byte-exact replayable traces with paired `tool_use_id` ↔ `tool_result` entries, per-turn token/cost tracking, and the ability to add custom step types (`design_spec`, `critique`) at semantic boundaries. High-level frameworks abstract those mechanics away and make trajectory emission painful. Hand-written loop = ~150 lines, full control, easy to audit. The trade-off is that we re-implement what frameworks give for free (retry, streaming, etc.), but those aren't on the v0 critical path.

**Revisit when**: We need streaming responses for UX, OR when we want to run multiple planners in parallel and a framework's worker-pool abstractions would help.

---

## 2026-04-17 — `propose_design_spec` is a tool, NOT free-form text

**Decision**: The planner submits its initial DesignSpec via a `propose_design_spec` tool call (with the spec as JSON in `tool_args.design_spec`), not as a JSON blob inside a free-form `text` block.

**Alternatives**: Have the planner emit the spec as a markdown JSON code block in its first response, parse it out with regex.

**Rationale**: Two benefits. (a) Pydantic validation happens cleanly at the tool boundary; bad JSON returns a structured `ToolObservation{status:"error"}` the planner can react to. (b) The trace records a clean `tool_call` ↔ `tool_result` ↔ `design_spec` (with `spec_snapshot`) sequence — downstream extractors don't have to parse free-form text to find the spec.

**Revisit when**: We add streaming + want the planner to start spec-emission incrementally before all fields are known.

---

## 2026-04-17 — Trajectory ownership: runner builds it, planner doesn't see it

**Decision**: `agent_trace` is owned by the `PlannerLoop` instance and accumulated turn-by-turn. `critique_loop` lives in `ctx.state["critique_results"]` and is appended by the `critique` tool. The runner stitches them together with everything else only at finalize-time. The planner LLM never sees trajectory state.

**Alternatives**: Pass the trajectory-so-far as context to the planner each turn so it can self-reflect on its history.

**Rationale**: Keeps the planner's working context lean (essential because tool definitions + spec + last few results already eat 30-60K tokens per turn). The planner doesn't need to read its own trace to make good decisions — the recent assistant + tool messages already give it the relevant short-term context. Adding the trajectory as context would balloon cost AND risk recursive reasoning issues.

**Revisit when**: We have a clear failure mode where the planner forgets a constraint from many turns ago that's not in its recent context.

---

## 2026-04-17 — Background generation: hard-enforce text-free via prompt suffix

**Decision**: `tools/generate_background.py` always appends the literal sentence `"No text, no characters, no lettering, no symbols, no logos, no watermarks."` to the planner's prompt before sending to Gemini. The Gemini SDK has no native `negative_prompt` parameter.

**Alternatives**: Trust the planner to include the suffix (planner.md already instructs it); use a negative-prompt parameter (doesn't exist for `gemini-3-pro-image-preview`); post-OCR check + retry.

**Rationale**: Trust-but-verify isn't enough — the entire pipeline assumes background carries zero text. A planner regression that drops the suffix would silently corrupt training data (model would learn to bake text into background). Cheap belt-and-suspenders enforcement at the tool boundary. Post-OCR check is v0.2+ tightening.

**Revisit when**: Gemini exposes a real `negative_prompt`; OR we want to allow some backgrounds to contain text (e.g., a poster of a sign, where the sign text is intentional).

---

## 2026-04-17 — SVG fonts: subset to used glyphs only

**Decision**: Use `fonttools.subset` to extract only the glyphs actually used in this poster's text, encode as WOFF2, embed via `data:font/woff2;base64,...` in `<defs><style>@font-face{...}</style></defs>`.

**Alternatives**: Embed the full Noto fonts (~16-24 MB each), reference by URL, rely on system-installed fonts.

**Rationale**: Full embed would produce 40+ MB SVG files per poster. URL references break self-containment (SVG won't render correctly when emailed/shared). System fonts break consistency (different machines, different rendering). Subsetted WOFF2 embedded inline gives self-contained SVGs at ~10-30 KB per font (vs 16-24 MB unsubsetted). The tradeoff: editing text to add new characters not in the original may show as fallback glyphs — see [GOTCHAS.md](GOTCHAS.md) "SVG character set drift" and [ROADMAP.md](ROADMAP.md) v0.1 rerender command.

**Revisit when**: Users frequently edit text content post-export and hit the character-drift issue more often than they hit the file-size issue.

---

## 2026-04-17 — Critic: max 2 iterations, hard cap

**Decision**: `Settings.max_critique_iters = 2`. Critic prompt forbids `verdict: "revise"` past iteration 2 (forces `fail`). Runner doesn't restart on `fail` — partial output still counts as a valid trajectory.

**Alternatives**: No cap; cap by total tokens; ask user to confirm continuation.

**Rationale**: Critic loops can become recursive when the model gets attached to a particular fix that doesn't help. 2 iterations is enough for one round of meaningful revision (initial → fix → final). Beyond that is diminishing returns. Partial trajectories are still useful training data — they teach the critic+planner what "stuck" looks like.

**Revisit when**: We have evidence that 3-4 iterations meaningfully improve final scores in a measurable way.

---

## 2026-04-17 — Pydantic 2 for schema, NOT free-form dicts

**Decision**: Every data shape that crosses module boundaries (especially `Trajectory`, `DesignSpec`, `LayerNode`, `ToolObservation`) is a Pydantic 2 `BaseModel`. Free-form dicts only appear inside `metadata` and `canvas` dicts where the keys can vary by run.

**Alternatives**: TypedDict, dataclasses, raw dicts.

**Rationale**: Pydantic gives runtime validation (catches bad LLM JSON early), JSON serialization with `.model_dump(mode="json")` (handles datetime, enums, nested models for free), and self-documenting schemas (you can read `schema.py` like a spec). Downstream consumers (training data loaders) get free type safety. The cost is a dependency we already have.

**Revisit when**: Pydantic becomes a perf bottleneck (won't happen at our scale).

---

## 2026-04-17 — Bundled fonts: Noto SC family (sans + serif)

**Decision**: Ship `NotoSansSC-Bold.otf` and `NotoSerifSC-Bold.otf` (Open Font License) in `assets/fonts/`. The renderer only knows about these two; unknown `font_family` strings fall back to `NotoSansSC-Bold` with a `partial` warning.

**Alternatives**: Use system fonts; ship more font weights; integrate a Font Generator tool (Lovart-style).

**Rationale**: Two fonts cover 95% of CJK + Latin needs (sans for body/Latin, serif for titles/calligraphic feel). System fonts vary by machine, breaking reproducibility. More weights add 16-24 MB each — a regular-weight pair would double bundled size for small visual benefit. Custom font generation (Lovart's "Bronze Calligraphy" feature) is v0.5+ work.

**Revisit when**: Users frequently complain that fallback to NotoSansSC ruins the design intent; OR we add the Font Generator tool.

---

## 2026-04-17 — Layer coordinates: top-left origin, pixel units

**Decision**: All `bbox` fields use top-left origin and pixel units. This matches PSD convention, Pillow convention, and SVG default.

**Alternatives**: Bottom-left origin (mathematician convention); normalized [0..1] coordinates; em / pt units.

**Rationale**: Three downstream rendering targets all use top-left pixel coords. Aligning the spec to them eliminates per-tool conversion bugs. Normalized coordinates would require knowing canvas size at every layer — possible, but adds friction. Top-left pixel is the unambiguous lingua franca.

**Revisit when**: We add print-medium output where physical units (mm/inches) matter more.

---

## Schema version history

When the trajectory schema changes, add a row here. **Never break old trajectories** — branch on `metadata.version` in downstream loaders.

| Date | Version | Change |
|---|---|---|
| 2026-04-17 | `v0` | Initial trajectory schema with 5 SFT-ready lanes. |
