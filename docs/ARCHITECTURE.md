# Architecture

## One-paragraph overview

A `ChatREPL` (in [`longcat_design/chat.py`](../longcat_design/chat.py)) manages the outer conversation loop; each non-slash user message invokes a `PipelineRunner` (in [`longcat_design/runner.py`](../longcat_design/runner.py)) which bootstraps a per-run directory and a `ToolContext`, then hands control to a `PlannerLoop` (in [`longcat_design/planner.py`](../longcat_design/planner.py)) driving Claude Opus 4.7 through a handwritten tool-use loop (no LangGraph / CrewAI). When the brief is prefixed with an `Attached files:` block (from CLI `--from-file` or chat `:attach`), the planner calls `ingest_document` **first** to extract structure + figures + tables; as of v1.2 this step uses **pymupdf natively** (`doc.extract_image(xref)` for embedded rasters at native resolution; `page.get_drawings()` clustered at 300 dpi for vector diagrams; `page.find_tables()` for table localization) and asks **Qwen-VL-Max via OpenRouter** (dispatched through `longcat_design/util/vlm.py`) only for the non-localization jobs: structure manifest extraction, per-figure caption matching, fake-figure filtering, and per-table cell parsing with optional `col_highlight_rule`. Then the full pipeline runs: `switch_artifact_type` → `propose_design_spec` → (`generate_background` | `generate_image`)× → `render_text_layer` × N (poster only) → `edit_layer` (critic-revise loops) → `fetch_brand_asset` (v1 stub) → `composite` → `critique` → `finalize`. **11 tools wired** (since v1.1 `ingest_document`). The `composite` tool dispatches on `spec.artifact_type`: **POSTER** → PSD + SVG + HTML + aspect-preserve preview.png (image layers contain-fit via `_aspect_fit_contain`; table layers re-rendered at bbox dims with font autoscale); **LANDING** → single HTML with 6 bundled design systems + inline NBP imagery + real `<table>` + `figcaption`; **DECK** → `deck.pptx` with native `add_table` shapes for `kind="table"` layers + per-slide PNG thumbs + grid preview.png. Every assistant turn and every tool result is appended to a structured `agent_trace`. When the planner calls `finalize`, the runner serializes the `Trajectory` (brief + spec + layer_graph + trace + critique loop + composition artifacts + metadata) to `out/trajectories/<run_id>.json`; the chat loop appends a lightweight `TrajectoryRef` to the `ChatSession` and persists that to `sessions/<id>.json`. For poster + landing, a standalone `apply-edits` CLI round-trips user-edited HTML back into a new run. For deck, editability lives directly in PowerPoint / Keynote — TextFrames AND native tables are live.

