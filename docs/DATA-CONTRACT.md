# Data Contract — Trajectory schema (v2)

> **⚠️ v2 REWRITE (2026-04-22)** — `Trajectory` is replaced by **`DistillTrajectory`**, a lean training-data-only format. NO backward compat with v0/v1 trajectories — old JSON files are deleted, not migrated. See [DECISIONS.md § 2026-04-22](DECISIONS.md) for rationale.

The single source of truth is [`open_design/schema.py`](../open_design/schema.py). This doc explains the *meaning* of each field and the downstream training tasks it supports. **If this doc disagrees with `schema.py`, the code wins — fix the doc.**

---

## What changed in v2

The legacy `Trajectory` was a **product/debug + training data + demo** hybrid. Per the audit (real 144 KB v1 trajectory):
- ~40% was `design_spec` + `layer_graph` (engineering state, **already** present inside the planner's `propose_design_spec` tool_call args)
- ~13% was `next_actions` (hint prose that, in RL, would cause shortcut learning at deploy time)
- ~17% was descriptive `summary` strings (log noise)
- ~7% was `composition.{psd,svg,preview}_path` (host-machine paths)

v2 strips all of the above. Trajectory is now **purely model decisions + lean tool results + episode reward**, suitable as-is for mid-training SFT and RL post-training. A real v2 trajectory of equivalent complexity is ~40 KB (70% smaller than v1).

The product-side artifacts (HTML / PSD / PPTX / preview) still live on disk under `out/runs/<run_id>/` for inspection — they're just **not referenced from the trajectory JSON**.

---

## Top-level shape

```python
class DistillTrajectory(BaseModel):
    run_id: str                              # sortable: YYYYMMDD-HHMMSS-shortuuid
    brief: str                               # the literal user input
    agent_trace: list[AgentTraceStep]        # model decisions + lean tool results
    final_reward: float | None               # = critique score on pass; None on abort/max_turns
    terminal_status: Literal[                # episode outcome label for offline RL
        "pass", "revise", "fail", "max_turns", "abort",
    ]
    metadata: TrainingMetadata               # model IDs, token counts, thinking config
```

**Deliberately absent**: `design_spec`, `layer_graph`, `composition`, `critique_loop`, `created_at`, file paths. design_spec is recoverable from the latest `propose_design_spec` tool_call args; layer info from rendering tool_calls; critique results from the `critique` tool_result payload; product files from `out/runs/<run_id>/`.

## Chat session outer shape (v1.0 #4)

Each chat REPL session wraps N trajectories under a single `ChatSession`:

```python
class ChatSession(BaseModel):
    session_id: str                            # "session_YYYYMMDD-HHMMSS_<shortuuid>"
    created_at: datetime
    updated_at: datetime
    current_artifact_type: ArtifactType        # most recent artifact declared
    message_history: list[ChatMessage]         # full user↔assistant turn log
    trajectories: list[TrajectoryRef]          # light refs to Trajectory JSONs on disk
    metadata: dict
```

Persists to `sessions/<session_id>.json` (gitignored).

```python
class ChatMessage(BaseModel):
    role: Literal["user", "assistant", "system"]
    content: str                               # user brief, or assistant's 1-line summary
    timestamp: datetime
    trajectory_id: str | None                  # on assistant msgs that produced an artifact

class TrajectoryRef(BaseModel):
    run_id: str                                # matches Trajectory.run_id
    artifact_type: ArtifactType
    created_at: datetime
    trajectory_path: str                       # absolute path to <run_id>.json
    preview_path: str | None                   # POSTER + LANDING: flat; DECK: grid thumb
    psd_path: str | None                       # POSTER only
    svg_path: str | None                       # POSTER only
    html_path: str | None                      # POSTER (poster.html) + LANDING (index.html)
    pptx_path: str | None                      # DECK only (v1.0 #7)
    n_layers: int
    verdict: Literal["pass","revise","fail"] | None
    score: float | None
    cost_usd: float
    wall_s: float
```

**Why separate `ChatSession` from `Trajectory`?** Trajectories are the per-artifact unit of work (one brief → one generation → one trajectory JSON). A chat session may produce many artifacts across turns. The session file stores only lightweight refs + conversational metadata (≤10KB typical) and links to full Trajectory JSONs elsewhere. This keeps session JSON cheap to read during `:history`/`:list` operations and avoids re-parsing the entire artifact trace when the user just wants to see "what did I make in this session."

---

## ArtifactType enum (v1.0 #3)

```python
class ArtifactType(str, Enum):
    POSTER = "poster"       # absolutely-positioned layers over text-free NBP background
                            # → PSD + SVG + HTML + preview.png
    DECK = "deck"           # N slides, PPTX-native editability
                            # → deck.pptx (live TextFrames) + slides/*.png + grid preview.png
                            # (v1.0 #7 — shipped 2026-04-20)
    LANDING = "landing"     # self-contained HTML one-pager, flow layout, 6 bundled design systems
                            # → index.html + preview.png
                            # (v1.0 #8 — shipped 2026-04-19; v1.0 #8.5 design systems; v1.0 #8.75 imagery)
```

Drives renderer selection in `composite`, fills fallback default when `propose_design_spec` omits `artifact_type`. Set by `switch_artifact_type` tool; read from `ctx.state["artifact_type"]`.

## DesignSpec — the planner's blueprint

```python
class DesignSpec(BaseModel):
    brief: str                                 # echoed from input for searchability
    artifact_type: ArtifactType = POSTER       # v1.0 #3; default=POSTER
    canvas: dict                               # {w_px, h_px, dpi, aspect_ratio, color_mode}
    palette: list[str]                         # hex colors, ordered by importance
    typography: dict                           # {title_font, subtitle_font, stamp_font, ...}
    mood: list[str]                            # ["oriental epic", "dignified", ...]
    composition_notes: str                     # planner's free-form layout rationale
                                               # (for DECK: carries the style-prefix for coherent NBP imagery)
    layer_graph: list[LayerNode]               # per-artifact-type structure:
                                               #   POSTER  → flat list of text/background layers (plan)
                                               #   LANDING → nested: section[] → children (text/image)
                                               #   DECK    → nested: slide[]   → children (text/image/background)
    references: list[str]                      # optional: URIs/hex inspiration
    design_system: DesignSystem | None = None  # LANDING only — picks 1 of 6 bundled styles (v1.0 #8.5)

class DesignSystem(BaseModel):
    style: Literal["minimalist","editorial","neubrutalism",
                   "glassmorphism","claymorphism","liquid-glass"] = "minimalist"
    accent_color: str | None = None            # overrides --ld-accent CSS token
    font_pairing: str | None = None
```

The skeleton `layer_graph` here describes the *plan*. For POSTER the actual `src_path` for each layer is filled in by later tool calls (`generate_background`, `render_text_layer`). For LANDING + DECK the planner declares image / background children with `src_path: null`, separately calls `generate_image`, and a hydration helper (`_hydrate_landing_image_srcs` / `_hydrate_deck_image_srcs` in composite.py) copies `src_path` from `rendered_layers` onto matching children before renderer writes.

**Why two `layer_graph` slots (one in DesignSpec, one at root)?** The DesignSpec one is the *plan* (what the planner intended). The root one is the *result* (what actually got rendered, possibly after critique revisions). For POSTER the root version is materialised from `rendered_layers` blackboard. For LANDING + DECK the root version is copied directly from `spec.layer_graph` — the nested section/slide tree IS the authoritative structural record. Comparing them is a useful SFT signal — see DPO lane below.

---

## LayerNode — polymorphic per-layer descriptor

```python
class LayerNode(BaseModel):
    layer_id: str                              # uuid-suffixed, stable across rerenders
    name: str                                  # semantic: "title" | "hero" | "slide_01" | ...
    kind: Literal[
        "background",    # POSTER full-canvas raster or DECK slide bg
        "text",          # text run (rasterized for POSTER, native for LANDING + DECK)
        "brand_asset",   # v1 stub
        "group",         # (unused in v1.0)
        "section",       # LANDING section container (v1.0 #8) — children = text/image
        "image",         # inline NBP image inside a LANDING section or DECK slide (v1.0 #8.75 + #7)
        "slide",         # DECK slide container (v1.0 #7) — children = text/image/background
    ]
    z_index: int                               # render order (0 = bottom)
    bbox: SafeZone | None                      # POSTER + DECK: pixel coords required
                                               # LANDING (kind=section/text): None (flow layout)

    # text-only fields
    text: str | None
    font_family: str | None
    font_size_px: int | None
    align: Literal["left", "center", "right"] | None
    effects: TextEffect | None                 # {stroke, shadow, fill}

    # image / background-only fields
    prompt: str | None                         # the literal NBP prompt sent (for image-gen SFT)
    aspect_ratio: str | None                   # "3:4", "16:9", "1:1", ...
    image_size: str | None                     # "1K" | "2K"

    # any
    src_path: str | None                       # filled by tool execution (rasterizer or hydration)
    children: list["LayerNode"]                # nested: section.children (landing), slide.children (deck)
```

Polymorphism by `kind` is intentional — keeps the schema flat and SFT-friendly. A future model trained on `(design_spec → layer_graph)` learns to emit one of N kind values per layer and to pick the right nesting pattern per artifact type.

**Nesting patterns by artifact type**:
- **POSTER**: flat list of layers (background + texts) — no children
- **LANDING**: `section` nodes at top level, each with `children: [text..., image...]`
- **DECK**: `slide` nodes at top level, each with `children: [background?, text..., image...]`

---

## AgentTraceStep — the byte-exact replayable turn log (v2)

```python
StepType = Literal[
    "input",        # user brief — exactly one per trajectory
    "reasoning",    # extended-thinking blocks (planner OR critic)
    "tool_call",    # planner emits a tool_use block
    "tool_result",  # environment returns a ToolResultRecord (lean)
    "finalize",     # terminal marker
]

class AgentTraceStep(BaseModel):
    step_idx: int
    actor: Literal["user", "planner", "tool", "critic"]
    type: StepType
    # Only on tool_call:
    tool_use_id: str | None
    tool_name: str | None
    tool_args: dict | None                       # full input as the model emitted
    # Only on tool_result:
    tool_result: ToolResultRecord | None
    # Only on reasoning:
    thinking_blocks: list[ThinkingBlockRecord] | None
    # Only on input / finalize:
    text: str | None
    # On assistant-emitting steps:
    model: str | None
    stop_reason: str | None
    usage: dict[str, int] | None  # {input, output, cache_read, cache_create}
```

**Removed in v2** (recoverable elsewhere):
- `thought` step type → planner's between-tool prose isn't real CoT; the actual reasoning is in `reasoning` blocks. Discarded.
- `artifact_switch` step type → recoverable from `switch_artifact_type` tool_call.
- `design_spec` step type → recoverable from `propose_design_spec` tool_args.
- `critique` step type → full result lives in `critique` tool_result.payload.
- `timestamp` field → not needed for distillation.
- `spec_snapshot` field → duplicate of tool_args.
- `observation` field → replaced by lean `tool_result`.
- `input_tokens`/`output_tokens`/`cache_*` separate fields → merged into `usage` dict.

## ToolResultRecord — lean tool result (v2)

```python
class ToolResultRecord(BaseModel):
    status: Literal["ok", "error"]
    error_message: str | None = None      # full text on error, NEVER truncated
    error_category: ErrorCategory | None  # validation / safety_filter / api /
                                          # timeout / not_found / unsupported_format
                                          # / parse_error / unknown
    payload: dict[str, Any]               # success state OR diagnostic
```

**Per-tool payload shape** (success):

| Tool | payload |
|---|---|
| `switch_artifact_type` | `{type, switched}` |
| `propose_design_spec` | `{artifact_type, n_layers, canvas, is_revision}` |
| `generate_background` / `generate_image` | `{layer_id, sha256, width, height}` |
| `render_text_layer` | `{layer_id, sha256}` (+ optional `font_fallback`) |
| `edit_layer` | `{layer_id, sha256, fields_changed}` |
| `composite` | `{artifact_type, preview_sha256, psd_sha256?, svg_sha256?, html_sha256?, pptx_sha256?, n_layers, canvas, text_overlap_warnings, xref_misses}` |
| `critique` | full `CritiqueResult.model_dump()` — `{iteration, verdict, score, issues, rationale}` |
| `ingest_document` | `{n_files, n_figures, n_tables, files, figures, tables}` (figures = list of `{layer_id, source_page, image_size, caption, sha256}`) |
| `finalize` | `{}` |

**Why no `next_actions` / `summary` / `artifacts`**: every tool used to emit hint prose ("call X next") and a descriptive summary string. In SFT this works as teacher forcing, but at RL deploy time the policy would have learned to listen to hints rather than the workflow. The hints are removed *at the tool level* (not just stripped from JSON) so train↔deploy distribution stays identical. The workflow contract in `prompts/planner.md` is the single source of "what to do next."

## Legacy doc continues below (the runtime models DesignSpec / LayerNode / CritiqueResult / CompositionArtifacts still exist in schema.py for use by tools and the critic — they just don't appear in the trajectory JSON anymore)

## Legacy AgentTraceStep — pre-v2 reference

```python
class AgentTraceStep(BaseModel):
    step_idx: int                              # monotonic, 1-indexed
    timestamp: datetime
    actor: Literal["user", "planner", "tool", "critic", "system"]
    type: Literal["input", "thought", "tool_call", "tool_result",
                  "design_spec", "critique", "finalize",
                  "artifact_switch"]            # v1.0 #3: emitted on switch_artifact_type

    tool_use_id: str | None                    # pairs tool_call ↔ tool_result (Anthropic format)
    tool_name: str | None
    tool_args: dict | None
    observation: ToolObservation | None        # filled on tool_result entries

    text: str | None                           # planner thinking / critic reasoning
    spec_snapshot: DesignSpec | None           # only on type="design_spec"

    input_tokens: int | None
    output_tokens: int | None
    model: str | None                          # "claude-opus-4-7" or "anthropic/claude-opus-4.7"
```

**Pairing rule**: every `type: "tool_call"` step has a matching `type: "tool_result"` step with the same `tool_use_id`. This makes SFT replay trivial: load the trace, replay assistant→tool→assistant turns verbatim, the model sees exactly what the planner saw.

**Step type inventory** (real distribution from a 17-text-layer poster run with 1 critique):

```
input         : 1     # the brief
thought       : 4     # planner free-form text between tool calls
tool_call     : 22    # one per planner action
tool_result   : 22    # paired
design_spec   : 1     # spec_snapshot recorded after propose_design_spec succeeds
critique      : 1     # one per critique iteration
finalize      : 0     # implicit (the finalize tool result + runner write)
─────────
total         : 51-55  # depends on whether planner re-rendered any layers
```

**Why `design_spec` and `critique` get their own step types** even though they're triggered by tool calls: it lets a downstream extractor pick out "give me only the spec snapshots" with `[s for s in trace if s.type == "design_spec"]` without parsing tool_args. Cheap, queryable.

---

## CritiqueResult — the self-review

```python
class CritiqueResult(BaseModel):
    iteration: int                             # 1-indexed
    verdict: Literal["pass", "revise", "fail"]
    score: float                               # 0..1, weighted rubric (see prompts/critic.md)
    issues: list[CritiqueIssue]
    rationale: str                             # 2-4 sentence overall justification

class CritiqueIssue(BaseModel):
    severity: Literal["blocker", "major", "minor"]
    layer_id: str | None                       # null = whole-artifact issue
    category: Literal["typography", "composition", "brand",
                      "legibility", "cultural", "artifact",
                      "copy", "content"]        # v1.0 #8.5-fix adds copy/content for LANDING + DECK
    description: str
    suggested_fix: str                         # actionable, references concrete fields/values
```

`pass` requires `score ≥ 0.75` AND zero blockers. `revise` only allowed while `iteration < max_iters`; otherwise forced to `fail`. The runner (`config.max_critique_iters = 2`) caps the loop to prevent infinite revision.

`suggested_fix` must be achievable by re-calling a tool the planner has (POSTER: `edit_layer` or `render_text_layer` with different args, or full `propose_design_spec` re-issue; LANDING: re-issue `propose_design_spec` with section-tree edits; DECK: re-issue `propose_design_spec` with slide-tree edits). Each critic rubric forbids fixes outside its tool vocabulary.

---

## CompositionArtifacts — what got written to disk

```python
class CompositionArtifacts(BaseModel):
    psd_path: str | None = None                # POSTER only (multi-layer Photoshop, named pixel layers)
    svg_path: str | None = None                # POSTER only (self-contained SVG + embedded fonts)
    html_path: str | None = None               # POSTER (poster.html) + LANDING (index.html)
    pptx_path: str | None = None               # DECK only (native PowerPoint TextFrames) — v1.0 #7
    preview_path: str | None = None            # flat PNG (POSTER + LANDING) or grid thumb (DECK)
    layer_manifest: list[dict] = []            # simplified mirror of layer_graph
```

All paths are `Optional` because each artifact type produces only a subset. `layer_manifest` is a simplified mirror of the `layer_graph` — useful for downstream tools that don't want to parse the full polymorphic schema (e.g., a Figma plugin that just needs name/bbox/png to recreate layers, or `apply-edits` reading just the layer shape to verify a round-trip).

---

## Metadata — provenance & accounting

```python
metadata: dict[str, Any] = {
    "planner_model": "anthropic/claude-opus-4.7",       # or "claude-opus-4-7"
    "critic_model":  "anthropic/claude-opus-4.7",
    "image_model":   "gemini-3-pro-image-preview",
    "total_input_tokens":  60981,
    "total_output_tokens": 3932,
    "estimated_cost_usd":  2.4925,             # rough — uses stock Anthropic pricing
    "wall_time_s": 195.73,
    "max_critique_iters": 2,
    "max_planner_turns":  30,
    "finalize_notes": "<planner's one-line summary>",
    "version": "v0",                           # bump when schema changes
}
```

**Cost is an estimate**, not a reading. OpenRouter's actual `usage.cost` field per response is not currently aggregated — adding that is a v0.1 polish. The estimator in [`runner._estimate_cost`](../design_agent/runner.py) uses Anthropic stock pricing as a worst case.

**Bump `version`** when you add/remove/rename schema fields, so downstream loaders can branch on it.

---

## The 5 SFT extraction lanes

A single trajectory yields 5 distinct training-pair types. The right way to think about this: the trajectory is a *graph* of related artifacts; each lane projects out a particular `(input, output)` slice for a particular training task.

### Lane 1 — Planner SFT

```
input:  trajectory.brief
output: trajectory.design_spec
```

Trains a model to go from a one-line brief to a structured plan. Useful when the model doesn't need to also draw — just plan. Good for cheap reasoning models or as a warm-start for the layered-gen lane.

### Lane 2 — Layered-gen SFT (Longcat-Next core target)

```
input:  trajectory.design_spec
output: trajectory.layer_graph                 # the post-execution one
```

Trains a model to go from a design plan to a fully-fleshed layer graph (with prompts, fonts, sizes, positions, src_paths). **This is the lane Longcat-Next is being designed around.**

### Lane 3 — Image-gen SFT

```
for L in trajectory.layer_graph if L.kind == "background":
    input:  L.prompt
    output: image at L.src_path
```

Per-layer image generation. Each background layer gives one (text-prompt, image) pair where the prompt is *guaranteed* to result in a text-free image (because we hard-enforced that in the tool). Useful for fine-tuning a text-to-image model on the "no text in output" objective.

### Lane 4 — Reasoning / CoT

```
input:  full agent_trace (sliced into turn-windows)
output: next planner action
```

Trains a model on the full reasoning chain — text thoughts + tool_use + tool_result interleaved. The `tool_use_id` pairing makes turn-by-turn replay trivial. Good for long-horizon tool-use models.

### Lane 5 — DPO / preference

```
for c in trajectory.critique_loop:
    if c.verdict == "revise":
        rejected: layer_graph BEFORE c             # stale snapshot from prior trace
        chosen:   layer_graph AFTER c              # next snapshot
```

Critique-driven preference pairs. Currently rare (only emitted when a critique iteration leads to a revise→pass flow). To get more of these, you can either:

- **Lower the critic threshold** to force more revise verdicts (set `score ≥ 0.85` for pass).
- **Use the rerender command** (when v0.1 lands — see [ROADMAP.md](ROADMAP.md)) — manual edits become preference pairs.

### Exporting trajectories to SFT jsonl

The script `scripts/export_sft_jsonl.py` flattens a directory of
`DistillTrajectory` files into OpenAI-Chat-Completions-compatible
jsonl — **one record per assistant turn**. Each record is self-
contained: system prompt + message history so far + available tools,
plus the target output (this turn's `reasoning_content` + `tool_calls`)
plus episode metadata (reward, terminal_status, source).

```bash
uv run python scripts/export_sft_jsonl.py --out dataset/sft.jsonl

# Filters:
#   --source agent_run|apply_edits|any    (default: agent_run)
#   --actor planner|critic|both           (default: both)
#   --provider anthropic|openai_compat|any (default: any)
#   --min-reward 0.7                      (skip low-quality runs)
#   --terminal-status pass|revise|fail|...
```

Record shape (see [scripts/export_sft_jsonl.py](../scripts/export_sft_jsonl.py)
docstring for full layout):

```json
{
  "run_id": "...",
  "turn_idx": 3,
  "actor": "planner",
  "model": "moonshotai/kimi-k2.6",
  "provider": "openai_compat",
  "messages": [
    {"role": "system", "content": "..."},
    {"role": "user", "content": "..."},
    {"role": "assistant", "reasoning_content": "...", "tool_calls": [...]},
    {"role": "tool", "tool_call_id": "...", "content": "..."}
  ],
  "target": {
    "reasoning_content": "...",
    "content": null,
    "tool_calls": [{"id": "...", "type": "function",
                    "function": {"name": "...", "arguments": "..."}}]
  },
  "tools": [...],
  "metadata": {
    "terminal_status": "pass",
    "final_reward": 0.89,
    "turn_stop_reason": "tool_use",
    "turn_usage": {"input":N, "output":N, "cache_read":N, "cache_create":N},
    "thinking_budget": 10000,
    "trajectory_source": "agent_run"
  }
}
```

Critic turns are emitted as separate records with `actor="critic"`,
`tools=[]`, and `target.content` = the full CritiqueResult JSON
(verdict + score + issues + rationale). The critic's vision input is
not re-serialized into the record (would bloat the jsonl with base64
image data); instead a pointer to `out/runs/<run_id>/final/preview.png`
is placed in the user message so the SFT trainer can reconstruct the
image block if vision SFT is desired.

### Lane 6 — Extended-thinking CoT (v1)

```
for step in trajectory.agent_trace if step.type == "reasoning":
    input:  messages up to step.step_idx - 1      # prior turns
    output: step.thinking_blocks[].thinking       # Claude's internal CoT
```

Two independent CoT streams per trajectory:

- `actor="planner"` — Claude's reasoning **before each tool decision**, captured via the `interleaved-thinking-2025-05-14` beta so thinking appears between tool calls, not only at turn start.
- `actor="critic"` — reasoning behind each `CritiqueResult.verdict` / issue list.

Each `ThinkingBlockRecord` carries:
- `thinking`: plaintext CoT (empty when `is_redacted=True`).
- `signature`: Anthropic-issued opaque verification token — required for offline replay; never interpret client-side.
- `is_redacted`: `True` when Anthropic encrypted the block (e.g. safety-sensitive reasoning). Record is kept for accounting even though text is unavailable.

Companion per-step telemetry captured on the same `AgentTraceStep`: `stop_reason`, `cache_read_input_tokens`, `cache_creation_input_tokens`. These support cost/efficiency reward shaping in RL post-training.

**Enable/disable** via env:
- `PLANNER_THINKING_BUDGET` (default 10000; set `0` to disable)
- `CRITIC_THINKING_BUDGET` (default 10000)
- `ENABLE_INTERLEAVED_THINKING` (default `1`; set `0` to fall back to standard thinking only at turn start)

**Filtering trajectories with CoT**:
```python
# v1 trajectories expose thinking config in metadata
t["metadata"].get("planner_thinking_budget", 0) > 0  # has planner CoT
t["metadata"].get("critic_thinking_budget", 0) > 0   # has critic CoT
t["metadata"].get("interleaved_thinking")            # CoT interleaved with tool calls
```

---

## Real distribution (current trajectory examples)

From two real production runs. See `out/trajectories/` for the JSON.

| Field | 国宝回家 (5 layers) | CVPR poster (18 layers) |
|---|---|---|
| `brief` length | 21 chars | 255 chars |
| `design_spec.layer_graph` planned layers | 5 | 18 |
| `design_spec.palette` colors | 4 | 5 |
| `design_spec.composition_notes` length | ~120 chars | ~200 chars |
| Final `layer_graph` size | 5 | 18 |
| `agent_trace` total steps | 27 | 55 |
| - `tool_call` steps | 10 | 22 |
| - `thought` steps | ~3 | ~4 |
| `critique_loop` iterations | 1 | 1 |
| Trajectory JSON size | ~40 KB | ~95 KB |
| Composition PSD size | 18 MB | 18 MB |
| Composition SVG size | 5.3 MB (bg dominates) | 5.4 MB |

---

## Querying trajectories

For ad-hoc analysis you can use jq, Python, or DuckDB. Examples:

### Count trajectories where the critique caught a typography issue

```bash
jq '[.critique_loop[].issues[] | select(.category=="typography")] | length' out/trajectories/*.json
```

### Extract all (brief, palette) pairs

```python
import json, glob
pairs = []
for path in glob.glob("out/trajectories/*.json"):
    with open(path) as f:
        t = json.load(f)
    pairs.append((t["brief"], t["design_spec"]["palette"]))
```

### Build a Lane-2 SFT dataset

```python
# (design_spec, layer_graph) pairs
import json, glob
dataset = []
for path in glob.glob("out/trajectories/*.json"):
    t = json.load(open(path))
    dataset.append({
        "input":  t["design_spec"],
        "output": t["layer_graph"],
        "run_id": t["run_id"],
        "version": t["metadata"].get("version", "v0"),
    })
```

That's it. No special infra needed — flat JSON files in a directory.

---

## Schema evolution policy

When you change the schema:

1. Edit [`open_design/schema.py`](../open_design/schema.py) (the source of truth).
2. Update this doc to match.
3. Bump `metadata.version` in [`runner.py`](../open_design/runner.py) (e.g. `"v0"` → `"v0.1"`) — but only when the change is non-backward-compat. Field additions with defaults don't require a version bump.
4. Add a migration note in [DECISIONS.md](DECISIONS.md) explaining what changed and why.
5. Old trajectories remain readable — just branch on `metadata.version` in downstream loaders.

Don't break old trajectories. The dataset is the asset.

## Schema change log

| Date | Change | Compat |
|---|---|---|
| 2026-04-17 | Initial trajectory schema (`metadata.version = "v0"`). | — |
| 2026-04-18 | `DesignSpec.artifact_type: ArtifactType` added (default=POSTER). `AgentTraceStep.type = "artifact_switch"` added to union. | Backward-compat: old trajectories load with artifact_type defaulting to `poster`. Version unchanged (`v0`). |
| 2026-04-18 | New sidecar schema: `ChatSession` / `ChatMessage` / `TrajectoryRef` (in `session.py`) with `_schema_version = "v1.0-chat"`. Lives at `sessions/<id>.json`. | Independent of Trajectory schema — the pair evolve separately. |
| 2026-04-19 | `LayerKind += "section"` (v1.0 #8, landing) and `"image"` (v1.0 #8.75, inline NBP imagery). `LayerNode.bbox` relaxed to `Optional` (landing flow-layout). `CompositionArtifacts.html_path` added (v1.0 #6). All `CompositionArtifacts` paths made Optional. `DesignSpec.design_system: DesignSystem \| None` added (landing-only, v1.0 #8.5). `IssueCategory += "copy", "content"` (v1.0 #8.5-fix). | Backward-compat: old poster trajectories load cleanly; landing is additive. Version unchanged. |
| 2026-04-20 | `LayerKind += "slide"` (v1.0 #7, deck). `CompositionArtifacts.pptx_path` added. `TrajectoryRef.pptx_path` previously reserved, now populated for DECK runs. | Backward-compat: additive. Old trajectories without `pptx_path` default to None. Version unchanged. |
| 2026-04-20 | **v0 → v1** (training-data capture). New `ThinkingBlockRecord` model. `AgentTraceStep` gains optional `thinking_blocks`, `stop_reason`, `cache_read_input_tokens`, `cache_creation_input_tokens`. `StepType += "reasoning"` for planner + critic extended-thinking blocks captured via `interleaved-thinking-2025-05-14` beta. `metadata` gains `planner_thinking_budget`, `critic_thinking_budget`, `interleaved_thinking`. **Bump: `version` v0→v1.** | Backward-compat: all new `AgentTraceStep` fields optional with default `None`; old v0 trajectories load unchanged. Branch on `metadata["version"]` in downstream loaders when thinking-field presence matters. |
| 2026-04-22 | **v1 → v2** (lean trajectory). `Trajectory` → `DistillTrajectory`. Top-level `design_spec` / `layer_graph` / `composition` / `critique_loop` / `created_at` removed (info recoverable from `agent_trace`). `ToolObservation` → `ToolResultRecord` (drops `summary` / `next_actions` / `artifacts`; gains `error_message` / `error_category` / lean `payload`). `StepType` slimmed to `{input, reasoning, tool_call, tool_result, finalize}` (drops `thought` / `artifact_switch` / `design_spec` / `critique`). `AgentTraceStep` drops `timestamp` / `spec_snapshot` / `observation`; merges 4 token fields into `usage` dict. New `TrainingMetadata` strict model replaces `metadata: dict`. New top-level `final_reward` + `terminal_status` for offline RL. `next_actions` removed at the tool layer, not just stripped — eliminates train↔deploy distribution shift. **Bump: `version` v1→v2.** | **NO backward compat.** Old v0/v1 trajectories must be re-generated. Trajectory size drops ~70% (144 KB → 44 KB on equivalent run). |
| 2026-04-22 | **Multi-provider backend**. New `LLMBackend` Protocol abstracts Anthropic + OpenAI-compat (Kimi/DeepSeek/Doubao/vLLM) under the same trajectory format. `ThinkingBlockRecord.signature` is empty when produced by OpenAI-compat (no signature mechanism); `ThinkingBlockRecord.is_redacted` is always False for OpenAI-compat. Default planner switched to `moonshotai/kimi-k2.6`. Trajectory schema unchanged — only the producer changes. | Backward-compat: schema field semantics unchanged; downstream consumers needing to filter by provider should use `metadata.planner_model` / `metadata.critic_model` (e.g. `startswith("moonshotai/")` vs `startswith("claude-")` / `startswith("anthropic/")`). |
| 2026-04-22 | **Intermediate-artifact versioning**. Layer renderers + composite no longer overwrite prior outputs. New `tool_result.payload` fields on render/edit/composite results: `version` (int, 1-indexed), `relative_path` (str, relative to `<run_dir>/`), `supersedes_sha256` (str, sha of prior version, absent on first write). Composite payload also gains `iteration` (int) + `{preview,html,pptx}_relative_path`. Disk layout: `out/runs/<run_id>/composites/iter_NN/<files>` (versioned snapshots) + `out/runs/<run_id>/final/<files>` (relative symlinks to latest iter) + `out/runs/<run_id>/layers/<prefix>_<id>.vN.png` (versioned layer renders). | Backward-compat: trajectory schema unchanged. Old trajectories without `version` / `relative_path` / `supersedes_sha256` keys load fine — these fields are inside the polymorphic `tool_result.payload` dict so absent-key access returns None. Product consumers (cli / chat / apply-edits) read through `final/` symlinks; the versioning is invisible to user flows. |
