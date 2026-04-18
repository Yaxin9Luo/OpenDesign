# Architecture

## One-paragraph overview

A `ChatREPL` (in [`longcat_design/chat.py`](../longcat_design/chat.py)) manages the outer conversation loop; each non-slash user message invokes a `PipelineRunner` (in [`longcat_design/runner.py`](../longcat_design/runner.py)) which bootstraps a per-run directory and a `ToolContext`, then hands control to a `PlannerLoop` (in [`longcat_design/planner.py`](../longcat_design/planner.py)) driving Claude Opus 4.7 through a tool-use loop. The planner calls 8 tools (`switch_artifact_type` → `propose_design_spec` → `generate_background` → `render_text_layer` × N → `composite` → `critique` → `finalize`). Every assistant turn and every tool result is appended to a structured `agent_trace`. When the planner calls `finalize`, the runner serializes the `Trajectory` (brief + spec + layer_graph + trace + critique loop + composition artifacts + metadata) to `out/trajectories/<run_id>.json`; the chat loop appends a lightweight `TrajectoryRef` to the `ChatSession` and persists that to `sessions/<id>.json`. The composite tool produces three artifacts: a multi-layer **PSD** (psd-tools), a self-contained **SVG** (svgwrite + fonttools subset), and a flat **preview PNG** (Pillow alpha-composite); v1.0 adds **HTML** and **PPTX** as first-class output formats per artifact type (pending, see ROADMAP).

## Top-level data flow

```
┌──────────────────────────┐
│ ChatREPL                   │  readline-backed loop
│  (chat.py)                 │  8 slash commands (:help / :save / :load / :new /
│                            │   :list / :history / :tokens / :export / :exit)
│                            │  persists ChatSession → sessions/<id>.json
└──────────────┬───────────┘
               │ non-slash input (brief, possibly prefixed with prior-artifact
               │ context when session has trajectories)
               ▼
┌──────────────────────────┐
│ PipelineRunner            │  owns: per-run paths, ToolContext, Trajectory I/O
│  (runner.py)              │  called once per user turn — does NOT make decisions
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
│  TOOLS  (action space — 8 tools, all in longcat_design/tools/)                │
│                                                                              │
│  switch_artifact_type → poster/deck/landing declaration → ctx.state           │
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
└──────────────┬───────────┘
               │
               ▼
┌──────────────────────────┐
│ Trajectory serialization   │  runner.py builds Trajectory object,
│  (runner.py + util/io.py)  │  atomic-writes to out/trajectories/<run_id>.json
└──────────────┬───────────┘
               │
               ▼  (back in chat.py)
┌──────────────────────────┐
│ ChatSession update         │  chat.py appends TrajectoryRef (run_id, paths,
│  (session.py + chat.py)    │  verdict, cost, wall_s) to session.trajectories,
│                            │  appends user/assistant ChatMessages, writes
│                            │  sessions/<id>.json atomically
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
├── longcat_design/                # the package
│   ├── __init__.py
│   ├── cli.py                     # argparse subparsers: `chat` (default), `run` (one-shot)
│   ├── chat.py                    # ChatREPL — multi-turn REPL, 8 slash commands, session I/O
│   ├── session.py                 # ChatSession / ChatMessage / TrajectoryRef + save/load/list
│   ├── config.py                  # env loading + Settings (LLM backend detection)
│   ├── schema.py                  # Pydantic models — single source of truth (incl. ArtifactType)
│   ├── runner.py                  # PipelineRunner — per-turn orchestration
│   ├── planner.py                 # PlannerLoop — tool-use loop
│   ├── critic.py                  # Critic — vision self-review
│   ├── smoke.py                   # `python -m longcat_design.smoke` (7/7 no-API checks)
│   │
│   ├── tools/                     # the action space (8 tools)
│   │   ├── __init__.py            # TOOL_SCHEMAS + TOOL_HANDLERS registry
│   │   ├── _contract.py           # ToolContext + obs_ok/obs_error helpers
│   │   ├── switch_artifact_type.py # poster/deck/landing declaration (NEW in v1.0 #3)
│   │   ├── propose_design_spec.py
│   │   ├── generate_background.py
│   │   ├── render_text_layer.py
│   │   ├── fetch_brand_asset.py   # v0 stub
│   │   ├── composite.py           # PSD + SVG + preview (HTML + PPTX pending)
│   │   ├── critique_tool.py       # planner-invocable wrapper for Critic
│   │   └── finalize.py
│   │
│   └── util/
│       ├── __init__.py
│       ├── ids.py                 # run_id (sortable), layer_id (uuid)
│       ├── io.py                  # atomic_write_json, sha256_file, ensure_dirs
│       └── logging.py             # one-line JSON to stderr per event
│
├── sessions/                      # gitignored — chat session persistence
│   └── session_<YYYYMMDD-HHMMSS>_<shortuuid>.json
│
└── out/                           # gitignored — artifact outputs
    ├── runs/<run_id>/
    │   ├── layers/{bg_*.png, text_*.png}
    │   ├── poster.psd
    │   ├── poster.svg
    │   └── preview.png
    ├── trajectories/<run_id>.json
    └── smoke/                     # smoke-test artifacts
```