**Dual-SDK routing**: planner and critic always use the Anthropic SDK (preserves tool_use protocol — OpenRouter's Claude endpoint is Anthropic-compatible). Ingest's VLM calls route through `longcat_design/util/vlm.py::vlm_call_json`, which picks the Anthropic or OpenAI SDK based on model id prefix (`anthropic/` / `claude-` → Anthropic; `qwen/` → OpenAI against OpenRouter's `/api/v1`). Ingest is the **only** surface that uses the OpenAI SDK; leaking it into planner.py would break tool_use.

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
│  system prompt:            │  max_tokens=16384 (room for large decks)
│   prompts/planner.md       │  emits: AgentTraceStep × N
└──────────────┬───────────┘
               │ tool_use blocks
               ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  TOOLS  (action space — 11 tools, all in longcat_design/tools/)               │
│                                                                              │
│  switch_artifact_type → poster/deck/landing declaration → ctx.state           │
│  ingest_document      → v1.2 paper2any: PDF (pymupdf native figure + table   │
│                         extraction; Qwen-VL-Max via OpenRouter for structure │
│                         + caption matching + fake-figure filtering; scanned  │
│                         PDFs auto-OCR via VLM at 200 dpi) / DOCX / PPTX /    │
│                         MD / image → structured manifest; registers          │
│                         kind="image" figures + kind="table" layers with      │
│                         rows/headers/col_highlight_rule in rendered_layers   │
│  propose_design_spec  → validate planner JSON → ctx.state["design_spec"]      │
│  generate_background  → Gemini 3 Pro Image (NBP) → full-canvas text-free PNG  │
│  generate_image       → NBP inline image (landing + deck); no safe_zones      │
│  render_text_layer    → Pillow → transparent RGBA PNG (poster only)           │
│  edit_layer           → subset-merge diff onto a text layer + re-render       │
│  fetch_brand_asset    → v0 stub: returns not_found                            │
│  composite            → artifact-type-dispatched:                             │
│                           POSTER  → psd-tools + svgwrite + html_renderer      │
│                                     + Pillow preview                          │
│                           LANDING → write_landing_html + Pillow preview       │
│                                     (6 bundled design systems + NBP imagery) │
│                           DECK    → pptx_renderer.write_pptx (native          │
│                                     TextFrames) + per-slide PNGs + grid      │
│  critique             → invokes Critic:                                       │
│                           POSTER  → vision on preview.png                     │
│                           LANDING → text-only on section tree                 │
│                           DECK    → text-only on slide tree                   │
│  finalize             → flips ctx.state["finalized"] = True                   │
└──────────────┬───────────────────────────────────────────────────────────────┘
               │ ToolObservation per call
               ▼
┌──────────────────────────┐
│ Critic                     │  Anthropic SDK, branches on design_spec.artifact_type:
│  (critic.py)               │    LANDING → text-only via prompts/critic-landing.md
│                            │    DECK    → text-only via prompts/critic-deck.md
│                            │    POSTER  → vision on preview.png via prompts/critic.md
│                            │  outputs: CritiqueResult (strict JSON)
└──────────────┬───────────┘
               │
               ▼
┌──────────────────────────┐
│ Trajectory serialization   │  runner.py builds Trajectory object:
│  (runner.py + util/io.py)  │    POSTER  → _materialize_layer_graph(rendered_layers)
│                            │    LANDING + DECK → copy spec.layer_graph directly
│                            │  atomic-writes to out/trajectories/<run_id>.json
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

## Round-trip edit path (poster + landing; out of scope for deck)

```
poster.html / index.html  (written by composite with contenteditable text + data-* attrs)
        │
        ▼  open in browser, click layers, edit inline, Save button → Copy/Download
poster-edited.html
        │
        ▼  uv run python -m longcat_design.cli apply-edits <path>
longcat_design/apply_edits.py
  - bs4 parses HTML
  - LANDING detected via <main class="ld-landing"> → _restore_landing rebuilds section tree
  - POSTER detected via <div class="canvas">    → _restore_poster rebuilds rendered_layers
  - background decoded from data: URI into new run's layers/
        │
        ▼
new out/runs/<run_id>/  with metadata.parent_run_id lineage + metadata.source = "apply-edits"
  - POSTER:  poster.psd + poster.svg + poster.html + preview.png all regenerated
  - LANDING: index.html + preview.png with edits applied
```

Deck has no equivalent path — PowerPoint / Keynote / Google Slides IS the edit surface.

## File map

```
Design-Agent/                      # directory name preserved from v0 for stability
├── design_agent_blog.pdf          # the seed reference article (local only; .gitignored)
├── README.md                      # quickstart (links to docs/)
├── pyproject.toml                 # deps (incl. python-pptx, beautifulsoup4); uv-managed
├── uv.lock                        # pinned dep graph for reproducible installs
├── .python-version                # pins Python 3.14 for uv
├── .env / .env.example            # API keys (GEMINI_API_KEY required;
│                                  #          OPENROUTER_API_KEY OR ANTHROPIC_API_KEY)
├── .gitignore
│
├── docs/                          # ← THIS KB
│   ├── README.md                  # index + reading order
│   ├── VISION.md                  # why + end goal + paper2any North Star
│   ├── ARCHITECTURE.md            # this file
│   ├── DATA-CONTRACT.md           # Trajectory + DesignSpec schema
│   ├── WORKFLOWS.md               # run / edit / apply-edits / extend
│   ├── DECISIONS.md               # design log
│   ├── ROADMAP.md                 # v1.0 + v1.1 paper2any + v1.x versions
│   ├── GOTCHAS.md                 # runtime quirks
│   └── COMPETITORS.md             # Claude Design / Paper2Any / Lovart audit
│
├── prompts/
│   ├── planner.md                 # system prompt for PlannerLoop (poster + landing + deck workflows)
│   ├── critic.md                  # poster critic rubric (vision-based)
│   ├── critic-landing.md          # landing critic rubric (text-only) — v1.0 #8.5-fix
│   ├── critic-deck.md             # deck critic rubric (text-only) — v1.0 #7
│   └── design-systems/            # 6 bundled landing style guides (v1.0 #8.5)
│       ├── README.md              # index + style picker cheat sheet
│       ├── minimalist.md
│       ├── editorial.md
│       ├── claymorphism.md
│       ├── liquid-glass.md
│       ├── glassmorphism.md
│       └── neubrutalism.md
│
├── assets/
│   ├── fonts/
│   │   ├── NotoSansSC-Bold.otf    # Chinese sans (16 MB, OFL)
│   │   └── NotoSerifSC-Bold.otf   # Chinese serif / "毛笔" stand-in (24 MB, OFL)
│   └── design-systems/            # 6 bundled landing CSS files (v1.0 #8.5)
│       ├── minimalist.css
│       ├── editorial.css
│       ├── claymorphism.css
│       ├── liquid-glass.css
│       ├── glassmorphism.css
│       └── neubrutalism.css
│
├── longcat_design/                # the package
│   ├── __init__.py
│   ├── cli.py                     # argparse: `chat` (default) / `run` (one-shot) / `apply-edits`
│   ├── chat.py                    # ChatREPL — multi-turn REPL, 8 slash commands, session I/O
│   ├── session.py                 # ChatSession / ChatMessage / TrajectoryRef + save/load/list
│   ├── config.py                  # env loading + Settings (LLM backend detection)
│   ├── schema.py                  # Pydantic models — single source of truth
│   │                              #   ArtifactType (POSTER / DECK / LANDING)
│   │                              #   LayerKind (background / text / brand_asset / group /
│   │                              #              section / image / slide / table / cta)
│   │                              #   DesignSpec · LayerNode (+ speaker_notes v2.3.1,
│   │                              #     href/variant v1.3, rows/headers/caption_short v2.3.2)
│   │                              #   DistillTrajectory · ToolResultRecord ·
│   │                              #     TrainingMetadata · ThinkingBlockRecord (v2 schema)
│   ├── runner.py                  # PipelineRunner — per-turn orchestration
│   ├── planner.py                 # PlannerLoop — tool-use loop (max_tokens=16384)
│   ├── critic.py                  # Critic — branches POSTER vision / LANDING+DECK text-only
│   ├── apply_edits.py             # HTML → new run round-trip (poster + landing) — v1.0 #6.5
│   ├── smoke.py                   # `python -m longcat_design.smoke` (18/18 no-API checks)
│   │
│   ├── tools/                     # the action space (11 tools)
│   │   ├── __init__.py            # TOOL_SCHEMAS + TOOL_HANDLERS registry (11 tools)
│   │   ├── _contract.py           # ToolContext + obs_ok/obs_error helpers
│   │   ├── _font_embed.py         # shared WOFF2 subset + base64 @font-face (SVG + HTML)
│   │   ├── _deck_preview.py       # Pillow grid compositor for deck main preview
│   │   ├── switch_artifact_type.py # poster/deck/landing declaration (v1.0 #3)
│   │   ├── propose_design_spec.py
│   │   ├── generate_background.py # full-canvas text-free bg for POSTER
│   │   ├── generate_image.py      # inline NBP image for LANDING + DECK (v1.0 #8.75)
│   │   ├── render_text_layer.py   # Pillow → RGBA PNG (POSTER only)
│   │   ├── edit_layer.py          # subset-merge diff onto text layer (v1.0 #5)
│   │   ├── fetch_brand_asset.py   # v1 stub
│   │   ├── composite.py           # dispatches POSTER / LANDING / DECK
│   │   ├── html_renderer.py       # write_html (poster) + write_landing_html (v1.0 #6 + #8)
│   │   ├── pptx_renderer.py       # write_pptx + render_slide_preview_png (v1.0 #7)
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
    ├── runs/<run_id>/             # per-run artifacts (poster / landing / deck)
    │   ├── layers/                # POSTER text PNGs + all NBP images (bg + inline)
    │   ├── slides/                # DECK per-slide PNG previews (slide_NN.png)
    │   ├── poster.psd             # POSTER only
    │   ├── poster.svg             # POSTER only
    │   ├── poster.html            # POSTER (contenteditable)
    │   ├── index.html             # LANDING (contenteditable; 6 bundled design systems)
    │   ├── deck.pptx              # DECK (native PowerPoint TextFrames)
    │   ├── preview.png            # flat preview (grid for DECK)
    │   └── critique_<i>.json      # per-iteration critique dumps
    ├── trajectories/<run_id>.json
    └── smoke/…                    # smoke-test artifacts (per-check subdir)
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

The chat layer does NOT touch tool internals or the planner loop — it just wraps per-turn execution. `PipelineRunner`, `PlannerLoop`, `Critic`, and the 11 tools are unchanged by chat mode.

Legacy one-shot `longcat-design run "<brief>"` bypasses `chat.py` entirely and invokes `PipelineRunner.run()` directly. Backward compat with the v0 CLI behavior, minus the outer ChatSession.

## The 11 tools (action space)

Every tool follows the same signature: `fn(args: dict, *, ctx: ToolContext) -> ToolObservation`. The `ToolObservation` is a Pydantic model with fields `{status, summary, next_actions, artifacts}` — see [DATA-CONTRACT.md](DATA-CONTRACT.md). Tools never raise to the planner; they always return an observation. Exceptions caught in `planner._invoke` become `status: "error"` observations.

| # | Tool | File | Purpose | Side effect |
|---|---|---|---|---|
| 1 | `switch_artifact_type` | [`tools/switch_artifact_type.py`](../longcat_design/tools/switch_artifact_type.py) | Declare `poster` \| `deck` \| `landing`. MUST be first tool for any new artifact (not counting re-affirm). Writes to `ctx.state["artifact_type"]` — drives renderer selection + acts as fallback default if `propose_design_spec` omits the field. | Sets `ctx.state["artifact_type"]`. Planner emits `AgentTraceStep(type="artifact_switch")`. |
| 2 | `ingest_document` | [`tools/ingest_document.py`](../longcat_design/tools/ingest_document.py) | v1.2 paper2any. Dispatches on extension: `.pdf` → pymupdf native raster/vector figure extraction + `find_tables()` localization; Qwen-VL-Max (via `util/vlm.py`) handles structure manifest + caption matching + fake-figure filtering + table cell parsing. Scanned PDFs auto-detected via `detect_scanned_pdf` → `_ocr_scanned_pdf` runs a 6-worker Qwen-VL page-OCR pass at 200 dpi (v1.2.5). `.docx` → `python-docx` structural reader: heading-styled paragraphs build the section tree, `doc.part.rels` provides embedded images. `.pptx` → `python-pptx` reader: each slide becomes a section, picture shapes become `ingest_fig_NN` (v1.2.5). `.md,.txt` → embedded `![](…)` ref resolution. `.png/.jpg/.jpeg/.webp` → passthrough. Registers `ingest_fig_NN` (`kind="image"`) + `ingest_table_NN` (`kind="table"` with `rows`, `headers`, `col_highlight_rule`) in `rendered_layers`. Tool result summarizes the top-20 figure catalog for planner picks. | Writes `layers/img_<id>.png`; registers records in `rendered_layers`; stores manifest in `ctx.state["ingested"]`. |
| 3 | `propose_design_spec` | [`tools/propose_design_spec.py`](../longcat_design/tools/propose_design_spec.py) | Validate `DesignSpec` JSON from planner; store in `ctx.state["design_spec"]`. Falls back to `ctx.state["artifact_type"]` when spec omits `artifact_type`. For DECK and LANDING, the `layer_graph` is the authoritative section/slide tree. Supports `kind="table"` layers with `rows`, `headers`, `caption`, `col_highlight_rule` fields. | Sets `ctx.state["design_spec"]`. |
| 4 | `generate_background` | [`tools/generate_background.py`](../longcat_design/tools/generate_background.py) | POSTER-only full-canvas text-free background via NBP (`gemini-3-pro-image-preview`). Auto-appends "no text" suffix. Has `safe_zones` so the model protects title/stamp regions. | Writes `layers/bg_<id>.png`; registers in `ctx.state["rendered_layers"]`. |
| 5 | `generate_image` | [`tools/generate_image.py`](../longcat_design/tools/generate_image.py) | Inline NBP image for LANDING sections OR DECK slide elements (image / background children). No `safe_zones` — planner controls placement via `bbox`. v1.0 #8.75 for landing; v1.0 #7 extends for deck. | Writes `layers/img_<layer_id>.png`; registers record in `rendered_layers`. |
| 6 | `render_text_layer` | [`tools/render_text_layer.py`](../longcat_design/tools/render_text_layer.py) | POSTER-only. Render a text run to RGBA PNG with stroke + drop-shadow. Auto-wraps long lines (Latin on space, CJK char-by-char). Falls back to NotoSansSC-Bold for unknown fonts. LANDING + DECK skip this — text stays as native HTML / PPTX TextFrame. | Writes `layers/text_<id>.png`; registers layer. |
| 7 | `edit_layer` | [`tools/edit_layer.py`](../longcat_design/tools/edit_layer.py) | v1.0 #5. Subset-merge diff onto an existing text layer (text / font / size / color / bbox / effects) and re-render. Used during critique revise loops to avoid re-issuing full `propose_design_spec`. Non-text layers return an error with a redirect to the right tool. | Replaces `rendered_layers[layer_id]`; rewrites the layer PNG. |
| 8 | `fetch_brand_asset` | [`tools/fetch_brand_asset.py`](../longcat_design/tools/fetch_brand_asset.py) | v1 stub. Always returns `status: "not_found"`. Replaced by v1.5 Brand Kit ingestion. | Nothing. |
| 9 | `composite` | [`tools/composite.py`](../longcat_design/tools/composite.py) | **Dispatches on `spec.artifact_type`.** POSTER → PSD (psd-tools named pixel layers + `text` group; image layers use `_aspect_fit_contain` letterbox; table layers are re-rendered at bbox dims via `render_table_png`) + SVG (svgwrite real `<text>` + subsetted-WOFF2 @font-face; `<image preserveAspectRatio="xMidYMid meet">` for images + tables) + HTML (contenteditable toolbar + inline fonts/images) + preview.png (PIL alpha_composite, aspect-preserve). LANDING → `index.html` via `write_landing_html` (6 bundled design systems + NBP imagery + real `<table>` with winner-class styling for ingested tables; v1.3 adds CTA `<a role="button">` layers with per-style chrome, `<header><nav>` auto-generated when `section_count ≥ 4` or `design_system.show_nav=true`, semantic `<footer>` auto-upgrade for footer-variant sections, and an inline vanilla-JS `<script>` for `IntersectionObserver` reveal + smooth anchor scroll + `aria-current` active-nav tracking). DECK → `deck.pptx` via `pptx_renderer.write_pptx` (native TextFrames, picture shapes for images, native `add_table` shapes with bold-winner cells for `kind="table"` layers) + `slides/slide_NN.png` per-slide previews + grid `preview.png`. | Writes artifacts under `run_dir`; sets `ctx.state["composition"]`. |
| 10 | `critique` | [`tools/critique_tool.py`](../longcat_design/tools/critique_tool.py) | Invoke `Critic.evaluate()`. Hard-capped at `max_critique_iters` (default 2). Branches on `artifact_type`: POSTER → vision on `preview.png` with v1.2 visual-density rubric (20% weight) that penalizes < 3 figures on paper posters, image-area < 45%, tables < 400 px tall; LANDING → text-only on section tree via `critic-landing.md`; DECK → text-only on slide tree via `critic-deck.md`. | Appends to `ctx.state["critique_results"]`; writes `critique_<i>.json`. |
| 11 | `finalize` | [`tools/finalize.py`](../longcat_design/tools/finalize.py) | Signal "planner is done." | Sets `ctx.state["finalized"] = True`. Runner sees this after the turn and serializes the trajectory. |

The `ToolContext` (in [`tools/_contract.py`](../longcat_design/tools/_contract.py)) is the per-run shared state object. It carries the `Settings`, the run/layers paths, and a mutable `state` dict that tools both read and write. This is intentionally a "blackboard" pattern — keeps tool signatures simple and lets the runner snapshot the final state for the trajectory. In chat mode, each turn gets a FRESH ToolContext — inter-turn state lives in `ChatSession`, not `ToolContext`.

## Two-step image flow + hydration (LANDING + DECK)

Both landing and deck use a **declare-then-fetch** pattern for inline imagery:

1. **`propose_design_spec`** declares each image as a child node with `kind: "image"` (landing) or `kind: "image"` / `kind: "background"` (deck) and `src_path: null` — structure only.
2. **`generate_image`** is called per layer_id separately; NBP returns a PNG, the tool writes it to `ctx.layers_dir/img_<layer_id>.png` AND registers a record in `ctx.state["rendered_layers"][layer_id]`.
3. **`composite`** calls a hydration helper (`_hydrate_landing_image_srcs` / `_hydrate_deck_image_srcs`) that walks the section/slide tree and copies `src_path` + `aspect_ratio` from `rendered_layers` onto matching children via pydantic `model_copy(update=...)` **before** writing the final HTML / PPTX.

This keeps the planner's workflow short — it doesn't have to re-issue `propose_design_spec` after every NBP call — while keeping the DesignSpec (captured in trajectory + parsed by `apply-edits`) as the authoritative structural record.

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

The `critique` tool is **planner-invocable** — the planner decides whether and when to call it. The planner is instructed (via `prompts/planner.md`) to call it once after the first composite and use it at most twice. Hard caps in [`config.py`](../longcat_design/config.py): `max_critique_iters = 2`.

The critic ([`critic.py`](../longcat_design/critic.py)) branches on `design_spec.artifact_type`:

- **POSTER** → `_evaluate_with_vision`: downscales `preview.png` to ≤1024px long edge (respects Anthropic's vision cap of 5 MB / 8000×8000), encodes as JPEG, sends alongside the DesignSpec + layer manifest. Rubric in `prompts/critic.md`.
- **LANDING** → `_evaluate_landing`: text-only on the flattened section tree. The Pillow preview is a lossy proxy for the real browser-rendered HTML (tofu emojis, missing CSS, etc.); grading it with vision leads to false fails. Rubric in `prompts/critic-landing.md`. Decision in DECISIONS.md 2026-04-19.
- **DECK** → `_evaluate_deck`: text-only on the flattened slide tree. PPTX is the authoritative artifact; the per-slide PNG thumbs are Pillow approximations — stitching them also risks the 5 MB vision cap for large decks. Rubric in `prompts/critic-deck.md`. Decision in DECISIONS.md 2026-04-20.

Parse / validation failures fall back to a `verdict: "fail"` so the pipeline still ends cleanly.

If `verdict: "revise"` and iter < max, the planner is expected to adjust layers (POSTER: `edit_layer` for text tweaks, full `propose_design_spec` for structural changes; LANDING: re-issue `propose_design_spec` with section-tree tweaks; DECK: re-issue `propose_design_spec` with slide-tree fixes) and re-call `composite`. The poster rubric explicitly forbids "regenerate background" suggestions unless the issue is a `blocker` on the background itself.

## Smoke test (no-API)

[`longcat_design/smoke.py`](../longcat_design/smoke.py) is a 13-check end-to-end battery that hits no LLM and no Gemini API:

| # | Check | What it proves |
|---|---|---|
| 1 | imports | all modules import (incl. chat, session, edit_layer) |
| 2 | tool registry | 11 tools with valid JSON Schema; `switch_artifact_type` first |
| 3 | pydantic roundtrip | `Trajectory` + nested models serialize/deserialize losslessly |
| 4 | fonts | both Noto fonts load as real OTF |
| 5 | composite (poster) | PSD + SVG + HTML + preview.png from stub bg + 2 text layers |
| 6 | SVG + HTML content | SVG has `<text>` vector + WOFF2 `@font-face`; HTML has 23 markers (canvas, contenteditable, data-* attrs, inline fonts) |
| 7 | chat session roundtrip | save → list → load session JSON preserves messages + trajectories |
| 8 | edit_layer | happy / partial bbox merge / unknown-id / non-text redirect / empty diff |
| 9 | apply-edits roundtrip | poster edited HTML → new run with PSD + SVG + HTML regenerated |
| 10 | landing mode | section-tree spec → HTML + preview; no PSD/SVG; round-trip with seeded font_size_px edit |
| 11 | design-system styles | all 6 bundled styles render with matching CSS signatures + accent_color override |
| 12 | landing with images | 2 stub image PNGs inline as `<figure>` with data: URIs; round-trip preserves |
| 13 | deck mode | 3-slide spec → `.pptx` + per-slide PNGs + grid preview; pptx reopen verifies slide count + native text runs + picture shapes |

Run with `uv run python -m longcat_design.smoke`. Use this whenever you change any of: schema, tool contract, renderer, critic, apply-edits, or the registry. Catches dependency / wiring regressions in ~5 seconds and zero $.

## Performance baselines (reference)

Measured on real OpenRouter runs with `claude-opus-4-7` planner + critic and NBP (`gemini-3-pro-image-preview`) for imagery. See [DATA-CONTRACT.md](DATA-CONTRACT.md) for trajectory contents.

| Brief | Artifact | Layers / slides | Images | Wall | Cost | Critic |
|---|---|---|---|---|---|---|
| 国宝回家 主视觉 | poster (3:4) | 5 layers (4 text + 1 bg) | 1 NBP bg | 100 s | $1.41 | pass 0.86 (1 iter) |
| CVPR academic poster | poster (3:4) | 18 layers (17 text + 1 bg) | 1 NBP bg | 196 s | $2.49 | pass 0.86 (1 iter) |
| LongcatDesign 发布海报 | poster (3:4) | 5 layers | 1 NBP bg (3 retries) | 297 s | $3.74 | revise 0.78 → pass 0.82 (2 iter) |
| 茉语 奶茶 landing | landing (claymorphism) | 4 sections | 5 NBP (1 × 2K hero + 4 × 1K icons) | 207 s | $2.20 | pass 0.94 (1 iter) |
| MilkCloud 投资人 deck | deck (16:9) | 10 slides | 10 NBP (cover bg + 8 content + closing bg) | 384 s | $3.43 | pass 0.92 (1 iter) |

Cost scales with (a) planner turn count + spec size (Opus 4.7 driver) and (b) number of NBP calls. For landing + deck, imagery is the dominant contributor at the N-images-per-artifact level (~$0.10 per 1K image, ~$0.20 per 2K image). For poster, one NBP call dominates everything else.
