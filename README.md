# LongcatDesign

> **An open-source, terminal-first conversational design agent. Describe what you want; LongcatDesign builds and iterates it with you, exporting real HTML, PPTX, or editable PSD/SVG.**

The open-source alternative to [Claude Design](https://www.anthropic.com/news/claude-design-anthropic-labs) and similar closed SaaS design tools. From the [Longcat](https://github.com/) ecosystem.

> **Current status** (2026-04-18): v1.0 MVP **4 of 11 items shipped** — package rename, `switch_artifact_type` tool, conversational CLI chat shell, session persistence. Next deep work: HTML renderer. Remaining ~15h coding + docs/video to v1.0 tag. See [docs/V1-MVP-PLAN.md](docs/V1-MVP-PLAN.md) for the status table.

---

## What LongcatDesign does

Three artifact types, generated conversationally from a CLI chat shell:

- **Posters** — production-quality, fully layered. Output: HTML + PSD (named pixel layers) + SVG (real `<text>` vector) + PNG.
- **Slide decks** — editable PPTX. Output: PPTX (native PowerPoint type frames) + HTML preview.
- **Landing pages / one-pagers** — self-contained HTML with inline CSS, fonts, and assets. Output: single `.html` file.

All text is rendered as separate, named, editable layers — including Chinese. No "your title got rasterized into the background" failure mode.

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

---

## Quickstart

### Install

```bash
git clone https://github.com/Yaxin9Luo/longcat-design.git
cd longcat-design
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
cp .env.example .env       # fill in GEMINI_API_KEY + (OPENROUTER_API_KEY OR ANTHROPIC_API_KEY)
```

### Smoke test (no API, ~5 sec, 7 checks)

```bash
.venv/bin/python -m longcat_design.smoke
```

### Chat shell (default — conversational multi-turn, v1.0 #4 ✅)

```bash
.venv/bin/python -m longcat_design.cli
> design a 3:4 poster for "国宝回家 公益项目"
  ✓ poster generated  (5 layers · pass 0.86 · $1.41 · 100s)
> make the title bigger
  ✓ poster revised   (planner detects revision vs new-artifact automatically)
> now a matching landing page for this project
  (HTML renderer pending v1.0 #6)
> :history          # conversation log
> :tokens           # cumulative cost/wall per artifact
> :export ~/Desktop/guobao
> :exit
```

Full slash command reference in [docs/WORKFLOWS.md](docs/WORKFLOWS.md#slash-commands). Resume a prior session: `longcat_design.cli chat --resume <session_id>`.

### One-shot (legacy, for scripting / CI)

```bash
.venv/bin/python -m longcat_design.cli run "国宝回家 公益项目主视觉海报，竖版 3:4"
```

Outputs land in `out/runs/<run_id>/` (PSD/SVG/preview/layers). Chat mode additionally wraps trajectories under `sessions/<session_id>.json`.

---

## Architecture in one breath

A **chat REPL** loop takes each user turn, a single **Claude Opus 4.7** planner drives a **handwritten Anthropic tool-use loop** over 8 tools (declare artifact type, plan design, generate background via Gemini Nano Banana Pro, render text layers via Pillow, composite into PSD/SVG/HTML/PPTX, self-critique, finalize). Per-turn `Trajectory` JSON gets wrapped under a `ChatSession` persisted to `sessions/<id>.json`.

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

**v1.0 MVP in progress** (2026-04-18): 4 of 11 items shipped — package rename, `switch_artifact_type` tool + `ArtifactType` enum, conversational CLI chat shell + `ChatSession` persistence. Remaining v1.0 work: HTML renderer (the biggest remaining lift + primary differentiator), PPTX renderer, `edit_layer` tool, landing-page schema, README polish, demo video, smoke extension.

Three real dogfood runs validated (100s–300s, $1.4–$3.7 each): 国宝回家 poster, CVPR academic poster, LongcatDesign launch poster.

**Not yet published to PyPI.** Local development only until v1.0 tag.

---

## License

MIT (planned). Fonts in `assets/fonts/` are OFL (Noto Sans SC + Noto Serif SC), redistributable.

---

## Part of the Longcat ecosystem

LongcatDesign is built by the Longcat team alongside [Longcat-Next](https://github.com/) (next-gen layered image-text generation model). The trajectory-capture architecture preserved inside LongcatDesign can feed Longcat-Next's training data pipeline when needed — but that's a side-channel, not the product.
