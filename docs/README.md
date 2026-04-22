# LongcatDesign — Knowledge Base

This directory is the **single source of truth** for what LongcatDesign is, why it exists, how it's built, and what's next. If you (or future-you, or a Longcat-Next teammate) come back to this project after a break and want to pick up cold without re-reading all of Slack, **start here**.

> **Status (2026-04-22)**: **v2 training-data pipeline shipped** (PR [#1](https://github.com/Yaxin9Luo/OpenDesign/pull/1)), layered on top of v1.3 interactive academic landings. The project now IS both a product (OSS design agent) AND a training-data producer for layered design generation — every run is simultaneously a design artifact and a distillation-ready `DistillTrajectory` JSON. 11 tools wired, smoke **19/19 green**.
>
> **v2 milestones shipped this cycle**:
> - [30cab95](https://github.com/Yaxin9Luo/OpenDesign/commit/30cab95) — v2 `DistillTrajectory` schema (lean, 44 KB per run vs v1's 144 KB — no summary / next_actions hints that would cause train↔deploy distribution shift) + new `ToolResultRecord` tool contract + multi-provider LLM backend (`llm_backend.py` with `AnthropicBackend` + `OpenAICompatBackend`). Default planner + critic switched to **Kimi K2.6** (plaintext reasoning, ~$3.58/run); Claude one env var away. 9 protocol differences normalized in one abstraction layer.
> - [c264545](https://github.com/Yaxin9Luo/OpenDesign/commit/c264545) — versioned intermediate artifacts: `composites/iter_NN/` per composite call + `final/` relative symlinks, layer PNGs gain `.vN.png` suffix, payloads carry `version / relative_path / supersedes_sha256` chain. Revise loops + `edit_layer` calls preserve every prior state → DPO (rejected, chosen) pairs are now extractable by default. Smoke #19 verifies preservation.
> - [eeb6490](https://github.com/Yaxin9Luo/OpenDesign/commit/eeb6490) — `scripts/export_sft_jsonl.py`: flattens `out/trajectories/` → OpenAI-compat SFT jsonl, one record per assistant turn (planner or critic), with CoT + tool_calls + tool catalog + per-turn usage + metadata. CLI filters by `--min-reward / --source / --actor / --provider / --terminal-status`.
>
> **v1.3 milestones still shipped** (layered below):
> - [383f7db](https://github.com/Yaxin9Luo/OpenDesign/commit/383f7db) — interactive landing pages: `LayerKind "cta"` + auto-nav + reveal-on-scroll + semantic `<header>/<main>/<footer>`.
> - [b88a04a](https://github.com/Yaxin9Luo/OpenDesign/commit/b88a04a) — paper-landing imagery policy (ingested figures over NBP; default style `editorial` for paper landings).
> - [31d2fe9](https://github.com/Yaxin9Luo/OpenDesign/commit/31d2fe9) — self-landing dogfood (LongcatDesign's own landing page built by itself in 94 s, pass 0.92, 3 own architecture diagrams as ingested figures).
>
> **v2 self-landing dogfood** (run `20260422-162157-d0f37cba`, Kimi K2.6 end-to-end, re-runs the self-landing from [31d2fe9](https://github.com/Yaxin9Luo/OpenDesign/commit/31d2fe9) on the v2 stack):
> - **Terminal**: `pass` · **Reward**: 1.0 · **Wall**: 4:00 · **Cost**: $2.90 · **Model**: `moonshotai/kimi-k2.6` (planner + critic) · **Backend**: `openai_compat`
> - **Trajectory** 72 KB on disk (v1 equivalent: ~144 KB → -50 %). 24 trace steps (`{input: 1, reasoning: 8, tool_call: 7, tool_result: 7, finalize: 1}`) — **zero** `summary` / `next_actions` / `artifacts` in any tool_result payload (verified by inspection).
> - **9 sections rendered** — hero + three-contributions + how-it-works (agent-arch diagram) + rendering-pipeline (pipeline diagram) + paper2any (paper2any-flow diagram) + **training-data** (new v2 section explaining DistillTrajectory on itself) + dogfood-stats + cta + footer. All 3 architecture diagrams placed in correct sections, 0 NBP calls.
> - **SFT export sample**: `python scripts/export_sft_jsonl.py --min-reward 0.85 --terminal-status pass` → 20 records from 2 trajectories, 80 710 total thinking chars (avg 4 K chars/record), 1.7 MB jsonl. First record's `target.reasoning_content` alone is 21 917 chars of plaintext Kimi CoT — the un-redacted training signal that's the whole point of going multi-provider.
>
> **Paper2landing dogfood** (retained from v1.3.1) on the 43-page Longcat-Next paper: poster critique **0.62 → 0.86**, landing **pass 0.92** (editorial: 0 NBP + 9 paper figures + 1 benchmark table across 8 sections), deck **pass 0.88** with 8 bold-winner cells on a 15×12 editable PPTX table. See [ROADMAP.md § Shipped](ROADMAP.md#shipped) for the full ladder.

---

## What LongcatDesign is in 30 seconds

An **open-source, terminal-first conversational design agent AND a training-data pipeline** for layered design generation. Describe what you want (poster / slide deck / landing page); LongcatDesign builds and iterates with you via a CLI chat shell, exporting to **HTML (first-class, contenteditable + apply-edits round-trip) + PPTX (native PowerPoint TextFrames) + editable PSD/SVG**. Every run **simultaneously** writes a structured `DistillTrajectory` JSON built for SFT distillation + DPO / RL post-training — the product's output IS the dataset's input.

Commercial-grade by default: landing pages and decks call **Gemini 3 Pro Image Preview (NBP)** for inline imagery with per-artifact style prefixes, so output is "investor-ready," not wireframe. Model-agnostic at the planner + critic layer — default stack is **Kimi K2.6** (plaintext reasoning, ~$3.58/run), **Claude Opus 4.7** one env var away, **any OpenAI-compatible endpoint** (DeepSeek-R1, Doubao, vLLM, self-hosted) works out of the box.

Positioned as both:
- the **open-source alternative to [Claude Design](https://www.anthropic.com/news/claude-design-anthropic-labs)** — terminal-first instead of browser-only, open formats instead of Canva-locked, model-agnostic instead of tied to one subscription;
- the **training-data collection vehicle for the [Longcat](https://github.com/) layered / interleaved image-text ecosystem** — intermediate artifacts (revise loops, layer edits) are versioned on disk so DPO pairs and layered-gen SFT samples can be extracted without re-running anything.

> **Evolution note (2026-04-18 → 2026-04-22)**: This project started as "Design-Agent," a research prototype for capturing training trajectories for the Longcat-Next layered-generation model. After Claude Design shipped, we pivoted to ship **LongcatDesign** as an open-source product (pivot 2026-04-18). Four days later (2026-04-22), PR [#1](https://github.com/Yaxin9Luo/OpenDesign/pull/1) **closed the loop**: the product is now an explicit training-data pipeline again — the agent's designs go to users, and its trajectories + versioned intermediate artifacts feed Longcat-Next training. Both missions run on the same code. See [DECISIONS.md](DECISIONS.md) 2026-04-22 entries for the full rationale.

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
