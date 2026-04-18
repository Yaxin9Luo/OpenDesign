# Architecture

## One-paragraph overview

A `PipelineRunner` (in [`design_agent/runner.py`](../design_agent/runner.py)) bootstraps a per-run directory and a `ToolContext`, then hands control to a `PlannerLoop` (in [`design_agent/planner.py`](../design_agent/planner.py)) which drives Claude Opus 4.7 through a tool-use loop. The planner calls 7 tools in sequence (`propose_design_spec` → `generate_background` → `render_text_layer` × N → `composite` → `critique` → `finalize`). Every assistant turn and every tool result is appended to a structured `agent_trace`. When the planner calls `finalize`, the runner serializes everything (brief + spec + layer_graph + trace + critique loop + composition artifacts + metadata) to `out/trajectories/<run_id>.json` — that JSON is the product. The composite tool produces three artifacts: a multi-layer **PSD** (psd-tools), a self-contained **SVG** (svgwrite + fonttools subset), and a flat **preview PNG** (Pillow alpha-composite).

## Top-level data flow

```
[brief: str]
   │
   ▼
┌──────────────────────────┐
│ PipelineRunner            │  owns: per-run paths, ToolContext, trajectory I/O
│  (runner.py)              │  does NOT make decisions
└──────────────┬───────────┘
               │
               ▼
┌──────────────────────────┐
│ PlannerLoop                │  Anthropic SDK + handwritten tool-use loop
│  (planner.py)              │  model: claude-opus-4-7  (or  anthropic/claude-opus-4.7 via OpenRouter)
│  system prompt:            │  emits: AgentTraceStep × N
│   prompts/planner.md       │
└──────────────┬───────────┘
               │ tool_use blocks
               ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  TOOLS  (action space — 7 tools, all in design_agent/tools/)                  │
│                                                                              │
│  propose_design_spec  → validate planner JSON → ctx.state["design_spec"]      │
│  generate_background  → Gemini 3 Pro Image (NBP) → text-free bg PNG          │
│  render_text_layer    → Pillow → transparent RGBA PNG (one per text run)     │
│  fetch_brand_asset    → v0 stub: returns not_found                            │
│  composite            → psd-tools + svgwrite + Pillow → PSD + SVG + preview   │
│  critique             → invokes Critic (vision) → CritiqueResult              │
│  finalize             → flips ctx.state["finalized"] = True                   │
└──────────────┬───────────────────────────────────────────────────────────────┘
               │ ToolObservation per call
               ▼
┌──────────────────────────┐
│ Critic                     │  Anthropic SDK + vision input on preview.png
│  (critic.py)               │  system prompt: prompts/critic.md
│                            │  outputs: CritiqueResult (strict JSON)
└──────────────────────────┘
               │
               ▼
┌──────────────────────────┐
│ Trajectory serialization   │  runner.py builds Trajectory object,
│  (runner.py + util/io.py)  │  atomic-writes to out/trajectories/<run_id>.json
└──────────────────────────┘
```

## File map

