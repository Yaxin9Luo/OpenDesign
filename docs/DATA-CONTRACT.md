# Data Contract — Trajectory schema

> **⚠️ POST-PIVOT NOTICE (2026-04-18)**
>
> Since the [2026-04-18 pivot to LongcatDesign](DECISIONS.md#2026-04-18--pivot-rebrand-as-longcatdesign-reposition-as-open-source-claude-design-alternative), the trajectory schema is **no longer the primary product pitch**. It's now **internal session state** — used for chat-session persistence (`:save` / `:load`), undo/redo of layer edits, and project resume.
>
> The schema is preserved intact because (a) the full session-capture machinery is already built and works, and (b) if the Longcat-Next team wants to flip a feature flag and harvest trajectories from real user sessions for training-data purposes, the infrastructure is ready with no refactor.
>
> The 5-SFT-lane extraction described below still works as written. It's just not what we lead with in the README.
>
> See [VISION.md](VISION.md) for the current product pitch and [V1-MVP-PLAN.md](V1-MVP-PLAN.md) for what ships in v1.0.

---

> **Historical framing (pre-pivot):** The trajectory IS the product. Everything else (PSD, SVG, preview) is a derivable artifact. The trajectory is the structured record that turns one Design-Agent run into 5 distinct training pairs for Longcat-Next.

The single source of truth is [`design_agent/schema.py`](../design_agent/schema.py). This doc explains the *meaning* of each field and the downstream training tasks it supports. **If this doc disagrees with `schema.py`, the code wins — fix the doc.**

---

## Top-level shape

```python
class Trajectory(BaseModel):
    run_id: str                                # sortable: YYYYMMDD-HHMMSS-shortuuid
    created_at: datetime
    brief: str                                 # the literal user input, untouched
    design_spec: DesignSpec                    # the planner's plan (Stage 1)
    layer_graph: list[LayerNode]               # the final, post-critique layers (Stage 4)
    agent_trace: list[AgentTraceStep]          # every assistant turn + tool call/result (Stage 2-3)
    critique_loop: list[CritiqueResult]        # one entry per critique iteration (Stage 3)
    composition: CompositionArtifacts          # paths to PSD/SVG/preview + manifest
    metadata: dict                             # cost, tokens, wall time, model versions
```

A real trajectory.json is ~40-60 KB. Layer image files are referenced by path (not embedded), keeping the JSON itself queryable.

---

## DesignSpec — the planner's blueprint

```python
class DesignSpec(BaseModel):
    brief: str                                 # echoed from input for searchability
    canvas: dict                               # {w_px, h_px, dpi, aspect_ratio, color_mode}
    palette: list[str]                         # hex colors, ordered by importance
    typography: dict                           # {title_font, subtitle_font, stamp_font, ...}
    mood: list[str]                            # ["oriental epic", "dignified", ...]
    composition_notes: str                     # planner's free-form layout rationale (1-3 sentences)
    layer_graph: list[LayerNode]               # SKELETON of planned layers; src_path/prompt unfilled
    references: list[str]                      # optional: URIs/hex inspiration
```

The skeleton `layer_graph` here describes the *plan* — the planner thinks "I'll put a title here, a subtitle there, a stamp top-right." The actual `src_path` for each layer is filled in by later tool calls (`generate_background`, `render_text_layer`). The post-execution version of `layer_graph` lives at the trajectory root level.

**Why two `layer_graph` slots (one in DesignSpec, one at root)?** The DesignSpec one is the *plan* (what the planner intended). The root one is the *result* (what actually got rendered, possibly after critique revisions). Comparing them is itself a useful signal — see DPO lane below.

---

## LayerNode — polymorphic per-layer descriptor

```python
class LayerNode(BaseModel):
    layer_id: str                              # uuid-suffixed, stable across rerenders
    name: str                                  # semantic: "title" | "subtitle" | "stamp" | ...
    kind: Literal["background", "text", "brand_asset", "group"]
    z_index: int                               # render order (0 = bottom)
    bbox: SafeZone                             # top-left origin, pixel units, on canvas

    # text-only fields
    text: str | None
    font_family: str | None
    font_size_px: int | None
    align: Literal["left", "center", "right"] | None
    effects: TextEffect | None                 # {stroke, shadow, fill}

    # background-only fields
    prompt: str | None                         # the literal NBP prompt sent (for image-gen SFT)
    aspect_ratio: str | None                   # "3:4", "16:9", ...
    image_size: str | None                     # "1K" | "2K"

    # any
    src_path: str | None                       # filled by tool execution
    children: list["LayerNode"]                # for groups (currently unused in v0; v0.2+)
```

Polymorphism by `kind` is intentional — keeps the schema flat and SFT-friendly. A future model trained on `(design_spec → layer_graph)` learns to emit one of N kind values per layer.

---

## AgentTraceStep — the byte-exact replayable turn log

```python
class AgentTraceStep(BaseModel):
    step_idx: int                              # monotonic, 1-indexed
    timestamp: datetime
    actor: Literal["user", "planner", "tool", "critic", "system"]
    type: Literal["input", "thought", "tool_call", "tool_result",
                  "design_spec", "critique", "finalize"]

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
    layer_id: str | None                       # null = whole-poster issue
    category: Literal["typography", "composition", "brand",
                      "legibility", "cultural", "artifact"]
    description: str
    suggested_fix: str                         # actionable, references render_text_layer args
```

`pass` requires `score ≥ 0.75` AND zero blockers. `revise` only allowed while `iteration < max_iters`; otherwise forced to `fail`. The runner (`config.max_critique_iters = 2`) caps the loop to prevent infinite revision.

`suggested_fix` must be achievable by re-calling `render_text_layer` with different args (different bbox, font_size, color, alignment). The critic prompt forbids "regenerate background" suggestions unless a blocker requires it.

---

## CompositionArtifacts — what got written to disk

```python
class CompositionArtifacts(BaseModel):
    psd_path: str                              # multi-layer Photoshop file
    svg_path: str                              # self-contained SVG with embedded fonts
    preview_path: str                          # flat PNG for critic / sharing
    layer_manifest: list[dict]                 # [{layer_id, name, png_path, bbox, kind}]
```

The `layer_manifest` is intentionally a simplified mirror of the `layer_graph` — useful for downstream tools that don't want to parse the full polymorphic schema (e.g., a Figma plugin that just needs name/bbox/png to recreate layers).

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

1. Edit [`design_agent/schema.py`](../design_agent/schema.py) (the source of truth).
2. Update this doc to match.
3. Bump `metadata.version` in [`runner.py`](../design_agent/runner.py) (e.g. `"v0"` → `"v0.1"`).
4. Add a migration note in [DECISIONS.md](DECISIONS.md) explaining what changed and why.
5. Old trajectories remain readable — just branch on `metadata.version` in downstream loaders.

Don't break old trajectories. The dataset is the asset.
