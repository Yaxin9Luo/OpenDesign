# LongcatDesign — Knowledge Base

This directory is the **single source of truth** for what LongcatDesign is, why it exists, how it's built, and what's next. If you (or future-you, or a Longcat-Next teammate) come back to this project after a break and want to pick up cold without re-reading all of Slack, **start here**.

> **Status (2026-04-21)**: **v1.2 paper2any shipped + v1.2.5 polish**. Full 3-artifact coverage (poster + landing + deck) × full paper → artifact pipeline, now also eating `.docx` / `.pptx` / scanned-PDF inputs. 11 tools wired, smoke 18/18 green.
>
> **v1.2 milestones shipped this cycle**:
> - [ce50f2a](https://github.com/Yaxin9Luo/OpenDesign/commit/ce50f2a) — pymupdf-native figure extraction + Qwen-VL-Max ingest (replaces Claude-Sonnet vision locator).
> - [da664a5](https://github.com/Yaxin9Luo/OpenDesign/commit/da664a5) — `kind="table"` LayerKind → native PPTX `add_table` / HTML `<table>` / PIL-drawn PNG with bold-winner column highlights.
> - [a08bbb9](https://github.com/Yaxin9Luo/OpenDesign/commit/a08bbb9) — poster visual-density rules in planner + critic prompts (≥4 figures for paper posters; text-wall layouts flagged as blocker).
> - [349c899](https://github.com/Yaxin9Luo/OpenDesign/commit/349c899) — composite aspect-preserve (contain-fit for images, re-render-at-bbox for tables).
> - **v1.2.4** — deterministic text-overlap detector (catches title descender ↔ subtitle cap-height crashes at composite time, before the critic round) + figure↔text cross-reference enforcement in planner + critic prompts (assign display numbers in reading order; every placed `ingest_fig_NN` / `ingest_table_NN` must be cited as `(Fig. N)` / `(Table N)` in at least one text layer).
> - **v1.2.5** — `.docx` / `.pptx` ingest branches (structural readers, no VLM needed) + scanned-PDF OCR fallback via Qwen-VL-Max (6-worker page-parallel OCR at 200 dpi). Ingest now eats any paper or deck format the user throws at it; smoke suite gains two new steps (18/18).
>
> End-to-end verification on the 43-page Longcat-Next paper: poster critique **0.62 → 0.86**, landing **pass 0.92**, deck **pass 0.88** with 8 bold-winner cells on a 15×12 editable PPTX table. See [ROADMAP.md § Shipped](ROADMAP.md#shipped).

---

## What LongcatDesign is in 30 seconds

An **open-source, terminal-first conversational design agent**. Describe what you want (poster / slide deck / landing page); LongcatDesign builds and iterates with you via a CLI chat shell, exporting to **HTML (first-class, contenteditable + apply-edits round-trip) + PPTX (native PowerPoint TextFrames) + editable PSD/SVG**.

Commercial-grade by default: landing pages and decks call **Gemini 3 Pro Image Preview (NBP)** for inline imagery with per-artifact style prefixes, so output is "investor-ready," not wireframe.

Positioned as the open-source alternative to [Claude Design](https://www.anthropic.com/news/claude-design-anthropic-labs) — terminal-first instead of browser-only, open formats instead of Canva-locked, model-agnostic instead of tied to one subscription.

> **Pivot note (2026-04-18)**: This project started as "Design-Agent," a research prototype for capturing training trajectories for the Longcat-Next layered-generation model. After Claude Design shipped, we pivoted to ship **LongcatDesign** as an open-source product. The trajectory-capture machinery is preserved as internal session state; it's no longer the product pitch. See [DECISIONS.md](DECISIONS.md) for the rationale.

---

## Recommended reading order

### If you're new to the project
1. [VISION.md](VISION.md) — why this exists, end-goal, Longcat-Next connection
2. [ARCHITECTURE.md](ARCHITECTURE.md) — what the codebase looks like
3. [DATA-CONTRACT.md](DATA-CONTRACT.md) — the trajectory format = the product
4. [WORKFLOWS.md](WORKFLOWS.md) — how to actually run it

### If you're picking up where we left off (future-self)
1. [ROADMAP.md](ROADMAP.md) — what's next
2. [DECISIONS.md](DECISIONS.md) — why we made the calls we made
3. [GOTCHAS.md](GOTCHAS.md) — landmines we already stepped on

### If you're debugging something
1. [GOTCHAS.md](GOTCHAS.md) — check known issues first
2. [ARCHITECTURE.md](ARCHITECTURE.md) — module map to find the right file

---

## Documents in this KB

| File | Purpose | When to read |
|---|---|---|
| [VISION.md](VISION.md) | Product pitch (LongcatDesign as OSS Claude Design alt); paper2any North Star; differentiation | First read; any time scope is being debated |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Components, files, data flow, the 11 tools, composite dispatch per artifact type, dual-SDK LLM routing (Anthropic + OpenAI for Qwen) | Before touching code |
| [V1-MVP-PLAN.md](V1-MVP-PLAN.md) | Concrete shipping plan for v1.0 MVP launch (work breakdown + estimates + per-item status) | Before starting implementation |
| [DATA-CONTRACT.md](DATA-CONTRACT.md) | Session-state / trajectory schema (internal detail post-pivot) | Before touching `schema.py` |
| [WORKFLOWS.md](WORKFLOWS.md) | Run pipeline, edit artifacts, smoke test, extending tools, per-artifact-type workflows | Day-to-day reference |
| [DECISIONS.md](DECISIONS.md) | Design decisions log (pivot, no LangGraph, OpenRouter, deck schema, critic branches, etc.) | Before reopening a settled question |
| [ROADMAP.md](ROADMAP.md) | v1.0 MVP + v1.1 / v1.2 paper2any (shipped) + v1.3+ planned versions | Planning next session of work |
| [GOTCHAS.md](GOTCHAS.md) | Runtime quirks + fixes (dotenv, Gemini PNG/JPEG, OpenRouter base_url, macOS UF_HIDDEN .pth, planner max_tokens, PPTX CJK fonts) | Any time something behaves unexpectedly |
| [COMPETITORS.md](COMPETITORS.md) | Audited related projects (Paper2Any, Claude Design, Lovart); differentiation analysis | When asked "but what about X?" / planning a feature already shipped elsewhere |

---

## Living artifacts (outside this KB but always relevant)

- **The reference article**: [`design_agent_blog.pdf`](../design_agent_blog.pdf) at the repo root — Claude Opus 4.7 + Lovart 国宝回家 case. The motivating example and the gap we're closing.
- **Sample trajectories**: `out/trajectories/<run_id>.json` — every successful run dumps one. Internal session-state record; schema in [DATA-CONTRACT.md](DATA-CONTRACT.md).
- **Sample artifacts**: `out/runs/<run_id>/` — per run: `poster.{psd,svg,html}` + `preview.png` (poster) · `index.html` + `preview.png` (landing) · `deck.pptx` + `slides/*.png` + `preview.png` (deck).
- **Chat sessions**: `sessions/<session_id>.json` — multi-turn conversations wrapping N trajectories.

---

## Maintenance

This KB is meant to be edited as we go, not written once. When you:

- **Add a new feature** → update [ARCHITECTURE.md](ARCHITECTURE.md) and check off the item in [ROADMAP.md](ROADMAP.md)
- **Hit a new bug or quirk** → log it in [GOTCHAS.md](GOTCHAS.md) with the fix
- **Make a non-obvious design call** → log it in [DECISIONS.md](DECISIONS.md) with rationale
- **Change the trajectory schema** → update [DATA-CONTRACT.md](DATA-CONTRACT.md) immediately (this one is brittle to drift)
- **Plan a new version** → bump [ROADMAP.md](ROADMAP.md)
- **Re-scope the project** → revise [VISION.md](VISION.md)

Don't let the KB rot. If a doc here disagrees with the code, **the code is right and the doc is a bug**. Fix the doc.
