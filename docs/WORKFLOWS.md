# Workflows

Day-to-day recipes. For *why* the system is shaped the way it is, see [VISION.md](VISION.md) and [DECISIONS.md](DECISIONS.md). For module reference, see [ARCHITECTURE.md](ARCHITECTURE.md).

---

## Setup (one-time)

Environment is managed by [uv](https://docs.astral.sh/uv/). Install uv once, then:

```bash
cd /Users/yaxinluo/Desktop/Projects/Design-Agent
uv sync                    # reads pyproject.toml + uv.lock → .venv with pinned deps
cp .env.example .env
# edit .env to fill in:
#   FRIDAY_APP_ID (company Friday route for both LLM + image generation)
#   OR one of:
#     OPENROUTER_API_KEY  (external pay-as-you-go)
#     ANTHROPIC_API_KEY   (stock Anthropic — needs balance topped up)
```

Day-to-day commands are prefixed with `uv run` (auto-syncs deps and uses the project venv). You can still `source .venv/bin/activate` if you prefer the old workflow — uv just manages the venv, it does not replace it.

If both LLM keys are set, **OpenRouter wins**. To force stock Anthropic, comment out `OPENROUTER_API_KEY`. See [GOTCHAS.md](GOTCHAS.md) if env vars don't load (shell-exported empty values can mask `.env`).

---

## Smoke test (no API, no $$, ~5 sec)

Use this whenever you change tools, schema, fonts, or composite logic:

```bash
uv run python -m open_design.smoke
```

Verifies 7 steps: imports (incl. chat + session modules), tool registry shape (8 tools + `switch_artifact_type` first), `Trajectory` Pydantic round-trip, font loading, real composite call against a stub background, SVG vector text + embedded fonts, **ChatSession save/load round-trip**.

Outputs go to `out/smoke/`. Inspect `poster.psd` (should have 3 named layers: `background` + group `text` containing `title` + `subtitle`) and `poster.svg` (`<text>国宝回家</text>` should be a real vector element).

---

## Two ways to run: chat (default) vs one-shot

### Chat shell (v1.0 default — conversational multi-turn)

```bash
uv run python -m open_design.cli
# or equivalently:
uv run python -m open_design.cli chat
```

Launches a REPL. Type a brief, agent generates, iterate. Every turn auto-saves to `sessions/<session_id>.json`. See [§slash commands](#slash-commands) below.

Resume a prior session:

```bash
uv run python -m open_design.cli chat --resume session_20260418-214348_ea6c0de9
```

### One-shot (legacy, for scripting / CI / single-brief work)

```bash
uv run python -m open_design.cli run "<your brief>"
```

Examples:

```bash
# Minimal — replicates the 国宝回家 reference case
uv run python -m open_design.cli run "国宝回家 公益项目主视觉海报，竖版 3:4"

# Academic poster (text-heavy)
uv run python -m open_design.cli run "学术海报：CVPR 2026 投稿《<title>》。需要：主标题 + 5 位作者及 affiliation + 4 个 section（Abstract / Method / Results / Conclusion）+ 底部 conference info + 右上角 QR 占位框。竖版 3:4。"
```

Both modes produce artifacts in `out/runs/<run_id>/` (per-artifact-type — see layout below) and trajectories in `out/trajectories/<run_id>.json`. Chat mode ADDITIONALLY wraps trajectories under `sessions/<session_id>.json`.

**Per-artifact output layout** (`out/runs/<run_id>/`):

| Type | Files | Notes |
|---|---|---|
| POSTER | `poster.psd` · `poster.svg` · `poster.html` · `preview.png` · `layers/` | HTML has edit toolbar + `data-*` round-trip attrs |
| LANDING | `index.html` · `preview.png` · `layers/img_*.png` (NBP imagery) | HTML uses 1 of 6 bundled design systems |
| DECK | `deck.pptx` · `preview.png` (grid) · `slides/slide_NN.png` · `layers/img_*.png` (NBP imagery) | PPTX has live TextFrames for editing |

**Cost & time** per artifact (measured — see [ARCHITECTURE.md § Performance baselines](ARCHITECTURE.md#performance-baselines-reference) for the full table):

- Poster, 5 layers, 1 NBP bg, 1-iter critic: ~100 s, ~$1.4
- Landing, 4 sections, 5 NBP images, 1-iter critic: ~200 s, ~$2.2
- Deck, 10 slides, 10 NBP images, 1-iter critic: ~380 s, ~$3.4

---

## Slash commands

Inside the chat REPL, any line starting with `:` is a slash command; everything else is a brief for the planner.

| Command | Effect |
|---|---|
| `:help` · `:h` · `:?` | Show command reference |
| `:save [id]` | Persist session to `sessions/<id>.json` (default: current session_id; auto-saves happen after every turn) |
| `:load <id>` | Replace current session with the loaded one (auto-saves current first) |
| `:new` | Start fresh session (auto-saves current). New session_id generated. |
| `:list` · `:ls` | List recent sessions (most recent first, current marked with `*`) |
| `:history` | Show message history with timestamps + trajectory refs |
| `:tokens` · `:cost` | Cumulative session stats per artifact |
| `:export [path]` | Copy all artifacts + session.json to `path/` (default: `~/Desktop/<session_id>/`) |
| `:exit` · `:quit` · `:q` · Ctrl-D | Exit (auto-saves session) |

### Revision vs new-artifact (automatic)

Revisions go through the planner's natural retry path — you don't need a dedicated slash command. Say "make the title bigger" and the planner picks the cheaper route (v1.0 #5 `edit_layer` tool for single-field text tweaks; full `propose_design_spec` re-call for structural changes). See ["Chat mode: revision vs new-artifact decision" in prompts/planner.md](../prompts/planner.md) for the decision rules.

## Chat mode — revision vs new-artifact

When a session already has prior artifacts, each new brief is automatically prefixed with a summary of the latest trajectory before going to the planner. The planner then decides:

- **Revision** (keep same artifact, tweak): user says "make the title bigger", "change palette to red", "try a cleaner composition" → planner re-calls `edit_layer` for single-field text tweaks OR `propose_design_spec` for structural changes, re-renders affected layers, recomposites. Background stays unless explicitly asked.
- **New artifact** (possibly different type): user says "now a matching landing page", "give me a horizontal version", "a poster for DIFFERENT project X" → planner calls `switch_artifact_type` first, then fresh `propose_design_spec`. Prior artifact preserved in session for reference.

The decision rules live in [`prompts/planner.md`](../prompts/planner.md) "Chat mode: revision vs new-artifact decision" section.

## Per-artifact workflows

### Poster (`artifact_type = "poster"`)

Absolutely-positioned layers over a text-free NBP background.

```
switch_artifact_type("poster")
  → propose_design_spec { canvas, palette, layer_graph[] }
  → generate_background (1× NBP, with safe_zones protecting text regions)
  → render_text_layer × N (Pillow rasterization, one per text layer)
  → composite → PSD + SVG + HTML + preview.png
  → critique (vision) → pass / revise / fail
  → (if revise) edit_layer or re-render → composite again
  → finalize
```

Canvas: typically 3:4 (`1536×2048`) or 4:3 (`2048×1536`) for print; `1920×1080` for screen. Output is commercial-printable because text stays as editable vector in SVG + named pixel layers in PSD.

**v2.3.3 `--template` flag** — skip typing venue dims:
```
open-design run --from-file paper.pdf --template neurips-portrait "poster brief..."
```
5 bundled presets: `neurips-portrait` / `cvpr-landscape` / `icml-portrait` / `a0-portrait` / `a0-landscape`. Resolves to a canvas dict (w_px / h_px / dpi / aspect_ratio) injected via brief prologue. Case-insensitive; unknown names fail fast before any API cost.

### Landing (`artifact_type = "landing"`)

Self-contained HTML one-pager with 6 bundled design systems + inline NBP or ingested imagery. **v1.3** adds CTA layers, auto-generated top nav, reveal-on-scroll, and semantic `<header>/<main>/<footer>`.

```
switch_artifact_type("landing")
  → propose_design_spec {
      canvas: { w_px: 1200-1440 },
      design_system: { style: "editorial", accent_color: "#...", show_nav: null|true|false },
      layer_graph: [
        section {
          name: "hero"|"features"|"cta"|"footer"|...,
          children: [
            text { ... },                              // native HTML text
            image { src_path: null (→ generate_image) OR ingest_fig_NN (paper passthrough) },
            table { rows, headers, col_highlight_rule },  // real <table>
            cta { text, href, variant: primary|secondary|ghost }  // v1.3 <a role="button">
          ]
        },
        ...
      ]
    }
  → generate_image × N (only for NBP imagery; skip entirely for paper landings per the policy below)
  → composite → index.html + preview.png (NO PSD/SVG — text is native HTML)
  → critique (text-only) → pass / revise / fail
  → finalize
```

**v1.3 auto-behaviors** (no spec fields needed):
- Every `<section>` gets `id="sec-{slug(name)}"` — CTAs with `href="#sec-pricing"` scroll smoothly to that section.
- A `<header><nav>` is auto-prepended when `section_count ≥ 4`; override explicitly via `design_system.show_nav: true|false`. Hero + footer sections are skipped from the nav; active link gets `aria-current="page"` via JS.
- Last section whose `variant == "footer"` is auto-upgraded to semantic `<footer>` outside `<main>` (apply-edits round-trip also scans for it).
- An inline vanilla JS IIFE runs `IntersectionObserver` reveal-on-scroll (`[data-reveal]` → `.is-revealed`) + smooth `scrollIntoView` on `<a href="#...">` + active-nav tracking. Respects `prefers-reduced-motion`.

**v2.3.4 KaTeX math** — `$…$` / `$$…$$` / `\(…\)` / `\[…\]` in any text layer auto-typeset client-side via self-hosted KaTeX 0.16.9. Gated on a `_has_math()` scan of the layer_graph — landings without math skip the ~645 KB bundle entirely. Preserve LaTeX markup verbatim in `text` layers; do NOT rasterize equations as images. Known limitation: in-browser edits to math-containing text operate on KaTeX-rendered spans, not the LaTeX source (v2.5 will add `data-math-source` preservation).

Design system picker (via `prompts/planner.md` loudness cheat sheet):

| Style | Loudness | Brief vibe |
|---|---|---|
| editorial | 3/10 | publication · essay · long-form · **paper landings (default)** |
| minimalist | 3/10 | SaaS · fintech · enterprise |
| liquid-glass | 5/10 | premium · Apple-like · media-rich |
| claymorphism | 5/10 | friendly · consumer · wellness |
| glassmorphism | 6/10 | modern SaaS · AI platform |
| neubrutalism | 10/10 | indie · punk · bold portfolio |

**Paper landings (v1.3.1)** — when `ingest_document` registered ≥ 3 figure layers, the prompt enforces an academic vs marketing split:
- ≥ 3 ingested figure layers in content sections if ≥ 5 available (≥ 5 if ≥ 10); NO NBP calls for `highlights` / `method` / `benchmarks` / `showcase`. NBP is reserved for imagery the paper can't provide (rare — most papers have Fig. 1 as a viable hero).
- Ingested `ingest_table_NN` MUST appear on the landing as `kind: "table"` — a real HTML `<table>` with winner-cell bolding, not a cropped screenshot.
- Preferred styles: `editorial` (default), `minimalist`, `liquid-glass`. Consumer-product styles (`claymorphism`, `neubrutalism`, `glassmorphism`) are explicitly discouraged.

### Deck (`artifact_type = "deck"`)

Native PowerPoint `.pptx` with one slide per top-level `kind: "slide"` LayerNode.

```
switch_artifact_type("deck")
  → propose_design_spec {
      canvas: { w_px: 1920, h_px: 1080 },  # 16:9 default
      composition_notes: "<style-prefix for imagery coherence>",
      layer_graph: [
        slide { children: [background, text (title), text (body), image (src_path: null)],
                speaker_notes: "<talking points + timing + Q&A — v2.3.1>" },
        ...
      ]
    }
  → generate_image × N (one per image / background child; style-prefix from composition_notes goes at the start of every prompt)
  → composite → deck.pptx + slides/slide_NN.png + preview.png (grid)
  → critique (text-only, slide-tree rubric) → pass / revise / fail
  → finalize
```

**v2.3.1 speaker notes** — each `kind: "slide"` LayerNode can carry `speaker_notes: str | None`. Populates `slide.notes_slide.notes_text_frame.text` so PowerPoint / Keynote presenter view shows the notes. Guidelines: ≤ 200 words per slide, skip cover / thank-you / divider, match language to slide body.

**Typography ranges** (enforced by `prompts/critic-deck.md`):

| Role | font_size_px | Notes |
|---|---|---|
| Slide title | 48–96 | Cover title up to 120 |
| Body / bullets | 24–40 | 28–32 sweet spot for back-of-room readability |
| Caption / footer | 14–22 | Slide numbers, footer attribution |
| Big stat | 140–240 | Paired with 24–36 caption below |

**Imagery budget**: 1 image per content slide + 1 cover background = ~6-10 NBP calls per deck. At ~$0.10 per 1K image, a 10-slide deck ≈ $0.80-1.50 in NBP cost. Fine.

## Inspect outputs

### Find latest run id (shell helper)

```bash
# Most recent run
LATEST_RUN=$(ls -t out/runs/ | head -1)

# Most recent session
LATEST_SESSION=$(ls -t sessions/ | head -1 | sed 's/\.json$//')
```

### View the flat preview

```bash
open out/runs/$LATEST_RUN/preview.png
```

### Open SVG in browser (THE BEST way to see the truth)

```bash
open out/runs/$LATEST_RUN/poster.svg
```

The browser is the reference renderer for our SVG output. Every `<text>` element renders correctly with the embedded WOFF2 font.

### Inspect PSD layer tree

```bash
uv run python -c "
from psd_tools import PSDImage
p = PSDImage.open('out/runs/<run_id>/poster.psd')
def walk(n, d=0):
    for L in n:
        kind = 'group' if L.is_group() else 'pixel'
        print(f'  {\"  \"*d}- [{kind}] {L.name!r}  bbox={L.bbox}')
        if L.is_group(): walk(L, d+1)
walk(p)
"
```

### Verify SVG text is vector (not rasterized)

```bash
grep -oE '<text[^>]*>[^<]+</text>' out/runs/<run_id>/poster.svg
```

Should return one line per text layer.

### Read the trajectory

```bash
uv run python -c "
import json
t = json.load(open('out/trajectories/<run_id>.json'))
print(f'brief: {t[\"brief\"]}')
print(f'layers: {len(t[\"layer_graph\"])}')
print(f'trace steps: {len(t[\"agent_trace\"])}')
print(f'critiques: {len(t[\"critique_loop\"])}')
print(f'metadata: {t[\"metadata\"]}')
"
```

---

## Paper → poster / landing / deck (v1.2 paper2any, shipped)

Drop a source document into any run — planner calls `ingest_document` first, extracts structure + native-resolution figures + structured tables, then maps to the target artifact type.

### CLI — one-shot

```bash
uv run python -m open_design.cli run \
  --from-file ~/papers/longcat-next.pdf \
  "基于附件的论文，设计一张 3:4 学术海报。包含：标题 + 作者 + abstract + method + results + 2-3 张原论文图表直接 passthrough。学术会议风格。"

# repeatable — attach multiple files
uv run python -m open_design.cli run \
  --from-file paper.pdf --from-file brand-logo.png \
  "landing page for this paper, claymorphism style, use the attached logo in the hero"
```

### Chat REPL

```
> :attach ~/papers/paper.pdf
  ✓ queued: paper.pdf (17 MB). Will be ingested on the next non-slash turn.
> :attach ~/photos/team.jpg
  ✓ queued: team.jpg (2 MB). Will be ingested on the next non-slash turn.
> 10-slide pitch deck from this paper, team photo on the thank-you slide
```

Attachments are cleared automatically after the next non-slash turn; use `:detach` to abort.

### Supported inputs

| Extension | Handling | Notes |
|---|---|---|
| `.pdf` | pymupdf native: `doc.extract_image(xref)` for embedded rasters at author-uploaded resolution; `get_drawings()` clustered + rendered at 300 dpi for vector diagrams; `find_tables()` for table localization. Qwen-VL-Max (via `util/vlm.py`) only for structure extraction + caption matching + fake-figure filtering + table cell parsing. | ≤ 80 pages, ≤ 40 MB per file (raised from 24 MB in v2.3 — image-heavy papers like BAGEL routinely land in the 25-35 MB range, and dropping below the cap via page rasterization destroys pymupdf's embedded-figure extraction). Larger → split. Scanned PDFs auto-fall-back to Qwen-VL OCR (no longer raises). |
| `.md` / `.markdown` / `.txt` | Raw text passthrough + `![alt](./image.png)` refs resolved + copied into `ctx.layers_dir` | Relative paths only; URLs / data: are skipped |
| `.png` / `.jpg` / `.jpeg` / `.webp` | `shutil.copy2` into `ctx.layers_dir` + register as passthrough layer | Single-image bundle (logo, brand shot, reference photo) |

### What the planner sees

The ingest tool_result summary includes a **ranked top-20 figure catalog** (with `page`, `width×height`, `extract-strategy`, `caption` per figure) plus any registered tables with shape + caption. This is what drives the planner's figure picks on paper posters — see the v1.2 poster visual-density rules in [`prompts/planner.md`](../prompts/planner.md) (figure-count floor ≥ 4, diversity rules, image-area ≥ 45 %). `ctx.state["ingested"]` carries the full structured manifest:

```json
{
  "file": "/abs/path/paper.pdf",
  "type": "pdf",
  "manifest": {
    "title": "LongCat-Next: Lexicalizing Modalities as Discrete Tokens",
    "authors": ["Meituan LongCat Team"],
    "abstract": "…",
    "sections": [{"idx": 1, "heading": "Introduction", "summary": "…", "key_points": […]}, …],
    "figures": [{"caption": "…", "page": 3, "description": "…"}, …],
    "tables": [{"caption": "…", "page": 16}, …],
    "key_quotes": […]
  },
  "registered_layer_ids": ["ingest_fig_01", …, "ingest_table_01", "ingest_table_02"],
  "registered_figure_ids": ["ingest_fig_01", "ingest_fig_02", …],
  "registered_table_ids": ["ingest_table_01", "ingest_table_02"]
}
```

- **Figures** (`ingest_fig_NN`, kind="image") register with native-resolution PNG at `src_path` + `caption` from VLM caption matching. Reference them in `propose_design_spec.layer_graph` as `{"layer_id": "ingest_fig_03", "kind": "image", "bbox": {…}}` — composite hydrates.
- **Tables** (`ingest_table_NN`, kind="table") register with structured `rows`, `headers`, `col_highlight_rule` (`"max"` / `"min"` / `""` per column → renderers bold the winning row per column). Reference as `{"layer_id": "ingest_table_01", "kind": "table", "bbox": {…}}`; the planner MAY override `rows` / `headers` / `col_highlight_rule` to subset a wide table down to slide-friendly dims.

### What the renderers do with them

| Artifact | Figures (`kind="image"`) | Tables (`kind="table"`) |
|---|---|---|
| **Poster** | Native-res PNG, contain-fit into bbox via `_aspect_fit_contain`, SVG uses `preserveAspectRatio="xMidYMid meet"` | PIL-drawn PNG re-rendered at bbox dims at composite time (font autoscale, row truncation if `bh` too tight); deep-green winner text (bundled Noto is bold-only so color carries the highlight) |
| **Landing** | Inline `<img>` inside `<figure>` with data-URI base64 embed | Real `<table class="ld-table">` with `<thead>`, zebra `<tbody>`, `.ld-table-winner` bolded via CSS `font-weight: 700` |
| **Deck** | Native-res PNG as PPTX picture shape | Native `slide.shapes.add_table` — live editable in PowerPoint / Keynote. Column widths scale with cell content length, font size autoscale (11-18 pt by row count), bold-winner cells |

### Sample costs (43-page / 17 MB Longcat-Next paper, Qwen-VL-Max default)

| Run | Wall time | Cost | Critique | Imagery |
|---|---|---|---|---|
| paper → poster (v1.2) | ~8-11 min | $6-12 | revise 0.86 (3 issues) | 5 ingested figures + 1 ingested table |
| paper → landing (v1.3, claymorphism — marketing vibe) | 8m 12s | $4.42 | pass 0.88 (3 issues) | 5 NBP icons + 1 ingested figure + 1 table |
| **paper → landing (v1.3.1, editorial — academic)** | **6m 26s** | **$4.07** | **pass 0.88** (4 issues) | **0 NBP + 9 ingested figures + 1 table** |
| paper → deck | ~7.8-9.6 min | $4.17-6.63 | pass 0.88 (4 issues) | Mix — planner chooses per slide |

Landing is fastest + cheapest because flow layout needs no preview rasterization; deck is moderate because of per-slide PNG previews + critic. Poster is most expensive + most-revised because the visual-density critique is strictest (20 % weight) and the layout has more placement DOF.

The claymorphism-vs-editorial contrast on the same paper is the v1.3.1 dogfood — adding "Paper landing imagery policy" rules to the planner prompt flipped the feature/highlights/method sections from NBP stock icons to the paper's actual figures without changing any code.

### Tuning

| Env var | Default | Purpose |
|---|---|---|
| `INGEST_MODEL` | `anthropic/claude-sonnet-4-6` (OpenRouter) / `claude-sonnet-4-6` (stock) | Override the structure-extraction + bbox-locator model (e.g. Opus for higher fidelity on visually complex papers) |
| `INGEST_HTTP_TIMEOUT` | `600` (10 min) | Hard timeout per ingest Anthropic call — prevents silent hangs on big PDFs |

### Known quirks (dogfood surfaced)

- Big PDFs (17 MB+) on OpenRouter sometimes fail with SSL EOF after 2-3 min. Retry usually works; v1.1.5 will add explicit retry handling in the planner loop.
- Claude occasionally emits `"figures": null` instead of `[]` for figure-light papers — coerced to empty list in `ingest_document._ingest_pdf`.
- OpenRouter → Anthropic geo-403 ("model not available in your region") can hit mid-run when VPN routes shift. Switch planner model to Sonnet (`export PLANNER_MODEL=anthropic/claude-sonnet-4-6`) if the Opus route is blocked.

---

## Editing artifacts

### Path A — in-browser edit toolbar + `apply-edits` (POSTER + LANDING) ✅

Every poster + landing HTML ships with an inline edit toolbar. This is the **preferred path** for text tweaks and small layout adjustments.

```bash
open out/runs/<run_id>/poster.html
# or: open out/runs/<run_id>/index.html   (landing)
```

In the browser:
1. Click any text layer → floating toolbar appears (font family / font size / color / drag handle).
2. Edit inline (the text is `contenteditable`; toolbar writes to `data-*` attrs for the round-trip).
3. Click **Save** → dialog offers **Copy HTML** or **Download HTML**.

Round-trip back into PSD / SVG / HTML (poster) or HTML (landing):

```bash
uv run python -m open_design.cli apply-edits ~/Downloads/poster-edited.html
#  → new out/runs/<new_run_id>/ with metadata.source = "apply-edits"
#    and metadata.parent_run_id tracing back to the original
```

For POSTER this regenerates `poster.psd + poster.svg + poster.html + preview.png` from the edited HTML's `data-*` attrs. For LANDING it rebuilds the section tree and rewrites `index.html + preview.png`. The background PNG is recovered from the embedded `data:` URI — no dependency on the original `run_dir` still existing.

### Path B — PowerPoint / Keynote / Google Slides (DECK) ✅

Deck editability is native:

```bash
open out/runs/<run_id>/deck.pptx
```

Every title / body / caption is a live `TextFrame` — double-click and type. Font family / size / color all work through the consuming app's UI. Images are picture shapes (drag to move, resize via handles).

There is no `apply-edits` path for deck — PowerPoint IS the edit surface. If you want a re-generated variant, edit the source brief in a new chat turn.

### Path C — SVG text editor (POSTER power-user)

Open `poster.svg` in any text editor (VSCode, Sublime), find:

```xml
<text fill="#fafafa" font-family="'NotoSerifSC-Bold'"
      font-size="240" text-anchor="middle"
      x="768" y="420">国宝回家</text>
```

Change content/color/size/position attrs, save, reload in browser. Done.

**Caveat — character set drift**: the embedded font is subsetted to only the glyphs in the original text. If you change `国宝回家` → `国宝回家了`, the `了` won't have a glyph and renders as a fallback (or `□`). To add new characters cleanly, use Path A (in-browser toolbar) or re-issue the brief with the changed text.

### Path D — Inkscape / Illustrator / Affinity (POSTER SVG)

```bash
open -a Inkscape out/runs/<run_id>/poster.svg
```

These respect embedded WOFF2 fonts and render text as editable vector objects. Good for vector fine-tuning that's painful in the HTML toolbar.

### Path E — Photoshop (POSTER PSD)

Open `poster.psd`. You'll see ≥ 5 named layers (background + a `text` group containing per-element pixel layers). What you can do:

- ✅ Move / resize / rotate / re-order layers
- ✅ Adjust opacity / blend mode
- ✅ Hide a layer; add your own type layer over it
- ❌ Double-click to edit text content (text layers are RASTER, not type — v1.3 plans real PSD type layers)

For text edits in PS, the practical workflow is: hide the existing pixel layer, add a fresh PS Type layer over it. Or use Path A/C for the edit, then re-export.

### Path F — Figma (currently broken for SVG)

Figma's SVG importer mishandles `text-anchor`, drops embedded `@font-face`, and breaks layout. **Don't use Figma for SVG editing.** See [GOTCHAS.md](GOTCHAS.md) entry "Figma SVG import" for the full diagnosis. Workaround: SVG → print to PDF → Figma. PSD import preserves the layer structure; you add Figma-native text overlays on top.

---

## Extending the system

### Add a new tool

1. Create `open_design/tools/<your_tool>.py`. The handler signature:

```python
from typing import Any
from ._contract import ToolContext, obs_ok, obs_error
from ..schema import ToolObservation

def your_tool(args: dict[str, Any], *, ctx: ToolContext) -> ToolObservation:
    # validate args
    # do work
    # mutate ctx.state if needed
    return obs_ok("did the thing", artifacts=["<path>"], next_actions=["<hint>"])
```

2. Register it in [`open_design/tools/__init__.py`](../open_design/tools/__init__.py):
   - Add a JSON schema entry to `TOOL_SCHEMAS` (description ends with the observation contract notice).
   - Add the handler to `TOOL_HANDLERS` dict.

3. Update [`prompts/planner.md`](../prompts/planner.md) to mention the tool, when to use it, and any constraints.

4. Update the `expected` tool set in `open_design/smoke.py::check_tool_registry`.

5. Re-run `uv run python -m open_design.smoke` — the registry assertion will catch typos.

### Add a new artifact type or renderer

1. Extend `ArtifactType` enum in [`schema.py`](../open_design/schema.py).
2. Add any new `LayerKind` literals needed (e.g. `"slide"` for deck).
3. Add a new branch in `tools/composite.py::composite()` that dispatches to your renderer.
4. Write the renderer module (e.g. `tools/pptx_renderer.py`) with a `write_<type>(spec, out_path, ctx)` function.
5. Add a critic rubric at `prompts/critic-<type>.md` if the artifact warrants text-only critique (landing + deck precedent).
6. Extend `critic.py::evaluate` to branch on the new artifact type.
7. Update `runner.py` to decide whether to preserve `spec.layer_graph` directly (nested) or materialise from `rendered_layers` (flat).
8. Add a `check_<type>_mode` to `smoke.py` and bump the header count (`[N/M]` across all checks).
9. Add a new "workflow" section to `prompts/planner.md`.

### Modify the trajectory schema

1. Edit [`open_design/schema.py`](../open_design/schema.py).
2. Update [DATA-CONTRACT.md](DATA-CONTRACT.md) to match (this drifts easiest).
3. Bump `metadata.version` in [`runner.py`](../open_design/runner.py) **only** for non-backward-compat changes.
4. Add a note to [DECISIONS.md](DECISIONS.md) under a new dated entry.
5. Re-run smoke — pydantic round-trip will catch breakage.

### Tweak the critic rubric

Rubrics live in markdown so they're tweakable without code changes:
- Poster → `prompts/critic.md` (vision-based)
- Landing → `prompts/critic-landing.md` (text-only on section tree)
- Deck → `prompts/critic-deck.md` (text-only on slide tree)

To change the pass threshold (currently `score ≥ 0.75`), edit the verdict-rules section of each rubric (they all share the same threshold by convention; if it ever moves into Python, it'll live in `critic.py::_parse_critique`).

### Switch LLM model (test cheaper alternatives)

Set env vars:

```bash
export PLANNER_MODEL="anthropic/claude-haiku-4-5"   # cheaper, see if planning still holds
export CRITIC_MODEL="anthropic/claude-sonnet-4-6"
uv run python -m open_design.cli run "..."
```

Both planner and critic still go through the same Anthropic SDK + tool-use protocol regardless of model.

### Force a verdict revision (to exercise the revise loop)

Tighten any of the critic rubrics' pass threshold (e.g. `score ≥ 0.90 AND zero blockers`). Run a few briefs — more will hit `verdict: "revise"`, producing pre/post layer_graph snapshots in `critique_loop`. Useful for testing the edit_layer and re-issue-propose_design_spec paths.

---

## Useful one-liners

```bash
# Count trajectories
ls out/trajectories/*.json | wc -l

# Total cost spent so far (sum of estimates)
uv run python -c "
import json, glob
total = sum(json.load(open(p))['metadata']['estimated_cost_usd']
            for p in glob.glob('out/trajectories/*.json'))
print(f'\${total:.2f} across {len(glob.glob(\"out/trajectories/*.json\"))} runs')
"

# Find runs where critic gave any blocker
uv run python -c "
import json, glob
for p in glob.glob('out/trajectories/*.json'):
    t = json.load(open(p))
    blockers = [i for c in t['critique_loop'] for i in c['issues'] if i['severity'] == 'blocker']
    if blockers:
        print(f'{t[\"run_id\"]}: {len(blockers)} blocker(s)')
"

# Average wall time per layer count
uv run python -c "
import json, glob
data = [(len(json.load(open(p))['layer_graph']), json.load(open(p))['metadata']['wall_time_s'])
        for p in glob.glob('out/trajectories/*.json')]
for layers, wt in sorted(data):
    print(f'{layers:3d} layers → {wt:.0f}s')
"
```

---

## Troubleshooting

| Symptom | First thing to check | Reference |
|---|---|---|
| `ANTHROPIC_API_KEY missing` despite `.env` being filled | shell exports an empty value masking it | [GOTCHAS.md](GOTCHAS.md) |
| `404 OpenRouter HTML page` from planner | `base_url` includes `/v1` (it shouldn't) | [GOTCHAS.md](GOTCHAS.md) |
| `'Group' object has no attribute 'create_pixel_layer'` | psd-tools 1.11+ API change | [GOTCHAS.md](GOTCHAS.md) |
| `Image.save() got unexpected kwarg 'format'` after Gemini call | google-genai SDK Image type | [GOTCHAS.md](GOTCHAS.md) |
| SVG opens fine in browser but explodes in Figma | Figma SVG importer is broken | [GOTCHAS.md](GOTCHAS.md) |
| `BadRequestError: credit balance too low` from Anthropic | top up at console.anthropic.com — or use OpenRouter | [GOTCHAS.md](GOTCHAS.md) |
| Edited SVG text shows `□` for new characters | font subset doesn't include those glyphs | [GOTCHAS.md](GOTCHAS.md) |
| Planner doesn't call `finalize` and hits `max_planner_turns` | tighten `prompts/planner.md` workflow contract | [ARCHITECTURE.md](ARCHITECTURE.md) |