## Chat layer (v1.0 #4)

The chat layer wraps the per-run `PipelineRunner` inside a multi-turn loop:

- **`ChatSession`** ([session.py](../longcat_design/session.py)): outer container. Holds `session_id`, `message_history: list[ChatMessage]`, `trajectories: list[TrajectoryRef]`, `current_artifact_type`. Persists to `sessions/<session_id>.json`.
- **`ChatMessage`**: single turn (`role ∈ {user, assistant, system}` + content + optional `trajectory_id` linking an assistant message to the Trajectory it produced).
- **`TrajectoryRef`**: lightweight pointer from session → the full Trajectory JSON on disk. Holds run_id, artifact paths, verdict, cost, wall_s — enough to display in `:history` / `:tokens` without loading the full ~50KB Trajectory.

The chat REPL (`chat.py`) sits above `PipelineRunner`:

1. User types a line. If it starts with `:`, dispatch to slash-command handler (`_cmd_help`, `_cmd_save`, etc.). Otherwise it's a brief.
2. `_build_contextual_brief(user_text, session)` prefixes the user's text with a summary of the most-recent trajectory if one exists. The planner's "revision vs new-artifact" section of the prompt steers it toward either re-rendering text layers or calling `switch_artifact_type` + fresh `propose_design_spec`.
3. `PipelineRunner.run(contextual_brief)` executes. Produces a `Trajectory` + trajectory.json.
4. `_trajectory_to_ref(traj)` creates a `TrajectoryRef`. Appends to `session.trajectories`. Appends user + assistant `ChatMessage` to `session.message_history`. Saves session JSON.
5. `_display_turn_result()` prints artifact paths + session totals.

The chat layer does NOT touch tool internals or the planner loop — it just wraps per-turn execution. `PipelineRunner`, `PlannerLoop`, `Critic`, and the 8 tools are unchanged by chat mode.

Legacy one-shot `longcat-design run "<brief>"` bypasses `chat.py` entirely and invokes `PipelineRunner.run()` directly. Backward compat with the v0 CLI behavior, minus the outer ChatSession.

## The 8 tools (action space)

Every tool follows the same signature: `fn(args: dict, *, ctx: ToolContext) -> ToolObservation`. The `ToolObservation` is a Pydantic model with fields `{status, summary, next_actions, artifacts}` — see [DATA-CONTRACT.md](DATA-CONTRACT.md). Tools never raise to the planner; they always return an observation. Exceptions caught in `planner._invoke` become `status: "error"` observations.

