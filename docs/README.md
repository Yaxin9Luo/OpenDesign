# Design-Agent — Knowledge Base

This directory is the **single source of truth** for what Design-Agent is, why it exists, how it's built, and what's next. If you (or future-you, or a Longcat-Next teammate) come back to this project after a break and want to pick up cold without re-reading all of Slack, **start here**.

---

## What LongcatDesign is in 30 seconds

An **open-source, terminal-first conversational design agent**. Describe what you want (poster / slide deck / landing page); LongcatDesign builds and iterates with you via a CLI chat shell, exporting to **HTML (first-class) + PPTX + editable PSD/SVG**.

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
| [VISION.md](VISION.md) | Product pitch (LongcatDesign as OSS Claude Design alt); differentiation | First read; any time scope is being debated |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Components, files, data flow, the 7 tools, two LLM backends | Before touching code |
| [V1-MVP-PLAN.md](V1-MVP-PLAN.md) | Concrete shipping plan for v1.0 MVP launch (work breakdown + estimates) | Before starting implementation |
| [DATA-CONTRACT.md](DATA-CONTRACT.md) | Session-state / trajectory schema (internal detail post-pivot) | Before touching `schema.py` |
| [WORKFLOWS.md](WORKFLOWS.md) | Run pipeline, edit artifacts, smoke test, extending tools | Day-to-day reference |
| [DECISIONS.md](DECISIONS.md) | Design decisions log (the pivot, no LangGraph, OpenRouter, etc.) | Before reopening a settled question |
| [ROADMAP.md](ROADMAP.md) | v1.0 MVP + v1.1+ planned versions | Planning next session of work |
| [GOTCHAS.md](GOTCHAS.md) | Runtime quirks + fixes (dotenv, Gemini PNG/JPEG, OpenRouter base_url, Figma SVG) | Any time something behaves unexpectedly |
| [COMPETITORS.md](COMPETITORS.md) | Audited related projects (Paper2Any, Claude Design); differentiation analysis | When asked "but what about X?" / planning a feature already shipped elsewhere |

---

## Living artifacts (outside this KB but always relevant)

- **The plan file**: [`~/.claude/plans/v0-b-psd-svg-type-vast-sonnet.md`](~/.claude/plans/v0-b-psd-svg-type-vast-sonnet.md) — the original v0 implementation plan, kept as a snapshot for reference. The KB supersedes it.
- **The reference article**: [`design_agent_blog.pdf`](../design_agent_blog.pdf) at the repo root — Claude Opus 4.7 + Lovart 国宝回家 case. The motivating example and the gap we're closing.
- **Sample trajectories**: `out/trajectories/<run_id>.json` — every successful run dumps one. These are the actual training data.

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
