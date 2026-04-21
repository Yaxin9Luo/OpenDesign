# LongcatDesign — Open-Source Conversational Design Agent

> A terminal-first design agent that turns one-line briefs (or whole research papers) into **layered, editable** posters, landing pages, and slide decks — with real figures, real tables, and real editability in PSD / SVG / HTML / PPTX.

## Tagline

**Open source. Terminal-first. Layered, editable output.**
The open alternative to Claude Design — model-agnostic, format-agnostic, yours to fork.

## What LongcatDesign does

Three artifact types, generated conversationally from a CLI chat shell:

- **Poster** — absolutely-positioned layers over a text-free background. Output: PSD (named pixel layers + `text` group), SVG (real `<text>` vector elements + subsetted-WOFF2 @font-face), HTML (contenteditable toolbar + inline fonts/images), PNG preview. 3:4 / 4:3 academic, marketing, cultural.
- **Landing page** — self-contained HTML one-pager with 6 bundled design systems (minimalist / editorial / claymorphism / liquid-glass / glassmorphism / neubrutalism). v1.3 adds CTA buttons, auto-generated top nav, reveal-on-scroll, semantic `<header>/<main>/<footer>`.
- **Slide deck** — editable PPTX with native PowerPoint TextFrames + native `add_table` shapes. Live-editable in PowerPoint / Keynote / Google Slides with no conversion.

## Three contributions

### 1. Agent-first, not template-first

Built on the Anthropic SDK with a **handwritten tool loop** — no LangGraph, no CrewAI, no hidden orchestration framework. The `PlannerLoop` runs Claude Opus 4.7 through 11 registered tools (`switch_artifact_type`, `ingest_document`, `propose_design_spec`, `generate_background`, `generate_image`, `render_text_layer`, `edit_layer`, `fetch_brand_asset`, `composite`, `critique`, `finalize`) and iterates against a `Critic` that grades every output on brief fidelity, typography, composition, and (for paper posters) visual density. Every run serializes a `Trajectory` JSON blob: brief + spec + agent trace + critique loop + composition artifacts — replayable and inspectable.

### 2. Three first-class output families, one DesignSpec

Most design tools lock you into their export format. LongcatDesign renders **one `DesignSpec` into three format families** via a single `composite` tool that dispatches on `spec.artifact_type`:

- POSTER: PSD + SVG + HTML + PNG preview (layered, vector text, contenteditable)
- LANDING: index.html (6 design systems + CTA + nav + reveal) + PNG preview
- DECK: deck.pptx (native TextFrames + native tables) + per-slide PNG thumbs + grid mosaic preview

Every poster and landing is **round-trip editable**: open the HTML in a browser, edit inline via the built-in toolbar, download, and the standalone `apply-edits` CLI rebuilds the entire run (including a fresh PSD / SVG / preview for posters) from the edited HTML. Decks are editable directly in PowerPoint — TextFrames and tables are live, not rasterized.

### 3. paper2any — real figures, real tables, real editability

Drop a paper / docx / pptx / scanned-PDF / markdown into any run and the planner calls `ingest_document` first. The ingestion pipeline is format-aware:

- **PDF**: pymupdf native extraction — `extract_image(xref)` for embedded rasters at native resolution, `get_drawings()` + proximity clustering at 300 dpi for vector diagrams, `find_tables()` for table localization. Qwen-VL-Max on OpenRouter handles the non-localization jobs (structure manifest extraction, caption matching, fake-figure filtering, per-table cell parsing with `col_highlight_rule`).
- **Scanned PDF**: auto-detected via `detect_scanned_pdf`; falls back to per-page Qwen-VL OCR at 200 dpi, 6 workers parallel.
- **DOCX** (Word): `python-docx` reads Heading 1/2/Title paragraphs → section tree, `doc.part.rels` yields embedded images. No VLM call needed.
- **PPTX** (PowerPoint): `python-pptx` reader — each slide becomes one section, picture shapes become figure layers.
- **Markdown / TXT / image**: passthrough with embedded-reference resolution.

