# LongcatDesign

> **An open-source, terminal-first conversational design agent AND a training-data pipeline for layered design generation. Describe what you want; LongcatDesign builds and iterates it with you, exporting real HTML, PPTX, or editable PSD/SVG — and every run is automatically captured as a distillation-ready trajectory.**

The open-source alternative to [Claude Design](https://www.anthropic.com/news/claude-design-anthropic-labs) and similar closed SaaS design tools — with a second life as the training-data producer for the [Longcat](https://github.com/) layered / interleaved image-text ecosystem.

> **Current status** (2026-04-22): **v2 training-data pipeline + multi-provider backend** (PR [#1](https://github.com/Yaxin9Luo/OpenDesign/pull/1), commits [30cab95](https://github.com/Yaxin9Luo/OpenDesign/commit/30cab95) · [c264545](https://github.com/Yaxin9Luo/OpenDesign/commit/c264545) · [eeb6490](https://github.com/Yaxin9Luo/OpenDesign/commit/eeb6490)) layered on top of v1.3 interactive landings. **Two things shipped together because they share the same code paths and the same goal** — making every design run simultaneously a product output AND a training sample:
>
> - **v2 `DistillTrajectory` schema** — pure model decisions + lean tool results + episode reward. 70 % size drop (144 KB → 44 KB). Tool results no longer leak `summary` / `next_actions` hints, so train↔deploy distribution shift vanishes. Episode-level `final_reward` + `terminal_status` (`pass` / `revise` / `fail` / `max_turns` / `abort`) is the RL reward signal directly.
> - **Multi-provider LLM backend** (`longcat_design/llm_backend.py`) — `LLMBackend` Protocol + `AnthropicBackend` + `OpenAICompatBackend` handle 9 normalized protocol differences. Default planner + critic switched to **`moonshotai/kimi-k2.6`** (~$3.58/run with full plaintext reasoning vs Claude Opus 4.7 at $8-12/run with ~80 % redacted thinking). Claude is one env var away: `PLANNER_MODEL=anthropic/claude-opus-4.7`. Mix-and-match supported: planner on Claude + critic on DeepSeek-R1 works. Self-hosted vLLM / native Kimi / Moonshot supported via `OPENAI_COMPAT_BASE_URL`.
> - **Versioned intermediate artifacts** (`composites/iter_NN/` + layer `.vN.png` + `supersedes_sha256` chain) — every revise loop and every `edit_layer` call preserves prior state on disk. The data DPO training (rejected/chosen pairs) and layered-gen SFT (per-layer edit history) need most is now extractable by default. `final/` symlinks resolve transparently for product consumers.
> - **SFT jsonl exporter** (`scripts/export_sft_jsonl.py`) — one `uv run` turns a directory of `DistillTrajectory` JSONs into OpenAI-compat jsonl, one record per assistant turn, with CoT + tool_calls + tool catalog + turn usage. CLI flags gate by `--min-reward` / `--source` / `--actor` / `--provider` / `--terminal-status`.
>
> Product surface unchanged: still 11 tools, still 3-artifact coverage (poster + landing + deck) × full paper / deck / doc ingestion (PDF + DOCX + PPTX + scanned-PDF OCR), still in-browser edit toolbar + `apply-edits` round-trip, still v1.3 interactive landings with CTA / nav / reveal / semantic `<header>/<main>/<footer>`. Smoke **20/20 green**.
>
> **v2.3 paper2any Tier-1 polish** (2026-04-22, 5 follow-up commits on main): **deck speaker notes** ([1368422](https://github.com/Yaxin9Luo/OpenDesign/commit/1368422)) — `LayerNode.speaker_notes` → PowerPoint's notes pane, decks finally talk-ready. **Short caption** ([bb3aec8](https://github.com/Yaxin9Luo/OpenDesign/commit/bb3aec8)) — VLM returns `short_caption ≤ 15 chars` alongside full; planner picks per bbox. **`--template` flag** ([b3a4e21](https://github.com/Yaxin9Luo/OpenDesign/commit/b3a4e21)) — 5 bundled presets (neurips-portrait, cvpr-landscape, icml-portrait, a0-portrait, a0-landscape) injected via brief prologue. **KaTeX for landing** ([ce759d8](https://github.com/Yaxin9Luo/OpenDesign/commit/ce759d8)) — self-hosted vendor bundle (645 KB per math-containing landing; non-math landings unaffected), `$…$` / `$$…$$` / `\(…\)` / `\[…\]` auto-typeset. **Sub-figure extraction** ([4a77830](https://github.com/Yaxin9Luo/OpenDesign/commit/4a77830)) — VLM panel bbox detection + Pillow crop → `ingest_fig_NN_<label>` layers so the planner can place individual panels. Smoke went from 19/19 to **20/20** — new `check_sub_figure_registration` synthesizes a composite PNG + verifies per-panel crops.
>
> **v2.3 triple-dogfood on longcat-next-2026.pdf (Claude Opus 4.7)** validates the full polish set end-to-end: **Landing pass 0.88** in 6:50 ($3.96, KaTeX-typeset math in 9.8 MB HTML) · **Deck pass 0.88** in 10:47 ($9.45, 18/18 slides auto-populated with `speaker_notes`) · **Poster** rendered with `--template neurips-portrait` → 1536×2048 canvas applied but revise loop degraded (0.86 → 0.68, pre-existing poster-revise flaw parked for v2.4). Prior Kimi K2.6 runs all hit `max_turns` on 47K-char bbox-reasoning loops — the v2 `LLMBackend` abstraction paid off at dogfood time: **one env var** flipped planner + critic from Kimi to Claude with zero code change ([v2.3.6 fix 4d8f58d](https://github.com/Yaxin9Luo/OpenDesign/commit/4d8f58d) also capped the figure catalog to 20 to prevent a latent scalability regression on 80+-layer paper ingests).
>
> **v2 dogfood verification** (run `20260422-162157-d0f37cba`, Kimi K2.6 self-landing with 3 ingested diagrams): `terminal_status=pass`, `final_reward=1.0`, wall **4:00**, cost **$2.90**, **24 trace steps** (1 input + 8 reasoning + 7 tool_call + 7 tool_result + 1 finalize), **9 sections** including a new `training-data` section explaining DistillTrajectory on itself. Trajectory **72 KB** on disk vs v1's 144 KB on an equivalent run (-50%). `scripts/export_sft_jsonl.py --min-reward 0.85 --terminal-status pass` emits **20 OpenAI-compat SFT records from 2 trajectories** (80 K chars of captured plaintext reasoning, 1.7 MB jsonl). Multi-paper dogfood on longcat-next (editorial style, v1.3.1) still delivers **0 NBP + 9 paper figures + 1 benchmark table across 8 sections, pass 0.88** in one iteration.
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
git clone https://github.com/Yaxin9Luo/OpenDesign.git
cd OpenDesign
uv sync                    # creates .venv, installs deps from uv.lock, editable-installs the package
cp .env.example .env       # fill in GEMINI_API_KEY + OPENROUTER_API_KEY (or ANTHROPIC_API_KEY)
```

> **macOS note:** if `uv run` fails with `ModuleNotFoundError: longcat_design`, Apple's Gatekeeper may have hidden the editable `.pth` file. Fix with `xattr -c .venv/lib/python*/site-packages/*.pth && chflags nohidden .venv/lib/python*/site-packages/*.pth`. See [docs/GOTCHAS.md](docs/GOTCHAS.md).

### Smoke test (no API, ~5 sec, 19 checks)

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

### Switching models (planner + critic)

All LLM access goes through [`longcat_design/llm_backend.py`](longcat_design/llm_backend.py), a provider-agnostic abstraction with two implementations: `AnthropicBackend` (Claude via Anthropic API or OpenRouter Anthropic-compat endpoint) and `OpenAICompatBackend` (Kimi / DeepSeek / Doubao / Qwen / vLLM-served / anything OpenAI-compatible).

**Default**: both planner and critic use `moonshotai/kimi-k2.6` — cheap, agentic, reasoning not redacted. One `OPENROUTER_API_KEY` covers all providers below.

Switch via env vars (set in `.env` or export before running):

| Scenario | Env vars |
|---|---|
| Claude across the board | `PLANNER_MODEL=anthropic/claude-opus-4.7` + `CRITIC_MODEL=anthropic/claude-opus-4.7` |
| DeepSeek-R1 planner | `PLANNER_MODEL=deepseek/deepseek-r1` |
| Qwen3 Max Thinking | `PLANNER_MODEL=qwen/qwen3-max-thinking` |
| Heterogeneous (Claude plans, DeepSeek critiques) | `PLANNER_MODEL=anthropic/claude-opus-4.7` + `CRITIC_MODEL=deepseek/deepseek-r1` |
| Self-hosted vLLM | `OPENAI_COMPAT_BASE_URL=http://localhost:8000/v1` + `OPENAI_COMPAT_API_KEY=dummy` + `PLANNER_MODEL=Qwen/Qwen3-Max-Thinking` |
| Force provider when auto-detection is wrong | `PLANNER_PROVIDER=openai_compat` (choices: `auto` / `anthropic` / `openai_compat`) |
| Cheaper runs | `PLANNER_THINKING_BUDGET=2000` or `=0` (disable thinking) |

Provider auto-detection: model ids starting with `anthropic/` or `claude-` route to Anthropic backend; everything else goes OpenAI-compat. See [.env.example](.env.example) for the full list of knobs.

Inspect what's active:

```bash
uv run python -c "
from longcat_design.config import load_settings
s = load_settings()
print(f'planner: {s.planner_model} ({s.planner_provider})')
print(f'critic:  {s.critic_model} ({s.critic_provider})')
print(f'thinking: planner={s.planner_thinking_budget} critic={s.critic_thinking_budget}')
"
```

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
- **PDF** (pymupdf native figure extraction via `page.get_images()` + `doc.extract_image(xref)` for embedded rasters; `get_drawings()` + proximity clustering @ 300 dpi for vector diagrams; `page.find_tables()` for data-table localization. VLM = Qwen-VL-Max via OpenRouter for structure extraction + caption matching + fake-figure filtering). Size cap: ≤ 80 pages, ≤ 40 MB per file — do not pre-rasterize image-heavy papers to "shrink" them under the cap, that kills embedded-figure extraction and forces the planner onto NBP fallback.
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

**v1.0 MVP — code complete** (2026-04-20). All 10 code items (#1 – #8.75) shipped across the commit series; full 3-artifact coverage (poster + deck + landing) with in-browser edit toolbar + `apply-edits` round-trip; 11 tools wired (v1.1+ added `ingest_document`); smoke **18/18** green. Remaining for the v1.0 tag: **#10 demo video** (screencast, non-code) and the README screenshots / showcase gallery polish. Post-v1.0 work (v1.1 / v1.2.0 – v1.2.5) has been shipping continuously since 2026-04-20 — see [ROADMAP.md](docs/ROADMAP.md).

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
