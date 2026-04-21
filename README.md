# LongcatDesign

> **An open-source, terminal-first conversational design agent. Describe what you want; LongcatDesign builds and iterates it with you, exporting real HTML, PPTX, or editable PSD/SVG.**

The open-source alternative to [Claude Design](https://www.anthropic.com/news/claude-design-anthropic-labs) and similar closed SaaS design tools. From the [Longcat](https://github.com/) ecosystem.

> **Current status** (2026-04-21): **v1.2 paper2any shipped + v1.2.4 / v1.2.5 polish** — full **3-artifact coverage** (poster + landing + deck) × full **paper / deck / doc → artifact** ingestion pipeline (PDF + DOCX + PPTX + scanned-PDF OCR), **11 tools wired**, smoke **18/18 green**. Landing + deck ship with **NBP (Gemini 3 Pro Image Preview) imagery**; paper-sourced artifacts ship with **native-resolution PDF figures + live-editable tables**. Composite runs **deterministic text-overlap + figure-cross-reference detectors** so geometry + citation errors surface one turn earlier than the critic round.
>
> **v1.2 highlights (shipped 2026-04-21, commits [ce50f2a](https://github.com/Yaxin9Luo/OpenDesign/commit/ce50f2a) · [da664a5](https://github.com/Yaxin9Luo/OpenDesign/commit/da664a5) · [a08bbb9](https://github.com/Yaxin9Luo/OpenDesign/commit/a08bbb9) · [349c899](https://github.com/Yaxin9Luo/OpenDesign/commit/349c899))**:
> - **pymupdf-native figure extraction** replaces the Claude-Sonnet vision locator — native-resolution crops (author-uploaded PNGs at 1890×1211 instead of 1454×820 half-page screenshots).
> - **Qwen-VL-Max via OpenRouter** is the default ingest model (~5× cheaper than Sonnet 4.6 for the "read this paper + match captions" workload; dual-SDK dispatcher routes Qwen via OpenAI-compat endpoint while planner / critic stay on Anthropic SDK).
> - **`kind="table"` LayerKind**: ingested data tables render as **native PowerPoint** `add_table` shapes for decks, real **`<table>`** for landing, **PIL-drawn PNG** with bold-winner column highlights for posters. No more cropped-screenshot tables.
> - **Poster visual-density rules**: planner now places ≥ 4 figures + ≥ 1 table for a paper poster (up from 1 figure previously); critic rubric weight reshuffled to penalize text-wall layouts.
> - **Aspect-preserve composite**: letterbox contain-fit for images, re-render tables at bbox dims with font autoscale — underspec'd bboxes degrade gracefully instead of squishing 14-row tables into 13 px / row.
>
> Verified end-to-end on the 43-page Longcat-Next paper: poster critique **0.62 → 0.86**, landing **pass 0.92**, deck **pass 0.88** with 8 bold-winner cells on a 15×12 editable PPTX table.

---

## What LongcatDesign does

Three artifact types, generated conversationally from a CLI chat shell:

| Artifact | Primary output | Secondary outputs | NBP imagery |
|---|---|---|---|
| **Poster** | HTML (contenteditable + edit toolbar) | PSD (named pixel layers) · SVG (real `<text>` vector) · PNG | Full-canvas background |
| **Slide deck** | PPTX (native PowerPoint TextFrames) | per-slide PNGs · grid preview | 1 image per slide, style-consistent across the deck |
| **Landing page** | Self-contained HTML with 6 bundled design systems (minimalist / editorial / claymorphism / liquid-glass / glassmorphism / neubrutalism) | PNG screenshot | Inline section imagery (hero + feature icons) |

All text is a separate, named, editable element — including Chinese. The poster + landing outputs round-trip through a browser edit toolbar: click any text, edit inline, save, and `apply-edits` re-materializes PSD / SVG / HTML from the edited file. Deck edits happen directly in PowerPoint / Keynote because every `TextFrame` is live.

**Commercial-grade by default**: landing pages and decks call [Gemini 3 Pro Image Preview (Nano Banana Pro)](https://ai.google.dev/) for inline imagery with a consistent per-artifact style prefix — so an 8-image deck feels like one cohesive pitch, not 8 stock-photo collages.

---

## Why LongcatDesign

### vs [Claude Design](https://www.anthropic.com/news/claude-design-anthropic-labs)

- **Open-source (MIT)**. Claude Design is closed; we're on GitHub.
- **Terminal-first**. Scriptable, pipeable, automatable. Claude Design is browser-only.
- **Open output formats**. HTML + PPTX + PSD + SVG — all editable in standard tools. Claude Design exports to Canva (proprietary) as its "fully editable" story.
- **Model-agnostic**. OpenRouter, stock Anthropic, future local models. Not tied to Claude Pro/Max/Team subscription.
- **No login, no subscription**. Clone the repo, bring your API key, run locally.

### vs Canva / Figma / Adobe Firefly

- **Your data stays local**. No uploads to a vendor's canvas.
- **Conversational iteration** via plain English. No dropdown menus.
- **Editable source files** you can push into your own design pipeline.

### vs Paper2Any / Lovart

- **HTML as first-class output**, not just a PPTX/PDF afterthought.
- **Real vector text for Chinese titles** — not rasterized into background images.
- **One repo, three artifact types**, done deep. No SaaS sprawl.
- **Round-trip editable**: poster / landing HTML opens in a browser with a toolbar for live edits, then `apply-edits` round-trips changes back into PSD / SVG / HTML. Deck `.pptx` has native editable TextFrames.

---

## Quickstart

### Install (uv-managed)

LongcatDesign uses [uv](https://docs.astral.sh/uv/) for environment + dependency management. Install uv once (`curl -LsSf https://astral.sh/uv/install.sh | sh`), then:

```bash
git clone https://github.com/Yaxin9Luo/longcat-design.git
cd longcat-design
uv sync                    # creates .venv, installs deps from uv.lock, editable-installs the package
cp .env.example .env       # fill in GEMINI_API_KEY + (OPENROUTER_API_KEY OR ANTHROPIC_API_KEY)
```

> **macOS note:** if `uv run` fails with `ModuleNotFoundError: longcat_design`, Apple's Gatekeeper may have hidden the editable `.pth` file. Fix with `xattr -c .venv/lib/python*/site-packages/*.pth && chflags nohidden .venv/lib/python*/site-packages/*.pth`. See [docs/GOTCHAS.md](docs/GOTCHAS.md).

### Smoke test (no API, ~5 sec, 16 checks)

```bash
uv run python -m longcat_design.smoke
```

### Chat shell (default — conversational multi-turn)

```bash
uv run python -m longcat_design.cli
> 设计一张 3:4 竖版海报：「国宝回家」公益项目
  ✓ poster generated  (5 layers · critic pass 0.86 · $1.41 · 100s)

> 现在配一个 landing page，claymorphism 风格，奶茶品牌「茉语」
  ✓ landing generated (4 sections, 5 NBP images · critic pass 0.94 · $2.20 · 207s)

> 再出一份 10-slide 投资人 pitch deck，每张 slide 配图
  ✓ deck generated    (10 slides, 10 NBP images · critic pass 0.92 · $3.43 · 384s)

> :history                  # conversation log
> :tokens                   # cumulative cost/wall per artifact
> :export ~/Desktop/milk-tea
> :exit
```

Full slash command reference in [docs/WORKFLOWS.md](docs/WORKFLOWS.md#slash-commands). Resume a prior session: `uv run python -m longcat_design.cli chat --resume <session_id>`.

### One-shot (for scripting / CI)

```bash
uv run python -m longcat_design.cli run "10 张投资者 pitch deck：奶茶品牌 MilkCloud。封面 · 3 张 problem · 3 张 solution · 2 张 traction · thank-you。1920×1080，每张 slide 都要配图。"
```

Outputs land in `out/runs/<run_id>/` (per-artifact — `poster.pptx` + `slides/` + `preview.png` for deck; `index.html` + `preview.png` for landing; `poster.psd/svg/html` + `layers/` for poster). Chat mode additionally wraps trajectories under `sessions/<session_id>.json`.

### Paper → poster / landing / deck (v1.2 paper2any, shipped)

Drop a paper / markdown / image into any run — planner calls `ingest_document` first, pulls the structured content through, and the artifact renderers consume real figures + editable tables directly from the PDF:

```bash
# One-shot CLI
uv run python -m longcat_design.cli run \
  --from-file ~/papers/longcat-next.pdf \
  "基于附件的 Longcat-Next 论文，设计一张 3:4 学术海报。包含：标题 + 作者 + abstract 核心观点 + method/results + 2-3 张原论文图表直接 passthrough。学术会议风格。"
# (optional — repeat --from-file for logo / brand kit / reference shots)

# Or in chat
uv run python -m longcat_design.cli
> :attach ~/papers/longcat-next.pdf
  ✓ queued: longcat-next.pdf (17 MB). Will be ingested on the next non-slash turn.
> 生成一张 claymorphism 风格的 landing，把 abstract 当 hero，method 和 results 当特性卡
  [generating — anthropic/claude-opus-4.7, may take 1-5 min, ingesting 1 file(s)]
```

Supported inputs:
- **PDF** (pymupdf native figure extraction via `page.get_images()` + `doc.extract_image(xref)` for embedded rasters; `get_drawings()` + proximity clustering @ 300 dpi for vector diagrams; `page.find_tables()` for data-table localization. VLM = Qwen-VL-Max via OpenRouter for structure extraction + caption matching + fake-figure filtering).
- **Scanned PDF** — auto-detected via `detect_scanned_pdf`; falls back to per-page Qwen-VL OCR at 200 dpi, 6 workers in parallel. Figure extraction is skipped (scanned pages are single rasters); structure extraction runs as normal on the OCR'd text.
- **DOCX** (Word) — `python-docx` reads Heading 1/2/Title paragraphs → section tree, `doc.part.rels` yields embedded images as `ingest_fig_NN`. No VLM call needed.
- **PPTX** (PowerPoint) — each slide becomes one section (title placeholder → heading, body placeholders → summary + key_points), picture shapes become `ingest_fig_NN`.
- **Markdown / TXT** (with embedded `![](image.png)` refs resolved).
- **PNG / JPG** (single-image passthrough).
- Multi-paper fusion: deferred to v1.3+.

What the planner gets on a paper run: title / authors / abstract / sections, up to 20 ranked figure candidates with `(page, size, extract-strategy, caption)` each, and any registered tables with structured `rows + headers + col_highlight_rule` (winner-per-column "max"/"min"/""). Tables render as **native editable primitives** in decks + landing and as a PIL-drawn PNG on poster (with deep-green winner-cell highlighting since the bundled NotoSansSC only ships bold). See [docs/WORKFLOWS.md § Paper → artifact](docs/WORKFLOWS.md#paper--poster--landing--deck).

### Round-trip edit (poster + landing)

Every poster + landing HTML comes with an embedded edit toolbar. Open the `.html` in a browser, click any text layer, edit inline (font / size / color / content / drag-to-move), click **Save** → download the edited HTML. Then:

```bash
uv run python -m longcat_design.cli apply-edits ~/Downloads/poster-edited.html
# → new run_dir with PSD + SVG + HTML regenerated from the edited version
#   (parent_run_id tracked in the trajectory for lineage)
```

Deck edits happen in PowerPoint / Keynote / Google Slides directly — the `.pptx` contains live `TextFrame`s, not rasterized text.

---

## Architecture in one breath

A **chat REPL** loop takes each user turn; a single **Claude Opus 4.7** planner drives a **handwritten Anthropic tool-use loop** (no LangGraph / CrewAI) over **11 tools**: `switch_artifact_type` → `ingest_document` (optional — paper2any, uses **Qwen-VL-Max via OpenRouter** for structure + caption matching + fake-figure filtering; pymupdf does all figure / table localization natively) → `propose_design_spec` → `generate_background` / `generate_image` (both via Gemini 3 Pro Image Preview / NBP) → `render_text_layer` → `edit_layer` → `fetch_brand_asset` → `composite` (dispatches on artifact type to PSD+SVG+HTML+aspect-preserve preview for poster · HTML + `<table>` + inline-imagery for landing · PPTX+native-tables+per-slide-PNGs for deck) → `critique` (vision for poster — includes visual-density rubric for paper posters; text-only for landing + deck) → `finalize`. Per-turn `Trajectory` JSON gets wrapped under a `ChatSession` persisted to `sessions/<id>.json`.

Full component map and data flow in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

---

## Documentation

| Doc | Read when |
|---|---|
| [docs/VISION.md](docs/VISION.md) | Product pitch + differentiation |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Before touching code |
| [docs/V1-MVP-PLAN.md](docs/V1-MVP-PLAN.md) | What's shipping in v1.0 |
| [docs/WORKFLOWS.md](docs/WORKFLOWS.md) | Day-to-day reference |
| [docs/DECISIONS.md](docs/DECISIONS.md) | Design log (why the pivot, why no LangGraph, etc.) |
| [docs/ROADMAP.md](docs/ROADMAP.md) | v1.x planned + future versions |
| [docs/GOTCHAS.md](docs/GOTCHAS.md) | Runtime quirks + fixes |
| [docs/COMPETITORS.md](docs/COMPETITORS.md) | How we compare to Claude Design, Paper2Any, Lovart |
| [docs/DATA-CONTRACT.md](docs/DATA-CONTRACT.md) | Session-state / trajectory schema (internal detail) |

---

## Status

**v1.0 MVP — 9.75 of 11 items shipped** (2026-04-20). Full 3-artifact coverage complete; 10 tools wired; smoke 13/13 green. Remaining for v1.0 tag: README screenshots + showcase gallery (#9), demo video (#10), smoke HTML/PPTX regression extension (#11).

**Dogfood runs** (real API, real $):

| Brief | Artifact | Layers / slides | Images | Critic | Cost | Wall |
|---|---|---|---|---|---|---|
| 国宝回家 公益项目 | poster (3:4) | 5 | 1 bg | pass 0.86 | $1.41 | 100s |
| CVPR academic poster | poster (3:4) | 18 | 1 bg | pass 0.86 | $2.49 | 196s |
| LongcatDesign 发布海报 | poster (3:4) | 5 | 1 bg | 0.78 → 0.82 (2 iter) | $3.74 | 297s |
| 茉语 奶茶品牌 landing | landing (claymorphism) | 4 sections | 5 (hero + icons) | pass 0.94 | $2.20 | 207s |
| MilkCloud 投资人 deck | deck (16:9) | 10 slides | 10 (cover bg + 8 content + closing bg) | pass 0.92 | $3.43 | 384s |

**Not yet published to PyPI.** Local development only until v1.0 tag.

---

## License

MIT (planned). Fonts in `assets/fonts/` are OFL (Noto Sans SC + Noto Serif SC), redistributable.

---

## Part of the Longcat ecosystem

LongcatDesign is built by the Longcat team alongside [Longcat-Next](https://github.com/) (next-gen layered image-text generation model). The trajectory-capture architecture preserved inside LongcatDesign can feed Longcat-Next's training data pipeline when needed — but that's a side-channel, not the product.
