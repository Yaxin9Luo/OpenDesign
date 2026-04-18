# Vision

## Product name

**LongcatDesign** — the open-source conversational design agent from the Longcat team.

The `Longcat` prefix aligns with our team's broader ecosystem (Longcat-Next et al.) and signals that this is a serious OSS release with a brand, not a throwaway research artifact.

## The 1-line pitch

> **An open-source, terminal-first conversational design agent. Describe what you want; LongcatDesign builds and iterates it with you, exporting real HTML, PPTX, or editable PSD/SVG.**

## Why LongcatDesign exists

[Claude Design (Anthropic Labs)](https://www.anthropic.com/news/claude-design-anthropic-labs) shipped the right UX thesis — *conversational design iteration produces dramatically better artifacts than one-shot text-to-image* — but locked it inside a closed browser SaaS tied to Claude subscriptions. That's the same bet every closed AI design tool makes (Canva Magic / Figma AI / Lovart / Adobe Firefly).

We think the right UX + open-source distribution + open output formats wins a real user base:

- **Developers and researchers** who already live in the terminal and want to script / pipe / automate design workflows.
- **Self-hosting teams** who can't send IP to a cloud SaaS but still want AI-accelerated design.
- **Designers** who want the agent's output as *real editable source files* (PSD, SVG, HTML) they can push into their own pipeline — not just rasters locked in a vendor's canvas.

LongcatDesign is the terminal-first, open-source answer to "I want what Claude Design does, but I want to own the stack and the output."

## What LongcatDesign does (v1 MVP scope)

Three artifact types, produced from conversational briefs in a CLI chat shell:

| Artifact | Primary output | Secondary outputs |
|---|---|---|
| **Poster** (our current depth) | HTML preview | PSD (named pixel layers) · SVG (real `<text>` vector) · PNG |
| **Slide deck** | PPTX (python-pptx native type frames) | HTML preview · PNG thumbnails |
| **Landing page / one-pager** | **Self-contained HTML + inline CSS** | PNG screenshot |

The CLI is a conversational shell, not a one-shot command. Typical session:

```
$ longcat-design
> design a 3:4 poster for "国宝回家 公益项目"
[agent designs, composites, shows preview link]
> make the title bigger and move the stamp to the top-left
[agent calls edit_layer tools, recomposes, shows preview link]
> now give me a landing page that announces this project
[agent switches artifact_type, reuses brand palette/mood, generates HTML]
> export everything
[agent writes poster.psd, poster.svg, landing.html, deck.pptx ...]
```

## What sets us apart

### vs Claude Design (closed SaaS)
- **Open-source**: MIT-licensed code on GitHub; self-hostable end-to-end.
- **Terminal-first**: scriptable, pipeable, automatable. Fits into existing dev workflows without a browser.
- **Open output formats**: HTML / PPTX / PSD / SVG — all inspectable, all editable in standard tools.
- **Model-agnostic**: works with OpenRouter (any Claude via OR), stock Anthropic, and future local-model backends. Not tied to one subscription.

### vs Canva / Figma / Adobe Firefly
- **No subscription, no login**: clone the repo, bring your API key, run locally.
- **Your data stays yours**: no uploads to a proprietary canvas. Every artifact is a file on your disk.
- **Conversational iteration**: natural language refinement, not dropdown menus.

### vs Lovart / existing tools (Paper2Any etc.)
- **HTML as first-class output**: Lovart/Paper2Any export PPTX or rasterize to PNG. We treat structured HTML (with inline Tailwind CSS and inlined asset encoding) as a peer format. That's the format most web-native, most editable in devtools, most embeddable.
- **Real vector text, always**: including Chinese titles. No "your title got rasterized into the background" failure mode. (See [the origin story](#origin-story-the-gap-we-were-built-from) below.)
- **One repo, one CLI, three artifact types**: no SaaS sprawl; we go deep on conversational design primitives.

## Origin story (the gap we were built from)

LongcatDesign started as a research prototype (codename "Design-Agent") aimed at capturing layered-generation training trajectories for the Longcat-Next model team. Along the way we realized:

1. The **architecture** we built (Anthropic SDK + handwritten tool loop + text-free background generation + separately-rendered text layers + structured composition) produces genuinely better editable artifacts than any closed competitor's approach — especially for Chinese text, where SaaS tools rasterize titles into the image.
2. The **UX** that Claude Design validated (conversational iteration + multi-format export + brand system consistency) is the natural front-end to that architecture.
3. Shipping this as **open-source product** reaches more users than keeping it internal, and the internal tool / training-data usage stays possible as a side-channel (see [DATA-CONTRACT.md](DATA-CONTRACT.md) — the trajectory schema is preserved internally).

So we pivoted. The trajectory emission machinery is still there, it's just no longer the primary product pitch. If the Longcat-Next team wants to flip a flag and harvest trajectories from real user sessions, the infrastructure is ready.

## What we're explicitly NOT building (v1)

- **Browser / Web UI.** CLI chat shell only. We can't out-engineer Anthropic's design team on web UX and shouldn't try.
- **Multi-user / auth / billing / SaaS features.** Self-hosted single-user only.
- **Real-time collaboration.** Single-session single-user.
- **Breadth beyond 3 artifact types.** No video, no drawio, no social-post variants for v1. Depth first.
- **Training-data dataset publishing.** Trajectory schema stays; dataset publishing is out of scope for v1 product positioning.

See [ROADMAP.md](ROADMAP.md) for ordering and what moves past v1.

## Internal architecture commitments (unchanged by pivot)

These carry over from the original Design-Agent prototype and remain technical commitments:

- **Anthropic SDK + handwritten tool loop** for the agent — [DECISIONS.md](DECISIONS.md) "Anthropic SDK + handwritten tool loop, NOT LangGraph."
- **Text-free background generation** via Gemini Nano Banana Pro — text always rendered separately as editable layers.
- **Pydantic schema** as single source of truth for session state.
- **Trajectory preservation** as internal session state (undo, project resume, future training-data flip).

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full module map — most of it still applies; the v1 MVP adds conversational chat shell + HTML/PPTX renderers + artifact-type switching on top.

## Success criteria

**v1.0 launch**: LongcatDesign ships on GitHub (MIT license) with:

- Working CLI chat shell, runnable from `pip install longcat-design`
- The three artifact types produce viewable / editable artifacts
- README showcases one great example of each artifact type
- Docs cover setup, quickstart, extending with new tools, model-backend config
- A short YouTube/video walkthrough of a multi-turn design session
- At least the first external contributor's PR merged — proof that the architecture is readable and extensible

**v1.x growth** (months post-launch): GitHub stars as a directional signal, but the real signal is whether teams actually self-host LongcatDesign for internal design needs.

## The honest constraint (still relevant post-pivot)

LongcatDesign depends on:

- **Claude Opus 4.7** (via OpenRouter or Anthropic stock) for planning + critiquing
- **Gemini 3 Pro Image / Nano Banana Pro** for text-free background generation

If either provider disappears tomorrow, the architecture adapts (planner swap is ~1 line in config; image backend swap is ~100 lines in `tools/generate_background.py`). If both disappear, we'd need to ship support for local models — a plausible v2.0 work item but not on v1 critical path.

The Longcat-Next model, when it lands, is the natural long-term replacement for the planner role. Until then, we're LLM-dependent but provider-neutral.