```
Design-Agent/
├── design_agent_blog.pdf          # the seed reference article
├── README.md                      # quickstart (links to docs/)
├── pyproject.toml                 # dependencies, package metadata
├── .env / .env.example            # API keys (GEMINI_API_KEY required;
│                                  #          OPENROUTER_API_KEY OR ANTHROPIC_API_KEY)
├── .gitignore
│
├── docs/                          # ← THIS KB
│   ├── README.md                  # index + reading order
│   ├── VISION.md                  # why + end goal
│   ├── ARCHITECTURE.md            # this file
│   ├── DATA-CONTRACT.md           # Trajectory schema
│   ├── WORKFLOWS.md               # run/edit/extend
│   ├── DECISIONS.md               # design log
│   ├── ROADMAP.md                 # planned versions
│   └── GOTCHAS.md                 # runtime quirks
│
├── prompts/
│   ├── planner.md                 # system prompt for PlannerLoop
│   └── critic.md                  # system prompt for Critic
│
├── assets/
│   └── fonts/
│       ├── NotoSansSC-Bold.otf    # Chinese sans (16 MB, OFL)
│       └── NotoSerifSC-Bold.otf   # Chinese serif / "毛笔" stand-in (24 MB, OFL)
│
├── design_agent/                  # the package
│   ├── __init__.py
│   ├── cli.py                     # `python -m design_agent.cli "<brief>"`
│   ├── config.py                  # env loading + Settings (LLM backend detection)
│   ├── schema.py                  # Pydantic models — single source of truth
│   ├── runner.py                  # PipelineRunner — orchestration
│   ├── planner.py                 # PlannerLoop — tool-use loop
│   ├── critic.py                  # Critic — vision self-review
│   ├── smoke.py                   # `python -m design_agent.smoke` (no-API end-to-end)
│   │
│   ├── tools/                     # the action space
│   │   ├── __init__.py            # TOOL_SCHEMAS + TOOL_HANDLERS registry
│   │   ├── _contract.py           # ToolContext + obs_ok/obs_error helpers
│   │   ├── propose_design_spec.py
│   │   ├── generate_background.py
│   │   ├── render_text_layer.py
│   │   ├── fetch_brand_asset.py   # v0 stub
│   │   ├── composite.py           # PSD + SVG + preview
│   │   ├── critique_tool.py       # planner-invocable wrapper for Critic
│   │   └── finalize.py
│   │
│   └── util/
│       ├── __init__.py
│       ├── ids.py                 # run_id (sortable), layer_id (uuid)
│       ├── io.py                  # atomic_write_json, sha256_file, ensure_dirs
│       └── logging.py             # one-line JSON to stderr per event
│
└── out/                           # gitignored
    ├── runs/<run_id>/
    │   ├── layers/{bg_*.png, text_*.png}
    │   ├── poster.psd
    │   ├── poster.svg
    │   └── preview.png
    ├── trajectories/<run_id>.json
    └── smoke/                     # smoke-test artifacts
```

## The 7 tools (action space)

Every tool follows the same signature: `fn(args: dict, *, ctx: ToolContext) -> ToolObservation`. The `ToolObservation` is a Pydantic model with fields `{status, summary, next_actions, artifacts}` — see [DATA-CONTRACT.md](DATA-CONTRACT.md). Tools never raise to the planner; they always return an observation. Exceptions caught in `planner._invoke` become `status: "error"` observations.

| Tool | File | Purpose | Side effect |
|---|---|---|---|
| `propose_design_spec` | [`tools/propose_design_spec.py`](../design_agent/tools/propose_design_spec.py) | Validate `DesignSpec` JSON from planner; store in `ctx.state["design_spec"]`. MUST be called first. | Sets `ctx.state["design_spec"]`. |
| `generate_background` | [`tools/generate_background.py`](../design_agent/tools/generate_background.py) | Call Gemini Nano Banana Pro (`gemini-3-pro-image-preview`) for a text-free background. Auto-appends "no text" suffix. PIL re-encodes to true PNG. | Writes `layers/bg_<id>.png`; registers in `ctx.state["rendered_layers"]`. |
| `render_text_layer` | [`tools/render_text_layer.py`](../design_agent/tools/render_text_layer.py) | Render a text run to RGBA PNG with optional stroke + drop-shadow. Auto-wraps long lines (Latin on space, CJK char-by-char). Falls back to NotoSansSC-Bold for unknown fonts. | Writes `layers/text_<id>.png`; registers layer. |
| `fetch_brand_asset` | [`tools/fetch_brand_asset.py`](../design_agent/tools/fetch_brand_asset.py) | v0 stub. Always returns `status: "not_found"`. | Nothing. |
| `composite` | [`tools/composite.py`](../design_agent/tools/composite.py) | Build PSD (psd-tools, named pixel layers + `text` group), SVG (svgwrite, real `<text>` + base64 background + subsetted-WOFF2 font @font-face), and flat preview (PIL alpha_composite chain). | Writes `poster.psd`, `poster.svg`, `preview.png`; sets `ctx.state["composition"]`. |
| `critique` | [`tools/critique_tool.py`](../design_agent/tools/critique_tool.py) | Invoke `Critic.evaluate()` on the latest preview. Hard-capped at `max_critique_iters` (default 2). | Appends to `ctx.state["critique_results"]`; writes `critique_<i>.json`. |
| `finalize` | [`tools/finalize.py`](../design_agent/tools/finalize.py) | Signal "planner is done." | Sets `ctx.state["finalized"] = True`. Runner sees this after the turn and serializes the trajectory. |

The `ToolContext` (in [`tools/_contract.py`](../design_agent/tools/_contract.py)) is the per-run shared state object. It carries the `Settings`, the run/layers paths, and a mutable `state` dict that tools both read and write. This is intentionally a "blackboard" pattern — keeps tool signatures simple and lets the runner snapshot the final state for the trajectory.