Ingested figures land as `ingest_fig_NN` layers with `kind="image"`; tables land as `ingest_table_NN` layers with `kind="table"` carrying structured `rows + headers + col_highlight_rule` (`"max"` / `"min"` / `""` per column). The renderer produces **native PPTX `add_table` shapes for decks, real `<table>` with winner-bolding for landings, and PIL-drawn PNGs with deep-green winner text for posters** — no cropped screenshots.

## Dogfood: the landing page you're reading

This landing page was built by LongcatDesign itself. The three figures below are the agent's own architecture diagrams, pulled in via `ingest_document`:

- **Figure 1** (`ingest_fig_01` / agent_architecture.png) — the agent architecture: User → ChatREPL → PipelineRunner → PlannerLoop ↔ Critic + the 11 registered tools + output dispatch to POSTER / LANDING / DECK.
- **Figure 2** (`ingest_fig_02` / rendering_pipeline.png) — the 3-artifact rendering pipeline: one DesignSpec, per-type renderers (PSD + SVG + HTML | HTML | PPTX), plus the four cross-cutting composite features (aspect-preserve, text-overlap detector, figure↔text xref, paper→editable tables).
- **Figure 3** (`ingest_fig_03` / paper2any_flow.png) — the paper2any ingestion flow: 5 input types × 4 extraction backends × Qwen-VL-Max → `rendered_layers` registry → planner tool_result summary with a ranked top-20 figure catalog.

## Status — shipped as of 2026-04-21

| Version | Milestone |
|---|---|
| v1.0 | Code complete — 3 artifact types + in-browser edit toolbar + `apply-edits` round-trip + CLI chat shell + 10 code items shipped |
| v1.1 | `ingest_document` tool + `--from-file` / `:attach` CLI entries — paper2any foundation |
| v1.2 | Full paper2any production: pymupdf native figure/table extraction · Qwen-VL-Max ingest · `kind="table"` native renderer · poster visual-density rules · composite aspect-preserve |
| v1.2.4 | Deterministic text-overlap + figure↔text cross-reference detectors in composite |
| v1.2.5 | `.docx` / `.pptx` ingest branches + scanned-PDF OCR fallback (18/18 smoke) |
| v1.3.0 | Interactive landings — CTA / nav / reveal / semantic `<header>/<main>/<footer>` |
| v1.3.1 | Paper-landing imagery policy — ingested figures first, NBP reserved for hero |

Verified end-to-end on a real 43-page / 17 MB research paper (LongCat-Next):
- **Poster**: 5 ingested figures + 1 benchmark table, critique 0.86
- **Landing** (editorial, academic): 0 NBP calls, 9 paper figures + 1 benchmark table, 8 sections, critique pass 0.88 in one iteration
- **Deck**: 12 slides, 15×12 editable PPTX table with 8 bold-winner cells, critique pass 0.88

## How to try it

```bash
pip install longcat-design
longcat-design                                         # chat (default)
longcat-design run "your brief"                        # one-shot
longcat-design run --from-file paper.pdf "poster"      # paper → artifact
longcat-design apply-edits ~/Downloads/edited.html     # round-trip
```

BYO Anthropic + Gemini (+ OpenRouter) API keys. All artifacts land in `out/runs/<run_id>/`; every run is a full `Trajectory` JSON on disk.

## Why open source

Design tools live in walled gardens. The generation prompts, the tool definitions, the critic rubric — all hidden. LongcatDesign is MIT-licensed and the prompts + schema + critic live in-repo. Fork it, swap models, run locally, capture trajectories for training — the project is the agent, not the SaaS front-end.

## Links

- GitHub repo: `https://github.com/Yaxin9Luo/OpenDesign`
- Primary CTA: `pip install longcat-design`
- Secondary CTA: `See the architecture` (anchors to the architecture section)
- Docs: `docs/ARCHITECTURE.md`, `docs/ROADMAP.md`, `docs/WORKFLOWS.md`, `docs/DECISIONS.md`
