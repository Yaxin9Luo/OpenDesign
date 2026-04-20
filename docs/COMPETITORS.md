# Competitors / Related Work

What other projects are doing in our space, and where the real differentiation is. Updated as we audit new ones.

When someone asks "but what about X?" — read the relevant section here first. If they ask about a project not listed, audit it (clone, read code, verify load-bearing claims, write a section).

> **⚠️ POST-PIVOT + POST-v1.0-#7 NOTICE (2026-04-20)**
>
> After the [pivot to LongcatDesign](DECISIONS.md) (2026-04-18) and the v1.0 #7 deck shipping (2026-04-20), the audits below have partially aged out. The **factual audits** (architecture, editability mechanism, output formats) remain accurate; the **"why this doesn't kill our positioning"** sections were written under the old "trajectory-as-product" framing and the "deck renderer pending" framing — both stale now.
>
> **Updated as of 2026-04-20**:
>
> - LongcatDesign now ships full **3-artifact coverage**: poster (PSD + SVG + HTML) + landing (HTML with 6 bundled design systems) + deck (native PPTX with TextFrames). See [V1-MVP-PLAN.md](V1-MVP-PLAN.md) status line.
> - Our deck renderer (`tools/pptx_renderer.py`, v1.0 #7) now uses the **same `python-pptx` native-TextFrame approach** as Paper2Any's renderer — that was the explicit "what we should steal" in the audit below. Paper2Any's PPTX approach is now our PPTX approach.
> - Our current differentiation vs Paper2Any: we're **multi-artifact + agentic** (poster + landing + deck from one CLI + conversational iteration), they're **single-artifact + pipelined** (poster-from-paper via a 7-agent LangGraph DAG). We also have round-trip HTML editing (Path A) which they don't.
> - Our current differentiation vs Claude Design: **open-source + terminal-first + open output formats**, not tied to any subscription. The shipped v1.0 proves the thesis works.
> - **v1.1 paper2any** (our North Star, not to be confused with the unrelated Paper2Any project): drop in a paper / PDF / docx → generate matching poster / landing / deck. See [ROADMAP.md § v1.1](ROADMAP.md#v11--document-ingestion-paper2any--core). Once v1.1 ships, we'll directly compete with Paper2Any's "paper in, poster out" use case, with a wider output surface (landing + deck too) and round-trip editability.
>
> The sections below are kept as historical audits — read them for the factual audits and the "what they have over us" tables, not for positioning. The "Verdict — same lane?" lines are now: Paper2Any is partially in our lane (both OSS, both target editable-artifact-from-paper); Claude Design remains adjacent (closed SaaS vs our open-source).

---

## Paper2Any (OpenDCAI) — audited 2026-04-18

**Repo**: [github.com/OpenDCAI/Paper2Any](https://github.com/OpenDCAI/Paper2Any). Cloned to `/Users/yaxinluo/Desktop/Projects/Design-Agent/Paper2Any/`.

A 12-product SaaS suite generating "editable" academic artifacts from paper PDFs/images/text. The product directly competing with us is **Paper2Poster**.

### TL;DR — they exist, they ship, they don't compete on our axis

Paper2Any is a real product, deployed, with paying users. They market "editable" everywhere. **The mechanism is fundamentally different from ours**: their output is **PPTX**, our output is **PSD + SVG + Trajectory JSON**. They optimize for "PowerPoint user can open the file"; we optimize for "designer pipeline + future-model training data."

We are not in the same competitive lane. They serve researchers who want to ship a poster to a conference; we serve **the team training the next-gen layered-generation model**. There's overlap in the rendering, no overlap in the customer.

### Architecture (their side)

Multi-agent **LangGraph state machine**, 7 sequential agents:

```
Parser → Curator → ColorAgent → SectionTitleDesigner →
LayoutOptimizer (with Balancer) → FontAgent → Renderer
```

- Each agent is a Python class with `__call__(state: PosterState) → PosterState` — pure procedural function.
- Agents READ the LLM (multi-model: GPT-4o, Claude 3.5, Gemini 2.5 Pro, Qwen-VL — configured per-agent), but the **LLM does NOT decide which agent runs next**. The DAG is hardcoded.
- Shared mutable `PosterState: TypedDict` flows through the graph; each agent mutates it in place.
- Files: [`Paper2Any/dataflow_agent/toolkits/postertool/src/workflow/pipeline.py:31-54`](../Paper2Any/dataflow_agent/toolkits/postertool/src/workflow/pipeline.py), agent definitions in `[Paper2Any/dataflow_agent/toolkits/postertool/src/agents/](../Paper2Any/dataflow_agent/toolkits/postertool/src/agents/)`.

**Contrast with our [ARCHITECTURE.md](ARCHITECTURE.md)**: we run a single LLM (Claude Opus 4.7) in a **handwritten tool-use loop** where the LLM picks tools turn by turn. Two genuinely different paradigms — neither is universally better; ours captures reasoning as trajectory, theirs trades reasoning capture for specialization per agent.

### Their "editable" = native PPTX type frames

Verified in [`Paper2Any/dataflow_agent/toolkits/postertool/src/agents/renderer.py`](../Paper2Any/dataflow_agent/toolkits/postertool/src/agents/renderer.py):

```python
from pptx import Presentation
from pptx.util import Inches, Pt

run.text = segment['text']
run.font.name = font_family
run.font.size = base_font_size
run.font.color.rgb = self._parse_color(segment['color'])
run.font.bold = True
```

This writes **native PowerPoint type frames** — text stays as live editable text in PowerPoint, Google Slides, Keynote. Chinese text is just a string passed to python-pptx; CJK rendering is delegated to the consuming app's font engine. No rasterization. No font subsetting needed. **Designer / researcher opens the .pptx, double-clicks any text, types a new value.** Period.

This is genuinely better UX than our v0 PSD-pixel-layer approach for the "edit text in your familiar tool" use case.

### Why this doesn't kill our positioning

The cost of their approach:

1. **You're locked into the PPTX layout box model.** PowerPoint frames support text + shapes + simple gradients. They can't express things our layer graph can — arbitrary blend modes, alpha-composited layered illustrations, agent-decomposed "background image + N positioned overlays where each layer is a generated raster with explicit prompt." Posters that need real visual richness (the 国宝回家 case from our blog reference) are out of scope for python-pptx.
2. **No structured intermediate states are emitted to disk.** They have a `Trajectory` Pydantic model in [`Paper2Any/dataflow_agent/trajectory/models.py`](../Paper2Any/dataflow_agent/trajectory/models.py) (lines 16–195) with `to_sft_format()` and `to_dpo_format()` methods. **But the Paper2Poster workflow [`wf_paper2poster.py`](../Paper2Any/dataflow_agent/workflow/wf_paper2poster.py) NEVER CALLS THE EXPORTER**. We grep'd it: zero references to `trajectory`, `exporter`, `to_sft`, `to_dpo`, `save_traj`. They built training-data infrastructure, then didn't wire it into the product. The trajectory exists as a runtime workflow-state object during execution and is discarded when the run completes.
3. **The LLM doesn't reason about layout.** Layout is decided by a hand-coded `LayoutOptimizer` + `Balancer` (not by the LLM). The LLM's role is per-agent text generation (story, color choice, font sizing) — not orchestration. So *the trajectory wouldn't even capture interesting layout decisions* if they did emit it. Our planner emits a layer_graph as a structured `tool_args` blob explicitly chosen by the LLM — that IS the supervision signal Longcat-Next needs.

### What we've genuinely got over them

Three real wins, mapped to our [VISION.md](VISION.md):

| Their gap | Our position |
|---|---|
| Trajectory infrastructure built, not emitted | **Emitted by default**, every run → `out/trajectories/<run_id>.json`. 5 SFT lanes ready (see [DATA-CONTRACT.md](DATA-CONTRACT.md)). |
| LLM doesn't decide layout (hardcoded LayoutOptimizer) | LLM proposes full `layer_graph` as a structured tool call — captures layout reasoning as supervised data |
| PPTX confines visual richness to PPT primitives | NBP background + Pillow text layers + free composition — supports any poster style our planner can describe |

### What they've genuinely got over us

Don't pretend these don't exist. Real wins for them:

| Our gap | Their position |
|---|---|
| PSD/SVG editing requires Photoshop / Inkscape / Illustrator | PPTX opens in any office suite — designer-grade UX out of the box |
| Chinese text edits in v0 SVG hit font subset drift (see [GOTCHAS.md](GOTCHAS.md)) | PPTX delegates CJK rendering to consuming app's font engine — "just works" |
| Single-user CLI, no multi-tenant infrastructure | Production SaaS: FastAPI + Supabase + Docker + workers + auth + billing |
| Single LLM, single planner | Multi-model per-agent (cheaper inference for simple tasks, expensive for hard ones) |
| 12 product types? We have 1 (poster) | 12 product types (figures / posters / slides / video / drawio / rebuttals / …) |
| No paper-PDF input parser | Real paper-PDF → structured-content extraction is part of their stack |

### Verdict — same lane?

**No.** We're solving different problems even though both projects "make posters."

- **Paper2Any solves "I'm a researcher, give me a PPTX I can polish in PowerPoint and submit to my conference Friday."** Their PPTX-native pipeline is the right answer for that user.
- **We solve "I'm building Longcat-Next and need (brief → design_spec → layer_graph → composited image) tuples at training-data scale, plus a working agent that produces production-ready editable artifacts during the validation phase."** Our trajectory-first design + flexible composition is the right answer for that.

If a researcher just wants a poster, point them at Paper2Any. We are not interested in that user. Our user is the model trainer.

### What we should steal from them (if we ever need to)

If we expand into v1.x and want to broaden beyond Lovart-style design posters into academic posters specifically:

1. **PDF-paper input parser** — they have a real one in `Paper2Any/dataflow_agent/parsers/`. Worth porting if v0.6 Brand Kit grows into "structured input" more generally.
2. **PPTX as an additional output format** — we could add `python-pptx` as a third composite target (PSD + SVG + PPTX) so users who want PowerPoint editability get it. Doesn't conflict with our PSD/SVG; just another renderer. ~100 lines of code.
3. **Layout optimizer / spatial balancer pattern** — their `balancer_agent.py` is a hand-tuned post-processor for cleaning up overlaps. We currently rely on Claude to get layout right first time. A balancer pass after composite could be a v0.4 quality improvement.
4. **Their multi-model-per-agent pattern** — for cost optimization. Most of our cost is in the planner, but we could route the critic to Sonnet/Haiku to cut cost ~50% with little quality drop. (See [ROADMAP.md](ROADMAP.md) always-on backlog.)

### Things NOT to copy

- Hardcoded DAG orchestration — we deliberately chose LLM-driven tool loop because trajectory capture matters more than per-agent specialization (see [DECISIONS.md](DECISIONS.md) "Anthropic SDK + handwritten tool loop, NOT LangGraph").
- Multi-product sprawl — they have 12 sub-products and each is shallower than ours. We pick one (poster) and go deep on layered-output + training data. Premature breadth would dilute our trajectory dataset.
- PPTX-only output — would surrender the layer-graph-as-training-target lane.

---

## Claude Design (Anthropic Labs) — audited 2026-04-18

**Link**: [anthropic.com/news/claude-design-anthropic-labs](https://www.anthropic.com/news/claude-design-anthropic-labs). Closed-source SaaS; audit by article + public statements, not code.

### TL;DR — this is the user-facing product tier we might be *consumed by*, not a code reference

Claude Design is a **shipped browser-based SaaS** at `claude.ai/design`, in research preview for Claude Pro/Max/Team/Enterprise subscribers as of 2026-04-18. It generates slides, one-pagers, pitch decks, landing pages, social media assets, marketing collateral, and "code-powered prototypes with voice, video, shaders, 3D." Powered by Claude Opus 4.7. Zero public info on underlying agent architecture or training-data emission — it's pitched as a **product**, not research.

**Their editability model is "export and hand off"**: you export to Canva / PDF / PPTX / standalone HTML and edit in the target tool (their Canva partnership is explicit: "instantly become fully editable" in Canva). They do NOT expose a native layered editable format like our PSD+SVG. There's no mention of SVG, PSD, layered assets, or layer primitives anywhere in their announcement.

### Key product signals

- **Browser-only**, no CLI/API.
- **Conversational iteration**: chat UI + inline comments + direct edits + "custom sliders (made by Claude)". Group mode: "chat with Claude together in a group conversation."
- **Rich input modalities**: text + uploads (DOCX/PPTX/XLSX) + codebase reference + **web capture tool** (grabs elements from live websites).
- **Export targets**: Canva, PDF, PPTX, HTML, internal org URL. Not SVG, not PSD, not Figma.
- **Customer claims**: Brilliant — "complex pages that took 20+ prompts in other tools required 2 in Claude Design." Datadog — "rough idea to working prototype before anyone leaves the room." **Their differentiation is iteration density + speed, not primitives.**

### Things explicitly NOT in the announcement

- No mention of "editable layers" or layer architecture
- No mention of training data, fine-tuning, or layered-generation models
- No agent-architecture detail (tools / multi-agent / critique)
- No language support statement (Chinese / other scripts)
- No size / resolution limits discussed
- No public API, no SDK, no self-hosted

### Why this is NOT the same lane as us (most important)

Claude Design is **consumer + enterprise productivity**. Their user is a marketer / founder / researcher who wants to ship a deck Friday. Their UX philosophy is *"abstract away the layer architecture — just edit the result."* Their output-format strategy is *"export to whatever tool the user already lives in."*

We are **training-data pipeline + capability validation** for [Longcat-Next](VISION.md). Our user is the model trainer. Our output-format strategy is *"emit every intermediate state (design_spec, layer_graph, agent_trace, critique_loop) as structured training signal."* Our trajectory IS the product; the PSD/SVG is a side effect.

**These two sets of design pressures conflict.** Claude Design's "just chat until it looks right, edit in Canva" UX is actively incompatible with "emit every intermediate reasoning step as SFT data." You optimize for one or the other. You don't get both from a single system.

### What Claude Design can teach us (features worth borrowing without pivoting)

See `Conversational iteration` + `Multi-format export` items in [ROADMAP.md](ROADMAP.md) — several UX patterns here map cleanly onto things we already planned:

- **Conversational iteration** ≈ our v0.2 rerender command, but with a chat wrapper. Produces valuable **edit-pair trajectories** (before/after layer_graph) = bonus DPO / image-edit SFT signal.
- **Multi-format export (Canva/PPTX/HTML)** ≈ what we noted in the Paper2Any audit: add `python-pptx` as a third composite target. Claude Design's Canva partnership hints at where downstream consumption will live.
- **Upload-as-reference** ≈ our v0.6 Brand Kit PDF parsing, but generalized to arbitrary references (image, URL capture, codebase).
- **Brand-system consistency** ≈ Brand Kit at spec level — they enforce palette/typography/components at the project level, not per-artifact. Our Brand Kit plan should match this.

### What we must NOT do even if we "align"

- **Don't abandon trajectory emission** to chase their conversational UX aesthetic. The trajectory is our entire raison d'être as a Longcat-Next input pipeline. Their UX treats intermediate state as disposable; ours treats it as the asset.
- **Don't broaden to 12 product types** (slides / decks / social / prototypes / …). They have the distribution to ship breadth; we have the focus to ship depth in layered poster generation. Breadth would dilute the trajectory dataset.
- **Don't surrender native layered output** (PSD+SVG) to "just export to Canva." Canva can't consume our layer_graph structurally — that capability is precisely what Longcat-Next needs to learn.
- **Don't build a consumer browser UI** on our budget. If we ever need a UI, it's a thin shell over the CLI. Claude Design has Anthropic's engineering + design org behind it; we can't win on UX polish — and shouldn't try.

### Verdict — same lane?

**No.** Claude Design is what the *output* of a system like ours eventually powers at the consumer layer. They're a layered-cake product; we're the layered-gen data pipeline beneath it. If Anthropic ever open-sources their Claude Design agent harness or exposes a "layered output" API, audit again and revise this section.

If the user asks "should we pivot to compete with Claude Design?" the honest answer is **no, because we can't**: they have distribution + engineering budget + Anthropic brand. We can only win on the axis they're not competing on — structured layered output with full trajectory capture for training.

---

## (Future entries)

When auditing a new related project, follow this template:

```
## <Project Name> — audited <YYYY-MM-DD>

Repo: <link>. (Cloned to: <path> if locally available.)

### TL;DR — <1-line position>
### Architecture (their side)
### Their "editable" = <mechanism>
### Why this doesn't kill our positioning
### What we've genuinely got over them
### What they've genuinely got over us
### Verdict — same lane?
### What we should steal from them (if we ever need to)
### Things NOT to copy
```

Audit triggers: someone mentions a project in conversation; new release of a tool we've cited; quarterly review.

### Backlog of projects worth auditing

- **Lovart** — the SaaS the seed blog used. Closed source; would need to audit by usage rather than code reading. Their PSD-export quirk (Chinese title rasterized) is the gap we nominally exist to close — worth confirming it's still true on a current account.
- **qwen-image-layered** — Alibaba's layered image generation model. Most directly relevant model-level competitor / collaborator. Different layer because they're a model, we're an agent + data pipeline. Audit when their paper drops or weights release.
- **Adobe Firefly Layered** — if/when Adobe ships a layered generation model integrated with Photoshop.
- **Figma AI** — Figma's native AI poster gen, when it ships.
- **Canva Magic** — closed product; useful for understanding consumer UX expectations.