## LLM backend abstraction

Two backends, auto-detected from env:

| Backend | Trigger | Model | Endpoint |
|---|---|---|---|
| **Anthropic stock** | `ANTHROPIC_API_KEY` set, no `OPENROUTER_API_KEY` | `claude-opus-4-7` | `https://api.anthropic.com/v1/messages` |
| **OpenRouter (preferred)** | `OPENROUTER_API_KEY` set | `anthropic/claude-opus-4.7` | `https://openrouter.ai/api/v1/messages` |

Either way, the **Anthropic Python SDK** is used. OpenRouter exposes an Anthropic-compatible `/messages` endpoint, so the same `client.messages.create(..., tools=[...])` call works with just a `base_url` swap. See [GOTCHAS.md](GOTCHAS.md) for the `/v1` URL trap.

The selection logic lives in [`config.py:load_settings()`](../design_agent/config.py). When `OPENROUTER_API_KEY` is set, it forces `base_url=https://openrouter.ai/api` (NOT `/api/v1` — the SDK appends `/v1/messages` itself) and overrides any stray `ANTHROPIC_BASE_URL` from the shell environment.

Per-model overrides are available via `PLANNER_MODEL` and `CRITIC_MODEL` env vars if you want to test a different model.

## Trace and trajectory ownership

The `agent_trace: list[AgentTraceStep]` is owned by the **PlannerLoop** instance and accumulated turn-by-turn. The `critique_loop: list[CritiqueResult]` lives in `ctx.state["critique_results"]` and is appended by the `critique` tool. The runner stitches both together with the rest of the data into a `Trajectory` object only at finalization.

This split is deliberate: it keeps the planner's working context lean (planner doesn't have to pass trace/critique state in every tool call), and it gives the runner a clean point to add metadata (token totals, wall time, cost estimate) that the planner shouldn't be aware of. See [DECISIONS.md](DECISIONS.md) for the rationale.

## Critique loop

The `critique` tool is **planner-invocable** — the planner decides whether and when to call it. The planner is instructed (via `prompts/planner.md`) to call it once after the first composite and use it at most twice. Hard caps in [`config.py`](../design_agent/config.py): `max_critique_iters = 2`.

The critic itself ([`critic.py`](../design_agent/critic.py)) downscales the preview to ≤1024px long edge before sending (to respect Anthropic's vision input cap of 5 MB / 8000×8000), encodes as JPEG, and asks the model to output a strict JSON `CritiqueResult` matching the schema. Parse failures fall back to a `verdict: "fail"` so the pipeline still ends cleanly.

If `verdict: "revise"` and iter < max, the planner is expected to adjust text layers and re-call `composite` (NOT `generate_background`). The `prompts/critic.md` rubric explicitly forbids "regenerate background" suggestions unless the issue is a `blocker` on the background itself.

## Smoke test (no-API)

`design_agent/smoke.py` is a 6-step end-to-end check that hits no LLM and no Gemini API:

1. All modules import
2. Tool registry has 7 tools with valid JSON Schema
3. `Trajectory` Pydantic model round-trips
4. Both Noto fonts load (and are real OTF, not corrupted)
5. `composite` runs against a stub solid-color background + 2 real text layers (rendered via Pillow)
6. The output SVG contains real `<text>` elements (proves text wasn't rasterized) + an `@font-face` block (proves font subsetting worked)

Run with `python -m design_agent.smoke`. Use this whenever you change any of: schema, tool contract, composite, font handling, or the registry. It catches dependency / wiring regressions in <10 seconds and ~zero $.

## Performance baselines (reference)

Measured on real OpenRouter runs with `claude-opus-4-7` planner + critic and `gemini-3-pro-image-preview` 2K bg. See [DATA-CONTRACT.md](DATA-CONTRACT.md) for trajectory contents.

| Brief complexity | Layers | Trace steps | Wall time | Cost (est.) | Notes |
|---|---|---|---|---|---|
| 国宝回家 主视觉 (5 layers, 4 text) | 5 | 27 | 100 s | $1.41 | bg + title + subtitle + tagline + stamp |
| CVPR academic poster (18 layers, 17 text) | 18 | 55 | 196 s | $2.49 | 4-section grid + header + footer; ~2.2K chars |

Cost scales sub-linearly with layer count (most cost is in planner reasoning and the one NBP call).