| Tool | File | Purpose | Side effect |
|---|---|---|---|
| `switch_artifact_type` | [`tools/switch_artifact_type.py`](../longcat_design/tools/switch_artifact_type.py) | Declare `poster` \| `deck` \| `landing`. MUST be first tool for any new artifact (not counting re-affirm). Writes to `ctx.state["artifact_type"]` — drives renderer selection + acts as fallback default if `propose_design_spec` omits the field. | Sets `ctx.state["artifact_type"]`. Planner emits `AgentTraceStep(type="artifact_switch")`. |
| `propose_design_spec` | [`tools/propose_design_spec.py`](../longcat_design/tools/propose_design_spec.py) | Validate `DesignSpec` JSON from planner; store in `ctx.state["design_spec"]`. Falls back to `ctx.state["artifact_type"]` when spec omits `artifact_type` field. | Sets `ctx.state["design_spec"]`. |
| `generate_background` | [`tools/generate_background.py`](../longcat_design/tools/generate_background.py) | Call Gemini Nano Banana Pro (`gemini-3-pro-image-preview`) for a text-free background. Auto-appends "no text" suffix. PIL re-encodes to true PNG. | Writes `layers/bg_<id>.png`; registers in `ctx.state["rendered_layers"]`. |
| `render_text_layer` | [`tools/render_text_layer.py`](../longcat_design/tools/render_text_layer.py) | Render a text run to RGBA PNG with optional stroke + drop-shadow. Auto-wraps long lines (Latin on space, CJK char-by-char). Falls back to NotoSansSC-Bold for unknown fonts. | Writes `layers/text_<id>.png`; registers layer. |
| `fetch_brand_asset` | [`tools/fetch_brand_asset.py`](../longcat_design/tools/fetch_brand_asset.py) | v0 stub. Always returns `status: "not_found"`. | Nothing. |
| `composite` | [`tools/composite.py`](../longcat_design/tools/composite.py) | Build PSD (psd-tools, named pixel layers + `text` group), SVG (svgwrite, real `<text>` + base64 background + subsetted-WOFF2 font @font-face), and flat preview (PIL alpha_composite chain). HTML + PPTX outputs pending v1.0 #6/#7. | Writes `poster.psd`, `poster.svg`, `preview.png`; sets `ctx.state["composition"]`. |
| `critique` | [`tools/critique_tool.py`](../longcat_design/tools/critique_tool.py) | Invoke `Critic.evaluate()` on the latest preview. Hard-capped at `max_critique_iters` (default 2). | Appends to `ctx.state["critique_results"]`; writes `critique_<i>.json`. |
| `finalize` | [`tools/finalize.py`](../longcat_design/tools/finalize.py) | Signal "planner is done." | Sets `ctx.state["finalized"] = True`. Runner sees this after the turn and serializes the trajectory. |

The `ToolContext` (in [`tools/_contract.py`](../longcat_design/tools/_contract.py)) is the per-run shared state object. It carries the `Settings`, the run/layers paths, and a mutable `state` dict that tools both read and write. This is intentionally a "blackboard" pattern — keeps tool signatures simple and lets the runner snapshot the final state for the trajectory. In chat mode, each turn gets a FRESH ToolContext — inter-turn state lives in `ChatSession`, not `ToolContext`.

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

| Brief complexity | Layers | Trace steps | Wall time | Cost (est.) | Critique | Notes |
|---|---|---|---|---|---|---|
| 国宝回家 主视觉 (5 layers, 4 text) | 5 | 27 | 100 s | $1.41 | pass 0.86 (1 iter) | bg + title + subtitle + tagline + stamp |
| CVPR academic poster (18 layers, 17 text) | 18 | 55 | 196 s | $2.49 | pass 0.86 (1 iter) | 4-section grid + header + footer; ~2.2K chars |
| LongcatDesign 发布海报 (5 layers, 4 text) | 5 | 52 | 297 s | $3.74 | revise 0.78 → pass 0.82 (2 iter) | 东方赛博 concept; 3 NBP retries (safety filter + composition) |

Cost scales sub-linearly with layer count (most cost is in planner reasoning and the one NBP call). Second-critique-iteration runs cost ~1.5-2× vs single-pass runs of similar layer count, driven by token accumulation across retry + re-render + re-critique turns.
